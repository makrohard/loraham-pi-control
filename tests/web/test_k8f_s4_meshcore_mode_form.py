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
