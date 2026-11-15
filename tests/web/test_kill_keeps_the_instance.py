"""Gate 2 (VERDICT-G2-0.14.0-COMB 46e21ad5) finding 1, DEC-W409ax: the console's Kill keeps the instance. kiss runs as
kiss@r1 and kiss@r2 (both 433): a failed stop of kiss@r1 offers the Kill of kiss@r1, its preview and its POST name
kiss@r1, and the Stacks page's component Kill carries the shown radio. The core's bare Kill keeps its promise (the whole
stack, for the orphan card; DEC-W409ba); tests/core/test_kill.py proves the kill of the offered instance alone."""
import radios_fixture

from lhpc.core import instances
from lhpc.core import kill as kill_mod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


def _svc(tmp_path, monkeypatch, running=("kiss@r1", "kiss@r2")):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))          # r1 and r2, both on 433
    real = instances.running_keys
    monkeypatch.setattr(instances, "running_keys", lambda s, sid: list(running) if sid == "kiss" else real(s, sid))
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _spy(monkeypatch):
    calls = []

    def kill(svc, target, component="", apply=False):
        calls.append((target, component, apply))
        return ActionResult(True, f"Would kill {target}.")
    monkeypatch.setattr(kill_mod, "kill", kill)
    return calls


def test_a_failed_instance_stop_offers_that_instances_kill(tmp_path, monkeypatch, web, csrf):
    # red on 01d3d09e: the Kill confirm previewed (and would POST) "kiss", the whole stack, kiss@r2 included
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(ControllerService, "run_action",
                        lambda self, op, target, **kw: ActionResult(False, f"stop of {target} failed"))
    calls = _spy(monkeypatch)
    c = web(service_factory=lambda: svc)
    page = c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss@r1", "confirmed": "yes"},
                  follow_redirects=True).get_data(as_text=True)
    assert calls == [("kiss@r1", "", False)], calls
    assert '<input type="hidden" name="radio" value="r1">' in page and "Kill kiss@r1?" in page


def test_the_kill_page_and_its_post_keep_the_radio(tmp_path, monkeypatch, web, csrf):
    # red on 01d3d09e: both called kill("kiss"), the radio dropped
    svc = _svc(tmp_path, monkeypatch)
    calls = _spy(monkeypatch)
    c = web(service_factory=lambda: svc)
    assert c.get("/kill/kiss?radio=r2&component=loraham-kiss-tnc").status_code == 200
    c.post("/kill/kiss", data={"_csrf": csrf(c), "component": "loraham-kiss-tnc", "radio": "r2"})
    assert calls == [("kiss@r2", "loraham-kiss-tnc", False), ("kiss@r2", "loraham-kiss-tnc", True)], calls


def test_the_stacks_page_kill_names_the_shown_radio(tmp_path, monkeypatch, web):
    # the component Kill on kiss@r2's Stacks view links to its radio. red on 01d3d09e: a bare /kill/kiss link
    svc = _svc(tmp_path, monkeypatch)
    page = web(service_factory=lambda: svc).get("/stacks?open=kiss&radio=r2").get_data(as_text=True)
    assert "/kill/kiss?component=loraham-kiss-serial&amp;radio=r2" in page, [ln for ln in page.splitlines() if "/kill/" in ln]

