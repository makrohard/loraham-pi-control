# Code report: testrun-p2 v2 (a check over a computed list first asserts the list is not empty)

## Changes v1 → v2 (second read: 2 points + 1 note; auditor round 11: GREEN WITH NOTES, 1 note; all applied)

| finding | change |
|---|---|
| `test_manifest_model.py` guard `any(c.endpoints …)` weaker than the loop's effective set (TCP listeners, not external) | the set is built once with the loop's filter (`listeners`), guarded, and the loop runs over it (14 today) |
| `test_reticulum_stack.py` guard misses the loop's `secret_ref` skip | the stacks without a secret (`plain`) and their config writes (`writes`) are built first; the guard is on `writes`, the loop runs over it |
| note: `test_daemon_params.py` inner `continue` | exact as well: the non-empty defaults (`defaults`) are built first, guarded, looped |
| auditor note: `len(U.ALL_UNITS) == 7` needs the contract shown | kept, with the source line: `docs/architecture.md:59` `updater_units.py     # the seven canonical systemd units (frozen bytes) + verification` |
| "each guard asserts the computed iterable a test loops over" | now "the set the loop really checks", true after the three changes |

Branch `cons/testrun-p2-r1` on `origin/dev` = `d77e34f4`. Plan: PLAN-testrun v3 §3 P2 (gate 1 GREEN WITH NOTES).
Tests only; no production file. Slow-marker decisions for these files are not here: they moved to P6 on the
handler's line (plan v3 said per packet).

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| c28185b6 | 13 test files (below) | core, host, install, repo and stacks layers: 14 guards (each asserts that the set its loop really checks is non-empty, or `len(U.ALL_UNITS) == 7`); test_identity.py's card read via htmlq | none: a test-suite change, no behaviour. Negative run instead (below) |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        0 new tests; layers tests/core=core tests/host=host tests/install=install tests/repo=repo tests/stacks=stacks; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The 22 tests (the scan's computed-loop list on d77e34f4, minus host/test_firewall.py's three, which are P4's)

| test | iterable | decision |
|---|---|---|
| core/test_clean_purge_wipe.py::test_no_other_stack_uses_a_state_root_or_secret_a_purge_removes | `svc.stacks()`, the declared roots/secrets | guard: `svc.stacks() and any(s or r for s, r in decl.values())` |
| core/test_rflog_decode.py::test_the_decoder_scripts_ship_and_take_no_key_on_argv | `rflog.REGISTRY` entries with a decoder | guard: `any(e.decoder for e in rflog.REGISTRY)` |
| host/test_updater_units.py::test_verify_pct_h_same_root_is_modified_ours | `U.ALL_UNITS` | guard: `len(U.ALL_UNITS) == 7`; the contract: `docs/architecture.md:59` `updater_units.py     # the seven canonical systemd units (frozen bytes) + verification` |
| install/test_binary_channel.py::test_default_channel_is_never_the_branch_tip | `svc.stacks()`, `svc.auto_install_rows()` | guard: both non-empty |
| install/test_deps.py::test_bootstrap_script_runs_no_sudo | the script's lines | guard: `lines` |
| install/test_slow_target_budget.py::test_every_calibration_entry_is_well_formed | `BASELINE["calibration"]` (4 entries today) | guard: `BASELINE.get("calibration")` |
| repo/test_packaging.py::test_every_shipped_asset_tree_is_in_the_package_data_allow_list | the `-dist` trees; the `*constraints.txt` files | two guards, one per loop |
| repo/test_packaging.py::test_every_file_in_a_shipped_asset_tree_is_tracked_by_git | the `-dist` trees | guard |
| repo/test_workflow_shell.py::test_every_workflow_shell_block_parses (3 cases) | the workflow's `run:` blocks (ci 8, pages 3, testlab 10 today) | guard: `blocks` |
| repo/test_workflow_shell.py::test_the_helper_does_not_wrap_a_verdict | `WORKFLOWS` | guard |
| stacks/test_daemon_control.py::test_no_verify_entry_uses_the_scanning_channel_read | `dc._VERIFY` | guard |
| stacks/test_daemon_params.py::test_all_default_values_are_daemon_valid | the non-empty defaults of `dp.CLIENT_STACKS` × both bands × the radio and LBT params | built as `defaults` (the loop's `v == ""` skip as a filter), guarded, looped |
| stacks/test_manifest_model.py::test_every_tcp_listener_endpoint_carries_its_port_claim | the manifest's TCP listener endpoints that are not external (14 today) | built as `listeners` (the loop's skip as a filter), guarded, looped |
| stacks/test_reticulum_stack.py::test_a_stack_without_secrets_is_unaffected | the config writes of the stacks other than reticulum that declare no `secret_ref` | built as `plain` → `writes` (both skips as filters), guarded on `writes`, looped |
| core/test_controller.py::test_every_generic_verb_refuses_controller | `_GENERIC_VERBS`, a module literal (8 verbs) | none: a literal |
| core/test_identity.py::test_licensed_with_neither_value_is_refused | `LICENSED`, a module literal (4 stacks) | none: a literal |
| core/test_post_start.py::test_schedule_rejects_malformed_and_ambiguous | `bads`, a literal tuple in the test | none |
| core/test_probes.py::test_an_unrelated_socat_is_not_our_kiss_bridge | `foreign`, a literal list in the test | none |
| core/test_progress.py::test_tiny_steady_progress_never_stalls | `while clock.t < 10 * 600` on a fake clock from 0 | none: the loop always runs |
| host/test_network_controls.py::test_shared_ap_way_home_actions_are_required_and_granted | `share` (literal), then `svc._NET_ACTIONS` | none: the first loop asserts both share actions are in `_NET_ACTIONS`, so the second is never empty |
| stacks/test_meshcore.py::test_lhpc_never_emits_the_key | `blobs`, built in the test (two unconditional `append`s: the status summary and the config view) | none |
| web/test_hmac.py::test_secret_value_never_appears_in_action_results | `results`, a two-element list in the test | none |

The scan's class counts module-level names as computed; the 8 "none" rows are literals by reading (manual).
Rule 5 in the same packet: `core/test_identity.py` (`test_global_card_renders_and_never_marked_required`) read the
card with `html.split(...)`; it now uses `htmlq.parse`, `by_id("operator-callsign")` and `within(...).find("tr",
class_="field-bad")` / `find("span", class_="req")`, the tags the settings template puts those classes on
(`_stack_settings.html:211-212`).

## Negative run (each guard can fail)

A temporary copy of each file with only the guard's iterable replaced by an empty one, the one test run, the copy
deleted (`git status --short` after: clean):

| test | result | first assertion line |
|---|---|---|
| tests/core/test_clean_purge_wipe.py::test_no_other_stack_uses_a_state_root_or_secret_a_purge_removes | 1 failed in 0.08s | `E       assert ([])` |
| tests/core/test_rflog_decode.py::test_the_decoder_scripts_ship_and_take_no_key_on_argv | 1 failed in 0.05s | `E       assert False` |
| tests/host/test_updater_units.py::test_verify_pct_h_same_root_is_modified_ours | 1 failed in 0.08s | `E       assert 0 == 7` |
| tests/install/test_binary_channel.py::test_default_channel_is_never_the_branch_tip | 1 failed in 0.22s | `E       AssertionError: assert ((Stack(id='daemon', name='LoRaHAM daem` |
| tests/install/test_deps.py::test_bootstrap_script_runs_no_sudo | 1 failed in 0.11s | `E       assert []` |
| tests/install/test_slow_target_budget.py::test_every_calibration_entry_is_well_formed | 1 failed in 0.08s | `E       AssertionError: assert None` |
| tests/repo/test_packaging.py::test_every_shipped_asset_tree_is_in_the_package_data_allow_list | 1 failed in 0.03s | `E       AssertionError: assert False` |
| tests/repo/test_packaging.py::test_every_file_in_a_shipped_asset_tree_is_tracked_by_git | 1 failed in 0.03s | `E       assert False` |
| tests/repo/test_workflow_shell.py::test_every_workflow_shell_block_parses | 3 failed in 0.04s | `E       assert []` |
| tests/repo/test_workflow_shell.py::test_the_helper_does_not_wrap_a_verdict | 1 failed in 0.03s | `E       assert ()` |
| tests/stacks/test_daemon_control.py::test_no_verify_entry_uses_the_scanning_channel_read | 1 failed in 0.06s | `E       assert {}` |
| tests/stacks/test_daemon_params.py::test_all_default_values_are_daemon_valid | 1 failed in 0.03s | `E       assert []` (v2: the new set emptied) |
| tests/stacks/test_manifest_model.py::test_every_tcp_listener_endpoint_carries_its_port_claim | 1 failed in 0.06s | `E       assert []` (v2: the new set emptied) |
| tests/stacks/test_reticulum_stack.py::test_a_stack_without_secrets_is_unaffected | 1 failed in 0.13s | `E       assert []` (v2: the new set emptied) |

`test_packaging.py`'s first test has two guards; the run empties the constraints one (the `-dist` guard is the
same expression as the second test's, emptied there).

## The 6-point block

1. **Contracts:** none; every existing assertion is unchanged.
2. **Invariants:** each of the 14 tests now fails when its iterable is empty (the negative run).
3. **Failure classes:** a manifest, registry or workflow change that empties an iterable now fails the test instead
   of passing it vacuously; `len(U.ALL_UNITS) == 7` also fails when a unit is added, which is the contract change
   docs/architecture.md would need anyway.
4. **Test rules:** rule 10 (shown); rule 5 for one HTML read.
5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/host
   tests/install tests/repo tests/stacks` → `6427 passed, 1 skipped in 110.37s`; `ruff check lhpc testlab` and
   `ruff check tests --select F,E9` → `All checks passed!`.
6. **Adversarial self-review:**
   - (a) The plan said "one guard per test" over 22; reading showed 8 literal iterables, where a guard would assert
     a constant; those are left and listed with the reason.
   - (b) The scan class falls from 25 to 11 (3 firewall + the 8 literals): it cannot tell a literal from a call.
   - (c) The identity card's checks still name two classes, through htmlq's class-token match, the suite's tool for it.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | d77e34f4 |
| diff | `git diff --stat origin/dev c28185b6` | 13 files changed, 44 insertions(+), 33 deletions(-) |
| computed-loop class before/after | `python3 -I testrun-scan-v3.py` | 25 / 11 |
| `.split(` on a page before/after | same | 3 / 2 (the shell-script one and `stacks/test_hardware.py:311`, P3's) |
| negative run | the table above | 14 of 14 failed at the guard |
| directories | the command in 5 | 6427 passed, 1 skipped |
