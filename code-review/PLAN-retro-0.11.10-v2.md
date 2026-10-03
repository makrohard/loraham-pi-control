# PLAN (retrospective) v2 — 0.11.10 code-review fixes

Base: `origin/main` = `e5187f70` (release v0.11.10). v1 is `code-review/PLAN-retro-0.11.10.md` on `code-review/brief`. The review
is `code-review/VERDICT-PLAN-retro.md` on the same branch: it agreed with all 36 verdicts and asked for the follow-up plan to
change. This v2 keeps the 36 verdicts and rewrites the follow-up section. No code changes here. Line numbers are at `e5187f70`.

**Method.** I re-read every cited site at `e5187f70`. Where a claim matters for a follow-up I ran it: Python 3.11, pytest, flask,
waitress, cryptography and zstandard, with scratch tests that I deleted afterwards. These were run: the rflog raise/NaN matrix,
the firewall failed-restore contract (next check, boot load, retry apply), the first-install restore that cannot see the table,
the secondary web-job attempt left `starting`, and the per-band daemon stop with different process tables. **Not verified:** the
full suite, hardware, systemd, the Pyodide demo, and what a `pinned` source update does to a binary-covered component (see F7).

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

Order: F1–F2 are the two correctness items the review added, F3–F6 close the four incomplete verdicts, F7–F8 are the
consistency items. Each one is a separate commit with its own regression test.

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
- **Guarantee.** Every exit after `jobresult.reserve` either spawns a child or leaves the attempt terminal. An unexpected
  exception terminalizes `failed` and is then re-raised unchanged.
- **Verified gap.** Run: in the U-1 test scenario the secondary `build-meshcom-bridge` attempt stays `starting` after the raise.
  There is also a non-programming path: `spawn_job` catches only `OSError` (`lifecycle.py:1473-1476`), but `_real_spawn` opens the
  log with `runtime_fs.open_log_append` (`lifecycle.py:220`), which can raise `PathContainmentError`, a `ValueError`
  (`paths.py:34`). It escapes before any child exists. Three sites share the window: `_spawn_install`
  (`service_lifecycle_ops.py:3459-3470`), `_spawn_build` (`:3477-3512`), and `spawn_start_job` (`:3615-3634`).
- **Change.**
  1. `jobresult.py` (next to `terminalize`, `:243`): add an 8-line context manager `settle_on_raise(paths, log, attempt_id)`. On
     `Exception` it calls `terminalize(..., "failed", detail=f"internal error before the job started: {type(e).__name__}")` and
     re-raises. It never catches `BaseException`.
  2. Wrap the code from just after each successful `reserve` through the `if not ln or not pid` check in `with
     jobresult.settle_on_raise(...)` at the three sites. The block ends before identity capture and tracking: once a child exists,
     a `failed` write would be a lie, and `_track_or_terminate`/`_settle_track` already own that phase.
  3. `lifecycle.py:1475`: `except (OSError, PathContainmentError)`, so the real path returns `(None, None)` and settles through
     the existing typed branch instead of raising.
- **Tests** (`tests/web/test_webjob.py`):
  - Extend `test_spawn_web_job_secondary_raise_releases_its_admission`: after `pytest.raises`, assert
    `jobresult.read_one(svc._paths, calls[1] + ".log")["state"] == "failed"`. **RED before** (run: `starting`).
  - `test_spawn_start_job_raise_before_spawn_settles_the_attempt`: `spawn_job` raises `RuntimeError`. Assert it propagates and the
    `web-start-<t>.log` attempt is `failed`. RED before (by reading `:3615-3632`).
  - `tests/core/test_lifecycle*`: `spawn_job` returns `(None, None)` when `_spawn` raises `PathContainmentError`. RED before (by
    reading).
- **What else could break.** The paths that terminalize then return do not raise, so there is no double write. If `terminalize`
  itself raises inside the handler, Python chains the original as `__context__`, so it is not lost. Not covered: `log.close()`
  raising after `Popen` succeeded (`lifecycle.py:227`). That is pre-existing and out of scope. **Size:** small (≈ 20 lines + 3
  tests).

### F3. CR9-11 — `parse_line` never raises and every record is strict JSON
- **Guarantee.** For any input line, `parse_line` returns a record, and `json.dumps(record, allow_nan=False)` succeeds.
- **Verified gaps** (run at `e5187f70`): `{"rssi": 1<400 zeros>}`, `{"snr": …}` → OverflowError from `_num` (`rflog.py:174-180`).
  `{"size": 1e400}` / `Infinity` → OverflowError from `_int` (`:183-187`). `{"from": 1e400}` → OverflowError from `node()`
  (`:216-221`). A common-contract line with `len=` of 5000 digits → ValueError from `int(g["len"])` (`:200`). `rssi: NaN`/`1e400`,
  or a 400-digit `rssi=` in a line → `nan`/`inf`, which fails `allow_nan=False`. Timestamps are already safe (`:212-215`).
- **Change** (`rflog.py`, three helpers + one call): `_num` catches `OverflowError` too and returns `None` unless
  `math.isfinite(v)`. `_int` and `node` catch `OverflowError`. Line `:200` uses `_int(g["len"])`.
- **Test** (`tests/core/test_rflog_parse.py::test_parse_line_never_raises_and_is_strict_json`): parametrized over timestamp, rssi,
  snr, size and from/to, with values `NaN`, `Infinity`, `1e400`, a 400-digit integer, `[1]` and `{}`, plus the two common-contract
  lines. Assert no raise, the field is `None`/`0`/`""`, and `json.dumps(r, allow_nan=False)`. **RED before** (run: 6 of the cases
  raise, 3 fail strict JSON).
- **What else could break.** The display only loses non-finite values, which were never valid JSON (`app.py:1778` `jsonify`).
  Ruled out: no caller compares `rssi`/`snr` with `nan`/`inf` (grep for `isnan|Infinity|isFinite` near rflog in `lhpc/`).
  **Size:** trivial.

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

### F5. CR1-4 — clear the marker only when no daemon instance remains (fail closed)
- **Guarantee.** After a verified per-band daemon stop, the restart marker and the known-working candidate are cleared only when
  an authoritative post-stop observation shows no daemon instance at all. If any instance remains, or the observation is
  indeterminate, both are kept. A whole-daemon stop is unchanged: its `ok` already proves every instance stopped.
- **Change** (`service_lifecycle_ops.py`): `_daemon_radio_modes` (`:1731`) gets an optional `cmdlines` argument. At `:2619`, for a
  band stop: `cl = self._system.procfs.cmdlines()`, then `_whole = bool(cl) and not self._daemon_radio_modes(cl)`. An empty
  process table is indeterminate, because a live `/proc` always contains the calling process, while `cmdlines()` returns `{}` when
  `/proc` cannot be listed (`probes/backends.py:455-460`). Any observed daemon, including one with an unknown `--radio`, keeps the
  marker. One read, so no race between the two calls.
- **Tests** (`tests/core/test_restart_required.py`):
  - `…band_stop_clears_when_no_instance_remains`: `cmdlines={1: ["init"]}`, stop 433 → marker and candidate cleared. **RED
    before** (run: marker kept, because `active_bands()` = 433+868).
  - `…band_stop_keeps_while_another_instance_runs`: an 868 `loraham_daemon` in `cmdlines`, with the stop leg made to verify
    through `_patch_life(..., Outcome.STOPPED)` as in `test_stop_propagation.py`. Marker kept. (Run: without the patch the fake
    reports `manual_required`, so `ok` is False and the case would not reach `_whole`.)
  - The existing `test_a_single_band_daemon_stop_keeps_the_marker` runs with `cmdlines={}`. It is renamed
    `…keeps_the_marker_when_the_process_view_is_indeterminate` and keeps its assertions.
- **What else could break.** Other per-band stop tests with an empty fake process table now keep the marker, which is the safe
  side. Checked by grep: only `test_restart_required` combines a band stop with marker or candidate assertions. `/proc` mounted
  `hidepid` hides other users' daemons; lhpc runs them as its own user. **Not verified:** a live hidepid box. **Size:** small.

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

### F8. CR7-16 — inventory the public mutators instead of decorating everything
- **Guarantee.** Every public `ControllerService` entry that can change state `build_snapshot()` reads (processes, install/build
  markers, sources, config/params, receipts) drops the memo on entry and exit. Every other entry is listed, with its reason, as
  snapshot-neutral.
- **Inventory** (introspected at `e5187f70`): 325 public methods. 93 return `ActionResult` or take `apply`; 18 of those are
  decorated. Of the 75 undecorated:
  - *Read-only / plan-only (allowlist):* `status`, `status_versions`, `explain`, `doctor`, `list_stacks`, `logs`, `deps_declared`,
    `firewall_render`, `graywolf_upstream_check`, `self_update_check`, `source_check`, `network_scan`, `webserver_cert_list`,
    `webserver_verify`, `webserver_monitor`.
  - *Dispatcher:* `run_action`. Every op it dispatches is decorated (`:5812-5825`).
  - *Mutate snapshot-visible state (decorate):* `graywolf_upstream_update` (build marker), `binary_install` and `binary_retire`
    (receipts/files; `binary_install` already calls `invalidate_snapshot` only on success, `:445`), `reset_config`,
    `save_stack_config`, `save_daemon_params`, `reset_daemon_params`, `daemon_set`, `save_component_remote`,
    `confirm_known_working`, `boot_restore_run`, `set_high_power`, `set_gps`, `set_rflog`, `set_rflog_all`.
  - *The rest* (`network_*`, `power_action`, `webserver_*`, `firewall_configure`, `self_update_*`, `secrets_*`,
    `hmac_apply_abort/recover`, uninstall guard/prep, `bootstrap`, `set_*` identity/hardware/boot-restore, `rflog_clear*`,
    `stack_web*`): each one is decided while coding by reading what it writes against what `build_snapshot` reads. Each lands in
    one of the two lists. None is decorated by default.
- **Change.** Add the decorators above (one line each). Add a module constant `SNAPSHOT_NEUTRAL = {name: reason}` in
  `snapshot_memo.py`.
- **Meta-test** `tests/core/test_snapshot_memo.py::test_every_public_mutator_is_decorated_or_listed`: for each public method
  returning `ActionResult` or taking `apply`, assert it either has `__wrapped__` from `invalidates_snapshot` or is in
  `SNAPSHOT_NEUTRAL`, and that every `SNAPSHOT_NEUTRAL` name still exists. **RED before** (75 unlisted). Plus a behavioural test:
  `graywolf_upstream_update` drops the memo (like `test_uninstall_drops_the_memo`).
- **What else could break.** Decorating a read path called inside a render loop would recompute the snapshot many times per page.
  That is why read-only entries are allowlisted, not decorated. A newly added public mutator fails the meta-test until it is
  classified. Not covered: an unannotated public mutator without `apply`, since the meta-test sees only the annotation and the
  `apply` parameter. **Size:** small (≈ 20 decorator lines + one dict + 2 tests).

## 3. Not carried as follow-ups (agreed with the review)
CR9-8's multi-line `if <block>; then` caveat (no multi-line block in the manifest contract). The duplicated AP-scoping rule
(CR6-1), the duplicate atomic writers (CR6-9) and the third demo guard (CR10-7) are maintainability debt, not corrections.
