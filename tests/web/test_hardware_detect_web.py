"""DEC-W43 W3: Detect through the console. Each radio card and each addable stick on /hardware carries a Detect
button; POST /hardware/detect runs radio_detect.run and redirects back to /hardware, where the one result line
shows next to the button it was pressed on: green found, red refused, neutral not found. The route adds no guard
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


def _detect(client, csrf, **form):
    token = csrf(client, "/hardware")
    resp = client.post("/hardware/detect", data={"_csrf": token, **form})
    assert resp.status_code == 302, resp.status_code
    return resp.headers["Location"], " ".join(html.unescape(client.get("/hardware").get_data(as_text=True)).split())


def _result(page, key):
    """The result line rendered right after the Detect form `detect-<key>`: (outcome, text), or None."""
    m = re.search(rf'id="detect-{re.escape(key)}"(?:(?!</form>).)*</form>\s*<p class="depnote depnote-(\w+)" data-detect="(\w[\w-]*)">'
                  r"([^<]*)</p>", page)
    return (m.group(2), m.group(1), m.group(3).strip()) if m else None


def test_every_radio_card_has_a_detect_button(web, tmp_path, monkeypatch):
    page = _box(web, tmp_path, monkeypatch).get("/hardware").get_data(as_text=True)
    for rid in ("r1", "r2"):
        assert f'id="detect-{rid}"' in page and f'name="rid" value="{rid}"' in page, rid


def test_a_running_daemon_refuses_the_detect_and_no_bus_opens(web, tmp_path, monkeypatch, csrf):
    # The refusal is run.py's own, through the route; the bus seam proves nothing was touched.
    c = _box(web, tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")])
    monkeypatch.setattr(run, "spi_bus", lambda *a, **k: pytest.fail("the bus opened while r1's daemon runs"))
    where, page = _detect(c, csrf, kind="radio", rid="r1")
    assert where.endswith("/hardware#detect-r1")
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
    assert _result(page, "r1") is None                                   # only next to the button pressed


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
    page = c.get("/hardware").get_data(as_text=True)
    assert 'id="detect-port-1-1.3"' in page
    where, page = _detect(c, csrf, kind="stick", model="meshstick-1262", port="1-1.3")
    assert seen == [("meshstick-1262", "1-1.3")] and where.endswith("#detect-port-1-1.3")
    assert _result(page, "port-1-1.3") == ("found", "ok", "meshstick-1262 on port 1-1.3: SX1262 found (SX1261 V2D 2D02)")


def test_a_board_detect_goes_to_detect_board(web, tmp_path, monkeypatch, csrf):
    # The HAT row's Detect (Add Hardware): the route passes model, variant and band through.
    c = _box(web, tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(run, "detect_board", lambda svc, m, v, b: seen.append((m, v, b)) or run.Detected(
        "refused", "Detect refused: r1's daemon is running. Stop it first.", "sx127x"))
    where, _page = _detect(c, csrf, kind="board", model="loraham", variant="433", band="433")
    assert seen == [("loraham", "433", "433")] and where.endswith("/hardware#detect-board-loraham-433")


@pytest.mark.parametrize("form", [{"kind": "radio", "rid": "r1"}, {"kind": "nonsense"}])
def test_without_a_valid_token_or_kind_nothing_runs(web, tmp_path, monkeypatch, csrf, form):
    c = _box(web, tmp_path, monkeypatch)
    monkeypatch.setattr(run, "detect_radio", lambda svc, rid: pytest.fail("Detect ran"))
    token = csrf(c, "/hardware") if form["kind"] == "nonsense" else "wrong"
    assert c.post("/hardware/detect", data={"_csrf": token, **form}).status_code == 400
