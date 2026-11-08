"""Must-fix 2 of the stack-copy cleanup (security): the firewall models and gates a listed copy by its OWN saved
port, bind and allow (DEC-W60: a copy's own settings file), never slot 0's. A copy that binds 0.0.0.0 while slot 0
stays on loopback gets its firewall row, and its start is refused until the firewall is verified."""
import radios_fixture
from lhpc.core.commands import Instance
from lhpc.core import config as cfgmod
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
ROW = "loraham-kiss-tnc-r3.tcp-8001"


def _svc(tmp_path, own):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('kiss = ["r3"]\n')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    cfgmod.update_stack_config(svc._paths, "kiss", own, inst=Instance(1, "r3"))              # kiss@r3's own file only
    return svc


def _rows(svc):
    return {e["id"]: e for e in svc.firewall_candidate()["endpoints"] if "kiss" in e["id"]}


def test_a_copy_exposing_its_own_port_gets_its_own_firewall_row(tmp_path):
    rows = _rows(_svc(tmp_path, {"kiss_host": "0.0.0.0", "kiss_port": "8050"}))
    assert set(rows) == {ROW}                                                  # slot 0 stays on loopback: no row
    assert (rows[ROW]["addr"], rows[ROW]["port"]) == ("*", 8060)               # its own port, + 10 for slot 1


def test_the_model_carries_the_copys_own_allow_list(tmp_path):
    rows = _rows(_svc(tmp_path, {"kiss_host": "0.0.0.0", "kiss_bind": "192.168.7.0/24"}))
    assert rows[ROW]["allow_cidrs"] == ["192.168.7.0/24"]


def test_a_copys_exposing_start_is_gated_and_slot0s_loopback_start_is_not(tmp_path, monkeypatch):
    svc = _svc(tmp_path, {"kiss_host": "0.0.0.0"})
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status", lambda: {"config_ok": True, "live_ok": False})
    copy, why = instances.copy_of(svc, "kiss", "r3")
    assert why == ""
    assert svc.start("kiss", apply=False).data.get("firewall_gate") is None
    for refused in (svc.start("kiss", apply=False, inst=copy), svc.start("kiss", apply=True, inst=copy),
                    svc.restart("kiss", apply=False, inst=copy)):
        assert refused.data.get("firewall_gate") == "pending"


def test_a_copys_restart_is_refused_before_its_stop(tmp_path, monkeypatch):
    svc = _svc(tmp_path, {"kiss_host": "0.0.0.0"})
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status", lambda: {"config_ok": True, "live_ok": False})
    res = svc.restart("kiss", apply=True, inst=instances.copy_of(svc, "kiss", "r3")[0])
    assert res.data.get("firewall_gate") == "pending" and not res.results
    assert "restart was not performed; the running stack was left up" in res.summary
