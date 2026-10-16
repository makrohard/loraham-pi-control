# Code report: testrun-p6 v5 (tests whose time is a real wait are marked slow)

## Changes v4 → v5 (auditor round 2: RED, 1 finding; accepted; no code change)

| finding | change |
|---|---|
| the grep did not rule out workflow-provided pytest options (`PYTEST_ADDOPTS`, another `-m` expression) | the evidence covers every way a marker expression can reach pytest from CI (env, `-m`/`-k`, config files, the collection hook), shown in full; the claim says only what it shows |

## Changes v3 → v4 (auditor round 1: RED, 1 finding; accepted; no code change)

| finding | change |
|---|---|
| "still run in CI (no -m lane)" without evidence | the evidence block below (the workflow grep, CI's suite command, the pyproject lane line); the sentence now says only what that shows |

## Changes v2 → v3 (second read: CLEAN, 2 notes; both applied)

| note | change |
|---|---|
| the marker's definition (pyproject.toml `slow`: "a real venv, or a timed retry loop"; tests/README.md "timed loops") is narrower than the rule applied | both now name the timed wait: "the test spends less than half its wall time on CPU" (pyproject.toml) / "a test that spends less than half its wall time on CPU" (tests/README.md) |
| the shares were measured under `-n 4` | said in the method below: contention lengthens wall time, so a share near 0.5 could move at `-n 1`; the marked cases sit at ≤ 0.43; a flip only moves a test between the fast lane and the full suite |

Branch `cons/testrun-p6-r1` on `origin/dev` = `a3733021`. Plan: PLAN-testrun v3 §3 P6 (gate 1 GREEN WITH NOTES), with
the handler's line: every slow-marker decision is in this packet (plan v3 said per packet; moved to P6 on the
handler's line), one rule, one list. Tests only.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| c04de901 | 21 test files, pyproject.toml, tests/README.md | the marker: 56 `@pytest.mark.slow` decorators + one `pytest.param(..., marks=pytest.mark.slow)`; its definition in pyproject.toml and tests/README.md | none: no behaviour |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 27 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        0 new tests; layers tests/cli=cli tests/core=core tests/golden=golden tests/host=host tests/install=install tests/stacks=stacks tests/web=web; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The rule and how it was measured

- tests/README.md, Markers: "`slow` marks the real-venv builds and timed loops." The handler's line: a marker only
  where the test does real venv work or a timed loop, said per test; slow alone gets none.
- **The population:** the 161 test calls of 2 s or more in CI run 38825597727 (Python 3.11, f874040d + `--durations=0
  --durations-min=2`; `M0-durations-min2-3.11.txt`).
- **Real venv work:** the test body creates a venv or runs pip / `install.sh` (read). None of the 161 does without
  the marker already: the three a word search flagged do not (two run `uninstall.sh`, CPU-bound; one only lists venv
  argv in a fake build plan and waits on a real `sleep` child).
- **Timed wait** (the measured reading of "timed loop"): the test spends less than half its wall time on CPU, counting
  the test process and its reaped children (`resource.getrusage(RUSAGE_SELF)` + `RUSAGE_CHILDREN`, around
  `pytest_runtest_call`, a measuring plugin outside the repository, `-n 4`; contention under `-n 4` lengthens wall
  time, so a share near 0.5 could move at `-n 1`; a flip only moves a test between the fast lane and the full suite). Its time is then a wait it cannot shorten:
  a retry schedule, a bounded read, a stop window, a lock timeout. First measured on 941bcc5f, then the marked ones
  again on 771d1a37 (after the efficiency landings): one (`test_success`) rose to 53 % CPU and keeps no marker.
- **Not counted:** CPU of a child that another process reaps (none of the marked tests' time comes from such a child:
  their CPU share is ≤ 0.43 on both runs).

## Counts

| | cases |
|---|---|
| already marked slow | 9 |
| marked slow here (timed wait) | 58 (57 test functions; `test_gps_monitor.py::test_a_satellite_report_in_the_window_replaces_the_last_list` has 2 cases) |
| no marker (CPU work, or re-measured not wait-dominated) | 94 |
| total | 161 |

`pytest --collect-only -m slow`: 18 on 771d1a37 before, 76 after (18 + 58).

## Per test (CI s; local wall / CPU s on 941bcc5f; local wall / CPU s on 771d1a37 for the marked; decision)

| test | CI | 941bcc5f | 771d1a37 | decision |
|---|---|---|---|---|
| `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` | 2.19 | 2.03 / 0.03 | 2.06 / 0.06 | slow: timed wait |
| `tests/core/test_bounded_runner.py::test_run_returns_while_an_escaped_descendant_holds_stdout` | 4.01 | 4.01 / 0.00 | 4.01 / 0.00 | slow: timed wait |
| `tests/core/test_bounded_runner.py::test_streaming_run_returns_and_flags_unverified_output` | 3.01 | 3.01 / 0.23 | 3.01 / 0.00 | slow: timed wait |
| `tests/core/test_bounded_runner.py::test_timeout_kills_term_ignoring_child_that_outlives_parent` | 2.66 | 2.88 / 0.96 | 2.83 / 0.85 | slow: timed wait |
| `tests/core/test_build_launcher_runtime.py::test_web_build_outlives_spec_step_timeout` | 3.03 | 3.08 / 3.33 | — | already marked |
| `tests/core/test_build_plan.py::test_the_console_builds_a_component_as_the_cli_does` | 3.33 | 3.17 / 0.16 | 3.07 / 0.07 | slow: timed wait |
| `tests/core/test_build_timeout.py::test_busy_build_outlives_its_manifest_value` | 3.34 | 3.14 / 3.53 | — | already marked |
| `tests/core/test_group_requirement.py::test_stacks_page_shows_copyable_usermod_only_when_missing` | 3.84 | 1.13 / 1.12 | — | none: work (CPU) |
| `tests/core/test_identity.py::test_a_stale_confirmed_band_is_refused_never_remapped` | 2.26 | 0.93 / 0.92 | — | none: work (CPU) |
| `tests/core/test_identity.py::test_global_change_is_atomic_and_lock_serialized` | 15.23 | 15.04 / 0.03 | 15.04 / 0.03 | slow: timed wait |
| `tests/core/test_identity.py::test_save_only_rechecks_the_identity_inside_its_write` | 4.58 | 3.31 / 0.30 | 3.37 / 0.37 | slow: timed wait |
| `tests/core/test_identity.py::test_web_bandless_restart_is_one_band_end_to_end` | 4.52 | 3.21 / 0.21 | 3.25 / 0.24 | slow: timed wait |
| `tests/core/test_identity.py::test_web_refusal_names_every_missing_field_on_the_settings_row` | 2.22 | 0.77 / 0.77 | — | none: work (CPU) |
| `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` | 5.42 | 5.12 / 0.12 | 5.12 / 0.11 | slow: timed wait |
| `tests/core/test_post_start.py::test_arm_write_exception_is_not_scheduled` | 4.28 | 4.26 / 0.00 | 4.26 / 0.01 | slow: timed wait |
| `tests/core/test_post_start.py::test_bound_runner_sends_when_main_matches` | 3.08 | 3.07 / 0.05 | 3.07 / 0.43 | slow: timed wait |
| `tests/core/test_post_start.py::test_bound_runner_skips_send_when_main_gone` | 2.07 | 2.03 / 0.03 | 2.06 / 0.04 | slow: timed wait |
| `tests/core/test_post_start.py::test_bound_runner_skips_send_when_main_starttime_changed` | 2.07 | 2.03 / 0.03 | 2.06 / 0.05 | slow: timed wait |
| `tests/core/test_post_start.py::test_bound_runner_skips_send_when_main_zombie` | 2.09 | 2.09 / 0.04 | 2.08 / 0.05 | slow: timed wait |
| `tests/core/test_post_start.py::test_bound_runner_skips_send_when_session_or_group_changed` | 4.1 | 4.13 / 0.09 | 4.13 / 0.08 | slow: timed wait |
| `tests/core/test_post_start.py::test_first_success_then_failed_repeats_exits_zero` | 2.08 | 2.07 / 0.05 | 2.07 / 0.04 | slow: timed wait |
| `tests/core/test_post_start.py::test_no_attempt_starts_after_the_schedule_window` | 3.54 | 3.53 / 0.04 | — | already marked |
| `tests/core/test_post_start.py::test_record_write_failure_delivers_no_payload` | 4.11 | 4.11 / 0.00 | 4.11 / 0.00 | slow: timed wait |
| `tests/core/test_post_start.py::test_retry_later_listener_accepts_exits_zero` | 7.1 | 7.08 / 0.06 | 7.08 / 0.06 | slow: timed wait |
| `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified` | 3.69 | 3.31 / 0.06 | 3.31 / 0.06 | slow: timed wait |
| `tests/core/test_post_start.py::test_tcp_send_spaces_successful_deliveries_by_interval` | 2.08 | 2.07 / 0.06 | 2.07 / 0.06 | slow: timed wait |
| `tests/core/test_post_start.py::test_tcp_wait_records_ready_and_timeout` | 2.14 | 2.13 / 0.11 | 2.13 / 0.10 | slow: timed wait |
| `tests/core/test_post_start.py::test_the_start_waits_for_real_config_stability_after_post_start` | 18.42 | 18.07 / 0.07 | 18.07 / 0.07 | slow: timed wait |
| `tests/core/test_process_ownership.py::test_an_identity_that_never_completes_is_returned_weak_within_the_bound` | 2.03 | 2.01 / 0.01 | 2.02 / 0.01 | slow: timed wait |
| `tests/core/test_process_ownership.py::test_empty_cmdline_argv_rejected_despite_nonempty_digest` | 2.02 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
| `tests/core/test_process_ownership.py::test_incomplete_identity_blocks_ownership` | 2.02 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
| `tests/core/test_process_ownership.py::test_record_launch_rejects_empty_exec` | 2.02 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
| `tests/core/test_process_ownership.py::test_terminate_unobserved_returns_false_when_still_alive` | 2.51 | 2.51 / 0.01 | 2.51 / 0.00 | slow: timed wait |
| `tests/core/test_secrets_backup.py::test_backup_is_refused_while_a_lock_is_held[config]` | 2.06 | 2.01 / 0.01 | 2.01 / 0.01 | slow: timed wait |
| `tests/core/test_secrets_backup.py::test_the_session_key_write_takes_the_config_lock_and_is_ephemeral_while_it_is_busy` | 2.01 | 2.00 / 0.00 | 2.00 / 0.00 | slow: timed wait |
| `tests/core/test_snapshot_memo.py::test_a_render_reads_firewall_status_and_listeners_once` | 2.36 | 1.07 / 1.05 | — | none: work (CPU) |
| `tests/core/test_snapshot_memo.py::test_each_request_reassesses_fresh` | 2.24 | 0.37 / 0.37 | — | none: work (CPU) |
| `tests/core/test_snapshot_memo.py::test_every_traced_write_is_classified` | 26.95 | 4.88 / 4.56 | — | none: work (CPU) |
| `tests/golden/test_golden_boot_restore.py::test_failed_after_the_hook_consumes_the_evidence` | 4.13 | 3.89 / 0.51 | 3.86 / 0.36 | slow: timed wait |
| `tests/golden/test_golden_restart.py::test_restart_a_running_stack` | 2.65 | 2.65 / 0.75 | 2.28 / 0.42 | slow: timed wait |
| `tests/golden/test_golden_start.py::test_unverified_termination_is_cleaned_up` | 4.13 | 3.79 / 0.24 | 3.87 / 0.31 | slow: timed wait |
| `tests/golden/test_same_decision_across_entry_paths.py::test_admission_refusal` | 2.73 | 1.31 / 1.27 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_band_owner` | 2.76 | 0.72 / 0.72 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_damaged_client_index` | 5.2 | 2.15 / 0.93 | 2.18 / 0.95 | slow: timed wait |
| `tests/golden/test_same_decision_across_entry_paths.py::test_firewall_gate` | 2.85 | 0.57 / 0.57 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_interactive_launch` | 3.64 | 1.00 / 0.97 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_interrupted_install` | 3.49 | 2.33 / 2.28 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_missing_identity` | 2.96 | 0.56 / 0.56 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_pending_config_journal` | 5.11 | 2.55 / 1.49 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_restart_of_an_interactive_stack` | 6.61 | 5.31 / 2.22 | 4.33 / 1.60 | slow: timed wait |
| `tests/golden/test_same_decision_across_entry_paths.py::test_success` | 5.24 | 2.20 / 0.94 | 2.21 / 1.17 | none: re-measured on 771d1a37 at CPU 53 % of wall (1.17 / 2.21 s), not wait-dominated |
| `tests/golden/test_same_decision_across_entry_paths.py::test_tx_without_high_power_permission` | 4.08 | 1.30 / 1.27 | — | none: work (CPU) |
| `tests/golden/test_same_decision_across_entry_paths.py::test_unverified_termination` | 18.05 | 17.34 / 3.00 | 16.25 / 2.06 | slow: timed wait |
| `tests/host/test_deploy_scripts.py::test_install_allows_uninstall_remainder` | 16.45 | 12.95 / 11.34 | — | already marked |
| `tests/host/test_deploy_scripts.py::test_install_boot_enable_failure_is_unmistakable` | 12.58 | 8.45 / 6.83 | — | already marked |
| `tests/host/test_deploy_scripts.py::test_install_enables_boot_restore_without_starting_it` | 16.29 | 8.85 / 7.25 | — | already marked |
| `tests/host/test_deploy_scripts.py::test_install_full_creates_usable_layout_and_identity_ok` | 18.42 | 9.02 / 7.47 | — | already marked |
| `tests/host/test_deploy_scripts.py::test_install_generates_canonical_updater_units` | 16.23 | 10.32 / 8.78 | — | already marked |
| `tests/host/test_deploy_scripts.py::test_template_and_generated_unit_have_equivalent_security_semantics` | 17.4 | 7.79 / 6.27 | — | already marked |
| `tests/host/test_deploy_scripts.py::test_uninstall_retry_after_reload_failure_succeeds` | 2.24 | 1.94 / 1.93 | — | none: work (CPU; runs uninstall.sh, no venv) |
| `tests/host/test_deploy_scripts.py::test_uninstall_retry_recovers_a_newer_releases_staged_units` | 2.49 | 1.98 / 1.98 | — | none: work (CPU; runs uninstall.sh, no venv) |
| `tests/host/test_deploy_scripts.py::test_uninstall_retry_recovers_leftover_staged_units_then_completes` | 2.56 | 1.96 / 1.96 | — | none: work (CPU; runs uninstall.sh, no venv) |
| `tests/host/test_firewall.py::test_check_verdicts_and_busy_leaves_receipt_untouched` | 10.03 | 10.01 / 0.01 | 10.01 / 0.01 | slow: timed wait |
| `tests/host/test_network_controls.py::test_web_ap_button_and_confirm` | 2.3 | 0.59 / 0.58 | — | none: work (CPU) |
| `tests/host/test_port_exposure.py::test_port_pin_is_never_an_inert_tcp_anchor` | 2.31 | 0.93 / 0.92 | — | none: work (CPU) |
| `tests/host/test_sdnotify.py::test_embedded_nul_is_rejected_never_truncated` | 2.0 | 2.00 / 0.00 | 2.00 / 0.00 | slow: timed wait |
| `tests/install/test_auto_install.py::test_boundary_blocks_concurrent_update_and_start` | 10.59 | 10.15 / 0.14 | 10.13 / 0.12 | slow: timed wait |
| `tests/install/test_auto_install.py::test_child_death_before_claim_is_ackable_while_spawner_lives` | 2.7 | 2.58 / 0.07 | 2.54 / 0.04 | slow: timed wait |
| `tests/install/test_auto_install.py::test_rebind_write_failure_yields_orphan_risk` | 2.68 | 2.64 / 0.11 | 2.60 / 0.06 | slow: timed wait |
| `tests/install/test_channel_switch_route.py::test_a_failed_switch_to_source_restores_the_binary_install` | 2.11 | 0.45 / 0.45 | — | none: work (CPU) |
| `tests/install/test_channel_switch_route.py::test_binary_then_back_to_source` | 2.4 | 0.32 / 0.32 | — | none: work (CPU) |
| `tests/repo/test_no_pin_literals_in_tests.py::test_no_test_hardcodes_a_manifest_pin` | 10.05 | 4.07 / 4.05 | — | none: work (CPU) |
| `tests/repo/test_refusal_remedy.py::test_no_admission_refusal_is_built_by_hand` | 8.91 | 3.37 / 3.32 | — | none: work (CPU) |
| `tests/repo/test_released_manifest_capture.py::test_released_manifests_still_parse` | 6.6 | 1.50 / 1.44 | — | none: work (CPU) |
| `tests/stacks/test_gps.py::test_a_device_sending_binary_is_reported_not_waited_on` | 8.21 | 8.21 / 0.01 | 8.20 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps.py::test_a_gpsd_that_sends_nothing_never_admits_a_start` | 5.16 | 5.02 / 0.02 | 5.02 / 0.03 | slow: timed wait |
| `tests/stacks/test_gps.py::test_auto_admits_a_stack_whose_gpsd_vanished_or_delivers_nothing` | 6.81 | 6.25 / 0.06 | 6.30 / 0.11 | slow: timed wait |
| `tests/stacks/test_gps.py::test_explicit_gpsd_with_an_empty_gpsd_still_refuses_the_start` | 5.15 | 5.05 / 0.05 | 5.04 / 0.04 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_a_dop_only_report_does_not_replace_the_last_list` | 2.4 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_a_list_older_than_the_stale_bound_is_not_used` | 2.4 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_a_satellite_report_in_the_window_replaces_the_last_list[a new list wins]` | — | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_a_satellite_report_in_the_window_replaces_the_last_list[an empty list is the truth]` | — | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_a_window_without_a_satellite_report_keeps_the_last_list` | 2.4 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_altitude_datum_order_and_legacy_fallback` | 3.61 | 3.61 / 0.00 | 3.61 / 0.01 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list` | 2.41 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_the_last_list_belongs_to_one_gpsd` | 2.41 | 2.40 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_monitor.py::test_untagged_tpv_beside_several_devices_keeps_the_ambiguity` | 2.4 | 2.41 / 0.00 | 2.40 / 0.00 | slow: timed wait |
| `tests/stacks/test_gps_nmea_monitor.py::test_only_gps_keys_during_start_get_the_wait` | 2.15 | 2.12 / 0.12 | 2.13 / 0.11 | slow: timed wait |
| `tests/stacks/test_hardware.py::test_probe_result_renders_inline_and_is_consumed_once` | 2.52 | 0.48 / 0.48 | — | none: work (CPU) |
| `tests/web/test_binary_stack_pages.py::test_a_binary_stack_is_not_offered_as_not_installed` | 4.1 | 0.68 / 0.68 | — | none: work (CPU) |
| `tests/web/test_certificate_expiry_page.py::test_the_panel_says_a_damaged_client_list_needs_repair` | 2.8 | 0.51 / 0.51 | — | none: work (CPU) |
| `tests/web/test_certificate_replacement.py::test_console_a_change_between_the_applies_shows_the_replacement_again[lan]` | 2.02 | 0.36 / 0.36 | — | none: work (CPU) |
| `tests/web/test_certificate_replacement.py::test_console_a_change_between_the_applies_shows_the_replacement_again[san]` | 2.01 | 0.38 / 0.38 | — | none: work (CPU) |
| `tests/web/test_certificate_replacement.py::test_console_an_explicit_san_removal_is_not_unioned_back` | 2.03 | 0.34 / 0.33 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_apply_live_disabled_unless_running_or_daemon` | 2.67 | 1.17 / 1.15 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_apply_live_failure_flashes_warning` | 2.37 | 0.45 / 0.44 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_apply_live_saves_then_reports` | 2.53 | 0.39 / 0.38 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_crafted_start_post_fields_are_ignored_not_applied` | 5.33 | 3.40 / 0.39 | 3.34 / 0.33 | slow: timed wait |
| `tests/web/test_daemon_params_web.py::test_daemon_panel_follows_upper_band_switch` | 2.51 | 0.51 / 0.51 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_daemon_params_band_chooser_rendered` | 2.54 | 0.45 / 0.44 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_dp1_no_longer_opens_every_daemon_panel` | 2.05 | 0.25 / 0.25 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_dp_target_specific_opens_only_matching_panel` | 2.03 | 0.28 / 0.28 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_meshtastic_has_no_daemon_panel` | 2.22 | 0.64 / 0.64 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_panel_stays_open_after_save` | 3.04 | 0.63 / 0.63 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_save_rejects_out_of_range` | 2.45 | 0.39 / 0.39 | — | none: work (CPU) |
| `tests/web/test_daemon_params_web.py::test_save_then_reset_daemon_params` | 2.95 | 0.48 / 0.47 | — | none: work (CPU) |
| `tests/web/test_firewall_configure_route.py::test_a_refused_submission_leaves_the_applied_ruleset_as_it_was` | 2.2 | 0.57 / 0.57 | — | none: work (CPU) |
| `tests/web/test_firewall_configure_route.py::test_the_recommended_preset_drops_every_route` | 2.09 | 0.54 / 0.54 | — | none: work (CPU) |
| `tests/web/test_hardware_setup_route.py::test_a_refused_choice_changes_nothing[unknown-setup]` | 2.26 | 1.05 / 0.80 | — | none: work (CPU) |
| `tests/web/test_high_power_web.py::test_banner_keys_on_the_running_sx127x_permission` | 2.34 | 0.68 / 0.67 | — | none: work (CPU) |
| `tests/web/test_high_power_web.py::test_control_saves_through_the_form_and_refuses_junk` | 3.6 | 0.63 / 0.63 | — | none: work (CPU) |
| `tests/web/test_high_power_web.py::test_no_sx127x_banner_on_an_sx1262` | 2.28 | 0.98 / 0.97 | — | none: work (CPU) |
| `tests/web/test_hmac.py::test_the_meshcom_password_section_follows_the_hmac_state` | 2.07 | 0.32 / 0.32 | — | none: work (CPU) |
| `tests/web/test_hmac_runner.py::test_controlled_runner_large_output_does_not_deadlock` | 4.22 | 0.41 / 0.57 | — | none: work (CPU) |
| `tests/web/test_lazy_stacks.py::test_partial_body_route_renders_every_stack` | 2.4 | 0.96 / 0.95 | — | none: work (CPU) |
| `tests/web/test_maintenance_health.py::test_a_failing_task_is_shown_with_its_time_until_it_succeeds[clock-normalise]` | 2.0 | 0.27 / 0.27 | — | none: work (CPU) |
| `tests/web/test_pin_override_web.py::test_confirm_meshcom_pin_refusal_hint_and_selfupdate_no_checkbox[install]` | 2.29 | 1.03 / 1.01 | — | none: work (CPU) |
| `tests/web/test_pin_override_web.py::test_confirm_meshcom_pin_refusal_hint_and_selfupdate_no_checkbox[update]` | 2.31 | 1.08 / 1.02 | — | none: work (CPU) |
| `tests/web/test_pin_override_web.py::test_post_with_the_checkbox_and_token_runs_the_plan_with_the_override` | 2.37 | 0.34 / 0.34 | — | none: work (CPU) |
| `tests/web/test_pin_override_web.py::test_post_without_the_checkbox_is_still_refused` | 2.39 | 0.36 / 0.36 | — | none: work (CPU) |
| `tests/web/test_pin_override_web.py::test_the_pill_and_the_stack_page_warning_while_the_override_is_active` | 3.9 | 1.17 / 1.16 | — | none: work (CPU) |
| `tests/web/test_pin_override_web.py::test_the_token_reaches_the_install_jobs_argv` | 2.47 | 0.33 / 0.33 | — | none: work (CPU) |
| `tests/web/test_rflog_web.py::test_logging_sets_every_owner_at_once_and_shows_mixed` | 2.58 | 1.03 / 1.02 | — | none: work (CPU) |
| `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/logs/rns/clear-data2]` | 2.22 | 0.37 / 0.36 | — | none: work (CPU) |
| `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/rflog/all-data1]` | 2.43 | 0.37 / 0.37 | — | none: work (CPU) |
| `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/rflog/clear-all-data3]` | 2.22 | 0.78 / 0.78 | — | none: work (CPU) |
| `tests/web/test_rflog_web.py::test_the_band_follows_from_the_dashboard_through_the_page_and_its_bottom_forms[/stacks/reticulum/rflog-data0]` | 2.27 | 0.95 / 0.94 | — | none: work (CPU) |
| `tests/web/test_rflog_web.py::test_the_stack_switch_saves_one_key_on_the_owner_and_returns_to_the_page` | 2.32 | 0.52 / 0.52 | — | none: work (CPU) |
| `tests/web/test_stackweb.py::test_a_saved_disable_keeps_the_live_proxy_visible_until_apply` | 2.78 | 0.47 / 0.47 | — | none: work (CPU) |
| `tests/web/test_stackweb.py::test_a_saved_proxy_port_move_keeps_advertising_the_port_that_still_answers` | 2.45 | 0.38 / 0.38 | — | none: work (CPU) |
| `tests/web/test_stackweb.py::test_a_stored_password_never_reaches_the_json_apis_or_flashes` | 2.52 | 0.39 / 0.39 | — | none: work (CPU) |
| `tests/web/test_stackweb.py::test_panel_renders_with_the_bypass_warning` | 2.01 | 0.32 / 0.32 | — | none: work (CPU) |
| `tests/web/test_stackweb.py::test_password_section_states_absent_lax_malformed_and_unreadable_without_a_value` | 2.83 | 0.46 / 0.46 | — | none: work (CPU) |
| `tests/web/test_stackweb.py::test_the_bulk_form_preselects_the_policy_the_pages_share` | 2.1 | 0.78 / 0.78 | — | none: work (CPU) |
| `tests/web/test_web.py::test_a_blocked_or_pending_spawn_flashes_and_returns` | 2.26 | 1.03 / 1.00 | — | none: work (CPU) |
| `tests/web/test_web.py::test_a_refused_plan_flashes_where_the_operator_came_from` | 2.11 | 0.41 / 0.41 | — | none: work (CPU) |
| `tests/web/test_web.py::test_an_inline_update_whose_build_job_is_refused_ends_as_the_clis_failed_build` | 2.29 | 1.13 / 1.11 | — | none: work (CPU) |
| `tests/web/test_web.py::test_binary_install_apply_is_not_gated_on_build_dependencies` | 2.61 | 0.77 / 0.76 | — | none: work (CPU) |
| `tests/web/test_web.py::test_confirm_working_button_hidden_when_stopped_or_recorded` | 2.78 | 0.54 / 0.53 | — | none: work (CPU) |
| `tests/web/test_web.py::test_deferred_webserver_apply_is_announced_on_every_page_until_the_firewall_runs` | 2.09 | 0.40 / 0.40 | — | none: work (CPU) |
| `tests/web/test_web.py::test_fetched_binary_stack_offers_uninstall_in_the_console` | 3.31 | 1.92 / 1.91 | — | none: work (CPU) |
| `tests/web/test_web.py::test_fetched_stack_shows_its_version_and_an_update_when_the_pin_moves` | 3.39 | 1.58 / 1.53 | — | none: work (CPU) |
| `tests/web/test_web.py::test_get_routes_make_no_network_calls` | 2.21 | 0.34 / 0.34 | — | none: work (CPU) |
| `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` | 3.03 | 0.53 / 0.53 | — | none: work (CPU) |
| `tests/web/test_web.py::test_install_runs_as_live_logged_job` | 4.42 | 3.22 / 0.21 | 3.16 / 0.16 | slow: timed wait |
| `tests/web/test_web.py::test_multi_band_config_stored_per_band` | 2.88 | 0.48 / 0.47 | — | none: work (CPU) |
| `tests/web/test_web.py::test_no_inline_style_or_script_on_pages` | 2.92 | 0.54 / 0.54 | — | none: work (CPU) |
| `tests/web/test_web.py::test_restart_required_chip_on_stack_page_and_dashboard` | 2.13 | 0.42 / 0.41 | — | none: work (CPU) |
| `tests/web/test_web.py::test_routine_restart_runs_with_no_page_between` | 2.26 | 0.93 / 0.92 | — | none: work (CPU) |
| `tests/web/test_web.py::test_routine_start_runs_with_no_page_between` | 2.15 | 0.69 / 0.68 | — | none: work (CPU) |
| `tests/web/test_web.py::test_server_forced_open_marks_data_force_open` | 2.53 | 0.43 / 0.42 | — | none: work (CPU) |
| `tests/web/test_web.py::test_settings_cfg_query_opens` | 2.0 | 1.10 / 1.09 | — | none: work (CPU) |
| `tests/web/test_web.py::test_the_meshcore_mode_switch_on_the_stack_body_saves_the_same_setting` | 3.23 | 0.81 / 0.79 | — | none: work (CPU) |
| `tests/web/test_web.py::test_the_notice_link_opens_the_firewall_apply_section` | 2.01 | 0.34 / 0.33 | — | none: work (CPU) |
| `tests/web/test_web.py::test_the_removed_settings_panel_stays_removed` | 2.74 | 0.65 / 0.64 | — | none: work (CPU) |
| `tests/web/test_web.py::test_the_settings_card_refusal_does_not_ask_for_two_saves` | 2.46 | 0.43 / 0.42 | — | none: work (CPU) |
| `tests/web/test_web_auto_install.py::test_the_limit_a_used_token_is_remembered_for_256_later_consumptions_whatever_the_time[255]` | 48.33 | 7.80 / 7.75 | — | none: work (CPU) |
| `tests/web/test_web_auto_install.py::test_the_limit_a_used_token_is_remembered_for_256_later_consumptions_whatever_the_time[256]` | 54.18 | 7.59 / 7.54 | — | none: work (CPU) |
| `tests/web/test_webjob.py::test_proven_terminated_timeout_step_records_failed` | 2.52 | 2.33 / 0.03 | 2.34 / 0.04 | slow: timed wait |
| `tests/web/test_webjob.py::test_unverified_timeout_step_records_unsafe` | 2.46 | 2.33 / 0.03 | 2.36 / 0.06 | slow: timed wait |
| `tests/web/test_webserver.py::test_fetch_commands_render_in_every_serving_mode` | 4.85 | 0.87 / 0.86 | — | none: work (CPU) |

## The 6-point block

1. **Contracts:** none. 2. **Invariants:** none. 3. **Failure classes:** in CI a marked test runs as before: no workflow passes `-m` or sets `PYTEST_ADDOPTS`, and
   nothing in the suite's configuration deselects `slow` (evidence below). Only an `-m "not slow"` run leaves it out:
   pyproject.toml:90 names that lane, docs/maintenance.md:330 a Pi run of it.
4. **Test rules:** the Markers section.
5. **Directories:** every directory the commit touches (cli, core, golden, host, install, stacks, web) plus repo, i.e.
   all of `tests/`: `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests` → `7747 passed, 1
   skipped in 97.81s`; both ruff runs → `All checks passed!`.
6. **Adversarial self-review:**
   - (a) The 0.5 CPU share is a threshold chosen here; the marked cases sit at ≤ 0.43, most at ≤ 0.1 (the table).
   - (b) Local wall times are shorter than CI's; the population is CI's (≥ 2 s there); the local run decides only wait
     versus work.
   - (c) Two ids of the CI table carry spaces in their parameter; they were run through their function id (both
     cases).
   - (d) The branch's first code commit (dacb65d5, pushed in 2f96ea94; its report cited an earlier, unpushed sha)
     still carried the marker on `test_success`: the removal stayed unstaged. Found before the auditor's round by
     `git status`; the commit was rebuilt (a4cb9e0b). The `-m slow` count and the full run above were taken on the
     corrected tree.


## Evidence: how a marker expression could reach pytest in CI (measured on a3733021)

```text
$ git grep -nE 'PYTEST_ADDOPTS|(^|[^a-z])-m[ =]|--markers|-k ' -- .github tools pyproject.toml
.github/workflows/ci.yml:62:          retry 3 python -m pip install --upgrade pip
.github/workflows/ci.yml:66:        run: python -m compileall -q lhpc
.github/workflows/ci.yml:85:        # `python -m pytest`, NOT the console script: the package is installed non-editable
.github/workflows/ci.yml:91:        run: python -m pytest -q -rs -n auto --dist loadfile --cov=lhpc --cov-branch --cov-report=term:skip-covered --cov-report=xml:coverage.xml
.github/workflows/ci.yml:152:          retry 3 python -m pip install --upgrade pip
.github/workflows/ci.yml:156:        run: python -m pytest -q -p no:cacheprovider tests/repo/test_fake_signatures.py tests/repo/test_unit_templates_frozen.py
.github/workflows/ci.yml:196:          python -m tests.policy_covers_manifest \
.github/workflows/ci.yml:294:          retry 3 python -m pip install -q --upgrade pip
.github/workflows/ci.yml:295:          retry 3 python -m pip install -q /tmp/openhop-core meshcore pytest pytest-asyncio
.github/workflows/ci.yml:296:          retry 3 python -m pip install -q --no-deps ./lhpc/data/meshcore_host
.github/workflows/ci.yml:322:          retry 3 python -m pip install -q --upgrade pip
.github/workflows/ci.yml:323:          retry 3 python -m pip install -q "$ver" pycryptodomex==3.23.0 pytest
.github/workflows/ci.yml:363:          retry 3 python -m pip install -q --upgrade pip
.github/workflows/ci.yml:364:          retry 3 python -m pip install -q /tmp/rns pytest
.github/workflows/ci.yml:365:          retry 3 python -m pip install -q --no-deps /tmp/lxmf
.github/workflows/ci.yml:367:          retry 3 python -m pip install -q --no-deps /tmp/rnsif
.github/workflows/pages.yml:40:          retry 3 python -m pip install --upgrade build
.github/workflows/pages.yml:76:          ( cd web && python3 -m http.server 8099 >/tmp/httpd.log 2>&1 & )
.github/workflows/testlab.yml:90:                python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
.github/workflows/testlab.yml:98:                backoff python -m playwright install chromium
.github/workflows/testlab.yml:107:                python -m pytest testlab/tests/unit -q
.github/workflows/testlab.yml:112:                  python -m pytest testlab/tests/acceptance -q -rs
.github/workflows/testlab.yml:113:                LHPC_BROWSER=1 python -m pytest testlab/tests/browser -q -rs"'
.github/workflows/testlab.yml:207:          python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
.github/workflows/testlab.yml:224:            python -m pytest testlab/tests/release -q -rs -x \
.github/workflows/testlab.yml:232:              install -m 0755 /repo/lane.sh /tmp/lane.sh
.github/workflows/testlab.yml:348:            ". /repo/.github/scripts/retry.sh && cp -r /repo /tmp/src && backoff python3 -m pip download -q -d /xfer/wheels $cli 'setuptools>=68' /tmp/src"
.github/workflows/testlab.yml:441:          python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
.github/workflows/testlab.yml:455:            python -m pytest testlab/tests/slowbuild -q -rs \
.github/workflows/testlab.yml:468:              install -m 0755 /repo/lane.sh /tmp/lane.sh
pyproject.toml:88:  "contract: Tier 0 — the readable core, one lane that state…
pyproject.toml:89:  "safety(id): a contract case that guards a named SAFETY in…
pyproject.toml:90:  "slow: … Excluded by the fast lane `-m 'not slow'`; always run by the complete suite.",
(exit 0)
$ git grep -nE "PYTEST_" -- .github tools pyproject.toml
(no output; exit 1)
$ sed -n 224,225p .github/workflows/testlab.yml; sed -n 455,456p .github/workflows/testlab.yml   # the two multi-line pytest commands
            python -m pytest testlab/tests/release -q -rs -x \
              --junitxml=/out/junit-release.xml
            python -m pytest testlab/tests/slowbuild -q -rs \
              --junitxml=/out/junit-slow-build.xml
$ ls setup.cfg tox.ini pytest.ini
(none exists)
```

What it shows: no workflow sets `PYTEST_ADDOPTS` or any `PYTEST_` variable; every `-m` in `.github` is
`python -m <module>` or `install -m 0755`; no pytest command in a workflow carries `-m` or `-k` (the two
multi-line ones continue only with `--junitxml`); the `tests/` suite runs in CI at ci.yml:91 and ci.yml:156; there is
no setup.cfg, tox.ini or pytest.ini; `addopts` (pyproject.toml:79) is `["--strict-markers"]`; tests/conftest.py's
collection hook skips on `needs_session`, `needs_nonroot` and `requires_zstd` only. The only `-m 'not slow'` lane is
the one pyproject.toml:90 names (docs/maintenance.md:330: a Pi run).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | 771d1a37 |
| marked | `grep -c 'mark.slow                   # timed wait'` over the changed files, + the one `pytest.param` | 56 + 1 |
| `-m slow` before/after | `pytest --collect-only -q -m slow` | 18 / 76 |
| full tests/ | the command in 5 | 7747 passed, 1 skipped |
