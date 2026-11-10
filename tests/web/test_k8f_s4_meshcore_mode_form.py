"""K8f S4-2 (K8F-S4-DESIGN-v4, G1-K8F-S4-D2-CTX B1/N3): the MeshCore Mode switch on the stack body is the shown radio's:
it shows that radio's saved mode, saves that radio's mode, and the page after the save shows the same radio. Two radios
on 868 (r3, r2)."""
import pytest
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


def _post(c, tok, **extra):
    return c.post("/stacks/meshcore/mode", data={"_csrf": tok, "mode": "repeater", **extra})


def test_with_a_hardware_list_the_mode_post_needs_a_valid_radio_even_with_one_radio(tmp_path, web, csrf):
    # G1-K8F-S4-2-RE B1 (DEC-W409j): the route states the rule: a box with a list saves a radio's mode only. One
    # eligible radio (r2; r3 disabled) and no radio: refused by name (instances.parse_target refuses a bare <stack>,
    # one radio included, instances.py:220-224); a disabled, an unknown and a malformed radio: refused; nothing saved
    from lhpc.core import radios
    radios_fixture.write(tmp_path, (radios.Radio("r3", "stick", "meshstick-1262", "868", enabled=False,
                                                 usb_serial="MSTICK01"), radios_fixture.R2_868))
    c = web()
    tok = csrf(c)
    for extra, says in (({}, "use meshcore@r2"), ({"radio": "r3"}, "r3 is disabled"),
                        ({"radio": "r9"}, "r9"), ({"radio": "x"}, "Mode: ")):
        r = _post(c, tok, **extra)
        assert r.status_code == 400 and says in r.get_data(as_text=True), (extra, r.get_data(as_text=True))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.meshcore_mode(instances.parse_target(svc, "meshcore@r2", "start")[1]) == "chat"


@pytest.mark.no_default_hardware
def test_without_a_hardware_list_the_mode_post_is_the_stack_wide_save(tmp_path, web, csrf):
    # no list: no instance exists; a radio named is refused, the radio-less POST keeps the stack-wide save
    c = web()
    tok = csrf(c)
    assert _post(c, tok, radio="r2").status_code == 400
    assert _post(c, tok).status_code in (302, 303)


def test_the_console_reads_the_shown_radios_mode_through_the_wrapper(tmp_path, web, monkeypatch):
    # N1 (S42-03): the wrapper forwards the shown radio to the CALLEE meshcore_mode: an instance-less callee answers
    # a mode no radio has, so the page's selection shows which call the wrapper made
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r3 = instances.parse_target(svc, "meshcore@r3", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3"}, inst=r3).ok
    assert svc.save_config_bundle("meshcore", values={"file_mode": "repeater"}, inst=r3).ok
    real = ControllerService.meshcore_mode
    monkeypatch.setattr(ControllerService, "meshcore_mode",
                        lambda self, inst=None: real(self, inst) if getattr(inst, "rid", "") else "chat+repeater")
    page = web().get("/stacks?open=meshcore&radio=r3").get_data(as_text=True)
    form = page[page.index('id="stack-mode-meshcore"'):]
    assert '<option value="repeater" selected>' in form[:form.index("</form>")]


def test_the_rf_log_viewer_decodes_a_radios_log_in_its_mode(tmp_path, web, monkeypatch):
    # B2 note, through the viewer's public route (rflog_records + rflog_decode): r2 runs as a repeater with its saved
    # mode back at chat; cached under r2's mode; when r2 stops its saved chat applies: the cache resets ONCE, a second
    # stopped read decodes nothing again
    import sys
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    gen = tmp_path / "config" / "files" / "r2" / "meshcore.toml"
    gen.parent.mkdir(parents=True, exist_ok=True)
    gen.write_text('[repeater]\nrole = "repeater"\n')
    log = tmp_path / "logs" / "r2" / "rf-meshcore.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f'2026-09-12T18:36:49.968Z RX rssi=-67.00 snr=11.25 len=4 hex=0102030{i} ascii="..."\n'
                           for i in range(2)))
    live = {"r2": True}
    monkeypatch.setattr(instances, "instance_runs", lambda s, comp, inst, band: live.get(getattr(inst, "rid", ""), False))
    runs = tmp_path / "runs"
    script = tmp_path / "fake_decoder.py"
    script.write_text("import json, sys\nreqs = [json.loads(l) for l in sys.stdin if l.strip()]\n"
                      f"open({str(runs)!r}, 'a').write(str(len(reqs)) + '\\n')\n"
                      "for r in reqs:\n    print(json.dumps({'key': r['key'], 'status': 'ok', 'kind': 'text', "
                      "'peer': 'p', 'decoded': 'hi'}))\n")
    monkeypatch.setattr(ControllerService, "_rflog_decoder_argv", lambda self, e, rid="": ((sys.executable, str(script)), ""))
    c = web()

    def read():
        r = c.get("/api/rflog/meshcore-node/decoded?job=r2/rf-meshcore.log")
        assert r.status_code == 200, r.get_data(as_text=True)
        return runs.read_text().splitlines() if runs.exists() else []
    assert read() == ["2"] and read() == ["2"]                  # cached under r2's running repeater
    live["r2"] = False                                          # r2 stopped: its saved chat applies
    assert read() == ["2", "2"] and read() == ["2", "2"]        # the mode moved: reset once, then cached again
