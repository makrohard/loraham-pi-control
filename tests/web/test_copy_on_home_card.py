"""Cleanup C7c (W286, W290): Home shows a running listed copy (`<stack>@<rid>`) on its own radio's card. Once the
snapshot stopped counting a copy's processes as slot 0's, the copy vanished from Home; the other radio of its band
reads nothing running, and the console offers no Stop it would refuse (the copy's command instead)."""
import re

import radios_fixture
from htmlq import parse

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _home(tmp_path, web, monkeypatch, running=True):
    """kiss@r3 runs (its own argv token) unless `running` is False; slot 0 is stopped; Home rendered over that box."""
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('kiss = ["r3"]\n')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), instances.copy_of(svc, "kiss", "r3")[0], "433")
    sysm = FakeSystem(cmdlines_data={61: ["/x/loraham-kiss-tnc", opt, val]} if running else {}).system
    return parse(web(service_factory=lambda: ControllerService(system=sysm, paths=Paths(runtime_root=tmp_path))).get("/")
                 .get_data(as_text=True))


def _card(doc, rid):
    return doc.within(doc.find("div", class_="radio-col", **{"data-radio-rid": rid})[0])


def test_a_running_copy_shows_on_its_own_radios_card_only(tmp_path, web, monkeypatch):
    doc = _home(tmp_path, web, monkeypatch)
    r3, r1 = _card(doc, "r3"), _card(doc, "r1")
    assert [re.sub(r"\s+", " ", e.text).strip() for e in r3.find("div", class_="runstack")] == [
        "LoRaHAM KISS TNC kiss@r3 running Stop it: lhpc stack stop kiss@r3"]
    assert not r1.find("div", class_="runstack")
    assert "No stack running on this radio." in r1.text
    stops = [f for f in (doc.within(x) for x in doc.find("form", action="/action")) if f.field_default("op") == "stop"]
    assert "kiss@r3" not in [f.field_default("target") for f in stops]        # no form the console would refuse


def test_a_listed_copy_that_does_not_run_shows_nowhere(tmp_path, web, monkeypatch):
    doc = _home(tmp_path, web, monkeypatch, running=False)
    for rid in ("r1", "r3"):
        assert not _card(doc, rid).find("div", class_="runstack") and "No stack running on this radio." in _card(doc, rid).text


def test_a_copy_is_its_radios_bands_alone(tmp_path, web, monkeypatch):
    # The entry is built per band from the copy's own radio: kiss@r3 (r3 on 433) is a running copy of 433, not of 868.
    from lhpc.core import radio_start
    _home(tmp_path, web, monkeypatch)
    idle = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    opt, val = instances._token(idle, idle.stack("kiss").component("loraham-kiss-tnc"),
                                instances.copy_of(idle, "kiss", "r3")[0], "433")
    svc = ControllerService(system=FakeSystem(cmdlines_data={61: ["/x/loraham-kiss-tnc", opt, val]}).system,
                            paths=Paths(runtime_root=tmp_path))
    assert [e["id"] for e in radio_start.running_copies(svc, "433")] == ["kiss@r3"]
    assert radio_start.running_copies(svc, "868") == []
