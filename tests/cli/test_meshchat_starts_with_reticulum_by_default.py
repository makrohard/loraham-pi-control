"""His word 11-23 (D-M1 = A: "meshchat to start as default with reti"; "ship the meshchat default on with 0.14.0"):
a reticulum stack start selects MeshChat unless the radio saved it off. Effective setting after an update = the radio's
saved choice if one exists, else the new default. Selected is not started: a MeshChat whose build is missing or older
than its sources is BLOCKED with the rebuild remedy LHPC prints, and the stack start reports non-success while the node
is up (a stated limit, as for the MeshCore web UI)."""
import json
import tomllib

import pytest
from radios_fixture import R1, R2_868

from lhpc.adapters.cli import main as cli
from lhpc.core import config as cfgmod
from lhpc.core import instances
from lhpc.core import service_boot_restore as sbr
from lhpc.core.commands import Instance
from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.services import ControllerService


def _reticulum(box, tmp_path, monkeypatch):
    run = box((R1, R2_868), paths=[str(tmp_path / "state" / "reticulum" / "ready")])
    monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, comp: [])   # the PC has no SPI device
    return run


def _plan(op, rid="r2"):
    """The start/restart plan of reticulum@<rid> (what it selects, nothing launched)."""
    svc = cli.ControllerService()
    res = getattr(svc, op)("reticulum", apply=False, inst=instances.parse_target(svc, f"reticulum@{rid}", "start")[1])
    return "\n".join([res.summary, *res.details])


def _launched(launches, comp):
    return [rid for c, _band, rid in launches if c == comp]


def _untick(tmp_path, rid):
    """The radio's Settings save with MeshChat unticked (the web form and `lhpc config` take this path)."""
    svc = cli.ControllerService()
    res = svc.save_config_bundle("reticulum", values={"autostart_meshchat": ""},
                                 inst=instances.parse_target(svc, f"reticulum@{rid}", "start")[1])
    assert res.ok, (res.summary, res.details)
    p = tmp_path / "config" / "stacks" / f"reticulum@{rid}.toml"
    return tomllib.loads(p.read_text()) if p.exists() else {}


def _stale(monkeypatch):
    """MeshChat's checkout is present, its build is not current (no build marker, or one from older sources)."""
    monkeypatch.setattr(ControllerService, "_build_complete", lambda self, c: c.id != "meshchat")


def test_a_reticulum_start_selects_meshchat_after_the_node_by_default(box, tmp_path, monkeypatch):
    # Red before: the plan held the node only (no component of the stack had the default).
    _reticulum(box, tmp_path, monkeypatch)
    plan = _plan("start")
    assert "  [start] rns (band 868)" in plan and "  [start] meshchat (band -)" in plan, plan
    assert plan.index("[start] rns") < plan.index("[start] meshchat"), plan


def test_an_untick_is_saved_as_off_and_survives_a_settings_save_and_a_restart(box, tmp_path, monkeypatch):
    # Only a deviation from the default is stored: "off" now differs from it. Red before: the untick stored nothing.
    run = _reticulum(box, tmp_path, monkeypatch)
    assert _untick(tmp_path, "r2").get("autostart_meshchat") == "off"
    assert _untick(tmp_path, "r2").get("autostart_meshchat") == "off"     # a second Settings save keeps it
    rc, out, launches = run("start", "reticulum@r2")
    assert rc == 0 and _launched(launches, "rns") == ["r2"] and _launched(launches, "meshchat") == [], out
    rc, out, launches = run("restart", "reticulum@r2")                    # and a restart keeps it off
    assert rc == 0 and _launched(launches, "rns") == ["r2", "r2"] and _launched(launches, "meshchat") == [], out


def test_a_saved_on_from_the_old_opt_in_stays_selected(box, tmp_path, monkeypatch):
    _reticulum(box, tmp_path, monkeypatch)
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "reticulum", {"autostart_meshchat": "on"},
                               inst=Instance("r2"))                          # as the old default-off store kept it
    assert "  [start] meshchat (band -)" in _plan("start"), _plan("start")


@pytest.mark.parametrize("checkout", ["present", "absent"])   # stale/unbuilt; a stack build skipped its absent source
def test_a_meshchat_not_built_is_blocked_with_its_remedy_and_the_start_is_non_success(box, tmp_path, monkeypatch,
                                                                                       checkout):
    # The stated limit (B1), asserted as it is: the node verified, MeshChat BLOCKED with the remedy LHPC prints,
    # the stack result non-success. Red before: MeshChat was not selected, the start succeeded.
    run = _reticulum(box, tmp_path, monkeypatch)
    _stale(monkeypatch)
    if checkout == "absent":
        monkeypatch.setattr(ControllerService, "_source_present", lambda self, c: c.id != "meshchat")
    rc, out, launches = run("start", "reticulum@r2")
    assert "  [verified] rns: started" in out and _launched(launches, "rns") == ["r2"], out
    assert "  [blocked] meshchat: its sources changed since the last build — rebuild first (lhpc build reticulum)" \
        in out and _launched(launches, "meshchat") == [], out
    assert rc != 0 and "Run FAILED for 'reticulum': " in out, out


def test_a_stack_restart_selects_meshchat_by_default(box, tmp_path, monkeypatch):
    # The documented recipe after an interface change (T1). Red before: the restart plan held the node only.
    _reticulum(box, tmp_path, monkeypatch)
    plan = _plan("restart")
    assert "  [start] rns (band 868)" in plan and "  [start] meshchat (band -)" in plan, plan


def test_boot_restore_brings_meshchat_back_with_the_stack_by_default(box, tmp_path, monkeypatch):
    # Red before: the restored reticulum@r2 ran the node only.
    _reticulum(box, tmp_path, monkeypatch)
    svc, launches = cli.ControllerService(), []
    monkeypatch.setattr(Lifecycle, "start", lambda self, stack, comp, params=None, band="", **kw: (
        launches.append((comp.id, band, (kw.get("inst") or Instance("")).rid)) or StartLaunch(True, "")))
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))
    pid = 999981
    rec = {"launch_id": f"rns__868__{pid}__{pid:032x}", "stack": "reticulum", "component": "rns", "band": "868",
           "pid": pid, "role": "", "launched_at": 1000, "version": 1, "requested_target": "reticulum",
           "start_scope": "stack", "boot_id": "OLDBOOT", "starttime": "123", "pgid": pid, "sid": pid, "rid": "r2"}
    d = tmp_path / "state" / "owned"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
    res = svc.boot_restore_run()
    assert _launched(launches, "rns") == ["r2"] and _launched(launches, "meshchat") == ["r2"], (res.summary, launches)

