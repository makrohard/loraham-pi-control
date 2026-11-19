"""UI-REORG c4 (plan v7 s3): the daemon's Settings tree. The Settings switch shows ONE radio's levels (High-power,
Monitor · rX, Live radio settings · rX, Daemon radio parameters); every radio's per-radio levels exist and are
addressable, a differing ?radio= shows the id's radio; the page reads only the selected radio's daemon. Monitor and
Live are always present: daemon not running -> paused / greyed with the configured values. High-power follows the
SAVED chip family."""
from __future__ import annotations

import re

import pytest
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_STATUS = (b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=0 RXREADY=1 "
           b"HIGHPOWER=0 CHIPFAMILY=SX127x\n")
_PER_RID = re.compile(r"stack-settings-daemon--(r\d)-[a-z]+")


def _box(web, tmp_path, live=("r1", "r2"), setup="uputronics"):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    assert radio_start.setup_radios(p, setup)[0]
    cfgmod.save_hardware_setup(p, setup)
    fs = FakeSystem(unix_replies={dc.conf_socket(r): _STATUS for r in live})
    svc = ControllerService(system=fs.system, paths=p)
    return web(service_factory=lambda: svc), svc, fs


def _doc(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return parse(r.get_data(as_text=True))


def _hidden(scope, form_id_part):
    return [i["value"] for f in scope.find("form") if form_id_part in (f["action"] or "")
            for i in scope.within(f).find("input", type="hidden") if i["name"] in ("radio", "rid")]


def _values(scope):
    """A Live block's shown value per key (its text/number input; a select's selected option)."""
    out = {}
    for f in scope.find("form", class_="setform"):
        key = [i["value"] for i in scope.within(f).find("input") if i["name"] == "key"][0]
        val = [i["value"] for i in scope.within(f).find("input") if i["name"] == "value"]
        sel = [o["value"] for o in scope.within(f).find("option") if o.has_attr("selected")]
        out[key] = val[0] if val else (sel[0] if sel else None)
    return out


@pytest.mark.parametrize("rid, other", [("r1", "r2"), ("r2", "r1")])
def test_the_switch_shows_one_radio_and_every_radio_level_stays_addressable(web, tmp_path, rid, other):
    c, _svc, _fs = _box(web, tmp_path)
    doc = _doc(c, f"/stacks/daemon/body?radio={rid}")
    s = doc.within(doc.by_id("stack-settings-daemon"))
    hp = doc.by_id("high-power-daemon")                                     # High-power: the selected radio's
    assert s.within(hp).find("summary")[0].text.strip() == f"High-power +20 dBm permission · {rid}"
    assert _hidden(s.within(hp), "/hardware/high-power") == [rid]
    for kind in ("monitor", "live"):                                         # Monitor, Live: the selected radio's
        assert doc.by_id(f"stack-settings-daemon--{rid}-{kind}") is not None, kind
        assert doc.by_id(f"stack-settings-daemon--{other}-{kind}") is None, kind
    dp = doc.by_id("stack-daemon-params-daemon")                             # Daemon radio parameters: in Settings
    assert dp is not None and s.within(dp).find("form") and _hidden(s.within(dp), "daemon-params") == [rid]
    tree = c.get("/stacks/menus").get_data(as_text=True)
    ids = sorted(set(re.findall(r'/stacks/daemon/menu/(stack-settings-daemon--r\d-[a-z]+)', tree)))
    assert {_PER_RID.fullmatch(i).group(1) for i in ids} == {"r1", "r2"} and len(ids) == 12, ids
    for mid in ids:                                                          # every radio's: with and without ?radio=
        for q in ("", f"?radio={rid}"):
            page = _doc(c, f"/stacks/daemon/menu/{mid}{q}")
            assert page.by_id(mid) is not None, (mid, q)                     # the id's radio, never a 404
    for mid in ("high-power-daemon", "stack-daemon-params-daemon"):          # no radio in the id: the page radio
        page = _doc(c, f"/stacks/daemon/menu/{mid}?radio={rid}")
        assert rid in _hidden(page.within(page.by_id(mid)), "/"), mid


def test_a_page_reads_only_the_selected_radios_daemon(web, tmp_path):
    # UI-REORG s3: the other radios' levels carry only their rid: one passive read of the shown radio (red before c4:
    # every radio's high-power state read its own daemon)
    _c, svc, fs = _box(web, tmp_path)
    paths = []
    real = fs.request
    fs.request = lambda path, *a, **k: (paths.append(path), real(path, *a, **k))[1]
    svc.config_view("daemon", rid="r2")
    assert paths and set(paths) == {dc.conf_socket("r2")}, paths


def test_live_and_monitor_of_a_radio_whose_daemon_never_answered(web, tmp_path):
    c, svc, _fs = _box(web, tmp_path, live=("r1",))
    doc = _doc(c, "/stacks/daemon/body?radio=r2")
    live = doc.by_id("stack-settings-daemon--r2-live")
    lv = doc.within(live)
    assert live["data-up"] == "0" and "Daemon not running" in lv.text
    fields = lv.find("select") + [i for i in lv.find("input") if i["type"] != "hidden"] + lv.find("button")
    assert fields and all(f.has_attr("disabled") for f in fields)            # greyed, server-side
    conf = svc._daemon_param_applies("daemon", "r2")                         # the configured values shown
    assert conf.get("FREQ") and _values(lv)["FREQ"] == conf["FREQ"]
    mon = doc.by_id("stack-settings-daemon--r2-monitor")
    assert mon["data-up"] == "0" and "Daemon not running" in doc.within(mon).text
    d1 = _doc(c, "/stacks/daemon/body?radio=r1")
    up = d1.within(d1.by_id("stack-settings-daemon--r1-live"))
    assert _values(up)["CADRSSI"] == "0" and _values(up)["CADWAIT"] == "1500"     # the running values; a zero kept
    assert not [b for b in up.find("button") if b.has_attr("disabled")]


@pytest.mark.parametrize("setup, unknown, off, note", [("uputronics", False, False, ""),
                                                      ("waveshare-433", False, True, "Not needed on an SX1262"),
                                                      ("uputronics", True, False, "chip unknown: only SX127x uses this")])
def test_high_power_follows_the_saved_chip_family(web, tmp_path, monkeypatch, setup, unknown, off, note):
    if unknown:                                     # a model whose preset names no family (hw_preset_family "")
        monkeypatch.setattr(ControllerService, "radio_chip_family", lambda self, rid: "")
    c, _svc, _fs = _box(web, tmp_path, setup=setup)
    doc = _doc(c, "/stacks?open=daemon&cfg=daemon&radio=r1")
    hp = doc.within(doc.by_id("high-power-daemon"))
    ctrl = hp.find("select") + hp.find("button")
    assert ctrl and all(x.has_attr("disabled") == off for x in ctrl), setup
    assert (note in hp.text) if note else ("Not needed" not in hp.text and "chip unknown" not in hp.text)
    assert "running daemon:" in hp.text                                      # saved vs running kept


def test_no_duplicate_badge_ids_on_the_daemon_body(web, tmp_path):
    c, _svc, _fs = _box(web, tmp_path)
    body = c.get("/stacks/daemon/body?radio=r1").get_data(as_text=True)
    ids = re.findall(r'id="(hw-badge-[^"]+)"', body)
    assert ids and len(ids) == len(set(ids))
    assert len(re.findall(r'class="badge [^"]* dbadge" data-rid="r1"', body)) == 2      # Monitor + Live, no id


@pytest.mark.parametrize("path", ["/stacks/daemon/body", "/stacks/daemon/menu/stack-settings-daemon"])
def test_the_daemon_radio_parameters_take_the_settings_radio_without_a_page_radio(web, tmp_path, path):
    # UI-REORG c4: the lowest rid on the higher band (r1 868, r2 433): Settings shows the first active band's radio
    # (r2); the panel inside it shows and saves THAT radio (was: view_radios[0] = r1, a radio the page did not name)
    import radios_fixture

    from lhpc.core import radios
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "868", ce="ce1"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce0")))
    c = web(system=FakeSystem(unix_replies={dc.conf_socket(r): _STATUS for r in ("r1", "r2")}).system,
            paths=Paths(runtime_root=tmp_path))
    doc = _doc(c, path)
    s = doc.within(doc.by_id("stack-settings-daemon"))
    sel = [a.text.strip() for a in s.within(s.find("nav", class_="rswitch")[0]).find("a", class_="sel")]
    dp = doc.by_id("stack-daemon-params-daemon")
    assert sel == ["r2 · 433"] and _hidden(s.within(dp), "daemon-params") == ["r2"], sel
@pytest.mark.parametrize("kind", ["monitor", "status", "stats", "socket", "rxtx", "live"])
def test_a_per_radio_page_has_the_switch_to_the_same_level_and_the_pill(web, tmp_path, kind):
    # UI-REORG c5 (s4, round-3 N1 = a): on Get STATUS of r1, r2 opens Get STATUS of r2; the pill upper right;
    # the Monitor and its views run in the full-page mode (data-fullpage)
    c, _svc, _fs = _box(web, tmp_path)
    doc = _doc(c, f"/stacks/daemon/menu/stack-settings-daemon--r1-{kind}?radio=r1")
    [head] = doc.find("div", class_="pagehead")
    links = {a.text.strip(): (a["href"], a.has_class("sel")) for a in doc.within(head).find("a")}
    assert links == {"r1 · 433": (f"/stacks/daemon/menu/stack-settings-daemon--r1-{kind}?radio=r1", True),
                     "r2 · 868": (f"/stacks/daemon/menu/stack-settings-daemon--r2-{kind}?radio=r2", False)}, links
    assert [b["data-rid"] for b in doc.within(head).find("span", class_="dbadge")] == ["r1"]
    assert doc.find("div", class_="menupage")[0]["data-fullpage"] == "1"
    assert not _doc(c, "/stacks/daemon/menu/stack-settings-daemon?radio=r1").find("div", class_="pagehead")


def test_the_monitor_and_live_header_pins_are_rendered_hidden_and_the_page_pills_are_not(web, tmp_path):
    # F11: a closed node never shows the load-time state: the Monitor / Live header pin is rendered hidden (dash.js
    # shows it on a live answer while its node is open); the page pills that share the macro show from the load
    c, _svc, _fs = _box(web, tmp_path)
    doc = _doc(c, "/stacks/daemon/body?radio=r1")
    for lvl in ("monitor", "live"):
        node = doc.by_id(f"stack-settings-daemon--r1-{lvl}")
        [pin] = doc.within(doc.within(node).find("summary")[0]).find("span", class_="dbadge")
        assert pin.has_attr("hidden"), lvl
    doc = _doc(c, "/stacks/daemon/menu/stack-settings-daemon--r1-status?radio=r1")
    pills = doc.find("span", class_="dbadge")
    assert len(pills) == 2 and not any(p.has_attr("hidden") for p in pills)        # the page pill + the view's own
