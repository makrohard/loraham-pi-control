"""F26: the MeshCore Mode result shows directly under the Mode line (never on top). A repeater-name refusal's banner
links to Settings with BOTH node-name rows (chat node, repeater) marked red; an OK result, a reload or a hand-typed
`bad=` without such a refusal shows no link. Through POST /stacks/meshcore/mode and the page it redirects to."""
from urllib.parse import parse_qs, urlsplit

import radios_fixture
from htmlq import parse

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path, web):
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return web(), svc, instances.parse_target(svc, "meshcore@r3", "start")[1]


def _mode(c, csrf, mode):
    r = c.post("/stacks/meshcore/mode", data={"_csrf": csrf(c), "mode": mode, "radio": "r3"})
    assert r.status_code in (302, 303), r.status_code
    return r.headers["Location"]


def _under_mode(doc):
    """The result banner that is the element right after the Mode form, and any flash on top."""
    form = doc.by_id("stack-mode-meshcore")
    banners = [b for b in doc.find("div", class_="panel-result") if b.index == form.end + 1]
    return banners, doc.find("p", class_="flash")


def _link(doc, banner):
    return [a for a in doc.within(banner).find("a") if "Set the node names" in a.text]


def test_a_refused_mode_shows_under_the_mode_line_with_the_link_to_both_names(tmp_path, web, csrf):
    # red before F26: the refusal was a flash on top of the page, with no link
    c, _svc, _r3 = _box(tmp_path, web)
    loc = _mode(c, csrf, "chat+repeater")                                      # no repeater name saved: refused
    doc = parse(c.get(loc.split("#")[0]).get_data(as_text=True))
    banners, top = _under_mode(doc)
    assert len(banners) == 1 and not top, (banners, top)
    [links] = [doc.within(banners[0]).find("div", class_="bnr-links")]          # the link INSIDE the banner
    [a] = _link(doc, banners[0])
    assert a in doc.within(links[0]).find("a")
    q = parse_qs(urlsplit(a["href"]).query)
    assert q["cfg"] == ["meshcore"] and q["open"] == ["meshcore"] and q["radio"] == ["r3"]
    assert sorted(q["bad"][0].split(",")) == ["f_node_name", "f_repeater_name"]
    assert a["href"].endswith("#stack-settings-meshcore")
    marked = parse(c.get(a["href"].split("#")[0]).get_data(as_text=True))      # following it: both rows red
    bad = [r for r in marked.find("tr", class_="field-bad")]
    names = sorted(i["name"] for r in bad for i in marked.within(r).find("input"))
    assert names == ["f_node_name", "f_repeater_name"], names


def test_a_saved_mode_shows_under_the_mode_line_without_the_link(tmp_path, web, csrf):
    c, svc, r3 = _box(tmp_path, web)
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3", "file_node_name": "N3"}, inst=r3).ok
    loc = _mode(c, csrf, "chat+repeater")
    assert "bad=" not in loc
    doc = parse(c.get(loc.split("#")[0]).get_data(as_text=True))
    banners, top = _under_mode(doc)
    assert len(banners) == 1 and not top and not _link(doc, banners[0])


def test_a_reload_after_the_refusal_shows_no_banner_and_no_link(tmp_path, web, csrf):
    c, _svc, _r3 = _box(tmp_path, web)
    loc = _mode(c, csrf, "chat+repeater").split("#")[0]
    c.get(loc)                                                                  # the result shown once
    doc = parse(c.get(loc).get_data(as_text=True))                              # reload: flash consumed, bad= kept
    banners, _top = _under_mode(doc)
    assert not banners and "Set the node names" not in doc.text


def test_an_ok_result_with_a_hand_typed_bad_shows_no_link(tmp_path, web, csrf):
    c, svc, r3 = _box(tmp_path, web)
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3"}, inst=r3).ok
    loc = _mode(c, csrf, "chat").split("#")[0]
    doc = parse(c.get(loc + "&bad=f_node_name,f_repeater_name").get_data(as_text=True))
    banners, _top = _under_mode(doc)
    assert len(banners) == 1 and not _link(doc, banners[0])
