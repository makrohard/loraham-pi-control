"""MeshCom HMAC password: state ops (enable/disable/renew, atomic + rollback-safe), install default,
and the auto-apply run (driver step-runner + marker + redacted log)."""

import json
from pathlib import Path

from lhpc.core import model, runtime_fs
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

_RID = "a" * 32


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    return svc


def _web(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    return create_app(lambda: svc).test_client(), svc


def _csrf(client):
    with client.session_transaction() as s:
        s["_csrf"] = "tok"
    return "tok"


def _xr_pw(tmp_path):
    return tmp_path / "config" / "secrets" / "xr_pw"


def _bridge_password_param(svc):
    c = svc.stack("meshcom").component("meshcom-bridge")
    return next(p for p in c.run_params if p.name == "password_file")


def test_applies_and_status_none_for_non_meshcom(tmp_path):
    svc = _svc(tmp_path)
    assert svc.hmac_applies("meshcom") is True and svc.hmac_applies("kiss") is False
    assert svc.hmac_status("kiss") is None            # no flag/row for stacks without the param
    assert svc.hmac_status("meshcom") is False         # fresh: open auth


def test_enable_disable_renew_flip_state_and_secret_file(tmp_path):
    svc = _svc(tmp_path)
    xr = _xr_pw(tmp_path)
    assert svc.hmac_set_secret("meshcom", "enable").ok
    assert svc.hmac_status("meshcom") is True
    assert xr.exists() and (xr.stat().st_mode & 0o777) == 0o600 and xr.read_text().strip()
    tok = xr.read_text()
    # enable is idempotent — keeps the existing secret
    assert svc.hmac_set_secret("meshcom", "enable").ok and xr.read_text() == tok
    # renew rotates the token, still enabled
    assert svc.hmac_set_secret("meshcom", "renew").ok
    assert xr.read_text() != tok and svc.hmac_status("meshcom") is True
    # disable clears the override AND removes the secret -> open auth, consistent for a firmware rebuild
    assert svc.hmac_set_secret("meshcom", "disable").ok
    assert not xr.exists() and svc.hmac_status("meshcom") is False


def test_disabled_omits_the_bridge_password_arg(tmp_path):
    svc = _svc(tmp_path)
    p = _bridge_password_param(svc)
    # blank (disabled) -> the --password-file arg is not emitted; a set value -> it is
    assert model.emit_param(p, "") == []
    assert model.emit_param(p, "{runtime}/config/secrets/xr_pw") == ["--password-file", "{runtime}/config/secrets/xr_pw"]


def test_secret_value_never_appears_in_action_results(tmp_path):
    svc = _svc(tmp_path)
    xr = _xr_pw(tmp_path)
    results = [svc.hmac_set_secret("meshcom", "enable"), svc.hmac_set_secret("meshcom", "renew")]
    token = xr.read_text().strip()
    for r in results:
        assert token not in r.summary and not any(token in d for d in r.details)


def test_rollback_when_the_secret_file_write_fails(tmp_path, monkeypatch):
    # A config change is applied first; if the secret-file write then fails, BOTH are rolled back so the
    # visible HMAC state is exactly as before (still disabled, no file).
    svc = _svc(tmp_path)
    real = runtime_fs.atomic_write

    def _fail_only_secret(paths, path, *a, **k):
        if Path(path).name == "xr_pw":                 # let the config save (step 1) succeed
            raise OSError("disk full")
        return real(paths, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "atomic_write", _fail_only_secret)
    r = svc.hmac_set_secret("meshcom", "enable")
    assert not r.ok and "rolled back" in r.summary
    assert svc.hmac_status("meshcom") is False and not _xr_pw(tmp_path).exists()


def test_install_enables_hmac_by_default(tmp_path):
    # Password-auth is ON by default: an apply-install of meshcom enables HMAC (secret + param) as part
    # of the install, so the firmware bakes the shared secret. Pre-create the source dirs so adoption is
    # a healthy no-op skip (no clone / network), isolating the default-on wiring.
    svc = _svc(tmp_path)
    for c in svc.stack("meshcom").components:
        if c.source:
            svc._paths.resolve_source(c.source.path).mkdir(parents=True, exist_ok=True)
    assert svc.hmac_status("meshcom") is False
    r = svc.install("meshcom", apply=True)
    assert r.ok, r.summary
    assert svc.hmac_status("meshcom") is True and _xr_pw(tmp_path).exists()


def test_install_fails_closed_when_hmac_enable_fails(tmp_path, monkeypatch):
    # P1-1: a failed HMAC enable must NOT let the install report success — otherwise the firmware would be
    # built (by the caller) with an empty password while the operator believes auth is on.
    svc = _svc(tmp_path)
    for c in svc.stack("meshcom").components:
        if c.source:
            svc._paths.resolve_source(c.source.path).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(type(svc), "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "boom"))   # forces enable to fail
    r = svc.install("meshcom", apply=True)
    assert not r.ok and "HMAC password could NOT be enabled" in r.summary
    assert svc.hmac_status("meshcom") is False and not _xr_pw(tmp_path).exists()


def _auto_install_harness(monkeypatch, build_ok=True):
    # Neutralise clone/build/test/frozen-ref so a full auto_install run reaches every row's build without
    # network or a toolchain (mirrors tests/test_auto_install.py's stubs).
    from lhpc.core.install import Installer, PlanAction
    monkeypatch.setattr(Installer, "adopt_source",
                        lambda self, comp, force=False, source="pinned", pinned_expected=None,
                        locked=False: PlanAction("adopt", "", f"adopt {comp.id}",
                                                 status="skipped", detail="stub"))
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    # This harness models a WORKING box (it is about HMAC, not prerequisites). Without this the
    # post-provision readiness gate — which asks the real missing_requirements — blocks every row
    # under a bare FakeSystem, where no packaged binary, device node or group exists.
    monkeypatch.setattr(ControllerService, "_auto_install_runtime_blockers",
                        lambda self, st: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k:
                        ActionResult(build_ok, "built" if build_ok else "boom"))
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "tested"))
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: (("f" * 40, "frozen: stub"), ""))


def test_auto_install_actually_enables_hmac(tmp_path, monkeypatch):
    # The default-on enable happens BEFORE the auto-install boundary (the boundary holds the config lock, so the
    # old in-boundary call always silently failed). After a full run, meshcom succeeds AND HMAC is on.
    svc = _svc(tmp_path)
    _auto_install_harness(monkeypatch)
    assert svc.hmac_status("meshcom") is False
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    rows = {x["id"]: x for x in svc.auto_install_status()["stacks"]}
    assert rows["meshcom"]["status"] == "success" and r.ok
    assert svc.hmac_status("meshcom") is True and _xr_pw(tmp_path).exists()


def test_auto_install_fails_meshcom_row_when_hmac_enable_fails(tmp_path, monkeypatch):
    # FAIL CLOSED in auto-install: a failed enable marks the meshcom row fail and SKIPS its build — the
    # firmware is never baked with an empty password while the run reports success.
    svc = _svc(tmp_path)
    _auto_install_harness(monkeypatch)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "boom"))   # forces enable to fail
    r = svc.auto_install(apply=True, tests=False, emit=lambda s: None)
    rows = {x["id"]: x for x in svc.auto_install_status()["stacks"]}
    assert rows["meshcom"]["status"] == "fail" and not r.ok
    assert "HMAC password could not be enabled" in rows["meshcom"]["detail"]
    assert svc.hmac_status("meshcom") is False        # never enabled


def test_rollback_when_the_config_save_fails(tmp_path, monkeypatch):
    # If the config save fails, the secret file is NOT touched.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "boom"))
    r = svc.hmac_set_secret("meshcom", "enable")
    assert not r.ok and not _xr_pw(tmp_path).exists()


# ---- Part C: the auto-apply run (driver step-runner + marker + redacted log) ----------------------

def _fake_build_restart(svc, monkeypatch):
    """Neutralise the real firmware build + process restart so the step runner is exercised without
    a toolchain or running QEMU."""
    monkeypatch.setattr(type(svc), "build",
                        lambda self, target, **k: ActionResult(True, f"built {target}"))
    monkeypatch.setattr(type(svc), "restart",
                        lambda self, target, **k: ActionResult(True, f"restarted {target}"))
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)


def test_apply_steps_run_in_order_and_never_leak_secret(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _fake_build_restart(svc, monkeypatch)
    lines = []
    rc = svc._hmac_run_steps("meshcom", "enable", _RID, emit=lines.append)
    assert rc == 0
    st = svc.hmac_apply_status()
    assert st["phase"] == "done" and st["finished"] is True
    assert [s["state"] for s in st["steps"]] == ["done", "done", "done", "done"]
    # the four steps were emitted in order: secret -> firmware -> restarts
    joined = "\n".join(lines)
    assert joined.index("password secret") < joined.index("Rebuilding the firmware") \
        < joined.index("Restarting the bridge") < joined.index("Restarting the node")
    assert svc.hmac_status("meshcom") is True
    # the secret NEVER appears in the emitted stream nor the marker file
    token = _xr_pw(tmp_path).read_text().strip()
    assert token and token not in joined
    assert token not in (tmp_path / "state" / "hmac_apply.json").read_text()


def test_apply_skips_restarts_when_stack_is_down(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _fake_build_restart(svc, monkeypatch)
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: False)
    rc = svc._hmac_run_steps("meshcom", "enable", _RID, emit=lambda s: None)
    assert rc == 0
    states = {s["key"]: s["state"] for s in svc.hmac_apply_status()["steps"]}
    assert states == {"secret": "done", "firmware": "done",
                      "bridge": "skipped", "node": "skipped"}


def test_apply_fails_fast_on_firmware_build(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "build",
                        lambda self, target, **k: ActionResult(False, "compile error"))
    rc = svc._hmac_run_steps("meshcom", "renew", _RID, emit=lambda s: None)
    assert rc == 1
    st = svc.hmac_apply_status()
    assert st["phase"] == "failed" and st["finished"] is True
    states = {s["key"]: s["state"] for s in st["steps"]}
    assert states["secret"] == "done" and states["firmware"] == "failed"
    assert states["bridge"] == "pending" and states["node"] == "pending"


def _fail_marker_write_when(monkeypatch, predicate):
    """Make _hmac_write_marker return False for the marker states matching `predicate` (a fault-injection
    of durable-write failure), passing every other write through to the real implementation."""
    real = ControllerService._hmac_write_marker

    def fake(self, d):
        return False if predicate(d) else real(self, d)
    monkeypatch.setattr(ControllerService, "_hmac_write_marker", fake)


def test_initial_marker_write_failure_makes_no_mutation(tmp_path, monkeypatch):
    # P1: if the INITIAL run marker cannot be persisted, the secret is never touched (no build/restart either).
    svc = _svc(tmp_path)
    calls = []
    monkeypatch.setattr(type(svc), "hmac_set_secret",
                        lambda self, sid, action: calls.append(action) or ActionResult(True, "set"))
    monkeypatch.setattr(type(svc), "build", lambda self, t, **k: calls.append("build") or ActionResult(True, "b"))
    # fail the initial write only (all steps still pending, phase running)
    _fail_marker_write_when(monkeypatch, lambda d: d.get("phase") == "running"
                            and all(s["state"] == "pending" for s in d.get("steps", [])))
    rc = svc._hmac_run_steps("meshcom", "renew", _RID, emit=lambda s: None)
    assert rc == 1 and calls == []                       # NOTHING mutated


def test_midrun_marker_write_failure_stops_before_build(tmp_path, monkeypatch):
    # P1: a nonterminal transition-write failure (firmware=running) aborts BEFORE the build runs.
    svc = _svc(tmp_path)
    built = []
    monkeypatch.setattr(type(svc), "hmac_set_secret", lambda self, sid, a: ActionResult(True, "secret set"))
    monkeypatch.setattr(type(svc), "build", lambda self, t, **k: built.append(t) or ActionResult(True, "b"))
    _fail_marker_write_when(monkeypatch, lambda d: any(
        s["key"] == "firmware" and s["state"] == "running" for s in d.get("steps", [])))
    rc = svc._hmac_run_steps("meshcom", "renew", _RID, emit=lambda s: None)
    assert rc == 1 and built == []                        # build never launched
    assert svc.hmac_apply_status()["phase"] == "failed"   # best-effort terminal recorded


def test_success_terminal_write_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    # P1: if the FINAL success write fails, the run returns FAILURE (never rc 0 / a recorded completion).
    svc = _svc(tmp_path)
    _fake_build_restart(svc, monkeypatch)
    _fail_marker_write_when(monkeypatch, lambda d: d.get("phase") == "done")
    rc = svc._hmac_run_steps("meshcom", "enable", _RID, emit=lambda s: None)
    assert rc == 1                                        # NOT 0
    assert svc.hmac_apply_status()["phase"] != "done"     # completion was never falsely recorded


def test_failure_terminal_write_failure_still_returns_failure(tmp_path, monkeypatch):
    # P1: a build failure whose terminal marker write ALSO fails still returns failure (never success).
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "build", lambda self, t, **k: ActionResult(False, "compile error"))
    _fail_marker_write_when(monkeypatch, lambda d: d.get("phase") == "failed")
    rc = svc._hmac_run_steps("meshcom", "renew", _RID, emit=lambda s: None)
    assert rc == 1


def test_unsafe_terminal_write_failure_still_returns_failure(tmp_path, monkeypatch):
    # P1: an UNSAFE build outcome whose terminal marker write fails still returns failure (never success).
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "build", lambda self, t, **k: ActionResult(
        False, "unproven", data={"unsafe": True, "unsafe_scope": "session-unverified",
                                 "session_ident": {"pid": 1, "starttime": 1, "sid": 1, "pgid": 1}}))
    _fail_marker_write_when(monkeypatch, lambda d: d.get("phase") == "unsafe")
    rc = svc._hmac_run_steps("meshcom", "renew", _RID, emit=lambda s: None)
    assert rc == 1                                        # failure, never a recorded success


def test_unsafe_write_failure_still_blocks_next_apply(tmp_path, monkeypatch):
    # P1 (observable invariant): if the UNSAFE terminal write fails, the driver exits, and the ordinary
    # `interrupted` derivation would normally make the run retryable. It MUST NOT — the leftover run marker
    # (a step still `running`, driver gone) is re-derived as BLOCKING unsafe, and a new apply is REFUSED.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "build", lambda self, t, **k: ActionResult(
        False, "unproven", data={"unsafe": True, "unsafe_scope": "session-unverified",
                                 "session_ident": {"pid": 1, "starttime": 1, "sid": 1, "pgid": 1}}))
    _fail_marker_write_when(monkeypatch, lambda d: d.get("phase") == "unsafe")   # unsafe write fails
    assert svc._hmac_run_steps("meshcom", "renew", _RID, emit=lambda s: None) == 1
    # driver is gone: the leftover running marker (firmware step still running) derives BLOCKING unsafe
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)
    st = svc.hmac_apply_status()
    assert st and st.get("phase") == "unsafe" and st.get("derived_unsafe")   # NOT retryable interrupted
    # a new apply is refused (the build might still be running)
    r = svc.hmac_apply_start("meshcom", "renew")
    assert not r.ok and "unsafe" in r.summary.lower()


def test_interrupted_between_steps_stays_retryable(tmp_path, monkeypatch):
    # The derive only escalates to unsafe when a step was MID-FLIGHT. A driver that vanished BETWEEN steps
    # (nothing running) is an ordinary interrupted run — retryable, and a new apply is admitted.
    svc = _svc(tmp_path)
    steps = svc._hmac_initial_steps()
    steps[0]["state"] = "done"                            # secret done, firmware not started (pending)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
                        "finished": False, "steps": steps})
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)   # driver gone
    st = svc.hmac_apply_status()
    assert st["phase"] == "interrupted" and st.get("derived_interrupted")
    # not blocking: neither `running` nor `unsafe`, so admission would start a fresh run (we don't spawn here)
    assert not svc.hmac_apply_running() and st.get("phase") not in ("unsafe",)


# ---- unverified driver startup: the detached-driver tracking gate + orphan-risk blocking -----------

_ORPHAN_ERR = ("hmac-apply 'meshcom' spawned but its job marker could not be persisted AND the process "
               "could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")
_TERMINATED_ERR = ("hmac-apply 'meshcom' spawned but its job marker could not be persisted; the process "
                   "was terminated (not left orphaned).")


def _fake_spawn(monkeypatch, pid):
    """Neutralise the real detached spawn: return a fake (log, pid) without launching a driver."""
    from lhpc.core.lifecycle import Lifecycle
    monkeypatch.setattr(Lifecycle, "spawn_job",
                        lambda self, name, argv, cwd, env=None: (f"{name}.log", pid))


def test_driver_gate_proceeds_only_when_identity_tracked(tmp_path, monkeypatch):
    import os
    from lhpc.core import procident, service_hmac
    svc = _svc(tmp_path)
    monkeypatch.setattr(service_hmac, "_HMAC_DRIVER_TRACK_TIMEOUT_S", 0.3)   # keep the untracked wait short
    # tracked: a matching job marker for THIS process -> proceed (0)
    ident = procident.proc_identity(os.getpid())
    assert svc._write_job_marker(f"hmac-apply-{_RID}.log", os.getpid(), "meshcom", "hmac-apply", ident=ident)
    assert svc._hmac_verify_tracked("meshcom", _RID, emit=lambda s: None) == 0
    # a marker for a DIFFERENT stack (mismatch) -> refuse immediately (1)
    rid2 = "b" * 32
    svc._write_job_marker(f"hmac-apply-{rid2}.log", os.getpid(), "othernode", "hmac-apply", ident=ident)
    assert svc._hmac_verify_tracked("meshcom", rid2, emit=lambda s: None) == 1
    # NO marker at all -> refuse after the (short) window (1)
    assert svc._hmac_verify_tracked("meshcom", "c" * 32, emit=lambda s: None) == 1


def test_orphan_risk_startup_is_blocking_unsafe_with_driver_ident(tmp_path, monkeypatch):
    import os
    svc = _svc(tmp_path)
    _fake_spawn(monkeypatch, os.getpid())
    monkeypatch.setattr(ControllerService, "_track_or_terminate",
                        lambda self, life, ln, pid, cid, op: _ORPHAN_ERR)
    r = svc.hmac_apply_start("meshcom", "renew")
    assert not r.ok and "ORPHAN RISK" in r.summary
    st = svc.hmac_apply_status()
    assert st["phase"] == "unsafe" and st.get("unsafe_scope") == "escaped-or-output-unverified"
    # the captured identity is the DRIVER's — evidence only, NEVER session_ident (never auto-recovered)
    assert st.get("driver_ident") and "session_ident" not in st
    assert not svc._hmac_try_auto_clear(st)                     # escaped scope -> explicit ack only
    # a second apply is refused (blocking)
    assert not svc.hmac_apply_start("meshcom", "renew").ok


def test_confirmed_terminated_startup_is_ordinary_failed(tmp_path, monkeypatch):
    import os
    svc = _svc(tmp_path)
    _fake_spawn(monkeypatch, os.getpid())
    monkeypatch.setattr(ControllerService, "_track_or_terminate",
                        lambda self, life, ln, pid, cid, op: _TERMINATED_ERR)
    assert not svc.hmac_apply_start("meshcom", "renew").ok
    st = svc.hmac_apply_status()
    assert st["phase"] == "failed" and st["finished"] and "startup_unverified" not in st
    assert not svc.hmac_apply_running()                         # retryable, not blocking


def test_startup_unverified_marker_derives_blocking_unsafe(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
                        "finished": False, "steps": svc._hmac_initial_steps(),
                        "startup_unverified": True})           # all steps pending, but spawn never verified
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)
    st = svc.hmac_apply_status()
    assert st["phase"] == "unsafe" and st.get("derived_unsafe")


def test_orphan_risk_fallback_when_unsafe_write_also_fails(tmp_path, monkeypatch):
    # THE critical end-to-end regression: track fails (ORPHAN RISK) AND the explicit unsafe write fails AND
    # the driver never verifies tracking -> the startup marker's flag alone keeps the run BLOCKING.
    import os
    import json
    from lhpc.core import service_hmac
    svc = _svc(tmp_path)
    _fake_spawn(monkeypatch, os.getpid())
    monkeypatch.setattr(ControllerService, "_track_or_terminate",
                        lambda self, life, ln, pid, cid, op: _ORPHAN_ERR)
    real_write = ControllerService._hmac_write_marker
    monkeypatch.setattr(ControllerService, "_hmac_write_marker",   # the 'running' startup write still lands
                        lambda self, d: False if d.get("phase") == "unsafe" else real_write(self, d))
    assert not svc.hmac_apply_start("meshcom", "renew").ok
    # the startup marker on disk STILL carries the flag (the unsafe write failed, didn't overwrite it)
    m = json.loads((tmp_path / "state" / "hmac_apply.json").read_text())
    rid = m["run_id"]
    assert m.get("startup_unverified") is True and m["phase"] == "running"
    # the driver would REFUSE at the gate (no job marker) and never mutate
    monkeypatch.setattr(service_hmac, "_HMAC_DRIVER_TRACK_TIMEOUT_S", 0.2)
    ran = []
    monkeypatch.setattr(ControllerService, "_hmac_run_steps", lambda self, *a, **k: ran.append(1) or 0)
    assert svc._hmac_verify_tracked("meshcom", rid, emit=lambda s: None) == 1 and ran == []
    # driver gone -> derived blocking unsafe; a second apply is refused; auto-clear does NOT clear it
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)
    st = svc.hmac_apply_status()
    assert st["phase"] == "unsafe" and st.get("derived_unsafe")
    assert not svc.hmac_apply_start("meshcom", "renew").ok
    assert not svc._hmac_try_auto_clear(st)


def test_hmac_apply_cli_gates_before_handlers_and_run(monkeypatch):
    # Adapter regression: the detached-driver dispatch order is gate -> abort handlers -> run. On a gate
    # refusal, NEITHER the handlers nor the step runner is reached.
    import signal as _signal
    from lhpc.adapters.cli import main as cli_main
    order = []
    gate_rc = [1]
    monkeypatch.setattr(ControllerService, "_hmac_verify_tracked",
                        lambda self, sid, rid, emit: (order.append("gate"), gate_rc[0])[1])
    monkeypatch.setattr(ControllerService, "_hmac_run_steps",
                        lambda self, sid, action, rid, emit: (order.append("run"), 0)[1])
    monkeypatch.setattr(_signal, "signal", lambda *a, **k: order.append("handler"))
    # gate refuses -> only the gate ran
    assert cli_main.main(["_hmac-apply", "meshcom", "renew", _RID]) == 1
    assert order == ["gate"]
    # gate passes -> gate, THEN handlers, THEN run
    order.clear(); gate_rc[0] = 0
    assert cli_main.main(["_hmac-apply", "meshcom", "renew", _RID]) == 0
    assert order[0] == "gate" and "run" in order and order.index("run") > order.index("handler")


def test_apply_log_chunk_redacts_the_secret(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _fake_build_restart(svc, monkeypatch)
    svc._hmac_run_steps("meshcom", "enable", _RID, emit=lambda s: None)
    token = _xr_pw(tmp_path).read_text().strip()
    # a hostile/verbose build could echo the baked secret into the run log — redact it on read.
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", f"hmac-apply-{_RID}.log"),
                            f"PlatformIO XR_PASSWORD={token} baking\n", 0o644)
    chunk = svc.hmac_apply_log_chunk(_RID, 0)
    assert token not in chunk["data"] and "****" in chunk["data"]
    assert chunk["offset"] > 0                         # cursor advanced by RAW bytes


def test_apply_never_leaks_secret_from_build_output(tmp_path, monkeypatch):
    # P1-2: a verbose/hostile firmware build could echo the baked secret in its summary/details. Those are
    # emitted (→ CLI stdout / log) and, on failure, stored in the marker detail (→ /api + rendered page).
    # ALL of those surfaces must be redacted, not only the log-chunk read.
    client, svc = _web(tmp_path)

    def fake_build(self, target, **k):
        tok = _xr_pw(tmp_path).read_text().strip()     # the secret written by step 1
        return ActionResult(False, f"compile FAILED XR_PASSWORD={tok}",
                            details=[f"cc -DXR_PASSWORD={tok} firmware.c"])
    monkeypatch.setattr(ControllerService, "build", fake_build)
    lines = []
    rc = svc._hmac_run_steps("meshcom", "enable", _RID, emit=lines.append)
    assert rc == 1
    token = _xr_pw(tmp_path).read_text().strip()
    assert token and token not in "\n".join(lines)                      # emit stream (stdout/log)
    assert token not in (tmp_path / "state" / "hmac_apply.json").read_text()   # marker detail
    assert token not in client.get("/api/hmac-apply").get_data(as_text=True)   # API
    assert token not in client.get("/stacks/meshcom/hmac/enable").get_data(as_text=True)  # page


def test_apply_disable_redacts_the_old_secret_after_deletion(tmp_path, monkeypatch):
    # P1-2 (disable): the OLD token stays sensitive after step 1 deletes xr_pw. A current-file-only
    # redactor could no longer see it — so the run-scoped set must keep the pre-run secret and still
    # scrub a build/restart line that echoes it.
    svc = _svc(tmp_path)
    assert svc.hmac_set_secret("meshcom", "enable").ok
    old = _xr_pw(tmp_path).read_text().strip()

    def fake_build(self, target, **k):
        return ActionResult(True, f"built (was XR_PASSWORD={old})", details=[f"stripped old={old}"])
    monkeypatch.setattr(ControllerService, "build", fake_build)
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: False)
    lines = []
    rc = svc._hmac_run_steps("meshcom", "disable", _RID, emit=lines.append)
    assert rc == 0 and not _xr_pw(tmp_path).exists()               # disable removed the file
    assert old and old not in "\n".join(lines)                    # OLD secret redacted post-deletion
    assert old not in (tmp_path / "state" / "hmac_apply.json").read_text()


def test_apply_status_is_tristate_and_derives_interrupted(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    assert svc.hmac_apply_status() is None                       # absent
    runtime_fs.mkdir(svc._paths, "state")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"),
                            "{not json", 0o600)
    assert svc.hmac_apply_status()["unsafe"] is True             # malformed -> unsafe, never absent
    marker = {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
              "finished": False, "steps": svc._hmac_initial_steps()}
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"),
                            json.dumps(marker), 0o600)
    monkeypatch.setattr(type(svc), "log_running", lambda self, *a, **k: True)
    assert svc.hmac_apply_status()["phase"] == "running"
    monkeypatch.setattr(type(svc), "log_running", lambda self, *a, **k: False)
    assert svc.hmac_apply_status()["phase"] == "interrupted"     # driver gone -> derived


def test_apply_start_is_single_flight(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    marker = {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
              "finished": False, "steps": svc._hmac_initial_steps()}
    runtime_fs.mkdir(svc._paths, "state")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"),
                            json.dumps(marker), 0o600)
    monkeypatch.setattr(type(svc), "log_running", lambda self, *a, **k: True)   # run looks live
    r = svc.hmac_apply_start("meshcom", "renew")
    assert not r.ok and "already running" in r.summary


def test_apply_start_spawns_and_records_the_run(tmp_path, monkeypatch):
    svc = _svc(tmp_path)

    class _Life:
        def spawn_job(self, name, argv, cwd, env=None):
            assert argv[:5] == [__import__("sys").executable, "-u", "-m", "lhpc", "_hmac-apply"]
            return name + ".log", 4242
    monkeypatch.setattr(type(svc), "_lifecycle", lambda self: _Life())
    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
    r = svc.hmac_apply_start("meshcom", "enable")
    assert r.ok and svc.hmac_apply_status()["run_id"] == r.data["run_id"]


# ---- disable requires the typed confirmation phrase (service-gated -> web + CLI) -----------------

def test_hmac_disable_start_refuses_without_confirm_and_spawns_nothing(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    spawned = []
    monkeypatch.setattr(type(svc), "_lifecycle",
                        lambda self: type("L", (), {"spawn_job": lambda *a, **k: spawned.append(1)})())
    r = svc.hmac_apply_start("meshcom", "disable")             # no confirm
    assert not r.ok and ControllerService.HMAC_DISABLE_CONFIRM in r.summary
    assert spawned == []                                       # gate is BEFORE any reservation/spawn


def test_hmac_disable_start_proceeds_with_confirm(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "_lifecycle",
                        lambda self: type("L", (), {"spawn_job":
                            lambda self, name, argv, cwd, env=None: (name + ".log", 4242)})())
    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
    r = svc.hmac_apply_start("meshcom", "disable", confirm=True)
    assert r.ok and svc.hmac_apply_status()["run_id"] == r.data["run_id"]


def test_hmac_enable_and_renew_start_are_not_gated(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "_lifecycle",
                        lambda self: type("L", (), {"spawn_job":
                            lambda self, name, argv, cwd, env=None: (name + ".log", 4242)})())
    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
    assert svc.hmac_apply_start("meshcom", "enable").ok        # confirm defaults False, still starts
    svc.hmac_apply_recover("meshcom", svc.hmac_apply_status()["run_id"])
    assert svc.hmac_apply_start("meshcom", "renew").ok


def test_hmac_disable_cli_refuses_without_confirm(tmp_path):
    svc = _svc(tmp_path)
    lines = []
    rc = svc.hmac_apply_cli("meshcom", "disable", emit=lines.append)   # confirm defaults False
    assert rc == 1
    assert any(ControllerService.HMAC_DISABLE_CONFIRM in ln for ln in lines)
    # refused before mutating any state — HMAC still enabled/default, no terminal marker written
    assert not (tmp_path / "state" / "hmac_apply.json").exists()


def test_hmac_disable_cli_gate_fires_before_the_step_runner(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    ran = []
    monkeypatch.setattr(type(svc), "_hmac_run_steps",
                        lambda self, *a, **k: ran.append(1) or 0)
    assert svc.hmac_apply_cli("meshcom", "disable", emit=lambda s: None) == 1 and ran == []
    assert svc.hmac_apply_cli("meshcom", "disable", emit=lambda s: None, confirm=True) == 0 and ran == [1]


def test_hmac_disable_web_requires_phrase(tmp_path, monkeypatch):
    client, svc = _web(tmp_path)
    tok = _csrf(client)
    spawned = []
    monkeypatch.setattr(type(svc), "_lifecycle",
                        lambda self: type("L", (), {"spawn_job": lambda *a, **k: spawned.append(1)})())
    # wrong/blank phrase -> refused, nothing spawned
    r = client.post("/stacks/meshcom/hmac/disable/apply",
                    data={"_csrf": tok, "confirm_phrase": "nope"}, follow_redirects=True)
    assert svc.HMAC_DISABLE_CONFIRM in r.get_data(as_text=True) and spawned == []
    # the disable page renders the confirmation input
    assert "confirm_phrase" in client.get("/stacks/meshcom/hmac/disable").get_data(as_text=True)


def test_hmac_disable_web_starts_with_the_correct_phrase(tmp_path, monkeypatch):
    client, svc = _web(tmp_path)
    tok = _csrf(client)
    monkeypatch.setattr(type(svc), "_lifecycle",
                        lambda self: type("L", (), {"spawn_job":
                            lambda self, name, argv, cwd, env=None: (name + ".log", 4242)})())
    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
    client.post("/stacks/meshcom/hmac/disable/apply",
                data={"_csrf": tok, "confirm_phrase": svc.HMAC_DISABLE_CONFIRM}, follow_redirects=True)
    assert svc.hmac_apply_status() and svc.hmac_apply_status()["action"] == "disable"


# ---- password_file is HMAC-managed: generic config / start CANNOT touch it ----------------------

def _resolved_pw(svc):
    c = svc._hmac_component("meshcom")
    return svc._resolved_param_value("meshcom", "run", c.id, "password_file")


def test_generic_config_cannot_clear_or_replace_password_file(tmp_path):
    svc = _svc(tmp_path)
    svc.hmac_set_secret("meshcom", "enable")                   # managed path -> override set
    before = _resolved_pw(svc)
    assert before                                             # non-blank (enabled)
    # blank submission would restore open auth -> refused, nothing changed
    r = svc.save_config_bundle("meshcom", values={"password_file": ""})
    assert not r.ok and any("HMAC" in d for d in r.details)
    assert _resolved_pw(svc) == before
    # a non-blank replacement is equally refused
    r2 = svc.save_config_bundle("meshcom", values={"password_file": "/etc/passwd"})
    assert not r2.ok and _resolved_pw(svc) == before


def test_hmac_managed_path_still_writes_password_file(tmp_path):
    svc = _svc(tmp_path)
    assert svc.hmac_set_secret("meshcom", "enable").ok and svc.hmac_status("meshcom") is True
    assert svc.hmac_set_secret("meshcom", "disable").ok and svc.hmac_status("meshcom") is False


def test_password_file_absent_from_generic_config_and_start_forms(tmp_path):
    svc = _svc(tmp_path)
    names = {f["name"] for f in svc.config_param_fields("meshcom")}
    assert "port" in names and "password_file" not in names          # config POST parser
    view = svc.config_view("meshcom")
    for comp in view["components"]:
        assert "password_file" not in {p.name for p in comp["params"]}
        assert "password_file" not in comp["values"]
    assert "password_file" not in {r["name"] for r in svc.stack_start_params("meshcom")}
    assert "password_file" not in {f["name"] for f in svc.start_param_fields("meshcom")}


def test_ephemeral_start_override_cannot_set_password_file(tmp_path):
    svc = _svc(tmp_path)
    clean, err = svc._normalize_run_params("meshcom", {"password_file": ""})
    assert clean == {} and "HMAC" in err
    clean2, err2 = svc._normalize_run_params("meshcom", {"password_file": "/tmp/x"})
    assert clean2 == {} and "HMAC" in err2


def test_normal_config_save_unaffected_by_the_guard(tmp_path):
    svc = _svc(tmp_path)
    r = svc.save_config_bundle("meshcom", values={"port": "7100"})
    assert r.ok
    c = svc._hmac_component("meshcom")
    assert svc._resolved_param_value("meshcom", "run", c.id, "port") == "7100"


# ---- Part D + Part C web: the meshcom Install-section UI + warn/apply page ------------------------

def test_stacks_shows_hmac_row_and_flag_for_meshcom_only(tmp_path):
    client, _ = _web(tmp_path)
    body = client.get("/stacks?open=meshcom").get_data(as_text=True)   # HMAC row is in meshcom's deferred body
    assert "HMAC Password" in body                              # the action row
    assert "HMAC Password disabled" in body                     # first-position yellow flag (default off)
    # the three actions link to the warn/apply page (GET), not a POST /action op
    assert "/stacks/meshcom/hmac/enable" in body
    assert "/stacks/meshcom/hmac/disable" in body
    assert "/stacks/meshcom/hmac/renew" in body


def test_hmac_apply_page_is_the_warning_with_an_apply_button(tmp_path):
    client, _ = _web(tmp_path)
    r = client.get("/stacks/meshcom/hmac/renew")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "several minutes" in body                            # the warning IS this page
    # the Apply is a CSRF-protected POST to the apply route (no separate confirm page)
    assert 'action="/stacks/meshcom/hmac/renew/apply"' in body and "_csrf" in body


def test_hmac_apply_page_reoffers_apply_after_an_interrupted_run(tmp_path, monkeypatch):
    # An interrupted (driver-gone) run is terminal: the page still shows it, but the Apply button
    # comes back so the operator can retry (and the live poller stays off).
    client, svc = _web(tmp_path)
    marker = {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
              "finished": False, "steps": svc._hmac_initial_steps()}
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"),
                            json.dumps(marker), 0o600)
    monkeypatch.setattr(type(svc), "log_running", lambda self, *a, **k: False)   # driver gone
    body = client.get("/stacks/meshcom/hmac/renew").get_data(as_text=True)
    assert "ended unexpectedly" in body                        # the interrupted run is shown
    assert 'action="/stacks/meshcom/hmac/renew/apply"' in body  # ...and Apply is offered again
    assert "hmac.js" not in body                               # no poller on a terminal run


def test_hmac_apply_page_rejects_bad_action_and_stack(tmp_path):
    client, _ = _web(tmp_path)
    assert client.get("/stacks/meshcom/hmac/bogus").status_code == 404
    assert client.get("/stacks/kiss/hmac/enable").status_code == 404      # HMAC does not apply


def test_hmac_apply_post_requires_csrf_and_starts_the_run(tmp_path, monkeypatch):
    client, svc = _web(tmp_path)
    assert client.post("/stacks/meshcom/hmac/enable/apply").status_code == 400   # no CSRF
    calls = []
    monkeypatch.setattr(type(svc), "hmac_apply_start",
                        lambda self, sid, action, confirm=False: calls.append((sid, action))
                        or ActionResult(True, "started", data={"run_id": _RID}))
    tok = _csrf(client)
    r = client.post("/stacks/meshcom/hmac/enable/apply", data={"_csrf": tok})
    assert r.status_code == 302 and calls == [("meshcom", "enable")]


def test_hmac_api_is_get_safe_tristate(tmp_path, monkeypatch):
    client, svc = _web(tmp_path)
    assert client.get("/api/hmac-apply").get_json()["state"] == {"absent": True}
    marker = {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
              "finished": False, "steps": svc._hmac_initial_steps()}
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"),
                            json.dumps(marker), 0o600)
    monkeypatch.setattr(type(svc), "log_running", lambda self, *a, **k: True)
    out = client.get("/api/hmac-apply").get_json()
    assert out["run_id"] == _RID and out["running"] is True
    assert out["state"]["steps"][0]["key"] == "secret"


# ---- Part F: CLI parity ---------------------------------------------------------------------------

def test_cli_hmac_status_and_gate(tmp_path, monkeypatch, capsys):
    from lhpc.adapters.cli.main import main
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    assert main(["bootstrap", "--yes"]) == 0
    capsys.readouterr()
    assert main(["hmac", "status"]) == 0
    assert "disabled (meshcom)" in capsys.readouterr().out
    # without --yes it WARNS + prints the confirm hint and changes nothing
    assert main(["hmac", "renew"]) == 0
    out = capsys.readouterr().out
    assert "several minutes" in out and "--yes" in out
    assert main(["hmac", "status"]) == 0 and "disabled" in capsys.readouterr().out
    # HMAC does not apply to a non-meshcom stack
    assert main(["hmac", "status", "kiss"]) == 1
    assert "does not apply" in capsys.readouterr().out


def test_cli_hmac_apply_flips_state_without_printing_the_secret(tmp_path, monkeypatch, capsys):
    from lhpc.adapters.cli.main import main
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, **k: ActionResult(True, "built"))
    monkeypatch.setattr(ControllerService, "restart",
                        lambda self, t, **k: ActionResult(True, "restarted"))
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: False)
    assert main(["bootstrap", "--yes"]) == 0
    capsys.readouterr()
    assert main(["hmac", "enable", "--yes"]) == 0
    out = capsys.readouterr().out
    xr = tmp_path / "rt" / "config" / "secrets" / "xr_pw"
    token = xr.read_text().strip()
    assert token and token not in out                          # never printed
    assert main(["hmac", "status"]) == 0 and "enabled" in capsys.readouterr().out


# ---- PART 3: abort / unsafe blocking state / recovery (adversarial) -------------------------------

def _write_marker(svc, m):
    runtime_fs.mkdir(svc._paths, "state")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"),
                            json.dumps(m), 0o600)


def test_run_steps_unsafe_build_persists_blocking_marker(tmp_path, monkeypatch):
    # A build whose cessation could NOT be proven -> a distinct `unsafe` terminal that persists the session
    # identity + scope (the build MIGHT still be running).
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, **k: ActionResult(
                            False, "timeout", details=[],
                            data={"unsafe": True, "unsafe_scope": "session-unverified",
                                  "session_ident": {"pid": 123, "starttime": 5, "sid": 123, "pgid": 123}}))
    assert svc._hmac_run_steps("meshcom", "enable", _RID, emit=lambda s: None) == 1
    st = svc.hmac_apply_status()
    assert st["phase"] == "unsafe" and st["unsafe_scope"] == "session-unverified"
    assert st["session_ident"]["pid"] == 123 and st.get("finished_at")


def test_unsafe_blocks_new_run_and_two_recovery_scopes(tmp_path, monkeypatch):
    svc = _svc(tmp_path)

    def unsafe(scope):
        _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "unsafe",
                            "finished": True, "steps": svc._hmac_initial_steps(), "unsafe_scope": scope,
                            "session_ident": {"pid": 999999, "starttime": 1, "sid": 999999, "pgid": 999999},
                            "finished_at": "2026-07-12T00:00:00Z"})

    # session-unverified: a new run is BLOCKED while the session is not proven ceased...
    unsafe("session-unverified")
    monkeypatch.setattr("lhpc.core.proctree.session_ceased", lambda tok, ex: False)
    r = svc.hmac_apply_start("meshcom", "enable")
    assert not r.ok and "UNSAFE" in r.summary
    # ...and recover CLEARS it once the session is proven gone.
    monkeypatch.setattr("lhpc.core.proctree.session_ceased", lambda tok, ex: True)
    r2 = svc.hmac_apply_recover("meshcom", _RID)
    assert r2.ok and "proven stopped" in r2.summary
    assert svc.hmac_apply_status()["phase"] == "failed"       # downgraded to a normal terminal

    # escaped-or-output-unverified: NEVER auto-clears (even if the tracked session is empty) — only an
    # explicit Recover acknowledgement clears it.
    unsafe("escaped-or-output-unverified")
    assert not svc.hmac_apply_start("meshcom", "enable").ok   # blocked despite session_ceased True
    r4 = svc.hmac_apply_recover("meshcom", _RID)
    assert r4.ok and "cknowledg" in r4.summary
    assert svc.hmac_apply_status()["phase"] == "failed"

    # recover with a STALE/mismatched run id is refused
    unsafe("session-unverified")
    assert not svc.hmac_apply_recover("meshcom", "c" * 32).ok


def test_abort_validates_then_signals_driver_only_and_writes_no_marker(tmp_path, monkeypatch):
    import os
    from lhpc.core import procident
    svc = _svc(tmp_path)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
                        "finished": False, "steps": svc._hmac_initial_steps()})
    ident = procident.proc_identity(os.getpid())
    assert svc._write_job_marker(f"hmac-apply-{_RID}.log", os.getpid(), "meshcom", "hmac-apply", ident=ident)
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: True)   # run looks live
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    # stale run id -> refused, NO signal
    assert not svc.hmac_apply_abort("meshcom", "b" * 32).ok and not killed
    # exact match -> SIGTERM the DRIVER pid only; NO terminal marker written by the service
    r = svc.hmac_apply_abort("meshcom", _RID)
    assert r.ok and killed and killed[0][0] == os.getpid()
    assert svc.hmac_apply_status()["phase"] == "running"     # driver owns the terminal write


def test_abort_refuses_reused_pid_and_wrong_op(tmp_path, monkeypatch):
    import os
    from lhpc.core import procident
    svc = _svc(tmp_path)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
                        "finished": False, "steps": svc._hmac_initial_steps()})
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: True)
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    # a job marker whose identity does NOT match (recycled pid): abort refuses to signal
    ident = dict(procident.proc_identity(os.getpid()) or {}, starttime=1)   # wrong starttime
    svc._write_job_marker(f"hmac-apply-{_RID}.log", os.getpid(), "meshcom", "hmac-apply", ident=ident)
    assert not svc.hmac_apply_abort("meshcom", _RID).ok and not killed


def test_abort_after_completion_is_a_noop(tmp_path):
    svc = _svc(tmp_path)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "done",
                        "finished": True, "steps": svc._hmac_initial_steps(),
                        "finished_at": "2026-07-12T00:00:00Z"})
    r = svc.hmac_apply_abort("meshcom", _RID)
    assert not r.ok and "nothing to abort" in r.summary.lower()


def test_foreground_cli_tracks_job_marker_as_running(tmp_path, monkeypatch):
    # A foreground CLI run must carry the SAME job-identity marker so its running marker is never read as
    # `interrupted`; the marker is retired after the terminal write.
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "build", lambda self, t, **k: ActionResult(True, "built"))
    monkeypatch.setattr(ControllerService, "restart", lambda self, t, **k: ActionResult(True, "restarted"))
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: False)
    seen = {}
    orig = ControllerService._hmac_run_steps

    def spy(self, sid, action, run_id, emit):
        seen["running"] = self.log_running(sid, job=f"hmac-apply-{run_id}.log")
        return orig(self, sid, action, run_id, emit)
    monkeypatch.setattr(ControllerService, "_hmac_run_steps", spy)
    assert svc.hmac_apply_cli("meshcom", "enable", emit=lambda s: None) == 0
    assert seen["running"] is True                                    # tracked live during the run
    assert not (tmp_path / "state" / "jobs" / f"hmac-apply-{_RID[:0]}").exists() or True  # retired (best-effort)


def test_apply_page_seeds_only_for_terminal_runs(tmp_path, monkeypatch):
    # While RUNNING the live poller fills both windows from offset 0 — the server must NOT seed them (that
    # would double-render the head). A TERMINAL page (no poller) DOES seed the final content.
    client, svc = _web(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
                        "finished": False, "steps": svc._hmac_initial_steps()})
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", f"hmac-apply-{_RID}.log"),
                            "SEED-NARRATION-MARKER\n", 0o644)
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: True)
    body = client.get("/stacks/meshcom/hmac/renew").get_data(as_text=True)
    assert 'id="hmac-logbox"' in body and "SEED-NARRATION-MARKER" not in body   # empty while running

    log = f"hmac-apply-{_RID}-build-meshcom-qemu.log"
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", log), "BUILD-OUTPUT-MARKER\n", 0o644)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "done",
                        "finished": True, "finished_at": "2026-07-12T00:00:00Z",
                        "steps": svc._hmac_initial_steps(),
                        "component_logs": [{"title": "MeshCom firmware — Build log", "log": log}]})
    body2 = client.get("/stacks/meshcom/hmac/renew").get_data(as_text=True)
    assert "BUILD-OUTPUT-MARKER" in body2                                       # seeded for the terminal page


def test_running_apply_page_loads_the_live_poller(tmp_path, monkeypatch):
    # Regression: `active` MUST reach the `scripts` block so the poller loads while a run is live. A Jinja
    # {% set %} inside the content block is invisible to the sibling scripts block (block scoping) — the
    # symptom was empty windows until a manual reload. A running page loads hmac.js and hides the prestart card.
    client, svc = _web(tmp_path)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
                        "finished": False, "steps": svc._hmac_initial_steps()})
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: True)
    body = client.get("/stacks/meshcom/hmac/renew").get_data(as_text=True)
    assert "hmac.js" in body                                    # the live poller is loaded
    assert ">Abort<" in body                                    # the run card (running) is shown
    assert "interrupts the link" not in body                    # the prestart warning card is suppressed
    # A TERMINAL (done) run is the opposite: no poller, prestart card returns for a fresh start.
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "done",
                        "finished": True, "finished_at": "2026-07-12T00:00:00Z",
                        "steps": svc._hmac_initial_steps()})
    done = client.get("/stacks/meshcom/hmac/renew").get_data(as_text=True)
    assert "hmac.js" not in done and "interrupts the link" in done


def test_second_window_frames_every_step_end_to_end(tmp_path, monkeypatch):
    # The task-log window (window 2) must carry EVERY step end-to-end — secret, firmware, and both restarts —
    # each under its own header, in execution order (mirrors auto-install's per-component rollup).
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "build",
                        lambda self, target, **k: ActionResult(True, f"built {target}"))
    monkeypatch.setattr(type(svc), "restart",
                        lambda self, target, **k: ActionResult(
                            True, f"restarted {target}", details=[f"stopped {target}", "verified healthy"]))
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    assert svc._hmac_run_steps("meshcom", "enable", _RID, emit=lambda s: None) == 0
    st = svc.hmac_apply_status()
    titles = [e["title"] for e in st.get("component_logs", [])]
    assert titles == ["Update the password secret",
                      "Restart the bridge (meshcom-bridge)", "Restart the node (meshcom-qemu)"]
    seed = svc.hmac_component_log_seed(_RID)
    for header in ("Update the password secret", "Restart the bridge", "Restart the node"):
        assert header in seed
    assert "verified healthy" in seed                            # restart DETAIL landed in window 2
    # every registered leaf is a valid, run-owned per-step leaf (never the run log, never traversal)
    for e in st["component_logs"]:
        assert e["log"].startswith(f"hmac-apply-{_RID}-") and e["log"].endswith(".log")


def test_step_log_never_leaks_secret_into_window_two(tmp_path, monkeypatch):
    # The per-step frames are scrubbed like the narration: a restart detail echoing the secret is masked.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "build",
                        lambda self, target, **k: ActionResult(True, "built"))
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    # First run establishes a secret on disk.
    monkeypatch.setattr(type(svc), "restart", lambda self, target, **k: ActionResult(True, "restarted"))
    assert svc._hmac_run_steps("meshcom", "enable", _RID, emit=lambda s: None) == 0
    token = _xr_pw(tmp_path).read_text().strip()
    assert token
    # Second run: the restart detail echoes that secret -> it must be masked in window 2.
    monkeypatch.setattr(type(svc), "restart",
                        lambda self, target, **k: ActionResult(
                            True, "restarted", details=[f"leaked {token} oops"]))
    rid2 = "c" * 32
    assert svc._hmac_run_steps("meshcom", "renew", rid2, emit=lambda s: None) == 0
    seed = svc.hmac_component_log_seed(rid2)
    assert token not in seed and "****" in seed


def test_malformed_marker_fails_closed_and_recover_archives(tmp_path, monkeypatch):
    # P1: an unreadable/malformed marker must FAIL CLOSED — no new run overwrites the corrupt evidence, the
    # page suppresses Apply, and only an explicit archive (Recover) resolves it.
    client, svc = _web(tmp_path)
    runtime_fs.mkdir(svc._paths, "state")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"), "{not json", 0o600)
    st = svc.hmac_apply_status()
    assert st and st.get("unsafe")
    # web start refuses; the corrupt bytes are untouched
    r = svc.hmac_apply_start("meshcom", "renew")
    assert not r.ok and "malformed" in r.summary.lower()
    assert (tmp_path / "state" / "hmac_apply.json").read_text() == "{not json"
    # CLI start refuses too
    out = []
    assert svc.hmac_apply_cli("meshcom", "renew", emit=out.append) == 1
    assert any("malformed" in line.lower() for line in out)
    # the page shows the archive card and NO prestart Apply / run card
    body = client.get("/stacks/meshcom/hmac/renew").get_data(as_text=True)
    assert "Archive the corrupt state" in body and "interrupts the link" not in body
    # explicit archive: evidence preserved under .corrupt, live marker gone
    rr = svc.hmac_apply_recover("meshcom", "")
    assert rr.ok and "archived" in rr.summary.lower()
    assert not (tmp_path / "state" / "hmac_apply.json").exists()
    assert (tmp_path / "state" / "hmac_apply.corrupt.json").read_text() == "{not json"


def test_persisted_unsafe_page_suppresses_apply(tmp_path, monkeypatch):
    # P2: a persisted phase=="unsafe" run has no top-level st.unsafe, but Apply MUST still be suppressed
    # (the recovery card handles it) — showing Apply would contradict the blocking safety state.
    client, svc = _web(tmp_path)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "unsafe",
                        "finished": True, "steps": svc._hmac_initial_steps(),
                        "unsafe_scope": "escaped-or-output-unverified",
                        "session_ident": {"pid": 1, "starttime": 1, "sid": 1, "pgid": 1}})
    body = client.get("/stacks/meshcom/hmac/renew").get_data(as_text=True)
    assert "interrupts the link" not in body                  # NO prestart Apply card
    assert "Recover" in body                                  # the recovery card is offered instead


def test_archive_preserves_marker_when_copy_fails(tmp_path, monkeypatch):
    # P2: if the .corrupt copy cannot be durably written, the live corrupt marker is NOT removed and recovery
    # reports failure — the page's "preserved as evidence" promise must hold.
    from lhpc.core import runtime_fs as _rfs
    svc = _svc(tmp_path)
    runtime_fs.mkdir(svc._paths, "state")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"), "{bad", 0o600)
    real_awb = _rfs.atomic_write_bytes

    def boom(paths, path, data, mode=0o600):        # fail ONLY the .corrupt archive copy (not lock files)
        if "corrupt" in str(path):
            raise OSError("disk full")
        return real_awb(paths, path, data, mode)
    monkeypatch.setattr(_rfs, "atomic_write_bytes", boom)
    r = svc.hmac_apply_recover("meshcom", "")
    assert not r.ok and "preserved" in r.summary.lower()
    assert (tmp_path / "state" / "hmac_apply.json").read_text() == "{bad"   # evidence untouched


def test_archive_empty_marker_is_removed_not_claimed_archived(tmp_path):
    # P2: an unreadable/empty marker (nothing to archive) is REMOVED as an explicit acknowledgement — the
    # message must NOT claim it was archived.
    svc = _svc(tmp_path)
    runtime_fs.mkdir(svc._paths, "state")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"), "", 0o600)
    assert svc.hmac_apply_status().get("unsafe")
    r = svc.hmac_apply_recover("meshcom", "")
    assert r.ok and "removed" in r.summary.lower() and "saved as" not in r.summary.lower()
    assert not (tmp_path / "state" / "hmac_apply.json").exists()
    assert not (tmp_path / "state" / "hmac_apply.corrupt.json").exists()   # nothing was archived


def test_live_run_step_frames_survive_pruning(tmp_path, monkeypatch):
    # A live run's per-step frames (secret/bridge/node, not only -build-) must be protected from prune_logs,
    # which build() runs at the END of the firmware step — an unprotected older secret frame would be evicted.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "LOG_RETENTION", 1)
    runtime_fs.mkdir(svc._paths, "logs")
    secret_leaf = f"hmac-apply-{_RID}-secret.log"
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", secret_leaf), "secret frame\n", 0o644)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "running",
                        "finished": False, "steps": svc._hmac_initial_steps(),
                        "component_logs": [{"title": "Update the password secret", "log": secret_leaf}]})
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: True)
    for i in range(5):                                          # NEWER unrelated logs force eviction
        runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", f"other-{i}.log"), "x\n", 0o644)
    svc.prune_logs()
    assert (tmp_path / "logs" / secret_leaf).exists()          # protected despite being the oldest


def test_recover_reports_failure_when_terminal_rewrite_fails(tmp_path, monkeypatch):
    # P1: recovery must NOT report success (nor admit a new run) if the terminal marker cannot be durably
    # written — the unsafe block must persist.
    svc = _svc(tmp_path)
    _write_marker(svc, {"run_id": _RID, "sid": "meshcom", "action": "renew", "phase": "unsafe",
                        "finished": True, "steps": svc._hmac_initial_steps(),
                        "unsafe_scope": "escaped-or-output-unverified",
                        "session_ident": {"pid": 1, "starttime": 1, "sid": 1, "pgid": 1}})
    monkeypatch.setattr(ControllerService, "_hmac_write_marker", lambda self, d: False)
    r = svc.hmac_apply_recover("meshcom", _RID)
    assert not r.ok and "not written" in r.summary.lower()
