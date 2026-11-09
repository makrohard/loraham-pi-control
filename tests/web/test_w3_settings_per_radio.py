"""W3 (WEB-LANE-PLAN-v5 §W3, CLEANUP-DESIGN-v13 13.2/13.3, W312/W319-W321): Settings and the daemon's radio parameters
per radio. The forms name the radio (`radio=`), never a band: a `band` field is a 400 by name and writes nothing; a
stack with radios refuses a save without one. The daemon's panel saves the radio it shows (its row in the hardware
list), with or without a page radio. Driven through the rendered page and the console's routes; files read back."""
import tomllib

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios
from lhpc.core.paths import Paths

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"),
        radios_fixture.R3)


@pytest.fixture
def box(web, tmp_path, csrf):
    radios_fixture.write(tmp_path, ROWS)
    c = web()
    token = csrf(c, "/stacks?cfg=chat")

    def post(path, **form):
        return c.post(path, data={"_csrf": token, **form})
    return c, post, tmp_path


def _files(tmp_path):
    d = tmp_path / "config"
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()}


def _hidden(c, url, panel, action):
    """The hidden fields of the rendered form posting to `action` inside element `panel` (what a browser sends)."""
    doc = parse(c.get(url).get_data(as_text=True))
    inner = doc.within(doc.by_id(panel))
    form = next(f for f in inner.find("form") if f["action"] == action)
    return {i["name"]: i["value"] for i in inner.within(form).find("input", type="hidden")}


def _row_dp(tmp_path, rid):
    return dict(next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == rid).dp)


def test_a_radios_settings_save_writes_its_own_file_alone(box):
    c, post, tmp_path = box
    form = _hidden(c, "/stacks?radio=r2&cfg=chat", "stack-settings-chat", "/stacks/chat/config")
    assert form.get("radio") == "r2" and "band" not in form
    before = _files(tmp_path)
    r = post("/stacks/chat/config", **{**form, "f_call": "DL1ABC"})
    assert r.status_code == 302 and "radio=r2" in r.headers["Location"] and "band=" not in r.headers["Location"]
    after = _files(tmp_path)
    assert tomllib.loads(after["stacks/chat@r2.toml"].decode()).get("file_call") == "DL1ABC"
    changed = {k for k in after if after[k] != before.get(k) and k.startswith("stacks/")}
    assert changed == {"stacks/chat@r2.toml"}                     # local.toml holds the global remotes (13.12.1)


@pytest.mark.parametrize("path, form", [
    ("/stacks/chat/config", {"radio": "r2", "band": "433", "f_call": "DL1ABC"}),
    ("/stacks/chat/config", {"band": "433", "f_call": "DL1ABC"}),
    ("/stacks/chat/config/reset", {"radio": "r2", "band": "433"}),
    ("/stacks/daemon/daemon-params", {"radio": "r2", "band": "433", "dp_FREQ": "433.650"}),
    ("/stacks/daemon/daemon-params/apply", {"radio": "r2", "band": "433", "dp_FREQ": "433.650"}),
    ("/stacks/daemon/daemon-params/reset", {"radio": "r2", "band": "433"}),
    ("/stacks/daemon/daemon-params", {"rid": "r2", "dp_FREQ": "433.650"}),
])
def test_a_band_or_rid_field_is_refused_by_name_and_writes_nothing(box, path, form):
    _c, post, tmp_path = box
    before = _files(tmp_path)
    r = post(path, **form)
    assert r.status_code == 400 and ("band" in r.get_data(as_text=True) or "rid" in r.get_data(as_text=True))
    assert _files(tmp_path) == before


@pytest.mark.parametrize("path", ["/stacks/chat/config", "/stacks/chat/config/reset"])
def test_a_save_without_its_radio_is_refused_by_name(box, path):
    _c, post, tmp_path = box
    before = _files(tmp_path)
    r = post(path, f_call="DL1ABC")
    assert (r.status_code, "name the radio" in r.get_data(as_text=True)) == (400, True)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("url", ["/stacks?cfg=chat", "/stacks?radio=r2&cfg=chat"])
def test_the_settings_page_has_no_band_field_link_or_add_instance_entry(box, url):
    c, _post, _tmp = box
    page = c.get(url).get_data(as_text=True)
    doc = parse(page)
    sec = doc.within(doc.by_id("stack-settings-chat"))
    assert not sec.find("input", name="band")
    assert [a["href"] for a in sec.find("a") if "radio=" in a["href"]] and \
        not [a["href"] for a in sec.find("a") if "band=" in a["href"]]
    assert "add instance" not in page.lower() and "add-instance" not in page.lower()          # DEC-W344


def test_the_daemons_panel_saves_the_radio_it_shows(box):
    # The panel on ?radio=r2 shows r2's own values; its Save writes r2's row alone (it posted only the band before W3)
    c, post, tmp_path = box
    url = "/stacks?radio=r2&open=daemon&dp=daemon"
    form = _hidden(c, url, "stack-daemon-params-daemon", "/stacks/daemon/daemon-params")
    assert form.get("radio") == "r2" and "band" not in form
    r = post("/stacks/daemon/daemon-params", **{**form, "dp_FREQ": "433.650"})
    assert r.status_code == 302 and "radio=r2" in r.headers["Location"] and "band=" not in r.headers["Location"]
    assert (_row_dp(tmp_path, "r2"), _row_dp(tmp_path, "r1")) == ({"FREQ": "433.650"}, {})
    assert parse(c.get(url).get_data(as_text=True)).field_default("dp_FREQ") == "433.650"


def test_the_daemons_panel_without_a_page_radio_is_its_first_radios_and_chooses_by_radio(box):
    c, post, tmp_path = box
    url = "/stacks?open=daemon&dp=daemon"
    form = _hidden(c, url, "stack-daemon-params-daemon", "/stacks/daemon/daemon-params")
    assert form.get("radio") == "r1" and "band" not in form
    doc = parse(c.get(url).get_data(as_text=True))
    links = [a["href"] for a in doc.within(doc.by_id("stack-daemon-params-daemon")).find("a")]
    assert [h.split("?")[1].split("&")[0] for h in links] == ["radio=r1", "radio=r2", "radio=r3"]
    post("/stacks/daemon/daemon-params", **{**form, "dp_SF": "9"})
    assert (_row_dp(tmp_path, "r1"), _row_dp(tmp_path, "r2")) == ({"SF": "9"}, {})


def test_the_daemons_live_block_chooses_a_radio_not_a_band(box):
    c, _post, _tmp = box
    doc = parse(c.get("/stacks?radio=r3&cfg=daemon").get_data(as_text=True))
    sec = doc.within(doc.by_id("stack-settings-daemon"))
    hrefs = [a["href"] for a in sec.find("a") if "cfg=daemon" in a["href"]]
    assert [h.split("?")[1].split("&")[0] for h in hrefs] == ["radio=r1", "radio=r2", "radio=r3"]
    assert [a for a in sec.find("a", class_="sel") if "radio=r3" in a["href"]]
    assert not [h for h in hrefs if "band=" in h]



@pytest.mark.parametrize("path, form", [
    ("/stacks/daemon/daemon-params", {"band": "433", "dp_FREQ": "433.650"}),
    ("/stacks/daemon/daemon-params/apply", {"band": "433", "dp_FREQ": "433.650"}),
    ("/stacks/daemon/daemon-params/reset", {"band": "433"}),
    ("/stacks/daemon/daemon-params?band=433", {"radio": "r2", "dp_FREQ": "433.650"}),
    ("/stacks/chat/config?band=433", {"radio": "r2", "f_call": "DL1ABC"}),
    ("/stacks/chat/config/reset?band=433", {"radio": "r2"}),
    ("/stacks/kiss/daemon-params?band=433", {"band": "433", "dp_FREQ": "433.650"}),
])
def test_g1_w3_b1_a_band_for_the_daemon_or_in_the_query_is_refused_by_name_and_writes_nothing(box, path, form):
    # G1-W3 B1/N3 (DEC-W384bi): the daemon's band-only POST no longer reaches its band keys; a query band never counts
    _c, post, tmp_path = box
    before = _files(tmp_path)
    r = post(path, **form)
    assert r.status_code == 400 and "band is a radio's attribute: use radio=<rid>" in r.get_data(as_text=True)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("path, form, says", [
    ("/stacks/daemon/daemon-params", {"dp_FREQ": "433.650"}, "the daemon's radio parameters are per radio"),
    ("/stacks/daemon/daemon-params?rid=r2", {"band": "433", "dp_FREQ": "433.650"}, "rid is no field here"),
    ("/stacks/daemon/daemon-params?rid=r2", {"radio": "r2", "dp_FREQ": "433.650"}, "rid is no field here"),
    ("/stacks/daemon/daemon-params/reset?rid=r2", {}, "rid is no field here"),
])
def test_g1_w3_b1_the_daemons_panel_needs_its_radio_and_a_rid_in_the_url_is_refused(box, path, form, says):
    _c, post, tmp_path = box
    before = _files(tmp_path)
    r = post(path, **form)
    assert r.status_code == 400 and says in r.get_data(as_text=True)
    assert _files(tmp_path) == before


def test_g1_w3_b2_a_client_panel_never_posts_a_radio(box):
    # Pin (DEC-W384bi B2): on ?radio= a client stack has no panel (its radio's settings are the daemon's until K8d);
    # without one, its panel posts its band, as on ba316d42
    c, _post, _tmp = box
    for url, field in (("/stacks?radio=r2&open=kiss&dp=kiss", None), ("/stacks?open=kiss&dp=kiss", "band")):
        doc = parse(c.get(url).get_data(as_text=True))
        forms = [f for f in doc.find("form") if f["action"] == "/stacks/kiss/daemon-params"]
        names = {i["name"] for f in forms for i in doc.within(f).find("input", type="hidden")}
        assert ("radio" not in names) and (field in names if field else not forms), url


def test_g1_w3_n3_the_daemons_panel_never_reads_a_page_band(box):
    c, _post, _tmp = box
    form = _hidden(c, "/stacks?band=868&open=daemon&dp=daemon", "stack-daemon-params-daemon",
                   "/stacks/daemon/daemon-params")
    assert form.get("radio") == "r1" and "band" not in form          # its first enabled radio, never a band's
