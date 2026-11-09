"""K4ac/K4b (DEC-W344, DEC-W348, DEC-W327): a stack's instances come from the hardware list, nothing is listed first:
one on each enabled radio of its bands (instances.radios_of), and every per-instance consumer follows: its web pages
(`<page>@<rid>`, on its radio's port, base + 133 x (n - 1) for rN, r1 the base) and its firewall rows
(`<component>-<rid>.tcp-<base>`, from that instance's own store). A config/instances.toml left from before is not
read. Driven through the service's own entries."""
import pytest
import radios_fixture

from lhpc.core import commands, instances, radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
R2_PAGES = [("meshcore@r2", "127.0.0.1:8921"), ("meshcore-meshcore-node@r2", "127.0.0.1:8133")]   # + 133
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


def test_every_radio_of_the_band_is_an_instance_with_its_own_pages_on_its_radios_ports(tmp_path):
    svc = _svc(tmp_path)
    assert instances.radios_of(svc.stack("meshcore"), svc._paths) == ("r2", "r3")
    assert _pages(svc, "meshcore") == [*R2_PAGES, *R3_PAGES]


def test_each_instance_gets_its_own_firewall_row_from_its_own_store(tmp_path):
    # r1 is 433's first radio: it reads the 433 store the save writes (DEC-W384e until K6); r3 its own file (DEC-W60);
    # r2 (868's first, nothing saved for 868) stays on loopback, so no row.
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "kiss", {"kiss_host": "0.0.0.0"}, inst=Instance("r3"))
    assert _kiss_rows(svc) == [("loraham-kiss-tnc-r1.tcp-8001", 8001), ("loraham-kiss-tnc-r3.tcp-8001", 8267)]


def test_one_radio_per_band_gives_one_instance_per_band(tmp_path):
    svc = _svc(tmp_path, ROWS[:2])
    assert _pages(svc, "meshcore") == R2_PAGES
    assert _kiss_rows(svc) == [("loraham-kiss-tnc-r1.tcp-8001", 8001)]


def test_a_disabled_radio_has_no_instance_and_the_bands_first_enabled_one_answers_the_band(tmp_path):
    import dataclasses
    svc = _svc(tmp_path, (ROWS[0], dataclasses.replace(ROWS[1], enabled=False), ROWS[2]))
    assert instances.radios_of(svc.stack("meshcore"), svc._paths) == ("r3",)
    assert instances.band_radio(svc._paths, "868") == "r3"


def test_every_stack_has_an_instance_on_each_radio_of_its_bands(tmp_path):
    svc = _svc(tmp_path)
    got = {s.id: instances.radios_of(s, svc._paths) for s in svc.stacks() if s.id in ("chat", "kiss", "meshcore")}
    assert got == {"chat": ("r1",), "kiss": ("r1", "r2", "r3"), "meshcore": ("r2", "r3")}


@pytest.mark.parametrize("stale", ['meshcore = ["r2"]\n', "meshcore = [", 'kiss = ["r1"]\n'],
                         ids=["another-radio", "not-toml", "the-first-radio"])
def test_an_old_instances_toml_is_not_read(tmp_path, stale):
    svc = _svc(tmp_path, stale=stale)
    assert _pages(svc, "meshcore") == [*R2_PAGES, *R3_PAGES]
    assert [i.rid for i in commands.stack_instances(svc.stack("kiss"), svc._paths)] == ["r1", "r2", "r3"]


def test_without_paths_a_stack_has_the_template_only(tmp_path):
    svc = _svc(tmp_path)
    assert commands.stack_instances(svc.stack("kiss")) == (commands.TEMPLATE,)


def test_w384h_a_stack_with_only_fixed_port_listeners_has_one_instances_pages_and_rows(tmp_path):
    # DEC-W384h, by property (a fake stack, no stack name in the rule): every TCP listener without a port template
    # means one set of ports for whichever radio it runs on, so ONE instance: the radio of its launch record, else the
    # first radio it can run on. A stack with templated ports keeps one instance per radio.
    import dataclasses
    import subprocess
    import sys

    import radios_fixture
    from lhpc.core import commands, instances, radios
    from lhpc.core.manifest import load_manifest
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    kiss = next(s for s in load_manifest() if s.id == "kiss")
    fixed = dataclasses.replace(kiss, components=tuple(
        dataclasses.replace(c, endpoints=tuple(dataclasses.replace(e, address_template="") for e in c.endpoints))
        for c in kiss.components))
    rows = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
    radios_fixture.write(tmp_path, rows)
    paths = Paths(runtime_root=tmp_path)
    assert instances.fixed_ports_only(fixed) and not instances.fixed_ports_only(kiss)
    assert [i.rid for i in commands.stack_instances(kiss, paths)] == ["r1", "r3"]
    assert [i.rid for i in commands.stack_instances(fixed, paths)] == ["r1"]          # no record: the first radio
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        svc = ControllerService(system=FakeSystem().system, paths=paths)
        assert svc._lifecycle().record_launch(kiss, kiss.component("loraham-kiss-tnc"), p.pid, band="433",
                                              extra={"rid": "r3"})
        assert [i.rid for i in commands.stack_instances(fixed, paths)] == ["r3"]      # the radio of its record
    finally:
        p.kill()
        p.wait(timeout=5)
