# PLAN (retrospective) v3 — 0.11.10 code-review fixes

Base: `origin/main` = `e5187f70` (release v0.11.10). v2 is `code-review/PLAN-retro-0.11.10-v2.md` on `code-review/brief`; its
review is `code-review/VERDICT-PLAN-retro-v2.md` there (F1, F4, F6, F7 OK; F2, F3, F5, F8 one finding each). v3 keeps the 36
verdicts, v2's two corrections and F1/F4/F6/F7 word for word, and reworks F2, F3, F5 and F8. No code changes here. Bare
paths are under `lhpc/core/` (`app.py`, `cli/main.py` under `lhpc/adapters/`). Line numbers are at `e5187f70`.

**Method.** Every cited site re-read at `e5187f70`; Python 3.11.15 scratch scripts (deleted) ran the `parse_line` matrix,
the `settle_on_raise` re-raise shape and an introspection of `ControllerService`. **Not verified:** the full suite, hardware,
systemd, the Pyodide demo, a live `hidepid` box, and what a `pinned` source update does to a binary-covered component (F7).

## 1. The 36 verdicts (unchanged)

The per-item text of v1 stands, with two factual corrections below. Verdicts:

| # | id | verdict | | # | id | verdict | | # | id | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CR7-2 | incomplete | | 13 | CR2-5 | complete | | 25 | CR4-6 | incomplete |
| 2 | CR7-11 | complete | | 14 | CR7-9 | complete | | 26 | CR8-1 | complete |
| 3 | CR7-8 | complete | | 15 | CR4-4 | complete | | 27 | CR4-3 | complete |
| 4 | CR7-16 | complete | | 16 | CR8-4 | complete | | 28 | CR5-3 | complete |
| 5 | CR7-7 | complete | | 17 | CR8-2 | complete | | 29 | CR9-1 | complete |
| 6 | CR1-8 | complete | | 18 | CR8-3 | complete | | 30 | CR9-4 | complete |
| 7 | CR1-5 | complete | | 19 | CR7-15 | complete | | 31 | CR9-8 | complete (read only) |
| 8 | U-2 | complete | | 20 | CR4-8 | complete | | 32 | CR9-9 | complete |
| 9 | CR2-1 | complete | | 21 | CR6-9 | complete | | 33 | CR9-10 | complete |
| 10 | CR1-4 | incomplete | | 22 | CR6-7 | complete | | 34 | CR9-11 | incomplete |
| 11 | CR1-6 | complete | | 23 | CR6-1 | complete | | 35 | CR9-13 | complete |
| 12 | U-1 | complete | | 24 | CR7-14 | complete | | 36 | CR10-7 | complete |

**Corrections to v1's side-effect notes (verdicts unaffected):**
- #22 CR6-7. v1 said "I did not verify whether `firewall check` reports that drift". It does, and I ran it. The canonical snapshot
  is still the *old* one after a failed restore (`firewall_helper.py:1349-1364` never promotes). So `op_check` (`:1168-1172`)
  compares the live table with the old model and writes `mismatch`. See F1 for the one gap that remains.
- #26 CR8-1. v1 said the refusal at `service_maintenance.py:1759+` limits the CLI harm. It does not for a component target:
  `:1744` and `:1760` call `on_binary_channel(target)` with the raw target too, so a component of a binary-installed stack never
  reaches either branch. F7 fixes the default choice. The explicit-selector branches stay as they are (see F7, what else).

## 2. Follow-ups

Order: F1–F2 correctness, F3–F6 the four incomplete verdicts, F7–F8 consistency. One commit and regression test each.

### F1. CR6-7 — recovery contract when the failed-apply restore itself fails
- **Guarantee.** After a failed apply whose restore or teardown failed, firewall state counts as *indeterminate* until a live
  readback proves otherwise. No check, boot load or mutation treats it as healthy. The next operation can retry.
- **Contract (no new mechanism needed — verified).** The durable recovery record is the canonical snapshot. A failed apply never
  promotes the staged snapshot (`:1349`). So after a failed restore, `etc/snapshot.json` is still the last accepted state, and
  every next step derives from it:
  - `op_check` (`:1145`): re-verifies live against it. It writes `verified` only when the live table equals the accepted model
    (`:1168-1172`). Run: after a failed restore with live = the new ruleset, check returns EXIT_FAIL with `mismatch`. Once live
    matches again, it returns `verified`.
  - `op_load` (boot, `:1177`): reloads it. That is the retry, under the same ownership guard.
  - `op_apply`: the journal is gone, so `recover()` returns True (`:1047-1048`). The apply runs normally from the accepted
    snapshot. Run: a re-apply of c1 succeeds.
  - Consumers gate on a fresh `verified` receipt only (`service_firewall.py:233-235`, `:725`). The failed apply writes `error`
    (`:1363`), so nothing reads healthy in between.
  - First install (no old snapshot, table left in place): `op_check` reports "no accepted snapshot" (`:1157-1163`), and `op_apply`
    refuses with "reset first" (`:1307-1313`). Fail closed.
  - Why not keep the journal: the next `op_check` would then mutate the table through `_recover_apply` (`:1083-1095`). A
    separate marker would only duplicate what the snapshot comparison already derives.
- **The one gap (the change).** `firewall_helper.py:1358-1361`: in the first-install branch, `undo` stays "previous ruleset
  restored" when `live_table_state` returns `error` or `not-owned`. The helper does not know whether the table is gone. Change:
  `if st != "absent" and not (st == "ours" and <destroy rc 0>): undo = f"previous ruleset NOT restored ({st})"`, i.e. claim
  "restored" only for `absent` or a successful destroy. ≤ 4 lines, one function (`op_apply`).
- **Tests** (`tests/host/test_firewall.py`):
  - `test_failed_first_install_whose_table_state_is_unreadable_does_not_claim_restored`: no snapshot; verify mismatches; the
    post-failure `nft -j list tables` returns rc 1. Assert the receipt detail does not contain "restored" unless it says "NOT
    restored". **RED before** (run: detail was "apply failed at mismatch; previous ruleset restored").
  - `test_failed_restore_is_never_verified_until_live_matches_and_apply_retries`: the existing CR6-7 scenario, with live set to
    the c2 listing when the restore fails. Then `op_check` → EXIT_FAIL, verdict `mismatch`; live back to c1 → `op_check` →
    EXIT_OK; `op_apply(c1)` → EXIT_OK. This is a **guard** (green before, run). It pins the contract so a later change such as
    promoting on failure, or a check that trusts the last receipt, goes red.
- **What else could break.** Only the receipt wording changes. The service reads `verdict`, not `detail`
  (`service_firewall.py:233`). Ruled out by grep: no test or code matches "previous ruleset restored" except the CR6-7 test, which
  asserts its absence. **Size:** trivial.

### F2. U-1 / CR1-6 — exception-safe settlement of a reserved attempt
- **Guarantee.** Every exit after `jobresult.reserve` spawns a child or makes one best-effort `failed` write; an unexpected
  `Exception` then escapes as **the same object**, unchained, whether that write succeeds, fails or raises.
- **When storage fails, "terminal" is not guaranteed** — only fail-closed and recoverable. The attempt stays `starting` with
  `startup_unverified` and no live child, which the projection derives as **unsafe** (`service_maintenance.py:1095-1096`):
  red in the banner, blocking new web jobs on that source (`service_lifecycle_ops.py:3336-3355`), turned into `failed` by
  Recover (`service_maintenance.py:1349-1350`). A `KeyboardInterrupt` in the window ends the same way today.
- **Verified gap** (as v2): the U-1 secondary attempt stays `starting` after the raise (run); `_real_spawn`'s
  `open_log_append` (`lifecycle.py:220`) can raise `PathContainmentError` (`paths.py:34`), which `spawn_job` does not catch
  (`:1473-1476`). Sites: `_spawn_install` (`service_lifecycle_ops.py:3456-3470`), `_spawn_build` (`:3472-3512`),
  `spawn_start_job` (`:3613-3634`).
- **Change.**
  1. `jobresult.py`, next to `terminalize` (`:243`), a context manager of about 12 lines (`@contextlib.contextmanager`):
     ```python
     def settle_on_raise(paths, log, attempt_id):
         try:
             yield
         except Exception as exc:
             why = "write refused"
             try:
                 ok = terminalize(paths, log, attempt_id, "failed", detail=f"internal error: {type(exc).__name__}")
             except Exception as t_exc:          # never replaces exc
                 ok, why = False, repr(t_exc)
             if not ok:
                 print(f"lhpc: attempt {log} not settled ({why}); reads unsafe until Recover", file=sys.stderr)
             raise                                # exc itself, unchained
     ```
     Bare `raise` re-raises `exc` itself (the inner handler has ended; `contextlib` passes the same object through). Run in
     isolation: `e is boom`, `boom.__context__ is None` after an inner `OSError`. The inner `try` is needed: `terminalize`
     returns False on most storage failures (`_write` `:175-183`, `_locked` `:59-71`), but `fcntl.flock` (`:74`) can raise.
     The module docstring ("NEVER raises", `:10`) names this helper as the deliberate exception. stderr as in F4.
  2. As v2: wrap the code from just after each successful `reserve` through the `if not ln or not pid` check in
     `with jobresult.settle_on_raise(...)` at the three sites. The block ends before identity capture: once a child exists, a
     `failed` write would be wrong, and `_track_or_terminate`/`_settle_track` own that phase.
  3. As v2: `lifecycle.py:1475` becomes `except (OSError, PathContainmentError)`.
- **Tests.**
  - As v2: extend `tests/web/test_webjob.py::test_spawn_web_job_secondary_raise_releases_its_admission` (**RED before**, run),
    add `test_spawn_start_job_raise_before_spawn_settles_the_attempt`, and the `tests/core/test_lifecycle.py` case.
  - `tests/core/test_jobresult.py::test_settle_on_raise_reraises_the_original_when_settlement_fails`, three cases on a
    reserved attempt: (a) `terminalize` monkeypatched to raise `OSError("disk")`; (b) it returns False. In both, the block
    raises `boom = RuntimeError("x")`. Assert `excinfo.value is boom`, `boom.__context__ is None`, and that stderr (`capsys`)
    names the log. In (b) also assert that `read_one` still shows `starting` and that `_project_job` gives `unsafe`, which
    pins the fallback above. (c) settlement succeeds: `boom` escapes and the marker is `failed`.
- **What else could break.** No double write (terminalize-then-return paths do not raise). Not covered (pre-existing):
  `log.close()` raising after `Popen` (`lifecycle.py:227`). **Size:** small (≈ 25 lines + 4 tests).

### F3. CR9-11 — `parse_line` is defensive at the JSON boundary; every record is strict JSON
- **Guarantee (narrowed to what is tested).** For any `str` line, `parse_line` returns a record and `json.dumps(record,
  allow_nan=False)` succeeds. Out of scope: `MemoryError`, non-`str` input (the one caller, `service_lifecycle_ops.py:5603`, passes text lines).
- **The review's case is already safe; another decoder failure is not** (run). On 3.11 `json.loads` raises `ValueError` for an
  over-4300-digit integer; `rflog.py:206` catches it, so `{"rssi": 1<5000 zeros>}` returns `{key, raw}` (a guard). What escapes
  is **`RecursionError`**: a JSON line nested 1000 deep (500 is fine). As `:206` caps decoded integers at 4300 digits, later
  `int()`/`str()` on decoded values cannot hit the digit limit.
- **Field gaps** (as v2, run): OverflowError from `_num` (`:174-180`), `_int` (`:183-187`), `node()` (`:216-221`); ValueError
  from a 5000-digit `len=` (`:200`); `NaN`/`1e400` → `nan`/`inf`, failing `allow_nan=False`.
- **Change** (`rflog.py`): `:206` becomes `except (ValueError, RecursionError):`. `_num` also catches `OverflowError`, and
  returns `None` unless `math.isfinite(v)`. `_int` and `node` catch `OverflowError`. `:200` uses `_int(g["len"])`. No
  catch-all: the rest maps decoded values through these hardened helpers.
- **Test** (`tests/core/test_rflog_parse.py::test_parse_line_never_raises_and_is_strict_json`): as v2, parametrized over
  timestamp, rssi, snr, size and from/to with `NaN`, `Infinity`, `1e400`, a 400-digit integer, `[1]` and `{}`, plus the two
  common-contract lines. Added: JSON `rssi` with a 5000-digit integer (guard: `{key, raw}`) and a JSON line nested 2000 deep
  (**RED before**, run: RecursionError). Every case asserts no raise and `json.dumps(r, allow_nan=False)`.
- **What else could break.** The display only loses non-finite values, which were never valid JSON (`app.py:1778`
  `jsonify`). A deeply nested line shows as a raw line, as any other non-record line does. **Size:** trivial.

### F4. CR4-6 — every exception between `open_txn` and commit unwinds
- **Guarantee.** Any `Exception` raised after `bi.open_txn` (`service_binary_ops.py:339`) and before `bi.commit` unwinds the
  transaction (displaced files back, created files removed, previous receipt and mesh password restored, journal closed). The
  original exception is preserved and logged, and the result is the typed failure. A `BaseException` (KeyboardInterrupt,
  SystemExit) is not caught; the journal stays open and the next binary operation recovers it, as today.
- **Change** (`binary_install` method only):
  1. Start the `try` right after `open_txn` succeeds. Today the auth switch (`:345-356`) sits outside it: a raise from
     `save_config_bundle` leaves the journal open, and its `binary_recover()` result at `:352` is ignored. Inside the `try`, `if
     not _r.ok: raise bi.BinaryInstallError(f"could not switch the mesh password off ({_r.summary})")`.
  2. `:395` becomes `except Exception as exc:`. For a type outside the four expected ones, write `traceback.print_exc()` to stderr
     (the CLI terminal or the web unit's journal) before wrapping it as `BinaryInstallError(f"unexpected {type(exc).__name__}:
     {exc}")`, and set `data["unexpected"] = repr(exc)`. Then the existing unwind (`:408`) and result are unchanged.
- **Tests** (`tests/install/test_binary_channel.py`): extend `test_any_extraction_error_unwinds_the_transaction` with `value`
  (ValueError from `validate_and_extract`). Add `test_unexpected_error_after_publish_unwinds_everything`: `validate_and_extract`
  stages one file under a publish root, then `bi.build_receipt` raises `KeyError`. Assert a typed result with
  `data["unexpected"]`, journal `absent`, the published file gone, the previous receipt state unchanged, and `password_file` back.
  Add `test_failed_password_switch_unwinds_through_the_common_path`. **RED before:** ValueError and KeyError escape with the
  journal open (by reading `:395`). The fixture for the post-publish case must be confirmed when coding.
- **What else could break.** A programming error now shows as a typed failure plus a stderr traceback instead of a 500/traceback.
  That is intended, and the traceback is kept. The auth refusal summary changes from "blocked" to "failed"; no test matches that
  text (grep). **Size:** small.

### F5. CR1-4 — clear the marker only on a seen "no daemon" (tri-state)
- **Guarantee.** After a verified per-band daemon stop, the restart marker and the known-working candidate are cleared only
  when one post-stop snapshot of the process table shows **no daemon**. **Some daemon** or **indeterminate** keeps both. A
  whole-daemon stop is unchanged: its `ok` already proves every instance stopped.
- **On the premise.** v2's `not self._daemon_radio_modes(cl)` did keep an unknown `--radio` (the helper appends `None`,
  `:1739-1745`), but it answers a presence question with a mode parser that only looks at argv[0] (`:1737`): a daemon under a
  wrapper is invisible to it and would clear. v3 decides on presence alone.
- **Change** (`service_lifecycle_ops.py`): a pure module-level helper (≈ 10 lines)
  `_daemon_presence(cmdlines) -> "none" | "some" | "indeterminate"`. **indeterminate** when `cmdlines` is empty (`{}` is what
  `cmdlines()` returns when `/proc` cannot be listed, `probes/backends.py:457-460`; a readable `/proc` always holds the
  caller). **some** when any token of any argv has basename `loraham_daemon`, whatever `--radio` is (missing, empty, unknown,
  malformed, valid) and whatever argv[0] is. **none** otherwise. At `:2619`: `_whole = not _daemon_band_stop or
  _daemon_presence(self._system.procfs.cmdlines()) == "none"`. One read, after the stop; `_daemon_radio_modes` is untouched.
- **Residual.** An unreadable `/proc/<pid>/cmdline` is skipped (`probes/backends.py:465-469`): a race, or `hidepid` for another
  user (lhpc runs the daemon as its own user).
- **Tests** (`tests/core/test_restart_required.py`):
  - As v2: `…band_stop_clears_when_no_instance_remains` (`cmdlines={1: ["init"]}`, stop 433 → both cleared; **RED before**,
    run). `…band_stop_keeps_while_another_instance_runs` (an 868 daemon; stop leg patched with `_patch_life(...,
    Outcome.STOPPED)` from `test_stop_propagation.py:52`). The existing `test_a_single_band_daemon_stop_keeps_the_marker`
    (`:187`) is renamed `…keeps_the_marker_when_the_process_view_is_indeterminate` (`cmdlines={}`).
  - **New regression** `…band_stop_keeps_while_a_daemon_with_unknown_or_malformed_radio_runs`, parametrized next to
    `{1: ["init"]}`: `[".../loraham_daemon", "--radio", "915"]`, `[".../loraham_daemon", "--radio"]`,
    `[".../loraham_daemon", "--radio="]`, `[".../loraham_daemon"]`, and `["stdbuf", "-oL", ".../loraham_daemon", "--radio",
    "868"]`. Stop 433, leg patched as above → marker and candidate kept. Green at `e5187f70` (433 ⊉ active bands), so a
    guard; the wrapper case is red under v2's predicate.
  - `test_daemon_presence_is_tri_state`: `{}` → indeterminate, `{1: ["init"]}` → none, each argv above → some.
- **What else could break.** Per-band stop tests with an empty fake table now keep the marker (safe side); by grep only
  `test_restart_required` combines a band stop with marker/candidate assertions. **Size:** small.

### F6. CR7-2 — prep tries the auto-clear, then decides on the refreshed phase
- **Guarantee.** Prep never passes while the persisted HMAC phase is running, interrupted or unsafe. A `session-unverified` block
  that is provably ceased is cleared the same way as at the other gates. Any remaining refusal names the remedy.
- **Change** (`service_maintenance.py:2095-2101`, inside the existing `try`, so any raise still blocks): if `hst` is a well-formed
  phase-`unsafe` marker with a `sid`, take `reslock.operation_lock(self._paths, "hmac-apply", sid, "")`, the lock the other gates
  hold (`service_hmac.py:456`). Under it, re-read and call `self._hmac_try_auto_clear(st)`. Then **re-read `hst =
  self.hmac_apply_status()`** and compute `hmac_bad` from that refreshed value, never from the pre-attempt read or the boolean.
  The refusal gets `next_commands=[f"lhpc hmac recover {sid}"]` and `details=["  inspect processes (ps) first"]`. `lhpc hmac
  status` is not named: it prints only enabled/disabled (`cli/main.py:1260-1262`).
- **Tests** (`tests/core/test_uninstall_prep.py`, with `hmac_apply_status` reading a mutable dict):
  - `test_prep_auto_clears_a_ceased_session_block`: auto-clear flips the phase to `failed` → `prep_blocked != "hmac"`. **RED
    before** (prep never calls it).
  - `test_prep_blocks_when_auto_clear_leaves_it_unsafe`: the stub returns True but the phase stays `unsafe` → blocked `hmac`. This
    is the refreshed-phase rule.
  - Extend `test_prep_blocks_on_hmac_phase_unsafe`: assert `"lhpc hmac recover" in r.next_commands[0]`. RED before (`:2100` has
    none).
- **What else could break.** Prep now may write the HMAC marker. It does so only through the same durable downgrade, under the
  same lock, as `hmac_apply_start` and `hmac_apply_cli`. `ResourceBusy` is caught by the surrounding `except Exception`, so it
  blocks. `uninstall.sh:230` prints `next_commands` via `_render` (`cli/main.py:233-235`). **Size:** trivial.

### F7. CR8-1 — the bare-Update channel rule lives in core
- **Guarantee.** A bare Update (no selector) chooses its channel from the target's *stack*: `binary` while that stack is
  binary-installed, otherwise `pinned`. CLI and web choose the same selector for any target, stack or component.
- **Change.** `service_binary_channel.py`, next to `default_channel` (`:68`): `update_default_channel(target)`: `sid =
  self.stack_of(target) or target`; return `BINARY_CHANNEL if sid and self.on_binary_channel(sid) else "pinned"`. Call it from
  `cli/main.py:1562` (`args.source or svc.update_default_channel(args.target)`, with an empty target giving `pinned`) and from
  `app.py:1592-1593`. Both adapters lose their copy.
- **Test** `test_bare_update_of_a_component_uses_the_stack_channel` in `tests/cli/test_cli.py` and `tests/web/test_web.py`:
  `on_binary_channel` is stubbed `sid == "daemon"`, and the target is `loraham-daemon` (covered by the daemon stack,
  `manifest.example.toml:40`). Spy on `svc.update` / `run_action` and assert source `binary` in both. **RED before** for the CLI
  (raw target → `pinned`); the web case is a guard.
- **What else could break.** The default only. An explicit `--source pinned` on a component is untouched. `binary_install` relies
  on that for `clone_required` components (`service_binary_ops.py:175`), so `update()`'s explicit-selector branches (`:1744`,
  `:1760`) are deliberately not re-keyed here. A bare component update on a binary stack now gets the typed refusal from
  `:1749-1752` in the CLI too, which is what the web already gives. **Not verified:** whether that refusal's "--source pinned"
  remedy is right for a covered component. I flag it, but it is not part of this item. **Size:** trivial.

### F8. CR7-16 — split: (a) decorate the known mutators now; (b) complete inventory + meta-test as a design item
- **Why split.** A mechanically complete universe is every public attribute of `ControllerService` (`services.py:161`):
  introspected, 325 public callables plus 1 property, 18 decorated. Classifying the other 308 against what `build_snapshot`
  (`services.py:594`) reads is ~300 lines of data plus review: a separate item. Until F8b, CR7-16 is complete **for the known
  set only**.
- **F8a (this round). Guarantee:** these 15 entries drop the memo on entry and exit: `graywolf_upstream_update` (build
  marker), `binary_install`, `binary_retire` (receipts/files; `binary_install`'s own success-only `invalidate_snapshot`
  stays), `reset_config`, `save_stack_config`, `save_daemon_params`, `reset_daemon_params`, `daemon_set`,
  `save_component_remote`, `confirm_known_working`, `boot_restore_run`, `set_high_power`, `set_gps`, `set_rflog`,
  `set_rflog_all`. All 15 exist and are undecorated at `e5187f70` (introspected). `run_action` stays a dispatcher: every op it
  dispatches is decorated (`service_lifecycle_ops.py:5812-5825`).
  - **Change.** One decorator line each. In `invalidates_snapshot` (`snapshot_memo.py:24`), set `_wrap.invalidates_snapshot =
    True`. Tests then check that marker, not `__wrapped__`, which any `functools.wraps` decorator sets.
  - **Tests** (`tests/core/test_snapshot_memo.py`): `test_known_mutators_are_decorated` over a fixed tuple of the 18 + 15
    names (**RED before**: 15 unmarked), and a behavioural `graywolf_upstream_update` drops the memo (like
    `test_uninstall_drops_the_memo`, `:83`). **Not a completeness guarantee** (said in the docstring): a new public mutator
    can still bypass both. **Size:** small (15 decorator lines + 1 line + 2 tests).
- **F8b (separate design item). Guarantee:** every public `ControllerService` name is either snapshot-invalidating or
  explicitly snapshot-neutral, never both and never neither.
  - **Universe:** every public name in `dir(ControllerService)` (360) except plain `int`/`float`/`str`/`tuple` constants (34,
    asserted to be only those types): 326 methods, static/class methods and the property. No annotation or `apply` heuristic.
  - **Data:** `snapshot_memo.SNAPSHOT_NEUTRAL: dict[str, str]` (name → one-line reason). Rule: an entry is neutral only if it
    writes nothing `build_snapshot` reads (processes, install/build markers, sources, config/params, receipts). Otherwise it
    is decorated. Reads and plan-only entries stay neutral, so render loops do not recompute the snapshot.
  - **Meta-test** `test_every_public_entry_is_classified`: every name in the universe carries the marker or is in
    `SNAPSHOT_NEUTRAL`; no name has both; every key still exists. A new public entry of any shape fails until classified.
  - **Size:** medium: ≈ 290 dict lines after F8a (~a day reading each entry's writes), 1 meta-test, the decorators found.

## 3. Not carried as follow-ups (agreed with the review)
CR9-8's multi-line `if <block>; then` caveat (no multi-line block in the manifest contract). The duplicated AP-scoping rule
(CR6-1), the duplicate atomic writers (CR6-9) and the third demo guard (CR10-7) are maintainability debt, not corrections.
