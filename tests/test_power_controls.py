"""Dashboard power controls (Reboot / Shut down): the polkit-rule dependency, the logind
authorization handshake, the power-pending admission gate (one choke point in _acquire_key),
the bounded respond-first trigger, and the web flow (hidden-unless-supported + confirm)."""

from __future__ import annotations

import json
import re

import pytest

from lhpc.core import deps as deps_mod
from lhpc.core import lifecycle as lcmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import AdmissionRefused
from lhpc.core.services import ControllerService

UPTIME = "/proc/uptime"


def _svc(tmp_path, uptime="100.00 380.00\n", **fk):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    files = {UPTIME: uptime, **fk.pop("files", {})}
    return ControllerService(system=FakeSystem(files=files, **fk).system,
                             paths=Paths(runtime_root=tmp_path))


def _busctl(svc, monkeypatch, verdict='s "yes"\n', rc=0):
    """Intercept ONLY busctl; every other runner call keeps its real Fake behavior."""
    orig = svc._system.runner.run

    class R:
        returncode = rc
        stdout = verdict
        stderr = ""

    def run(argv, timeout=None):
        return R() if argv and argv[0] == "busctl" else orig(argv, timeout)
    monkeypatch.setattr(svc._system.runner, "run", run)


def _spawn_spy(monkeypatch, pid=4242, raise_exc=None):
    calls = []

    def spy(self, argv, log_path, cwd=None, env=None):
        calls.append((list(argv), str(log_path)))
        if raise_exc is not None:
            raise raise_exc
        return pid
    monkeypatch.setattr(lcmod.Lifecycle, "_real_spawn", spy)
    return calls


# --- shared helpers (deps.py) ---------------------------------------------------------------------


def test_rule_text_and_install_cmd():
    txt = deps_mod.power_rule_text("makro")
    assert 'subject.user == "makro"' in txt
    for act in ("reboot", "reboot-multiple-sessions", "power-off",
                "power-off-multiple-sessions"):
        assert f'"org.freedesktop.login1.{act}"' in txt
    cmd = deps_mod.power_rule_install_cmd("makro")
    assert "sudo apt install -y polkitd" in cmd
    assert f"sudo install -D -m 0644 /dev/stdin {deps_mod.POWER_RULE_PATH}" in cmd
    assert "tee" not in cmd


def test_dependency_entry_probe_and_bootstrap_exclusion(tmp_path):
    svc = _svc(tmp_path)
    grp = next(g for g in svc.controller_system_deps() if g["title"] == "Power controls")
    d = grp["deps"][0]
    assert d["required"] is False and d["bootstrap"] is False
    assert d["satisfied"] is False                            # bare FakeSystem: nothing present
    assert "49-lhpc-power.rules" in d["install"]
    import getpass
    assert getpass.getuser() in d["install"]                  # THIS box's user, paste-ready
    # satisfied flips only with rule file AND polkitd
    svc2 = _svc(tmp_path / "b", files={deps_mod.POWER_RULE_PATH: "x"})
    d2 = next(g for g in svc2.controller_system_deps()
              if g["title"] == "Power controls")["deps"][0]
    assert d2["satisfied"] is False                           # rule alone is not enough
    svc3 = _svc(tmp_path / "c", files={deps_mod.POWER_RULE_PATH: "x",
                                       "/usr/lib/polkit-1/polkitd": "x"})
    d3 = next(g for g in svc3.controller_system_deps()
              if g["title"] == "Power controls")["deps"][0]
    assert d3["satisfied"] is True
    # the leak test: the copybox never reaches the generated bootstrap command set — the
    # ONLY rule-install site in the script is the dedicated scaffold (a leak would add a
    # second `install -D` block and a literal username)
    core, gui, gps = svc._declared_dep_scopes()
    assert not any("49-lhpc-power" in c for c in core + gui + gps)
    script = svc.deps_script()
    assert script.count("install -D -m 0644 /dev/stdin") == 1
    import getpass
    assert getpass.getuser() not in script                    # no baked username, ever


def test_power_supported_requires_the_full_execution_path(tmp_path):
    base = {deps_mod.POWER_RULE_PATH: "x", "/usr/lib/polkit-1/polkitd": "x",
            "/usr/bin/busctl": "x", "/usr/bin/systemctl": "x"}
    assert _svc(tmp_path, files=base).power_supported() is True
    for missing in base:
        files = {k: v for k, v in base.items() if k != missing}
        assert _svc(tmp_path / re.sub(r"\W", "_", missing),
                    files=files).power_supported() is False, missing


# --- service: dry-run + apply ---------------------------------------------------------------------


def test_unknown_kind_refused(tmp_path):
    res = _svc(tmp_path).power_action("halt", apply=True)
    assert not res.ok and "unknown power action" in res.summary


def test_dry_run_names_running_stacks_and_notes(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stack_running",
                        lambda self, t: t in ("graywolf", "kiss"))
    res = svc.power_action("reboot", apply=False)
    assert res.ok and "Reboot" in res.summary
    assert any("[running] graywolf" in d for d in res.details)
    assert any("[running] kiss" in d for d in res.details)
    assert any("boot-restore" in d for d in res.details)
    off = svc.power_action("poweroff", apply=False)
    assert any("PHYSICAL access" in d for d in off.details)


def test_apply_refuses_without_boot_id(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "")
    res = svc.power_action("reboot", apply=True)
    assert not res.ok and "boot id" in res.summary


@pytest.mark.parametrize("verdict,rc", [
    ('s "no"\n', 0), ('s "challenge"\n', 0), ('s "na"\n', 0),
    ("yes\n", 0),                       # unstructured output is NOT a yes
    ('s "yes" trailing\n', 0),          # must match exactly
    ("", 7),                            # command failure / timeout
])
def test_handshake_refusals_are_typed_and_carry_the_copybox(tmp_path, monkeypatch,
                                                            verdict, rc):
    svc = _svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    _busctl(svc, monkeypatch, verdict=verdict, rc=rc)
    spawned = _spawn_spy(monkeypatch)
    res = svc.power_action("reboot", apply=True)
    assert not res.ok and "Cannot reboot" in res.summary
    assert any("49-lhpc-power.rules" in d for d in res.details)   # the healing copybox
    assert not spawned and not svc._power_pending_path().exists()


def test_apply_success_marker_and_bounded_trigger(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    _busctl(svc, monkeypatch)
    spawned = _spawn_spy(monkeypatch)
    res = svc.power_action("poweroff", apply=True)
    assert res.ok and "console will go dark" in res.summary
    argv, _ = spawned[0]
    assert argv[:2] == ["sh", "-c"]
    assert "timeout -k 5s 90s systemctl --no-block poweroff" in argv[2]
    assert "sleep 1.5" in argv[2]
    rec = json.loads(svc._power_pending_path().read_text())
    assert rec == {"kind": "poweroff", "boot_id": "boot-1", "requested_uptime": 100.0}
    assert any("ONLY in" in d for d in res.details)               # the log-only contract


def test_marker_write_failure_is_typed_and_spawns_nothing(tmp_path, monkeypatch):
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    _busctl(svc, monkeypatch)
    spawned = _spawn_spy(monkeypatch)
    orig_aw = runtime_fs.atomic_write

    def boom(paths, path, *a, **k):
        # fail ONLY the pending-marker write — reslock's own record writes stay real
        if "power-pending" in str(path):
            raise OSError("read-only filesystem")
        return orig_aw(paths, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "atomic_write", boom)
    res = svc.power_action("reboot", apply=True)
    assert not res.ok and "could not record the pending action" in res.summary
    assert not spawned


def test_spawn_failure_removes_the_marker(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    _busctl(svc, monkeypatch)
    _spawn_spy(monkeypatch, raise_exc=OSError("no fds"))
    res = svc.power_action("reboot", apply=True)
    assert not res.ok and "pending guard was cleared" in res.summary
    assert not svc._power_pending_path().exists()


# --- the power-pending admission gate (ONE choke point) -------------------------------------------


def _arm(svc, monkeypatch, kind="poweroff", uptime0=100.0, boot="boot-1"):
    (svc._paths.runtime_root / "state").mkdir(exist_ok=True)
    svc._power_pending_path().write_text(json.dumps(
        {"kind": kind, "boot_id": boot, "requested_uptime": uptime0}))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")


def test_gate_refuses_admission_guard(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _arm(svc, monkeypatch)
    with pytest.raises(AdmissionRefused, match="poweroff of this box is pending"), \
            svc._admission_guard("build", "x"):
        pass


def test_gate_refuses_admit_raw(tmp_path, monkeypatch):
    import contextlib
    svc = _svc(tmp_path)
    _arm(svc, monkeypatch)
    with pytest.raises(AdmissionRefused), contextlib.ExitStack() as st:
        svc._admit_raw(st, "self-update-helper", "")


def test_gate_refuses_self_update_trigger_typed(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _arm(svc, monkeypatch)
    # satisfy the managed-service + integration + availability preconditions so the trigger
    # genuinely reaches its DIRECT `_acquire_key(ADMISSION_KEY)` call — where the
    # power-pending check must refuse it as a typed ActionResult, never an escaping exception.
    monkeypatch.setenv("INVOCATION_ID", "test-invocation")
    monkeypatch.setattr(ControllerService, "updater_integration",
                        lambda self: {"status": "ok"})
    monkeypatch.setattr(ControllerService, "self_update_status",
                        lambda self: {"available": True})
    res = svc.self_update_trigger()                            # must NOT raise
    assert res.ok is False
    assert "pending" in res.summary and res.data.get("admission_blocked") == "power-pending"


def test_gate_refuses_uninstall_prep_typed(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _arm(svc, monkeypatch)
    res = svc.controller_uninstall_prep()                      # must NOT raise
    assert res.ok is False and "pending" in res.summary


def test_gate_reentrant_acquire_stays_exempt(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    with svc._admission_guard("outer", "x"):
        _arm(svc, monkeypatch)                                 # marker appears while held
        with svc._admission_guard("nested", "x"):              # reentrant -> no fresh check
            pass


def test_gate_prunes_valid_stale_and_other_boot_markers(tmp_path, monkeypatch):
    svc = _svc(tmp_path, uptime="500.00 900.00\n")             # 400s after request -> expired
    _arm(svc, monkeypatch, uptime0=100.0)
    assert svc._power_pending_blocked() is None
    assert not svc._power_pending_path().exists()              # pruned
    _arm(svc, monkeypatch, boot="other-boot")
    assert svc._power_pending_blocked() is None
    assert not svc._power_pending_path().exists()              # pruned


def test_gate_malformed_marker_refuses_and_is_kept(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    (svc._paths.runtime_root / "state").mkdir(exist_ok=True)
    svc._power_pending_path().write_text("{not json")
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    blocked = svc._power_pending_blocked()
    assert blocked is not None and "unreadable" in blocked[0]
    assert str(svc._power_pending_path()) in blocked[0]        # names the file to inspect
    assert svc._power_pending_path().exists()                  # NEVER auto-pruned


@pytest.mark.parametrize("field,value", [
    ("boot_id", None),                  # AUDIT: str(None) laundered to "None" -> pruned
    ("boot_id", ""),
    ("boot_id", 7),
    ("requested_uptime", float("nan")),  # AUDIT: NaN comparisons all False -> pruned
    ("requested_uptime", float("inf")),
    ("requested_uptime", -5.0),
    ("requested_uptime", True),
    ("requested_uptime", "100"),
    ("kind", "halt"),                   # outside the closed set
])
def test_gate_parseable_but_invalid_marker_refuses_and_is_kept(tmp_path, monkeypatch,
                                                               field, value):
    """AUDIT (fail-open): parseable-but-invalid markers must refuse like unreadable ones —
    type-coerced null/NaN used to slip into the prune path and admit new work while the
    delayed reboot was still live."""
    svc = _svc(tmp_path)
    (svc._paths.runtime_root / "state").mkdir(exist_ok=True)
    rec = {"kind": "poweroff", "boot_id": "boot-1", "requested_uptime": 100.0, field: value}
    svc._power_pending_path().write_text(json.dumps(rec))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    blocked = svc._power_pending_blocked()
    assert blocked is not None and "unreadable" in blocked[0]
    assert svc._power_pending_path().exists()                  # retained, never pruned


def test_gate_unreadable_current_boot_id_fails_closed(tmp_path, monkeypatch):
    """AUDIT (fail-open): an EMPTY current_boot_id() used to read as 'different boot' —
    deleting a possibly-live marker and admitting work the delayed reboot would kill.
    It must refuse and RETAIN; admission reopens once the boot id reads again."""
    svc = _svc(tmp_path)
    _arm(svc, monkeypatch)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "")
    blocked = svc._power_pending_blocked()
    assert blocked is not None and "cannot be read" in blocked[0]
    assert svc._power_pending_path().exists()                  # retained
    with pytest.raises(AdmissionRefused), \
            svc._admission_guard("build", "x"):                # and admission genuinely refused
        pass
    # boot id readable again, same boot -> still pending (fresh marker) -> still refused
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    assert svc._power_pending_blocked() is not None


def test_second_power_click_is_refused_while_pending(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _arm(svc, monkeypatch, kind="reboot")
    res = svc.power_action("reboot", apply=True)
    assert not res.ok and "pending" in res.summary


# --- web ------------------------------------------------------------------------------------------


def _web(tmp_path, monkeypatch=None, supported=False):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    if monkeypatch is not None and supported:
        monkeypatch.setattr(ControllerService, "power_supported", lambda self: True)
    return svc, create_app(lambda: svc).test_client()


def _tok(c):
    body = c.get("/stacks").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_web_hidden_and_404_when_unsupported(tmp_path):
    _, c = _web(tmp_path)
    body = c.get("/").get_data(as_text=True)
    assert "Shut down" not in body and "/power/" not in body
    assert c.post("/power/reboot", data={"_csrf": _tok(c)}).status_code == 404


def test_web_csrf_required(tmp_path, monkeypatch):
    _, c = _web(tmp_path, monkeypatch, supported=True)
    assert c.post("/power/reboot").status_code == 400


def test_web_buttons_confirm_and_apply(tmp_path, monkeypatch):
    _, c = _web(tmp_path, monkeypatch, supported=True)
    body = c.get("/").get_data(as_text=True)
    assert 'action="/power/reboot"' in body and 'action="/power/poweroff"' in body
    tok = _tok(c)
    # unknown kind -> 404 even when supported
    assert c.post("/power/halt", data={"_csrf": tok}).status_code == 404
    # stage 1: the confirm page, posting back to the SAME power route
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, t: t == "graywolf")
    r = c.post("/power/poweroff", data={"_csrf": tok})
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Shut down this box?" in page
    assert 'action="/power/poweroff"' in page
    assert "graywolf" in page                                  # the running-stack names
    assert "physical access" in page.lower()
    # stage 2: applies via the service and redirects to the dashboard
    seen = {}

    def fake_apply(self, kind, apply=False):
        from lhpc.core.services import ActionResult
        seen["kind"], seen["apply"] = kind, apply
        return ActionResult(True, "poweroff requested")
    monkeypatch.setattr(ControllerService, "power_action", fake_apply)
    r2 = c.post("/power/poweroff", data={"_csrf": tok, "confirmed": "yes"})
    assert r2.status_code in (302, 303)
    assert seen == {"kind": "poweroff", "apply": True}
