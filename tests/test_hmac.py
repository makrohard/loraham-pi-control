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


def _install_all_harness(monkeypatch, build_ok=True):
    # Neutralise clone/build/test/frozen-ref so a full install_all run reaches every row's build without
    # network or a toolchain (mirrors tests/test_install_all.py's stubs).
    from lhpc.core.install import Installer, PlanAction
    monkeypatch.setattr(Installer, "adopt_source",
                        lambda self, comp, force=False, source="pinned", pinned_expected=None,
                        locked=False: PlanAction("adopt", "", f"adopt {comp.id}",
                                                 status="skipped", detail="stub"))
    monkeypatch.setattr(ControllerService, "missing_system_deps", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, bulk_ctx=None, **k:
                        ActionResult(build_ok, "built" if build_ok else "boom"))
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, bulk_ctx=None, **k:
                        ActionResult(True, "tested"))
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: (("f" * 40, "frozen: stub"), ""))


def test_install_all_actually_enables_hmac(tmp_path, monkeypatch):
    # The default-on enable happens BEFORE the bulk boundary (the boundary holds the config lock, so the
    # old in-boundary call always silently failed). After a full run, meshcom succeeds AND HMAC is on.
    svc = _svc(tmp_path)
    _install_all_harness(monkeypatch)
    assert svc.hmac_status("meshcom") is False
    r = svc.install_all(apply=True, tests=False, emit=lambda s: None)
    rows = {x["id"]: x for x in svc.bulk_status()["stacks"]}
    assert rows["meshcom"]["status"] == "success" and r.ok
    assert svc.hmac_status("meshcom") is True and _xr_pw(tmp_path).exists()


def test_install_all_fails_meshcom_row_when_hmac_enable_fails(tmp_path, monkeypatch):
    # FAIL CLOSED in install-all: a failed enable marks the meshcom row fail and SKIPS its build — the
    # firmware is never baked with an empty password while the run reports success.
    svc = _svc(tmp_path)
    _install_all_harness(monkeypatch)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "boom"))   # forces enable to fail
    r = svc.install_all(apply=True, tests=False, emit=lambda s: None)
    rows = {x["id"]: x for x in svc.bulk_status()["stacks"]}
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


# ---- Part D + Part C web: the meshcom Install-section UI + warn/apply page ------------------------

def test_stacks_shows_hmac_row_and_flag_for_meshcom_only(tmp_path):
    client, _ = _web(tmp_path)
    body = client.get("/stacks").get_data(as_text=True)
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
                        lambda self, sid, action: calls.append((sid, action))
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
