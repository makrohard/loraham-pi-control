"""K8f S4-2 (K8F-S4-DESIGN-v4, G1-K8F-S4-D2-CTX B1/N3): the MeshCore Mode switch on the stack body is the shown radio's:
it shows that radio's saved mode, saves that radio's mode, and the page after the save shows the same radio. Two radios
on 868 (r3, r2)."""
import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_the_mode_switch_shows_and_saves_the_shown_radios_mode(tmp_path, web, csrf):
    # red on a3dce2ea: the form carried no radio, the save went to the fallback instance (r2), the page showed r2's mode
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    c = web()
    tok = csrf(c)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r3 = instances.parse_target(svc, "meshcore@r3", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3"}, inst=r3).ok
    page = c.get("/stacks?open=meshcore&radio=r3").get_data(as_text=True)
    form = page[page.index('id="stack-mode-meshcore"'):]
    form = form[:form.index("</form>")]
    assert 'name="radio" value="r3"' in form, form
    r = c.post("/stacks/meshcore/mode", data={"_csrf": tok, "mode": "repeater", "radio": "r3"})
    assert r.status_code in (302, 303) and "radio=r3" in r.headers["Location"], r.headers.get("Location")
    svc._invalidate_config()
    r2 = instances.parse_target(svc, "meshcore@r2", "start")[1]
    assert (svc.meshcore_mode(r3), svc.meshcore_mode(r2)) == ("repeater", "chat")
    shown = c.get("/stacks?open=meshcore&radio=r3").get_data(as_text=True)
    assert '<option value="repeater" selected>' in shown
    other = c.get("/stacks?open=meshcore&radio=r2").get_data(as_text=True)
    assert '<option value="chat" selected>' in other and '<option value="repeater" selected>' not in other


def test_a_mode_post_without_its_radio_is_refused_by_name_and_saves_nothing(tmp_path, web, csrf):
    # G1-K8F-S4-2 B1: a hardware list is present, the POST names no radio: 400 naming the instances, neither changes.
    # red on 99da4eb1: saved through the fallback instance
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    c = web()
    tok = csrf(c)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ins = {r: instances.parse_target(svc, f"meshcore@{r}", "start")[1] for r in ("r2", "r3")}
    for r in ("r2", "r3"):
        assert svc.save_config_bundle("meshcore", values={"file_repeater_name": f"RPT{r}"}, inst=ins[r]).ok
    r = c.post("/stacks/meshcore/mode", data={"_csrf": tok, "mode": "repeater"})
    assert r.status_code == 400 and "use meshcore@r2 or meshcore@r3" in r.get_data(as_text=True)
    svc._invalidate_config()
    assert (svc.meshcore_mode(ins["r2"]), svc.meshcore_mode(ins["r3"])) == ("chat", "chat")


def test_the_console_asks_the_shown_radios_mode(tmp_path, web, monkeypatch):
    # N1 (S42-03): the stack body's mode is meshcore_mode_display(<the shown radio>); an instance-less call answers a
    # different mode on purpose, so the page's selection proves which one it read
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r3 = instances.parse_target(svc, "meshcore@r3", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3"}, inst=r3).ok
    assert svc.save_config_bundle("meshcore", values={"file_mode": "repeater"}, inst=r3).ok     # r2 stays chat
    real, seen = ControllerService.meshcore_mode_display, []

    def spy(self, inst=None):
        seen.append(getattr(inst, "rid", None))
        return real(self, inst) if inst is not None else "chat+repeater"
    monkeypatch.setattr(ControllerService, "meshcore_mode_display", spy)
    c = web()
    page = c.get("/stacks?open=meshcore&radio=r3").get_data(as_text=True)
    assert "r3" in seen and '<option value="repeater" selected>' in page, seen
