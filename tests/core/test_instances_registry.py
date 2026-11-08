"""K4ac (DEC-W344, DEC-W348): a stack's copies come from the hardware list, nothing is listed first. A stack whose
resources are all per instance (instances.MAY) runs as a copy on each enabled radio of its bands but each band's
first (slot 0's), and every per-instance consumer follows: its web pages (`<page>@<rid>`, on its radio's port, base
+ 133 x (n - 1) for rN) and its firewall rows (`<component>-<rid>.tcp-<base>`). Slot 0 keeps its base (until K4b).
A config/instances.toml left from before is not read. Driven through the service's own entries."""
import pytest
import radios_fixture

from lhpc.core import commands, instances, radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
SLOT0_PAGES = [("meshcore", "127.0.0.1:8788"), ("meshcore-meshcore-node", "127.0.0.1:8000")]
R3_PAGES = [("meshcore@r3", "127.0.0.1:9054"), ("meshcore-meshcore-node@r3", "127.0.0.1:8266")]   # + 133 x 2


def _svc(tmp_path, rows=ROWS, stale=None):
    radios_fixture.write(tmp_path, rows)
    assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID          # the fixture list itself holds
    if stale is not None:
        (tmp_path / "config" / "instances.toml").write_text(stale)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _pages(svc, sid):
    return [(p.page_id, p.address) for p in svc.stack_web_pages(sid)]


def _kiss_rows(svc):
    assert svc.save_stack_config("kiss", {"kiss_bind": "192.168.0.0/24", "kiss_host": "0.0.0.0"}).ok
    return [(e["id"], e["port"]) for e in svc.firewall_candidate()["endpoints"] if "kiss" in e["id"]]


def test_k4ac_a_further_radio_of_the_band_is_a_copy_with_its_own_pages_on_its_radios_ports(tmp_path):
    svc = _svc(tmp_path)
    assert instances.copy_rids(svc.stack("meshcore"), svc._paths) == ("r3",)
    assert _pages(svc, "meshcore") == [*SLOT0_PAGES, *R3_PAGES]


def test_k4ac_a_copy_gets_its_own_firewall_row_on_its_radios_port(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "kiss", {"kiss_host": "0.0.0.0"}, inst=Instance(1, "r3"))   # DEC-W60: its own bind
    assert _kiss_rows(svc) == [("loraham-kiss-tnc.tcp-8001", 8001), ("loraham-kiss-tnc-r3.tcp-8001", 8267)]


def test_k4ac_one_radio_per_band_gives_slot_0_only(tmp_path):
    svc = _svc(tmp_path, ROWS[:2])
    assert _pages(svc, "meshcore") == SLOT0_PAGES
    assert _kiss_rows(svc) == [("loraham-kiss-tnc.tcp-8001", 8001)]


def test_k4ac_a_disabled_radio_gives_no_copy_and_the_bands_first_enabled_one_is_slot_0s(tmp_path):
    import dataclasses
    svc = _svc(tmp_path, (ROWS[0], dataclasses.replace(ROWS[1], enabled=False), ROWS[2]))
    assert instances.copy_rids(svc.stack("meshcore"), svc._paths) == ()
    assert instances.slot0_radio(svc._paths, "meshcore", "868") == ("r3", "")


def test_k4ac_a_stack_outside_may_has_no_copy(tmp_path):
    # TRANSITIONAL (K7 removes MAY, W330): meshtastic, meshcom, voice, reticulum, graywolf run once.
    svc = _svc(tmp_path)
    assert [s.id for s in svc.stacks() if instances.copy_rids(s, svc._paths)] == ["kiss", "meshcore"]   # chat: 433, r1 only


@pytest.mark.parametrize("stale", ['meshcore = ["r2"]\n', "meshcore = [", 'kiss = ["r1"]\n'],
                         ids=["another-radio", "not-toml", "slot-0s-radio"])
def test_k4ac_an_old_instances_toml_is_not_read(tmp_path, stale):
    svc = _svc(tmp_path, stale=stale)
    assert _pages(svc, "meshcore") == [*SLOT0_PAGES, *R3_PAGES]
    assert [i.rid for i in commands.stack_instances(svc.stack("kiss"), svc._paths)] == ["", "r3"]


def test_without_paths_a_stack_has_slot_0_only(tmp_path):
    svc = _svc(tmp_path)
    assert commands.stack_instances(svc.stack("kiss")) == (commands.SLOT0,)
