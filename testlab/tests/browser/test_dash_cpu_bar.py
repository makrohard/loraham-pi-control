"""The System card's CPU row has ONE bar like the rows below
(#sys-cpu-bar, width = the total CPU %, from the d.cpu.total jiffy deltas), no per-core bars; the row turns warn at
>= 70 % and crit at >= 90 %; the number keeps "N %"; a reopened card starts with an empty, neutral bar. Real browser,
/api/system answered by the test."""
import json

_BASE = {"cpu": {"cores": 2, "percore": [[10, 0, 5, 100, 0, 0, 0, 0], [90, 0, 5, 100, 0, 0, 0, 0]]},
         "mem": {"total_kb": 512 * 1024, "available_kb": 256 * 1024},
         "disk": {"root": {"free_b": 4 * 1024 ** 3, "total_b": 8 * 1024 ** 3, "free_inodes": 90000, "total_inodes": 100000,
                           "level": "ok", "reason": ""}},
         "load": [0.1, 0.1, 0.1], "uptime_s": 100, "info": {}}


def _sample(ts, busy, idle):
    d = json.loads(json.dumps(_BASE))
    d["ts"], d["cpu"]["total"] = ts, [busy, 0, 0, idle, 0, 0, 0, 0]
    return d


_STATE = """() => { const b = document.getElementById('sys-cpu-bar'), row = b && b.closest('tr');
  return {bars: document.querySelectorAll('#sysbox .sysrow-cpu .sysbar').length, cores: !!document.getElementById('sys-cpu-cores'),
          width: b ? b.style.width : null, level: row ? [...row.classList].filter(c => c.startsWith('sys-')).sort() : null,
          val: document.getElementById('sys-cpu-val').textContent}; }"""


def _wait(page, api, sample, width):
    api["/api/system"] = sample
    page.wait_for_function("w => document.getElementById('sys-cpu-bar').style.width === w", arg=width)
    return page.evaluate(_STATE)


def test_one_total_cpu_bar(flask_page):
    api = {"/api/system": _sample(1, 100, 900)}
    page, _posts = flask_page(path="/", api=api)
    page.locator("#sysbox > summary").click()
    page.wait_for_function("() => document.getElementById('sys-mem-val').textContent !== '…'")   # the baseline sample is in
    s = _wait(page, api, _sample(2, 500, 1500), "40%")                 # 400 busy of 1000: 40 %, per-core loads differ
    assert s["bars"] == 1 and not s["cores"] and s["val"] == "40 %", s
    assert "sys-warn" not in s["level"] and "sys-crit" not in s["level"], s
    s = _wait(page, api, _sample(3, 1250, 1750), "75%")
    assert "sys-warn" in s["level"] and "sys-crit" not in s["level"], s
    s = _wait(page, api, _sample(4, 2200, 1800), "95%")
    assert "sys-crit" in s["level"], s
    page.locator("#sysbox > summary").click()                           # close, then reopen: a fresh baseline
    page.locator("#sysbox > summary").click()
    page.wait_for_function("() => document.getElementById('sys-cpu-val').textContent === '…'")
    s = page.evaluate(_STATE)
    assert s["width"] == "0%" and "sys-crit" not in s["level"] and "sys-warn" not in s["level"], s
    page.context.close()
