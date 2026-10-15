# Code report: field-fixes-p1 (plan item P1 = field decision D2, the GPS sky view)

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
