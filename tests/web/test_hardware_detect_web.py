"""DEC-W43 W3 on the redesigned pages (W257): Detect sits on each radio's detail page and on Add Hardware (each new
stick, the HAT row); POST /hardware/detect runs radio_detect.run and redirects back to the page it was pressed on,
where the one result line shows next to the button: green found, red refused, neutral not found. The route adds no guard
of its own: the refusal is run.py's (the daemon check first, the same answer as Remove)."""
import html
import re

import pytest
import radios_fixture

from lhpc.core import radio_daemon, radio_start, radios
from lhpc.core.radio_detect import run

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))


def _box(web, tmp_path, monkeypatch, procs=()):
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: list(procs))
    return web()


def _detect(client, csrf, page="/hardware/radio/r2", **form):
    token = csrf(client, page)
    resp = client.post("/hardware/detect", data={"_csrf": token, "back": page, **form})
    assert resp.status_code == 302, resp.status_code
    return resp.headers["Location"], " ".join(html.unescape(client.get(page).get_data(as_text=True)).split())


def _result(page, key):
    """The page's one Detect result line, rendered only where its `key`'s button sits: (outcome, css, text), or None."""
    assert f'id="detect-{key}"' in page, key
    m = re.search(r'<p class="depnote depnote-(\w+)" data-detect="([\w-]+)">([^<]*)</p>', page)
    return (m.group(2), m.group(1), m.group(3).strip()) if m else None


def test_every_radio_detail_has_a_detect_button(web, tmp_path, monkeypatch):
    c = _box(web, tmp_path, monkeypatch)
    for rid in ("r1", "r2"):
        page = c.get(f"/hardware/radio/{rid}").get_data(as_text=True)
        assert f'id="detect-{rid}"' in page and f'name="rid" value="{rid}"' in page, rid


def test_a_running_daemon_refuses_the_detect_and_no_bus_opens(web, tmp_path, monkeypatch, csrf):
    # The refusal is run.py's own, through the route; the bus seam proves nothing was touched.
    c = _box(web, tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")])
    monkeypatch.setattr(run, "spi_bus", lambda *a, **k: pytest.fail("the bus opened while r1's daemon runs"))
    where, page = _detect(c, csrf, page="/hardware/radio/r1", kind="radio", rid="r1")
    assert where.endswith("/hardware/radio/r1#detect-r1")
    assert _result(page, "r1") == ("refused", "bad", "Detect refused: r1's daemon is running. Stop it first.")


@pytest.mark.parametrize("outcome,css,line", [
    ("found", "ok", "r2: SX127x found (version 0x12)"),
    ("not-found", "info", "r2: no SX127x answered"),
])
def test_the_result_line_shows_next_to_its_button(web, tmp_path, monkeypatch, csrf, outcome, css, line):
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_radio", lambda svc, rid: run.Detected(outcome, line, "sx127x"))
    _where, page = _detect(c, csrf, kind="radio", rid="r2")
    assert _result(page, "r2") == (outcome, css, line)
    assert "r2: " not in c.get("/hardware/radio/r1").get_data(as_text=True)    # only on the page it was pressed on


def test_the_result_shows_once(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_radio", lambda svc, rid: run.Detected("found", "r2: SX127x found", "sx127x"))
    _detect(c, csrf, kind="radio", rid="r2")
    assert "r2: SX127x found" not in c.get("/hardware").get_data(as_text=True)


def test_an_addable_stick_has_detect_and_its_result(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(radio_start, "hardware_view", lambda svc, _real=radio_start.hardware_view: {
        **_real(svc), "addable": [("meshstick-1262", type("C", (), {"port": "1-1.3", "label": "USB 1a86:5512"})())]})
    seen = []

    def stick(svc, model_id, port):
        seen.append((model_id, port))
        return run.Detected("found", f"{model_id} on port {port}: SX1262 found (SX1261 V2D 2D02)", "sx1262")
    monkeypatch.setattr(run, "detect_stick", stick)
    page = c.get("/hardware/add").get_data(as_text=True)
    assert 'id="detect-port-1-1.3"' in page
    where, page = _detect(c, csrf, page="/hardware/add", kind="stick", model="meshstick-1262", port="1-1.3")
    assert seen == [("meshstick-1262", "1-1.3")] and where.endswith("#detect-port-1-1.3")
    assert _result(page, "port-1-1.3") == ("found", "ok", "meshstick-1262 on port 1-1.3: SX1262 found (SX1261 V2D 2D02)")


def test_a_board_detect_goes_to_detect_board(web, tmp_path, monkeypatch, csrf):
    # The HAT row's Detect (Add Hardware): the route passes model, variant and band through.
    c = _box(web, tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(run, "detect_board", lambda svc, m, v, b: seen.append((m, v, b)) or run.Detected(
        "refused", "Detect refused: r1's daemon is running. Stop it first.", "sx127x"))
    where, page = _detect(c, csrf, page="/hardware/add", kind="board", board="uputronics:ce1", band="868")
    assert seen == [("uputronics", "ce1", "868")] and where.endswith("/hardware/add#detect-board")
    assert _result(page, "board")[0] == "refused"


@pytest.mark.parametrize("form", [{"kind": "radio", "rid": "r1"}, {"kind": "nonsense"}])
def test_without_a_valid_token_or_kind_nothing_runs(web, tmp_path, monkeypatch, csrf, form):
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_radio", lambda svc, rid: pytest.fail("Detect ran"))
    token = csrf(c, "/hardware") if form["kind"] == "nonsense" else "wrong"
    assert c.post("/hardware/detect", data={"_csrf": token, **form}).status_code == 400


def _picked(page_html, name):
    """The option a browser shows as chosen in the rendered select `name`: the `selected` one, else the first."""
    from htmlq import parse
    doc = parse(page_html)
    sel = next(s for s in doc.find("select") if s["name"] == name)
    opts = doc.within(sel).find("option")
    return next((o for o in opts if o.has_attr("selected")), opts[0])["value"]


def test_a_detect_keeps_the_board_and_band_it_probed(web, tmp_path, monkeypatch, csrf):
    # A18: after Detect the HAT row still shows the board and band that were probed, not the first options.
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_board", lambda svc, m, v, b: run.Detected("not-found", "no SX127x answered", ""))
    first = _picked(c.get("/hardware/add").get_data(as_text=True), "board")
    board = next(v for v in ("uputronics:ce1", "uputronics:ce0") if v != first)
    token = csrf(c, "/hardware/add")
    c.post("/hardware/detect", data={"_csrf": token, "back": "/hardware/add", "kind": "board", "board": board,
                                     "band": "868"})
    page = c.get("/hardware/add").get_data(as_text=True)
    assert (_picked(page, "board"), _picked(page, "band")) == (board, "868")
    again = c.get("/hardware/add").get_data(as_text=True)            # the pick shows once, like the result line
    assert (_picked(again, "board"), _picked(again, "band")) == (first, "433")


def test_a_detect_keeps_the_stick_model_it_probed(web, tmp_path, monkeypatch, csrf):
    # A18: a stick row with two candidate models keeps the probed one; another port's row keeps its first.
    c = _box(web, tmp_path, monkeypatch)
    port = type("C", (), {"port": "1-1.3", "label": "USB 1a86:5512"})()
    other = type("C", (), {"port": "1-2", "label": "USB 1a86:5512"})()
    monkeypatch.setattr(radio_start, "hardware_view", lambda svc, _real=radio_start.hardware_view: {
        **_real(svc), "addable": [("meshstick-1262", port), ("pinedio-usb-1262", port),
                                  ("meshstick-1262", other), ("pinedio-usb-1262", other)]})
    monkeypatch.setattr(run, "detect_stick", lambda svc, m, p: run.Detected("found", f"{m} on {p}: found", "sx1262"))
    token = csrf(c, "/hardware/add")
    c.post("/hardware/detect", data={"_csrf": token, "back": "/hardware/add", "kind": "stick",
                                     "model": "pinedio-usb-1262", "port": "1-1.3"})
    from htmlq import parse
    doc = parse(c.get("/hardware/add").get_data(as_text=True))
    chosen = {}
    for sel in doc.find("select"):
        if sel["name"] == "model":
            opts = doc.within(sel).find("option")
            chosen[sel["id"]] = next((o for o in opts if o.has_attr("selected")), opts[0])["value"]
    assert chosen == {"detect-port-1-1.3": "pinedio-usb-1262", "detect-port-1-2": "meshstick-1262"}


def test_a_kept_pick_shows_once_on_the_page_that_consumes_it(web, tmp_path, monkeypatch, csrf):
    # A18, G1 NOTE 2: the pick is stored with the result, in one entry; another Hardware page that renders it
    # consumes both, so Add Hardware afterwards shows the first options and no result line.
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_board", lambda svc, m, v, b: run.Detected("not-found", "no SX127x answered", ""))
    first = _picked(c.get("/hardware/add").get_data(as_text=True), "board")
    token = csrf(c, "/hardware/add")
    c.post("/hardware/detect", data={"_csrf": token, "back": "/hardware/add", "kind": "board", "board": "uputronics:ce1",
                                     "band": "868"})
    c.get("/hardware/radio/r1")                                         # consumes the entry
    page = c.get("/hardware/add").get_data(as_text=True)
    assert (_picked(page, "board"), _picked(page, "band")) == (first, "433") and "no SX127x answered" not in page
