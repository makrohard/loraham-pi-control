# PLAN (retrospective) — 0.11.10 code-review fixes

Release v0.11.10 = `e5187f70`. The 36 fixes are the commits `c60028f0..eeeac3e` (`bac91054` is
only the version bump). They were coded without the per-item workflow. This file writes the
plan after the fact so it can be audited, and it names every place where the shipped change
falls short of it. No code changes here.

**Method.** I read each commit's diff and the code around it at `e5187f70`. Line numbers are at
`e5187f70`. For RED/GREEN, each commit's new or changed tests were run twice: on the commit's
parent with the commit's `tests/` checked out (RED), and on the commit itself (GREEN). The venv
was Python 3.11 with flask, werkzeug, waitress, cryptography, pytest and zstandard. The demo
probe (CR10-7) was run under plain CPython with the paths rewritten, not under Pyodide.
**Not verified:** the full suite, any hardware or systemd behaviour, the Pyodide demo build, and
CR9-8's test. The bootstrap-deps harness exits 2 in this container because it runs as root and
has no operator user, so CR9-8's test fails here at both the parent and the commit. Its RED/GREEN
result comes from reading the test only.

Each entry has six parts: (a) the defect, (b) the guarantee the fixed code must hold, (c) the
change, (d) the test and its RED result, (e) the side effects, (f) the verdict.

---

### 1. CR7-2 — uninstall prep blocks on a persisted HMAC phase "unsafe"
- a: `controller_uninstall_prep` treated phase `unsafe` as quiescent. b: Prep never reports "safe to remove" while an HMAC run is running, interrupted or unsafe.
- c: `lhpc/core/service_maintenance.py:2097` adds `"unsafe"` to the phase set. d: `tests/core/test_uninstall_prep.py::test_prep_blocks_on_hmac_phase_unsafe`. RED on the parent (run).
- e: Fails closed, so nothing unsafe gets through. Other HMAC gates call `_hmac_try_auto_clear` first (`service_hmac.py:470,596`, defined at `:685`). Prep does not, so an auto-clearable `session-unverified` block stops uninstall until another HMAC operation runs. The refusal also gives no `next_commands`, although `lhpc hmac recover` exists. f: **incomplete:** the refusal names no remedy (`lhpc hmac status`, `lhpc hmac recover`), and prep skips the auto-clear attempt the other gates make.

### 2. CR7-11 — rollup reads degraded when a not-installed main has sidecars up
- a: A main reading NOT_INSTALLED ranks below RUNNING, so a stack whose main was missing but whose sidecars ran rolled up as "running". b: RUNNING with a main that is not running (STOPPED or NOT_INSTALLED) rolls up as `degraded`.
- c: `lhpc/core/status.py:460`. d: `tests/core/test_status_rules.py::test_rollup_is_degraded_when_the_main_is_down_but_sidecars_run` (extended). RED on the parent (run).
- e: Display only (`services.py:940`, `app.py:851`, `summarize`). An optional main that is not installed is skipped earlier (`status.py:450`). No flow decides on the rollup. f: **complete**

### 3. CR7-8 — graywolf upstream update refuses an unknown installed version
- a: With no version stamp, the update reported ok "already at the latest ()". b: An unknown installed version is never reported as current. Dry run and apply both refuse.
- c: `lhpc/core/service_maintenance.py:924-929`. d: `tests/stacks/test_graywolf_upstream.py::test_update_with_absent_stamp_refuses_instead_of_claiming_latest`. RED (run).
- e: This matches the check's own "unknown" text (`:905`). The CLI now exits 1 where it exited 0. That is the intent. f: **complete**

### 4. CR7-16 — uninstall drops the snapshot memo
- a: `uninstall` lacked `@invalidates_snapshot`, so the pre-removal snapshot stayed cached. b: After any mutating public entry returns, the next `build_snapshot()` in the same thread reassesses.
- c: `lhpc/core/service_maintenance.py:2250` adds the decorator. d: `tests/core/test_snapshot_memo.py::test_uninstall_drops_the_memo`. RED (run).
- e: No sweep of the other entries was made. `graywolf_upstream_update` (`:915`) rewrites a build marker and is not decorated. It calls `restart()` (decorated) only when the stack was running. I did not verify whether it can leave a stale memo. f: **complete** (for uninstall; the sweep is a follow-up)

### 5. CR7-7 — clean removes `.prev.log` halves and web start/restart logs
- a: The log matcher missed `<log>.prev.log` and `web-start|restart-<target>.log`. b: Clean removes every log this stack's ops wrote, and nothing that belongs to another stack.
- c: `lhpc/core/service_maintenance.py:2494` adds the prefixes. `:2690` strips `.prev.log` before `.log`. d: `tests/core/test_clean.py::test_clean_removes_exact_set_and_preserves_the_rest` (extended). RED (run).
- e: I checked the `startswith(p + "-")` rule across all manifest stack and component ids: no id of one stack is a `-`-prefix of another's. The web-log name source is `service_lifecycle_ops.py:3613`. f: **complete**

### 6. CR1-8 — daemon integer SETs are ASCII decimal and sent canonical
- a: `int()` accepted `"1_0"`, `" 5"`, `"10\n"` and Unicode digits. The raw value reached the socket. b: Only `[+-]?[0-9]+` within range passes, and the socket receives `str(int(v))`.
- c: `lhpc/core/daemon_control.py:326` (regex) and `:512` (`canonical_value`). d: `tests/stacks/test_daemon_control.py::test_integer_values_are_ascii_decimal_and_sent_canonical`. RED (run).
- e: Live SET callers (`service_params.py:3444`) used to send `" 10"` raw. They now get a typed refusal. `service_lifecycle_ops.py:2121` strips first. A hand-edited stored override such as `" 10"` is now silently left out of the effective overrides (`service_params.py:262`). lhpc itself stores canonical values, so this is acceptable. f: **complete**

### 7. CR1-5 — poststart turns admission contention into a typed refusal
- a: `reslock.ResourceBusy` escaped `poststart` and raised. b: Contention in poststart is a typed `ActionResult`, never a raise.
- c: `lhpc/core/service_lifecycle_ops.py:1629-1631`. d: `tests/core/test_task_admission.py::test_poststart_contends_typed`. RED (run).
- e: I checked the other `_admission_guard` sites (auto-install `:734`, hmac `:515/:627`, start `:725`, restart `:2734`, self-update `:544`, boot-restore catch-all `:284`). All are typed. f: **complete**

### 8. U-2 — a client's TX test never falls back to another served band
- a: When none of a client's bands was served, the TX test planned on `active_bands()`. b: Only a target with no band of its own falls back to the served bands. A client with unserved bands refuses.
- c: `lhpc/core/service_lifecycle_ops.py:4059`. d: `tests/stacks/test_daemon_readiness.py::test_tx_test_never_moves_a_client_to_another_band`. RED (run).
- e: I listed the manifest bands: only `daemon` has none, so the fallback is daemon-only in practice. This is an RF safety tightening. f: **complete**

### 9. CR2-1 — config reset keeps the HMAC-managed password_file
- a: `reset_config` cleared `password_file`, which silently restored open auth. b: Reset to defaults never clears the HMAC-managed `password_file`.
- c: `lhpc/core/service_params.py:3339` builds `run_names` from `_form_run_params`. d: `tests/web/test_hmac.py::test_config_reset_keeps_the_hmac_managed_password_file`. RED (run).
- e: The `__r__`/`__f__` prefix rule at `:3348` still clears a scoped `__r__<comp>__password_file`. I confirmed this by script. HMAC writes only the flat key, and generic config refuses the name, so lhpc never creates that spelling. It is residual only. f: **complete**

### 10. CR1-4 — a per-band daemon stop keeps the restart marker and candidate
- a: Any verified daemon stop cleared the marker and candidate, including a one-band stop that left the other band's instance running. b: The marker and candidate are retired exactly when no daemon instance is left running the old config.
- c: `lhpc/core/service_lifecycle_ops.py:2619`. `_whole` compares the stopped bands with `active_bands()`. d: `tests/core/test_restart_required.py::test_a_single_band_daemon_stop_keeps_the_marker`. RED (run).
- e: `active_bands()` is the configured set, not the running set. A 433 stop on a box configured for 433+868 with only 433 running leaves the daemon fully stopped but keeps both the marker and the known-working candidate. The comment at `:2626-2630` names that leftover candidate as the hazard (confirming a composition that no longer runs). f: **incomplete:** decide `_whole` from the instances still running after the stop, not from the configured bands.

### 11. CR1-6 — a failed launcher write settles the reserved web-job attempt
- a: An `OSError` from `write_launcher` after `jobresult.reserve` escaped as a 500 and left the attempt "starting". b: Every exit after `reserve` either spawns or turns the attempt terminal.
- c: `lhpc/core/service_lifecycle_ops.py:3504-3508`. d: `tests/web/test_webjob.py::test_spawn_web_job_launcher_write_failure_is_typed_and_settles_the_attempt`. RED (run).
- e: `spawn_job` returns `(None, None)` on OSError (`lifecycle.py:1463-1476`). That path was already settled. f: **complete**

### 12. U-1 — a secondary web job releases its admission even when its spawn raises
- a: A raise in `_launch` leaked the secondary's admission hold, so the thread's next task was admitted re-entrantly. b: Every admission taken in `spawn_web_job` is released on every exit path.
- c: `lhpc/core/service_lifecycle_ops.py:3575` (`with _sec:`). d: `tests/web/test_webjob.py::test_spawn_web_job_secondary_raise_releases_its_admission`. RED (run).
- e: The raise still propagates (web 500) after the primary job was spawned. A raise after `reserve` in the secondary would leave that attempt "starting" (the CR1-6 class). Only a programming error can cause it, since `spawn_job` does not raise. f: **complete**

### 13. CR2-5 — a failed set-aside mid-switch undoes the adopted checkouts
- a: The failure path called `binary_recover()` alone. Earlier groups' checkouts and records stayed. b: A failed binary→source switch removes what it created before the binary is restored, as every other failed switch does.
- c: `lhpc/core/services.py:1697` routes through `_resolve_switch(ok=False, …)` (`:1392`). d: `tests/install/test_binary_install.py::test_failed_set_aside_removes_what_the_switch_already_adopted`. RED (run).
- e: A failed restore is now reported (`:1411`). Before, the `binary_recover()` result was ignored. f: **complete**

### 14. CR7-9 — reject a terminal job marker whose finished_at is not a string
- a: A falsy non-string `finished_at` raised TypeError out of `read_results`. b: `read_results` never raises. Such a marker is invalid.
- c: `lhpc/core/jobresult.py:112-113`. d: `tests/core/test_jobresult.py::test_read_results_skips_a_terminal_marker_with_a_falsy_non_string_finished_at[0,False,[]]`. RED (run).
- e: None. f: **complete**

### 15. CR4-4 — a non-UTF-8 unit file verifies as unreadable
- a: `UnicodeDecodeError` escaped `verify` and `integration` (dashboard, trigger, boot restore). b: `verify` returns a state for any unit content and never raises.
- c: `lhpc/core/updater_units.py:535`. `_read_unit` (`:431`) has no other caller. d: `tests/host/test_updater_units.py::test_verify_returns_unreadable_for_undecodable_bytes`. RED (run).
- e: I did not trace how repair or `--repair-integration` handles UNREADABLE. f: **complete**

### 16. CR8-4 — a bad cert label in export / discard-export is refused typed
- a: `ValidationError` from `pki.read_export` / `discard_export` gave a CLI traceback. b: The CLI never prints a traceback for a bad label.
- c: `lhpc/adapters/cli/main.py:1747-1753` pre-validates with the same `validators.path_component` that pki uses (`pki.py:543,554`). d: `tests/cli/test_cli_webserver.py::test_cli_export_and_discard_export_refuse_a_bad_label_without_a_traceback`. RED (run).
- e: The service method `webserver_cert_discard_export` still raises. The CLI is its only caller. The web download catches `Exception` (`app.py:2368-2370`). f: **complete**

### 17. CR8-2 — a non-numeric console port is a typed result
- a: `int(port)` raised, giving a 500.  b: Every form value of the port is a typed result. c: `lhpc/core/service_webserver.py:497-500`.
- d: `tests/web/test_webserver.py::test_configure_refuses_a_non_numeric_port_typed`. RED (run). e: `int()` still accepts `"8_443"` and Unicode digits as numbers. The range validation after it still applies.
- f: **complete**

### 18. CR8-3 — a non-numeric proxy port is a typed result
- a/b as #17, for `/stacks/<id>/webserver`. c: `lhpc/core/service_webserver.py:1249-1252`. d: `tests/web/test_stackweb.py::test_route_refuses_a_non_numeric_port_typed`. RED (run).
- e: As #17.  f: **complete**

### 19. CR7-15 — open_marker_excl removes its new leaf when the write fails
- a: A write or fsync failure after O_EXCL left an empty leaf that read as a malformed marker. b: A failed create leaves no leaf of ours, and never unlinks someone else's.
- c: `lhpc/core/runtime_fs.py:369-377`, an inode-checked unlink through the parent fd. d: `tests/core/test_runtime_fs.py::test_open_marker_excl_removes_its_leaf_when_the_write_fails`. RED (run). `…_cleanup_spares_a_leaf_replaced_after_the_failure` is a guard and green on the parent, as expected.
- e: None.  f: **complete**

### 20. CR4-8 — the atomic-rename probe leaves no scratch dir on failure
- a: Other OSErrors skipped the cleanup, and each re-probe leaked a dir into `src/`. b: A failed probe leaves no `.lhpc-atomic-probe-*`.
- c: `lhpc/core/source_fs.py:179-183`. d: `tests/install/test_source.py::test_a_failed_atomic_rename_probe_leaves_no_probe_dir`. RED (run).
- e: None.  f: **complete**

### 21. CR6-9 — firewall atomic writes loop short writes and unlink the temp file
- a: A `.tmp-*` was left behind on ENOSPC/EIO, and a short write could be promoted. b: Either the whole payload is renamed into place, or no temp file remains.
- c: `lhpc/core/firewall_helper.py:671-688` and `lhpc/core/service_firewall.py:1338-1355` (two identical copies). d: `tests/host/test_firewall.py::test_atomic_write_leaves_no_temp_file_when_the_write_fails` and `…_writes_the_whole_payload_under_short_writes` (×2 writers each). RED (run).
- e: None.  f: **complete**

### 22. CR6-7 — the receipt says when the failed-apply restore did not succeed
- a: The receipt said "previous ruleset restored" whatever the restore returned. b: The receipt detail reflects the actual restore or teardown result.
- c: `lhpc/core/firewall_helper.py:1352-1364`. d: `tests/host/test_firewall.py::test_failed_apply_whose_restore_fails_does_not_claim_the_old_ruleset_is_back`. RED (run).
- e: The journal is still unlinked (`:1362`) after a failed restore. The live ruleset is then unknown, with no record for recovery. I did not verify whether `firewall check` reports that drift. f: **complete**

### 23. CR6-1 — on AP-managed boxes the narrowing proof rebuilds the console ingress unscoped
- a: The proof rebuilt the console ingress with CIDRs, but `_fw_proxy_ingress` emits `[]` when the AP is managed. b: The reconstruction uses the same scoping rule as `_fw_proxy_ingress` (`service_firewall.py:178`).
- c: `lhpc/core/service_firewall.py:749-750`. `cand["ap"]["enabled"]` comes from the same `ap_enabled` (`:148-150`). d: `tests/host/test_firewall.py::test_narrowing_allowed_on_an_ap_managed_box`. RED (run).
- e: The rule now exists twice. A later change to one must change the other.  f: **complete**

### 24. CR7-14 — config recovery blocks on a journal target with a bad pre or mode
- a: `int(mode)` and an untyped `pre` raised mid-recovery, after earlier targets had been restored. b: A malformed journal blocks before any restore and never raises.
- c: `lhpc/core/config.py:1688-1692` (validated in `_resolve_journal_target`) and `:1756`. d: `tests/core/test_config.py::test_malformed_journal_pre_or_mode_blocks_not_raises[5 cases]`. RED (run).
- e: The writer (`:1803-1810`) journals `pre=None` only when `existed=False`, and every caller passes an int mode (`service_params.py:1207,1389,1402,1518`). Valid journals still pass. f: **complete**

### 25. CR4-6 — the binary transaction unwinds on tar and containment errors
- a: `TarError` and `PathContainmentError` escaped with the journal open and the mesh password blanked. b: Every failure between `begin` and commit unwinds the transaction and returns a typed result.
- c: `lhpc/core/service_binary_ops.py:395`. d: `tests/install/test_binary_channel.py::test_any_extraction_error_unwinds_the_transaction[tar,containment]`. RED (run).
- e: The fix lists types. Any other exception from the body (`ValueError`, `EOFError`, a `KeyError` from a receipt) still escapes with the journal open. Recovery then waits for the next binary operation. f: **incomplete:** the guarantee needs `except Exception` here, not two more named types.

### 26. CR8-1 — a bare web Update keeps the CLI's channel rule
- a: `/action` update fell back to `default_channel` (binary), which planned a channel switch. b: An Update without a selector stays binary when the stack is binary-installed. Otherwise it uses `pinned`.
- c: `lhpc/adapters/web/app.py:1592-1596`. d: `tests/web/test_web.py::test_bare_update_keeps_the_installed_channel_like_the_cli[False-pinned]`. RED (run). `[True-binary]` was already green.
- e: The rule now exists twice and differs. The web passes `stack_of(target)`. The CLI (`cli/main.py:1562`) passes the raw target, so for a component target the CLI reads "not binary". The safe-side refusal at `service_maintenance.py:1759+` limits the harm. f: **complete** (one shared helper is a follow-up)

### 27. CR4-3 — the prepared migration record is kept when git failed after HEAD moved
- a: A failed apply dropped the journal and anchor without re-reading HEAD. b: A prepared record is dropped only when HEAD is positively still at `from_head`.
- c: `lhpc/core/service_selfupdate.py:682-684`. d: `tests/install/test_selfupdate_migration.py::test_failed_apply_with_head_at_to_head_keeps_the_prepared_record[to,unreadable,from]`. RED for `to` and `unreadable` (run). `from` is a guard.
- e: The next-run reconciliation (`:596-601`) drops on `head != to_head`, but `classify_journal` (`selfupdate.py:623`) already returns `recovery_required` for an unreadable or foreign HEAD. So the two paths are consistent. f: **complete**

### 28. CR5-3 — start-service loads the promoted config and reports truthfully
- a: `enable --now` does not reload a running nginx, and the result was ok whatever the verify said. b: After `webserver start-service`, the promoted config is the one serving, and ok means the listeners match.
- c: `lhpc/core/service_webserver.py:2286` (`was_running`), `:2294-2301` (restart) and `:2304-2311` (listener gate). d: `tests/web/test_stackweb.py::test_start_service_fails_when_no_listener_comes_up`, `…::test_start_service_restarts_an_already_running_nginx_onto_the_new_config`. RED (run).
- e: The only caller is the CLI (`cli/main.py:1657`). The nginx unit is `Type=forking`, so the listeners are bound when systemctl returns and verify cannot race. This is the same restart pattern as `webserver_apply` (`:2114`). f: **complete**

### 29. CR9-1 — install.sh snapshots pre-existing entries space-separated
- a: The newline-separated snapshot did not match `" $PRE_ENTRIES "`, so rollback deleted the uninstall remainder. b: Rollback removes only entries created by this run.
- c: `install.sh:162`. d: `tests/host/test_deploy_scripts.py::test_install_rollback_keeps_an_uninstall_remainder`. RED (run).
- e: Names with spaces would split, but install.sh refuses foreign entries before this point, so only allowlisted names occur. f: **complete**

### 30. CR9-4 — each systemd unit is recorded for rollback before it is rendered
- a: `CREATED_UNITS` was set after all seven renders, so a failure part-way leaked units. b: Every unit file this run created, including an empty one, is rolled back.
- c: `install.sh:281-284`. d: `tests/host/test_deploy_scripts.py::test_install_rollback_removes_units_written_before_a_render_failure`. RED (run).
- e: Pre-existing units are refused at `install.sh:150`, so rollback cannot delete one. f: **complete**

### 31. CR9-8 — a failed packaged-service disable is deferred to the final verdicts
- a: The unguarded `systemctl disable --now` under `set -e` exited before the swap/group/time verdicts. b: All verdicts are reported. A failed disable alone exits 9, never "done".
- c: `bootstrap-deps.sh:989-994,1013-1016` and the generator `lhpc/core/deps.py:1726-1755,1793-1797`. The committed snapshot is held equal to the generator by `test_committed_snapshot_equals_generator`. d: `tests/install/test_bootstrap_deps.py::test_packaged_unit_disable_failure_keeps_the_deferred_verdicts`. **Not run here** (environment, see Method). RED by reading: on the parent, the disable aborts before exit 4.
- e: `if <block>; then` takes the block's last command's status. The block is a single line today (`manifest.example.toml:1356`). A multi-line block would hide failures from its earlier lines. f: **complete**

### 32. CR9-9 — "gpsd unreachable" is kept, not overwritten with "closed"
- a/b: A failed connect reads "unreachable". Only a session that ended reads "closed". c: `lhpc/core/gps_bridge.py:762-764`.
- d: `tests/stacks/test_gps.py::test_an_unreachable_gpsd_is_reported_as_unreachable_not_as_closed`. RED (run). e: None.  f: **complete**

### 33. CR9-10 — the next gpsd address is tried when a socket cannot be created
- a/b: An unsupported address family falls through to the next address. c: `lhpc/core/gps.py:932-936`. The bridge uses `create_connection` (`gps_bridge.py:726`), which already did this.
- d: `tests/stacks/test_gps_monitor.py::test_an_unsupported_address_family_falls_through_to_the_next_address`. RED (run). e: None.  f: **complete**

### 34. CR9-11 — a non-scalar RF-log timestamp/rssi/snr is treated as blank
- a: A list or object value raised TypeError and broke the whole RF-log view. b: `parse_line` never raises, and every record it returns serializes as valid JSON.
- c: `lhpc/core/rflog.py:179` (`_num`) and `:213` (timestamp). d: `tests/core/test_rflog_parse.py::test_unknown_lines_are_kept_raw_and_never_raise` (extended). RED (run).
- e: Checked by script at `e5187f70`: `{"size": 1e400}` still raises OverflowError from `_int` (`rflog.py:183`). `{"rssi": NaN}` and `{"snr": 1e400}` come back as `nan`/`inf` and reach `jsonify` at `app.py:1778`. Flask emits bare `NaN`/`Infinity`, which a browser's `JSON.parse` rejects (I verified this by reading, not in a browser). f: **incomplete:** add OverflowError to `_int`, and make `_num` return None for a non-finite value.

### 35. CR9-13 — bridge NMEA coordinates are validated as the Monitor does
- a: The bridge skipped the width, minutes < 60 and range checks. b: The bridge and the Monitor use one validator, and a malformed field never becomes a position.
- c: `lhpc/core/gps_bridge.py:405-407` delegates to `gps._nmea_coord` (`gps.py:581`). d: `tests/stacks/test_gps.py::test_a_malformed_coordinate_never_becomes_a_position`. RED for 3 of 4 cases (run). `48nan` was already green on the parent.
- e: An empty or invalid hemisphere is now dropped; before, it read as N/E. That is correct. f: **complete**

### 36. CR10-7 — the demo refuses a restart of an optional component
- a/b: A restart of an optional part is refused, as start and stop already refuse it, and never runs the stack. c: `demo/lhpc_demo/service.py:540-547`. This is the third copy of the guard (`:438-445`, `:495-502`).
- d: `demo/tests/probe.py:42-45` (the boot gate). RED (run under CPython against the parent's service). e: Demo only.  f: **complete**

---

## Summary

| # | id | verdict | | # | id | verdict |
|---|---|---|---|---|---|---|
| 1 | CR7-2 | incomplete | | 19 | CR7-15 | complete |
| 2 | CR7-11 | complete | | 20 | CR4-8 | complete |
| 3 | CR7-8 | complete | | 21 | CR6-9 | complete |
| 4 | CR7-16 | complete | | 22 | CR6-7 | complete |
| 5 | CR7-7 | complete | | 23 | CR6-1 | complete |
| 6 | CR1-8 | complete | | 24 | CR7-14 | complete |
| 7 | CR1-5 | complete | | 25 | CR4-6 | incomplete |
| 8 | U-2 | complete | | 26 | CR8-1 | complete |
| 9 | CR2-1 | complete | | 27 | CR4-3 | complete |
| 10 | CR1-4 | incomplete | | 28 | CR5-3 | complete |
| 11 | CR1-6 | complete | | 29 | CR9-1 | complete |
| 12 | U-1 | complete | | 30 | CR9-4 | complete |
| 13 | CR2-5 | complete | | 31 | CR9-8 | complete (test not run here) |
| 14 | CR7-9 | complete | | 32 | CR9-9 | complete |
| 15 | CR4-4 | complete | | 33 | CR9-10 | complete |
| 16 | CR8-4 | complete | | 34 | CR9-11 | incomplete |
| 17 | CR8-2 | complete | | 35 | CR9-13 | complete |
| 18 | CR8-3 | complete | | 36 | CR10-7 | complete |

32 complete, 4 incomplete, 0 wrong. Every regression test I ran was RED on the commit's parent
and GREEN on the commit. CR9-8 was judged by reading only.

## Follow-up items

1. **CR9-11:** in `rflog.py:183` catch OverflowError in `_int`. In `rflog.py:174` make `_num`
   return None when `not math.isfinite(v)`. Add `{"size": 1e400}` and `{"rssi": NaN}` to the
   test. **trivial**
2. **CR4-6:** at `service_binary_ops.py:395`, catch `Exception` and wrap it as
   `BinaryInstallError` like the OSError case. Test it with a `ValueError` from
   `validate_and_extract`. **trivial**
3. **CR1-4:** at `service_lifecycle_ops.py:2619`, compute `_whole` from the daemon instances
   still running after the stop (for example, `running_band`/the band view), not from
   `active_bands()`. Add a test: configured 433+868, only 433 running, a 433 stop clears the
   marker. **small**
4. **CR7-2:** at `service_maintenance.py:2100`, add `next_commands=["lhpc hmac status",
   "lhpc hmac recover"]`, and call `_hmac_try_auto_clear` before blocking on phase `unsafe`, as
   `service_hmac.py:470` does. **trivial**
5. **CR8-1:** add one service helper, `update_default_source(target)`, that resolves
   `stack_of`. Use it from `app.py:1592` and `cli/main.py:1562`. **trivial**
6. **CR7-16:** sweep the public mutating entries without `@invalidates_snapshot`
   (`graywolf_upstream_update`, `power_action`, …). Decorate each, or record why it needs no
   decorator. **small**
