"""auto-install auto-install driver: lease boundary, immutable plan + reconciliation, dependency
semantics, durable run marker (write-failure aborts; interrupted derivation; ack), TX
coupling, run_id-bound log access. Deterministic: FakeSystem + disposable roots + real tmp
git repos where identity proof is exercised."""
import pytest

import json
import os
import subprocess
import time

from lhpc.core import auto_install as ai_mod
from lhpc.core import known_working, source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService, ActionResult


def _sel(svc, source="pinned", tests=True, tx=False):
    """A uniform per-stack selection over the full auto-install scope (all stacks installed, one
    version, tests on all, TX only on the daemon) — the pre-per-stack global behaviour."""
    return {st.id: {"install": True, "version": source, "tests": tests,
                    "tx": bool(tx) and st.id == "daemon"}
            for st in svc.stacks() if any(c.source for c in st.components)}


def _svc(tmp_path, cmdlines=None):
    fake = FakeSystem(cmdlines_data=cmdlines or {})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    svc._fake = fake
    return svc


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(("git",) + args, cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def _make_repo(path, remote=""):
    path.mkdir(parents=True)
    (path / "file.txt").write_text("hello\n")
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "v1")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return _git(path, "rev-parse", "HEAD")


# ---- integration tests that need the running stack are deferred in auto-install ------------------

def test_running_required_host_test_is_deferred_in_auto_install_but_runs_explicitly(tmp_path, monkeypatch):
    """A component with `test_requires_running` (generic mechanism; no packaged stack uses it
    today — meshcom's test.sh is self-sufficient) is DEFERRED in a auto-install sweep (never a false
    failure) and RUN by an explicit `lhpc test` (no auto_install_ctx)."""
    from contextlib import nullcontext
    svc = _svc(tmp_path)
    ran: list[str] = []
    # Force the flag on a real testable component (meshcom-qemu) for this test only.
    # Component is a frozen dataclass -> object.__setattr__, restored in finally.
    comp = next(c for st in svc.stacks() for c in st.components if c.id == "meshcom-qemu")
    object.__setattr__(comp, "test_requires_running", True)
    try:
        class _FakeLife:
            def host_test(self, comp, log_base=None, should_cancel=None):
                ran.append(comp.id)
                return type("R", (), {"ok": True, "returncode": 0, "log_path": "x",
                                      "state": type("S", (), {"value": "succeeded"})()})()
        monkeypatch.setattr(svc, "_lifecycle", lambda: _FakeLife())
        monkeypatch.setattr(svc, "_auto_install_ctx_error", lambda ctx, paths: "")   # lock check elsewhere
        monkeypatch.setattr(svc, "_source_operation_guard", lambda *a, **k: nullcontext())

        # auto-install context present -> the flagged test is deferred, NOT run, NOT failed.
        r_bulk = svc.test("meshcom", tx=False, apply=True, auto_install_ctx=object())
        assert r_bulk.ok
        assert any("[deferred]" in d and "meshcom-qemu" in d for d in r_bulk.details), r_bulk.details
        assert "meshcom-qemu" not in ran

        # EXPLICIT (no auto_install_ctx) -> the test actually runs.
        ran.clear()
        r_expl = svc.test("meshcom", tx=False, apply=True)
        assert "meshcom-qemu" in ran
    finally:
        object.__setattr__(comp, "test_requires_running", False)


def test_packaged_meshcom_test_runs_in_bulk(tmp_path):
    """The PACKAGED meshcom-qemu test is self-sufficient (test.sh boots its own guest), so it
    must NOT carry test_requires_running — auto-install runs it right after the build instead of
    deferring (regression: 'deferred — 1 test(s) need the running stack')."""
    svc = _svc(tmp_path)
    comp = next(c for st in svc.stacks() for c in st.components if c.id == "meshcom-qemu")
    assert comp.test_argv == ("scripts/test.sh",)
    assert comp.test_requires_running is False


def test_packaged_meshcom_bridge_runs_ctest_in_bulk(tmp_path):
    """The meshcom-bridge component runs its own deterministic CTest suite (FakeBackend, no
    hardware/daemon/QEMU) — built during auto-install and run right after (regression: the bridge
    tests were compiled but never executed because the component had no host test)."""
    svc = _svc(tmp_path)
    comp = next(c for st in svc.stacks() for c in st.components if c.id == "meshcom-bridge")
    assert comp.test_argv == ("ctest", "--test-dir", "build", "--output-on-failure")
    assert comp.test_requires_running is False           # standalone -> runs in the build sweep


# ---- dry-run + flag coupling ------------------------------------------------------------

def test_dry_run_names_scope_and_flags(tmp_path):
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=False, tests=True, tx=False)
    assert r.ok and r.data["changes"] == 8
    assert r.details[0].startswith("  [install] daemon:")       # dependency order, daemon first
    assert any("TX test: off" in d for d in r.details)


def test_tx_requires_tests(tmp_path):
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, tx=True)
    assert not r.ok and "host tests" in r.summary
    assert svc.auto_install_status() is None                            # zero mutation
    ln, err = svc.spawn_auto_install_job(_sel(svc, tests=False, tx=True))
    assert ln is None and "host tests" in err


def test_invalid_source_and_run_id_refused(tmp_path):
    svc = _svc(tmp_path)
    assert not svc.auto_install(source="evil", apply=True).ok
    assert not svc.auto_install(run_id="../../etc", apply=True).ok
    assert svc.auto_install_status() is None


# ---- dependency semantics + marker truthfulness ------------------------------------------

@pytest.mark.needs_session
def test_failed_daemon_blocks_dependents_independents_continue(tmp_path, monkeypatch):
    # FakeSystem: every clone fails -> daemon FAILS; every daemon-dependent stack is
    # BLOCKED (not attempted); meshtastic (independent) is attempted on its own and hits
    # its own gate. Final state completed-with-failures, truthful summary.
    _stub_frozen(monkeypatch)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=True, emit=lambda s: None)
    assert not r.ok
    st = svc.auto_install_status()
    assert st["state"] == "completed-with-failures"
    rows = {x["id"]: x for x in st["stacks"]}
    assert rows["daemon"]["status"] == "fail"
    for sid in ("chat", "igate", "voice", "kiss", "meshcom", "meshcore"):
        assert rows[sid]["status"] == "blocked"
        assert "dependency failed: daemon" in rows[sid]["detail"]
    # meshtastic is independent: its own MANDATORY system deps are missing, so the early gate
    # (before any source work) blocks it — never a false "dependency failed".
    assert "missing mandatory system deps" in rows["meshtastic"]["detail"] \
        or rows["meshtastic"]["status"] in ("fail", "blocked")
    assert "dependency failed" not in rows["meshtastic"]["detail"]
    assert "REMAIN installed" in r.summary                       # partial results stated
    assert st["run_id"] and st["log"] == ai_mod.log_name_for(st["run_id"]) + ".log"


@pytest.mark.needs_session
def test_marker_write_failure_stops_run(tmp_path, monkeypatch):
    _stub_frozen(monkeypatch)
    svc = _svc(tmp_path)
    calls = {"n": 0}
    real = ai_mod.write_marker
    def failing(paths, d):
        calls["n"] += 1
        if calls["n"] >= 3:                                      # fail once running
            return False
        return real(paths, d)
    monkeypatch.setattr(ai_mod, "write_marker", failing)
    lines = []
    r = svc.auto_install(apply=True, emit=lines.append)
    assert not r.ok and "ABORTED" in r.summary
    assert any("durable progress evidence" in ln or "not be persisted" in ln
               for ln in lines)


@pytest.mark.needs_session
def test_running_component_refuses_with_zero_marker(tmp_path):
    svc = _svc(tmp_path, cmdlines={555: ["loraham_kiss_tnc"]})
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok and "never stops anything" in r.summary
    assert svc.auto_install_status() is None                             # no marker, no lease
    assert ai_mod.read_lease(svc._paths)[0] == "absent"


# ---- interrupted derivation, gate, ack ---------------------------------------------------

def _seed_marker(tmp_path, state="running", run_id="a" * 32):
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker(run_id, "install", "pinned", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = state
    assert ai_mod.write_marker(paths, m)
    return paths, m


@pytest.mark.needs_session
def test_running_marker_with_dead_job_reads_interrupted(tmp_path):
    _seed_marker(tmp_path, "running")
    svc = _svc(tmp_path)
    st = svc.auto_install_status()
    assert st["state"] == "interrupted" and st.get("derived_interrupted")
    assert not svc.auto_install_running()
    assert "interrupted" in svc._auto_install_gate()                     # blocks a new run
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok and "acknowledge" in r.summary
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None and "acknowledge" in err


def test_malformed_marker_is_unsafe_and_blocks(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    d = tmp_path / "state"
    d.mkdir(parents=True)
    (d / "auto-install.json").write_text("{not json")
    svc = _svc(tmp_path)
    st = svc.auto_install_status()
    assert st["unsafe"]
    assert "acknowledge" in svc._auto_install_gate()
    # symlinked marker leaf: unsafe too (never followed)
    (d / "auto-install.json").unlink()
    (d / "real.json").write_text("{}")
    os.symlink("real.json", d / "auto-install.json")
    assert svc.auto_install_status()["unsafe"]


def test_dead_lease_blocks_until_ack(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    dead = {"starttime": 1, "pgid": 1, "sid": 1, "exec": "/bin/false",
            "argv_fp": "x", "argv_len": 1}
    assert ai_mod.write_lease(paths, "b" * 32, 999999, dead, ["daemon"], ["src/x"])
    svc = _svc(tmp_path)
    gate = svc._auto_install_gate()
    assert "died while holding" in gate and "acknowledge" in gate
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok                                              # mutation-blocked
    ack = svc.auto_install_ack()
    assert ack.ok
    assert ai_mod.read_lease(paths)[0] == "absent"             # archived, not deleted
    assert list((tmp_path / "state").glob("auto-install-lease.json.*.acked"))
    assert svc._auto_install_gate() == ""                                # unblocked


def test_ack_refuses_with_pending_journal(tmp_path):
    _seed_marker(tmp_path, "running")
    d = tmp_path / "state" / "source-txn"
    d.mkdir(parents=True)
    (d / "app-" + "0" * 64 + ".json").write_text("{}") if False else \
        (d / ("app-" + "0" * 64 + ".json")).write_text("{}")
    svc = _svc(tmp_path)
    ack = svc.auto_install_ack()
    assert not ack.ok and "journal" in ack.summary
    assert (tmp_path / "state" / "auto-install.json").exists()   # nothing archived


def test_ack_archives_marker_no_follow(tmp_path):
    paths, _ = _seed_marker(tmp_path, "running")
    svc = _svc(tmp_path)
    ack = svc.auto_install_ack()
    assert ack.ok
    assert svc.auto_install_status() is None
    assert list((tmp_path / "state").glob("auto-install.json.*.acked"))


# ---- lease boundary blocks concurrent ops ------------------------------------------------

@pytest.mark.needs_session
def test_boundary_blocks_concurrent_update_and_start(tmp_path):
    svc = _svc(tmp_path)
    src_paths = ["src/loraham-kiss-tnc", "src/loraham-daemon", "src/RadioLib"]
    with svc._auto_install_boundary("c" * 32, ["kiss", "daemon"], src_paths):
        assert ai_mod.read_lease(svc._paths)[0] == "valid"
        svc2 = _svc(tmp_path)                                    # independent instance
        r = svc2.update("kiss", apply=True, source="dev")
        assert not r.ok and ("blocked" in r.summary.lower()
                             or "progress" in r.summary.lower())
        r2 = svc2.start("daemon", apply=True)
        assert not r2.ok                                         # source locks contended
    assert ai_mod.read_lease(svc._paths)[0] == "absent"        # lease cleared on exit


@pytest.mark.needs_session
def test_auto_install_ctx_validation_fails_closed(tmp_path):
    svc = _svc(tmp_path)
    foreign = ai_mod.AutoInstallOperationContext("d" * 32, ["src/loraham-kiss-tnc"])
    r = svc.update("kiss", apply=True, source="dev", auto_install_ctx=foreign)
    assert not r.ok and "not the active boundary" in r.summary + " ".join(r.details)
    with svc._auto_install_boundary("e" * 32, ["kiss"], ["src/loraham-kiss-tnc"]) as ctx:
        r2 = svc.update("daemon", apply=True, source="dev", auto_install_ctx=ctx)
        assert not r2.ok and "does not cover" in r2.summary      # uncovered paths refuse


# ---- reconciliation matrix ---------------------------------------------------------------

def test_reconcile_absent_installs_unowned_blocks_orphan_blocks(tmp_path):
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() if s.id == "kiss"
                for c in s.components if c.id == "loraham-kiss-tnc")
    assert svc._reconcile_group("src/loraham-kiss-tnc", comp) == ("install", "")
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    dest.mkdir(parents=True)                                     # present but unowned
    action, why = svc._reconcile_group("src/loraham-kiss-tnc", comp)
    assert action == "blocked" and "UNOWNED" in why
    import shutil
    shutil.rmtree(dest)                                          # orphaned record
    assert source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        "src/loraham-kiss-tnc", "", "legacy", "", time.time(), "", "",
        ("loraham-kiss-tnc",)))
    action, why = svc._reconcile_group("src/loraham-kiss-tnc", comp)
    assert action == "blocked" and "orphaned" in why.lower()


def test_reconcile_valid_identity_updates_dirty_blocks(tmp_path):
    from lhpc.core.probes import RealSystem
    remote = "https://github.com/makrohard/LoRaHAM_Daemon.git"
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    head = _make_repo(dest, remote=remote)
    paths = Paths(runtime_root=tmp_path)
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/loraham-kiss-tnc", remote, "legacy", head, time.time(), "", "",
        ("loraham-kiss-tnc", "loraham-kiss-serial")))
    svc = ControllerService(system=RealSystem(), paths=paths)
    comp = next(c for s in svc.stacks() if s.id == "kiss"
                for c in s.components if c.id == "loraham-kiss-tnc")
    assert svc._reconcile_group("src/loraham-kiss-tnc", comp) == ("update", "")
    (dest / "user-change.txt").write_text("late")                # dirty tree
    action, why = svc._reconcile_group("src/loraham-kiss-tnc", comp)
    assert action == "blocked" and "local changes" in why


def test_auto_install_mode_aggregate(tmp_path):
    svc = _svc(tmp_path)
    assert svc.auto_install_mode() == "install"
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    assert svc.auto_install_mode() == "mixed"


# ---- TX phase -----------------------------------------------------------------------------

def _tx_env(tmp_path, monkeypatch, callsign="OE1TST", start_ok=True, test_ok=True,
            stop_ok=True):
    svc = _svc(tmp_path)
    if callsign:
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "local.toml").write_text(
            f'[operator]\ncallsign = "{callsign}"\n')
    calls = []
    monkeypatch.setattr(svc, "start", lambda t, apply=False, auto_install_ctx=None, **k:
                        (calls.append(("start", t)),
                         ActionResult(start_ok, "start"))[1])
    monkeypatch.setattr(svc, "test", lambda t, tx=False, apply=False, auto_install_ctx=None, **k:
                        (calls.append(("test-tx" if tx else "test", t)),
                         ActionResult(test_ok, "txtest"))[1])
    monkeypatch.setattr(svc, "stop", lambda t, apply=False, auto_install_ctx=None, **k:
                        (calls.append(("stop", t)),
                         ActionResult(stop_ok, "stop"))[1])
    marker = ai_mod.new_marker("f" * 32, "install", "pinned", True, True,
                                 [{"id": "daemon", "name": "d"}])
    marker["stacks"][0]["status"] = "success"
    return svc, marker, calls


def test_tx_phase_start_test_stop_order(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch)
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    assert calls == [("start", "daemon"), ("test-tx", "daemon"), ("stop", "daemon")]
    assert marker["tx_phase"]["status"] == "success"
    assert marker["stacks"][0]["tx"] == {"ran": True, "ok": True, "detail": "passed"}


def test_tx_failure_flips_daemon_row_and_stop_still_runs(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch, test_ok=False)
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    assert ("stop", "daemon") in calls                           # finally-stop
    assert marker["tx_phase"]["status"] == "fail"
    assert marker["stacks"][0]["status"] == "fail"               # host-pass + TX-fail = fail
    assert marker["stacks"][0]["tx"]["ok"] is False


def test_tx_stop_failure_never_claims_stopped(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch, stop_ok=False)
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    assert marker["tx_phase"]["status"] == "fail"
    assert "may still be RUNNING" in marker["tx_phase"]["detail"]


def test_tx_refused_without_callsign(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch, callsign="")
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    assert calls == []                                           # never started
    assert marker["tx_phase"]["status"] == "fail"
    assert "callsign" in marker["tx_phase"]["detail"]


# ---- run_id-bound log access ---------------------------------------------------------------

def test_log_chunk_derives_name_from_run_id_only(tmp_path):
    svc = _svc(tmp_path)
    rid = "9" * 32
    (tmp_path / "logs").mkdir()
    log = tmp_path / "logs" / (ai_mod.log_name_for(rid) + ".log")
    log.write_text("hello auto-install\n")
    # marker log field tampered -> ignored, the derived file is read
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker(rid, "install", "pinned", True, False, [])
    m["log"] = "../../etc/passwd"
    assert ai_mod.write_marker(paths, m)
    chunk = svc.auto_install_log_chunk(rid, 0)
    assert chunk["data"] == "hello auto-install\n" and chunk["offset"] == 19
    # cursor semantics + caps
    assert svc.auto_install_log_chunk(rid, 19)["data"] == ""
    assert svc.auto_install_log_chunk(rid, 10 ** 13)["error"] == "invalid offset"
    assert svc.auto_install_log_chunk(rid, -1)["error"] == "invalid offset"
    assert svc.auto_install_log_chunk("nope", 0)["error"] == "invalid run id"
    # symlinked log leaf refused (no-follow)
    log.unlink()
    (tmp_path / "logs" / "real.log").write_text("x")
    os.symlink("real.log", log)
    assert "error" in svc.auto_install_log_chunk(rid, 0)


def test_log_chunk_byte_cap(tmp_path):
    svc = _svc(tmp_path)
    rid = "8" * 32
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / (ai_mod.log_name_for(rid) + ".log")).write_text("x" * 100_000)
    c1 = svc.auto_install_log_chunk(rid, 0)
    assert len(c1["data"]) == 64 * 1024                          # capped
    c2 = svc.auto_install_log_chunk(rid, c1["offset"])
    assert len(c2["data"]) == 100_000 - 64 * 1024


@pytest.mark.needs_session
def test_skipped_adopt_is_not_a_failure(tmp_path, monkeypatch):
    # A benign non-mutating adopt outcome (status "skipped") must NOT mark a stack FAIL.
    # Linked stacks WITH declared build/test work are truthfully BLOCKED (their required
    # work is refused by design); everything else is success — never a fail row.
    from lhpc.core.install import Installer, PlanAction
    monkeypatch.setattr(
        Installer, "adopt_source",
        lambda self, comp, force=False, source="pinned", pinned_expected=None,
        locked=False:
        PlanAction("adopt", "", f"adopt {comp.id}", status="skipped",
                   detail="linked dev tree — left as-is"))
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "built"))
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "tested"))
    _stub_frozen(monkeypatch)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    st = svc.auto_install_status()
    rows = {x["id"]: x for x in st["stacks"]}
    assert not any(x["status"] == "fail" for x in st["stacks"])  # skipped is never FAIL
    # CONTAINMENT: the shipped manifest has NO linked components — meshcom/meshcore are
    # ordinary managed-clone rows now (all-success run).
    for sid in ("meshcom", "meshcore", "daemon"):
        assert rows[sid]["status"] == "success"
        assert "linked external tree" not in rows[sid]["detail"]
    assert st["state"] == "completed" and r.ok


@pytest.mark.needs_session
def test_linked_stack_with_work_still_blocks(tmp_path, monkeypatch):
    # The linked-blocked semantics survive for SYNTHETIC linked components (legacy
    # manifests / direct construction) even though the shipped manifest has none.
    import dataclasses
    from lhpc.core.install import Installer, PlanAction
    _happy_ops(monkeypatch)
    real_scope = ControllerService._auto_install_scope
    def scope(self):
        out = []
        for st, comps in real_scope(self):
            if st.id == "meshcore":
                comps = [dataclasses.replace(
                    c, source=dataclasses.replace(c.source, strategy="link"))
                    for c in comps]
            out.append((st, comps))
        return out
    monkeypatch.setattr(ControllerService, "_auto_install_scope", scope)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    st = svc.auto_install_status()
    rows = {x["id"]: x for x in st["stacks"]}
    assert rows["meshcore"]["status"] == "blocked"
    assert "linked external tree" in rows["meshcore"]["detail"]
    assert rows["meshcore"]["tests"]["detail"] == "skipped (linked source)"
    assert st["state"] == "completed-with-failures" and not r.ok


# --- M2 correction: truthful terminal status ----------------------------------------------

def _stub_frozen(monkeypatch):
    """Deterministic plan-time freezing for FakeSystem tests (no real ls-remote)."""
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: (("f" * 40, "frozen: stub"), ""))


def _happy_ops(monkeypatch):
    from lhpc.core.install import Installer, PlanAction
    _stub_frozen(monkeypatch)
    monkeypatch.setattr(
        Installer, "adopt_source",
        lambda self, comp, force=False, source="pinned", pinned_expected=None,
        locked=False:
        PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="ok"))
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "built"))
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "tested"))


@pytest.mark.needs_session
def test_blocked_rows_without_fail_are_not_success(tmp_path, monkeypatch):
    # Blocked rows only (no fail rows): the run is completed-with-failures, ok=False, and
    # the summary truthfully reports the successes that remain installed/built.
    _happy_ops(monkeypatch)
    real = ControllerService._reconcile_group
    def reconcile(self, path, comp):
        if path == "src/loraham-kiss-tnc":
            return "blocked", "local changes present"
        return real(self, path, comp)
    monkeypatch.setattr(ControllerService, "_reconcile_group", reconcile)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    assert not r.ok                                              # NEVER ok with blocked rows
    st = svc.auto_install_status()
    assert st["state"] == "completed-with-failures"
    rows = {x["id"]: x["status"] for x in st["stacks"]}
    assert rows["kiss"] == "blocked"
    assert rows["daemon"] == "success"                           # successes reported truthfully
    assert "REMAIN installed" in r.summary
    assert "1 blocked" in r.summary and "0 failed" in r.summary  # only the mocked kiss group


@pytest.mark.needs_session
def test_candidate_cleanup_failure_downgrades_run(tmp_path, monkeypatch):
    _happy_ops(monkeypatch)
    monkeypatch.setattr(ControllerService, "_retire_candidates_for_paths",
                        lambda self, paths, out: False)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    assert not r.ok
    assert svc.auto_install_status()["state"] == "completed-with-failures"


@pytest.mark.needs_session
def test_lease_clear_failure_is_durable_incomplete(tmp_path, monkeypatch):
    # A failed lease clear after an otherwise clean run: ok=False, the marker is a durable
    # completed-with-failures naming the cleanup, and a new run is BLOCKED.
    _happy_ops(monkeypatch)
    monkeypatch.setattr(ai_mod, "clear_lease", lambda paths: False)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    assert not r.ok and "cleanup failed" in r.summary
    st = svc.auto_install_status()
    assert st["state"] == "completed-with-failures"
    assert "boundary cleanup failed" in st["error"]
    assert svc._auto_install_gate() != ""                                # next run blocked
    # evidence retained: the lease leaf still exists
    assert (tmp_path / "state" / "auto-install-lease.json").exists()


# --- M2 correction: every TX failure mode fails the daemon row ------------------------------

def test_tx_missing_callsign_fails_daemon_row(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch, callsign="")
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    assert calls == []
    row = marker["stacks"][0]
    assert row["status"] == "fail" and "callsign" in row["detail"]
    assert row["tx"]["ok"] is False


def test_tx_start_failure_fails_daemon_row(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch, start_ok=False)
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    row = marker["stacks"][0]
    assert row["status"] == "fail" and "start failed" in row["detail"]
    assert ("stop", "daemon") not in calls                       # never started -> no stop


def test_tx_test_failure_fails_daemon_row(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch, test_ok=False)
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    row = marker["stacks"][0]
    assert row["status"] == "fail" and "TX test failed" in row["detail"]
    assert ("stop", "daemon") in calls                           # finally-stop still ran


def test_tx_stop_failure_fails_daemon_row(tmp_path, monkeypatch):
    svc, marker, calls = _tx_env(tmp_path, monkeypatch, stop_ok=False)
    svc._auto_install_tx_phase(marker, None, lambda s: None, lambda: None)
    row = marker["stacks"][0]
    assert row["status"] == "fail"
    assert "may still be RUNNING" in row["detail"]
    assert marker["tx_phase"]["status"] == "fail"


# --- M2 correction: atomic auto-install-start reservation -------------------------------------------

def _spawnable(svc, monkeypatch, spawn_ok=True, track_ok=True):
    calls = []
    class FakeLife:
        def spawn_job(self, name, argv, cwd, env=None):
            calls.append(name)
            return (name + ".log", os.getpid()) if spawn_ok else (None, None)
    monkeypatch.setattr(svc, "_lifecycle", lambda: FakeLife())
    monkeypatch.setattr(svc, "_track_or_terminate",
                        lambda life, ln, pid, cid, op: "" if track_ok else "track failed")
    return calls


@pytest.mark.needs_session
def test_concurrent_starts_exactly_one_reservation(tmp_path, monkeypatch):
    svc1 = _svc(tmp_path)
    svc2 = _svc(tmp_path)
    c1 = _spawnable(svc1, monkeypatch)
    c2 = _spawnable(svc2, monkeypatch)
    ln, err = svc1.spawn_auto_install_job(_sel(svc1))
    assert ln and err is None and len(c1) == 1
    assert ai_mod.read_reservation(svc1._paths)[0] == "valid"
    ln2, err2 = svc2.spawn_auto_install_job(_sel(svc2))
    assert ln2 is None and "reserved" in err2                    # refused typed...
    assert c2 == []                                              # ...BEFORE spawning


@pytest.mark.needs_session
def test_spawn_failure_removes_reservation(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _spawnable(svc, monkeypatch, spawn_ok=False)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None and "could not spawn" in err
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"  # no silent stale state
    # a subsequent start is available again
    svc2 = _svc(tmp_path)
    c2 = _spawnable(svc2, monkeypatch)
    ln2, err2 = svc2.spawn_auto_install_job(_sel(svc2))
    assert ln2 and err2 is None


@pytest.mark.needs_session
def test_spawn_failure_with_stuck_reservation_is_recovery_blocked(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _spawnable(svc, monkeypatch, spawn_ok=False)
    monkeypatch.setattr(ai_mod, "clear_reservation", lambda paths: False)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None and "acknowledge" in err                   # explicit recovery-blocked
    monkeypatch.undo()
    gate = _svc(tmp_path)._auto_install_gate()
    assert "acknowledge" in gate                                 # blocked for new runs
    assert "no child process remains" in gate                    # ...with truthful proof
    assert _svc(tmp_path).auto_install_ack().ok                          # ordinary ack suffices


def test_driver_refusal_releases_claimed_reservation(tmp_path):
    # A pre-boundary refusal (running component) must not strand the claimed slot.
    svc = _svc(tmp_path, cmdlines={555: ["loraham_kiss_tnc"]})
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"
    assert _svc(tmp_path)._auto_install_gate() == ""                     # next run available


@pytest.mark.needs_session
def test_child_claim_requires_matching_run_id(tmp_path, monkeypatch):
    # A reservation bound to ANOTHER run_id (live spawner) refuses the mismatched child.
    from lhpc.core import procident
    svc = _svc(tmp_path)
    ident = procident.proc_identity(os.getpid())
    ok, why = ai_mod.write_reservation(svc._paths, "d" * 32, os.getpid(), ident)
    assert ok
    r = svc.auto_install(apply=True, run_id="e" * 32, emit=lambda s: None)
    assert not r.ok and "reserved" in r.summary
    st, res = ai_mod.read_reservation(svc._paths)
    assert st == "valid" and res["run_id"] == "d" * 32           # foreign slot untouched


def test_dead_reservation_blocks_until_ack(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    dead = {"starttime": 1, "pgid": 1, "sid": 1, "exec": "/bin/false",
            "argv_fp": "x", "argv_len": 1}
    ok, _ = ai_mod.write_reservation(paths, "f" * 32, 999999, dead,
                                       phase="spawned")          # a bound-then-dead child
    assert ok
    svc = _svc(tmp_path)
    assert "died holding its reservation" in svc._auto_install_gate()
    ack = svc.auto_install_ack()
    assert ack.ok
    assert ai_mod.read_reservation(paths)[0] == "absent"
    assert list((tmp_path / "state").glob("auto-install-start.json.start-*.acked"))
    assert svc._auto_install_gate() == ""


# --- M2 correction: descriptor-safe archive + log -------------------------------------------

def test_archive_never_overwrites_unique_names(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    for i in range(2):
        m = ai_mod.new_marker(str(i) * 32, "install", "pinned", True, False, [])
        assert ai_mod.write_marker(paths, m)
        ok, dst = ai_mod.archive(paths, ai_mod.MARKER, "run")
        assert ok and dst.endswith(".acked")
    acked = list((tmp_path / "state").glob("auto-install.json.run-*.acked"))
    assert len(acked) == 2                                       # two distinct, no overwrite
    contents = {p.read_text()[:60] for p in acked}
    assert len(contents) == 2                                    # nothing clobbered


def test_archive_symlink_leaf_never_follows(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    d = tmp_path / "state"
    d.mkdir(parents=True)
    (d / "victim.json").write_text("PRECIOUS")
    os.symlink("victim.json", d / "auto-install.json")
    ok, dst = ai_mod.archive(paths, ai_mod.MARKER, "run")
    assert ok                                                    # the SYMLINK is archived
    assert (d / "victim.json").read_text() == "PRECIOUS"         # target untouched
    assert not (d / "auto-install.json").exists()
    archived = list(d.glob("auto-install.json.run-*.acked"))
    assert archived and archived[0].is_symlink()                 # moved as a link, unfollowed


@pytest.mark.needs_nonroot
def test_archive_failure_is_truthful(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker("a" * 32, "install", "pinned", True, False, [])
    assert ai_mod.write_marker(paths, m)
    os.chmod(tmp_path / "state", 0o500)                          # rename must fail
    try:
        ok, why = ai_mod.archive(paths, ai_mod.MARKER, "run")
    finally:
        os.chmod(tmp_path / "state", 0o700)
    assert not ok and "could not archive" in why


def test_log_chunk_symlinked_parent_fails_closed(tmp_path):
    svc = _svc(tmp_path)
    rid = "7" * 32
    real = tmp_path / "real-logs"
    real.mkdir()
    (real / (ai_mod.log_name_for(rid) + ".log")).write_text("secret")
    os.symlink("real-logs", tmp_path / "logs")                   # symlinked log PARENT
    out = svc.auto_install_log_chunk(rid, 0)
    assert "error" in out and out["data"] == ""                  # never read through it


def test_log_chunk_non_regular_leaf_fails_closed(tmp_path):
    svc = _svc(tmp_path)
    rid = "6" * 32
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / (ai_mod.log_name_for(rid) + ".log")).mkdir()   # a DIRECTORY
    out = svc.auto_install_log_chunk(rid, 0)
    assert "error" in out and out["data"] == ""


# --- M2 round-2: child-bound reservation, ack serialization, cleanup convergence -------------

def _dead_ident():
    return {"starttime": 1, "pgid": 1, "sid": 1, "exec": "/bin/false",
            "argv_fp": "x", "argv_len": 1}


@pytest.mark.needs_session
def test_child_death_before_claim_is_ackable_while_spawner_lives(tmp_path, monkeypatch):
    # The reservation is bound to the CHILD after spawn: a child that dies before claiming
    # becomes DEAD and acknowledgeable even though the web-server/spawner process (this
    # test process) is alive; a later run can then start.
    import subprocess, sys as _sys
    svc = _svc(tmp_path)
    calls = []
    class FakeLife:
        def spawn_job(self, name, argv, cwd, env=None):
            p = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
            calls.append(p.pid)
            return name + ".log", p.pid
    monkeypatch.setattr(svc, "_lifecycle", lambda: FakeLife())
    monkeypatch.setattr(svc, "_track_or_terminate", lambda *a, **k: "")
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln and err is None
    st, res = ai_mod.read_reservation(svc._paths)
    assert st == "valid" and res["phase"] == "spawned"
    assert res["pid"] == calls[0]                                # bound to the CHILD
    os.kill(calls[0], 9)                                         # child dies pre-claim
    import time as _t
    for _ in range(50):
        try:
            os.kill(calls[0], 0)
            _t.sleep(0.05)
        except OSError:
            break
    os.waitpid(calls[0], 0)                                      # reap the zombie
    gate = svc._auto_install_gate()
    assert "died holding its reservation" in gate                # dead despite live spawner
    ack = svc.auto_install_ack()
    assert ack.ok
    assert svc._auto_install_gate() == ""                                # a later run can start


def _real_child_spawn(svc, monkeypatch, kids):
    """Spawn a REAL sleeping child through the real Lifecycle (its SIGTERM-only,
    identity-verified containment stays live); only spawn_job is redirected."""
    import subprocess, sys as _sys
    from lhpc.core.lifecycle import Lifecycle
    def fake_spawn(self, name, argv, cwd, env=None):
        p = subprocess.Popen([_sys.executable, "-c",
                              "import signal,time;"
                              "signal.signal(signal.SIGTERM, lambda *a: exit(0));"
                              "time.sleep(30)"],
                             start_new_session=True)   # like the real detached spawn
        kids.append(p)
        return name + ".log", p.pid
    monkeypatch.setattr(Lifecycle, "spawn_job", fake_spawn)


def _record_signals(monkeypatch):
    sent = []
    real_kill, real_killpg = os.kill, os.killpg
    def kill(pid, sig):
        sent.append(sig)
        return real_kill(pid, sig)
    def killpg(pgid, sig):
        sent.append(sig)
        return real_killpg(pgid, sig)
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", killpg)
    return sent


@pytest.mark.needs_session
def test_bind_persist_failure_sigterm_proven_clears_reservation(tmp_path, monkeypatch):
    # Reservation bind persistence fails with a healthy child identity: containment is
    # SIGTERM-ONLY through the identity-verified primitive, exit is PROVEN, and the
    # reservation is safely removed — a later run can start. NO SIGKILL is ever sent.
    svc = _svc(tmp_path)
    kids = []
    _real_child_spawn(svc, monkeypatch, kids)
    monkeypatch.setattr(ai_mod, "bind_reservation",
                        lambda *a, **k: False)                   # persist fails
    sent = _record_signals(monkeypatch)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None and "PROVEN" in err
    import signal as _signal
    assert _signal.SIGKILL not in sent                           # NEVER SIGKILL
    assert _signal.SIGTERM in sent
    kids[0].wait(timeout=5)
    monkeypatch.undo()
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"  # cleared after proof
    svc2 = _svc(tmp_path)
    c2 = _spawnable(svc2, monkeypatch)
    ln2, err2 = svc2.spawn_auto_install_job(_sel(svc2))
    assert ln2 and err2 is None                                  # next run available


@pytest.mark.needs_session
def test_identity_capture_failure_unproven_is_recovery_blocked(tmp_path, monkeypatch):
    # Child identity cannot be captured: LHPC must not signal a process it cannot prove
    # is its own (SIGTERM-only policy also means no blind kills), must NOT claim the
    # child is gone, and must leave a truthful recovery-blocking state.
    from lhpc.core import procident
    svc = _svc(tmp_path)
    kids = []
    _real_child_spawn(svc, monkeypatch, kids)
    real_ident = procident.proc_identity
    monkeypatch.setattr(procident, "proc_identity",
                        lambda pid: real_ident(pid) if pid == os.getpid() else None)
    sent = _record_signals(monkeypatch)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None
    assert "ORPHAN RISK" in err and "acknowledge" in err          # truthful, actionable
    assert "no untracked child" not in err                        # never a false claim
    import signal as _signal
    assert _signal.SIGKILL not in sent                            # NEVER SIGKILL
    assert kids[0].poll() is None                                 # child really alive
    assert ai_mod.read_reservation(svc._paths)[0] == "valid"    # evidence retained
    assert _svc(tmp_path)._auto_install_gate() != ""                      # new runs blocked
    monkeypatch.undo()
    kids[0].terminate()                                           # test cleanup
    kids[0].wait(timeout=5)


@pytest.mark.needs_session
def test_job_tracking_failure_sigterm_proven(tmp_path, monkeypatch):
    # Marker persistence fails AFTER a successful bind: the tracked containment path is
    # also SIGTERM-only; proven exit clears the reservation.
    svc = _svc(tmp_path)
    kids = []
    _real_child_spawn(svc, monkeypatch, kids)
    monkeypatch.setattr(type(svc), "_write_job_marker",
                        lambda self, *a, **k: False)              # tracking fails
    sent = _record_signals(monkeypatch)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None and "terminated" in err
    import signal as _signal
    assert _signal.SIGKILL not in sent
    kids[0].wait(timeout=5)
    monkeypatch.undo()
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"


@pytest.mark.needs_session
def test_ack_racing_start_never_archives_live_state(tmp_path, monkeypatch):
    # A completed start (live child-bound reservation) makes acknowledgement REFUSE —
    # live evidence is never archived and no duplicate job is spawned by the loser.
    svc = _svc(tmp_path)
    _spawnable(svc, monkeypatch)                                 # binds to OUR live pid
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln and err is None
    ack = _svc(tmp_path).auto_install_ack()                              # separate instance races
    assert not ack.ok and "alive" in ack.summary
    assert ai_mod.read_reservation(svc._paths)[0] == "valid"   # nothing archived
    svc2 = _svc(tmp_path)
    c2 = _spawnable(svc2, monkeypatch)
    ln2, err2 = svc2.spawn_auto_install_job(_sel(svc2))
    assert ln2 is None and c2 == []                              # no duplicate job either


@pytest.mark.needs_session
def test_claim_refuses_wrong_phase_and_foreign_identity(tmp_path):
    from lhpc.core import procident
    svc = _svc(tmp_path)
    ident = procident.proc_identity(os.getpid())
    # phase 'spawning' (never bound): claim refuses — stale slot, not overwritten
    ok, _ = ai_mod.write_reservation(svc._paths, "a" * 32, os.getpid(), ident,
                                       phase="spawning")
    assert ok
    why = svc._auto_install_claim("a" * 32)
    assert "not in the spawned phase" in why
    st, res = ai_mod.read_reservation(svc._paths)
    assert res["phase"] == "spawning"                            # untouched
    # phase 'spawned' but bound to a DIFFERENT (dead) process: foreign slot refused
    assert ai_mod.clear_reservation(svc._paths)
    ok, _ = ai_mod.write_reservation(svc._paths, "b" * 32, 999999, _dead_ident(),
                                       phase="spawned")
    assert ok
    why2 = svc._auto_install_claim("b" * 32)
    assert "different process" in why2
    assert ai_mod.read_reservation(svc._paths)[1]["pid"] == 999999   # not overwritten


@pytest.mark.needs_session
def test_reservation_clear_failure_on_early_refusal_is_typed(tmp_path, monkeypatch):
    # Pre-boundary running refusal + reservation clear failure: the result is a TYPED
    # incomplete (never only the original refusal), and status stays safe-side.
    svc = _svc(tmp_path, cmdlines={555: ["loraham_kiss_tnc"]})
    monkeypatch.setattr(ai_mod, "clear_reservation", lambda paths: False)
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok
    assert "cleanup INCOMPLETE" in r.summary and "acknowledge" in r.summary
    monkeypatch.undo()
    assert _svc(tmp_path).auto_install_recovery_reason() == "" or \
        "reservation" in _svc(tmp_path)._auto_install_gate()             # safe-side either way


def test_recovery_reason_from_stale_lease_with_absent_marker(tmp_path):
    # DEAD lease evidence with NO run marker at all: GET-side recovery derivation still
    # reports the acknowledgement requirement (safe-side even without a marker).
    paths = Paths(runtime_root=tmp_path)
    assert ai_mod.write_lease(paths, "c" * 32, 999999, _dead_ident(), ["daemon"],
                                ["src/x"])
    svc = _svc(tmp_path)
    assert svc.auto_install_status() is None                             # marker absent
    reason = svc.auto_install_recovery_reason()
    assert "acknowledge" in reason and "lease" in reason
    ack = svc.auto_install_ack()
    assert ack.ok and svc.auto_install_recovery_reason() == ""


@pytest.mark.needs_session
def test_linked_stack_without_declared_work_is_success(tmp_path, monkeypatch):
    # A linked stack with NO declared build/test work stays SUCCESS when its auto-install work
    # (source adoption) genuinely completed.
    import dataclasses
    _happy_ops(monkeypatch)
    real_scope = ControllerService._auto_install_scope
    def scope(self):
        out = []
        for st, comps in real_scope(self):
            if st.id in ("meshcom", "meshcore"):
                comps = [dataclasses.replace(c, build_steps=(), test_argv=(),
                                             build_cmd="", test_cmd="")
                         if (c.source.strategy or "") == "link" else c for c in comps]
            out.append((st, comps))
        return out
    monkeypatch.setattr(ControllerService, "_auto_install_scope", scope)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    st = svc.auto_install_status()
    rows = {x["id"]: x["status"] for x in st["stacks"]}
    assert rows["meshcom"] == "success" and rows["meshcore"] == "success"
    assert st["state"] == "completed" and r.ok


@pytest.mark.needs_session
def test_abort_path_with_reservation_clear_failure_is_typed(tmp_path, monkeypatch):
    # Marker-write abort (exception inside the boundary) + reservation clear failure:
    # the converged cleanup path still returns a TYPED incomplete result.
    _stub_frozen(monkeypatch)
    svc = _svc(tmp_path)
    calls = {"n": 0}
    real = ai_mod.write_marker
    def failing(paths, d):
        calls["n"] += 1
        return False if calls["n"] >= 2 else real(paths, d)
    monkeypatch.setattr(ai_mod, "write_marker", failing)
    monkeypatch.setattr(ai_mod, "clear_reservation", lambda paths: False)
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok
    assert "cleanup INCOMPLETE" in r.summary and "acknowledge" in r.summary
    assert "ABORTED" in r.summary                                # original cause retained


# --- M2 round-3: strict TX admission + early callsign refusal --------------------------------

def _callsign(tmp_path, call="OE1TST"):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(
        f'[operator]\ncallsign = "{call}"\n')


def _tx_run_env(tmp_path, monkeypatch, build_ok=True, test_ok=True):
    """Happy adopts; controllable daemon build/test; recorded start/stop/tx-test."""
    from lhpc.core.install import Installer, PlanAction
    _callsign(tmp_path)
    _stub_frozen(monkeypatch)
    monkeypatch.setattr(
        Installer, "adopt_source",
        lambda self, comp, force=False, source="pinned", pinned_expected=None,
        locked=False:
        PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="ok"))
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(build_ok if t == "daemon" else True, f"build {t}"))
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(test_ok if t == "daemon" and not tx else True,
                                     f"{'tx' if tx else 'host'} {t}"))
    lifecycle = []
    monkeypatch.setattr(ControllerService, "start",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        (lifecycle.append(("start", t)), ActionResult(True, "up"))[1])
    monkeypatch.setattr(ControllerService, "stop",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        (lifecycle.append(("stop", t)), ActionResult(True, "down"))[1])
    svc = _svc(tmp_path)
    return svc, lifecycle


@pytest.mark.needs_session
def test_tx_no_callsign_driver_refuses_before_any_mutation(tmp_path):
    svc = _svc(tmp_path)                                          # no callsign configured
    r = svc.auto_install(apply=True, tests=True, tx=True, emit=lambda s: None)
    assert not r.ok and "callsign" in r.summary
    assert svc.auto_install_status() is None                              # no running marker
    assert ai_mod.read_lease(svc._paths)[0] == "absent"         # no boundary/source work
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"   # launch slot cleaned up
    assert not (tmp_path / "src").exists() or not any((tmp_path / "src").iterdir())


def test_tx_no_callsign_spawn_refuses_before_child(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    calls = _spawnable(svc, monkeypatch)
    ln, err = svc.spawn_auto_install_job(_sel(svc, tx=True))
    assert ln is None and "callsign" in err
    assert calls == []                                            # refused BEFORE spawning
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"


@pytest.mark.needs_session
def test_tx_admission_refused_when_daemon_group_blocked(tmp_path, monkeypatch):
    svc, lifecycle = _tx_run_env(tmp_path, monkeypatch)
    real = ControllerService._reconcile_group
    def reconcile(self, path, comp):
        if path == "src/loraham-daemon":
            return "blocked", "local changes present"
        return real(self, path, comp)
    monkeypatch.setattr(ControllerService, "_reconcile_group", reconcile)
    r = svc.auto_install(apply=True, tests=True, tx=True, emit=lambda s: None)
    assert not r.ok
    st = svc.auto_install_status()
    assert lifecycle == []                                        # NO daemon start, NO TX
    assert st["tx_phase"]["status"] == "fail"
    assert "before source work" in st["tx_phase"]["detail"]
    assert "blocked" in st["tx_phase"]["detail"]
    drow = next(x for x in st["stacks"] if x["id"] == "daemon")
    assert drow["status"] == "blocked"                            # row stays truthful
    assert drow["tx"]["ok"] is False and "refused" in drow["tx"]["detail"]
    assert st["state"] == "completed-with-failures"


@pytest.mark.needs_session
def test_tx_refused_on_daemon_build_failure(tmp_path, monkeypatch):
    svc, lifecycle = _tx_run_env(tmp_path, monkeypatch, build_ok=False)
    r = svc.auto_install(apply=True, tests=True, tx=True, emit=lambda s: None)
    st = svc.auto_install_status()
    assert lifecycle == []                                        # NO start, NO transmit
    drow = next(x for x in st["stacks"] if x["id"] == "daemon")
    assert drow["status"] == "fail"
    assert st["tx_phase"]["status"] == "fail"
    assert "did not complete successfully" in st["tx_phase"]["detail"]
    assert st["state"] == "completed-with-failures" and not r.ok


@pytest.mark.needs_session
def test_tx_refused_on_daemon_host_test_failure(tmp_path, monkeypatch):
    svc, lifecycle = _tx_run_env(tmp_path, monkeypatch, test_ok=False)
    r = svc.auto_install(apply=True, tests=True, tx=True, emit=lambda s: None)
    st = svc.auto_install_status()
    assert lifecycle == []
    assert st["tx_phase"]["status"] == "fail"
    drow = next(x for x in st["stacks"] if x["id"] == "daemon")
    assert drow["status"] == "fail"
    assert st["state"] == "completed-with-failures" and not r.ok


@pytest.mark.needs_session
def test_tx_allowed_when_only_independent_stack_fails(tmp_path, monkeypatch):
    # An unrelated independent failure (meshtastic deps) does not veto TX while the
    # daemon itself is fully successful with a passed host test.
    svc, lifecycle = _tx_run_env(tmp_path, monkeypatch)
    monkeypatch.setattr(ControllerService, "missing_system_deps",
                        lambda self, t: [{"install": "apt install x"}]
                        if t == "meshtastic" else [])
    r = svc.auto_install(apply=True, tests=True, tx=True, emit=lambda s: None)
    st = svc.auto_install_status()
    assert ("start", "daemon") in lifecycle and ("stop", "daemon") in lifecycle
    assert st["tx_phase"]["status"] == "success"                  # TX ran and passed
    drow = next(x for x in st["stacks"] if x["id"] == "daemon")
    assert drow["status"] == "success" and drow["tx"]["ok"] is True
    assert st["state"] == "completed-with-failures"               # meshtastic blocked
    assert not r.ok and "REMAIN installed" in r.summary


@pytest.mark.needs_session
def test_tx_happy_path_still_runs(tmp_path, monkeypatch):
    svc, lifecycle = _tx_run_env(tmp_path, monkeypatch)
    real = ControllerService._reconcile_group
    r = svc.auto_install(apply=True, tests=True, tx=True, emit=lambda s: None)
    st = svc.auto_install_status()
    assert lifecycle == [("start", "daemon"), ("stop", "daemon")]
    assert st["tx_phase"]["status"] == "success"


# --- M2 round-4: bootstrap gate, orphan-risk recovery, TX row truth, frozen selectors --------

def test_unbootstrapped_root_refuses_auto_install_with_zero_mutation(tmp_path):
    absent = tmp_path / "absent-root"                            # never created
    fake = FakeSystem(cmdlines_data={})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=absent))
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok and "not bootstrapped" in r.summary
    assert "lhpc bootstrap" in " ".join(r.next_commands + r.details)
    assert not absent.exists()                                   # ZERO mutation
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None and "not bootstrapped" in err
    assert not absent.exists()
    plan = svc.auto_install(apply=False)                          # dry-run may explain
    assert plan.ok and any("bootstrap" in d for d in plan.details)


@pytest.mark.needs_session
def test_orphan_risk_phase_requires_confirmed_ack(tmp_path, monkeypatch):
    # Identity-capture failure with an unproven child: the reservation becomes the
    # TERMINAL orphan-risk phase (child pid + reason), the gate/page demand recovery,
    # plain acknowledgement REFUSES, and only the explicit confirmation archives it.
    from lhpc.core import procident
    svc = _svc(tmp_path)
    kids = []
    _real_child_spawn(svc, monkeypatch, kids)
    real_ident = procident.proc_identity
    monkeypatch.setattr(procident, "proc_identity",
                        lambda pid: real_ident(pid) if pid == os.getpid() else None)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    monkeypatch.undo()
    assert ln is None and "ORPHAN RISK" in err
    st, res = ai_mod.read_reservation(svc._paths)
    assert st == "valid" and res["phase"] == "orphan-risk"       # terminal phase
    assert res["pid"] == kids[0].pid and "unproven" in res["reason"]
    gate = svc._auto_install_gate()
    assert "ORPHAN RISK" in gate and "acknowledge" in gate       # never "in progress"
    assert "ORPHAN RISK" in svc.auto_install_recovery_reason()           # page exposes it
    ack = svc.auto_install_ack()                                         # WITHOUT confirmation
    assert not ack.ok and "confirmation" in ack.summary
    assert ai_mod.read_reservation(svc._paths)[0] == "valid"   # nothing archived
    kids[0].terminate()                                          # operator resolves it
    kids[0].wait(timeout=5)
    ack2 = svc.auto_install_ack(confirm_orphan=True)                     # WITH confirmation
    assert ack2.ok
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"
    assert svc._auto_install_gate() == ""                                # later launch possible


@pytest.mark.needs_session
def test_rebind_write_failure_yields_orphan_risk(tmp_path, monkeypatch):
    # bind persistence fails AND cessation is unproven (SIGTERM-ignoring child):
    # orphan-risk evidence with the pid; recovery only through the confirmed path.
    import subprocess, sys as _sys
    from lhpc.core.lifecycle import Lifecycle
    svc = _svc(tmp_path)
    kids = []
    ready = tmp_path / "child-ready"
    def fake_spawn(self, name, argv, cwd, env=None):
        p = subprocess.Popen([_sys.executable, "-c",
                              "import signal,time,pathlib;"
                              "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                              f"pathlib.Path({str(ready)!r}).touch();"
                              "time.sleep(30)"], start_new_session=True)
        kids.append(p)
        for _ in range(100):                     # handshake: SIG_IGN provably armed
            if ready.exists():
                break
            time.sleep(0.05)
        return name + ".log", p.pid
    monkeypatch.setattr(Lifecycle, "spawn_job", fake_spawn)
    monkeypatch.setattr(ai_mod, "bind_reservation", lambda *a, **k: False)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    monkeypatch.undo()
    assert ln is None and "ORPHAN RISK" in err
    st, res = ai_mod.read_reservation(svc._paths)
    assert st == "valid" and res["phase"] == "orphan-risk" and res["pid"] == kids[0].pid
    assert not svc.auto_install_ack().ok                                 # plain ack refused
    os.killpg(kids[0].pid, 9) if False else kids[0].kill()       # test cleanup only
    kids[0].wait(timeout=5)
    assert svc.auto_install_ack(confirm_orphan=True).ok                  # confirmed path works


@pytest.mark.needs_session
def test_tx_refusal_after_successful_daemon_flips_row(tmp_path, monkeypatch):
    # Candidate-marker cleanup failure makes TX ineligible AFTER the daemon completed its
    # host work: the row must NOT stay `success`; host-test evidence is preserved.
    svc, lifecycle = _tx_run_env(tmp_path, monkeypatch)
    monkeypatch.setattr(ControllerService, "_retire_candidates_for_paths",
                        lambda self, paths, out: False)
    r = svc.auto_install(apply=True, tests=True, tx=True, emit=lambda s: None)
    st = svc.auto_install_status()
    assert lifecycle == []                                       # no start, no transmit
    drow = next(x for x in st["stacks"] if x["id"] == "daemon")
    assert drow["status"] == "fail"                              # never success w/ refused TX
    assert "TX" in drow["detail"] and "refused" in drow["detail"].lower()
    assert drow["tests"] == {"ran": True, "ok": True, "detail": "passed"}  # evidence kept
    assert st["tx_phase"]["status"] == "fail"
    assert st["state"] == "completed-with-failures" and not r.ok


def _ls_remote_fakes(sha_by_ref):
    """FakeSystem `commands` map builder is awkward for dynamic argv — use a runner wrap."""
    def bind(svc):
        from lhpc.core.probes.backends import CommandResult
        real = svc._system.runner.run
        def run(argv, timeout, *a, **k):
            argv = list(argv)
            if argv[:2] == ["git", "ls-remote"]:
                key = tuple(argv[2:])
                if key in sha_by_ref:
                    return CommandResult(0, sha_by_ref[key], "")
                return CommandResult(1, "", "no fake")
            return real(argv, timeout, *a, **k)
        svc._system.runner.run = run
    return bind


@pytest.mark.needs_session
def test_dev_selector_frozen_against_remote_advance(tmp_path, monkeypatch):
    # The plan resolves the dev branch ONCE; the fake remote then ADVANCES — every later
    # group adoption still receives the ORIGINAL frozen commit (no second lookup).
    from lhpc.core.install import Installer, PlanAction
    _callsign(tmp_path)
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "b"))
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "t"))
    svc = _svc(tmp_path)
    sha_a, sha_b = "a" * 40, "b" * 40
    fakes = {}
    def resolve(self, comp, source):
        fakes[comp.source.path] = fakes.get(comp.source.path, 0) + 1
        return (sha_a, "frozen: dev @ aaaaaaaaa"), ""
    monkeypatch.setattr(ControllerService, "_frozen_ref", resolve)
    seen = []
    def adopt(self, comp, force=False, source="pinned", pinned_expected=None,
              locked=False):
        seen.append((comp.source.path, pinned_expected))
        # the remote "advances" after the FIRST adoption — later groups must not care
        monkeypatch.setattr(ControllerService, "_frozen_ref",
                            lambda self2, c2, s2: ((sha_b, "moved"), ""))
        return PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="ok")
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    r = svc.auto_install(apply=True, tests=False, source="dev", emit=lambda s: None)
    assert seen, "no adoptions ran"
    assert all(exp[0] == sha_a for _, exp in seen)               # ALL groups frozen @ A
    assert all(n == 1 for n in fakes.values())                   # ONE resolution per path
    paths_adopted = [p for p, _ in seen]
    assert len(paths_adopted) == len(set(paths_adopted))         # shared path adopted once


@pytest.mark.needs_session
def test_frozen_resolution_failure_refuses_before_mutation(tmp_path, monkeypatch):
    _callsign(tmp_path)
    svc = _svc(tmp_path)                                         # FakeSystem: ls-remote fails
    r = svc.auto_install(apply=True, tests=False, source="dev", emit=lambda s: None)
    assert not r.ok and "resolution" in " ".join([r.summary] + r.details)
    assert svc.auto_install_status() is None                             # refused BEFORE the marker
    assert not (tmp_path / "src").exists() or not any((tmp_path / "src").iterdir())


def test_stable_freeze_picks_version_tag_peeled_commit(tmp_path):
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() if s.id == "daemon"
                for c in s.components if c.id == "loraham-daemon")
    remote = svc.config().remotes.get(comp.id) or comp.source.remote
    tag_sha, peel_sha, head_sha = "1" * 40, "2" * 40, "3" * 40
    _ls_remote_fakes({("--tags", remote):
                      f"{tag_sha}\trefs/tags/v1.2\n{peel_sha}\trefs/tags/v1.2^{{}}\n"
                      f"{tag_sha}\trefs/tags/beta\n",
                      (remote, "HEAD"): f"{head_sha}\tHEAD\n"})(svc)
    (fz, why) = svc._frozen_ref(comp, "stable")
    assert why == "" and fz[0] == peel_sha                       # peeled tag commit
    _ls_remote_fakes({("--tags", remote): "",
                      (remote, "HEAD"): f"{head_sha}\tHEAD\n"})(svc)
    (fz2, _) = svc._frozen_ref(comp, "stable")
    assert fz2[0] == head_sha                                    # no tags -> default HEAD


# --- M2 round-5: exception-safe settlement + frozen identity on every adoption path ----------

from lhpc.core.install import Installer as _Inst
from lhpc.core.probes import RealSystem
from lhpc.core.config import Config
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack


@pytest.mark.needs_session
def test_spawn_exception_settles_no_child(tmp_path, monkeypatch):
    # spawn_job RAISES after reservation creation (web server stays alive): the slot
    # settles durably, is never a live web-server-owned run, and ordinary ack recovers.
    from lhpc.core.lifecycle import Lifecycle
    svc = _svc(tmp_path)
    def boom(self, name, argv, cwd, env=None):
        raise OSError("fork failed")
    monkeypatch.setattr(Lifecycle, "spawn_job", boom)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    assert ln is None and "before any child existed" in err
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"  # settled: removed
    svc2 = _svc(tmp_path)
    c2 = _spawnable(svc2, monkeypatch)
    ln2, err2 = svc2.spawn_auto_install_job(_sel(svc2))
    assert ln2 and err2 is None                                  # next run fine


@pytest.mark.needs_session
def test_identity_lookup_exception_settles_unproven(tmp_path, monkeypatch):
    from lhpc.core import procident
    svc = _svc(tmp_path)
    kids = []
    _real_child_spawn(svc, monkeypatch, kids)
    real_ident = procident.proc_identity
    def ident(pid):
        if pid == os.getpid():
            return real_ident(pid)
        raise RuntimeError("proc read exploded")
    monkeypatch.setattr(procident, "proc_identity", ident)
    sent = _record_signals(monkeypatch)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    monkeypatch.undo()
    import signal as _signal
    assert ln is None and "ORPHAN RISK" in err
    assert _signal.SIGKILL not in sent
    assert _signal.SIGTERM not in sent                           # unproven pid: NO signal
    st, res = ai_mod.read_reservation(svc._paths)
    assert st == "valid" and res["phase"] == "orphan-risk"
    assert not svc.auto_install_ack().ok                                 # confirmation required
    kids[0].terminate(); kids[0].wait(timeout=5)
    assert svc.auto_install_ack(confirm_orphan=True).ok


@pytest.mark.needs_session
def test_tracker_exception_settles(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    kids = []
    _real_child_spawn(svc, monkeypatch, kids)
    def boom(life, ln, pid, cid, op):
        raise RuntimeError("marker io exploded")
    monkeypatch.setattr(svc, "_track_or_terminate", boom)
    sent = _record_signals(monkeypatch)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    monkeypatch.undo()
    import signal as _signal
    assert ln is None and "auto-install start failed" in err
    assert _signal.SIGKILL not in sent
    # SIGTERM-cooperative child + identity was bound -> containment proves the exit
    assert "PROVEN" in err
    kids[0].wait(timeout=5)
    assert ai_mod.read_reservation(svc._paths)[0] == "absent"


@pytest.mark.needs_session
def test_orphan_risk_persistence_failure_leaves_blocking_residual(tmp_path, monkeypatch):
    # write_orphan_risk fails: the residual spawning+uncertain record itself blocks,
    # is NEVER read as a live web-server run, and demands the orphan confirmation.
    from lhpc.core import procident
    svc = _svc(tmp_path)
    kids = []
    _real_child_spawn(svc, monkeypatch, kids)
    real_ident = procident.proc_identity
    monkeypatch.setattr(procident, "proc_identity",
                        lambda pid: real_ident(pid) if pid == os.getpid() else None)
    monkeypatch.setattr(ai_mod, "write_orphan_risk", lambda *a, **k: False)
    ln, err = svc.spawn_auto_install_job(_sel(svc))
    monkeypatch.undo()
    assert ln is None
    assert "could not be persisted either" in err
    st, res = ai_mod.read_reservation(svc._paths)
    assert st == "valid" and res["phase"] == "spawning"          # residual record
    assert res.get("child") == "uncertain"
    gate = svc._auto_install_gate()
    assert "ORPHAN RISK" in gate and "in progress" not in gate   # never a live run
    assert "ORPHAN RISK" in svc.auto_install_recovery_reason()           # UI reachable
    assert not svc.auto_install_ack().ok                                 # confirmation required
    kids[0].terminate(); kids[0].wait(timeout=5)
    assert svc.auto_install_ack(confirm_orphan=True).ok
    assert svc._auto_install_gate() == ""


def test_residual_spawning_none_uses_ordinary_ack(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    ok, _ = ai_mod.write_reservation(paths, "5" * 32, os.getpid(),
                                       {"starttime": 1, "pgid": 1, "sid": 1,
                                        "exec": "x", "argv_fp": "x", "argv_len": 1})
    assert ok                                                    # spawning, child="none"
    svc = _svc(tmp_path)
    gate = svc._auto_install_gate()
    assert "no child process remains" in gate                    # despite OUR live pid:
    assert "in progress" not in gate                             # never web-owned-live
    assert svc.auto_install_ack().ok                                     # ordinary ack works


# ---- frozen identity on link/copy/fallback adoption paths ----------------------------------

def _frozen_env(tmp_path, strategy=""):
    """Real local checkout at commit A with a second commit B available; installer with
    no remote (forces link/fallback paths)."""
    local = tmp_path / "rt" / "local" / "app"
    sha_a = _make_repo(local)
    (local / "file.txt").write_text("v2\n")
    _git(local, "add", "-A")
    _git(local, "commit", "-qm", "v2")
    sha_b = _git(local, "rev-parse", "HEAD")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app",
                                       strategy=strategy, branch="master"))
    cfg = Config(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "local")}})
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    inst = _Inst(Paths(runtime_root=tmp_path / "rt"), stacks, cfg, RealSystem())
    (tmp_path / "rt").mkdir(exist_ok=True)
    return inst, comp, local, sha_a, sha_b


def test_linked_dev_wrong_frozen_commit_refused(tmp_path):
    # Local checkout IS on the configured branch but at B; the frozen plan says A:
    # branch membership must not substitute for commit equality — refused pre-activation.
    inst, comp, local, sha_a, sha_b = _frozen_env(tmp_path, strategy="link")
    _git(local, "branch", "-m", "master")                        # ensure branch name
    a = inst.adopt_source(comp, source="dev", pinned_expected=(sha_a, "frozen dev"))
    assert a.status == "failed" and "does not satisfy" in a.detail
    assert not (tmp_path / "rt" / "src" / "app").exists()        # nothing activated


def test_linked_stable_wrong_frozen_commit_refused(tmp_path):
    inst, comp, local, sha_a, sha_b = _frozen_env(tmp_path, strategy="link")
    _git(local, "tag", "v1.0")                                   # exact tag at B
    object.__setattr__(comp.source, "pin_tag", "v1.0") if False else None
    a = inst.adopt_source(comp, source="stable", pinned_expected=(sha_a, "frozen stable"))
    assert a.status == "failed"                                  # tag name never suffices
    assert not (tmp_path / "rt" / "src" / "app").exists()


def test_copy_fallback_wrong_frozen_commit_refused(tmp_path):
    inst, comp, local, sha_a, sha_b = _frozen_env(tmp_path, strategy="")
    a = inst.adopt_source(comp, source="dev", pinned_expected=(sha_a, "frozen dev"))
    assert a.status == "failed"
    assert not (tmp_path / "rt" / "src" / "app").exists()


def test_artifact_fallback_wrong_frozen_commit_refused(tmp_path):
    inst, comp, local, sha_a, sha_b = _frozen_env(tmp_path, strategy="")
    art = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                    source=SourceSpec(path="src/app", local_dir="app", artifact=True))
    stacks = (Stack(id="s", name="s", main="app", components=(art,)),)
    inst2 = _Inst(Paths(runtime_root=tmp_path / "rt"),
                  stacks, Config(values={"install":
                                         {"adopt_search_root": str(tmp_path / "rt" / "local")}}),
                  RealSystem())
    a = inst2.adopt_source(art, source="stable", pinned_expected=(sha_a, "frozen"))
    assert a.status == "failed"                                  # artifact never exempts
    assert not (tmp_path / "rt" / "src" / "app").exists()


def test_exact_frozen_link_and_copy_succeed_with_registry_commit(tmp_path):
    # Exact-match frozen adoptions activate, and the registry records the frozen commit.
    inst, comp, local, sha_a, sha_b = _frozen_env(tmp_path, strategy="link")
    a = inst.adopt_source(comp, source="dev", pinned_expected=(sha_b, "frozen dev"))
    assert a.status == "done", a.detail
    assert "frozen dev" in a.detail or "dev" in a.detail
    rec = source_registry.read_record(inst.paths, "src/app")
    assert rec is not None and rec.resolved_commit == sha_b      # actual frozen commit
    # copy strategy, exact match, after removing the link cleanly
    inst2, comp2, local2, s2a, s2b = _frozen_env(tmp_path / "c2", strategy="")
    a2 = inst2.adopt_source(comp2, source="dev", pinned_expected=(s2b, "frozen dev"))
    assert a2.status == "done", a2.detail
    rec2 = source_registry.read_record(inst2.paths, "src/app")
    assert rec2 is not None and rec2.resolved_commit == s2b


def test_local_advance_after_plan_still_frozen(tmp_path):
    # The checkout moves AFTER the plan froze B: adopting with frozen B still succeeds
    # (tree at B) but a LATER adoption after the tree moved to C is refused — every
    # path stays constrained to the original frozen identity.
    inst, comp, local, sha_a, sha_b = _frozen_env(tmp_path, strategy="link")
    a = inst.adopt_source(comp, source="dev", pinned_expected=(sha_b, "frozen"))
    assert a.status == "done"
    (local / "file.txt").write_text("v3\n")
    _git(local, "add", "-A")
    _git(local, "commit", "-qm", "v3")                           # tree moves to C
    a2 = inst.adopt_source(comp, force=True, source="dev",
                           pinned_expected=(sha_b, "frozen"))    # plan still says B
    assert a2.status == "failed"                                 # C != frozen B: refused


# --- M2 round-6: artifact sources frozen for EVERY auto-install selector -----------------------------

@pytest.mark.needs_session
def test_pinned_auto_install_freezes_artifact_commit(tmp_path, monkeypatch):
    # A `pinned` auto-install plan resolves every ARTIFACT group to a non-empty exact commit and
    # passes it to adoption (artifacts never use known-working entries — the plan-time
    # default-branch HEAD is this run's immutable identity).
    from lhpc.core.install import Installer, PlanAction
    art_sha = "e" * 40
    calls = {"n": 0}
    def resolver(self, comp, source):
        calls["n"] += 1
        assert comp.source.artifact                              # only artifacts resolve
        return ((art_sha, f"frozen: declared artifact @ {art_sha[:9]}"), "")
    monkeypatch.setattr(ControllerService, "_frozen_ref", resolver)
    seen = []
    monkeypatch.setattr(
        Installer, "adopt_source",
        lambda self, comp, force=False, source="pinned", pinned_expected=None,
        locked=False:
        (seen.append((comp.source.path, comp.source.artifact, pinned_expected)),
         PlanAction("adopt", "", "x", status="done", detail="ok"))[1])
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "b"))
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, source="pinned", emit=lambda s: None)
    art = [(p, exp) for p, is_art, exp in seen if is_art]
    assert art, "no artifact adoptions ran"
    assert all(exp is not None and exp[0] == art_sha for _, exp in art)
    # shared artifact path (chat/igate: src/LoRaHAM_Daemon): resolved once, adopted once
    shared = [p for p, _ in art]
    assert shared.count("src/LoRaHAM_Daemon") == 1
    art_paths = {p for p, is_art, _ in seen if is_art}
    assert calls["n"] == len(art_paths)                          # ONE resolution per path
    # non-artifact pinned groups keep known-working/manifest-pin semantics (no freeze)
    non_art = [exp for p, is_art, exp in seen if not is_art]
    assert all(exp is None or exp[0] == "" or len(exp[0]) == 40 for exp in non_art)


@pytest.mark.needs_session
def test_artifact_remote_advance_after_plan_is_ignored(tmp_path, monkeypatch):
    # The artifact remote moves AFTER plan construction: every later artifact adoption
    # still receives the ORIGINAL frozen commit (no second lookup at adopt time).
    from lhpc.core.install import Installer, PlanAction
    sha_a, sha_b = "a" * 40, "b" * 40
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: ((sha_a, "frozen"), ""))
    seen = []
    def adopt(self, comp, force=False, source="pinned", pinned_expected=None,
              locked=False):
        if comp.source.artifact:
            seen.append((comp.source.path, pinned_expected[0]))
        monkeypatch.setattr(ControllerService, "_frozen_ref",  # remote "advances"
                            lambda self2, c2, s2: ((sha_b, "moved"), ""))
        return PlanAction("adopt", "", "x", status="done", detail="ok")
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "b"))
    svc = _svc(tmp_path)
    svc.auto_install(apply=True, tests=False, source="pinned", emit=lambda s: None)
    assert seen and all(sha == sha_a for _, sha in seen)         # ALL frozen @ A


@pytest.mark.needs_session
def test_artifact_resolution_failure_refuses_before_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: ((None, None), "remote unreachable"))
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, source="pinned", emit=lambda s: None)
    assert not r.ok and "resolution" in " ".join([r.summary] + r.details)
    assert svc.auto_install_status() is None                             # no run marker
    assert not (tmp_path / "src").exists() or not any((tmp_path / "src").iterdir())
    assert not (tmp_path / "state").exists() or not list(
        (tmp_path / "state").glob("source-registry/*"))          # no registry mutation


def test_pinned_artifact_fallback_frozen_commit_enforced(tmp_path):
    # A pinned auto-install artifact link/copy fallback at the WRONG commit is refused; the exact
    # matching checkout succeeds and the registry records the verified frozen commit.
    inst, comp, local, sha_a, sha_b = _frozen_env(tmp_path, strategy="")
    art = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                    source=SourceSpec(path="src/app", local_dir="app", artifact=True))
    stacks = (Stack(id="s", name="s", main="app", components=(art,)),)
    inst2 = _Inst(Paths(runtime_root=tmp_path / "rt"), stacks,
                  Config(values={"install":
                                 {"adopt_search_root": str(tmp_path / "rt" / "local")}}),
                  RealSystem())
    bad = inst2.adopt_source(art, source="pinned", pinned_expected=(sha_a, "frozen"))
    assert bad.status == "failed"                                # wrong commit refused
    assert not (tmp_path / "rt" / "src" / "app").exists()
    ok = inst2.adopt_source(art, source="pinned",
                            pinned_expected=(sha_b, "frozen artifact"))
    assert ok.status == "done", ok.detail
    rec = source_registry.read_record(inst2.paths, "src/app")
    assert rec is not None and rec.resolved_commit == sha_b      # verified frozen commit


def test_frozen_artifact_provenance_text_is_truthful(tmp_path):
    from lhpc.core import provenance
    from lhpc.core.probes import RealSystem as RS
    local = tmp_path / "repo"
    sha = _make_repo(local)
    class Spec:
        artifact = True
        pin_commit = ""
        pin_tag = ""
    r = provenance.evaluate(RS().runner, str(local), Spec(), "pinned", (),
                            expected_commit=sha)
    assert r.ok
    assert "FROZEN for this auto-install run" in r.detail and sha[:9] in r.detail
    assert "current default branch" not in r.detail              # never the mutable claim
    r2 = provenance.evaluate(RS().runner, str(local), Spec(), "pinned", ())
    assert "current default branch" in r2.detail                 # unfrozen text unchanged


# --- live-finding fixes: optional comps in scope; starting card; dev default -----------------

def test_auto_install_scope_includes_optional_components(tmp_path):
    # USER GOAL: every declared source lives and builds under <root>/src — optional
    # components (meshcore-cli, node-manager, firmwares, kiss-serial) are IN scope, and
    # the boundary's lock set therefore covers everything build()/test() touch (live
    # finding: 'auto-install operation context does not cover src/meshcore-cli ...').
    svc = _svc(tmp_path)
    scope = svc._auto_install_scope()
    mc = next(comps for st, comps in scope if st.id == "meshcore")
    ids = {c.id for c in mc}
    assert {"meshcore-pi", "meshcore-nodegui", "meshcore-cli"} <= ids
    all_paths = {c.source.path for _, comps in scope for c in comps}
    assert "src/meshcore-cli" in all_paths and "src/meshcore-node-manager" in all_paths


@pytest.mark.needs_session
def test_auto_install_build_context_covers_optional_paths(tmp_path, monkeypatch):
    # The driver's build call for meshcore must NOT be refused for uncovered paths.
    _happy_ops(monkeypatch)
    refusals = []
    real_err = ControllerService._auto_install_ctx_error
    def spy(self, auto_install_ctx, source_paths):
        r = real_err(self, auto_install_ctx, source_paths)
        if r:
            refusals.append(r)
        return r
    monkeypatch.setattr(ControllerService, "_auto_install_ctx_error", spy)
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    assert not refusals, refusals                                # zero coverage refusals
    st = svc.auto_install_status()
    assert all(x["status"] == "success" for x in st["stacks"])


def test_optional_secret_file_form(tmp_path):
    from lhpc.core import commands
    import pytest as _pt
    sec = tmp_path / "config" / "secrets"
    sec.mkdir(parents=True)
    items = [("XR_PASSWORD", "@file?:{runtime}/config/secrets/xr_pw")]
    env = commands.build_env(items, str(tmp_path), str(tmp_path / "src"), "")
    assert env["XR_PASSWORD"] == ""                              # absent -> disabled (legacy)
    (sec / "xr_pw").write_text("hunter2\n")
    env2 = commands.build_env(items, str(tmp_path), str(tmp_path / "src"), "")
    assert env2["XR_PASSWORD"] == "hunter2"
    # the STRICT form stays fail-closed
    with _pt.raises(commands.CommandError):
        commands.build_env([("X", "@file:{runtime}/config/secrets/nope")],
                           str(tmp_path), str(tmp_path / "src"), "")


# --- default policy: latest dev with DISCLOSED known-working fallback -------------------------

def test_freeze_dev_failure_falls_back_to_known_working(tmp_path, monkeypatch):
    # dev resolution unreachable -> the group freezes at the stack's known-working
    # composition commit (disclosed label), NOT a whole-run refusal.
    from lhpc.core import known_working
    paths = Paths(runtime_root=tmp_path)
    svc = _svc(tmp_path)                                         # ls-remote fails (Fake)
    st0 = next(s0 for s0 in svc.stacks() if s0.id == "kiss")
    comp0 = next(c for c in st0.components if c.id == "loraham-kiss-tnc")
    entries = {c.id: {"commit": "d" * 40, "selector": "dev",
                      "remote": svc._effective_remote(c),
                      "source_rel": c.source.path, "strategy": ""}
               for c in st0.components if c.source}
    known_working.record(paths, "kiss", entries, {"confirmed_at": 1.0})
    st = next(s for s in svc.stacks() if s.id == "kiss")
    items = [(st, c) for c in st.components if c.source]
    groups, conflicts = svc._plan_source_groups(items, "dev", freeze=True)
    assert conflicts is None
    path, comp, selector, resolved = groups[0]
    assert selector == "dev"
    assert resolved[0] == "d" * 40                               # KW fallback commit
    assert "fallback: known-working" in resolved[1]              # disclosed


def test_freeze_dev_failure_without_fallback_still_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "_kw_fallback_expected",
                        lambda self, st, c: ("", ""))
    svc = _svc(tmp_path)
    st = next(s for s in svc.stacks() if s.id == "kiss")
    items = [(st, c) for c in st.components if c.source]
    groups, conflicts = svc._plan_source_groups(items, "dev", freeze=True)
    assert groups is None and conflicts                          # dev strictness retained


def test_adopt_dev_failure_falls_back_disclosed(tmp_path, monkeypatch):
    # A failed dev adoption retries ONCE at the fallback identity, with the fallback
    # disclosed in the detail; a non-dev failure is untouched.
    from lhpc.core.install import Installer, PlanAction
    calls = []
    def adopt(self, comp, force=False, source="pinned", pinned_expected=None,
              locked=False):
        calls.append((source, pinned_expected))
        if source == "dev":
            return PlanAction("adopt", "", "x", status="failed", detail="clone failed")
        return PlanAction("adopt", "", "x", status="done", detail="exact checkout ok")
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    monkeypatch.setattr(ControllerService, "_kw_fallback_expected",
                        lambda self, st, c: ("e" * 40, "fallback: known-working "
                                             "(dev unreachable)"))
    svc = _svc(tmp_path)
    st = next(s for s in svc.stacks() if s.id == "kiss")
    comp = next(c for c in st.components if c.id == "loraham-kiss-tnc")
    inst = svc._installer()
    a = svc._adopt_dev_fallback(inst, st, comp, "dev", ("", ""), force=False,
                                locked=False)
    assert a.status == "done"
    assert "FELL BACK" in a.detail and "known-working" in a.detail
    assert calls[0][0] == "dev" and calls[1][0] == "pinned"
    assert calls[1][1][0] == "e" * 40                            # exact fallback commit


# ---- Part B: cooperative operator Abort ------------------------------------------------------

def test_auto_install_abort_refuses_without_live_run(tmp_path):
    svc = _svc(tmp_path)
    r = svc.auto_install_abort("a" * 32)
    assert not r.ok and "nothing to abort" in r.summary
    r2 = svc.auto_install_abort("not-a-run-id")
    assert not r2.ok


def test_auto_install_cooperative_cancel_writes_aborted(tmp_path, monkeypatch):
    # With the module abort flag already set (as the SIGTERM handler would), the driver stops at the
    # first between-stack poll and records a clean, retryable `aborted` terminal marker.
    import lhpc.core.service_auto_install as sai
    svc = _svc(tmp_path)
    svc.bootstrap(apply=True)
    monkeypatch.setattr(type(svc), "_frozen_ref",
                        lambda self, comp, source: (("f" * 40, "frozen: stub"), ""))
    monkeypatch.setattr(sai._auto_install_abort, "_v", True)   # as the SIGTERM handler would (auto-reverts)
    r = svc.auto_install(apply=True, emit=lambda s: None)
    assert not r.ok and "ABORTED" in r.summary
    state, m = ai_mod.read_marker(svc._paths)
    assert state == "valid" and m["state"] == "aborted"
    assert ai_mod.TERMINAL_OK.count("aborted") == 1              # a new run may start over it


# ---- Review round 2: build/test cancel→aborted, unverified→unsafe, scope-gated recovery ----

def test_build_cancel_writes_aborted(tmp_path, monkeypatch):
    _happy_ops(monkeypatch)
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(False, "cancelled", data={"cancelled": True, "unsafe": False}))
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    assert not r.ok and "ABORTED" in r.summary
    assert svc.auto_install_status()["state"] == "aborted"
    assert "aborted" in ai_mod.TERMINAL_OK


def test_host_test_cancel_writes_aborted(tmp_path, monkeypatch):
    _happy_ops(monkeypatch)
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(False, "cancelled", data={"cancelled": True, "unsafe": False}))
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=True, emit=lambda s: None)
    assert not r.ok and "ABORTED" in r.summary
    assert svc.auto_install_status()["state"] == "aborted"


def test_build_unverified_writes_blocking_unsafe(tmp_path, monkeypatch):
    _happy_ops(monkeypatch)
    ident = {"pid": 5, "starttime": 9, "pgid": 5, "sid": 5}
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(False, "unsafe", data={"unsafe": True, "cancelled": False,
                                     "unsafe_scope": "session-unverified", "session_ident": ident}))
    svc = _svc(tmp_path)
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    assert not r.ok and "UNSAFE" in r.summary
    st = svc.auto_install_status()
    assert st["state"] == "unsafe" and st["unsafe_scope"] == "session-unverified"
    assert st["session_ident"] == ident                          # sanitized {pid,starttime,pgid,sid}
    assert "unsafe" not in ai_mod.TERMINAL_OK                     # blocking
    assert svc._auto_install_gate()                              # gate blocks a new run


def test_host_test_unverified_escaped_writes_unsafe(tmp_path, monkeypatch):
    _happy_ops(monkeypatch)
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(False, "unsafe", data={"unsafe": True, "cancelled": False,
                                     "unsafe_scope": "escaped-or-output-unverified",
                                     "session_ident": None}))
    svc = _svc(tmp_path)
    svc.auto_install(apply=True, tests=True, emit=lambda s: None)
    st = svc.auto_install_status()
    assert st["state"] == "unsafe" and st["unsafe_scope"] == "escaped-or-output-unverified"


def _unsafe_marker(paths, rid, scope, ident=None):
    m = ai_mod.new_marker(rid, "install", "pinned", False, False, [{"id": "d", "name": "d"}])
    m["state"] = "unsafe"; m["unsafe_scope"] = scope
    m["session_ident"] = ident
    assert ai_mod.write_marker(paths, m)


def test_ack_session_unverified_gated_on_ceased(tmp_path, monkeypatch):
    from lhpc.core import proctree
    _unsafe_marker(Paths(runtime_root=tmp_path), "a" * 32, "session-unverified",
                   {"pid": 5, "starttime": 9, "pgid": 5, "sid": 5})
    svc = _svc(tmp_path)
    monkeypatch.setattr(proctree, "session_ceased", lambda token, pid: False)
    assert not svc.auto_install_ack().ok                          # still alive -> refuse
    monkeypatch.setattr(proctree, "session_ceased", lambda token, pid: True)
    assert svc.auto_install_ack().ok and svc.auto_install_status() is None


def test_ack_escaped_needs_explicit_confirm(tmp_path):
    _unsafe_marker(Paths(runtime_root=tmp_path), "b" * 32, "escaped-or-output-unverified")
    svc = _svc(tmp_path)
    assert not svc.auto_install_ack().ok                          # no confirm -> refuse
    assert svc.auto_install_ack(confirm_orphan=True).ok           # explicit ack -> archived
    assert svc.auto_install_status() is None


def test_unsafe_marker_blocks_outermost_source_op(tmp_path):
    from lhpc.core.service_base import SourceTxnBlocked
    _unsafe_marker(Paths(runtime_root=tmp_path), "c" * 32, "escaped-or-output-unverified")
    svc = _svc(tmp_path); svc.bootstrap(apply=True)
    with pytest.raises(SourceTxnBlocked):
        with svc._source_operation_guard(["src/LoRaHAM_Daemon"], op="build"):
            pass
    with svc._source_operation_guard(["src/LoRaHAM_Daemon"], op="auto-install"):  # exempt
        pass


def test_tx_loop_cancels_between_bands(tmp_path, monkeypatch):
    from lhpc.core.config import save_operator_config
    svc = _svc(tmp_path)
    save_operator_config(svc._paths, "N0CALL"); svc._invalidate_config()

    class _V:
        ready = True
    monkeypatch.setattr(ControllerService, "daemon_view", lambda self, b: _V())
    calls = []

    class _R:
        def __init__(self, band):
            self.band, self.detail, self.txok_before, self.txok_after, self.ok = band, "ok", 0, 1, True
    from lhpc.core.lifecycle import Lifecycle
    monkeypatch.setattr(Lifecycle, "run_daemon_tx_test",
                        lambda self, band, payload: (calls.append(band), _R(band))[1])
    n = {"i": 0}

    def cancel():
        n["i"] += 1
        return n["i"] > 1                                         # False before band 1, True before band 2
    r = svc.test("daemon", tx=True, apply=True, should_cancel=cancel)
    assert not r.ok and (r.data or {}).get("cancelled") and len(calls) == 1
    assert r.data["attempted_bands"] == calls                    # a frame really went out


def test_marker_carries_per_stack_selection(tmp_path, monkeypatch):
    _happy_ops(monkeypatch)
    svc = _svc(tmp_path)
    svc.auto_install(apply=True, source="stable", tests=False, emit=lambda s: None)
    st = svc.auto_install_status()
    for row in st["stacks"]:
        assert row["selected"]["version"] == "stable" and row["selected"]["tests"] is False
    # valid_marker: absent selected accepted (back-compat), malformed rejected
    ok = ai_mod.new_marker("e" * 32, "install", "dev", True, False, [{"id": "d", "name": "d"}])
    assert ai_mod.valid_marker(ok)
    bad = ai_mod.new_marker("f" * 32, "install", "dev", True, False,
                            [{"id": "d", "name": "d", "selected": {"version": "nope",
                                                                   "tests": True, "tx": False}}])
    assert not ai_mod.valid_marker(bad)
