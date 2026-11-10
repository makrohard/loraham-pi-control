"""DEC-W384cs: meshtastic@rN's meshtasticd listens on its own instance's ports, the ones every LHPC part already
expects (endpoints, claims, firewall rows, the proxy upstream, `lhpc meshtastic@rN`: {port:4403}/{port:9443}). The
rendered meshtasticd.yaml carries General.APIPort and Webserver.Port of the instance (meshtasticd v2.7.26 reads them:
PortduinoGlue.cpp :1056-1059 APIPort without -p, :1007 Webserver.Port); both are derived, never operator-settable.
Before, every instance's file said 9443 and no APIPort (4403), so meshtastic@r2 bound r1's ports."""
import pytest
import radios_fixture
import yaml

from lhpc.core import config as cfgmod
from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    radios_fixture.write(tmp_path)                    # the LoRaHAM list: r1 on 433, r2 on 868
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _rendered(svc, tmp_path, rid):
    inst = instances.copy_of(svc, "meshtastic", rid)[0]
    assert all(w.status == "written" for w in svc.write_config_files("meshtastic", inst.band, inst=inst))
    doc = yaml.safe_load((tmp_path / "config" / "files" / rid / "meshtasticd.yaml").read_text())
    return inst, doc


@pytest.mark.parametrize("rid, api, web", [("r1", 4403, 9443), ("r2", 4536, 9576)])
def test_each_instances_meshtasticd_listens_on_its_own_endpoint_ports(tmp_path, rid, api, web):
    svc = _svc(tmp_path)
    inst, doc = _rendered(svc, tmp_path, rid)
    assert (doc["General"].get("APIPort"), doc["Webserver"].get("Port")) == (api, web)
    comp = svc.stack("meshtastic").main_component
    ports = sorted(int(e.address.rpartition(":")[2]) for e in instances.endpoints_for(comp.endpoints, inst))
    assert ports == sorted((api, web))                  # the readiness endpoint is the port the file makes it bind


def test_the_ports_are_derived_a_save_is_refused_and_a_stored_value_never_read(tmp_path):
    svc = _svc(tmp_path)
    r2 = instances.copy_of(svc, "meshtastic", "r2")[0]
    for name in ("api_port", "web_port"):
        r = svc.save_config_bundle("meshtastic", values={f"file_{name}": "5000"}, inst=r2)
        assert not r.ok and f"{name}: derived: LHPC computes it" in " ".join(r.details), (r.summary, r.details)
    cfgmod.save_stack_config(svc._paths, "meshtastic", {"file_api_port": "5000", "file_web_port": "5001"}, inst=r2)
    svc._invalidate_config()                            # a hand edit of the instance's store
    _inst, doc = _rendered(svc, tmp_path, "r2")
    assert (doc["General"]["APIPort"], doc["Webserver"]["Port"]) == (4536, 9576)
