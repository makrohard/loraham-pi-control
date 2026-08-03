"""System-box client state machine: focused regressions for omitted-sample sequences.

The two bugs these pin down were reproduced live in review:
  1. an omitted-net response between two net samples INFLATED the computed rate (prev.ts
     advanced while the counters did not — a two-interval byte delta divided by one interval);
  2. optional rows (Swap / Data-disk / Power) stayed visible with stale values when a later
     response omitted their source.

The state machine lives in browser JS, so the regression drives the real `system.js` under
`node` with a ~60-line DOM stub — no runtime dependency: the test SKIPS where node is absent
(the Pis) and runs on the hosted CI runners, which ship node.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web" / "static" / "system.js"

_HARNESS = r"""
'use strict';
const fs = require('fs');

// --- minimal auto-vivifying DOM ------------------------------------------------------------
const registry = new Map();
const allPolylines = [];
function makePolyline() {
  const pl = { points: '', setAttribute(k, v) { if (k === 'points') this.points = v; } };
  allPolylines.push(pl);
  return pl;
}
function makeEl(id) {
  const classes = new Set();
  const el = {
    id, textContent: '', hidden: false, style: {}, dataset: {}, children: [],
    classList: { add: c => classes.add(c), remove: c => classes.delete(c),
                 contains: c => classes.has(c) },
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
    get className() { return [...classes].join(' '); },
    get childElementCount() { return el.children.length; },
    get firstChild() { return el.children[0] || null; },
    appendChild(c) { el.children.push(c); return c; },
    removeChild(c) { el.children.splice(el.children.indexOf(c), 1); return c; },
    closest() { return makeEl(id + '-row-stub'); },
    addEventListener() {},
    querySelectorAll(sel) { return sel === 'polyline' ? el._polylines : []; },
  };
  el._polylines = id.endsWith('-spark') ? [makePolyline(), makePolyline()] : [];
  return el;
}
function byId(id) {
  if (!registry.has(id)) registry.set(id, makeEl(id));
  return registry.get(id);
}
globalThis.document = {
  getElementById: byId,
  createElement: () => makeEl('anon'),
  createTextNode: t => ({ textContent: t }),
  querySelectorAll: sel => (sel === '#sysbox polyline' ? allPolylines : []),
  addEventListener() {},
  hidden: false,
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.fetch = () => new Promise(() => {});
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};

const box = byId('sysbox');
box.open = false;                       // keep the IIFE from starting its poll loop
eval(fs.readFileSync(process.argv[2], 'utf8'));
const t = box.lhpcTest;

const out = {};

// --- sequence 1: net -> omitted net -> net must average over BOTH intervals ----------------
t.resetDynamic();
t.apply({ ts: 0, net: { rx_bytes: 0, tx_bytes: 0 } });
t.apply({ ts: 2 });                                     // omitted net sample
t.apply({ ts: 4, net: { rx_bytes: 4000, tx_bytes: 0 } });
const netVal = byId('sys-net-val');
out.rate = netVal.children.length ? netVal.children[0].children[1].textContent : '(none)';

// --- sequence 2: optional rows follow the CURRENT sample's availability --------------------
const full = {
  ts: 10,
  mem: { total_kb: 1000, available_kb: 500, swap_total_kb: 800, swap_free_kb: 700 },
  disk: { root: { total_b: 100, free_b: 50 }, runtime: { path: '/x', total_b: 10, free_b: 5 } },
  power: { source: 'hwmon-alarm', undervolt_alarm: false },
};
t.apply(full);
out.shown = ['sys-swap-row', 'sys-disk2-row', 'sys-power-row'].map(id => byId(id).hidden);
t.apply({ ts: 12 });                                    // everything omitted
out.hiddenAfterOmit = ['sys-swap-row', 'sys-disk2-row', 'sys-power-row'].map(id => byId(id).hidden);
t.apply(full);
t.resetDynamic();                                       // fresh baseline hides them too
out.hiddenAfterReset = ['sys-swap-row', 'sys-disk2-row', 'sys-power-row'].map(id => byId(id).hidden);

// --- sequence 3: the clock advances LOCALLY between polls ----------------------------------
// One sample, then the display is ticked without any further response: the seconds must move
// and both lines must stay in step, because polling once a second to move a digit costs real
// CPU on a Zero 2W and still stutters on a slow response.
let fakeNow = 1785886200000;
const realNow = Date.now;
Date.now = () => fakeNow;
t.apply({ ts: 20, time: { state: 'green', label: 'synced', epoch: 1785886200,
                          local: '2026-08-04 01:30:00', utc: '2026-08-03 23:30:00',
                          tz: 'Europe/Berlin', rtc_present: true } });
out.t0 = [byId('sys-time-val').textContent, byId('sys-time-utc').textContent];
fakeNow += 3000;                                        // three seconds pass, NO new response
t.tickClock();
out.t3 = [byId('sys-time-val').textContent, byId('sys-time-utc').textContent];
Date.now = realNow;

console.log(JSON.stringify(out));
"""


def _run_harness():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available (runs on the hosted CI runners)")
    res = subprocess.run([node, "-e", _HARNESS, "harness", str(_JS)],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_net_rate_averages_over_omitted_sample():
    # 4000 B over ts 0->4 with an omitted-net response at ts 2: the true average is 1.0 kB/s.
    # The regression value would be 2.0 kB/s (two-interval delta / one interval).
    assert _run_harness()["rate"] == "1.0 kB/s"


def test_optional_rows_follow_current_sample():
    out = _run_harness()
    assert out["shown"] == [False, False, False]            # all revealed by valid data
    assert out["hiddenAfterOmit"] == [True, True, True]     # omitted source -> hidden again
    assert out["hiddenAfterReset"] == [True, True, True]    # fresh baseline hides them too


def test_the_clock_advances_between_polls_without_a_request():
    """The seconds move from a LOCAL 1 Hz timer anchored on each response — polling every second
    to animate a digit would have doubled `/api/system` (2.3% of a core on a Zero 2W, measured)
    for something cosmetic, and would still stutter whenever a response was slow."""
    out = _run_harness()
    assert out["t0"] == ["2026-08-04 01:30:00 Europe/Berlin", "2026-08-03 23:30:00 UTC"]
    assert out["t3"] == ["2026-08-04 01:30:03 Europe/Berlin", "2026-08-03 23:30:03 UTC"], (
        "both lines must advance together, with no new sample")
