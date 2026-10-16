# Code report: field-fixes-p3 (plan item P3 = field decision D1, LHPC's MeshCore duty guard removed)

Branch `cons/field-fixes-p3-r1`, stacked on P2's code commit `c699bd16` (P2 on `origin/dev` = `78c9fac7`; P2 is in
review). It rebases onto dev once P2 lands, and shares `repeater.py`, the manifest, `meshcore.md` and CHANGELOG with
P2 in different hunks. Plan: `plans/PLAN-field-fixes-0.12.11.md` v2 (gate 1 GREEN WITH NOTES), item P3.

**v2 (the second reader's two points, before landing).** (1) The chat-mode claim is conditional: openhop_core's
leaky bucket applies while client-repeat is on, so the doc row and the CHANGELOG say "none unless client-repeat is
on". (2) The removed range check was the host's only `0 < airtime <= 100` guard (the manifest allows `min = 0`), so
an `airtime` of 0 would have started a repeater with a 0 ms budget that never sends. The check moves to the host's
config loader (config.py, after `airtime` is read), refusing in every role as the radio adapter did. There is no
refusal change: a saved 0 was refused at start before and still is. New test
`test_the_airtime_percentage_is_refused_outside_0_to_100` (0, -1, 100.5 refused; 0.5, 100 accepted).

## Commits

| commit | subject | files | test (layer; rule-10 assertion) | red-before |
|---|---|---|---|---|
| 18058658 | field-fixes-p3: MeshCore drops LHPC's own transmit limit | meshcore_host/loraham_radio.py, meshcore_host/app.py, meshcore_host/repeater.py, meshcore_host/config.py, meshcore_host/tests/test_loraham_radio.py, meshcore_host/tests/test_repeater.py, manifest.example.toml (label), docs/stacks/meshcore.md, CHANGELOG.md | in-package `meshcore_host/tests`: `test_back_to_back_frames_go_out_without_a_duty_wait`: six 50-byte frames (541.7 ms airtime each at the harness radio) sent back to back inside `asyncio.wait_for(..., 5.0)` → `[m is not None for m in metas] == [True] * 6` | yes, on c699bd16 with the committed test file: `TimeoutError`, `1 failed, 44 passed in 14.25s` (the guard holds the sixth frame for about 27 s) |

It replaces `test_duty_cycle_pacing_state`, which read the removed accounting (`transmit_wait()`,
`_airtime_txtime`). `tests/test_repeater.py::test_upstream_config_is_translated_from_lhpcs_one_file` is unchanged.
It still asserts `max_airtime_per_minute == 6000` for `airtime = 10`, so LHPC still feeds openHop's budget.

**Red-before command.** A detached worktree of c699bd16 received the committed
`lhpc/data/meshcore_host/tests/test_loraham_radio.py`. In `lhpc/data/meshcore_host`, the interpreter with the
pinned openhop_core 6e6dcf65 and openhop_repeater 9c958e31 ran `python -m pytest -q -p no:cacheprovider
tests/test_loraham_radio.py` → `1 failed, 44 passed in 14.25s` (`TimeoutError`). v2, with v2's
`tests/test_repeater.py` too: `4 failed, 55 passed in 14.20s` (the duty test and the three refused airtime cases). The worktree was reset and removed.

**Green** (on 18058658):
- the host suite `tests/` → `144 passed, 3 skipped in 25.30s` (3 skips need the MeshCore client library);
- `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/stacks tests/repo` → `1846 passed, 1 skipped
  in 43.47s`, and `tests/web tests/cli` → `1184 passed in 46.13s` (the manifest label renders in Settings);
- `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`.

Lanes: none per packet (the maintainer's rule until Sat 10:00). The live row runs in box window 2 and does not gate
this packet's gate 1.

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  c699bd16..HEAD
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 22 lines   OK
docs         1 files, +1 lines; fact "enforced by openHop over a 60 s window" -> docs/stacks/meshcore.md   OK
tests        2 new tests; layers lhpc/data/meshcore_host/tests=in-package; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

- Deletion, plus (v2) the host config loader's `0 < airtime <= 100` check (2 lines, moved from the removed adapter check) and its test, one docstring line, one label, one doc row and one CHANGELOG bullet. loraham_radio.py
  1022 → 974 (`git diff --stat`: 52 lines changed, 2 added, 50 removed); app.py 179 → 178; repeater.py 298 → 298 (a
  keyword removed from one line).
- Removed (at c699bd16 line numbers): the `airtime_dutycycle` parameter and attribute, the two history deques and
  their comment, the range check in `_validate_config`, the wait in `send()`, the history append after
  `TX_RESULT_STATUS_OK`, `transmit_wait()`, and `import time` (no other use).
- Dependencies of `LoRaHAMRadio.send`, before → after: `self.transmit_wait`, `asyncio.sleep`, `time.monotonic`, the
  two deques → none of these. Everything else is unchanged.
- Not a service-family module: no budget entry to lower.

## Deviations

None from plan v2. The plan's "About 46 lines out" was an estimate; measured: 50 removed, 2 added in
loraham_radio.py (one docstring sentence reworded).

## The 6-point block

1. **Contracts.**
   - `LoRaHAMRadio.__init__` no longer takes `airtime_dutycycle`. Its two callers (`app.py:56`,
     `repeater.py:147`, at c699bd16) dropped it; `grep -rn 'transmit_wait\|airtime_dutycycle\|_airtime_txt' --include=*.py .` →
     0 hits.
   - `send(data)` returns the same `{"airtime_ms", "tx_flags", "tx_seq"}` or `None`. It no longer sleeps before the
     write, and it no longer returns `None` for "daemon link changed during duty-cycle wait" (that wait is gone).
   - `HostConfig.airtime` (config.py) is refused outside `0 < airtime <= 100` at load (v2, moved from the adapter's
     removed `_validate_config`). It still feeds `duty_cycle.max_airtime_per_minute` (repeater.py,
     `build_upstream_config`), and the chat role no longer reads it.
2. **Invariants + tests.**
   - "Nothing transmits by itself" (architecture.md, Daemon control): unchanged, the adapter only sends what openHop
     hands it.
   - The repeater's budget is fed: `test_upstream_config_is_translated_from_lhpcs_one_file` (6000 ms/min at 10 %).
   - That openHop enforces it is upstream's code (plan v2 P3, the pinned gate at `engine.py:1886-1898`), proven
     live in part B of the box row.
   - The chat role has no software duty limit: a deliberate change, ruled (D1), stated in the doc row and the
     CHANGELOG.
3. **Failure classes.**
   - Real signature: the test drives the real `send()` against the suite's fake LoRaHAM daemon, with real
     TX_RESULT frames.
   - EIO/EACCES: no file path touched.
   - Ctrl-C/cancel: the `CancelledError` branch of `send()` is unchanged, and one await fewer precedes the write.
   - CLI/web/job/boot: the host is started the same way by all of them.
   - Stacked: P2's files as stated at the top; the rebase onto c699bd16 applied cleanly.
4. **Test rules.** Red-before as above. Typed result (`meta is not None` per frame), no sentence. The 5 s bound is
   the only timing in the test, against a red that waits about 27 s: a margin of more than five times. No private
   read (the old test's `_airtime_txtime` read is gone), no network.
5. **Directories + ruff.** As in Green. The constructor's signature changed: the grep in 1 lists every caller.
6. **Adversarial self-review.** Found and fixed:
   - `_calculate_airtime_ms`'s docstring still claimed it fed "the duty accounting" → reworded to the TX_RESULT
     timeout alone.
   - The section comment `# Health / duty cycle / misc` in the test file stays: the new test is about duty.
   Checked:
   - `deque` is still used (`_rx_queue`).
   - No other module imports `transmit_wait`.
   - The manifest label change renders in Settings: tests/web green.
   Absolute words: "none unless client-repeat is on" (v2) is the condition the pinned openhop_core states
   (`node/dispatcher.py:202-213`).

## Live row (box window 2; not yet run)

The plan's P3 row, unchanged:
- Setup: mode `chat+repeater` on e293, 868, with the T-Deck Pro as the witness.
- Part A at `airtime` 10: six `meshcli … public` messages give six "Retransmitted packet" lines and 0 "delaying TX".
- Part B at `airtime` ceil(A / 600 ms) %: one message sent, then "Packet dropped at TX time: duty-cycle exceeded".
- The result goes into this packet's record when it runs.

## Numbers, measured

| figure | command | output |
|---|---|---|
| parent | `git rev-parse --short 18058658~1` | c699bd16 |
| diff | `git diff --stat c699bd16 18058658 \| tail -1` | 9 files changed, 37 insertions(+), 61 deletions(-) |
| loraham_radio.py | `wc -l` before/after; `git diff --stat` | 1022 → 974; 52 (2+, 50−) |
| guard references left | `grep -rn 'transmit_wait\|airtime_dutycycle\|_airtime_txt' --include=*.py . \| wc -l` | 0 |
| frame airtime / red wait | `_calculate_airtime_ms(50)` at the harness radio; 5 × 0.5417 s / 0.10 | 541.7 ms; 27.1 s |
| red-before | the command above; v2 adds test_repeater.py | v1: 1 failed, 44 passed (TimeoutError); v2 (both files): 4 failed, 55 passed |
| green | the commands above | host 144 passed, 3 skipped; stacks+repo 1846 passed, 1 skipped; web+cli 1184 passed |
| field | FIELDTEST-E293 §B1 | 2,072 delays, max 27.2 s, at ~4 % airtime |
