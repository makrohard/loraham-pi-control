"""The RX-TX Monitor in a real browser, its poll (/api/daemon/r3)
answered by the test. Structure: an RX / TX pill in the summary, no TX active / CAD wait cells, rows Radio | TX mode,
Uptime | Packet RSSI, CAD state | CAD RSSI, RX pkts | TX ok/err, and after Live RSSI the FREQ / PWR / BW / SF line (names
in the field-label look, a name never wraps from its value). Painting: the pill from STATUS TX; an older daemon (no
RADIOERR= in STATUS) -> STATUS FREQ / POWER / SF, BW "-"; a 1.5.0 daemon -> `radio` (GET RADIO) only while its
RADIO=READY, else "-" x4 (FAILED, UNPLUGGED, {} or no `radio` key), never STATUS; a missing value -> "-" after a value."""
import json

_READY = b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"
_BASE = {"reachable": True, "ready": True, "label": "daemon READY", "channel": {"LIVERSSI": "-115.00"}, "stats": {}, "feed": []}
_OLD = {"RADIO": "READY", "TXMODE": "MANAGED", "TX": "1", "FREQ": "433.175", "POWER": "17", "SF": "12"}
_NEW = {"RADIO": "READY", "TXMODE": "MANAGED", "TX": "0", "RADIOERR": "0", "FREQ": "433.175", "POWER": "17", "SF": "12"}
_GOT = {"RADIO": "READY", "MODE": "LORA", "FREQ": "869.525", "SF": "11", "BW": "250", "CR": "5", "PREAMBLE": "8",
        "SYNC": "0x12", "CRC": "1", "LDRO": "0", "POWER": "10"}
_SHOWN = """rid => ({freq: document.getElementById('rd-freq-' + rid).textContent, pwr: document.getElementById('rd-pwr-' + rid).textContent,
  bw: document.getElementById('rd-bw-' + rid).textContent, sf: document.getElementById('rd-sf-' + rid).textContent,
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
    want = dict(zip(("freq", "pwr", "bw", "sf"), expect, strict=True), pill=list(pill) if pill else None)
    page.wait_for_function(f"want => JSON.stringify(({_SHOWN})('r3')) === want", arg=json.dumps(want, separators=(",", ":")))


def test_the_monitor_structure_and_its_params_line(flask_page):
    api = {"/api/daemon/r3": dict(_BASE, status=_OLD)}
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api=api)
    s = page.evaluate(_STRUCT, "r3")
    assert s["pill"] and not s["tx"] and not s["cadwait"], s
    assert s["rows"] == [["Radio", "TX mode"], ["Uptime", "Packet RSSI"], ["CAD state", "CAD RSSI"], ["RX pkts", "TX ok / err"]], s
    assert s["order"] == "ok" and s["nowrap"] and s["label"], s
    assert s["names"] == [["FREQ", "rd-freq-r3"], ["PWR", "rd-pwr-r3"], ["BW", "rd-bw-r3"], ["SF", "rd-sf-r3"]], s
    page.context.close()


def test_the_pill_and_the_params_follow_the_poll(flask_page):
    api = {"/api/daemon/r3": dict(_BASE, status=_OLD)}
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api=api)
    tx = ("TX", "badge txpill tx-on")
    _step(page, api, _OLD, ("433.175", "17", "-", "12"), pill=tx)                         # older daemon: STATUS, BW "-"
    _step(page, api, dict(_OLD, TX="0", FREQ="-", POWER=None), ("-", "-", "-", "12"))     # reset to "-" after a value
    _step(page, api, _NEW, ("869.525", "10", "250", "11"), radio=_GOT)                    # 1.5.0: GET RADIO while READY
    _step(page, api, _NEW, ("-", "-", "-", "-"), radio=dict(_GOT, RADIO="FAILED"))         # never STATUS's values
    _step(page, api, _NEW, ("869.525", "10", "250", "11"), radio=_GOT)
    _step(page, api, _NEW, ("-", "-", "-", "-"), radio=dict(_GOT, RADIO="UNPLUGGED"))
    _step(page, api, _NEW, ("869.525", "10", "250", "11"), radio=_GOT)
    _step(page, api, _NEW, ("-", "-", "-", "-"), radio={})                                 # read-back unavailable
    _step(page, api, _NEW, ("869.525", "10", "250", "11"), radio=_GOT)
    _step(page, api, _NEW, ("-", "-", "-", "-"))                                           # no `radio` key at all
    _step(page, api, {k: v for k, v in _OLD.items() if k != "TX"}, ("433.175", "17", "-", "12"), pill=None)  # no TX: hidden
    page.context.close()
