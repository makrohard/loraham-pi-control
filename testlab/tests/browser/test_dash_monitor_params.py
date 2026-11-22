"""The RX-TX Monitor in a real browser, its poll (/api/daemon/r3)
answered by the test. Structure: an RX / TX pill in the summary, no TX active / CAD wait cells, rows Radio | TX mode,
Uptime | Packet RSSI, CAD state | CAD RSSI, RX pkts | TX ok/err, and after Live RSSI the FREQ / PWR / BW / SF line (names
in the field-label look, a name never wraps from its value). Painting: the pill from STATUS TX; an older daemon (no
RADIOERR= in STATUS) -> STATUS FREQ / POWER / SF, BW "-"; a 1.5.0 daemon -> `radio` (GET RADIO) only while its
RADIO=READY, else "-" x4 (FAILED, UNPLUGGED, {} or no `radio` key), never STATUS; a missing value -> "-" after a value.
Home's poll asks for the radio values (?radio=1); a page without the line (Hardware) polls without it."""
import json
from urllib.parse import parse_qs, urlsplit

_READY = b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"
_BASE = {"reachable": True, "ready": True, "label": "daemon READY", "channel": {"LIVERSSI": "-115.00"}, "stats": {}, "feed": []}
_OLD = {"RADIO": "READY", "TXMODE": "MANAGED", "TX": "1", "FREQ": "433.175", "POWER": "17", "SF": "12"}
_NEW = {"RADIO": "READY", "TXMODE": "MANAGED", "TX": "0", "RADIOERR": "0", "FREQ": "433.175", "POWER": "17", "SF": "12"}
_GOT = {"RADIO": "READY", "MODE": "LORA", "FREQ": "869.525", "SF": "11", "BW": "250", "CR": "5", "PREAMBLE": "8",
        "SYNC": "0x12", "CRC": "1", "LDRO": "0", "POWER": "10"}
_SHOWN = """rid => ({freq: document.getElementById('rd-freq-' + rid).textContent, pwr: document.getElementById('rd-pwr-' + rid).textContent,
  bw: document.getElementById('rd-bw-' + rid).textContent, sf: document.getElementById('rd-sf-' + rid).textContent,
  sync: document.getElementById('rd-sync-' + rid).textContent,
  pill: (p => p.hidden ? null : [p.textContent, p.className])(document.getElementById('rd-txpill-' + rid))})"""
_STRUCT = """rid => { const mon = document.getElementById('monitor-' + rid), sum = mon.querySelector('summary');
  const line = mon.querySelector('p.rparams'), rssi = mon.querySelector('table.monrssi');
  const fields = [...mon.querySelectorAll('table')].find(t => !t.classList.contains('monrssi'));
  const th = fields.querySelector('th'), k = line.querySelector('.rpk'), cs = e => getComputedStyle(e);
  return {pill: !!sum.querySelector('#rd-txpill-' + rid), tx: !!document.getElementById('rd-tx-' + rid),
          cadwait: !!document.getElementById('rd-cadwait-' + rid),
          rows: [...fields.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('th')].map(t => t.textContent.trim())),
          order: rssi.compareDocumentPosition(line) & 4 && line.compareDocumentPosition(fields) & 4 ? 'ok' : 'bad',
          names: [...line.querySelectorAll('.rp')].map(p => [p.querySelector('.rpk').textContent, p.querySelector('.rpv').id]),
          nowrap: [...line.querySelectorAll('.rp')].every(p => cs(p).whiteSpace === 'nowrap'),
          label: ['textTransform', 'fontSize', 'fontWeight', 'color'].every(x => cs(k)[x] === cs(th)[x])}; }"""


def _step(page, api, status, expect, radio=None, pill=("RX", "badge txpill badge-running")):
    reply = dict(_BASE, status=status)
    if radio is not None:
        reply["radio"] = radio
    api["/api/daemon/r3"] = reply                                     # the next poll (every 3 s) answers this
    want = dict(zip(("freq", "pwr", "bw", "sf", "sync"), expect, strict=True), pill=list(pill) if pill else None)
    page.wait_for_function(f"want => JSON.stringify(({_SHOWN})('r3')) === want", arg=json.dumps(want, separators=(",", ":")))


def test_the_monitor_structure_and_its_params_line(flask_page):
    api = {"/api/daemon/r3": dict(_BASE, status=_OLD)}
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api=api)
    s = page.evaluate(_STRUCT, "r3")
    assert s["pill"] and not s["tx"] and not s["cadwait"], s
    assert s["rows"] == [["Radio", "TX mode"], ["Uptime", "Packet RSSI"], ["CAD state", "CAD RSSI"], ["RX pkts", "TX ok / err"]], s
    assert s["order"] == "ok" and s["nowrap"] and s["label"], s
    assert s["names"] == [["FREQ", "rd-freq-r3"], ["PWR", "rd-pwr-r3"], ["BW", "rd-bw-r3"], ["SF", "rd-sf-r3"],
                          ["SYNC", "rd-sync-r3"]], s                              # F33 (his word): SYNC after SF
    page.context.close()


def test_the_pill_and_the_params_follow_the_poll(flask_page):
    api = {"/api/daemon/r3": dict(_BASE, status=_OLD)}
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api=api)
    tx = ("TX", "badge txpill tx-on")
    _step(page, api, dict(_OLD, SYNC="0x34", FREQ="433.180"), ("433.180", "17", "-", "12", "-"), pill=tx)  # older daemon:
    #                                                   STATUS, BW/SYNC "-" (its own FREQ: never the load-time state)
    _step(page, api, dict(_OLD, TX="0", FREQ="-", POWER=None), ("-", "-", "-", "12", "-"))  # reset to "-" after a value
    _step(page, api, _NEW, ("869.525", "10", "250", "11", "0x12"), radio=_GOT)              # 1.5.0: GET RADIO while READY
    _step(page, api, _NEW, ("869.525", "10", "250", "11", "-"),                            # F33: an FSK / absent SYNC
          radio={k: v for k, v in _GOT.items() if k != "SYNC"})                             # -> "-", never the stale value
    _step(page, api, _NEW, ("-", "-", "-", "-", "-"), radio=dict(_GOT, RADIO="FAILED"))     # never STATUS's values
    _step(page, api, _NEW, ("869.525", "10", "250", "11", "0x12"), radio=_GOT)
    _step(page, api, _NEW, ("-", "-", "-", "-", "-"), radio=dict(_GOT, RADIO="UNPLUGGED"))
    _step(page, api, _NEW, ("869.525", "10", "250", "11", "0x12"), radio=_GOT)
    _step(page, api, _NEW, ("-", "-", "-", "-", "-"), radio={})                                 # read-back unavailable
    _step(page, api, _NEW, ("869.525", "10", "250", "11", "0x12"), radio=_GOT)
    _step(page, api, _NEW, ("-", "-", "-", "-", "-"))                                           # no `radio` key at all
    _step(page, api, {k: v for k, v in _OLD.items() if k != "TX"}, ("433.175", "17", "-", "12", "-"), pill=None)  # no TX: hidden
    page.context.close()


def _next_poll(page, rid):
    with page.expect_request(lambda r: urlsplit(r.url).path == f"/api/daemon/{rid}", timeout=10000) as req:
        pass
    return parse_qs(urlsplit(req.value.url).query)


def test_only_homes_poll_asks_for_the_radio_values(flask_page):
    api = {"/api/daemon/r3": dict(_BASE, status=_NEW, radio=_GOT)}
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api=api)
    assert _next_poll(page, "r3") == {"radio": ["1"]}
    page.goto(urlsplit(page.url)._replace(path="/hardware", query="", fragment="").geturl(), wait_until="load")
    assert page.locator("#rd-freq-r3").count() == 0 and _next_poll(page, "r3") == {}
    page.context.close()


def test_the_five_groups_fit_a_narrow_card(flask_page):
    # F33: with SYNC the line holds five label+value groups; on a phone each group stays whole (no break inside a
    # group) and nothing scrolls sideways
    api = {"/api/daemon/r3": dict(_BASE, status=_NEW, radio=_GOT)}
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api=api)
    page.set_viewport_size({"width": 360, "height": 800})
    page.wait_for_function("() => document.getElementById('rd-sync-r3').textContent === '0x12'")
    groups = page.evaluate("""() => [...document.querySelectorAll('#monitor-r3 p.rparams .rp')].map(p => {
        const r = p.getBoundingClientRect(); return [p.querySelector('.rpk').textContent, Math.round(r.height)]; })""")
    assert [g[0] for g in groups] == ["FREQ", "PWR", "BW", "SF", "SYNC"]
    heights = {h for _k, h in groups}
    assert max(heights) <= min(heights) + 2, groups                                   # each group on one line
    assert page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
    page.context.close()
