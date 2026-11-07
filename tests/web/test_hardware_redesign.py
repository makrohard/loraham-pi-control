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
