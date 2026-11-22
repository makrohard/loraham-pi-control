"""On the RF log page (/api/rflog stubbed, all columns ticked) a long payload
wraps instead of being cut, nothing scrolls sideways at 1920; a hex cell holds 16 bytes per line (lines =
ceil(bytes / 16)), breaking only between two bytes; the ascii cell takes the rest of the row and is at least 12rem
(also at 1024, where the row is too tight for its share)."""
import json

_MSG = "APRS>LORA:!4807.38N/01131.00E-LoRaHAM test beacon r1 433.175 MHz with a longer comment"
_REC = {"key": "k1", "ts": "2026-11-22T22:10:00Z", "dir": "rx", "rssi": -70.5, "snr": 9.25, "len": len(_MSG), "outcome": "ok",
        "summary": _MSG[:24], "hex": _MSG.encode().hex(), "ascii": _MSG, "raw": "x"}
_M = """() => { const row = document.querySelector('#rfview tbody tr'); const tds = [...row.children].filter(t => !t.hidden);
  const hex = row.querySelector('td.rf-hex'), asc = [...row.querySelectorAll('td.rf-payload:not(.rf-hex)')][0];
  const lh = parseFloat(getComputedStyle(hex).lineHeight) || 1.2 * parseFloat(getComputedStyle(hex).fontSize);
  const r = document.createRange(); r.selectNodeContents(hex.firstChild);
  const lines = [...r.getClientRects()].filter(q => q.width), per = [];
  let pos = 0; const text = hex.textContent;
  for (const q of lines) { let n = 0; while (pos + n < text.length) { const rr = document.createRange(); rr.setStart(hex.firstChild, pos + n);
    rr.setEnd(hex.firstChild, pos + n + 1); const b = rr.getBoundingClientRect(); if (Math.abs(b.top - q.top) > 2) break; n++; } per.push(n); pos += n; }
  return {per, ascii: Math.round(asc.getBoundingClientRect().width), rem: parseFloat(getComputedStyle(document.documentElement).fontSize),
          scroll: document.documentElement.scrollWidth - innerWidth, shown: tds.length}; }"""


def test_payload_cells_wrap_and_hex_has_16_bytes_per_line(flask_page):
    job = "r1/rf-daemon.log"
    page, _posts = flask_page(path=f"/logs/loraham-daemon?job={job}&radio=r1",
                              api={"/api/rflog/loraham-daemon": {"records": [_REC], "running": True, "error": ""}})
    cols = dict.fromkeys(("ts", "dir", "rssi", "snr", "len", "outcome", "summary", "hex", "ascii", "decoded"), True)
    page.evaluate("([k, v]) => localStorage.setItem(k, v)", ["lhpc.rflog2." + job, json.dumps({"cols": cols})])
    page.set_viewport_size({"width": 1920, "height": 1000})
    page.reload(wait_until="load")
    page.wait_for_function("() => document.querySelector('#rfview tbody tr td.rf-hex')")
    m = page.evaluate(_M)
    n = len(_MSG.encode())
    assert m["per"] == [32] * (n // 16) + ([2 * (n % 16)] if n % 16 else []) and len(m["per"]) == -(-n // 16), m
    assert m["ascii"] >= 12 * m["rem"] and m["scroll"] <= 0, m
    page.set_viewport_size({"width": 1024, "height": 1000})
    m = page.evaluate(_M)
    assert m["ascii"] >= 12 * m["rem"], m
    page.context.close()
