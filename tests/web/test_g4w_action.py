"""G4-web (DEC-W384cz/da): /action's stack-wide operations (install, update, build, clean, test) name no band either:
a `band` field is a 400 by name and runs nothing (no in-app form sends one). The §6 stray band daemon's Stop keeps
its band (W290). Through the console's route; the service is spied."""
import pytest

from lhpc.core.services import ActionResult, ControllerService


@pytest.mark.parametrize("op", ["install", "update", "build", "clean", "test"])
def test_a_band_on_a_stack_wide_action_is_refused_by_name_and_runs_nothing(web, csrf, monkeypatch, op):
    seen = []
    for name in ("run_action", "spawn_web_job"):
        monkeypatch.setattr(ControllerService, name,
                            lambda self, *a, _n=name, **k: seen.append((_n, a, k)) or ActionResult(True, "ok"))
    c = web()
    r = c.post("/action", data={"_csrf": csrf(c), "op": op, "target": "kiss", "band": "433"})
    assert (r.status_code, "band is a radio's attribute: use radio=<rid>" in r.get_data(as_text=True)) == (400, True)
    assert seen == []


@pytest.mark.parametrize("op", ["start", "stop", "restart"])
def test_an_instances_action_hands_the_core_its_instance_never_a_band(web, csrf, monkeypatch, op):
    # G4-web: kiss@r1's plan and apply carry the instance; the core reads the band off its radio row (radio_band)
    seen = []
    monkeypatch.setattr(ControllerService, "run_action", lambda self, o, t, **k: seen.append(
        (o, t, k.get("band", ""), getattr(k.get("inst"), "rid", None))) or ActionResult(True, "ok", data={}))
    monkeypatch.setattr(ControllerService, "spawn_start_job", lambda self, *a, **k: (None, "blocked", "spied"))
    monkeypatch.setattr(ControllerService, "is_installed", lambda self, t: True)
    monkeypatch.setattr(ControllerService, "unbuilt_components", lambda self, t: [])
    c = web()
    tok = csrf(c)
    c.post("/action", data={"_csrf": tok, "op": op, "target": "kiss@r1"})
    c.post("/action", data={"_csrf": tok, "op": op, "target": "kiss@r1", "confirmed": "yes"})
    assert seen and all(s == (op, "kiss", "", "r1") for s in seen), seen
