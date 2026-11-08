"""Cleanup C7, the console's part: a listed copy's restart-required marker (`<stack>@<rid>`) shows on that copy's
settings view and in Home's banner, with the command that restarts it (the console has no restart for a copy), while
slot 0's row and its "Restart now" stay as they are."""
import re

import radios_fixture
from htmlq import parse

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _marked(tmp_path):
    """kiss@r3 runs and its saved restart-mode change marked it (the core's real write path); slot 0 is unmarked."""
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('kiss = ["r3"]\n')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), copy, "433")
    svc = ControllerService(system=FakeSystem(cmdlines_data={61: ["/x/loraham-kiss-tnc", opt, val]}).system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=instances.copy_of(svc, "kiss", "r3")[0]).ok
    return svc


def _text(el):
    return re.sub(r"\s+", " ", el.text).strip()


def test_a_copys_marker_shows_on_its_settings_with_the_command_and_not_on_slot0s_row(tmp_path, web):
    c = web(service_factory=lambda: _marked(tmp_path))
    doc = parse(c.get("/stacks?cfg=kiss&radio=r3&open=kiss").get_data(as_text=True))
    notes = [e for e in doc.find("div", class_="depnote") if "Restart required" in e.text]
    assert [n["id"] for n in notes] == ["copy-restart-required"]           # the copy's own; slot 0's row carries none
    assert _text(notes[0]) == ("Restart required — saved start-time settings (kiss_port) differ from what the running "
                               "stack uses. Restart it: lhpc stack restart kiss@r3")


def test_homes_banner_names_the_copys_restart_command_and_offers_no_button_for_it(tmp_path, web):
    home = parse(web(service_factory=lambda: _marked(tmp_path)).get("/").get_data(as_text=True))
    assert [_text(e) for e in home.find("code", class_="restart-copy")] == ["lhpc stack restart kiss@r3"]
    forms = (home.within(f) for f in home.find("form", action="/action"))
    assert [f.field_default("target") for f in forms if f.field_default("op") == "restart"] == []
