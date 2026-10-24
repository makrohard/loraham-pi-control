"""T-K5 (A §2.12): the whole-stack Kill shows only on a failed, timed-out or refused stop; the
component Kill sits beside each component's Stop; the confirm names the processes."""
from lhpc.core import commands
from lhpc.core.config import Config
from lhpc.core.services import ControllerService


def _kiss_argv(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = svc.stack("kiss").component("loraham-kiss-tnc")
    return commands.expand_argv(comp.run_argv, comp, svc.stack_config(comp.id, "433"),
                                Config().operator, str(tmp_path),
                                str(svc._paths.resolve_source(comp.source.path)), "433")


def test_the_component_kill_sits_beside_each_stop_and_no_stack_kill_shows(web):
    page = web().get("/stacks/kiss/body").get_data(as_text=True)        # the stack's own body
    # The component card of kiss-serial has its Kill beside its Stop; the daemon's card (another
    # stack's process, B2 preparation Q9) has none.
    assert page.count('href="/kill/kiss?component=loraham-kiss-serial"') == 1
    assert "component=loraham-daemon" not in page
    assert 'id="kill-form"' not in page and "/kill/kiss\"" not in page


def test_the_confirm_names_the_processes(web, tmp_path):
    import os
    c = web(cmdlines={os.getpid(): _kiss_argv(tmp_path)})       # a live pid: it has an identity
    page = c.get("/kill/kiss?component=loraham-kiss-tnc").get_data(as_text=True)
    assert 'id="kill-form"' in page and f"pid {os.getpid()} loraham-kiss-tnc" in page
    assert c.get("/kill/nosuch").status_code == 404


def test_a_failed_stop_offers_the_whole_stack_kill(web, csrf, tmp_path):
    # A matching process LHPC does not own: the stop refuses (MANUAL_REQUIRED).
    c = web(cmdlines={999999: _kiss_argv(tmp_path)})
    r = c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss",
                                "confirmed": "yes"})
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and 'id="stop-failed"' in page and 'id="kill-form"' in page
    assert 'name="component" value=""' in page                       # the whole stack


def test_a_stop_that_succeeds_offers_no_kill(web, csrf):
    c = web()
    r = c.post("/action", data={"_csrf": csrf(c), "op": "stop", "target": "kiss",
                                "confirmed": "yes"})
    assert r.status_code in (302, 303) and "kill" not in r.headers["Location"]


def test_the_kill_post_needs_the_csrf_token(web):
    assert web().post("/kill/kiss", data={"component": ""}).status_code == 400
