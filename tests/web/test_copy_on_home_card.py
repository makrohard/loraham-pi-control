"""Cleanup C7c (W286, W290): Home shows a running listed copy (`<stack>@<rid>`) on its own radio's card. Once the
snapshot stopped counting a copy's processes as slot 0's, the copy vanished from Home; the other radio of its band
reads nothing running, and the copy's card has its own Stop and Restart (W325), addressed `<stack>@<rid>`."""
import re

import radios_fixture
from htmlq import parse

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _home(tmp_path, web, monkeypatch, running=True, listening=False):
    """kiss@r3 runs (its own argv token) unless `running` is False, its own listener (127.0.0.1:8267, r3) up when
    `listening`; slot 0 is stopped; Home rendered over that box."""
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), instances.copy_of(svc, "kiss", "r3")[0], "433")
    sysm = FakeSystem(cmdlines_data={61: ["/x/loraham-kiss-tnc", opt, val]} if running else {},
                      listeners=[Listener("ipv4", "127.0.0.1", 8267, 1)] if listening else []).system
    return parse(web(service_factory=lambda: ControllerService(system=sysm, paths=Paths(runtime_root=tmp_path))).get("/")
                 .get_data(as_text=True))


def _card(doc, rid):
    return doc.within(doc.find("div", class_="radio-col", **{"data-radio-rid": rid})[0])


def test_a_running_copy_shows_on_its_own_radios_card_only(tmp_path, web, monkeypatch):
    doc = _home(tmp_path, web, monkeypatch)
    r3, r1 = _card(doc, "r3"), _card(doc, "r1")
    assert [re.sub(r"\s+", " ", e.text).strip() for e in r3.find("div", class_="runstack")] == [
        "LoRaHAM KISS TNC kiss@r3 degraded LoRaHAM KISS/TCP TNC listener absent: 127.0.0.1:8267 Stop Restart"]
    assert not r1.find("div", class_="runstack")
    assert "No stack running on this radio." in r1.text
    forms = [r3.within(x) for x in r3.find("form", action="/action")]
    assert [(f.field_default("op"), f.field_default("target")) for f in forms
            if f.field_default("target") == "kiss@r3"] == [("stop", "kiss@r3"), ("restart", "kiss@r3")]   # W325


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
    live = {cid: st for ss in svc.build_snapshot().stacks for cid, st in ss.components.items()}
    assert [e["id"] for e in radio_start.running_copies(svc, "433", live)] == ["kiss@r3"]
    assert radio_start.running_copies(svc, "868", live) == []


def test_a_copy_whose_own_listener_is_up_reads_running_with_no_note(tmp_path, web, monkeypatch):
    # G1-C49: the copy's rows are judged on its OWN listener, as slot 0's are on slot 0's (already_healthy's calls).
    r3 = _card(_home(tmp_path, web, monkeypatch, listening=True), "r3")
    assert [re.sub(r"\s+", " ", e.text).strip() for e in r3.find("div", class_="runstack")] == [
        "LoRaHAM KISS TNC kiss@r3 running LoRaHAM KISS/TCP TNC Stop Restart"]


def test_a_copys_entry_and_rows_come_from_one_read_of_its_processes(tmp_path, web, monkeypatch):
    # G1-C51: the entry and its rows are built from ONE copy_processes read, so a copy stopping between two probes
    # cannot leave an entry without rows; no second instance_runs probe is made.
    from lhpc.core import radio_start
    _home(tmp_path, web, monkeypatch)
    idle = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    opt, val = instances._token(idle, idle.stack("kiss").component("loraham-kiss-tnc"),
                                instances.copy_of(idle, "kiss", "r3")[0], "433")
    svc = ControllerService(system=FakeSystem(cmdlines_data={61: ["/x/loraham-kiss-tnc", opt, val]}).system,
                            paths=Paths(runtime_root=tmp_path))
    live = {cid: st for ss in svc.build_snapshot().stacks for cid, st in ss.components.items()}
    reads, real = [], instances.copy_processes
    monkeypatch.setattr(instances, "copy_processes", lambda s, t: reads.append(1) or real(s, t))

    def no_second_probe(*a, **k):
        raise AssertionError("running_copies probed a copy again")
    monkeypatch.setattr(instances, "instance_runs", no_second_probe)
    entries = radio_start.running_copies(svc, "433", live)
    assert [(e["id"], [c["id"] for c in e["components"]]) for e in entries] == [("kiss@r3", ["loraham-kiss-tnc"])]
    assert reads == [1]
