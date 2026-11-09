"""Cleanup C7, the console's part: a listed copy's restart-required marker (`<stack>@<rid>`) shows on that copy's
settings view and in Home's banner, each with its own "Restart now" button (W325), posting `<stack>@<rid>`, while
the other instance's row (kiss@r1, the band's first radio's) and its "Restart now" stay as they are (K5)."""
import re

import radios_fixture
from htmlq import parse

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _marked(tmp_path):
    """kiss@r3 runs and its saved restart-mode change marked it (the core's real write path); kiss@r1 is unmarked."""
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), copy, "433")
    svc = ControllerService(system=FakeSystem(cmdlines_data={61: ["/x/loraham-kiss-tnc", opt, val]}).system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=instances.copy_of(svc, "kiss", "r3")[0]).ok
    return svc


def _text(el):
    return re.sub(r"\s+", " ", el.text).strip()


def test_a_copys_marker_shows_on_its_settings_with_its_own_restart_button_and_not_on_the_bare_row(tmp_path, web):
    c = web(service_factory=lambda: _marked(tmp_path))
    doc = parse(c.get("/stacks?cfg=kiss&radio=r3&open=kiss").get_data(as_text=True))
    notes = [e for e in doc.find("div", class_="depnote") if "Restart required" in e.text]
    assert [n["id"] for n in notes] == ["copy-restart-required"]           # the copy's own; kiss@r1's row carries none
    assert _text(notes[0]) == ("Restart required — saved start-time settings (kiss_port) differ from what the running "
                               "stack uses. Restart now")
    form = doc.within(doc.within(notes[0]).find("form", action="/action")[0])
    assert (form.field_default("op"), form.field_default("target")) == ("restart", "kiss@r3")   # W325: this copy


def test_homes_banner_offers_the_copys_own_restart_button(tmp_path, web):
    home = parse(web(service_factory=lambda: _marked(tmp_path)).get("/").get_data(as_text=True))
    forms = [home.within(f) for f in home.find("form", action="/action")]
    restart = [f for f in forms if f.field_default("op") == "restart" and f.field_default("target") == "kiss@r3"]
    assert sorted(_text(f) for f in restart) == ["Restart", "Restart kiss@r3 now"]   # W325: the banner's and its card's


def _spy_spawns(monkeypatch):
    spawns = []

    def spy(self, op, target, band="", stop_owners=False, cascade=False, rid="", plan_hash=None):
        spawns.append((op, target, band, rid))
        return f"web-{op}-{target}-{rid}.log", "admitted", ""
    monkeypatch.setattr(ControllerService, "spawn_start_job", spy)
    return spawns


def test_the_copys_restart_button_restarts_exactly_that_copy(tmp_path, web, monkeypatch, csrf):
    # W325: POST op=restart target=kiss@r3 (the copy's button) spawns kiss's restart on r3 (its band, its rid).
    svc = _marked(tmp_path)
    c = web(service_factory=lambda: svc)
    spawns = _spy_spawns(monkeypatch)
    r = c.post("/action", data={"_csrf": csrf(c), "op": "restart", "target": "kiss@r3", "from": "dash"})
    assert r.status_code == 302 and spawns == [("restart", "kiss", "433", "r3")]


def test_the_copys_stop_confirms_and_stops_exactly_that_copy(tmp_path, web, monkeypatch, csrf):
    # W325: the card's Stop (target kiss@r3) goes through the confirm page, which carries the rid; the stop is r3's copy.
    from lhpc.core.services import ActionResult
    svc = _marked(tmp_path)
    c = web(service_factory=lambda: svc)
    stops = []

    def stop(self, target, apply=False, cascade=False, band="", release_daemon=True, auto_install_ctx=None, *,
             _operator=True, inst=None):
        stops.append((target, apply, inst.rid if inst is not None else None))
        return ActionResult(True, f"Stopped '{target}'.")
    monkeypatch.setattr(ControllerService, "stop", stop)
    page = parse(c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss@r3", "from": "dash"})
                 .get_data(as_text=True))
    form = page.within(next(f for f in page.find("form", action="/action") if f.has_attr("data-busy-form")))
    assert (form.field_default("target"), form.field_default("rid")) == ("kiss@r3", None)   # W2: the instance alone
    c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss@r3", "confirmed": "yes", "from": "dash"})
    assert [s for s in stops if s[1]] == [("kiss", True, "r3")]                    # the apply: kiss@r3, only it


def test_a_target_on_a_radio_off_the_list_is_refused_and_nothing_runs(tmp_path, web, monkeypatch, csrf):
    # K4b: every radio of the band is an instance (kiss@r1 too); a radio the hardware list does not have is refused.
    svc = _marked(tmp_path)
    c = web(service_factory=lambda: svc)
    spawns = _spy_spawns(monkeypatch)
    r = c.post("/action", data={"_csrf": csrf(c), "op": "restart", "target": "kiss@r9", "from": "dash"},
               follow_redirects=True)
    assert spawns == [] and "Cannot restart kiss@r9: kiss@r9: r9 is not a radio of the hardware list" in r.get_data(as_text=True)



def test_an_empty_suffix_and_a_second_rid_are_bad_requests(tmp_path, web, monkeypatch, csrf):
    # G1-C54, W2: `kiss@` (no rid) names no radio and never falls to the band's first; a `rid` field beside a target is
    # a second selector, so `kiss@r3` with rid r1 never picks either radio. Both 400, nothing runs.
    svc = _marked(tmp_path)
    c = web(service_factory=lambda: svc)
    spawns = _spy_spawns(monkeypatch)
    assert c.post("/action", data={"_csrf": csrf(c), "op": "restart", "from": "dash", "target": "kiss@"}).status_code == 400
    assert c.post("/action", data={"_csrf": csrf(c), "op": "restart", "target": "kiss@r3", "rid": "r1"}).status_code == 400
    assert spawns == []


def test_the_start_child_starts_exactly_the_copy_its_radio_names(tmp_path, monkeypatch):
    # W325: the detached start of a copy (`_stack-start kiss --radio r3`, log web-start-kiss-r3.log) starts kiss as r3's
    # copy, not a bare kiss.
    import os

    from lhpc.adapters.cli.main import main
    from lhpc.core import jobresult, jobs, procident
    from lhpc.core.services import ActionResult
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    log, aid = "web-start-kiss-r3.log", "a" * 32
    assert jobresult.reserve(svc._paths, log, aid, "start", "kiss", "kiss", [])
    assert jobs.write_job_marker(svc._paths, log, os.getpid(), "kiss", "start",
                                 ident=procident.proc_identity(os.getpid()), attempt_id=aid)
    calls = []

    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
              _before_start_locked=None, inst=None, **k):
        calls.append((target, band, inst.rid if inst is not None else None))
        return _before_start_locked() or ActionResult(True, "started (stub)")
    monkeypatch.setattr(ControllerService, "start", start)
    assert main(["_stack-start", "kiss", "--web-result", log, "--attempt-id", aid, "--radio", "r3"]) == 0
    assert calls == [("kiss", "", "r3")]


def test_a_disabled_radios_copy_still_stops_but_never_restarts(tmp_path, web, monkeypatch, csrf):
    # G1-C7 B3 kept for the console: a copy on a disabled radio is still addressed by a stop, never started.
    from lhpc.core.services import ActionResult
    svc = _marked(tmp_path)
    radios_fixture.write(tmp_path, (ROWS[0], radios.Radio("r3", "b", "uputronics", "433", ce="ce1", enabled=False)))
    c = web(service_factory=lambda: svc)
    stops, spawns = [], _spy_spawns(monkeypatch)

    def stop(self, target, apply=False, cascade=False, band="", release_daemon=True, auto_install_ctx=None, *,
             _operator=True, inst=None):
        stops.append((target, apply, inst.rid if inst is not None else None))
        return ActionResult(True, f"Stopped '{target}'.")
    monkeypatch.setattr(ControllerService, "stop", stop)
    c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss@r3", "confirmed": "yes", "from": "dash"})
    r = c.post("/action", data={"_csrf": csrf(c), "op": "restart", "target": "kiss@r3", "from": "dash"},
               follow_redirects=True)
    assert [s for s in stops if s[1]] == [("kiss", True, "r3")] and spawns == []
    assert "Cannot restart kiss@r3: kiss@r3: r3 is disabled" in r.get_data(as_text=True)


def test_g1_b1_re_a_recorded_slot_0_on_a_disabled_radio_is_not_started_from_the_console(tmp_path, web, monkeypatch,
                                                                                       csrf):
    # G1-B1-RE: kiss@r3 runs by its verified record, r1 joined 433 and r3 is disabled.
    # The console's start and restart of kiss@r3 are refused naming the disabled radio; nothing is spawned.
    import dataclasses
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        radios_fixture.write(tmp_path, ROWS[1:])
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("kiss")
        assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), p.pid, band="433",
                                              extra={"rid": "r3"})
        radios_fixture.write(tmp_path, (ROWS[0], dataclasses.replace(ROWS[1], enabled=False)))
        c = web(service_factory=lambda: ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))
        spawns = _spy_spawns(monkeypatch)
        for op in ("start", "restart"):
            r = c.post("/action", data={"_csrf": csrf(c), "op": op, "target": "kiss@r3", "from": "dash"},
                       follow_redirects=True)
            assert f"Cannot {op} kiss@r3: kiss@r3: r3 is disabled" in r.get_data(as_text=True), op
        assert spawns == []
    finally:
        p.kill()
        p.wait(timeout=5)
