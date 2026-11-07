"""W257 (mock v13, W258): the redesigned Hardware pages through the console. The list is a table, one row per radio,
the whole row opening its detail (the id link stretched over it, no JS), "enabled" in green, the read-only problem
notes, and the row's one Disable/Enable; the detail page carries Re-bind, Detect and Remove; Add Hardware lists the
new sticks (the model picked there) and one HAT row. The old daemon-spawn Detect is gone (DEC-W50)."""
import html
import re

import pytest
import radios_fixture

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


def test_a_problem_shows_in_the_row(web, tmp_path, monkeypatch):
    page = _text(_box(web, tmp_path, monkeypatch).get("/hardware").get_data(as_text=True))
    assert re.search(r'data-rid="r1">.*?⚠ daemon offline', page)                     # enabled, no CONF answer


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


@pytest.mark.parametrize("url,here", [("/hardware", "Hardware"), ("/hardware/radio/r2", "r2"),
                                      ("/hardware/add", "Add Hardware"), ("/hardware/remove/r2", "Remove r2")])
def test_each_hardware_page_says_where_it_sits(web, tmp_path, monkeypatch, url, here):
    # W245: Home › Apps › Daemon › Hardware [› here], each crumb but the last a link.
    page = _text(_box(web, tmp_path, monkeypatch).get(url).get_data(as_text=True))
    crumbs = re.search(r'<nav class="crumbs"[^>]*>(.*?)</nav>', page).group(1)
    assert re.findall(r">([^<>›]+)</a>", crumbs)[:3] == ["Home", "Apps", "Daemon"]
    assert crumbs.rstrip().endswith(f'<span class="here">{here}</span>'), crumbs


def test_the_daemon_entries_read_install_hardware_settings_info(web, tmp_path, monkeypatch):
    # W251: the daemon's own entries in that order, Hardware a link to its page; another stack keeps Info before Settings.
    c = _box(web, tmp_path, monkeypatch)
    page = c.get("/stacks?open=daemon").get_data(as_text=True)
    ids = [f'id="stack-{e}-daemon"' for e in ("install", "hardware", "settings", "info")]
    assert all(i in page for i in ids) and [page.index(i) for i in ids] == sorted(page.index(i) for i in ids), ids
    kiss = c.get("/stacks?open=kiss").get_data(as_text=True)
    assert kiss.index('id="stack-info-kiss"') < kiss.index('id="stack-settings-kiss"')     # unchanged elsewhere
    hw = page[page.index('id="stack-hardware-daemon"'):]
    assert 'href="/hardware"' in hw[:600]


def _hp(monkeypatch, warn=(), mismatch=None):
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "high_power_state", lambda self, band, view=None: {
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
    assert re.search(r'data-rid="r1">.*?⚠ \+20 dBm permission enabled on 433 MHz.*?data-rid="r2"', page)
    assert re.search(r'data-rid="r2">.*?⚠ high-power switch: saved on, the running daemon reports off', page)


@pytest.mark.parametrize("installed", [True, False])
def test_the_detail_page_links_the_radios_rf_log_while_a_daemon_is_installed(web, tmp_path, monkeypatch, installed):
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: installed)
    page = html.unescape(_box(web, tmp_path, monkeypatch).get("/hardware/radio/r2").get_data(as_text=True))
    assert ("job=r2/rf-daemon.log" in page) is installed and ("job=r1/" not in page)


def test_home_without_hardware_points_at_add_hardware(web, tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: False)
    page = _text(web().get("/").get_data(as_text=True))
    assert "No radio hardware is set up yet." in page and 'href="/hardware/add"' in page


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
