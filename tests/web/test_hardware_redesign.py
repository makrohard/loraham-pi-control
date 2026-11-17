"""W257 (mock v13, W258): the redesigned Hardware pages through the console. The list is a table, one row per radio,
the whole row opening its detail (the id link stretched over it, no JS), "enabled" in green, the read-only problem
notes, and the row's one Disable/Enable; the detail page carries Re-bind, Detect and Remove; Add Hardware lists the
new sticks (the model picked there) and one HAT row. The old daemon-spawn Detect is gone (DEC-W50)."""
import html
import re

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radio_daemon, radio_start, radios

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "stick", "meshstick-1262", "868", enabled=False, usb_port="1-1.9"))


def _box(web, tmp_path, monkeypatch, procs=()):
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: list(procs))
    return web()


def _text(page):
    return " ".join(html.unescape(page).split())


def _post(c, csrf, url, page="/hardware", **form):
    return c.post(url, data={"_csrf": csrf(c, page), **form})


def test_the_list_is_one_row_per_radio_each_opening_its_detail(web, tmp_path, monkeypatch):
    page = _box(web, tmp_path, monkeypatch).get("/hardware").get_data(as_text=True)
    for rid in ("r1", "r2", "r3"):
        row = re.search(rf'<tr class="hwrow[^"]*" data-rid="{rid}">(.*?)</tr>', page, re.S)
        assert row and f'class="idlink" href="/hardware/radio/{rid}"' in row.group(1), rid
        assert f'name="rid" value="{rid}"' in row.group(1)                     # its own Disable/Enable
    assert '<span class="state-on">enabled</span>' in page and 'href="/hardware/add"' in page


def test_the_row_button_reads_disable_or_enable(web, tmp_path, monkeypatch):
    page = _text(_box(web, tmp_path, monkeypatch).get("/hardware").get_data(as_text=True))
    assert re.search(r'data-rid="r1">.*?name="enabled" value="off"> <button type="submit">Disable', page)
    assert re.search(r'data-rid="r3">.*?name="enabled" value="on"> <button type="submit">Enable', page)


def _row(page, rid):
    return re.search(rf'data-rid="{rid}">(.*?)</tr>', page, re.S).group(1)


def test_an_offline_daemon_shows_once_in_the_row_as_its_badge(web, tmp_path, monkeypatch):
    page = _text(_box(web, tmp_path, monkeypatch).get("/hardware").get_data(as_text=True))
    row = _row(page, "r1")                                                           # enabled, no CONF answer
    assert ">daemon offline</span>" in row and row.count("daemon offline") == 1, row   # the badge, no red note too
    assert "⚠ stick not plugged in on port 1-1.9" in _row(page, "r3")                 # the row's other notes stay


_DASH = '<script src="/static/dash.js" defer></script>'


def test_the_list_badges_are_live_regions_the_poll_can_find(web, tmp_path, monkeypatch):
    # HWLIST-BADGE-LIVE: each list badge has its radio's id and announces changes politely; /hardware loads the poll
    page = _box(web, tmp_path, monkeypatch).get("/hardware").get_data(as_text=True)
    for rid in ("r1", "r2", "r3"):
        badge = re.search(rf'<span class="badge [^"]*" id="hw-badge-{rid}"([^>]*)>', page)
        assert badge and badge.group(1) == ' aria-live="polite"', rid
    assert _DASH in page


@pytest.mark.parametrize("url", ["/stacks", "/stacks?open=daemon", "/stacks/daemon/menu/stackrow-daemon"])
def test_each_page_that_can_show_the_list_loads_the_poll(web, tmp_path, monkeypatch, url):
    page = _box(web, tmp_path, monkeypatch).get(url).get_data(as_text=True)
    assert _DASH in page and page.count(_DASH) == 1, url


def test_a_menu_page_without_the_list_does_not_load_the_poll(web, tmp_path, monkeypatch):
    page = _box(web, tmp_path, monkeypatch).get("/stacks/daemon/menu/stack-settings-daemon").get_data(as_text=True)
    assert "hw-badge-" not in page and _DASH not in page


def test_the_detail_page_keeps_its_offline_note(web, tmp_path, monkeypatch):
    page = _text(_box(web, tmp_path, monkeypatch).get("/hardware/radio/r1").get_data(as_text=True))
    assert '<p class="note-bad">⚠ daemon offline</p>' in page                         # it has no badge


def test_the_detail_page_has_its_facts_detect_and_remove(web, tmp_path, monkeypatch):
    c = _box(web, tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")])
    page = _text(c.get("/hardware/radio/r1").get_data(as_text=True))
    assert "r1 — low" in page and 'id="detect-r1"' in page
    assert 'data-remove-blocked="r1"' in page and "Refused while it runs: stop its daemon, then remove r1." in page
    assert 'href="/hardware/remove/r2"' in _text(c.get("/hardware/radio/r2").get_data(as_text=True))


def test_an_unknown_radio_has_no_detail_page(web, tmp_path, monkeypatch):
    assert _box(web, tmp_path, monkeypatch).get("/hardware/radio/r9").status_code == 404


def test_add_hardware_offers_each_board_and_its_variants(web, tmp_path, monkeypatch):
    page = _box(web, tmp_path, monkeypatch).get("/hardware/add").get_data(as_text=True)
    assert 'value="loraham:"' in page                                          # the band picks its variant
    assert 'value="uputronics:ce0"' in page and 'value="uputronics:ce1"' in page
    assert 'formaction="/hardware/detect"' in page


@pytest.mark.parametrize("ok,where", [(True, "/hardware"), (False, "/hardware/add")])
def test_the_hat_row_adds_through_add_hat(web, tmp_path, monkeypatch, csrf, ok, where):
    c = _box(web, tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(radio_start, "add_hat", lambda svc, m, b, v="": seen.append((m, b, v)) or (ok, "said so"))
    r = _post(c, csrf, "/hardware/add-hat", page="/hardware/add", board="uputronics:ce1", band="868")
    assert seen == [("uputronics", "868", "ce1")] and r.headers["Location"].endswith(where)


def test_the_row_button_goes_through_set_enabled(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(radio_start, "set_enabled", lambda svc, rid, on: seen.append((rid, on)) or (True, "r3 enabled"),
                        raising=False)
    r = _post(c, csrf, "/hardware/enabled", rid="r3", enabled="on")
    assert seen == [("r3", True)] and r.headers["Location"].endswith("/hardware")
    assert "r3 enabled" in c.get("/hardware").get_data(as_text=True)


@pytest.mark.parametrize("url", ["/hardware/add-hat", "/hardware/enabled", "/hardware/detect"])
def test_without_the_token_nothing_runs(web, tmp_path, monkeypatch, url):
    c = _box(web, tmp_path, monkeypatch)
    for name in ("add_hat", "set_enabled"):
        monkeypatch.setattr(radio_start, name, lambda *a, **k: pytest.fail("ran"), raising=False)
    assert c.post(url, data={"_csrf": "wrong", "rid": "r1", "kind": "radio"}).status_code == 400


def test_detect_never_redirects_off_the_hardware_pages(web, tmp_path, monkeypatch, csrf):
    from lhpc.core.radio_detect import run
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_radio", lambda svc, rid: run.Detected("found", "r2: found", "sx127x"))
    for back in ("//evil.example/x", "https://evil.example/", "/stacks", "/hardware\\..\\x"):
        r = _post(c, csrf, "/hardware/detect", page="/hardware/radio/r2", kind="radio", rid="r2", back=back)
        assert r.headers["Location"].endswith("/hardware#detect-r2"), (back, r.headers["Location"])


def test_the_old_daemon_spawn_detect_is_gone(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch)
    assert c.post("/hardware/probe", data={"_csrf": csrf(c, "/hardware"), "band": "433"}).status_code in (404, 405)
    assert "hardware/probe" not in c.get("/stacks?open=daemon&cfg=daemon").get_data(as_text=True)


@pytest.mark.parametrize("url,here", [("/hardware", "Hardware"), ("/hardware/radio/r2", "r2 — high"),
                                      ("/hardware/add", "Add Hardware"), ("/hardware/remove/r2", "Remove r2")])
def test_each_hardware_page_says_where_it_sits(web, tmp_path, monkeypatch, url, here):
    # W245: Home › Apps › <the daemon row> › Hardware [› here], each crumb but the last a link; U5: the daemon row
    # is named as the Stacks page crumbs name it (its stack's name, as on /stacks/daemon/menu/stackrow-daemon).
    c = _box(web, tmp_path, monkeypatch)
    page = _text(c.get(url).get_data(as_text=True))
    crumbs = re.search(r'<nav class="crumbs"[^>]*>(.*?)</nav>', page).group(1)
    row = parse(c.get("/stacks/daemon/menu/stackrow-daemon").get_data(as_text=True)).find("nav", class_="crumbs")[0]
    assert row.text == "Home › Apps › LoRaHAM daemon"
    assert re.findall(r">([^<>›]+)</a>", crumbs)[:3] == ["Home", "Apps", "LoRaHAM daemon"]
    assert crumbs.rstrip().endswith(f'<h2 class="here">{here}</h2>'), crumbs


@pytest.mark.parametrize("url,gone", [("/hardware", "<h2>The radios</h2>"), ("/hardware/add", "<h2>Add Hardware</h2>"),
                                      ("/hardware/radio/r2", "<h2>r2 — high</h2>"),
                                      ("/hardware/remove/r2", "<h2>Hardware</h2>")])
def test_each_hardware_page_has_one_heading_after_all_menus(web, tmp_path, monkeypatch, url, gone):
    # STACKS-PAGE-HEADERS: the card's double heading is gone; the last crumb is the page's only h2, and
    # "All menus" stands right before the crumbs' <nav>
    page = _box(web, tmp_path, monkeypatch).get(url).get_data(as_text=True)
    assert gone not in page and len(re.findall(r"<h2[ >]", page)) == 1, re.findall(r"<h2[^>]*>.*?</h2>", page)
    assert re.search(r'<div class="crumbline"><a class="crumbs-all" href="/stacks/menus">All menus</a>'
                     r'<span class="crumbs-sep" aria-hidden="true"> · </span><nav class="crumbs"', page)


def test_the_daemon_entries_read_install_hardware_settings_info(web, tmp_path, monkeypatch):
    # W251: the daemon's own entries in that order, Hardware's "page ›" its page (/hardware, U5); another stack keeps
    # Info before Settings.
    c = _box(web, tmp_path, monkeypatch)
    page = c.get("/stacks?open=daemon").get_data(as_text=True)
    ids = [f'id="stack-{e}-daemon"' for e in ("install", "hardware", "settings", "info")]
    assert all(i in page for i in ids) and [page.index(i) for i in ids] == sorted(page.index(i) for i in ids), ids
    kiss = c.get("/stacks?open=kiss").get_data(as_text=True)
    assert kiss.index('id="stack-info-kiss"') < kiss.index('id="stack-settings-kiss"')     # unchanged elsewhere
    hw = page[page.index('id="stack-hardware-daemon"'):]
    assert re.match(r'.*?</details>\s*<div class="row-actions"><a class="pagelink" href="/hardware"', hw, re.S)


def _hp(monkeypatch, warn=(), mismatch=None):
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "high_power_state", lambda self, band, view=None, rid="": {
        "warn": band in warn, "mismatch": (mismatch or {}).get(band, "")})


def test_the_high_power_warning_and_mismatch_show_on_the_detail_page_and_in_the_row(web, tmp_path, monkeypatch):
    # DEC-W57: what Home's band column showed is now the radio's: the warning and the mismatch on its detail page,
    # a short note in its row on the list.
    _hp(monkeypatch, warn=("433",), mismatch={"868": "saved on, the running daemon reports off"})
    c = _box(web, tmp_path, monkeypatch)
    r1, r2 = (_text(c.get(f"/hardware/radio/{r}").get_data(as_text=True)) for r in ("r1", "r2"))
    assert 'id="hw-hp-warn"' in r1 and "+20 dBm permission is enabled on 433 MHz" in r1 and 'id="hw-hp-warn"' not in r2
    assert "High-power switch, 868 MHz: saved on, the running daemon reports off" in r2
    page = _text(c.get("/hardware").get_data(as_text=True))
    assert re.search(r'data-rid="r1">.*?⚠ \+20 dBm permission enabled on r1 \(433 MHz\).*?data-rid="r2"', page)
    assert re.search(r'data-rid="r2">.*?⚠ high-power switch: saved on, the running daemon reports off', page)


@pytest.mark.parametrize("installed", [True, False])
def test_the_detail_page_links_the_radios_rf_log_while_a_daemon_is_installed(web, tmp_path, monkeypatch, installed):
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: installed)
    page = html.unescape(_box(web, tmp_path, monkeypatch).get("/hardware/radio/r2").get_data(as_text=True))
    assert ("job=r2/rf-daemon.log" in page) is installed and ("job=r1/" not in page)


@pytest.mark.no_default_hardware                        # no hardware list at all (radios, none enabled: test_w_dash)
def test_home_without_hardware_points_at_add_hardware(web):
    r = web().get("/")
    page = _text(r.get_data(as_text=True))
    assert r.status_code == 200 and "No radio hardware is set up yet." in page and 'href="/hardware/add"' in page


def test_the_daemon_settings_hold_no_board_setup_any_more(web, tmp_path, monkeypatch):
    page = _box(web, tmp_path, monkeypatch).get("/stacks?open=daemon&cfg=daemon").get_data(as_text=True)
    assert 'action="/hardware"' not in page and 'name="hardware"' not in page


def test_the_row_button_disables_and_enables_a_radio_for_real(web, tmp_path, monkeypatch, csrf):
    # The real set_enabled under the route: r2 (nothing runs on it) is disabled in the saved list, then enabled again.
    from lhpc.core.paths import Paths
    c = _box(web, tmp_path, monkeypatch)
    _post(c, csrf, "/hardware/enabled", rid="r2", enabled="off")
    assert next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == "r2").enabled is False
    assert "r2 disabled" in c.get("/hardware").get_data(as_text=True)
    _post(c, csrf, "/hardware/enabled", rid="r2", enabled="on")
    assert next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == "r2").enabled is True


def test_a_running_radio_is_not_disabled_from_its_row(web, tmp_path, monkeypatch, csrf):
    from lhpc.core.paths import Paths
    c = _box(web, tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")])
    _post(c, csrf, "/hardware/enabled", rid="r1", enabled="off")
    assert "Not disabled: stop its daemon, then disable r1" in html.unescape(c.get("/hardware").get_data(as_text=True))
    assert next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == "r1").enabled is True


def test_c23_the_rebind_reason_shows_without_any_choice(web, tmp_path, monkeypatch):
    # Gate 1 C23 note 1: a USB radio with no stick to choose still shows why.
    real = radio_start.radio_detail

    def detail(svc, rid):
        d = real(svc, rid)
        if d is not None and rid == "r3":
            d["choices"] = type("C", (), {"items": [], "reason": "no stick of this model is plugged in"})()
        return d
    monkeypatch.setattr(radio_start, "radio_detail", detail)
    page = _box(web, tmp_path, monkeypatch).get("/hardware/radio/r3").get_data(as_text=True)
    assert "no stick of this model is plugged in" in page


def test_c23_a_failed_bind_of_an_unknown_radio_lands_on_a_page_that_exists(web, tmp_path, monkeypatch, csrf):
    # Gate 1 C23 note 2: the bind refuses r9 (no such radio); the redirect goes to the list, which shows the reason.
    c = _box(web, tmp_path, monkeypatch)
    r = _post(c, csrf, "/hardware/bind", rid="r9", port="1-1.3")
    assert r.headers["Location"].endswith("/hardware") and c.get(r.headers["Location"]).status_code == 200


# ---- U5 / E3-6: a Hardware form returns to the page it was pressed on, through K4's internal-URL parse ----------

def _where(resp):
    assert resp.status_code == 302, resp.status_code
    return resp.headers["Location"].removeprefix("http://localhost")


def _row_form(c, page, rid):
    """Row `rid`'s Disable/Enable fields as `page` renders them (the public path: what the button posts)."""
    r = c.get(page)
    assert r.status_code == 200, (page, r.status_code)
    doc = parse(r.get_data(as_text=True))
    rows = [t for t in doc.find("tr", class_="hwrow") if t["data-rid"] == rid]
    assert len(rows) == 1, (page, rid, len(rows))
    return {i["name"]: i["value"] for i in doc.within(rows[0]).find("input")}


@pytest.mark.parametrize("page, back", [
    ("/hardware", "/hardware"),
    ("/stacks?open=daemon", "/stacks?open=daemon#stack-hardware-daemon"),
    ("/stacks?open=daemon&radio=r1", "/stacks?open=daemon&radio=r1#stack-hardware-daemon"),
    ("/stacks/daemon/menu/stackrow-daemon", "/stacks/daemon/menu/stackrow-daemon#stackrow-daemon"),
], ids=["hardware", "stacks", "stacks-radio", "daemon-row-page"])
def test_disable_returns_to_the_page_it_was_pressed_on(web, tmp_path, monkeypatch, page, back):
    from lhpc.core.paths import Paths
    c = _box(web, tmp_path, monkeypatch)
    where = _where(c.post("/hardware/enabled", data=_row_form(c, page, "r2")))
    assert where == back
    assert next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == "r2").enabled is False
    assert "r2 disabled" in c.get(where.partition("#")[0]).get_data(as_text=True)    # its result shows there


@pytest.mark.parametrize("back", [
    "/hardware-not-a-page", "//host/x", "https://evil.example/hardware", "/hardware\\x", "/hardware?x=1",
    "/hardware/radio/r9", "/stacks?open=meshcore", "/stacks?open=daemon&radio=r9#stack-hardware-daemon",
    "/stacks?open=daemon#stack-install-daemon", "/stacks?open=daemon&x=1#stack-hardware-daemon", "/stacks",
    "//[", "http://[", "/hardware?x=[", "[",                       # gate 1 NOTE 1: urlsplit refuses `//[` (ValueError)
])
def test_an_invalid_back_returns_to_the_default_page(web, tmp_path, monkeypatch, csrf, back):
    from lhpc.core.paths import Paths
    from lhpc.core.radio_detect import run
    c = _box(web, tmp_path, monkeypatch)
    assert _where(_post(c, csrf, "/hardware/enabled", rid="r2", enabled="off", back=back)) == "/hardware"
    assert next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == "r2").enabled is False
    monkeypatch.setattr(run, "detect_radio", lambda svc, rid: run.Detected("found", "r2: found", "sx127x"))
    r = _post(c, csrf, "/hardware/detect", page="/hardware/radio/r2", kind="radio", rid="r2", back=back)
    assert _where(r) == "/hardware#detect-r2"


@pytest.mark.parametrize("back, where", [
    ("/hardware/add", "/hardware/add#detect-r2"), ("/hardware/radio/r1", "/hardware/radio/r1#detect-r2"),
    ("/hardware", "/hardware#detect-r2"),
])
def test_detect_returns_to_each_hardware_page(web, tmp_path, monkeypatch, csrf, back, where):
    from lhpc.core.radio_detect import run
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_radio", lambda svc, rid: run.Detected("found", "r2: found", "sx127x"))
    assert _where(_post(c, csrf, "/hardware/detect", page="/hardware/radio/r2", kind="radio", rid="r2",
                        back=back)) == where
