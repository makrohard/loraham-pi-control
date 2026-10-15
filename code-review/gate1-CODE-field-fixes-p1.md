# Gate 1: field-fixes-p1 (plan item P1, the GPS sky view), v2

v2 for the record: round 3 was GREEN on v1 (1d3a34a1). v2 adds the second reader's two post-audit tightenings (the
memo stamped at the start of the read; `dev` shown never None) and is rebased onto dev be97980b. See the report's top.

**Request.** Judge the one code commit below against the plan's item P1 (quoted). That means: correctness; whether
each red-before test can fail for the defect named; whether behaviour changes only where the plan says; the docs and
CHANGELOG lines; and the report's claims against the diff. The plan is in its own gate-1 rounds; only its P1 section
is implemented here.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's item P1 (plan v2, verbatim)

## P1 — D2: the sky view keeps each gpsd device's last satellite list (target 0.12.10)
- **Today.** `gps.gpsd_snapshot` (`gps.py:1018-1158`) keeps the last SKY arriving in its 2.5 s read (`:1094-1123`); the
  box's gpsd sends one every 5.00 s, so 9 of 20 polls returned `satellites: []` ("no satellites reported").
- **Why not `?POLL` (measured on e293, gpsd 3.25, NMEA0183, read-only):** POLL's cached `sky` is DOP-only (no
  `satellites`, no `time`), 15 of 15 POLLs over 7 s; satellite SKYs come in one burst, also without `time`.
- **The one rule.** The process remembers the last satellite SKY of each gpsd device (key: host, port, device path)
  with a monotonic timestamp and its `sats_used`/`sats_seen`. A window with a satellite SKY for the resolved device
  (an empty list included) replaces the memo; a window without one reports the memo when it is at most
  `NMEA_STALE_S` (20 s, `gps.py:556`) old, else `[]` as today. No host or receiver clock is used; the request, the
  read window and `gps.js` are unchanged. Process memory: `/api/gps` uses it, a one-shot `lhpc gps --monitor` run
  does not. Only caller: the Monitor (`service_params.py:1933`). Code in progress on cons/field-fixes-p1-r1 (not yet reviewed).
- **Tests** (`tests/stacks/test_gps_monitor.py`, ordinary layer; the fake gpsd now serves every connection in turn):
  (a) satellites, then none → `[s["prn"] for s in r["satellites"]] == [3, 7, 12] and r["sats_seen"] == 3` (red: `[]`);
  (b) a DOP-only SKY keeps the memo (red); (c) older than `NMEA_STALE_S` → `[]`; (d) a new or empty list wins;
  (e) another gpsd port gets none. An autouse fixture in `tests/stacks/conftest.py` clears the memo.
- **Docs** (`gps.md`, gpsd sources): the console keeps each device's last satellite list for up to 20 s; a single
  `lhpc gps --monitor` run shows only its own read. **CHANGELOG.** "The GPS sky view no longer alternates with "no
  satellites reported" when gpsd reports the satellites only every few seconds: the console keeps each receiver's
  last satellite list for up to 20 s."
- **Live row.** `/api/gps` 20× at 3 s → 0 without satellites while gpsd reports them (field 9/20). Direct NMEA: unchanged.

## Commits (full messages, `git log --reverse --format=%B`)
### 46ffa57f field-fixes-p1: the GPS sky view keeps each gpsd device's last satellite list

```text
field-fixes-p1: the GPS sky view keeps each gpsd device's last satellite list

gpsd sends a SKY with satellites only every few seconds (5.00 s on the field
box), so one 2.5 s read missed it in 9 of 20 polls and the sky view showed
"no satellites reported". gpsd_snapshot now remembers the last satellite list
per (host, port, device) with a monotonic timestamp and reports it when a read
saw no satellite SKY for that device, for at most NMEA_STALE_S (20 s). A
satellite SKY in the window, an empty one included, replaces it. The memo is
stamped at the START of the read that saw the list, so a served list is never
older than the bound says. gpsd's
?POLL cannot serve this: its cached SKY is DOP-only on gpsd 3.25 with an
NMEA0183 receiver (15 of 15 polls without satellites, measured).

The fake gpsd now serves json_lines to every connection in turn (it held the
first one until teardown), so a test can poll twice.

Red before: test_a_window_without_a_satellite_report_keeps_the_last_list and
test_a_dop_only_report_does_not_replace_the_last_list fail on the parent
([] == [3, 7, 12]; [] == [3]). test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list fails
with an end-of-read stamp (the list is served). The stale-bound, replace and
per-gpsd cases are guards that pass before and after.
```

## The code report (verbatim)

### Code report: field-fixes-p1 (plan item P1 = field decision D2, the GPS sky view)

Branch `cons/field-fixes-p1-r1` on `origin/dev` = `be97980b` (rebased from `254bbae3`, then `a8e0e5ce`; dev's new commits touch none of P1's files).

**v2, post-audit tightening** (gate 1 round 3 was GREEN on v1 = `1d3a34a1`; the second reader's two points):
(1) the memo is stamped at the START of the read that saw the list (`started`, the read's `deadline − timeout`), not at
its end, so a served list is never older than the 20 s bound says (v1: up to ~22.5 s); new guard test below.
(2) `dev` is never None where the memo is used, shown in point 1 of the 6-point block; no code change.
The code commit is now `46ffa57f`. Plan: `plans/PLAN-field-fixes-0.12.11.md` v2 (branch
`cons/field-fixes-plan` 4111787f), item P1. It implements the handler's ruling B (a server-side memo, at most 20 s
old) after a read-only measurement on the field box ruled out gpsd's `?POLL`.

## Commits

| commit | subject | files | tests (layer; rule-10 assertion) | red-before |
|---|---|---|---|---|
| 46ffa57f | field-fixes-p1: the GPS sky view keeps each gpsd device's last satellite list | lhpc/core/gps.py, tests/stacks/conftest.py, tests/stacks/test_gps_monitor.py, docs/gps.md, CHANGELOG.md | ordinary `tests/stacks`: `test_a_window_without_a_satellite_report_keeps_the_last_list` (`[s["prn"] for s in r["satellites"]] == [3, 7, 12] and r["sats_seen"] == 3`); `test_a_dop_only_report_does_not_replace_the_last_list` (PRNs `[3]`); guards `test_a_list_older_than_the_stale_bound_is_not_used` (`[]`, `None`), `test_a_satellite_report_in_the_window_replaces_the_last_list` ×2 (`[5]`; `[]`), `test_the_last_list_belongs_to_one_gpsd` (`[]`, `None`), `test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list` (`[]`, `None`) | yes, the first two, against a8e0e5ce: `assert ([] == [3, 7, 12]`, `assert [] == [3]`; the age-start test against v1 1d3a34a1 (end-of-read stamp): fails (the list is served); the other guard cases pass before and after |

**Red-before command.** A detached worktree of a8e0e5ce (and, for the age-start test, of 1d3a34a1) received the committed `tests/stacks/conftest.py` and
`tests/stacks/test_gps_monitor.py`. One line was changed for that run only: the autouse fixture's
`gps._SKY_MEMO.clear()` became `getattr(gps, "_SKY_MEMO", {}).clear()`, because the parent has no memo. Then
`python -m pytest -q -p no:cacheprovider tests/stacks/test_gps_monitor.py` → a8e0e5ce: `2 failed, 35 passed in
42.18s` (the two lines above); 1d3a34a1: `1 failed, 36 passed in 42.14s` (the age-start test). The worktree was reset (`git status --short` → 0 lines) and removed. The passes
also show that the changed fake gpsd keeps every existing case green on the parent.

**Green.** `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/stacks tests/repo` → `1845 passed,
1 skipped in 43.56s` (on 46ffa57f; v1 on 254bbae3: 1792 passed). `ruff check lhpc testlab` → `All checks passed!`. `ruff check tests --select
F,E9` → `All checks passed!`.

## Lane runs (GitHub, on v1 1d3a34a1, superseded by v2)

Under the maintainer's rule until Sat 10:00, packets land after gate 1 and the local focused checks; the lanes run on
dev per batch. These two runs were dispatched before that rule and were still running when this report was written:

| run | workflow | jobs → conclusion |
|---|---|---|
| 38822229636 | ci.yml (dispatch on the branch) | at writing: guards, pin-validation, meshcore-host, rfdecode-meshtastic, rfdecode-reticulum success; test (3.11/3.12/3.13) running |
| 38822249884 | testlab.yml (dispatch on the branch) | at writing: running |

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 12 lines   OK
docs         1 files, +6 lines; fact "for up to 20 s" -> docs/gps.md   OK
tests        6 new tests; layers tests/stacks=stacks; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

- No new module, class, abstraction or framework. One module-level dict (`_SKY_MEMO`), one `started` stamp and 7 code
  lines at the end of `gpsd_snapshot`. The request, the read window, the parser and `gps.js` are unchanged.
- Lines (`wc -l`, before → after): gps.py 1175 → 1193; tests/stacks/conftest.py 110 → 131; test_gps_monitor.py
  314 → 364; docs/gps.md 207 → 210; CHANGELOG.md 1163 → 1165.
- Dependencies of `gpsd_snapshot`, before → after: {`_gpsd_lines`, `_finite`, `_altitude`, `time.monotonic`,
  `GPSD_PROTO_MAJOR`} → the same plus `_SKY_MEMO` and `NMEA_STALE_S` (both in gps.py).
- Not a service-family module: no budget entry.

## Deviations

- Plan v2 says "+17 lines in gps.py" and "(17 net)". Measured on v2: 20 insertions, 2 deletions, +18 net (1175 →
  1193); the plan's figure was for v1 and wrong there too (+16 net).
- The fake gpsd change (serve every connection in turn) is stated in plan v2; nothing else deviates.

## The 6-point block

1. **Contracts.** `gps.gpsd_snapshot(host, port, timeout=2.5) -> dict` (gps.py:1027): same keys, types and
   early-exit rules. New: when the resolved device's window has no satellite SKY, `satellites`, `sats_used` and
   `sats_seen` come from that device's last satellite SKY, if it is at most `NMEA_STALE_S` (20.0 s, gps.py:556) old
   on `time.monotonic()`, counted from the start of the read that saw it. A satellite SKY in the window, an empty list included, always wins and replaces the memo.
   `dev` is never None there: `records` is keyed only by `rec_for`, which uses a non-empty string
   `device` tag or the one listed device path and otherwise returns None without a record (gps.py:1050-1059); the
   memo runs after `with_tpv` (record keys) proved exactly one device, as the cases with none or several return
   earlier (`ambiguous`, `gpsd-no-data`). `_SKY_MEMO` (gps.py:1024): `(host, port, device path) -> (monotonic time, satellites, sats_used, sats_seen)`.
   Stored and returned lists are copies of the satellite dicts, so a caller that mutates its result cannot change the
   memo. The only caller is `service_params.gps_monitor` (service_params.py:1933), the Monitor's display: nothing
   decides, gates or persists on it.
2. **Invariants + tests.** "Every probe is bounded" (architecture.md, Probes and status): no new I/O, so the same
   2.5 s read. "Status is reconstructed on each call": the memo feeds only the Monitor's satellite list, never
   `state`, position or fix. It is bounded at 20 s (`test_a_list_older_than_the_stale_bound_is_not_used`) and per
   gpsd (`test_the_last_list_belongs_to_one_gpsd`). A newer truth always wins
   (`test_a_satellite_report_in_the_window_replaces_the_last_list`). No GET route gains a network call: the memo is
   memory only.
3. **Failure classes.** Real signature: the tests drive the real `gpsd_snapshot` over a real loopback TCP fake, no
   stub. EIO/EACCES: no file. Ctrl-C: no cleanup path added. CLI/web: the same function. The console process (web)
   keeps the memo between requests; a one-shot `lhpc gps --monitor` process starts empty and shows only its own
   read, which the docs say. Threads: the console serves requests concurrently. A dict set/get of a tuple is atomic in
   CPython. The membership check and the read may see two different stored entries; either is a stored entry of
   that same device and at most 20 s old by its own stamp checked first, so the effect is a newer list, never a
   mixed one. Stacked conflicts: P1's files are disjoint from P2/P3/P4 except CHANGELOG.md (one bullet each).
4. **Test rules.** Red-before as above. Behaviour assertions on typed fields (PRN list, `sats_seen`), no sentence.
   No sleep, no network beyond the loopback fake. The autouse fixture clears module state for isolation and says so
   (rule 8). One test patches the public constant `NMEA_STALE_S` to 0 to age the memo without waiting.
5. **Directories + ruff.** tests/stacks and tests/repo: `1792 passed, 1 skipped`. Both ruff runs: `All checks
   passed!`. `gpsd_snapshot`'s signature is unchanged, so no caller grep was needed. Its one caller is listed in 1.
6. **Adversarial self-review.** Found and fixed:
   - The fake gpsd served only its first connection: the `json_lines` branch held it until teardown and then ended
     the thread. A second poll was never answered, which made "replace" cases pass for the wrong reason. Now it
     holds each connection until the client closes, then accepts the next.
   - A docs line overran the paragraph's wrap.
   - The plan's "+17 net" figure (see Deviations).
   Weighed and kept:
   - The memo never expires entries. Its keys are the configured gpsd host/port and that gpsd's device paths, a
     handful per box.
   - A memo-filled list carries no age marker on the page. The bound is the same 20 s the direct-NMEA path already
     uses (`NMEA_STALE_S`).
   Absolute words checked:
   - "only every few seconds" (CHANGELOG, docs) is the measured cause on the field receiver, worded as a condition.
   - "the console keeps" is true for the web process; the CLI exception is stated.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%H origin/dev` | be97980b (v1: 254bbae3) |
| code commit | `git log -1 --format=%H` | (v2) 46ffa57f |
| diff | `git diff --stat origin/dev..46ffa57f \| tail -1` | 5 files changed, 103 insertions(+), 9 deletions(-) |
| gps.py | `git diff --stat origin/dev..46ffa57f -- lhpc/core/gps.py`; `wc -l` | 22 (20+, 2−); 1175 → 1193 |
| test functions in test_gps_monitor.py | `grep -c '^def test_'` before/after | 30 → 36 (7 cases: one parametrized ×2) |
| red-before | the command above | a8e0e5ce: 2 failed, 35 passed; 1d3a34a1: 1 failed, 36 passed |
| green | `pytest -n 12 --dist loadfile tests/stacks tests/repo` on 46ffa57f | 1845 passed, 1 skipped |
| field symptom | FIELDTEST-E293 §F (20 runs of `lhpc gps --monitor --sats`) | 9 of 20 empty, SKY every 5.00 s |
| POLL measurement | python gpsd client on e293, `?POLL;` every 0.5 s for 7 s | 0 of 15 with satellites; POLL sky keys: class, device, gdop, hdop, pdop, tdop, uSat, vdop, xdop, ydop |

## The diff (`git diff be97980b 46ffa57f`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 000c87f1..ff949d4e 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -4,6 +4,8 @@
 
 Fixed
 
+- The GPS sky view no longer alternates with "no satellites reported" when gpsd reports the satellites only
+  every few seconds: the console keeps each receiver's last satellite list for up to 20 s.
 - The slow-target release check times the download of the Graywolf release the manifest pins and no longer asks
   GitHub's API for the newest one, so the API's rate limit can no longer stop a release candidate (the download itself
   still comes from GitHub).
diff --git a/docs/gps.md b/docs/gps.md
index 092d2e00..c3c5cf0d 100644
--- a/docs/gps.md
+++ b/docs/gps.md
@@ -49,9 +49,12 @@ RMC date it reads `12:34:56 (date unavailable)`), satellites used of seen, a **S
 **gpsd sources.** One gpsd connection per poll, 2.5 s total for name resolution, connects and
 reads; reports are newline-framed in a bounded buffer and a truncated or malformed report is
 dropped, never guessed. Reports are correlated **per device**; an untagged report is attributed
-only when gpsd lists exactly one device. The NMEA pane shows **gpsd's NMEA output** (pseudo-NMEA
-for a receiver gpsd runs in binary mode), combined across devices. Watching may activate a
-receiver on a gpsd running without `-n`; LHPC's own time-source gpsd runs with `-n`.
+only when gpsd lists exactly one device. gpsd sends a satellite report only every few seconds, so
+the console keeps each device's last satellite list for up to 20 s and shows it when a read sees
+none; a single `lhpc gps --monitor` run shows only what its own read saw. The NMEA pane shows
+**gpsd's NMEA output** (pseudo-NMEA for a receiver gpsd runs in binary mode), combined across
+devices. Watching may activate a receiver on a gpsd running without `-n`; LHPC's own time-source
+gpsd runs with `-n`.
 
 **Direct receiver (`nmea`).** A serial port has one reader, so the Monitor has three states:
 
diff --git a/lhpc/core/gps.py b/lhpc/core/gps.py
index ab3f25c7..05cd653a 100644
--- a/lhpc/core/gps.py
+++ b/lhpc/core/gps.py
@@ -1015,6 +1015,15 @@ def _altitude(tpv: dict) -> tuple[float | None, str | None]:
     return None, None
 
 
+# The last satellite list of each gpsd device: (host, port, device path) -> (monotonic start of the
+# read that saw it, satellites, sats_used, sats_seen). Stamped at the read's start, so the list is
+# never older than its stamp says. gpsd sends a SKY with satellites only every few seconds (5 s
+# measured with an NMEA receiver), so one 2.5 s read can miss it; a list at most `NMEA_STALE_S` old
+# fills that gap. Its own monotonic clock, no host or receiver time; process memory, so a one-shot
+# CLI run starts without it.
+_SKY_MEMO: dict = {}
+
+
 def gpsd_snapshot(host: str, port: int, timeout: float = 2.5) -> dict:
     """One bounded read of gpsd's JSON stream, collecting the latest coherent per-device state
     for the WHOLE budget (several TPV per epoch are normal; the first may carry no fix).
@@ -1023,9 +1032,11 @@ def gpsd_snapshot(host: str, port: int, timeout: float = 2.5) -> dict:
     that device; absent with several -> unattributed, never combined with anything, and it
     keeps the ambiguity visible (`nmea_ok` false). Early exit only on `DEVICES=[]` or a
     connection failure. Missing optional fields neither refresh nor clear a value, except that
-    losing a usable fix clears lat, lon and altitude.
+    losing a usable fix clears lat, lon and altitude. A window without a satellite SKY for the
+    resolved device reports that device's last list if it is at most `NMEA_STALE_S` old.
     """
-    deadline = time.monotonic() + max(0.05, timeout)
+    started = time.monotonic()                      # a SKY in this read arrived after this
+    deadline = started + max(0.05, timeout)
     out = {"ok": False, "error": "", "state": "unavailable", "devices": [], "device": None,
            "mode": None, "lat": None, "lon": None, "alt": None, "alt_kind": None,
            "time": None, "time_has_date": False, "sats_used": None, "sats_seen": None,
@@ -1153,6 +1164,13 @@ def gpsd_snapshot(host: str, port: int, timeout: float = 2.5) -> dict:
         out["alt"], out["alt_kind"] = r["alt"], r["alt_kind"]
     out["time"] = r["time"]
     out["time_has_date"] = bool(r["time"])
+    key = (host, port, dev)
+    if r["satellites"] is not None:                 # a satellite SKY in this window, even an empty one
+        _SKY_MEMO[key] = (started, [dict(s_) for s_ in r["satellites"]],
+                          r["sats_used"], r["sats_seen"])
+    elif key in _SKY_MEMO and time.monotonic() - _SKY_MEMO[key][0] <= NMEA_STALE_S:
+        _at, sats, r["sats_used"], r["sats_seen"] = _SKY_MEMO[key]
+        r["satellites"] = [dict(s_) for s_ in sats]
     out["sats_used"], out["sats_seen"] = r["sats_used"], r["sats_seen"]
     out["satellites"] = r["satellites"] or []
     return out
diff --git a/tests/stacks/conftest.py b/tests/stacks/conftest.py
index a65820c5..26822061 100644
--- a/tests/stacks/conftest.py
+++ b/tests/stacks/conftest.py
@@ -17,7 +17,8 @@ import pytest
 class _FakeGpsd:
     """A minimal gpsd: answers ?DEVICES with a device list and streams NMEA after ?WATCH.
 
-    `json_lines` serves a JSON `?WATCH` response instead of NMEA:
+    `json_lines` serves a JSON `?WATCH` response instead of NMEA, to each connection in turn (read
+    when it connects, so a test may change it between two polls):
     pass raw byte chunks, so a test can split one TPV across recv boundaries, send junk, or
     send a TPV with no fix. `silent=True` accepts the connection and says nothing, for the
     timeout path.
@@ -71,9 +72,8 @@ class _FakeGpsd:
                         except OSError:
                             return
                         time.sleep(0.01)
-                    while not self._stop.wait(0.05):
-                        pass
-                    return
+                    self._hold_until_closed(conn)    # then serve the next connection
+                    continue
                 sent = 0
                 while not self._stop.is_set():
                     for s in self.sentences:
@@ -86,6 +86,17 @@ class _FakeGpsd:
                             return
                     time.sleep(0.05)
 
+    def _hold_until_closed(self, conn):
+        """Keep the connection open, as gpsd does, until the client closes it or the fake stops."""
+        while not self._stop.is_set():
+            try:
+                if not conn.recv(4096):
+                    return
+            except TimeoutError:
+                continue
+            except OSError:
+                return
+
     def close(self):
         self._stop.set()
         try:
@@ -94,6 +105,16 @@ class _FakeGpsd:
             pass
 
 
+@pytest.fixture(autouse=True)
+def _no_satellite_memo():
+    """Isolation: `gps.gpsd_snapshot` remembers each gpsd device's last satellite list in the
+    process; every test starts and ends without one."""
+    from lhpc.core import gps
+    gps._SKY_MEMO.clear()
+    yield
+    gps._SKY_MEMO.clear()
+
+
 @pytest.fixture
 def fake_gpsd():
     """Factory for a minimal in-process gpsd. Every server it hands out is closed at teardown,
diff --git a/tests/stacks/test_gps_monitor.py b/tests/stacks/test_gps_monitor.py
index 0d9fd4cd..e2c0717a 100644
--- a/tests/stacks/test_gps_monitor.py
+++ b/tests/stacks/test_gps_monitor.py
@@ -104,6 +104,56 @@ def test_tpv_without_sky_is_a_fix_with_satellites_na(fake_gpsd):
     assert r["alt"] == 120.5 and r["alt_kind"] == "msl"
 
 
+# --- the last satellite list per device bridges a window without one -------------------------
+# gpsd sends a satellite SKY only every few seconds (5.00 s on the field box), so a 2.5 s window
+# can miss it. One fake gpsd serves both polls; its lines change between the two connections.
+
+def _two_polls(fake_gpsd, first, second):
+    srv = fake_gpsd(json_lines=[VERSION, DEV_A, _tpv(3), *first])
+    _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=1.2)
+    srv.json_lines = [VERSION, DEV_A, _tpv(3), *second]
+    return _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=1.2)
+
+
+def test_a_window_without_a_satellite_report_keeps_the_last_list(fake_gpsd):
+    r = _two_polls(fake_gpsd, [_sky([_sat(3), _sat(7), _sat(12)])], [])
+    assert [s["prn"] for s in r["satellites"]] == [3, 7, 12] and r["sats_seen"] == 3
+
+
+def test_a_dop_only_report_does_not_replace_the_last_list(fake_gpsd):
+    r = _two_polls(fake_gpsd, [_sky([_sat(3)])],
+                   [(json.dumps({"class": "SKY", "device": "/dev/ttyACM0", "hdop": 1.1}) + "\n").encode()])
+    assert [s["prn"] for s in r["satellites"]] == [3]
+
+
+def test_a_list_older_than_the_stale_bound_is_not_used(fake_gpsd, monkeypatch):
+    monkeypatch.setattr(_gps, "NMEA_STALE_S", 0.0)
+    r = _two_polls(fake_gpsd, [_sky([_sat(3)])], [])
+    assert r["satellites"] == [] and r["sats_seen"] is None
+
+
+def test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list(fake_gpsd, monkeypatch):
+    # Each poll reads for its whole 1.2 s budget. The list may have arrived at the first read's
+    # start, so at the second read's end it can be ~2.4 s old: a 1.5 s bound must refuse it (a stamp
+    # taken at the first read's END would make it look 1.2 s old and serve it).
+    monkeypatch.setattr(_gps, "NMEA_STALE_S", 1.5)
+    r = _two_polls(fake_gpsd, [_sky([_sat(3)])], [])
+    assert r["satellites"] == [] and r["sats_seen"] is None
+
+
+@pytest.mark.parametrize("second, prns", [([_sky([_sat(5)])], [5]), ([_sky([])], [])],
+                         ids=["a new list wins", "an empty list is the truth"])
+def test_a_satellite_report_in_the_window_replaces_the_last_list(fake_gpsd, second, prns):
+    r = _two_polls(fake_gpsd, [_sky([_sat(3)])], second)
+    assert [s["prn"] for s in r["satellites"]] == prns
+
+
+def test_the_last_list_belongs_to_one_gpsd(fake_gpsd):
+    _snap(fake_gpsd, DEV_A, _tpv(3), _sky([_sat(3)]))
+    r = _snap(fake_gpsd, DEV_A, _tpv(3))                      # another fake gpsd: another port
+    assert r["satellites"] == [] and r["sats_seen"] is None
+
+
 # --- collecting for the whole budget, per device ---------------------------------------------
 
 def test_a_later_tpv_in_the_session_wins(fake_gpsd):
```
