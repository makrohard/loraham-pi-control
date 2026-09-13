"""The one-click updater behind the web console's Update button.

The console cannot update the process that serves it, so it writes a request marker and a
separate helper claims it — no `systemctl` anywhere on the path, which is what makes the design
escape-proof. Everything here is about that handover: exclusive claim, crash recovery from a
half-finished run, and the repair of a legacy or drop-in-shadowed unit before any of it runs.
"""
from __future__ import annotations


import gitrepo
from lhpc.core import selfupdate
from lhpc.core import updater_units
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult


# --- last_apply envelope field (one-click web update outcome) ----------------------------------

def test_record_last_apply_merges_and_survives_refresh(env):
    selfupdate.refresh_cache(env["sys"], env["paths"])
    selfupdate.record_last_apply_strict(env["paths"], ok=True, summary="Update applied.", now=7)
    v = selfupdate.status_view(env["paths"])
    assert v["last_apply"] == {"ok": True, "summary": "Update applied.", "finished_at": 7}
    # a later refresh (check/apply) CARRIES the outcome forward, like identity
    selfupdate.refresh_cache(env["sys"], env["paths"])
    assert selfupdate.status_view(env["paths"])["last_apply"]["summary"] == "Update applied."
    # and the other envelope fields survived the merge
    assert selfupdate.status_view(env["paths"])["available"] is True


def test_record_last_apply_without_prior_cache(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    selfupdate.record_last_apply_strict(p, ok=False, summary="x" * 9000, now=1)   # bounded
    v = selfupdate.status_view(p)
    assert v["last_apply"]["ok"] is False and len(v["last_apply"]["summary"]) <= 512


def test_last_apply_malformed_is_rejected_safely(tmp_path):
    import json
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    # wrong-typed last_apply invalidates the WHOLE envelope (schema discipline) -> grey view
    (tmp_path / "state" / "selfupdate.json").write_text(
        json.dumps({"schema_version": 1, "local": {"is_git": True},
                    "last_apply": {"ok": "yes"}}))
    v = selfupdate.status_view(p)
    assert v["last_apply"] is None and v["available"] is False


# --- one-click updater: marker trigger + de-systemctl'd run-service (escape-proof design) -------

_WEB_INACTIVE = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"):
                 CommandResult(1, "", "")}


def _seed_available(tmp_path, **identity):
    import json
    env = {"schema_version": 1, "local": {"is_git": True, "head": "a", "head_short": "a",
                                          "branch": "main"},
           "upstream": {"ok": True, "upstream_version": "9.9", "upstream_head": "b",
                        "upstream_head_short": "b"}, "checked_at": 1}
    if identity:
        env["identity"] = identity
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "selfupdate.json").write_text(json.dumps(env))


def test_trigger_writes_exclusive_request_no_systemctl(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    r = svc.self_update_trigger(overwrite=False)
    assert r.ok and r.data["mode"] == "normal"
    assert (tmp_path / "state" / "selfupdate.request").read_text().strip() == "normal"
    # NO systemctl was ever called by the trigger
    assert not any(c and c[0] == "systemctl" for c in fake.calls)
    # a SECOND trigger while pending is refused (exclusive admission)
    r2 = svc.self_update_trigger(overwrite=True)
    assert not r2.ok and r2.data.get("already_pending")


def test_trigger_overwrite_payload(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    assert svc.self_update_trigger(overwrite=True).data["mode"] == "overwrite"
    assert (tmp_path / "state" / "selfupdate.request").read_text().strip() == "overwrite"


def test_trigger_refuses_foreground_process(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=False)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    r = svc.self_update_trigger()
    assert not r.ok and r.data.get("not_managed")
    assert not (tmp_path / "state" / "selfupdate.request").exists()


def test_trigger_refuses_when_integration_not_ok(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    (svc._user_unit_dir() / "lhpc-web.service").write_text("[Service]\nExecStart=/usr/bin/other\n")
    r = svc.self_update_trigger()
    assert not r.ok and r.data.get("integration") == "incomplete"   # a foreign ExecStart: not canonical, still fixable


def test_trigger_refuses_unsafe_identity(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    _seed_available(tmp_path, ok=False, status="unsafe", reason="bad", checked_at=1)
    assert svc.self_update_trigger().data.get("identity_unsafe")
    assert not (tmp_path / "state" / "selfupdate.request").exists()


def _write_request(tmp_path, mode="normal"):
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "selfupdate.request").write_text(mode + "\n")


def test_run_service_claims_applies_records_no_systemctl(tmp_path, monkeypatch, op_svc):
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake = op_svc(units=True, invocation=True)
    _write_request(tmp_path, "normal")
    order = []
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: (order.append(force),
                                                      ActionResult(True, "Already up to date.",
                                                                   data={"already": True}))[1])
    res = svc.self_update_run_service()
    assert res.ok and order == [False]
    # request claimed + in-flight released; NO systemctl anywhere
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not (tmp_path / "state" / "selfupdate.inflight").exists()
    assert not any("systemctl" in " ".join(c) for c in fake.calls)
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is True


def test_run_service_overwrite_mode_from_request(tmp_path, monkeypatch, op_svc):
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake = op_svc(units=True, invocation=True)
    _write_request(tmp_path, "overwrite")
    seen = {}
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: (seen.__setitem__("f", force),
                                                      ActionResult(True, "ok", data={"already": True}))[1])
    svc.self_update_run_service()
    assert seen["f"] is True


def test_run_service_noop_without_request(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    res = svc.self_update_run_service()
    assert res.ok and res.data.get("noop")


def test_run_service_malformed_request_is_recovery(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    _write_request(tmp_path, "wat")
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("malformed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # retained for recovery
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is False


def _advancing_svc(tmp_path, monkeypatch, op_svc):
    """A managed updater whose apply REALLY advances (so the venv sync runs); returns
    (svc, fake, pip argv) — the pip row is the fixture's succeeding one until a test changes it."""
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake = op_svc(units=True, invocation=True)
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "Update applied.",
                                                                  data={"already": False}))
    pip = next(k for k in fake.commands if k[1:4] == ("-m", "pip", "install"))
    _write_request(tmp_path, "normal")
    return svc, fake, pip


def test_run_service_syncs_the_venv_after_a_real_advance(tmp_path, monkeypatch, op_svc):
    svc, fake, pip = _advancing_svc(tmp_path, monkeypatch, op_svc)
    assert svc.self_update_run_service().ok
    assert list(pip) in fake.calls
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is True


def test_run_service_venv_sync_failure_fails_the_update(tmp_path, monkeypatch, op_svc):
    # pip FAILS -> the whole update is reported FAILED and recorded red
    svc, fake, pip = _advancing_svc(tmp_path, monkeypatch, op_svc)
    fake.commands[pip] = CommandResult(returncode=1, stdout="", stderr="boom")
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("venv_sync_failed")
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is False


def test_run_service_record_failure_retains_inflight(tmp_path, monkeypatch, op_svc):
    """If the STRICT last-apply write does not persist, the in-flight marker is retained
    (one-click blocked until recovery) — never deleted on an unrecorded outcome."""
    from lhpc.core import selfupdate as _su
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake = op_svc(units=True, invocation=True)
    _write_request(tmp_path, "normal")
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "ok", data={"already": True}))
    monkeypatch.setattr(_su, "record_last_apply_strict", lambda *a, **k: False)   # durable write fails
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("record_failed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # retained -> recovery required


def test_run_service_refuses_preexisting_inflight_preserving_both(tmp_path, monkeypatch, op_svc):
    """A request appearing while an in-flight record already exists must NOT clobber the
    in-flight evidence — the helper fails closed (recovery_required) and preserves both files."""
    from lhpc.core.services import ControllerService
    applied = []
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: applied.append(1))
    svc, fake = op_svc(units=True, invocation=True)
    (tmp_path / "state" / "selfupdate.inflight").write_text('{"mode":"normal","pid":4242,"start_time":7}')
    _write_request(tmp_path, "overwrite")
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("recovery_required") and not applied
    assert (tmp_path / "state" / "selfupdate.request").read_text() == "overwrite\n"   # preserved
    assert (tmp_path / "state" / "selfupdate.inflight").read_text() == \
        '{"mode":"normal","pid":4242,"start_time":7}'                                 # NOT clobbered


def test_run_service_inflight_only_is_noop(tmp_path, monkeypatch, op_svc):
    """In-flight present but no request -> nothing to claim -> no-op, in-flight untouched."""
    svc, fake = op_svc(units=True, invocation=True)
    (tmp_path / "state" / "selfupdate.inflight").write_text('{"mode":"normal","pid":1,"start_time":1}')
    res = svc.self_update_run_service()
    assert res.ok and res.data.get("noop")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()


def test_run_service_concurrent_claim_admits_one(tmp_path, monkeypatch, op_svc):
    """Two claims against ONE request admit exactly one; the loser fails closed, evidence intact."""
    from lhpc.core.services import ActionResult, ControllerService
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "ok", data={"already": True}))
    svc, fake = op_svc(units=True, invocation=True)
    _write_request(tmp_path, "normal")
    r1 = svc.self_update_run_service()                 # claims + runs, releases inflight
    r2 = svc.self_update_run_service()                 # nothing left to claim
    winners = [r for r in (r1, r2) if r.ok and not r.data.get("noop")]
    noops = [r for r in (r1, r2) if r.data.get("noop") or r.data.get("recovery_required")]
    assert len(winners) == 1 and len(noops) == 1


def test_run_service_final_unlink_failure_retains_evidence(tmp_path, monkeypatch, op_svc):
    """A failed FINAL in-flight unlink -> incomplete/cleanup_failed, evidence retained."""
    from lhpc.core import runtime_fs
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake = op_svc(units=True, invocation=True)
    _write_request(tmp_path, "normal")
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "ok", data={"already": True}))
    # Fail ONLY the final in-flight unlink (its intent) — NOT the admission flock-owner unlink that the
    # helper's task-admission release now also performs through runtime_fs.unlink.
    _real_unlink = runtime_fs.unlink
    def _fail_inflight(paths, path, *a, **k):
        if str(path).endswith("selfupdate.inflight"):
            raise OSError("busy")
        return _real_unlink(paths, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "unlink", _fail_inflight)
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("cleanup_failed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # retained
    # but the outcome WAS recorded durably before the unlink attempt
    assert selfupdate.status_view(svc._paths)["last_apply"] is not None


def test_recover_strict_record_failure_keeps_inflight(tmp_path, monkeypatch, op_svc):
    """Recovery must NOT clear the in-flight evidence if it cannot durably record the incomplete
    outcome."""
    import json
    from lhpc.core import selfupdate as _su
    svc, fake = op_svc(units=True, invocation=True)
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": 999999, "start_time": 1}))   # dead helper
    monkeypatch.setattr(_su, "record_last_apply_strict", lambda *a, **k: False)
    res = svc.self_update_recover_request()
    assert not res.ok and res.data.get("record_failed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # NOT silently cleared


# --- request classification + recovery -------------------------------------------------------

def test_classify_and_recover_pending(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    _write_request(tmp_path, "normal")
    assert svc.classify_request() == "pending"
    r = svc.self_update_recover_request()
    assert r.ok and r.data["cleared"] == "pending"
    assert svc.classify_request() == "absent"


def test_recover_inflight_requires_dead_helper(tmp_path, monkeypatch, op_svc):
    import json
    import os
    from lhpc.core.services import _proc_start_time
    svc, fake = op_svc(units=True, invocation=True)
    # in-flight owned by a LIVE process (this test) -> recovery refuses
    me = os.getpid()
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": me, "start_time": _proc_start_time(me)}))
    assert svc.classify_request() == "in_flight"
    assert not svc.self_update_recover_request().ok               # helper still alive
    # a DEAD process identity -> recovery clears + records incomplete
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": 999999, "start_time": 1}))
    r = svc.self_update_recover_request()
    assert r.ok and r.data["cleared"] == "in_flight"
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is False


def test_classify_malformed_inflight(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    (tmp_path / "state" / "selfupdate.inflight").write_text("{ not json")
    assert svc.classify_request() == "malformed"
    assert not svc.self_update_recover_request().ok               # never auto-cleared


def test_integration_recovery_required_when_inflight(tmp_path, monkeypatch, op_svc):
    import json
    import os
    from lhpc.core.services import _proc_start_time
    svc, fake = op_svc(units=True, invocation=True)
    me = os.getpid()
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": me, "start_time": _proc_start_time(me)}))
    assert svc.updater_integration()["status"] == "recovery_required"


# --- repair refuses to run while an active drop-in shadows the unit ---------------------------

def _repair_svc(tmp_path, op_svc, systemctl_ok_rows, show_for):
    """A repair-ready temp deployment: canonical unit dir + a checkout/.git, every integration
    step answering rc 0 (a test overrides the one it wants to fail), with the `systemctl show`
    responses supplied by `show_for(kind, want) -> stdout`. Returns (svc, fake)."""
    svc, fake = op_svc(systemctl_ok_rows, units=True, invocation=True)
    (tmp_path / "src" / "loraham-pi-control" / ".git").mkdir(parents=True)   # self-hosted checkout
    ud = svc._user_unit_dir()
    for kind in updater_units.ALL_UNITS:
        argv = ("systemctl", "--user", "show", "-p", "FragmentPath", "-p", "DropInPaths", kind)
        fake.commands[argv] = CommandResult(returncode=0, stdout=show_for(kind, str(ud / kind)),
                                            stderr="")
    return svc, fake


def test_repair_success_with_no_dropins(tmp_path, op_svc, systemctl_ok_rows):
    svc, fake = _repair_svc(tmp_path, op_svc, systemctl_ok_rows,
                      lambda k, want: f"FragmentPath={want}\nDropInPaths=\n")
    res = svc.self_update_repair_integration()
    assert res.ok, res.summary
    assert (tmp_path / ".lhpc-root").exists()                     # marker written on success
    assert any("enable" in c for c in fake.calls) and any("restart" in c for c in fake.calls)
    # repair arms boot restore on migrated deployments (install.sh does it for fresh ones)
    assert ["systemctl", "--user", "enable", "lhpc-boot-restore.service"] in fake.calls


def test_repair_boot_restore_enable_failure_is_hard(tmp_path, op_svc, systemctl_ok_rows):
    # A deployment whose boot unit cannot be armed is NOT a successful repair — silently
    # reporting success would leave "restore after reboot" quietly dead on this box.
    svc, fake = _repair_svc(tmp_path, op_svc, systemctl_ok_rows,
                            lambda k, want: f"FragmentPath={want}\nDropInPaths=\n")
    fake.commands[("systemctl", "--user", "enable", updater_units.BOOT_RESTORE_UNIT)] = \
        CommandResult(returncode=1, stdout="", stderr="nope")
    res = svc.self_update_repair_integration()
    assert not res.ok and res.data.get("boot_restore_enable_failed") is True


def test_repair_refuses_active_dropin(tmp_path, op_svc, systemctl_ok_rows):
    svc, fake = _repair_svc(tmp_path, op_svc, systemctl_ok_rows,
                      lambda k, want: f"FragmentPath={want}\n"
                      + ("DropInPaths=/etc/systemd/user/lhpc-web.service.d/x.conf\n"
                         if k == "lhpc-web.service" else "DropInPaths=\n"))
    res = svc.self_update_repair_integration()
    assert not res.ok and res.data.get("dropin") == "lhpc-web.service"
    assert not (tmp_path / ".lhpc-root").exists()                 # NO marker after refusal
    assert not any("enable" in c or "restart" in c for c in fake.calls)   # no enable/restart


def test_repair_refuses_wrong_fragment(tmp_path, op_svc, systemctl_ok_rows):
    svc, fake = _repair_svc(tmp_path, op_svc, systemctl_ok_rows,
                      lambda k, want: "FragmentPath=/some/other/place.service\nDropInPaths=\n")
    res = svc.self_update_repair_integration()
    assert not res.ok and res.data.get("shadowed")
    assert not (tmp_path / ".lhpc-root").exists()
    assert not any("enable" in c or "restart" in c for c in fake.calls)


# --- console self-repair-and-update (legacy same-root migration) ------------------------------

def _legacy_svc(tmp_path, op_svc, systemctl_ok_rows, *, seed_show=True):
    """A managed console (INVOCATION_ID) with LEGACY same-root units (modified_ours web+helper,
    no .path) -> fixable `incomplete`. Seeds the systemctl responses repair needs. Returns
    (svc, fake)."""
    U = updater_units
    svc, fake = op_svc(units=True, invocation=True)        # canonical units + INVOCATION_ID
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    (tmp_path / "src" / "loraham-pi-control" / ".git").mkdir(parents=True)   # self-hosted checkout
    ud = svc._user_unit_dir(); root = str(tmp_path)
    (ud / "lhpc-web.service").write_text(
        f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={root}\n"
        f"ExecStart={root}/venv/lhpc/bin/lhpc web --host 127.0.0.1 --port 8770\n")
    (ud / "lhpc-selfupdate.service").write_text(
        f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={root}\n"
        f"ExecStart={root}/venv/lhpc/bin/lhpc self-update --run-service\n")
    (ud / "lhpc-selfupdate.path").unlink()                   # -> missing
    if seed_show:
        fake.commands[("systemctl", "--user", "show", "-p", "Version")] = \
            CommandResult(returncode=0, stdout="Version=257\n", stderr="")
        for kind in U.ALL_UNITS:
            fake.commands[("systemctl", "--user", "show", "-p", "FragmentPath",
                                "-p", "DropInPaths", kind)] = \
                CommandResult(returncode=0, stdout=f"FragmentPath={ud/kind}\nDropInPaths=\n", stderr="")
        # Every integration step the repair checks succeeds by default; a test overrides one to fail.
        fake.commands.update(systemctl_ok_rows)
    return svc, fake


def test_repair_integration_restart_false_installs_starts_path_no_restart(tmp_path, op_svc, systemctl_ok_rows):
    U = updater_units
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows)
    res = svc.self_update_repair_integration(restart=False)
    assert res.ok, res.summary
    ud = svc._user_unit_dir()
    for k in U.ALL_UNITS:                                    # all three now canonical
        assert (ud / k).read_text() == U.render(k, *U.deployment_paths(str(tmp_path)))
    calls = fake.calls
    assert ["systemctl", "--user", "enable", "--now", "lhpc-selfupdate.path"] in calls
    assert not any(c[:3] == ["systemctl", "--user", "restart"] for c in calls)   # restart=False


def test_repair_and_trigger_migrates_then_writes_marker(tmp_path, op_svc, systemctl_ok_rows):
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows)
    assert svc.updater_integration()["fixable"] and svc.updater_integration()["status"] == "incomplete"
    res = svc.self_update_repair_and_trigger(overwrite=False)
    assert res.ok and res.data.get("triggered")
    assert (tmp_path / "state" / "selfupdate.request").read_text().strip() == "normal"
    assert svc.updater_integration()["status"] == "ok"       # units converged to canonical


def test_repair_and_trigger_ok_delegates_without_repair(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)        # already-canonical units
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    res = svc.self_update_repair_and_trigger()
    assert res.ok and res.data.get("triggered")
    assert not any("daemon-reload" in " ".join(c) for c in fake.calls)   # no repair happened


def test_repair_and_trigger_bus_unavailable_writes_nothing(tmp_path, op_svc, systemctl_ok_rows):
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows, seed_show=False)
    fake.commands[("systemctl", "--user", "show", "-p", "Version")] = \
        CommandResult(returncode=1, stdout="", stderr="Failed to connect to bus")
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("bus_unavailable")
    assert not (tmp_path / "state" / "selfupdate.request").exists()           # nothing written
    assert not any(c[:3] == ["systemctl", "--user", "enable"] for c in fake.calls)


def test_repair_and_trigger_refuses_unfixable(tmp_path, monkeypatch, op_svc):
    svc, fake = op_svc(units=True, invocation=True)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    # one ambiguous unit -> not fixable
    (svc._user_unit_dir() / "lhpc-web.service").write_text("[Service]\nExecStart=/usr/bin/other\n")
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("unfixable")
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not any("daemon-reload" in " ".join(c) for c in fake.calls)   # no writes/repair


# --- the migration path: the managed gate, an active watcher, and overwrite identity ----------

def test_repair_and_trigger_refuses_foreground_no_writes(tmp_path, monkeypatch, op_svc, systemctl_ok_rows):
    """A FOREGROUND console (no INVOCATION_ID) must refuse BEFORE any unit write/enable/
    marker — even with fixable units and a reachable bus."""
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows)
    monkeypatch.delenv("INVOCATION_ID", raising=False)      # foreground console
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("not_managed")
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not (tmp_path / ".lhpc-root").exists()
    assert not any("daemon-reload" in " ".join(c) or c[:4] == ["systemctl", "--user", "enable", "--now"]
                   for c in fake.calls)


def test_repair_restart_false_fails_when_watcher_enable_nonzero(tmp_path, op_svc, systemctl_ok_rows):
    """A nonzero `enable --now .path` fails the migration repair before the root marker."""
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows)
    fake.commands[("systemctl", "--user", "enable", "--now", "lhpc-selfupdate.path")] = \
        CommandResult(returncode=1, stdout="", stderr="boom")
    res = svc.self_update_repair_integration(restart=False)
    assert not res.ok and res.data.get("path_watcher_failed")
    assert not (tmp_path / ".lhpc-root").exists()           # marker not written


def test_repair_restart_false_fails_when_watcher_not_active(tmp_path, op_svc, systemctl_ok_rows):
    """A watcher that is not active after `enable` fails the repair, and queues no request."""
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows)
    fake.commands[("systemctl", "--user", "is-active", "--quiet", "lhpc-selfupdate.path")] = \
        CommandResult(returncode=3, stdout="inactive", stderr="")
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("path_watcher_failed")
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not (tmp_path / ".lhpc-root").exists()


def test_repair_success_proves_path_active_before_marker(tmp_path, op_svc, systemctl_ok_rows):
    """The happy migration proves the watcher active AND queues the request."""
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows)
    res = svc.self_update_repair_and_trigger()
    assert res.ok and res.data.get("triggered")
    assert ["systemctl", "--user", "is-active", "--quiet", "lhpc-selfupdate.path"] in fake.calls
    assert (tmp_path / "state" / "selfupdate.request").exists()


def test_repair_restart_true_also_verifies_watcher_active(tmp_path, op_svc, systemctl_ok_rows):
    """The watcher-active check applies in BOTH modes. restart=True must NOT silently
    leave the request watcher down — an inactive .path fails the repair and no root marker is written
    (a partial integration is never reported as success)."""
    svc, fake = _legacy_svc(tmp_path, op_svc, systemctl_ok_rows)
    fake.commands[("systemctl", "--user", "is-active", "--quiet", "lhpc-selfupdate.path")] = \
        CommandResult(returncode=3, stdout="inactive", stderr="")
    res = svc.self_update_repair_integration(restart=True)
    assert not res.ok and res.data.get("path_watcher_failed")
    assert not (tmp_path / ".lhpc-root").exists()


def test_check_upstream_follows_a_rewritten_upstream(env):
    """`git fetch <remote> <branch>` updates the remote-tracking ref only
    opportunistically, and NOT when upstream rewrote history. After a force-push we kept
    reading the OLD commit, so lhpc reported "update available" for a commit that no
    longer exists — and computed `ff_blocked` against that phantom."""
    up = env["up"]
    first = gitrepo.upstream_commit(up, version="0.9.9")
    assert selfupdate.check_upstream(env["sys"])["upstream_head"] == first

    # Upstream rewrites history (force-push), exactly like a re-cut release branch.
    gitrepo.git(up, "reset", "--hard", "HEAD~1")
    (up / "note.txt").write_text("rewritten\n")
    gitrepo.git(up, "add", "-A")
    gitrepo.git(up, "commit", "-m", "rewritten")
    gitrepo.git(up, "push", "--force", "origin", "main")
    rewritten = gitrepo.git(up, "rev-parse", "HEAD")
    assert rewritten != first

    out = selfupdate.check_upstream(env["sys"])
    assert out["ok"]
    assert out["upstream_head"] == rewritten, "must follow the rewritten upstream"
    assert out["upstream_head"] != first, "must not report the vanished commit"


def test_apply_leaves_the_managed_units_canonical(op_svc, monkeypatch, tmp_path):
    """An update whose new version changes a unit TEMPLATE left the installed unit
    non-canonical, and boot restore then refused — the box came back from a power cycle with
    nothing running. Boot restore cannot repair it (ProtectHome=read-only), so the updater must.

    The units really are written into the temp deployment and really are re-verified by the
    out-of-process check, so this passes only when the whole refresh does its job.
    """
    from lhpc.core.service_base import ActionResult
    from lhpc.core.services import ControllerService

    # The verification is a real sub-process and finds the units through HOME, which is where
    # `units=True` writes them (the per-test HOME). A foreground shell with the console stopped.
    svc, _fake = op_svc(_WEB_INACTIVE, units=True, invocation=False)

    calls = []
    monkeypatch.setattr(ControllerService, "updater_integration", lambda self: {"fixable": True})
    monkeypatch.setattr(ControllerService, "self_update_repair_integration",
                        lambda self, *, restart=True: (calls.append(restart),
                                                       ActionResult(True, "repaired"))[1])
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "applied", data={}))
    res = svc.self_update_apply_operator()
    assert res.ok, res.summary
    assert res.data.get("units_refreshed") is True, res.data.get("units_refresh_detail")
    assert calls == [False], "must not restart the console from inside the update"


def test_the_unit_refresh_runs_out_of_process(op_svc, monkeypatch):
    """The refresh must happen in a NEW interpreter: the running one still holds the PRE-update
    `updater_units`, so an in-process render would verify the OLD units and approve them. Proven
    by what the refresh actually executes, not by what its source says."""
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService

    svc, _fake = op_svc()
    seen = []

    def spy(argv, *a, **k):
        seen.append([str(x) for x in argv])
        return CR(0, "", "")

    # Production bypasses the injected System here on purpose — see the docstring of
    # `_refresh_units_post_update`: the verifier MUST be a fresh interpreter, so it is a real
    # `subprocess.run`. The root conftest's shell guard is not needed: `spy` executes nothing.
    monkeypatch.setattr("subprocess.run", lambda argv, *a, **k: spy(argv))
    monkeypatch.setattr(ControllerService, "updater_integration", lambda self: {"fixable": True})
    svc._refresh_units_post_update()          # the private IS the seam under test: what it executes
    assert seen, "the refresh executed nothing — it cannot have run out of process"
    argv = seen[-1]
    assert argv[0] != "", argv
    assert any("verify-set" in a for a in argv), (
        f"the refresh must ask a FRESH interpreter to verify the unit set, got {argv}")


def test_a_failed_unit_refresh_makes_the_update_visibly_partial(op_svc, monkeypatch):
    """A failed refresh was stored only in ActionResult.data while ok stayed True, and the CLI
    renderer never shows that data — the operator saw a clean success."""
    from lhpc.core.service_base import ActionResult
    from lhpc.core.services import ControllerService

    svc, _fake = op_svc(_WEB_INACTIVE, invocation=False)
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "applied", data={}))
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (False, "units still not canonical"))
    res = svc.self_update_apply_operator()
    assert res.ok is False
    assert res.data.get("reason") == "units-refresh-failed"
    assert res.data.get("units_refreshed") is False
    assert "repair-integration" in res.summary              # the command the operator is told to run


