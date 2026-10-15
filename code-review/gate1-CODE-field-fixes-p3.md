# Gate 1: field-fixes-p3 (plan item P3, LHPC's MeshCore duty guard removed), v2

**Request.** Judge the one code commit below against the plan's item P3 (quoted). That means: correctness; whether
each red-before test can fail for the defect named; whether behaviour changes only where the plan says; the docs and
CHANGELOG lines; and the report's claims against the diff. The plan is in its own gate-1 rounds; only its P3 section
is implemented here.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's item P3 (plan v2, verbatim)

## P3 — D1: remove LHPC's MeshCore duty guard (target 0.12.10 if box window 2 proves it, else 0.12.11)
- **Today.** `LoRaHAMRadio` (`loraham_radio.py`) holds the configured % over the last 5 TX: `:163, :196`, `:234-241`,
  `:291-294`, the wait in `send()` `:354-363`, `:432-433`, `transmit_wait()` `:1001-1018`; built in `app.py:56` and
  `repeater.py:126`. Field: 2,072 delays, max 27.2 s.
- **Change.** Delete all of it, the then-unused `import time` (`:33`) and the two `airtime_dutycycle=` keywords.
  About 46 lines out (estimate from the ranges), nothing added. `repeater.py:82-84` keeps feeding upstream's budget:
  `max_airtime_per_minute = airtime × 600 ms` (10 % → 6,000 ms). Manifest label: "Duty-cycle limit % (repeater modes;
  enforced by openHop over 60 s)". `loraham_radio.py` is not in the budget table: nothing to lower.
- **What enforces instead (pinned openhop_repeater 9c958e31, read).**
  - `repeater/airtime.py:97-120` `AirtimeManager.can_transmit(airtime_ms)`: keeps the TX of the last
    `window_size = 60` s (`:39`) and allows a send only if `window sum + airtime_ms <= max_airtime_per_minute`; with an
    empty window and a frame above the budget it refuses too (`return False, 1.0`). `record_tx` (`:122`) charges it.
  - The authoritative gate is per egress in `engine.schedule_retransmit` under the TX lock (`engine.py:1811`, gate `:1886-1898`; the fan-out path `:1960` calls it per radio):
    `manager.can_transmit(gate_ms)`; on refusal it logs "Packet dropped at TX time: duty-cycle exceeded" and does not
    send; after a send `_charge_egress` (`:1936`, `:2195-2234`) records it. The advisory check in `__call__`
    (`:505-517`) is skipped only for local transmissions, which still meet the TX-time gate.
  - Every repeater TX site reaches that gate: forwards via `__call__`; adverts (`main.py:1914-1917`), room adverts
    (`main.py:1722-1725`, `api_endpoints.py:7327-7331`), trace and text replies (`handler_helpers/trace.py:377`, `text.py:737-745`)
    and the hosted companion (`main.py:975-979, 1228-1229`) go through `router.inject_packet` (`packet_router.py:394`),
    which calls `repeater_handler(..., local_transmission=True)`. The direct `dispatcher.send_packet` calls at those
    sites are fallbacks only when no router exists, which a running daemon always has.
- **The chat role: a deliberate behaviour change.** Our guard is today the only limit in `chat`; afterwards only
  openhop_core's leaky bucket remains, consulted only while client-repeat is on (6e6dcf65 `node/dispatcher.py:202-213`,
  off by default), as in the MeshCore firmware. A chat node has no software duty limit: upstream's design, ruled (D1).
- **Tests.** `tests/test_loraham_radio.py`: `test_duty_cycle_pacing_state` (`:364-370`) becomes six 50-byte frames
  back-to-back inside `asyncio.wait_for(..., 5.0)` → `[m is not None for m in metas] == [True] * 6`; red today: the
  sixth waits 27.1 s (5 × 0.5417 s at 10 %). `test_repeater.py:43` (6,000 ms/min) stays: LHPC still feeds the budget;
  upstream's enforcement is its own code, proven live (part B).
- **Docs.** `docs/stacks/meshcore.md:42` → `| airtime | 10 % | duty-cycle limit of the repeater modes, enforced by
  openHop over a 60 s window; the chat mode has none (upstream's design) |`.
- **CHANGELOG.** "MeshCore: LHPC's own transmit limit is gone, which delayed repeats by up to 27 s; in the repeater
  modes openHop's budget at the configured percentage is the limit, and the chat mode has none, as in the firmware."
- **Live row (box window 2, e293, mode `chat+repeater`, 868, T-Deck Pro on Public as the witness).** Stimulus: one
  invocation of the managed meshcore-cli against the hosted companion, `meshcli -t 127.0.0.1 -p 5000 public "duty 1"
  … public "duty 6"` (six commands back-to-back). Each is injected → TX-time gate → radio.
  - **A** (`airtime` 10 = 6,000 ms/min): six "Retransmitted packet (N bytes, A ms airtime)" lines within ~10 s, six
    `ok` TX rows in `logs/rf-meshcore.log`, zero "Duty-cycle limit: delaying TX" lines, six messages on the T-Deck
    Pro. Expected A ≈ 0.5–0.7 s per frame (field mean 669 ms); Σ ≈ 4 s ≤ 6 s. (v0.12.9: the guard delays the sixth.)
  - **B** (upstream enforces): `airtime` = ceil(A / 600 ms) % (one frame fits the budget, two do not); restart, wait
    60 s (the start advert leaves the window), three messages → expected: message 1 "Retransmitted packet …", 2 and 3
    "Packet dropped at TX time: duty-cycle exceeded" + "Injected packet failed local transmission"; T-Deck Pro shows
    only message 1. Pass: ≥ 1 drop, and the "Retransmitted" airtime in any 60 s ≤ the budget (a forward in the window
    may shift which message drops). Then `airtime` back to 10, restart.
  - Airtime of the row ≤ 6 s on 868, within the ≤ 36 s/band/h rule.

## Commits (full messages, `git log --reverse --format=%B`)
### 18058658 field-fixes-p3: MeshCore drops LHPC's own transmit limit

```text
field-fixes-p3: MeshCore drops LHPC's own transmit limit

LoRaHAMRadio held the configured airtime percentage over its last five
transmissions (transmit_wait), a port of the retired meshcore-pi limiter. In
the repeater modes it ran in series with openHop's own budget at the same
percentage over 60 s, and on the field day it delayed repeats 2,072 times, by
up to 27.2 s, at about 4 % real airtime. It is removed with its parameter,
history and range check; LHPC still feeds openHop's budget from `airtime`
(max_airtime_per_minute = airtime x 600 ms), which gates every repeater
transmission at TX time (openhop_repeater 9c958e31 engine.py:1886-1898).
The chat mode now has no software duty limit unless client-repeat is on (then
openhop_core's leaky bucket applies), as openHop and the MeshCore firmware
design it; the stack doc and the setting's label say so. The removed range
check was the host's only `0 < airtime <= 100` guard, so it moves to the
config loader: an `airtime` of 0 (a repeater that never sends) is still
refused at start.

Red before: test_back_to_back_frames_go_out_without_a_duty_wait (six 50-byte
frames inside 5 s) fails on the parent with TimeoutError: the sixth waits
about 27 s. It replaces test_duty_cycle_pacing_state, which read the removed
accounting.

test_the_airtime_percentage_is_refused_outside_0_to_100 pins that refusal at
the loader (0, -1 and 100.5 refused; 0.5 and 100 accepted); on the parent the
loader accepted them and only the radio adapter refused them.
```

## The code report (verbatim)

### Code report: field-fixes-p3 (plan item P3 = field decision D1, LHPC's MeshCore duty guard removed)

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

- Deletion only, apart from one docstring line, one label, one doc row and one CHANGELOG bullet. loraham_radio.py
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

## The diff (`git diff c699bd16 18058658`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ba902936..261532f4 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -4,6 +4,8 @@
 
 Fixed
 
+- MeshCore: LHPC's own transmit limit is gone, which delayed repeats by up to 27 s; in the repeater modes openHop's
+  budget at the configured percentage is the limit, and the chat mode has none unless client-repeat is on.
 - The GPS sky view no longer alternates with "no satellites reported" when gpsd reports the satellites only
   every few seconds: the console keeps each receiver's last satellite list for up to 20 s.
 - MeshCore repeater dashboard: a restart no longer signs everyone out; the login-signing secret is kept in
diff --git a/docs/stacks/meshcore.md b/docs/stacks/meshcore.md
index 0d58c61d..2e6e064c 100644
--- a/docs/stacks/meshcore.md
+++ b/docs/stacks/meshcore.md
@@ -39,7 +39,7 @@ persistence, readiness and lifecycle. The node never drives SPI or GPIO itself.
 | `meshcore_allow` | `127.0.0.1` | who may connect to TCP 5000 (no auth); drives the managed firewall |
 | `txpower` | 14 dBm | advanced (0–20) |
 | `frequency` | blank = the preset's | Hz; an explicit value overrides the preset frequency |
-| `airtime` | 10 % | duty-cycle limit |
+| `airtime` | 10 % | duty-cycle limit of the repeater modes, enforced by openHop over a 60 s window; the chat mode has none unless client-repeat is on (upstream's design) |
 | `use_gps` | on | use the global position source |
 | `rf_log` | on | `logs/rf-meshcore.log`, written by the host's radio adapter in every mode: frames received (RSSI/SNR) or sent (`ok` after `TX_RESULT_STATUS_OK`; `unconfirmed` when the result was never learned; a refused send writes nothing). Read at the next start; Decrypt: [maintenance](../maintenance.md#rf-logs) |
 | `mode` | `chat` | see [Mode](#mode) |
diff --git a/lhpc/data/manifest.example.toml b/lhpc/data/manifest.example.toml
index b04463d9..bdf3b2be 100644
--- a/lhpc/data/manifest.example.toml
+++ b/lhpc/data/manifest.example.toml
@@ -2355,7 +2355,7 @@ main = "meshcore-node"
       default = "10"
       min = 0
       max = 100
-      label = "Duty-cycle limit %"
+      label = "Duty-cycle limit % (repeater modes; enforced by openHop over 60 s)"
       advanced = true
       # ----- Mode + the repeater's own settings (group "Repeater") -----------------------------
       # `mode` selects WHICH openhop program `python -m meshcore_host` runs; the chat rows above are
diff --git a/lhpc/data/meshcore_host/meshcore_host/app.py b/lhpc/data/meshcore_host/meshcore_host/app.py
index 404f9816..e7cdd7ae 100644
--- a/lhpc/data/meshcore_host/meshcore_host/app.py
+++ b/lhpc/data/meshcore_host/meshcore_host/app.py
@@ -53,7 +53,6 @@ class HostApp:
             syncword=cfg.syncword,
             ldro=cfg.ldro,
             enable_tx=cfg.enable_tx,
-            airtime_dutycycle=cfg.airtime,
             rf_log_path=cfg.rf_log_path if cfg.rf_log else "",
         )
         self.store: Optional[CompanionStore] = (
diff --git a/lhpc/data/meshcore_host/meshcore_host/config.py b/lhpc/data/meshcore_host/meshcore_host/config.py
index 7a7557c7..abd9246b 100644
--- a/lhpc/data/meshcore_host/meshcore_host/config.py
+++ b/lhpc/data/meshcore_host/meshcore_host/config.py
@@ -203,6 +203,8 @@ def load_config(path: str | Path) -> HostConfig:
     cfg.crc = _opt(radio, "crc", True, bool, "radio")
     cfg.enable_tx = _opt(radio, "enable_tx", True, bool, "radio")
     cfg.airtime = float(_opt(radio, "airtime", 10.0, (int, float), "radio"))
+    if not 0 < cfg.airtime <= 100:          # openHop's budget: airtime x 600 ms per minute
+        raise ConfigError("[radio] airtime must be between 0 and 100 percent")
     # The RF log switch is LHPC's enum (`"on"`/`"off"`), a hand-written bool is
     # accepted too; `on` without an absolute path is a config error, never a
     # silent off — the writer must not run unlogged when it was asked to log.
diff --git a/lhpc/data/meshcore_host/meshcore_host/loraham_radio.py b/lhpc/data/meshcore_host/meshcore_host/loraham_radio.py
index 51221855..8194481f 100644
--- a/lhpc/data/meshcore_host/meshcore_host/loraham_radio.py
+++ b/lhpc/data/meshcore_host/meshcore_host/loraham_radio.py
@@ -30,7 +30,6 @@ import os
 import re
 import stat
 import struct
-import time
 from collections import deque
 from typing import Callable, Optional
 
@@ -160,7 +159,6 @@ class LoRaHAMRadio(_LoRaRadioBase):
         reconnect_delay: float = 5.0,
         tx_result_margin: float = 1.0,
         max_packet_size: int = 255,
-        airtime_dutycycle: float = 10.0,
         noise_poll_interval: float = NOISE_POLL_INTERVAL_S,
         resolve_sockets: bool = True,
         rf_log_path: str = "",
@@ -193,7 +191,6 @@ class LoRaHAMRadio(_LoRaRadioBase):
         self.reconnect_delay = reconnect_delay
         self.tx_result_margin = tx_result_margin
         self.max_packet_size = max_packet_size
-        self.airtime_dutycycle = airtime_dutycycle
         self._noise_poll_interval = noise_poll_interval
 
         self._validate_config()
@@ -231,15 +228,6 @@ class LoRaHAMRadio(_LoRaRadioBase):
         self._connected = False
         self._pending_tx_result: Optional[asyncio.Future] = None
 
-        # Rolling duty-cycle accounting: the last 5 TX
-        # timestamps and airtimes bound the on-air share to `airtime_dutycycle` percent.
-        # DELIBERATELY separate from openHop's dispatcher TX budget: that leaky bucket
-        # is active only while client-repeat is enabled (prefs.client_repeat), while
-        # this limiter is the LHPC-configured regulatory backstop that must hold for
-        # a plain companion too. With client-repeat off it is the only governor.
-        self._airtime_txtimestamp: deque = deque([0.0] * 5, maxlen=5)
-        self._airtime_txtime: deque = deque([0.0] * 5, maxlen=5)
-
         # Optional observer: called with (connected: bool, tx_ready: bool) on every
         # connection state change, for host-level readiness reporting.
         self.on_link_state: Optional[Callable[[bool, bool], None]] = None
@@ -288,10 +276,6 @@ class LoRaHAMRadio(_LoRaRadioBase):
                 or not isinstance(self.tx_result_margin, (int, float))
                 or self.tx_result_margin < 0):
             raise ValueError("tx_result_margin must be zero or positive")
-        if (isinstance(self.airtime_dutycycle, bool)
-                or not isinstance(self.airtime_dutycycle, (int, float))
-                or not 0 < self.airtime_dutycycle <= 100):
-            raise ValueError("airtime_dutycycle must be between 0 and 100 percent")
 
     # ------------------------------------------------------------------
     # openHop LoRaRadio contract
@@ -351,17 +335,6 @@ class LoRaHAMRadio(_LoRaRadioBase):
                 self._request_reconnect()
                 return None
 
-            # Rolling duty-cycle guard: pace rather than refuse, so the mesh sees a
-            # delayed packet instead of a lost one.
-            wait_s = self.transmit_wait()
-            if wait_s > 0:
-                logger.info("Duty-cycle limit: delaying TX %.2f s", wait_s)
-                await asyncio.sleep(wait_s)
-                if self._data_writer is None or self._data_writer.is_closing() \
-                        or not self._tx_ready:
-                    logger.warning("Daemon link changed during duty-cycle wait; packet not sent")
-                    return None
-
             airtime_ms = self._calculate_airtime_ms(len(data))
             timeout = self._cadwait_s + (airtime_ms / 1000.0) + self.tx_result_margin
 
@@ -429,8 +402,6 @@ class LoRaHAMRadio(_LoRaRadioBase):
             if status == TX_RESULT_STATUS_OK:
                 # OK also covers send-after-CAD-timeout (flag 0x04): transmitted.
                 self._rflog.tx("ok", data)
-                self._airtime_txtimestamp.append(time.monotonic())
-                self._airtime_txtime.append(airtime_ms)
                 logger.debug(
                     "TX ok: %d bytes, airtime %.1f ms, flags=0x%02X seq=%d",
                     len(data), airtime_ms, flags, seq,
@@ -972,8 +943,8 @@ class LoRaHAMRadio(_LoRaRadioBase):
         """LoRa packet airtime in milliseconds.
 
         Uses openHop's RadioLib-matched helper — the SAME formula the Dispatcher
-        applies for its airtime maths — so the TX_RESULT timeout and the duty
-        accounting agree with the rest of the process. The local Semtech-formula
+        applies for its airtime maths — so the TX_RESULT timeout agrees with the
+        rest of the process. The local Semtech-formula
         fallback exists only for openhop-less standalone use.
         """
         if calculate_lora_airtime_ms is not None:
@@ -998,25 +969,6 @@ class LoRaHAMRadio(_LoRaRadioBase):
         )
         return (self.preamble_length + 4.25 + payload_symbols) * symbol_time * 1000
 
-    def transmit_wait(self) -> float:
-        """Suggested duty-cycle wait in seconds before the next TX (0 = go now)."""
-        tx_earliest = self._airtime_txtimestamp[0]
-        tx_period = time.monotonic() - tx_earliest
-        tx_total = sum(self._airtime_txtime)
-        if tx_earliest <= 0 or tx_period <= 0 or tx_total <= 0:
-            return 0.0
-        duty_cycle = 100 * (tx_total / 1000) / tx_period
-        for c in range(3):
-            fraction = 1 / (1 << c)
-            limit = self.airtime_dutycycle * fraction
-            if duty_cycle > limit:
-                tx_min = (
-                    tx_earliest + (tx_total / 1000) / (limit / 100) - time.monotonic()
-                ) * fraction
-                if tx_min > 0:
-                    return tx_min
-        return 0.0
-
     def get_radioconfig(self) -> tuple:
         """(freq_khz, bw_hz, sf, cr, txpower_dbm, txmaxpower_dbm)."""
         return (self.frequency // 1000, self.bandwidth, self.spreading_factor, self.coding_rate, self.txpower, self.txmaxpower)
diff --git a/lhpc/data/meshcore_host/meshcore_host/repeater.py b/lhpc/data/meshcore_host/meshcore_host/repeater.py
index 85f972d4..2bf502e5 100644
--- a/lhpc/data/meshcore_host/meshcore_host/repeater.py
+++ b/lhpc/data/meshcore_host/meshcore_host/repeater.py
@@ -144,7 +144,7 @@ class _Host:
             spreading_factor=cfg.spreading_factor, coding_rate=cfg.coding_rate,
             txpower=cfg.txpower, txmaxpower=cfg.txmaxpower, crc=cfg.crc,
             preamble=cfg.preamble, syncword=cfg.syncword, ldro=cfg.ldro,
-            enable_tx=cfg.enable_tx, airtime_dutycycle=cfg.airtime,
+            enable_tx=cfg.enable_tx,
             rf_log_path=cfg.rf_log_path if cfg.rf_log else "",
         )
         self.gps: Optional[GpsFeed] = None
diff --git a/lhpc/data/meshcore_host/tests/test_loraham_radio.py b/lhpc/data/meshcore_host/tests/test_loraham_radio.py
index d43d5e34..9f8787f0 100644
--- a/lhpc/data/meshcore_host/tests/test_loraham_radio.py
+++ b/lhpc/data/meshcore_host/tests/test_loraham_radio.py
@@ -361,13 +361,14 @@ async def test_link_state_observer(daemon):
     assert states[-1] == (False, False)
 
 
-async def test_duty_cycle_pacing_state(daemon, radio):
-    assert radio.transmit_wait() == 0.0
-    meta = await radio.send(b"x" * 50)
-    assert meta is not None
-    # One packet cannot exceed the duty budget over its own period alone;
-    # the accounting must at least have recorded it.
-    assert sum(radio._airtime_txtime) > 0
+async def test_back_to_back_frames_go_out_without_a_duty_wait(daemon, radio):
+    # The duty limit is openHop's (the repeater's 60 s budget), not the radio adapter's: six frames
+    # of about 0.54 s airtime each, sent back to back, all go out at once. A 10 % limit in the
+    # adapter would hold the sixth for about 27 s.
+    async def six():
+        return [await radio.send(b"x" * 50) for _ in range(6)]
+    metas = await asyncio.wait_for(six(), timeout=5.0)
+    assert [m is not None for m in metas] == [True] * 6
 
 
 async def test_get_radioconfig(daemon, radio):
diff --git a/lhpc/data/meshcore_host/tests/test_repeater.py b/lhpc/data/meshcore_host/tests/test_repeater.py
index 604d13a4..7768b4e4 100644
--- a/lhpc/data/meshcore_host/tests/test_repeater.py
+++ b/lhpc/data/meshcore_host/tests/test_repeater.py
@@ -77,6 +77,26 @@ def test_a_missing_presets_source_neither_fails_the_start_nor_writes(tmp_path):
     assert not (state / "radio-presets.json").exists()
 
 
+@pytest.mark.parametrize("airtime, ok", [(0, False), (-1, False), (100.5, False), (0.5, True), (100, True)])
+def test_the_airtime_percentage_is_refused_outside_0_to_100(tmp_path, airtime, ok):
+    # openHop's budget is `airtime` x 600 ms per minute: 0 would be a repeater that never sends.
+    p = tmp_path / "c.toml"
+    p.write_text(f'''
+[companion]
+name = "Chat 1"
+[identity]
+key = "{SEED}"
+[radio]
+preset = "eu_uk_narrow"
+airtime = {airtime}
+''')
+    if ok:
+        assert load_config(p).airtime == airtime
+    else:
+        with pytest.raises(Exception, match="airtime must be between 0 and 100"):
+            load_config(p)
+
+
 def test_radio_status_follows_the_link_state():
     assert rep.radio_status(True, True, True) == "ok"
     assert rep.radio_status(True, False, True) == "degraded"       # TX enabled, handshake pending
```
