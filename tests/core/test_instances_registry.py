"""Item 7b (DEC-W15): the stack instances beyond slot 0 come from config/instances.toml, one append-only list per
stack (slot k = position k; "" a reserved slot), and every per-instance consumer follows them: the stack's web
pages (`<page>@<rid>`, its own port BASE + 10 x slot) and its firewall rows (`<component>-<rid>.tcp-<base>`).
A file that does not read back gives slot 0 only. Driven through the service's own entries; the file is written
by hand here (add-instance is item 7c). The registry module's own reads: test_instances_load.py."""
import pytest
import radios_fixture

from lhpc.core.commands import Instance
from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
SLOT0_PAGES = [("meshcore", "127.0.0.1:8788"), ("meshcore-meshcore-node", "127.0.0.1:8000")]


def _svc(tmp_path, text=None):
    radios_fixture.write(tmp_path, ROWS)
    assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID          # the fixture list itself holds
    if text is not None:
        (tmp_path / "config" / "instances.toml").write_text(text)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _pages(svc, sid):
    return [(p.page_id, p.address) for p in svc.stack_web_pages(sid)]


def _kiss_rows(svc):
    assert svc.save_stack_config("kiss", {"kiss_bind": "192.168.0.0/24", "kiss_host": "0.0.0.0"}).ok
    return [(e["id"], e["port"]) for e in svc.firewall_candidate()["endpoints"] if "kiss" in e["id"]]


def test_a_listed_instance_gets_its_own_pages_on_its_slots_ports(tmp_path):
    svc = _svc(tmp_path, 'meshcore = ["", "r2"]\n')               # slot 1 reserved, r2 at slot 2
    assert _pages(svc, "meshcore") == [*SLOT0_PAGES, ("meshcore@r2", "127.0.0.1:8808"),
                                       ("meshcore-meshcore-node@r2", "127.0.0.1:8020")]


def test_a_listed_instance_gets_its_own_firewall_row(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path, 'kiss = ["r3"]\n')
    cfgmod.update_stack_config(svc._paths, "kiss", {"kiss_host": "0.0.0.0"}, inst=Instance(1, "r3"))   # DEC-W60: its own bind
    assert _kiss_rows(svc) == [("loraham-kiss-tnc.tcp-8001", 8001), ("loraham-kiss-tnc-r3.tcp-8001", 8011)]


def test_without_a_registry_a_stack_has_its_slot_0_pages_and_rows_only(tmp_path):
    svc = _svc(tmp_path)
    assert _pages(svc, "meshcore") == SLOT0_PAGES
    assert _kiss_rows(svc) == [("loraham-kiss-tnc.tcp-8001", 8001)]


@pytest.mark.parametrize("text", ["meshcore = [", 'meshcore = ["r0"]', 'meshcore = ["r2", "r2"]'],
                         ids=["not-toml", "bad-rid", "twice"])
def test_a_registry_that_does_not_read_back_gives_slot_0_only(tmp_path, text):
    assert _pages(_svc(tmp_path, text), "meshcore") == SLOT0_PAGES
