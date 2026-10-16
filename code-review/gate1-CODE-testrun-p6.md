# Gate 1: testrun-p6 v5 (tests whose time is a real wait are marked slow)

**Request.** Judge the one commit below against PLAN-testrun v3 §3 P6 (excerpt below) and the handler's line (all marker decisions here; a marker only for real venv work or a timed loop, said per test). Judge: is "less than half its wall time on CPU" a fair measured reading of "timed loop", and is it applied consistently (the per-test table); is every marked case in the list and nothing else marked (the diff); is the venv finding right; is any claim stronger than the evidence.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## The plan, §3 P6 (PLAN-testrun v3)

> | P6 slow markers | the 25 M0 files no other packet owns | per ≥ 2 s test: "real venv" / "timed loop" → `@pytest.mark.slow`; "slow, neither" → none; the decision per test in the report | M0 table + the decision list |

## Commit (full message, `git log -1 --format=%B`)

### c04de901 testrun-p6: tests whose time is a real wait are marked slow

```text
testrun-p6: tests whose time is a real wait are marked slow

tests/README.md: `slow` marks the real-venv builds and timed loops. Of the
161 tests over 2 s in CI (run 38825597727, Python 3.11, on f874040d), 9
already carry the marker. Each other one was measured once more, locally
on the branch base, for its wall time and the CPU time of the test process
and its reaped children. A test spending less than half its wall time on
CPU waits on something real (a retry schedule, a bounded read, a stop
window, a lock timeout); 58 such cases (57 test functions), re-measured on
771d1a37, are now marked slow (one of them a parametrized case, test_backup_is_refused_while_a_lock_is_held
[config], through pytest.param). No test does real venv work without the
marker already. The other 94 are slow through work (CPU), not waiting:
no marker, as the rule says.

The marker's definition says the same now: pyproject.toml's `slow` line and
tests/README.md's Markers line add the timed wait (less than half the wall
time on CPU).

`-m slow` selects 76 tests instead of 18 (`--collect-only`); the full
suite is unchanged.

Test-suite change, no behaviour: no parent-red.

```

## The report

> # Code report: testrun-p6 v5 (tests whose time is a real wait are marked slow)
>
> ## Changes v4 → v5 (auditor round 2: RED, 1 finding; accepted; no code change)
>
> | finding | change |
> |---|---|
> | the grep did not rule out workflow-provided pytest options (`PYTEST_ADDOPTS`, another `-m` expression) | the evidence covers every way a marker expression can reach pytest from CI (env, `-m`/`-k`, config files, the collection hook), shown in full; the claim says only what it shows |
>
> ## Changes v3 → v4 (auditor round 1: RED, 1 finding; accepted; no code change)
>
> | finding | change |
> |---|---|
> | "still run in CI (no -m lane)" without evidence | the evidence block below (the workflow grep, CI's suite command, the pyproject lane line); the sentence now says only what that shows |
>
> ## Changes v2 → v3 (second read: CLEAN, 2 notes; both applied)
>
> | note | change |
> |---|---|
> | the marker's definition (pyproject.toml `slow`: "a real venv, or a timed retry loop"; tests/README.md "timed loops") is narrower than the rule applied | both now name the timed wait: "the test spends less than half its wall time on CPU" (pyproject.toml) / "a test that spends less than half its wall time on CPU" (tests/README.md) |
> | the shares were measured under `-n 4` | said in the method below: contention lengthens wall time, so a share near 0.5 could move at `-n 1`; the marked cases sit at ≤ 0.43; a flip only moves a test between the fast lane and the full suite |
>
> Branch `cons/testrun-p6-r1` on `origin/dev` = `a3733021`. Plan: PLAN-testrun v3 §3 P6 (gate 1 GREEN WITH NOTES), with
> the handler's line: every slow-marker decision is in this packet (plan v3 said per packet; moved to P6 on the
> handler's line), one rule, one list. Tests only.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | c04de901 | 21 test files, pyproject.toml, tests/README.md | the marker: 56 `@pytest.mark.slow` decorators + one `pytest.param(..., marks=pytest.mark.slow)`; its definition in pyproject.toml and tests/README.md | none: no behaviour |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 27 lines   OK
> docs         0 files, +0 lines; no fact given   OK
> tests        0 new tests; layers tests/cli=cli tests/core=core tests/golden=golden tests/host=host tests/install=install tests/stacks=stacks tests/web=web; red-before 0/0   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> ## The rule and how it was measured
>
> - tests/README.md, Markers: "`slow` marks the real-venv builds and timed loops." The handler's line: a marker only
>   where the test does real venv work or a timed loop, said per test; slow alone gets none.
> - **The population:** the 161 test calls of 2 s or more in CI run 38825597727 (Python 3.11, f874040d + `--durations=0
>   --durations-min=2`; `M0-durations-min2-3.11.txt`).
> - **Real venv work:** the test body creates a venv or runs pip / `install.sh` (read). None of the 161 does without
>   the marker already: the three a word search flagged do not (two run `uninstall.sh`, CPU-bound; one only lists venv
>   argv in a fake build plan and waits on a real `sleep` child).
> - **Timed wait** (the measured reading of "timed loop"): the test spends less than half its wall time on CPU, counting
>   the test process and its reaped children (`resource.getrusage(RUSAGE_SELF)` + `RUSAGE_CHILDREN`, around
>   `pytest_runtest_call`, a measuring plugin outside the repository, `-n 4`; contention under `-n 4` lengthens wall
>   time, so a share near 0.5 could move at `-n 1`; a flip only moves a test between the fast lane and the full suite). Its time is then a wait it cannot shorten:
>   a retry schedule, a bounded read, a stop window, a lock timeout. First measured on 941bcc5f, then the marked ones
>   again on 771d1a37 (after the efficiency landings): one (`test_success`) rose to 53 % CPU and keeps no marker.
> - **Not counted:** CPU of a child that another process reaps (none of the marked tests' time comes from such a child:
>   their CPU share is ≤ 0.43 on both runs).
>
> ## Counts
>
> | | cases |
> |---|---|
> | already marked slow | 9 |
> | marked slow here (timed wait) | 58 (57 test functions; `test_gps_monitor.py::test_a_satellite_report_in_the_window_replaces_the_last_list` has 2 cases) |
> | no marker (CPU work, or re-measured not wait-dominated) | 94 |
> | total | 161 |
>
> `pytest --collect-only -m slow`: 18 on 771d1a37 before, 76 after (18 + 58).
>
> ## Per test (CI s; local wall / CPU s on 941bcc5f; local wall / CPU s on 771d1a37 for the marked; decision)
>
> | test | CI | 941bcc5f | 771d1a37 | decision |
> |---|---|---|---|---|
> | `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` | 2.19 | 2.03 / 0.03 | 2.06 / 0.06 | slow: timed wait |
> | `tests/core/test_bounded_runner.py::test_run_returns_while_an_escaped_descendant_holds_stdout` | 4.01 | 4.01 / 0.00 | 4.01 / 0.00 | slow: timed wait |
> | `tests/core/test_bounded_runner.py::test_streaming_run_returns_and_flags_unverified_output` | 3.01 | 3.01 / 0.23 | 3.01 / 0.00 | slow: timed wait |
> | `tests/core/test_bounded_runner.py::test_timeout_kills_term_ignoring_child_that_outlives_parent` | 2.66 | 2.88 / 0.96 | 2.83 / 0.85 | slow: timed wait |
> | `tests/core/test_build_launcher_runtime.py::test_web_build_outlives_spec_step_timeout` | 3.03 | 3.08 / 3.33 | — | already marked |
> | `tests/core/test_build_plan.py::test_the_console_builds_a_component_as_the_cli_does` | 3.33 | 3.17 / 0.16 | 3.07 / 0.07 | slow: timed wait |
> | `tests/core/test_build_timeout.py::test_busy_build_outlives_its_manifest_value` | 3.34 | 3.14 / 3.53 | — | already marked |
> | `tests/core/test_group_requirement.py::test_stacks_page_shows_copyable_usermod_only_when_missing` | 3.84 | 1.13 / 1.12 | — | none: work (CPU) |
> | `tests/core/test_identity.py::test_a_stale_confirmed_band_is_refused_never_remapped` | 2.26 | 0.93 / 0.92 | — | none: work (CPU) |
> | `tests/core/test_identity.py::test_global_change_is_atomic_and_lock_serialized` | 15.23 | 15.04 / 0.03 | 15.04 / 0.03 | slow: timed wait |
> | `tests/core/test_identity.py::test_save_only_rechecks_the_identity_inside_its_write` | 4.58 | 3.31 / 0.30 | 3.37 / 0.37 | slow: timed wait |
> | `tests/core/test_identity.py::test_web_bandless_restart_is_one_band_end_to_end` | 4.52 | 3.21 / 0.21 | 3.25 / 0.24 | slow: timed wait |
> | `tests/core/test_identity.py::test_web_refusal_names_every_missing_field_on_the_settings_row` | 2.22 | 0.77 / 0.77 | — | none: work (CPU) |
> | `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` | 5.42 | 5.12 / 0.12 | 5.12 / 0.11 | slow: timed wait |
> | `tests/core/test_post_start.py::test_arm_write_exception_is_not_scheduled` | 4.28 | 4.26 / 0.00 | 4.26 / 0.01 | slow: timed wait |
> | `tests/core/test_post_start.py::test_bound_runner_sends_when_main_matches` | 3.08 | 3.07 / 0.05 | 3.07 / 0.43 | slow: timed wait |
> | `tests/core/test_post_start.py::test_bound_runner_skips_send_when_main_gone` | 2.07 | 2.03 / 0.03 | 2.06 / 0.04 | slow: timed wait |
> | `tests/core/test_post_start.py::test_bound_runner_skips_send_when_main_starttime_changed` | 2.07 | 2.03 / 0.03 | 2.06 / 0.05 | slow: timed wait |
> | `tests/core/test_post_start.py::test_bound_runner_skips_send_when_main_zombie` | 2.09 | 2.09 / 0.04 | 2.08 / 0.05 | slow: timed wait |
> | `tests/core/test_post_start.py::test_bound_runner_skips_send_when_session_or_group_changed` | 4.1 | 4.13 / 0.09 | 4.13 / 0.08 | slow: timed wait |
> | `tests/core/test_post_start.py::test_first_success_then_failed_repeats_exits_zero` | 2.08 | 2.07 / 0.05 | 2.07 / 0.04 | slow: timed wait |
> | `tests/core/test_post_start.py::test_no_attempt_starts_after_the_schedule_window` | 3.54 | 3.53 / 0.04 | — | already marked |
> | `tests/core/test_post_start.py::test_record_write_failure_delivers_no_payload` | 4.11 | 4.11 / 0.00 | 4.11 / 0.00 | slow: timed wait |
> | `tests/core/test_post_start.py::test_retry_later_listener_accepts_exits_zero` | 7.1 | 7.08 / 0.06 | 7.08 / 0.06 | slow: timed wait |
> | `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified` | 3.69 | 3.31 / 0.06 | 3.31 / 0.06 | slow: timed wait |
> | `tests/core/test_post_start.py::test_tcp_send_spaces_successful_deliveries_by_interval` | 2.08 | 2.07 / 0.06 | 2.07 / 0.06 | slow: timed wait |
> | `tests/core/test_post_start.py::test_tcp_wait_records_ready_and_timeout` | 2.14 | 2.13 / 0.11 | 2.13 / 0.10 | slow: timed wait |
> | `tests/core/test_post_start.py::test_the_start_waits_for_real_config_stability_after_post_start` | 18.42 | 18.07 / 0.07 | 18.07 / 0.07 | slow: timed wait |
> | `tests/core/test_process_ownership.py::test_an_identity_that_never_completes_is_returned_weak_within_the_bound` | 2.03 | 2.01 / 0.01 | 2.02 / 0.01 | slow: timed wait |
> | `tests/core/test_process_ownership.py::test_empty_cmdline_argv_rejected_despite_nonempty_digest` | 2.02 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
> | `tests/core/test_process_ownership.py::test_incomplete_identity_blocks_ownership` | 2.02 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
> | `tests/core/test_process_ownership.py::test_record_launch_rejects_empty_exec` | 2.02 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
> | `tests/core/test_process_ownership.py::test_terminate_unobserved_returns_false_when_still_alive` | 2.51 | 2.51 / 0.01 | 2.51 / 0.00 | slow: timed wait |
> | `tests/core/test_secrets_backup.py::test_backup_is_refused_while_a_lock_is_held[config]` | 2.06 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
> | `tests/core/test_secrets_backup.py::test_the_session_key_write_takes_the_config_lock_and_is_ephemeral_while_it_is_busy` | 2.01 | 2.00 / 0.00 | 2.00 / 0.00 | slow: timed wait |
> | `tests/core/test_snapshot_memo.py::test_a_render_reads_firewall_status_and_listeners_once` | 2.36 | 1.07 / 1.05 | — | none: work (CPU) |
> | `tests/core/test_snapshot_memo.py::test_each_request_reassesses_fresh` | 2.24 | 0.37 / 0.37 | — | none: work (CPU) |
> | `tests/core/test_snapshot_memo.py::test_every_traced_write_is_classified` | 26.95 | 4.88 / 4.56 | — | none: work (CPU) |
> | `tests/golden/test_golden_boot_restore.py::test_failed_after_the_hook_consumes_the_evidence` | 4.13 | 3.89 / 0.51 | 3.86 / 0.36 | slow: timed wait |
> | `tests/golden/test_golden_restart.py::test_restart_a_running_stack` | 2.65 | 2.65 / 0.75 | 2.28 / 0.42 | slow: timed wait |
> | `tests/golden/test_golden_start.py::test_unverified_termination_is_cleaned_up` | 4.13 | 3.79 / 0.24 | 3.87 / 0.31 | slow: timed wait |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_admission_refusal` | 2.73 | 1.31 / 1.27 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_band_owner` | 2.76 | 0.72 / 0.72 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_damaged_client_index` | 5.2 | 2.15 / 0.93 | 2.18 / 0.95 | slow: timed wait |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_firewall_gate` | 2.85 | 0.57 / 0.57 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_interactive_launch` | 3.64 | 1.00 / 0.97 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_interrupted_install` | 3.49 | 2.33 / 2.28 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_missing_identity` | 2.96 | 0.56 / 0.56 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_pending_config_journal` | 5.11 | 2.55 / 1.49 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_restart_of_an_interactive_stack` | 6.61 | 5.31 / 2.22 | 4.33 / 1.60 | slow: timed wait |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_success` | 5.24 | 2.20 / 0.94 | 2.21 / 1.17 | none: re-measured on 771d1a37 at CPU 53 % of wall (1.17 / 2.21 s), not wait-dominated |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_tx_without_high_power_permission` | 4.08 | 1.30 / 1.27 | — | none: work (CPU) |
> | `tests/golden/test_same_decision_across_entry_paths.py::test_unverified_termination` | 18.05 | 17.34 / 3.00 | 16.25 / 2.06 | slow: timed wait |
> | `tests/host/test_deploy_scripts.py::test_install_allows_uninstall_remainder` | 16.45 | 12.95 / 11.34 | — | already marked |
> | `tests/host/test_deploy_scripts.py::test_install_boot_enable_failure_is_unmistakable` | 12.58 | 8.45 / 6.83 | — | already marked |
> | `tests/host/test_deploy_scripts.py::test_install_enables_boot_restore_without_starting_it` | 16.29 | 8.85 / 7.25 | — | already marked |
> | `tests/host/test_deploy_scripts.py::test_install_full_creates_usable_layout_and_identity_ok` | 18.42 | 9.02 / 7.47 | — | already marked |
> | `tests/host/test_deploy_scripts.py::test_install_generates_canonical_updater_units` | 16.23 | 10.32 / 8.78 | — | already marked |
> | `tests/host/test_deploy_scripts.py::test_template_and_generated_unit_have_equivalent_security_semantics` | 17.4 | 7.79 / 6.27 | — | already marked |
> | `tests/host/test_deploy_scripts.py::test_uninstall_retry_after_reload_failure_succeeds` | 2.24 | 1.94 / 1.93 | — | none: work (CPU; runs uninstall.sh, no venv) |
> | `tests/host/test_deploy_scripts.py::test_uninstall_retry_recovers_a_newer_releases_staged_units` | 2.49 | 1.98 / 1.98 | — | none: work (CPU; runs uninstall.sh, no venv) |
> | `tests/host/test_deploy_scripts.py::test_uninstall_retry_recovers_leftover_staged_units_then_completes` | 2.56 | 1.96 / 1.96 | — | none: work (CPU; runs uninstall.sh, no venv) |
> | `tests/host/test_firewall.py::test_check_verdicts_and_busy_leaves_receipt_untouched` | 10.03 | 10.01 / 0.01 | 10.01 / 0.01 | slow: timed wait |
> | `tests/host/test_network_controls.py::test_web_ap_button_and_confirm` | 2.3 | 0.59 / 0.58 | — | none: work (CPU) |
> | `tests/host/test_port_exposure.py::test_port_pin_is_never_an_inert_tcp_anchor` | 2.31 | 0.93 / 0.92 | — | none: work (CPU) |
> | `tests/host/test_sdnotify.py::test_embedded_nul_is_rejected_never_truncated` | 2.0 | 2.00 / 0.00 | 2.00 / 0.00 | slow: timed wait |
> | `tests/install/test_auto_install.py::test_boundary_blocks_concurrent_update_and_start` | 10.59 | 10.15 / 0.14 | 10.13 / 0.12 | slow: timed wait |
> | `tests/install/test_auto_install.py::test_child_death_before_claim_is_ackable_while_spawner_lives` | 2.7 | 2.58 / 0.07 | 2.54 / 0.04 | slow: timed wait |
> | `tests/install/test_auto_install.py::test_rebind_write_failure_yields_orphan_risk` | 2.68 | 2.64 / 0.11 | 2.60 / 0.06 | slow: timed wait |
> | `tests/install/test_channel_switch_route.py::test_a_failed_switch_to_source_restores_the_binary_install` | 2.11 | 0.45 / 0.45 | — | none: work (CPU) |
> | `tests/install/test_channel_switch_route.py::test_binary_then_back_to_source` | 2.4 | 0.32 / 0.32 | — | none: work (CPU) |
> | `tests/repo/test_no_pin_literals_in_tests.py::test_no_test_hardcodes_a_manifest_pin` | 10.05 | 4.07 / 4.05 | — | none: work (CPU) |
> | `tests/repo/test_refusal_remedy.py::test_no_admission_refusal_is_built_by_hand` | 8.91 | 3.37 / 3.32 | — | none: work (CPU) |
> | `tests/repo/test_released_manifest_capture.py::test_released_manifests_still_parse` | 6.6 | 1.50 / 1.44 | — | none: work (CPU) |
> | `tests/stacks/test_gps.py::test_a_device_sending_binary_is_reported_not_waited_on` | 8.21 | 8.21 / 0.01 | 8.20 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps.py::test_a_gpsd_that_sends_nothing_never_admits_a_start` | 5.16 | 5.02 / 0.02 | 5.02 / 0.03 | slow: timed wait |
> | `tests/stacks/test_gps.py::test_auto_admits_a_stack_whose_gpsd_vanished_or_delivers_nothing` | 6.81 | 6.25 / 0.06 | 6.30 / 0.11 | slow: timed wait |
> | `tests/stacks/test_gps.py::test_explicit_gpsd_with_an_empty_gpsd_still_refuses_the_start` | 5.15 | 5.05 / 0.05 | 5.04 / 0.04 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_a_dop_only_report_does_not_replace_the_last_list` | 2.4 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_a_list_older_than_the_stale_bound_is_not_used` | 2.4 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_a_satellite_report_in_the_window_replaces_the_last_list[a new list wins]` | — | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_a_satellite_report_in_the_window_replaces_the_last_list[an empty list is the truth]` | — | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_a_window_without_a_satellite_report_keeps_the_last_list` | 2.4 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_altitude_datum_order_and_legacy_fallback` | 3.61 | 3.61 / 0.00 | 3.61 / 0.01 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list` | 2.41 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_the_last_list_belongs_to_one_gpsd` | 2.41 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_monitor.py::test_untagged_tpv_beside_several_devices_keeps_the_ambiguity` | 2.4 | 2.41 / 0.00 | 2.40 / 0.00 | slow: timed wait |
> | `tests/stacks/test_gps_nmea_monitor.py::test_only_gps_keys_during_start_get_the_wait` | 2.15 | 2.12 / 0.12 | 2.13 / 0.11 | slow: timed wait |
> | `tests/stacks/test_hardware.py::test_probe_result_renders_inline_and_is_consumed_once` | 2.52 | 0.48 / 0.48 | — | none: work (CPU) |
> | `tests/web/test_binary_stack_pages.py::test_a_binary_stack_is_not_offered_as_not_installed` | 4.1 | 0.68 / 0.68 | — | none: work (CPU) |
> | `tests/web/test_certificate_expiry_page.py::test_the_panel_says_a_damaged_client_list_needs_repair` | 2.8 | 0.51 / 0.51 | — | none: work (CPU) |
> | `tests/web/test_certificate_replacement.py::test_console_a_change_between_the_applies_shows_the_replacement_again[lan]` | 2.02 | 0.36 / 0.36 | — | none: work (CPU) |
> | `tests/web/test_certificate_replacement.py::test_console_a_change_between_the_applies_shows_the_replacement_again[san]` | 2.01 | 0.38 / 0.38 | — | none: work (CPU) |
> | `tests/web/test_certificate_replacement.py::test_console_an_explicit_san_removal_is_not_unioned_back` | 2.03 | 0.34 / 0.33 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_apply_live_disabled_unless_running_or_daemon` | 2.67 | 1.17 / 1.15 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_apply_live_failure_flashes_warning` | 2.37 | 0.45 / 0.44 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_apply_live_saves_then_reports` | 2.53 | 0.39 / 0.38 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_crafted_start_post_fields_are_ignored_not_applied` | 5.33 | 3.40 / 0.39 | 3.34 / 0.33 | slow: timed wait |
> | `tests/web/test_daemon_params_web.py::test_daemon_panel_follows_upper_band_switch` | 2.51 | 0.51 / 0.51 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_daemon_params_band_chooser_rendered` | 2.54 | 0.45 / 0.44 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_dp1_no_longer_opens_every_daemon_panel` | 2.05 | 0.25 / 0.25 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_dp_target_specific_opens_only_matching_panel` | 2.03 | 0.28 / 0.28 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_meshtastic_has_no_daemon_panel` | 2.22 | 0.64 / 0.64 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_panel_stays_open_after_save` | 3.04 | 0.63 / 0.63 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_save_rejects_out_of_range` | 2.45 | 0.39 / 0.39 | — | none: work (CPU) |
> | `tests/web/test_daemon_params_web.py::test_save_then_reset_daemon_params` | 2.95 | 0.48 / 0.47 | — | none: work (CPU) |
> | `tests/web/test_firewall_configure_route.py::test_a_refused_submission_leaves_the_applied_ruleset_as_it_was` | 2.2 | 0.57 / 0.57 | — | none: work (CPU) |
> | `tests/web/test_firewall_configure_route.py::test_the_recommended_preset_drops_every_route` | 2.09 | 0.54 / 0.54 | — | none: work (CPU) |
> | `tests/web/test_hardware_setup_route.py::test_a_refused_choice_changes_nothing[unknown-setup]` | 2.26 | 1.05 / 0.80 | — | none: work (CPU) |
> | `tests/web/test_high_power_web.py::test_banner_keys_on_the_running_sx127x_permission` | 2.34 | 0.68 / 0.67 | — | none: work (CPU) |
> | `tests/web/test_high_power_web.py::test_control_saves_through_the_form_and_refuses_junk` | 3.6 | 0.63 / 0.63 | — | none: work (CPU) |
> | `tests/web/test_high_power_web.py::test_no_sx127x_banner_on_an_sx1262` | 2.28 | 0.98 / 0.97 | — | none: work (CPU) |
> | `tests/web/test_hmac.py::test_the_meshcom_password_section_follows_the_hmac_state` | 2.07 | 0.32 / 0.32 | — | none: work (CPU) |
> | `tests/web/test_hmac_runner.py::test_controlled_runner_large_output_does_not_deadlock` | 4.22 | 0.41 / 0.57 | — | none: work (CPU) |
> | `tests/web/test_lazy_stacks.py::test_partial_body_route_renders_every_stack` | 2.4 | 0.96 / 0.95 | — | none: work (CPU) |
> | `tests/web/test_maintenance_health.py::test_a_failing_task_is_shown_with_its_time_until_it_succeeds[clock-normalise]` | 2.0 | 0.27 / 0.27 | — | none: work (CPU) |
> | `tests/web/test_pin_override_web.py::test_confirm_meshcom_pin_refusal_hint_and_selfupdate_no_checkbox[install]` | 2.29 | 1.03 / 1.01 | — | none: work (CPU) |
> | `tests/web/test_pin_override_web.py::test_confirm_meshcom_pin_refusal_hint_and_selfupdate_no_checkbox[update]` | 2.31 | 1.08 / 1.02 | — | none: work (CPU) |
> | `tests/web/test_pin_override_web.py::test_post_with_the_checkbox_and_token_runs_the_plan_with_the_override` | 2.37 | 0.34 / 0.34 | — | none: work (CPU) |
> | `tests/web/test_pin_override_web.py::test_post_without_the_checkbox_is_still_refused` | 2.39 | 0.36 / 0.36 | — | none: work (CPU) |
> | `tests/web/test_pin_override_web.py::test_the_pill_and_the_stack_page_warning_while_the_override_is_active` | 3.9 | 1.17 / 1.16 | — | none: work (CPU) |
> | `tests/web/test_pin_override_web.py::test_the_token_reaches_the_install_jobs_argv` | 2.47 | 0.33 / 0.33 | — | none: work (CPU) |
> | `tests/web/test_rflog_web.py::test_logging_sets_every_owner_at_once_and_shows_mixed` | 2.58 | 1.03 / 1.02 | — | none: work (CPU) |
> | `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/logs/rns/clear-data2]` | 2.22 | 0.37 / 0.36 | — | none: work (CPU) |
> | `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/rflog/all-data1]` | 2.43 | 0.37 / 0.37 | — | none: work (CPU) |
> | `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/rflog/clear-all-data3]` | 2.22 | 0.78 / 0.78 | — | none: work (CPU) |
> | `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/stacks/reticulum/rflog-data0]` | 2.27 | 0.95 / 0.94 | — | none: work (CPU) |
> | `tests/web/test_rflog_web.py::test_the_stack_switch_saves_one_key_on_the_owner_and_returns_to_the_page` | 2.32 | 0.52 / 0.52 | — | none: work (CPU) |
> | `tests/web/test_stackweb.py::test_a_saved_disable_keeps_the_live_proxy_visible_until_apply` | 2.78 | 0.47 / 0.47 | — | none: work (CPU) |
> | `tests/web/test_stackweb.py::test_a_saved_proxy_port_move_keeps_advertising_the_port_that_still_answers` | 2.45 | 0.38 / 0.38 | — | none: work (CPU) |
> | `tests/web/test_stackweb.py::test_a_stored_password_never_reaches_the_json_apis_or_flashes` | 2.52 | 0.39 / 0.39 | — | none: work (CPU) |
> | `tests/web/test_stackweb.py::test_panel_renders_with_the_bypass_warning` | 2.01 | 0.32 / 0.32 | — | none: work (CPU) |
> | `tests/web/test_stackweb.py::test_password_section_states_absent_lax_malformed_and_unreadable_without_a_value` | 2.83 | 0.46 / 0.46 | — | none: work (CPU) |
> | `tests/web/test_stackweb.py::test_the_bulk_form_preselects_the_policy_the_pages_share` | 2.1 | 0.78 / 0.78 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_a_blocked_or_pending_spawn_flashes_and_returns` | 2.26 | 1.03 / 1.00 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_a_refused_plan_flashes_where_the_operator_came_from` | 2.11 | 0.41 / 0.41 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_an_inline_update_whose_build_job_is_refused_ends_as_the_clis_failed_build` | 2.29 | 1.13 / 1.11 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_binary_install_apply_is_not_gated_on_build_dependencies` | 2.61 | 0.77 / 0.76 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_confirm_working_button_hidden_when_stopped_or_recorded` | 2.78 | 0.54 / 0.53 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_deferred_webserver_apply_is_announced_on_every_page_until_the_firewall_runs` | 2.09 | 0.40 / 0.40 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_fetched_binary_stack_offers_uninstall_in_the_console` | 3.31 | 1.92 / 1.91 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_fetched_stack_shows_its_version_and_an_update_when_the_pin_moves` | 3.39 | 1.58 / 1.53 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_get_routes_make_no_network_calls` | 2.21 | 0.34 / 0.34 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` | 3.03 | 0.53 / 0.53 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_install_runs_as_live_logged_job` | 4.42 | 3.22 / 0.21 | 3.16 / 0.16 | slow: timed wait |
> | `tests/web/test_web.py::test_multi_band_config_stored_per_band` | 2.88 | 0.48 / 0.47 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_no_inline_style_or_script_on_pages` | 2.92 | 0.54 / 0.54 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_restart_required_chip_on_stack_page_and_dashboard` | 2.13 | 0.42 / 0.41 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_routine_restart_runs_with_no_page_between` | 2.26 | 0.93 / 0.92 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_routine_start_runs_with_no_page_between` | 2.15 | 0.69 / 0.68 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_server_forced_open_marks_data_force_open` | 2.53 | 0.43 / 0.42 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_settings_cfg_query_opens` | 2.0 | 1.10 / 1.09 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_the_meshcore_mode_switch_on_the_stack_body_saves_the_same_setting` | 3.23 | 0.81 / 0.79 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_the_notice_link_opens_the_firewall_apply_section` | 2.01 | 0.34 / 0.33 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_the_removed_settings_panel_stays_removed` | 2.74 | 0.65 / 0.64 | — | none: work (CPU) |
> | `tests/web/test_web.py::test_the_settings_card_refusal_does_not_ask_for_two_saves` | 2.46 | 0.43 / 0.42 | — | none: work (CPU) |
> | `tests/web/test_web_auto_install.py::test_the_limit_a_used_token_is_remembered_for_256_later_consumptions_whatever_the_time[255]` | 48.33 | 7.80 / 7.75 | — | none: work (CPU) |
> | `tests/web/test_web_auto_install.py::test_the_limit_a_used_token_is_remembered_for_256_later_consumptions_whatever_the_time[256]` | 54.18 | 7.59 / 7.54 | — | none: work (CPU) |
> | `tests/web/test_webjob.py::test_proven_terminated_timeout_step_records_failed` | 2.52 | 2.33 / 0.03 | 2.34 / 0.04 | slow: timed wait |
> | `tests/web/test_webjob.py::test_unverified_timeout_step_records_unsafe` | 2.46 | 2.33 / 0.03 | 2.36 / 0.06 | slow: timed wait |
> | `tests/web/test_webserver.py::test_fetch_commands_render_in_every_serving_mode` | 4.85 | 0.87 / 0.86 | — | none: work (CPU) |
>
> ## The 6-point block
>
> 1. **Contracts:** none. 2. **Invariants:** none. 3. **Failure classes:** in CI a marked test runs as before: no workflow passes `-m` or sets `PYTEST_ADDOPTS`, and
>    nothing in the suite's configuration deselects `slow` (evidence below). Only an `-m "not slow"` run leaves it out:
>    pyproject.toml:90 names that lane, docs/maintenance.md:330 a Pi run of it.
> 4. **Test rules:** the Markers section.
> 5. **Directories:** every directory the commit touches (cli, core, golden, host, install, stacks, web) plus repo, i.e.
>    all of `tests/`: `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests` → `7747 passed, 1
>    skipped in 97.81s`; both ruff runs → `All checks passed!`.
> 6. **Adversarial self-review:**
>    - (a) The 0.5 CPU share is a threshold chosen here; the marked cases sit at ≤ 0.43, most at ≤ 0.1 (the table).
>    - (b) Local wall times are shorter than CI's; the population is CI's (≥ 2 s there); the local run decides only wait
>      versus work.
>    - (c) Two ids of the CI table carry spaces in their parameter; they were run through their function id (both
>      cases).
>    - (d) The branch's first code commit (dacb65d5, pushed in 2f96ea94; its report cited an earlier, unpushed sha)
>      still carried the marker on `test_success`: the removal stayed unstaged. Found before the auditor's round by
>      `git status`; the commit was rebuilt (a4cb9e0b). The `-m slow` count and the full run above were taken on the
>      corrected tree.
>
>
> ## Evidence: how a marker expression could reach pytest in CI (measured on a3733021)
>
> ```text
> $ git grep -nE 'PYTEST_ADDOPTS|(^|[^a-z])-m[ =]|--markers|-k ' -- .github tools pyproject.toml
> .github/workflows/ci.yml:62:          retry 3 python -m pip install --upgrade pip
> .github/workflows/ci.yml:66:        run: python -m compileall -q lhpc
> .github/workflows/ci.yml:85:        # `python -m pytest`, NOT the console script: the package is installed non-editable
> .github/workflows/ci.yml:91:        run: python -m pytest -q -rs -n auto --dist loadfile --cov=lhpc --cov-branch --cov-report=term:skip-covered --cov-report=xml:coverage.xml
> .github/workflows/ci.yml:152:          retry 3 python -m pip install --upgrade pip
> .github/workflows/ci.yml:156:        run: python -m pytest -q -p no:cacheprovider tests/repo/test_fake_signatures.py tests/repo/test_unit_templates_frozen.py
> .github/workflows/ci.yml:196:          python -m tests.policy_covers_manifest \
> .github/workflows/ci.yml:294:          retry 3 python -m pip install -q --upgrade pip
> .github/workflows/ci.yml:295:          retry 3 python -m pip install -q /tmp/openhop-core meshcore pytest pytest-asyncio
> .github/workflows/ci.yml:296:          retry 3 python -m pip install -q --no-deps ./lhpc/data/meshcore_host
> .github/workflows/ci.yml:322:          retry 3 python -m pip install -q --upgrade pip
> .github/workflows/ci.yml:323:          retry 3 python -m pip install -q "$ver" pycryptodomex==3.23.0 pytest
> .github/workflows/ci.yml:363:          retry 3 python -m pip install -q --upgrade pip
> .github/workflows/ci.yml:364:          retry 3 python -m pip install -q /tmp/rns pytest
> .github/workflows/ci.yml:365:          retry 3 python -m pip install -q --no-deps /tmp/lxmf
> .github/workflows/ci.yml:367:          retry 3 python -m pip install -q --no-deps /tmp/rnsif
> .github/workflows/pages.yml:40:          retry 3 python -m pip install --upgrade build
> .github/workflows/pages.yml:76:          ( cd web && python3 -m http.server 8099 >/tmp/httpd.log 2>&1 & )
> .github/workflows/testlab.yml:90:                python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
> .github/workflows/testlab.yml:98:                backoff python -m playwright install chromium
> .github/workflows/testlab.yml:107:                python -m pytest testlab/tests/unit -q
> .github/workflows/testlab.yml:112:                  python -m pytest testlab/tests/acceptance -q -rs
> .github/workflows/testlab.yml:113:                LHPC_BROWSER=1 python -m pytest testlab/tests/browser -q -rs"'
> .github/workflows/testlab.yml:207:          python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
> .github/workflows/testlab.yml:224:            python -m pytest testlab/tests/release -q -rs -x \
> .github/workflows/testlab.yml:232:              install -m 0755 /repo/lane.sh /tmp/lane.sh
> .github/workflows/testlab.yml:348:            ". /repo/.github/scripts/retry.sh && cp -r /repo /tmp/src && backoff python3 -m pip download -q -d /xfer/wheels $cli 'setuptools>=68' /tmp/src"
> .github/workflows/testlab.yml:441:          python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
> .github/workflows/testlab.yml:455:            python -m pytest testlab/tests/slowbuild -q -rs \
> .github/workflows/testlab.yml:468:              install -m 0755 /repo/lane.sh /tmp/lane.sh
> pyproject.toml:88:  "contract: Tier 0 — the readable core, one lane that state…
> pyproject.toml:89:  "safety(id): a contract case that guards a named SAFETY in…
> pyproject.toml:90:  "slow: … Excluded by the fast lane `-m 'not slow'`; always run by the complete suite.",
> (exit 0)
> $ git grep -nE "PYTEST_" -- .github tools pyproject.toml
> (no output; exit 1)
> $ sed -n 224,225p .github/workflows/testlab.yml; sed -n 455,456p .github/workflows/testlab.yml   # the two multi-line pytest commands
>             python -m pytest testlab/tests/release -q -rs -x \
>               --junitxml=/out/junit-release.xml
>             python -m pytest testlab/tests/slowbuild -q -rs \
>               --junitxml=/out/junit-slow-build.xml
> $ ls setup.cfg tox.ini pytest.ini
> (none exists)
> ```
>
> What it shows: no workflow sets `PYTEST_ADDOPTS` or any `PYTEST_` variable; every `-m` in `.github` is
> `python -m <module>` or `install -m 0755`; no pytest command in a workflow carries `-m` or `-k` (the two
> multi-line ones continue only with `--junitxml`); the `tests/` suite runs in CI at ci.yml:91 and ci.yml:156; there is
> no setup.cfg, tox.ini or pytest.ini; `addopts` (pyproject.toml:79) is `["--strict-markers"]`; tests/conftest.py's
> collection hook skips on `needs_session`, `needs_nonroot` and `requires_zstd` only. The only `-m 'not slow'` lane is
> the one pyproject.toml:90 names (docs/maintenance.md:330: a Pi run).
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | 771d1a37 |
> | marked | `grep -c 'mark.slow                   # timed wait'` over the changed files, + the one `pytest.param` | 56 + 1 |
> | `-m slow` before/after | `pytest --collect-only -q -m slow` | 18 / 76 |
> | full tests/ | the command in 5 | 7747 passed, 1 skipped |

## The full diff (`git diff a373302116747be8f46f250622299a3efa2c8cf3 c04de9017d8bbd523ed00ec4171c6c942ff2f2b6`)

```diff
diff --git a/pyproject.toml b/pyproject.toml
index 960b76db..446188a4 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -87,7 +87,7 @@ filterwarnings = ["error::pytest.PytestUnhandledThreadExceptionWarning"]
 markers = [
   "contract: Tier 0 — the readable core, one lane that states what LHPC PROMISES. Each tagged case goes through the widest public seam (CLI verb / Flask route / typed ActionResult) and expresses a happy path or the refusal that defines a boundary. Run it with `-m contract`; it must be green and quick.",
   "safety(id): a contract case that guards a named SAFETY invariant. The id is the P0.x/P1.x id of the guarantee where one exists (P0.5 uninstall, P0.6 GET-no-network), else a descriptive slug (RF-TX-opt-in / firewall-fail-closed / exposure-fail-closed). Run the invariant set with `-m safety`.",
-  "slow: a genuinely slow test (real bash sub-process building a real venv, or a timed retry loop). Excluded by the fast lane `-m 'not slow'`; always run by the complete suite.",
+  "slow: a genuinely slow test (real bash sub-process building a real venv, or a timed retry loop, or a timed wait: the test spends less than half its wall time on CPU). Excluded by the fast lane `-m 'not slow'`; always run by the complete suite.",
   "needs_session: requires a real POSIX session (sid>0) — the product's procident.identity_complete refuses sid==0, so these tests fail in a sandbox whose processes have session id 0 (run them under `setsid`).",
   "needs_nonroot: requires a non-root euid — a chmod-based permission fixture does not bind for root.",
   "requires_zstd: crosses the production extraction boundary (`zstd -dc`), so it needs the host `zstd` binary. Skipped (with reason) where it is absent; the target and CI install it, so it normally runs.",
diff --git a/tests/README.md b/tests/README.md
index e1c17294..7b2e481f 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -126,7 +126,8 @@ All markers are declared in `pyproject.toml`; `--strict-markers` rejects a typo.
   boundary.
 - `-m safety` is every case guarding a named invariant of the
   [safety model](../docs/architecture.md). The two lanes overlap; neither is a subset of the other.
-- `slow` marks the real-venv builds and timed loops.
+- `slow` marks the real-venv builds, the timed loops and the timed waits (a test that spends less than half its
+  wall time on CPU).
 - `requires_zstd`, `needs_session`, `needs_nonroot`, `needs_git_checkout`, `no_default_hardware` and
   `no_default_display` state an environmental requirement or opt-out; `needs_git_checkout` skips in a
   source export and fails under CI (`CI` set).
diff --git a/tests/cli/test_cli.py b/tests/cli/test_cli.py
index 0f00d002..461f573b 100644
--- a/tests/cli/test_cli.py
+++ b/tests/cli/test_cli.py
@@ -1323,6 +1323,7 @@ def test_plumbing_flag_runs_with_invocation_id(argv, method, unit, use, tmp_path
 
 
 @pytest.mark.safety("config-transaction")
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts(capsys):
     """EAGER CLEANUP, not the invariant (that is `config_lock`, tests/core/test_config.py): a
     journal a crashed process left behind is finished and reported when the next `lhpc` process
diff --git a/tests/core/test_bounded_runner.py b/tests/core/test_bounded_runner.py
index c4b84676..3be283b9 100644
--- a/tests/core/test_bounded_runner.py
+++ b/tests/core/test_bounded_runner.py
@@ -60,6 +60,7 @@ def _dead_or_zombie(pid: int) -> bool:
         return True                                       # /proc gone -> dead
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_timeout_kills_term_ignoring_child_that_outlives_parent():
     # The parent spawns a child that IGNORES SIGTERM and, on its own SIGTERM, the parent
     # exits immediately — so the leader dies but the child survives in the original session.
@@ -309,6 +310,7 @@ def _kill(pid_text):
         pass
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_run_returns_while_an_escaped_descendant_holds_stdout():
     # Closing the pipe used to wait for the drain thread's buffer lock, i.e. for the escapee to
     # exit: the bounded joins did not bound the call.
@@ -321,6 +323,7 @@ def test_run_returns_while_an_escaped_descendant_holds_stdout():
         _kill(r.stdout)
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_streaming_run_returns_and_flags_unverified_output(tmp_path):
     log = tmp_path / "job.log"
     t0 = time.time()
diff --git a/tests/core/test_build_plan.py b/tests/core/test_build_plan.py
index 55f6510a..fff77a45 100644
--- a/tests/core/test_build_plan.py
+++ b/tests/core/test_build_plan.py
@@ -216,6 +216,7 @@ def test_the_receipt_records_the_revision_read_under_the_locks(tmp_path, monkeyp
         f"consumed openhop-repeater-src {moved[0]}\n")
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_the_console_builds_a_component_as_the_cli_does(tmp_path, monkeypatch):
     """A console Build of one component builds what `lhpc build <component>` builds — that
     component — even while a provider of it is not built (the stack's Build builds providers
diff --git a/tests/core/test_identity.py b/tests/core/test_identity.py
index 0d2ef8e4..81f3b4ff 100644
--- a/tests/core/test_identity.py
+++ b/tests/core/test_identity.py
@@ -390,6 +390,7 @@ def test_global_change_marking_is_band_aware(tmp_path, monkeypatch):
     assert "lhpc stack restart chat" in (r.next_commands or [])
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_global_change_is_atomic_and_lock_serialized(tmp_path, monkeypatch):
     # the [operator] patch and the restart markers are ONE all-or-recoverable
     # transaction inside ONE config-lock critical section.
@@ -1017,6 +1018,7 @@ def test_an_identity_no_op_writes_no_restart_marker(tmp_path, monkeypatch):
     m = svc.restart_required("chat")
     assert m["mode"] == "build" and m["params"] == ["firmware env", "call"], m
     assert m["band"] == "433", m
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_save_only_rechecks_the_identity_inside_its_write(tmp_path, monkeypatch):
     # REVIEW-FOUND: Save-only is the one panel path that does NOT defer the identity to a launch,
     # so its `_identity_guard=True` in-transaction recheck is what stops a raced blank from
@@ -1456,6 +1458,7 @@ def test_web_restart_identity_refusal_happens_before_any_stop(tmp_path, monkeypa
     assert stops == []                                         # refused BEFORE any stop
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_web_bandless_restart_is_one_band_end_to_end(tmp_path, monkeypatch):
     # A bandless Web restart of a stack running on 868 runs on 868 — the 433 store is untouched.
     svc = _svc(tmp_path)
diff --git a/tests/core/test_op_serialization.py b/tests/core/test_op_serialization.py
index b4442ef3..1c69f5bd 100644
--- a/tests/core/test_op_serialization.py
+++ b/tests/core/test_op_serialization.py
@@ -112,6 +112,7 @@ def test_clean_rechecks_running_after_locks(tmp_path, monkeypatch):
     assert _snapshot_state(tmp_path, paths) == before      # even config/log/markers untouched
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_multi_source_update_holds_locks_across_groups(tmp_path, monkeypatch):
     # daemon stack updates TWO source groups (loraham-daemon + RadioLib). A Start injected
     # BETWEEN the groups contends on the still-held source locks: typed refusal, nothing
diff --git a/tests/core/test_post_start.py b/tests/core/test_post_start.py
index 16b2a188..68b35fa5 100644
--- a/tests/core/test_post_start.py
+++ b/tests/core/test_post_start.py
@@ -93,6 +93,7 @@ def _kiss_svc(tmp_path):
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_start_required_post_start_failure_is_unverified(tmp_path, monkeypatch, set_call, real_spawn):
     # Drives ControllerService.start() and exercises the CALLER's handling of the typed
     # required-post-start result (the bug was the caller using a nonexistent Outcome.ok).
@@ -337,6 +338,7 @@ def test_optional_retry_all_fail_exits_zero():
                            "optional": True, "repeat": 3, "interval": 0}]) == 0   # optional never gates
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_retry_later_listener_accepts_exits_zero():
     port = _free_port()
     _serve_once(port, delay=1.5)
@@ -344,6 +346,7 @@ def test_retry_later_listener_accepts_exits_zero():
                            "required": True, "repeat": 8, "interval": 1}]) == 0
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_first_success_then_failed_repeats_exits_zero():
     port = _free_port()
     _serve_once(port, delay=0.0, accepts=1)                          # serves ONE then stops
@@ -725,16 +728,19 @@ def _run_bound(binding, port, data="P\n", repeat=4, interval=1, timeout=18):
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_bound_runner_sends_when_main_matches():
     assert _run_bound(_self_binding(), _free_port()) == b"P\n"
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_bound_runner_skips_send_when_main_starttime_changed():
     # main pid reused with a NEW start time (crash + later launch) -> old runner must not send.
     assert _run_bound(_self_binding(main_starttime=1), _free_port()) == b""
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_bound_runner_skips_send_when_main_gone():
     dead = {"main_launch_id": "x", "main_pid": 2 ** 31 - 1, "main_starttime": 1,
             "main_pgid": 1, "main_sid": 1}
@@ -933,6 +939,7 @@ def test_main_exit_before_post_scheduling_yields_no_unbound_runner(tmp_path):
     assert life.owned_records("pr", role="post") == []
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_record_write_failure_delivers_no_payload(tmp_path, monkeypatch):
     import socket
     life = _real_life(tmp_path)
@@ -954,6 +961,7 @@ def test_record_write_failure_delivers_no_payload(tmp_path, monkeypatch):
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_bound_runner_skips_send_when_main_zombie(tmp_path):
     import subprocess
     z = subprocess.Popen(["true"])                                   # exits immediately -> zombie
@@ -978,6 +986,7 @@ def test_bound_runner_skips_send_when_main_zombie(tmp_path):
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_bound_runner_skips_send_when_session_or_group_changed():
     assert _run_bound(_self_binding(main_sid=999999), _free_port()) == b""
     assert _run_bound(_self_binding(main_pgid=999999), _free_port()) == b""
@@ -1010,6 +1019,7 @@ def test_render_rejects_malformed_binding_and_nonstring_skip_values():
     commands.render_post_launcher([ok], _C(), {}, _Op(), "/rt", "/src", "")   # valid -> no raise
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_tcp_send_spaces_successful_deliveries_by_interval(tmp_path):
     # Three SUCCESSFUL deliveries (listener accepts every attempt) are measurably spaced by the
     # validated interval — the sleep applies after a success, not only after a failure.
@@ -1044,6 +1054,7 @@ def test_tcp_send_spaces_successful_deliveries_by_interval(tmp_path):
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_arm_write_exception_is_not_scheduled(tmp_path, monkeypatch):
     # os.write raising AFTER a durable record: no payload, not scheduled, record removed once
     # cessation is proven.
@@ -1553,6 +1564,7 @@ def test_exec_detail_is_bounded_and_traceback_free(tmp_path):
     assert '"' not in st["detail"] and "`" not in st["detail"]
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_tcp_wait_records_ready_and_timeout(tmp_path):
     import json
     import socket as _socket
@@ -2139,6 +2151,7 @@ def test_no_config_lock_is_held_while_required_post_start_runs(
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_the_start_waits_for_real_config_stability_after_post_start(
         tmp_path, monkeypatch, set_call, real_spawn):
     """AUDIT-FOUND: the release used to give up re-acquiring after a timeout and CONTINUE, leaving
diff --git a/tests/core/test_process_ownership.py b/tests/core/test_process_ownership.py
index 1c495e2c..847c3af0 100644
--- a/tests/core/test_process_ownership.py
+++ b/tests/core/test_process_ownership.py
@@ -285,6 +285,7 @@ def test_start_fails_when_ownership_record_cannot_persist(tmp_path, monkeypatch)
 
 # --- truthful cleanup + complete-identity ownership ---------------------
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_incomplete_identity_blocks_ownership(tmp_path, reaper, monkeypatch):
     # An OBSERVED process whose /proc identity is incomplete must not be recorded.
     life = _life(tmp_path)
@@ -295,6 +296,7 @@ def test_incomplete_identity_blocks_ownership(tmp_path, reaper, monkeypatch):
     assert life.owned_records("loraham-daemon") == []
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_terminate_unobserved_returns_false_when_still_alive(tmp_path, reaper, monkeypatch):
     # If the residual process does NOT cease, cleanup must report False (not a lie).
     life = _life(tmp_path)
@@ -322,6 +324,7 @@ def test_record_launch_rejects_none_identity(tmp_path, reaper, monkeypatch):
     assert life.record_launch(STACK, COMP, p.pid, band="433") is False
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_record_launch_rejects_empty_exec(tmp_path, reaper, monkeypatch):
     life = _life(tmp_path)
     p = _leader(reaper)
@@ -344,6 +347,7 @@ def test_proc_ceased_transient_read_failure_is_not_cessation(tmp_path, reaper, m
     assert life._proc_ceased(p.pid) is False     # transient failure != ceased
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_empty_cmdline_argv_rejected_despite_nonempty_digest(tmp_path, reaper, monkeypatch):
     import hashlib
     life = _life(tmp_path)
@@ -988,6 +992,7 @@ def test_an_identity_that_completes_late_is_still_captured_complete(tmp_path, re
     assert life._identity_complete(life._capture_identity(p.pid))
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_an_identity_that_never_completes_is_returned_weak_within_the_bound(tmp_path, reaper):
     life = _life(tmp_path)
     p = _leader(reaper)
diff --git a/tests/core/test_secrets_backup.py b/tests/core/test_secrets_backup.py
index 18335b85..fd9ca2e7 100644
--- a/tests/core/test_secrets_backup.py
+++ b/tests/core/test_secrets_backup.py
@@ -165,7 +165,8 @@ def test_backup_is_refused_while_an_included_stack_runs(tmp_path, monkeypatch):
     assert not (tmp_path / "b.tar").exists()
 
 
-@pytest.mark.parametrize("held", ["admission", "config", "pki"])
+@pytest.mark.parametrize("held", ["admission", pytest.param("config", marks=pytest.mark.slow),  # timed wait (M0)
+                                  "pki"])
 def test_backup_is_refused_while_a_lock_is_held(tmp_path, held):
     a = _root(tmp_path, "a")
     svc = _seed(a)
@@ -285,6 +286,7 @@ def test_a_root_without_config_tls_lists_it_absent_and_restores_so(tmp_path):
 
 # ---- the console's session key ------------------------------------------------------------
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_the_session_key_write_takes_the_config_lock_and_is_ephemeral_while_it_is_busy(tmp_path):
     a = _root(tmp_path, "a")
     p = Paths(runtime_root=a)
diff --git a/tests/golden/test_golden_boot_restore.py b/tests/golden/test_golden_boot_restore.py
index ea8399bb..ae9420ca 100644
--- a/tests/golden/test_golden_boot_restore.py
+++ b/tests/golden/test_golden_boot_restore.py
@@ -148,6 +148,7 @@ def test_refused_before_the_hook_stays_pending(kiss_box, prior_boot, run_op,
     assert run.files == {"added": ["state/boot-restore.json"], "removed": [], "changed": []}
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_failed_after_the_hook_consumes_the_evidence(kiss_box, prior_boot, run_op):
     """intended: a start that fails after its hook (the endpoint never came up — UNVERIFIED,
     cleaned up) settles the item `failed` with the component outcome, and consumes the
diff --git a/tests/golden/test_golden_restart.py b/tests/golden/test_golden_restart.py
index 065966c4..89dd1bd8 100644
--- a/tests/golden/test_golden_restart.py
+++ b/tests/golden/test_golden_restart.py
@@ -57,6 +57,7 @@ def test_plan_is_read_only(kiss_box, run_op):
     assert plan.files == NOTHING
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_restart_a_running_stack(kiss_box, run_op):
     """intended: all locks and rechecks first, then a verified stop, then a verified start; the
     old ownership record is replaced by the new one."""
diff --git a/tests/golden/test_golden_start.py b/tests/golden/test_golden_start.py
index a52994b6..4ae1f7b2 100644
--- a/tests/golden/test_golden_start.py
+++ b/tests/golden/test_golden_start.py
@@ -153,6 +153,7 @@ def test_refused_when_the_band_owner_does_not_stop(kiss_box, run_op):
     assert run.files == NOTHING
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_unverified_termination_is_cleaned_up(kiss_box, run_op):
     """intended: a TNC that is alive but never opens its ready endpoint (observed absent through
     the whole bounded wait) is stopped again (identity-verified) and reported UNVERIFIED; no
diff --git a/tests/golden/test_same_decision_across_entry_paths.py b/tests/golden/test_same_decision_across_entry_paths.py
index e37acba0..d829a785 100644
--- a/tests/golden/test_same_decision_across_entry_paths.py
+++ b/tests/golden/test_same_decision_across_entry_paths.py
@@ -508,6 +508,7 @@ def test_pending_config_journal(entry):
         assert cfgmod.load_stack_config(Paths(runtime_root=r["root"]), "kiss") == PENDING_SAVED
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_damaged_client_index(entry):
     """intended: a damaged client-certificate index gates certificate operations only — no start
     path decides on it, so every path starts alike."""
@@ -529,6 +530,7 @@ def test_interactive_launch(entry):
     assert _renders(res) == SUCCEEDED
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_unverified_termination(entry):
     """intended: a TNC that is alive but never opens its endpoint is cleaned up and reported
     UNVERIFIED on every path; each renders it as a failure — the CLI and the web's child exit 1,
@@ -540,6 +542,7 @@ def test_unverified_termination(entry):
     assert _renders(res) == FAILED_AFTER_ADMISSION
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_restart_of_an_interactive_stack(entry):
     """intended: a restart of an interactive stack ends like its start (above): the CLI, the web
     and the job runner count a MANUAL_REQUIRED-only result as success (exit 0, job done).
diff --git a/tests/host/test_firewall.py b/tests/host/test_firewall.py
index 53fca4c0..945462bb 100644
--- a/tests/host/test_firewall.py
+++ b/tests/host/test_firewall.py
@@ -580,6 +580,7 @@ def test_secure_default_aborts_on_unresolvable_ssh_compat_proceeds(tmp_path):
     assert fh.op_apply(sysx, str(p2), etc_dir=etc, run_dir=run) == fh.EXIT_OK
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_check_verdicts_and_busy_leaves_receipt_untouched(tmp_path):
     import json as _json
     from lhpc.core import firewall_helper as fh
diff --git a/tests/host/test_sdnotify.py b/tests/host/test_sdnotify.py
index 27d48b92..5964f291 100644
--- a/tests/host/test_sdnotify.py
+++ b/tests/host/test_sdnotify.py
@@ -78,6 +78,7 @@ def test_socket_errors_are_swallowed(monkeypatch, tmp_path):
 
 # --- sanitation: the payload is a protocol ------------------------------------------------------
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_embedded_nul_is_rejected_never_truncated(sock):
     # A NUL truncates the datagram at the C boundary — half a status would read as a whole one.
     assert sdnotify.notify("STATUS=good\x00EVIL") is False
diff --git a/tests/install/test_auto_install.py b/tests/install/test_auto_install.py
index 8d366443..bdf5f640 100644
--- a/tests/install/test_auto_install.py
+++ b/tests/install/test_auto_install.py
@@ -293,6 +293,7 @@ def test_ack_archives_marker_no_follow(tmp_path):
 # ---- lease boundary blocks concurrent ops ------------------------------------------------
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_boundary_blocks_concurrent_update_and_start(tmp_path):
     svc = _svc(tmp_path)
     src_paths = ["src/loraham-kiss-tnc", "src/loraham-daemon", "src/RadioLib"]
@@ -779,6 +780,7 @@ def _dead_ident():
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_child_death_before_claim_is_ackable_while_spawner_lives(tmp_path, monkeypatch):
     # The reservation is bound to the CHILD after spawn: a child that dies before claiming
     # becomes DEAD and acknowledgeable even though the web-server/spawner process (this
@@ -1216,6 +1218,7 @@ def test_unverified_tracking_is_orphan_risk_whatever_its_message_says(tmp_path,
 
 
 @pytest.mark.needs_session
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_rebind_write_failure_yields_orphan_risk(tmp_path, monkeypatch):
     # bind persistence fails AND cessation is unproven (SIGTERM-ignoring child):
     # orphan-risk evidence with the pid; recovery only through the confirmed path.
diff --git a/tests/stacks/test_gps.py b/tests/stacks/test_gps.py
index 48fe2ce8..89c87fe3 100644
--- a/tests/stacks/test_gps.py
+++ b/tests/stacks/test_gps.py
@@ -1386,6 +1386,7 @@ def test_run_serves_a_fixed_position_without_any_source(tmp_path):
         t.join(timeout=_FEED_UP_S)
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_device_sending_binary_is_reported_not_waited_on(tmp_path):
     """Found on hardware: gpsd switches u-blox receivers into UBX BINARY mode, and they stay
     there after gpsd stops. The feed read 434 bytes and forwarded none, sitting at "waiting
@@ -2215,6 +2216,7 @@ def _run_gpsd_pump(tmp_path, srv, seconds=1.2):
     return ready
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_gpsd_that_sends_nothing_never_admits_a_start(tmp_path, fake_gpsd):
     """gpsd accepts connections whether or not it owns a receiver — after a restart it commonly
     reports `devices: []` and streams nothing (hit exactly this on hardware). Announcing
@@ -2693,6 +2695,7 @@ def _admission_svc(tmp_path, feed_marker_text):
     return svc, _feed_comp(svc, "meshtastic", "meshtastic-gps")
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_auto_admits_a_stack_whose_gpsd_vanished_or_delivers_nothing(tmp_path, fake_gpsd,
                                                                       monkeypatch):
     """AUDIT-FOUND: freezing the auto verdict was not enough. gpsd disappearing (or owning no
@@ -2733,6 +2736,7 @@ def test_auto_admits_a_stack_whose_gpsd_vanished_or_delivers_nothing(tmp_path, f
     assert svc3._gps_feed_admission(comp3)[0] is False
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_explicit_gpsd_with_an_empty_gpsd_still_refuses_the_start(tmp_path, fake_gpsd):
     """The soft rule is `auto`-only: an operator who NAMED gpsd gets today's fail-closed
     refusal for a gpsd that streams nothing — starting position-blind would hide breakage
diff --git a/tests/stacks/test_gps_monitor.py b/tests/stacks/test_gps_monitor.py
index e2c0717a..ebc0fa4f 100644
--- a/tests/stacks/test_gps_monitor.py
+++ b/tests/stacks/test_gps_monitor.py
@@ -115,23 +115,27 @@ def _two_polls(fake_gpsd, first, second):
     return _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=1.2)
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_window_without_a_satellite_report_keeps_the_last_list(fake_gpsd):
     r = _two_polls(fake_gpsd, [_sky([_sat(3), _sat(7), _sat(12)])], [])
     assert [s["prn"] for s in r["satellites"]] == [3, 7, 12] and r["sats_seen"] == 3
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_dop_only_report_does_not_replace_the_last_list(fake_gpsd):
     r = _two_polls(fake_gpsd, [_sky([_sat(3)])],
                    [(json.dumps({"class": "SKY", "device": "/dev/ttyACM0", "hdop": 1.1}) + "\n").encode()])
     assert [s["prn"] for s in r["satellites"]] == [3]
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_list_older_than_the_stale_bound_is_not_used(fake_gpsd, monkeypatch):
     monkeypatch.setattr(_gps, "NMEA_STALE_S", 0.0)
     r = _two_polls(fake_gpsd, [_sky([_sat(3)])], [])
     assert r["satellites"] == [] and r["sats_seen"] is None
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list(fake_gpsd, monkeypatch):
     # Each poll reads for its whole 1.2 s budget. The list may have arrived at the first read's
     # start, so at the second read's end it can be ~2.4 s old: a 1.5 s bound must refuse it (a stamp
@@ -143,11 +147,13 @@ def test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list(fake_
 
 @pytest.mark.parametrize("second, prns", [([_sky([_sat(5)])], [5]), ([_sky([])], [])],
                          ids=["a new list wins", "an empty list is the truth"])
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_satellite_report_in_the_window_replaces_the_last_list(fake_gpsd, second, prns):
     r = _two_polls(fake_gpsd, [_sky([_sat(3)])], second)
     assert [s["prn"] for s in r["satellites"]] == prns
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_the_last_list_belongs_to_one_gpsd(fake_gpsd):
     _snap(fake_gpsd, DEV_A, _tpv(3), _sky([_sat(3)]))
     r = _snap(fake_gpsd, DEV_A, _tpv(3))                      # another fake gpsd: another port
@@ -191,6 +197,7 @@ def test_untagged_tpv_with_one_device_is_attributed(fake_gpsd):
     assert r["state"] == "3d" and r["device"] == "/dev/ttyACM0" and r["nmea_ok"]
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_untagged_tpv_beside_several_devices_keeps_the_ambiguity(fake_gpsd):
     r = _snap(fake_gpsd, DEV_AB, _tpv(3, device="/dev/ttyACM0"), _tpv(3, device=None))
     assert r["state"] == "3d" and r["device"] == "/dev/ttyACM0"
@@ -208,6 +215,7 @@ def test_non_gps_device_in_the_list_does_not_become_a_receiver(fake_gpsd):
 
 # --- fields, framing, finiteness ---------------------------------------------------------------
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_altitude_datum_order_and_legacy_fallback(fake_gpsd):
     assert _snap(fake_gpsd, DEV_A, _tpv(3, altHAE=50.0))["alt_kind"] == "hae"
     r = _snap(fake_gpsd, DEV_A, _tpv(3, alt=33.0))
diff --git a/tests/stacks/test_gps_nmea_monitor.py b/tests/stacks/test_gps_nmea_monitor.py
index 4d657c3b..e9112020 100644
--- a/tests/stacks/test_gps_nmea_monitor.py
+++ b/tests/stacks/test_gps_nmea_monitor.py
@@ -804,6 +804,7 @@ def test_a_holder_that_never_releases_still_fails_within_the_deadline(tmp_path):
         child.wait(5)
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_only_gps_keys_during_start_get_the_wait(tmp_path):
     svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
     child = _hold_in_child(tmp_path, "claim.loraham.radio.433", "published", 1.0)
diff --git a/tests/web/test_daemon_params_web.py b/tests/web/test_daemon_params_web.py
index 5b382125..c7292d78 100644
--- a/tests/web/test_daemon_params_web.py
+++ b/tests/web/test_daemon_params_web.py
@@ -429,6 +429,7 @@ def test_a_start_applies_the_saved_daemon_params_and_nothing_else(tmp_path):
     assert svc._daemon_param_applies("daemon", "868")["CADIDLE"] == "222"
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_crafted_start_post_fields_are_ignored_not_applied(tmp_path, web):
     # A crafted web POST carrying the OLD per-launch fields (dp_<band>_<PARAM>, p_<name>) changes
     # nothing: the start runs the saved configuration, the saved daemon params stay untouched,
diff --git a/tests/web/test_web.py b/tests/web/test_web.py
index decd4f59..550d41f7 100644
--- a/tests/web/test_web.py
+++ b/tests/web/test_web.py
@@ -691,6 +691,7 @@ def test_install_confirm_shows_missing_system_deps(web, csrf):
 
 
 @pytest.mark.needs_session  # spawns a real process; identity_complete needs sid>0 (skips under sid==0)
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_install_runs_as_live_logged_job(web, csrf):
     c = web()
     token = csrf(c)
diff --git a/tests/web/test_webjob.py b/tests/web/test_webjob.py
index ee27356e..aa2c70a2 100644
--- a/tests/web/test_webjob.py
+++ b/tests/web/test_webjob.py
@@ -115,6 +115,7 @@ def _timeout_env(monkeypatch):
     monkeypatch.setenv("LHPC_BUILD_STEP_TIMEOUT_S", "0.3")
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_unverified_timeout_step_records_unsafe(tmp_path, monkeypatch):
     from lhpc.core import proctree
     _timeout_env(monkeypatch)
@@ -129,6 +130,7 @@ def test_unverified_timeout_step_records_unsafe(tmp_path, monkeypatch):
     assert jobresult._read_raw(svc._paths, _LOG)["state"] == "unsafe"     # cessation UNPROVEN → unsafe
 
 
+@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_proven_terminated_timeout_step_records_failed(tmp_path, monkeypatch):
     from lhpc.core import proctree
     _timeout_env(monkeypatch)
```
