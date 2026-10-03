# PLAN G5 — CR1-3: a daemon `ERR` to a SET is thrown away

Base: `e5187f70ae4e81a1081a835be06f84746222c3b6` (v0.11.10). Read-only on the code. A scratch prototype of
this plan (two production files and three test modules) was run against the full `tests/` suite. Results in §8.

## 1. Analysis

| step | file:line (base) | today |
|---|---|---|
| send | `lhpc/core/daemon_control.py:515` `apply_set` | `system.unix.send(sock, b"SET K=V\n", 1.0)` |
| transport | `lhpc/core/probes/backends.py:754-760` `RealUnixClient.send` | connect, `/tmp` peer check, `sendall`, close. **No read**, so the daemon's `OK`/`ERR <WHY>` line is discarded |
| unconfirmable keys | `daemon_control.py:518-521` | every key not in `_VERIFY` (POWER, FREQ, SF, BW, CR, PREAMBLE, SYNC, CRC, LDRO, …) returns `(True, False, "… SENT but UNCONFIRMED …")`, even when the daemon refused it |
| confirmable keys | `daemon_control.py:522-534` | the read-back hides the refusal (the old value is read back, so the result is a mismatch), but the reason is lost: "NOT applied — daemon reports TXMODE=MANAGED" |
| the reply exists | `daemon_control.py:38-42` (comment); brief fact: daemon v1.1.0/1.1.1/1.2.0 `config_dispatch.cpp:10-23`, `config_apply.cpp:171-203` | the daemon sends exactly one line per command, to the requesting client only. Validation runs before any hardware access. The connection stays open until the client closes it |
| a reader already exists | `backends.py:729-752` `RealUnixClient.request` | connect, peer check **before** send, `sendall`, read until the first `\n` or `max_bytes`, close. This is exactly the "send one line, read one line, close" the fix needs. **No new client method.** On a timeout it raises `TimeoutError` (`socket.timeout`, an `OSError` subclass; Python ≥ 3.11 per `pyproject.toml:10`) |
| canonical token | `daemon_control.py:511` | already `canonical_value(key, value).upper()` (CR1-8, 0.11.10). Nothing to do |

Callers, all through `apply_set` (none needs a code change, see §2):

| caller | file:line | what the operator sees today on an `ERR` |
|---|---|---|
| `_apply_tx_mode` | `service_params.py:77` | a read-back mismatch after the polling loop, without the daemon's reason |
| `_apply_conf_param` (CADWAIT/CADIDLE) | `service_params.py:112` | the same |
| `_apply_daemon_param` (radio params) | `service_params.py:146` | `(True, "… SENT but UNCONFIRMED …")` |
| start path `_apply_stack_daemon_params` | `service_lifecycle_ops.py:2051-2060` | `[ok] 433: SF=12 sent` |
| Apply live `apply_daemon_params` | `service_lifecycle_ops.py:2165-2172, 2181-2184` | the key is counted in `applied` + `sent_unconfirmed`, so the result can be "applied n/n" with `ok=True` |
| `lhpc daemon <band> --set` / web radio-set, via `daemon_set` | `service_params.py:3467-3478`, `app.py:1466-1471`, `cli/main.py:1544` | "SET SF=12: SENT (unconfirmed)." with `ok=True` and exit 0 |

What a test sees today: a fake answering `ERR INVALID` to the SET still gets `(True, False, "SF=12 SENT but UNCONFIRMED…")`.
The prototype test below reproduces this: `assert (not True)`.

Fakes:
- `FakeSystem.request`/`send` (`backends.py:875-885`): `request` answers per path only, with no notion of SET.
  `send` records into `.sent`. Nine test modules read `.sent` to check that a SET went out or was refused before the socket.
- Testlab fake daemon (`testlab/lhpc_testlab/data/loraham-daemon-fake/loraham_daemon/loraham_daemon:129-178`):
  **already answers every line `OK\n` / `ERR INVALID|MALFORMED|UNKNOWN|RADIO_NOT_READY\n`** on the requesting connection,
  like the real daemon. No change is needed. It does not range-check RF values, so LHPC's own validation means the lab cannot
  produce a refusal (see Q4).

## 2. The change (one finding, one fix)

**`daemon_control.apply_set`.** New behaviour: send the SET with `system.unix.request` and read its one reply line.
`ERR` is a failure. `OK` is required before a key without read-back counts as sent. A key with read-back is still decided by the read-back.

```text
ack = _ack(system.unix.request(sock, b"SET K=V\n", _READ_TIMEOUT, _MAX))   # TimeoutError -> ack = ""
OSError (not a timeout)        -> (False, False, "CONF socket unreachable: …")          # unchanged
ack starts "ERR"               -> (False, False, "K=V refused by the daemon (ERR INVALID)")   # no read-back
key not in _VERIFY, ack "OK"   -> (True,  False, "K=V SENT but UNCONFIRMED — accepted (OK), not reported back …")
key not in _VERIFY, no/odd ack -> (False, False, "K=V SENT but UNCONFIRMED — no OK from the daemon within 1 s …")
key in _VERIFY, OK/no/odd ack  -> read-back as today (the read-back is the stronger evidence)
```

- `_ack(raw) -> str` is a new private helper of about 4 lines. It takes the first line, capped at `_MAX_LINE` and reduced to
  printable ASCII, so the `ERR <WHY>` that reaches the console and CLI is bounded and has no control characters.
  This is the same sanitising as `read_socket_line`.
- `except TimeoutError` comes **before** `except OSError`. A timeout is "sent, no reply", not "unreachable".
- Docstring (`daemon_control.py:496-507`): the table above. `live_power_error` docstring `:428-429`: "(which refuses it
  while the unconfirmable SET is reported 'sent')" becomes "(which refuses it with `ERR`)".
- **Size:** about 15 lines in `daemon_control.py`. `RealUnixClient` is unchanged. `send` stays for the raw DATA socket (`lifecycle.py:1556`).
- **Callers: no code change.** `ok=False` on `ERR` already flows to the right place in every caller:
  - the start log shows `[warn]` for radio and CAD keys (non-gating, unchanged) and `[fail]` for TXMODE (gating, unchanged);
  - `apply_daemon_params` counts the key in `failed`, which gives PARTIAL/FAILED and `ok=False`;
  - `daemon_set` returns "FAILED" with the daemon's reason in `details`, CLI exit 1, and a `warn` flash on the web.
  The start-log override at `service_lifecycle_ops.py:2058` applies only when `ok`, so `[ok] SF=12 sent` now means the daemon
  answered `OK`.
- **`FakeSystem`** (`backends.py:798-885`) gets about 4 lines: a field `set_replies: dict[str, bytes]`. In `request`, a payload starting
  `SET ` is appended to `.sent` (as `send` did) and answered with `set_replies.get(path, b"OK\n")`.
  Every existing `FakeSystem(unix_replies=…)` test therefore keeps a daemon that says `OK`, and every `.sent` assertion keeps working.

Risk: what working path could break, and how the plan rules it out.

| risk | ruled out by |
|---|---|
| A slow `OK` (SET waits on the radio mutex during a long TX) passes the 1 s timeout, and an applied value shows as failed | It fails safe: it never shows as applied. Radio keys are non-gating at start. The brief fact says validation and BUSY answer without waiting. See Q2 for the timeout value |
| A daemon line that arrives before the ack (an unsolicited `TX=`/`CAD=` broadcast) is read as the ack | The brief fact: one reply, to the requesting client only, on a fresh connection. An odd first line counts as "no ack", which is a failure and never "applied". The GET path has the same exposure today. Q3 |
| Hand-written test fakes that answer every `request` with a STATUS line | Confirmable keys: the STATUS line is "no ack", so the read-back decides and the result is unchanged (`test_post_start.py:1790-1806, 1822-1838`, `test_daemon_bounds.py:84-98`, `test_daemon_control.py::test_mode_readback_never_scans` all stay green in the prototype). Unconfirmable keys: §3 lists the 4 fakes that need `OK` |
| `/tmp` squat defence lost | `request` peer-checks before `sendall`, exactly like `send` |
| Old daemon that does not ack | None exists: the ack predates 2a0db88 (`daemon_control.py:38-41`), and the pinned version is 1.2.0 |

## 3. Tests

| module::name | asserts | red before |
|---|---|---|
| `tests/stacks/test_daemon_control.py::test_a_daemon_err_ack_makes_apply_set_fail` (new) | `FakeSystem(set_replies={sock: b"ERR INVALID\n"})`: `apply_set(…,"SF","12")` gives `ok is False`, `confirmed is False`, `"ERR INVALID"` in detail. Also `TXMODE=DIRECT` gives `not ok` | yes, but on base it fails with a `TypeError` for the unknown `set_replies` kwarg, because the fake knob lands in the same commit. Red for the right reason is the next test |
| `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent` (new) | a stub `unix` raising `TimeoutError`, answering `b""`, or answering `b"TX=1\n"` (the stub keeps a no-op `send`): `SF=12` gives `not ok`, `not confirmed`, `UNCONFIRMED` in detail | **yes: `assert (not True)`** (verified on base) |
| `tests/web/test_daemon_params_web.py::test_apply_live_counts_a_refused_set_as_failed` (new) | `set_replies` `ERR INVALID` with READY STATUS: `apply_daemon_params("daemon","433")` gives `not ok`, `"SF" in data["failed"]`, `"SF" not in data["sent_unconfirmed"]` | yes (same `TypeError` caveat as the first row) |
| `test_daemon_control.py::test_integer_values_are_ascii_decimal_and_sent_canonical` (adapt) | it monkeypatched `fs.system.unix.send`. It now reads `fs.sent` (same assertion: `[b"SET SF=10\n"]`, then nothing more after the refused `1_0`) | — |
| `test_daemon_control.py::test_apply_set_confirms_via_readback` (comment only) | "The daemon never acks a SET" becomes "acks with OK; confirmation is the read-back" | — |
| `test_daemon_params_web.py::_echo_svc` `_Echo.request` (adapt) | answer `b"OK\n"` to a payload starting `SET `. This fixes `test_apply_live_full_success`, `…_reentrant_within_held_guard` and `…_other_band_not_blocked`, which were red in the prototype because their unconfirmable keys got a STATUS line as the "ack" | — |

Mark the two `apply_set` tests `@pytest.mark.safety` (invariant: truthful outcomes, daemon sockets).
Existing tests that must stay green unchanged include `test_daemon_setting_bounds.py:60-75`, `test_run_order.py:194-205`
(`"SF=10 sent"`), `test_high_power.py:193-303` (`.sent`) and `test_daemon_params_web.py:199-204`.

## 4. Docs / CHANGELOG

| file:line | today (now untrue) | becomes |
|---|---|---|
| `docs/architecture.md:178-179` | "The daemon answers only `GET`, so `lhpc` sends the `SET` and confirms by reading back `GET STATUS`." | "The daemon answers every line with one `OK` or `ERR <reason>`; `lhpc` reads that reply to each `SET` (an `ERR`, or no `OK` within 1 s, is a failure) and confirms a key the daemon reports back by reading back `GET STATUS`." |
| `docs/stacks/daemon.md:93-94` | "An apply is `ok` only when every SET landed; radio params are echoed by no `GET`, so they report *sent*, not confirmed." | "…every SET landed: a SET the daemon answers `ERR` (or does not answer) fails with its reason; radio params the daemon accepted with `OK` are echoed by no `GET`, so they report *sent*, not confirmed." |
| `docs/architecture.md:229-231` (Daemon sockets) | still true | — |
| `docs/stacks/daemon.md:67` "a sent 20 is 'sent', not 'confirmed'" | still true | — |
| `daemon_control.py:428-429, 496-507`; `service_params.py:3468-3469` comment "an unconfirmable radio param that was accepted" | already correct once `OK` is read | docstring only, in the fix commit |

CHANGELOG (next patch section): "- A daemon setting the daemon refuses (`ERR …`) is now shown as failed, with the daemon's
reason, in the console, `lhpc daemon --set`, Apply and the start log. Radio parameters were reported "sent" even when
the daemon rejected them."

## 5. Order and commits

One finding, so one commit: `CR1-3: read the daemon's OK/ERR reply to every SET`. It touches `lhpc/core/daemon_control.py`,
`lhpc/core/probes/backends.py` (FakeSystem only), the two test modules, the two docs and the CHANGELOG.
It depends on no other group. CR1-8 (canonical token) is already in base.

## 6. Live proof (Pi 5): needed

This path is visible to the operator on hardware. Row: daemon 1.2.0 pinned, a 433 SX127x radio, no stack on 433.

1. `lhpc daemon 433 --set SF=12 --yes` should give "SF=12: SENT (unconfirmed)." with exit 0. The daemon log shows the SET applied.
2. A SET that LHPC validates but the daemon refuses gives "SET …: FAILED." with exit 1, and the details carry `ERR <WHY>`.
   Candidate: `FREQ=900.000` on the 433 SX127x radio. LHPC admits 150–960 MHz, and the brief says the daemon validates
   before any hardware access. **Confirm this in `config_validate.cpp` before the row (Q1).**
3. The same value through the console (stack Settings → Daemon radio parameters → Apply): a `flash-warn` "PARTIAL … FREQ FAILED" with
   the `ERR` in the details. Then `lhpc start kiss` with that saved value shows `[warn] 433: FREQ=900.000 refused by
   the daemon (ERR …)` and kiss still starts (non-gating). Then reset the profile.
4. `lhpc daemon 433` afterwards shows the radio is still READY on its old frequency. A refused SET left the hardware untouched.

Record the row under `docs/live-tests/`. No RF is transmitted.

## 7. Open questions

1. **Which refusal for the live row?** Recommendation: read the pinned daemon's `config_validate.cpp` for a value LHPC
   admits and the daemon refuses (FREQ outside the SX127x module's band is the likely one). If none exists, prove step 2 by
   stopping the radio (`RADIO_NOT_READY`) with a direct `socat` SET plus the unit test, and say so in the record.
2. **Ack timeout:** keep `_READ_TIMEOUT` = 1.0 s (shared with the GETs)? Recommendation: yes. A refusal is immediate, and a
   late `OK` fails safe (shown as failed, never applied). Raise it only if the live row shows a slow `OK` during RX/TX.
3. **A first line that is neither `OK` nor `ERR`:** treat it as no ack (fail safe), or read on to the next line?
   Recommendation: treat it as no ack, as the brief specifies one line. Reading on would add a loop for a case the daemon contract excludes.
4. **Testlab:** add a scenario knob so the fake daemon refuses a SET (e.g. `flags.set_err_433="SF"` gives `ERR INVALID`)?
   Recommendation: not in this fix. The fake already acks like the real daemon, the unit tests own the behaviour (tests/README
   "one canonical owner"), and the Pi row proves the hardware path.
5. **Wording:** with `OK` now read, "SENT (unconfirmed)" could become "accepted (OK, not read back)". Recommendation: keep
   the wording in this patch (docs and tests pin it, and it is a separate operator-facing change). Revisit in a minor.

## 8. Self-check

Verified against base `e5187f7`:
- all file:line references above;
- `canonical_value` is already used at `:511`;
- `request` reads one line and peer-checks first;
- every `apply_set` caller treats `ok=False` as failure;
- `apply_daemon_params` counts `ok` as `applied`;
- the testlab fake answers `OK`/`ERR` per line.

Prototype (scratch worktree, not committed): the §2 change plus the §3 tests.
- With only the production change: 4 tests failed (`test_integer_values…`, and 3 in `test_daemon_params_web.py` from `_Echo`)
  and 2040 passed across the 13 affected modules, exactly the adaptations listed in §3.
- On base, the new tests are red; on base `test_a_set_without_an_ok…` fails with `assert (not True)`.
- Full `tests/` with the adaptations, against base in the same sandbox: 6098 passed, 98 failed. Base gives 6095 passed,
  98 failed, and the set of failing tests is **identical**. Those failures come from the environment (a scratch worktree and a
  non-Pi container: `install/`, `repo/`, …), not from this change. The 3 new tests are the difference.

Not verified (no access): the daemon sources `config_dispatch.cpp`/`config_apply.cpp`/`config_validate.cpp`
(taken from the brief); whether a SET can block longer than 1 s on the radio mutex (Q2); whether the real daemon writes any
unsolicited line to a fresh CONF connection (Q3).
