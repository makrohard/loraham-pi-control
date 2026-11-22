"""F31 (his choice A): MeshCore's openHop mode lives only in Settings, per radio, as the first row (inside the Settings
form, so every save sends it); the stack page has no Mode form. A repeater mode refused for its missing repeater name
marks both node-name rows, shown only with that refusal. Through the Settings form as rendered and the public pages."""
import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path, rows=(radios_fixture.R3, radios_fixture.R2_868)):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _page(c, rid):
    return parse(c.get(f"/stacks?open=meshcore&cfg=meshcore&radio={rid}").get_data(as_text=True))


def _fields(doc):
    """The fields a browser sends for the Settings form as rendered (inputs, checked boxes, selected options)."""
    form = doc.by_id("cfg-form-meshcore")
    f = doc.within(form)
    out = {i["name"]: i.attrs.get("value", "") for i in f.find("input")
           if i.attrs.get("name") and (i.attrs.get("type") not in ("checkbox", "radio") or "checked" in i.attrs)}
    for s in f.find("select"):
        opts = f.within(s).find("option")
        pick = [o for o in opts if o.has_attr("selected")] or opts[:1]
        if s["name"] and pick:
            out[s["name"]] = pick[0]["value"]
    return out


def test_the_mode_is_settings_first_row_per_radio_and_exactly_once(tmp_path, web):
    # red before F31: the mode was a row of the Repeater group (and a separate form on the stack page)
    svc = _box(tmp_path)
    r3 = instances.parse_target(svc, "meshcore@r3", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3", "file_mode": "repeater"}, inst=r3).ok
    c = web()
    for rid, want in (("r3", "repeater"), ("r2", "chat")):
        doc = _page(c, rid)
        form = doc.within(doc.by_id("cfg-form-meshcore"))
        [first] = form.find("section", class_="cfgsec")[:1]
        assert first.has_class("cfgmode")                                       # the first thing in the form
        mode = [s for s in form.find("select") if s["name"] == "f_mode"]
        assert len(mode) == 1                                                    # exactly once
        assert [o["value"] for o in form.within(mode[0]).find("option") if o.has_attr("selected")] == [want]
    assert not _page(c, "r2").by_id("stack-mode-meshcore")                         # no stack-page Mode form


def test_saving_another_setting_keeps_a_non_default_mode(tmp_path, web, csrf):
    svc = _box(tmp_path)
    r3 = instances.parse_target(svc, "meshcore@r3", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3", "file_mode": "repeater"}, inst=r3).ok
    c = web()
    data = _fields(_page(c, "r3"))
    data["f_repeater_name"] = "RPT3B"                                              # another setting changed
    r = c.post("/stacks/meshcore/config", data=data)
    assert r.status_code in (302, 303)
    svc._invalidate_config()
    assert svc.meshcore_mode(r3) == "repeater"                                     # the mode stays


def test_a_refused_repeater_mode_marks_both_names_only_with_its_result(tmp_path, web, csrf):
    _box(tmp_path)
    c = web()
    data = _fields(_page(c, "r3"))
    data["f_mode"] = "repeater"                                                   # no repeater name saved
    r = c.post("/stacks/meshcore/config", data=data)
    loc = r.headers["Location"]
    assert "bad=" in loc
    doc = parse(c.get(loc.split("#")[0]).get_data(as_text=True))
    marked = sorted(i["name"] for t in doc.find("tr", class_="field-bad") for i in doc.within(t).find("input"))
    assert marked == ["f_node_name", "f_repeater_name"], marked
    again = parse(c.get(loc.split("#")[0]).get_data(as_text=True))               # reload: the result is gone
    assert not again.find("tr", class_="field-bad")
    hand = parse(c.get("/stacks?open=meshcore&cfg=meshcore&radio=r3&bad=f_node_name,f_repeater_name")
                 .get_data(as_text=True))
    assert not hand.find("tr", class_="field-bad")                                # a hand-typed bad= marks nothing


def test_the_settings_save_keeps_the_radio_contract(tmp_path, web, csrf):
    # the three facts of the removed stack-page form tests: on a listed box a radio must be named (even with one
    # eligible radio); an unknown radio is refused; with no hardware list it is the stack-wide save
    _box(tmp_path, rows=(radios.Radio("r3", "stick", "meshstick-1262", "868", enabled=False, usb_serial="MSTICK01"),
                         radios_fixture.R2_868))
    c = web()
    tok = csrf(c)
    assert c.post("/stacks/meshcore/config", data={"_csrf": tok, "f_mode": "chat"}).status_code == 400
    assert c.post("/stacks/meshcore/config", data={"_csrf": tok, "f_mode": "chat", "radio": "r9"}).status_code in (400, 404)


@pytest.mark.no_default_hardware
def test_without_a_hardware_list_the_settings_save_is_stack_wide(tmp_path, web, csrf):
    c = web()
    tok = csrf(c)
    assert c.post("/stacks/meshcore/config", data={"_csrf": tok, "f_mode": "chat"}).status_code in (302, 303)


def test_the_running_hint_shows_only_when_running_and_saved_differ(tmp_path, web, monkeypatch):
    from seams import entry_args, exe
    svc = _box(tmp_path)
    c = web()
    assert "restart to apply" not in _page(c, "r2").text                           # not running
    r2 = instances.parse_target(svc, "meshcore@r2", "start")[1]
    comp = svc.stack("meshcore").component("meshcore-node")
    opt, val = instances._token(svc, comp, r2, "868")
    argv = [exe(comp.process, comp.process.exec_name), *entry_args(comp.process),
            *[a for a in comp.process.all_args if a != opt], opt, val]
    running = ControllerService(system=FakeSystem(cmdlines_data={700: argv}).system, paths=svc._paths)
    monkeypatch.setattr(ControllerService, "meshcore_running_mode", lambda self, inst=None: "repeater")
    doc = parse(web(service_factory=lambda: running).get("/stacks?open=meshcore&cfg=meshcore&radio=r2")
                .get_data(as_text=True))
    sec = doc.within(doc.find("section", class_="cfgmode")[0])
    assert "Running: repeater — restart to apply chat." in sec.text


def test_a_stack_without_a_mode_param_shows_no_mode_block(tmp_path, web):
    _box(tmp_path)
    doc = parse(web().get("/stacks?open=kiss&cfg=kiss&radio=r3").get_data(as_text=True))
    assert not doc.find("section", class_="cfgmode")


def test_the_generated_config_and_the_cli_listing_keep_the_mode(tmp_path):
    svc = _box(tmp_path)
    r2 = instances.parse_target(svc, "meshcore@r2", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT2"}, inst=r2).ok
    for mode in ("chat", "chat+repeater", "repeater"):
        assert svc.save_config_bundle("meshcore", values={"file_mode": mode}, inst=r2).ok
        svc._invalidate_config()
        assert svc.meshcore_mode(r2) == mode
    keys = [f["key"] for f in svc.config_param_fields("meshcore")]
    assert "mode" in keys                                                          # the CLI's config listing names it
