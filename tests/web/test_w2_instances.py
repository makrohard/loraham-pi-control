"""W2 (WEB-LANE-PLAN-v5, CLEANUP-DESIGN-v13 13.2/13.3): every console start, stop and restart names its instance,
`<stack>@<rid>`, through instances.parse_target, the one parser; a bare stack flashes the parser's refusal and
launches nothing; a `band` field is obsolete (400). Through POST /action on the default list (r1 433, r2 868)."""
from __future__ import annotations

import pytest
import radios_fixture

from lhpc.core.services import ActionResult, ControllerService


def _box(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path)
    monkeypatch.setattr(ControllerService, "is_installed", lambda self, t: True)
    monkeypatch.setattr(ControllerService, "unbuilt_components", lambda self, t: [])


def _spy(monkeypatch):
    seen = {"spawns": [], "stops": []}

    def spawn(self, op, target, band="", stop_owners=False, cascade=False, rid="", plan_hash=None):
        seen["spawns"].append((op, target, band, rid))
        return f"web-{op}-{target}-{rid}.log", "admitted", ""

    def stop(self, target, apply=False, cascade=False, band="", release_daemon=True, auto_install_ctx=None, *,
             _operator=True, inst=None):
        seen["stops"].append((target, apply, inst.rid if inst is not None else None))
        return ActionResult(True, f"Stopped '{target}'.")
    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn)
    monkeypatch.setattr(ControllerService, "stop", stop)
    return seen


def _post(c, csrf, **form):
    return c.post("/action", data={"_csrf": csrf(c), **form}, follow_redirects=True)


@pytest.mark.parametrize("op", ["start", "stop", "restart"])
def test_a_bare_stack_flashes_the_parsers_refusal_and_runs_nothing(tmp_path, monkeypatch, web, csrf, op):
    _box(tmp_path, monkeypatch)
    seen, c = _spy(monkeypatch), web()
    page = _post(c, csrf, op=op, target="kiss").get_data(as_text=True)
    assert "kiss can run on r1 or r2: use kiss@r1 or kiss@r2" in page or "kiss@" in page, page[-2000:]
    assert seen == {"spawns": [], "stops": []}


@pytest.mark.parametrize("form", [{"target": "kiss@r1", "band": "433"}, {"target": "kiss", "band": "433"},
                                  {"target": "kiss", "band": "433", "rid": "r1"}, {"target": "kiss@r1", "band": ""}])
def test_a_band_field_is_obsolete_and_a_bad_request(tmp_path, monkeypatch, web, csrf, form):
    _box(tmp_path, monkeypatch)
    seen, c = _spy(monkeypatch), web()
    assert c.post("/action", data={"_csrf": csrf(c), "op": "start", **form}).status_code == 400
    assert seen == {"spawns": [], "stops": []}


def test_an_instance_target_starts_that_instance_only(tmp_path, monkeypatch, web, csrf):
    _box(tmp_path, monkeypatch)
    seen, c = _spy(monkeypatch), web()
    assert ControllerService.set_operator_identity  # the plan needs no callsign for kiss
    _post(c, csrf, op="start", target="kiss@r2")
    assert seen["spawns"] == [("start", "kiss", "868", "r2")], seen


# W2, the confirm's freshness: the confirm page carries the core's plan hash; the detached `_stack-start` recomputes it
# under every lock and runs only the plan it confirmed: another plan is refused as "plan changed", nothing launched.


def _two_433(tmp_path):
    import dataclasses

    from lhpc.core import radios
    rows = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
    radios_fixture.write(tmp_path, rows)
    return rows, dataclasses.replace


def _svc(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _confirmed_hash(tmp_path, target, rid):
    from lhpc.core import instances
    svc = _svc(tmp_path)
    return instances.plan_hash(svc, "start", target, instances.copy_of(svc, target, rid)[0], "433")


def _run_child(tmp_path, monkeypatch, target, rid, plan_hash):
    """The detached start as the console spawns it (`_stack-start <target> --radio <rid> --plan-hash <h>`); the start
    is the real planner with its launch stubbed: returns (exit code, launched (target, rid) list, the attempt's detail)."""
    import os

    from lhpc.adapters.cli.main import main
    from lhpc.core import jobresult, jobs, procident
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = _svc(tmp_path)
    log, aid = f"web-start-{target}-{rid}.log", "b" * 32
    assert jobresult.reserve(svc._paths, log, aid, "start", target, target, [])
    assert jobs.write_job_marker(svc._paths, log, os.getpid(), target, "start",
                                 ident=procident.proc_identity(os.getpid()), attempt_id=aid)
    real, launched = ControllerService.start, []

    def start(self, t, apply=False, stop_owners=False, band="", auto_install_ctx=None, *, _before_start_locked=None,
              inst=None, **k):
        if not apply:
            return real(self, t, apply=False, stop_owners=stop_owners, band=band, inst=inst, **k)
        refused = _before_start_locked()
        if refused is None:
            launched.append((t, inst.rid))
        return refused or ActionResult(True, "started (stub)")
    monkeypatch.setattr(ControllerService, "start", start)
    rc = main(["_stack-start", target, "--web-result", log, "--attempt-id", aid, "--radio", rid,
               "--plan-hash", plan_hash])
    return rc, launched, (jobresult.read_one(svc._paths, log) or {}).get("detail", "")


def test_the_confirmed_plan_unchanged_runs(tmp_path, monkeypatch):
    _two_433(tmp_path)
    rc, launched, _detail = _run_child(tmp_path, monkeypatch, "kiss", "r1", _confirmed_hash(tmp_path, "kiss", "r1"))
    assert (rc, launched) == (0, [("kiss", "r1")])


def _changed(rc, launched, detail):
    return rc != 0 and launched == [] and "the plan changed since it was confirmed" in detail


def test_a_flipped_radio_grant_is_plan_changed_and_launches_nothing(tmp_path, monkeypatch):
    rows, replace = _two_433(tmp_path)
    h = _confirmed_hash(tmp_path, "kiss", "r1")
    radios_fixture.write(tmp_path, (replace(rows[0], high_power=True, high_power_band="433"), rows[1]))
    assert _changed(*_run_child(tmp_path, monkeypatch, "kiss", "r1", h))


def test_a_swapped_target_with_equal_inputs_is_plan_changed_and_launches_nothing(tmp_path, monkeypatch):
    _two_433(tmp_path)
    h = _confirmed_hash(tmp_path, "kiss", "r1")                                  # confirmed kiss@r1, runs kiss@r3
    assert _changed(*_run_child(tmp_path, monkeypatch, "kiss", "r3", h))


def test_an_edited_saved_config_is_plan_changed_and_launches_nothing(tmp_path, monkeypatch):
    from lhpc.core import instances
    _two_433(tmp_path)
    h = _confirmed_hash(tmp_path, "kiss", "r1")
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=instances.copy_of(svc, "kiss", "r1")[0]).ok
    assert _changed(*_run_child(tmp_path, monkeypatch, "kiss", "r1", h))


def test_a_confirmed_post_without_a_hash_is_plan_changed_and_launches_nothing(tmp_path, monkeypatch):
    _two_433(tmp_path)
    assert _changed(*_run_child(tmp_path, monkeypatch, "kiss", "r1", ""))


def test_the_confirm_page_carries_the_plan_hash_and_the_confirmed_post_hands_it_on(tmp_path, monkeypatch, web, csrf):
    # meshtastic holds 868, so daemon@r2's start asks "Stop owner(s) & start" (the one start confirm page).
    from htmlq import parse
    _box(tmp_path, monkeypatch)
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\n")
    c, hashes = web(cmdlines={200: ["meshtasticd"]}), []

    def spawn(self, op, target, band="", stop_owners=False, cascade=False, rid="", plan_hash=None):
        hashes.append(plan_hash)
        return f"web-{op}-{target}-{rid}.log", "admitted", ""
    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn)
    tok = csrf(c)
    page = parse(c.post("/action", data={"_csrf": tok, "op": "start", "target": "daemon@r2"}).get_data(as_text=True))
    form = page.within(next(f for f in page.find("form", action="/action") if f.has_attr("data-busy-form")))
    shown = form.field_default("plan_hash")
    assert shown and len(shown) == 64
    c.post("/action", data={"_csrf": tok, "op": "start", "target": "daemon@r2", "confirmed": "yes",
                            "stop_owners": "yes", "plan_hash": shown})
    c.post("/action", data={"_csrf": tok, "op": "start", "target": "daemon@r2"})          # unconfirmed: no hash
    assert hashes == [shown]


def test_a_radio_that_moved_band_under_the_form_launches_nothing(tmp_path, monkeypatch):
    rows, replace = _two_433(tmp_path)
    h = _confirmed_hash(tmp_path, "kiss", "r1")                                  # confirmed on 433
    radios_fixture.write(tmp_path, (replace(rows[0], band="868", ce=""), rows[1]))
    rc, launched, detail = _run_child(tmp_path, monkeypatch, "kiss", "r1", h)
    assert rc != 0 and launched == [] and detail, detail


def test_every_home_form_posts_its_instance(tmp_path, monkeypatch, web):
    # W2 Home (selector kept, DEC-W384z): every start, stop and restart form on Home posts `<stack>@<rid>` of a radio
    # on the list, the "Start a stack on rN" selector's options included; none carries a band or a rid field.
    import re

    from htmlq import parse
    _box(tmp_path, monkeypatch)
    c = web(cmdlines={100: ["loraham_daemon", "--radio", "433", "--id", "r1"], 200: ["loraham-kiss-tnc"]})
    doc = parse(c.get("/").get_data(as_text=True))
    targets = []
    for f in doc.find("form", action="/action"):
        w = doc.within(f)
        if w.field_default("op") not in ("start", "stop", "restart"):
            continue
        names = {e["name"] for e in w.find("input") if e.has_attr("name")} | {e["name"] for e in w.find("select")}
        assert not names & {"band", "rid"}, names
        options = [o["value"] for o in w.find("option")] if w.find("select", name="target") else []
        targets += options or [w.field_default("target")]
    assert {"daemon@r1", "daemon@r2", "kiss@r1", "chat@r1", "voice@r2"} <= set(targets), targets
    assert all(re.fullmatch(r"[\w-]+@r[12]", t) for t in targets), targets
    heads = [h.text.strip() for h in doc.find("h3")]
    assert {"Running on r1", "Start a stack on r1", "Start a stack on r2"} <= set(heads), heads   # a radio, not a band


def test_the_stacks_page_of_a_radio_shows_that_instance(tmp_path, monkeypatch, web):
    # W2: `?radio=r2` is the one selector: kiss's settings and its optional components are kiss@r2's, on r2's 868.
    from htmlq import parse
    _box(tmp_path, monkeypatch)
    c = web()
    doc = parse(c.get("/stacks?cfg=kiss&radio=r2").get_data(as_text=True))
    note = next(p for p in doc.find("p", class_="instance-note"))
    assert " ".join(note.text.split()) == ("kiss@r2 starts from the defaults: each radio's settings are its own, and a "
                                           "change applies at its next start.")
    forms = [doc.within(f) for f in doc.find("form", action="/action")]
    fixed = {(f.field_default("op"), f.field_default("target")) for f in forms if f.field_default("op") in ("start", "stop")
             and "serial" in f.field_default("target") and not f.find("select")}      # the settings' own Run/Stop
    assert fixed == {("start", "loraham-kiss-serial@r2"), ("stop", "loraham-kiss-serial@r2")}
    assert doc.find("input", name="c_rx_freq")[0]["value"] == "869.525"


def test_the_dash_signature_changes_on_an_instances_start_and_stop(tmp_path, web):
    # W2 (dash.js reloads Home on a new signature): kiss@r1 runs; kiss@r3's start and its stop each change it.
    import subprocess
    import sys
    rows, _replace = _two_433(tmp_path)
    procs = {}

    def launch(rid):
        procs[rid] = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
        svc = _svc(tmp_path)
        stk = svc.stack("kiss")
        assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), procs[rid].pid, band="433",
                                              extra={"rid": rid})
    c = web(service_factory=lambda: _svc(tmp_path))
    sig = lambda: c.get("/api/dash-signature").get_json()["sig"]  # noqa: E731
    try:
        launch("r1")
        one = sig()
        launch("r3")
        two = sig()
        procs.pop("r3").kill()
        assert one != two and sig() != two, (one, two)
    finally:
        for p in procs.values():
            p.kill()


def test_an_owner_that_took_the_radio_under_the_form_is_plan_changed(tmp_path, monkeypatch):
    # Only the plan moves (its row and its config stay): meshtastic started on 433 after kiss@r1's confirm, so the
    # start's plan now has a blocker the operator never confirmed.
    import subprocess
    import sys
    _two_433(tmp_path)
    h = _confirmed_hash(tmp_path, "kiss", "r1")
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        svc = _svc(tmp_path)
        stk = svc.stack("meshtastic")
        assert svc._lifecycle().record_launch(stk, stk.main_component, p.pid, band="433", extra={"rid": "r1"})
        assert _changed(*_run_child(tmp_path, monkeypatch, "kiss", "r1", h))
    finally:
        p.kill()
        p.wait(timeout=5)


def test_the_confirmed_hash_reaches_the_jobs_argv(tmp_path, monkeypatch, web, csrf):
    # The real spawn_start_job, recorded at its seam (Lifecycle.spawn_job): a confirmed restart hands the form's hash
    # to `_stack-start` as --plan-hash; an unconfirmed one hands none.
    from lhpc.core.lifecycle import Lifecycle
    _box(tmp_path, monkeypatch)
    argvs = []

    def spawn_job(self, name, argv, cwd, env=None):
        argvs.append(argv)
        return f"{name}.log", 0                         # "could not start": nothing runs
    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
    c = web()
    c.post("/action", data={"_csrf": csrf(c), "op": "restart", "target": "kiss@r1", "confirmed": "yes",
                            "plan_hash": "f" * 64})
    c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "kiss@r2"})
    assert [a[a.index("--plan-hash") + 1] if "--plan-hash" in a else None for a in argvs] == ["f" * 64, None], argvs


# ---- G1-W2 dd81a2de fixes ----


def test_g1_w2_1_the_confirm_hashes_the_plan_it_shows(tmp_path, monkeypatch, web, csrf):
    # A second planner call that would answer differently (an owner changed between the two) never decides the hash:
    # the page's hash is the hash of the plan the page shows.
    from htmlq import parse

    from lhpc.core import instances
    _box(tmp_path, monkeypatch)
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\n")
    real, plans = ControllerService.run_action, []

    def run_action(self, op, target, apply=False, **k):
        res = real(self, op, target, apply=apply, **k)
        if not apply:
            if plans:
                res.data = {**res.data, "blockers": []}                  # the box moved: no owner any more
            plans.append((self, res))
        return res
    monkeypatch.setattr(ControllerService, "run_action", run_action)
    c = web(cmdlines={200: ["meshtasticd"]})
    page = parse(c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "daemon@r2"}).get_data(as_text=True))
    form = page.within(next(f for f in page.find("form", action="/action") if f.has_attr("data-busy-form")))
    svc, shown = plans[0]
    assert shown.data["blockers"]                                           # the page shows the owner
    inst = instances.parse_target(svc, "daemon@r2", "start")[1]
    assert form.field_default("plan_hash") == instances.plan_hash(svc, "start", "daemon", inst, "868", shown)


def _two_433_web(tmp_path, web, cmdlines=None):
    _two_433(tmp_path)
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    return web(service_factory=lambda: ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                                                         paths=Paths(runtime_root=tmp_path)))


@pytest.mark.parametrize("rid", ["r1", "r3"])
def test_g1_w2_2_the_pages_radio_is_picked_in_every_instance_selector(tmp_path, web, rid):
    # Two radios on 433: ?radio=r3 is not ?radio=r1. Every instance selector of the page picks that radio, and the lazy
    # bodies are fetched for it.
    from htmlq import parse
    c = _two_433_web(tmp_path, web)
    doc = parse(c.get(f"/stacks?radio={rid}&open=kiss").get_data(as_text=True))
    selects = [doc.within(s) for s in doc.find("select", name="target")]
    assert selects
    for s in selects:
        picked = [o["value"] for o in s.find("option") if o.has_attr("selected")]
        assert picked and all(v.endswith(f"@{rid}") for v in picked), picked
    urls = [e["data-body-url"] for e in doc.find("div", class_="lazy-body")]
    assert urls and all(f"radio={rid}" in u for u in urls)


def test_g1_w2_2_w1s_daemon_panel_link_opens_the_same_radio(tmp_path, web):
    # W290: the W1 link `/stacks?dp=daemon&rid=r2` keeps working: it opens `?radio=r2`, the one selector.
    from urllib.parse import parse_qs, urlsplit
    _two_433(tmp_path)
    c = web()
    r = c.get("/stacks?dp=daemon&rid=r3&open=daemon")
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["Location"]).query)
    assert q == {"dp": ["daemon"], "open": ["daemon"], "radio": ["r3"]}
    assert c.get(r.headers["Location"]).status_code == 200


def _card_forms(doc, card_id):
    card = doc.within(doc.by_id(card_id))
    return sorted((card.within(f).field_default("op"), card.within(f).field_default("target"))
                  for f in card.find("form", action="/action") if card.within(f).field_default("op") in ("stop", "restart"))


def test_g1_w2_3_an_entry_without_an_instance_keeps_its_stop_and_loses_run(tmp_path, web, csrf):
    # W370 (G1-W2-RE, W290): a kiss process no launch record and no argv names a radio for keeps its state and its Stop
    # on each card of its band (posting that card's instance), never a Run; the Stop is not dead: lhpc owns no such
    # process, so the stop is refused and Kill is offered.
    from htmlq import parse
    c = _two_433_web(tmp_path, web, {200: ["/x/loraham-kiss-tnc"]})
    doc = parse(c.get("/").get_data(as_text=True))
    for card in ("radio-r1", "radio-r3"):
        assert [f for f in _card_forms(doc, card) if f[1].partition("@")[0] == "kiss"] == [("stop", f"kiss@{card[6:]}")]
        assert doc.present(f"noinst-{card[6:]}-kiss"), card
    page = c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss@r3", "confirmed": "yes"})
    body = page.get_data(as_text=True)
    assert page.status_code == 200 and 'id="stop-failed"' in body and 'id="kill-form"' in body


def test_g1_w2_3_an_instance_shows_on_its_own_radios_card_only(tmp_path, web):
    import subprocess
    import sys

    from htmlq import parse
    _two_433(tmp_path)
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        svc = _svc(tmp_path)
        stk = svc.stack("kiss")
        assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), p.pid, band="433", extra={"rid": "r1"})
        doc = parse(web(service_factory=lambda: _svc(tmp_path)).get("/").get_data(as_text=True))
        assert [f for f in _card_forms(doc, "radio-r1") if f[1].partition("@")[0] == "kiss"] == [("restart", "kiss@r1"),
                                                                                       ("stop", "kiss@r1")]
        assert [f for f in _card_forms(doc, "radio-r3") if f[1].partition("@")[0] == "kiss"] == []
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_w2_3_an_interactive_app_shows_on_its_instances_card(tmp_path, web):
    from htmlq import parse

    from lhpc.core import instances
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    _two_433(tmp_path)
    ready = {f"/tmp/loraconf-{r}.sock": f"STATUS ID={r} RADIO=READY TX=0 TXMODE=MANAGED\n".encode() for r in ("r1", "r3")}

    def svc():          # chat runs (two radios on a band have no band daemon view, so a marker alone shows nothing)
        return ControllerService(system=FakeSystem(unix_replies=ready, cmdlines_data={300: ["loraham_chat"]}).system,
                                 paths=Paths(runtime_root=tmp_path))
    assert svc().mark_shown("chat", instances.copy_of(svc(), "chat", "r3")[0], "433")
    doc = parse(web(service_factory=svc).get("/").get_data(as_text=True))
    heads = {card: [h.text.strip() for h in doc.within(doc.by_id(card)).find("h3")] for card in ("radio-r1", "radio-r3")}
    assert "Interactive apps" in heads["radio-r3"] and "Interactive apps" not in heads["radio-r1"], heads
    stops = {card: [f[1] for f in _card_forms(doc, card) if f[1].partition("@")[0] == "chat"] for card in ("radio-r1", "radio-r3")}
    assert stops == {"radio-r1": [], "radio-r3": ["chat@r3"]}, stops


@pytest.mark.parametrize("band, stray, code", [("433", False, 400), ("868", False, 400), ("868", True, 302)])
def test_g1_w2_4_a_band_stop_is_only_a_stray_daemons(tmp_path, monkeypatch, web, csrf, band, stray, code):
    # The §6 exception holds only where the stray card shows: a band the hardware list has no enabled radio on (two
    # 433 radios here) AND a daemon running there (the card's own rule, radio_overview's stray entry).
    _two_433(tmp_path)
    if stray:
        real = ControllerService.radio_overview
        monkeypatch.setattr(ControllerService, "radio_overview",
                            lambda self: [*real(self), {"band": "868", "stray": True, "radios": [], "running": []}])
    seen, c = _spy(monkeypatch), web()
    r = c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "daemon", "band": band, "confirmed": "yes"})
    assert r.status_code == code
    assert [s[:2] for s in seen["stops"] if s[1]] == ([("daemon", True)] if code == 302 else [])


def test_g1_w2_5_a_running_instance_whose_radio_moved_band_still_stops(tmp_path, monkeypatch, web, csrf):
    # The parser's "stop it first" case: kiss@r3 runs as launched on 433, r3's row says 868 now. Its stop reaches it
    # (the waiver), its start is refused with that why.
    import dataclasses
    import subprocess
    import sys
    rows, _replace = _two_433(tmp_path)
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        svc = _svc(tmp_path)
        stk = svc.stack("kiss")
        assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), p.pid, band="433", extra={"rid": "r3"})
        radios_fixture.write(tmp_path, (rows[0], dataclasses.replace(rows[1], model="meshstick-1262", band="868", ce="",
                                                                    usb_serial="MS654321")))
        seen, c = _spy(monkeypatch), web(service_factory=lambda: _svc(tmp_path))
        c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss@r3", "confirmed": "yes"})
        page = _post(c, csrf, op="start", target="kiss@r3").get_data(as_text=True)
        assert [s for s in seen["stops"] if s[1]] == [("kiss", True, "r3")] and seen["spawns"] == []
        assert "stop it first" in page
    finally:
        p.kill()
        p.wait(timeout=5)


@pytest.mark.parametrize("target", ["kiss@r9", "kiss", "nosuch@r1"])
def test_g1_w2_5_a_stop_waives_no_refusal_but_the_reachable_ones(tmp_path, monkeypatch, web, csrf, target):
    # Only a disabled radio's or an off-list running instance comes back from the parser with an instance (and still
    # stops, test_copy_restart_marker_web); a radio off the list with nothing running, a bare stack, an unknown stack:
    # refused, nothing stopped.
    _box(tmp_path, monkeypatch)
    seen, c = _spy(monkeypatch), web()
    page = _post(c, csrf, op="stop", target=target, confirmed="yes").get_data(as_text=True)
    assert f"Cannot stop {target}:" in page and seen["stops"] == []


def test_g1_w2_6_a_card_without_a_radio_offers_no_start_that_names_none(tmp_path, monkeypatch, web):
    # A band card with no radio (the template's `x` absent; the core builds none today: no list gives no card, a stray
    # card is one per live radio): no start control, which could name no radio; the card names the Hardware page.
    from htmlq import parse
    real = ControllerService.radio_overview
    monkeypatch.setattr(ControllerService, "radio_overview", lambda self: [
        dict(r, radios=[], daemon=dict(r["daemon"], installed=True)) for r in real(self)])
    doc = parse(_two_433_web(tmp_path, web).get("/").get_data(as_text=True))
    card = doc.within(doc.by_id("radio-433"))
    assert [card.within(f).field_default("op") for f in card.find("form", action="/action")] == []
    assert "Hardware" in doc.by_id("noradio-433").text


@pytest.mark.parametrize("rid", ["r1", "r3"])
def test_g1_w2_re_f2_every_stacks_view_keys_on_the_page_radio(tmp_path, web, rid):
    # DEC-W384ad (F2's reading): the radio is resolved once from its row and every per-stack view keys on it, with or
    # without cfg=; every stack row stays listed.
    from htmlq import parse
    c = _two_433_web(tmp_path, web)
    doc = parse(c.get(f"/stacks?radio={rid}&open=kiss").get_data(as_text=True))
    notes = [" ".join(p.text.split()) for p in doc.find("p", class_="instance-note")]
    assert notes and all(n.partition(" ")[0] == f"kiss@{rid}" for n in notes), notes
    rows = {e["id"] for e in doc.find("details") if e.has_attr("id") and e["id"][:9] == "stackrow-"}
    assert rows == {e["id"] for e in parse(c.get("/stacks?open=kiss").get_data(as_text=True)).find("details")
                    if e.has_attr("id") and e["id"][:9] == "stackrow-"}


@pytest.mark.parametrize("q", ["radio=", "dp=daemon&rid=r3&radio="])
def test_g1_w2_re_an_empty_radio_is_a_bad_request_by_name(tmp_path, web, q):
    _two_433(tmp_path)
    r = web().get(f"/stacks?{q}")
    assert (r.status_code, r.get_data(as_text=True)) == (400, "?radio= is empty: name a radio (?radio=rN)")


def test_g1_w2_re_a_radio_the_list_does_not_have_is_said_and_picks_none(tmp_path, web):
    # The page reports the rid it cannot use; every instance selector starts on "radio?" (no radio until one is chosen).
    from htmlq import parse
    c = _two_433_web(tmp_path, web)
    r = c.get("/stacks?radio=r9&open=kiss")
    doc = parse(r.get_data(as_text=True))
    assert r.status_code == 200 and doc.present("radio-refused")
    assert " ".join(doc.by_id("radio-refused").text.split()) == ("?radio=r9: r9 is not a radio of the hardware list. No "
                                                                  "radio is picked on this page: choose one in each selector.")
    selects = [doc.within(s) for s in doc.find("select", name="target")]
    assert selects and all([(o["value"], o.has_attr("disabled")) for o in s.find("option") if o.has_attr("selected")]
                           == [("", True)] for s in selects)

