"""W285/W286: Home has one card per radio ("r1 · 868", the band a property of the radio), and the card carries that
radio's daemon: Start, Stop and Restart by rid through POST /action, the detached job's `--radio`, the confirm page's
rid. An unknown rid is refused; a rid on anything but the daemon's start, stop or restart is a bad request."""
from __future__ import annotations

import os

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import jobresult, jobs, procident
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService

_A = "a" * 32


def _box(tmp_path):
    """A built daemon and the hardware list r1 (433), r2 (868)."""
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\n")
    radios_fixture.write(tmp_path)


def _spy_spawns(monkeypatch):
    spawns = []

    def spy(self, op, target, band="", stop_owners=False, cascade=False, rid=""):
        spawns.append((op, target, band, rid))
        return f"web-{op}-{target}-{rid}.log", "admitted", ""
    monkeypatch.setattr(ControllerService, "spawn_start_job", spy)
    return spawns


def _spy_stops(monkeypatch):
    stops = []

    def stop(self, target, apply=False, cascade=False, band="", release_daemon=True, auto_install_ctx=None, *,
             _operator=True, inst=None):
        stops.append((target, apply, band, inst.rid if inst is not None else None))
        return ActionResult(True, f"Stopped '{target}'.")
    monkeypatch.setattr(ControllerService, "stop", stop)
    return stops


def test_a_radio_cards_start_and_restart_run_that_radios_daemon(tmp_path, monkeypatch, web, csrf):
    _box(tmp_path)
    c = web()
    spawns, plans = _spy_spawns(monkeypatch), []
    for op in ("start", "restart"):                  # the plan before the job is made for the radio too
        real = getattr(ControllerService, op)

        def plan(self, target, *a, _real=real, _op=op, inst=None, **k):
            plans.append((_op, k.get("apply"), inst.rid if inst is not None else None))
            return _real(self, target, *a, inst=inst, **k)
        monkeypatch.setattr(ControllerService, op, plan)
    for op in ("start", "restart"):
        r = c.post("/action", data={"_csrf": csrf(c), "op": op, "target": "daemon", "rid": "r2", "from": "dash"})
        assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert spawns == [("start", "daemon", "868", "r2"), ("restart", "daemon", "868", "r2")]   # r2's band, by rid
    assert plans == [("start", False, "r2"), ("restart", False, "r2")]


def test_a_radio_cards_stop_confirms_and_stops_that_radios_daemon(tmp_path, monkeypatch, web, csrf):
    _box(tmp_path)
    c = web()
    stops = _spy_stops(monkeypatch)
    r = c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "daemon", "rid": "r1", "from": "dash"})
    doc = parse(r.get_data(as_text=True))
    form = doc.within(next(f for f in doc.find("form", action="/action") if f.has_attr("data-busy-form")))
    assert form.field_default("rid") == "r1" and form.field_default("band") == "433"   # the confirm carries the rid
    c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "daemon", "rid": "r1", "band": "433",
                            "confirmed": "yes", "from": "dash"})
    assert stops == [("daemon", False, "433", "r1"), ("daemon", True, "433", "r1")]    # the plan and the stop: r1


def test_an_unknown_or_disabled_rid_is_refused_and_nothing_runs(tmp_path, monkeypatch, web, csrf):
    _box(tmp_path)
    c = web()
    spawns, stops = _spy_spawns(monkeypatch), _spy_stops(monkeypatch)
    for op in ("start", "stop"):
        r = c.post("/action", data={"_csrf": csrf(c), "op": op, "target": "daemon", "rid": "r9", "from": "dash"},
                   follow_redirects=True)
        assert f"Cannot {op} the daemon: r9 is not a radio of the hardware list." in r.get_data(as_text=True)
    assert spawns == [] and stops == []


def test_a_rid_on_a_stack_with_no_such_instance_is_refused_and_on_another_operation_is_a_bad_request(tmp_path, monkeypatch,
                                                                                                    web, csrf):
    # W325: a rid on a stack names its instance (K4b: one on every radio of its bands); a radio of another band or of
    # no list row has none, so nothing runs and the refusal says why.
    _box(tmp_path)
    c = web()
    spawns, stops = _spy_spawns(monkeypatch), _spy_stops(monkeypatch)
    r = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "meshcom", "rid": "r2"}, follow_redirects=True)
    assert "Cannot start meshcom@r2: meshcom@r2: meshcom runs on 433 MHz, r2 is on 868 MHz." in r.get_data(as_text=True)
    r = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "kiss", "rid": "r9"}, follow_redirects=True)
    assert "Cannot start kiss@r9: kiss@r9: r9 is not in the hardware list." in r.get_data(as_text=True)
    assert c.post("/action", data={"_csrf": csrf(c), "op": "install", "target": "daemon", "rid": "r1"}).status_code == 400
    assert spawns == [] and stops == []


def test_home_has_one_card_per_enabled_radio_with_its_daemon_controls(tmp_path, monkeypatch, web):
    _box(tmp_path)
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)   # the start control's case
    doc = parse(web().get("/").get_data(as_text=True))
    cards = doc.find("div", class_="radio-col")
    assert [(c["data-radio-rid"], c["data-radio-band"]) for c in cards] == [("r1", "433"), ("r2", "868")]
    for card, rid in zip(cards, ("r1", "r2"), strict=True):
        forms = [doc.within(f) for f in doc.within(card).find("form", action="/action")]
        start = next(f for f in forms if f.field_default("op") == "start" and f.field_default("target") == "daemon")
        assert start.field_default("rid") == rid and start.field_default("band") is None   # the radio, not its band
        assert doc.within(card).by_id(f"rd-badge-{rid}")


def test_a_running_start_of_a_radios_daemon_makes_home_check_every_2_s(tmp_path, web):
    _box(tmp_path)
    c = web()
    grid = lambda: parse(c.get("/").get_data(as_text=True)).find("div", class_="radiogrid")[0]   # noqa: E731
    assert grid()["data-pending-interactive"] == "0"
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    log = "web-start-daemon-r2.log"
    assert jobresult.reserve(svc._paths, log, _A, "start", "daemon", "daemon", [])
    assert jobs.write_job_marker(svc._paths, log, os.getpid(), "daemon", "start",
                                 ident=procident.proc_identity(os.getpid()), attempt_id=_A)
    assert grid()["data-pending-interactive"] == "1"


def test_the_detached_start_names_its_log_by_the_radio_and_passes_it(tmp_path, monkeypatch, web, csrf):
    from lhpc.core.lifecycle import Lifecycle
    _box(tmp_path)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    argvs = []
    monkeypatch.setattr(Lifecycle, "spawn_job",
                        lambda self, name, argv, cwd, env=None: argvs.append(argv) or (f"{name}.log", os.getpid()))
    monkeypatch.setattr(ControllerService, "_track_or_terminate", lambda self, *a, **k: (jobs.TrackOutcome.TRACKED, ""))
    monkeypatch.setattr(ControllerService, "_web_admit_handshake", lambda self, log, aid: ("admitted", ""))
    c = web()
    for rid in ("r1", "r2"):                                     # two radios' starts run side by side
        c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "daemon", "rid": rid, "from": "dash"})
    assert [(a[a.index("--web-result") + 1], a[a.index("--radio") + 1] if "--radio" in a else None,
             "--band" in a) for a in argvs] == [
        ("web-start-daemon-r1.log", "r1", False), ("web-start-daemon-r2.log", "r2", False)]   # K8: the radio, no band


def _runner(tmp_path, monkeypatch, log):
    """The `_stack-start` child's world: a tracked attempt `log` and a start stub recording the radio it got."""
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert jobresult.reserve(svc._paths, log, _A, "start", "daemon", "daemon", [])
    assert jobs.write_job_marker(svc._paths, log, os.getpid(), "daemon", "start",
                                 ident=procident.proc_identity(os.getpid()), attempt_id=_A)
    calls = []

    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
              _before_start_locked=None, inst=None, **k):
        calls.append((target, band, inst.rid if inst is not None else None))
        return _before_start_locked() or ActionResult(True, "started (stub)")
    monkeypatch.setattr(ControllerService, "start", start)
    return svc, calls


def test_the_start_child_starts_the_radios_daemon_and_refuses_another_name(tmp_path, monkeypatch):
    from lhpc.adapters.cli.main import main
    _box(tmp_path)
    svc, calls = _runner(tmp_path, monkeypatch, "web-start-daemon-r1.log")
    assert main(["_stack-start", "daemon", "--web-result", "web-start-daemon.log", "--attempt-id", _A,
                 "--radio", "r1"]) == 3                                   # the log is bound to the radio too
    assert calls == []
    assert main(["_stack-start", "daemon", "--web-result", "web-start-daemon-r1.log", "--attempt-id", _A,
                 "--radio", "r1"]) == 0
    assert calls == [("daemon", "", "r1")]
    assert jobresult.read_one(svc._paths, "web-start-daemon-r1.log")["state"] == "done"


def test_the_start_child_refuses_a_radio_not_on_the_list(tmp_path, monkeypatch):
    from lhpc.adapters.cli.main import main
    _box(tmp_path)
    svc, calls = _runner(tmp_path, monkeypatch, "web-start-daemon-r9.log")
    assert main(["_stack-start", "daemon", "--web-result", "web-start-daemon-r9.log", "--attempt-id", _A,
                 "--radio", "r9"]) == 1
    rec = jobresult.read_one(svc._paths, "web-start-daemon-r9.log")
    assert calls == [] and rec["state"] == "failed" and rec["detail"] == "r9 is not a radio of the hardware list"


_READY = b"STATUS ID=r2 BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1\n"


def _daemon_forms(doc, card):
    return {(f.field_default("op"), f.field_default("rid"), f.field_default("band"))
            for f in (doc.within(e) for e in doc.within(card).find("form", action="/action"))
            if f.field_default("target") == "daemon"}


def test_a_running_daemon_keeps_its_card_while_its_stick_is_unplugged(tmp_path, monkeypatch, web):
    # W292: "while daemon is running, card is present (even with no hardware; might be a usb hardware that's replugged
    # later)". r2 is a Meshstick whose stick is not plugged in (no USB device at all); its daemon still answers.
    from lhpc.core import radios
    _box(tmp_path)
    radios_fixture.write(tmp_path, (radios_fixture.RADIOS[0], radios.Radio("r2", "stick", "meshstick-1262", "868",
                                                                          usb_serial="MSTICK01")))
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    fs = FakeSystem(unix_replies={"/tmp/loraconf-r2.sock": _READY})
    doc = parse(web(system=fs.system).get("/").get_data(as_text=True))
    card = doc.find("div", class_="radio-col", **{"data-radio-rid": "r2"})[0]
    assert doc.within(card).by_id("rd-badge-r2").text.strip() == "daemon usable (READY)"
    assert _daemon_forms(doc, card) == {("stop", "r2", None), ("restart", "r2", None)}     # its own, by rid


def test_add_hardware_sets_up_a_fresh_box_with_a_dual_board_in_one_step(tmp_path, web, csrf):
    # W292: the board setup lives on Add Hardware (once the daemon's Settings form). On a box with no hardware list
    # (the HAT row refuses there) it writes the list: both radios of a dual board in one step.
    from lhpc.core import radios
    c = web()
    doc = parse(c.get("/hardware/add").get_data(as_text=True))
    form = doc.within(doc.by_id("hw-setup"))
    assert form.field_default("hardware") is not None and "uputronics" in [o["value"] for o in form.find("option")]
    r = c.post("/hardware", data={"_csrf": csrf(c, "/hardware/add"), "hardware": "uputronics"})
    assert r.headers["Location"].endswith("/hardware/add#hw-setup")
    assert [(x.id, x.band, x.ce) for x in radios.load(Paths(runtime_root=tmp_path)).radios] == [
        ("r1", "433", "ce0"), ("r2", "868", "ce1")]


def test_add_hardware_serves_a_band_a_hat_was_added_on(tmp_path, web, csrf):
    # S2 (was the W292 gap): the list decides, so a HAT added on 868 to a 433-only box is served at once.
    svc = lambda: ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))   # noqa: E731
    svc().set_hardware_setup("uputronics-433")
    assert svc().radio_bands() == ("433",)
    c = web()
    c.post("/hardware/add-hat", data={"_csrf": csrf(c, "/hardware/add"), "board": "uputronics:ce1", "band": "868"})
    assert svc().radio_bands() == ("433", "868")


def test_a_cards_daemon_log_and_occupied_badge_are_its_radios(tmp_path, monkeypatch, web):
    # G1-C28 N3: the card's Daemon log opens its radio's own start log; REMOVALS row 11: an occupied badge names the
    # radio's state, as the band card did.
    _box(tmp_path)
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    fs = FakeSystem(unix_replies={"/tmp/loraconf-r2.sock": _READY.replace(b"RADIO=READY", b"RADIO=FAILED")})
    c = web(system=fs.system)
    doc = parse(c.get("/").get_data(as_text=True))
    card = doc.find("div", class_="radio-col", **{"data-radio-rid": "r2"})[0]
    assert doc.within(card).by_id("rd-badge-r2").text.strip() == "daemon occupied (FAILED)"
    href = next(a["href"] for a in doc.within(card).find("a") if a.text.strip() == "Daemon log")
    assert href == "/logs/loraham-daemon?job=start-loraham-daemon-868-r2.log"
    assert c.get(href).status_code == 200


def test_the_hardware_list_shows_each_radios_daemon_badge_as_home_does(tmp_path, web):
    # W301: the daemon status back on each Hardware-list row, rendered by Home's own badge macro.
    _box(tmp_path)
    fs = FakeSystem(unix_replies={"/tmp/loraconf-r2.sock": _READY.replace(b"RADIO=READY", b"RADIO=FAILED")})
    c = web(system=fs.system)
    doc = parse(c.get("/hardware").get_data(as_text=True))
    rows = {r["data-rid"]: r for r in doc.find("tr", class_="hwrow")}
    badge = {rid: doc.within(row).find("span", class_="badge")[0] for rid, row in rows.items()}
    assert {rid: (b.text.strip(), b.classes) for rid, b in badge.items()} == {
        "r1": ("daemon offline", {"badge", "badge-stopped"}), "r2": ("daemon occupied (FAILED)", {"badge", "badge-degraded"})}
    home = parse(c.get("/").get_data(as_text=True))
    assert home.by_id("rd-badge-r2").text.strip() == badge["r2"].text.strip()             # the same rendering


@pytest.mark.no_default_hardware                  # the list is really absent (conftest's baseline list off)
def test_a_band_card_without_a_radio_list_shows_its_daemons_badge(tmp_path, monkeypatch, web):
    # G1-C34 (handler DEC): Home's band card (no hardware list, so no radio card) renders the same badge from the band's
    # daemon view: a READY daemon is usable, a reachable one whose radio failed is occupied with that state.
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    for state, label, cls in ((b"READY", "daemon usable (READY)", "badge-running"),
                              (b"FAILED", "daemon occupied (FAILED)", "badge-degraded")):
        fs = FakeSystem(unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=" + state + b" TX=0 TXMODE=MANAGED\n"})
        badge = parse(web(system=fs.system).get("/").get_data(as_text=True)).by_id("rd-badge-433")
        assert (badge.text.strip(), cls in badge.classes) == (label, True)
