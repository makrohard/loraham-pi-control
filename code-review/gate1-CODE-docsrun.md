# Gate 1: docsrun (P1 docs + P2 CHANGELOG)

**Request.** Judge the two commits below against PLAN-docsrun v2 (included; gate 1 GREEN WITH NOTES, its note: for each code landing record the doc sentences it can affect, or "none" with evidence). Judge: is every changed line true against the evidence cited (the report's D table and S table); is the per-landing table complete for the commits it lists; is D5 (a clause removed from another item's text, not in the plan's D1–D4) within the plan's P1 ("any file the review corrects") and is the removal right; does D4 remove exactly the four tooling bullets and nothing else; does any sentence claim more than the diff; is any CHANGELOG released section touched.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## The plan (v2)

````markdown
# PLAN X2 docs run (v2): a mechanical docs sweep + a manual review of the docs this run touched

**Base:** `origin/dev` = 768b9c6b (moves tonight). **Scope:** `docs/`, `README.md`, `tests/README.md`, the
`## Unreleased` section of `CHANGELOG.md`; no code, no tests; no new explanations.
**Scan:** `python3 -I docsrun-scan.py` (v1 script, unchanged, sha256 10846369…aa4): (a) the architecture-table
`file:line` check; (b) a **history-candidate** grep (a fixed phrase list). (b) finds candidates only: zero hits
proves nothing; history is judged by the manual review in P1/P3. Rule: docs/maintenance.md "Docs and CHANGELOG".

## Changes v1 → v2 (round 2 verdict, RED, F1–F3; all accepted)
| finding | change |
|---|---|
| F1 the history scan is not exhaustive | named a history-candidate scan everywhere; no claim of completeness; the manual review (P1, P3) judges history |
| F2 D4 kept tooling bullets on precedent | the handler's line: all four tooling bullets leave `## Unreleased` |
| F3 "docs made true again" not provable by the scans | the claim is narrowed: a mechanical sweep + a manual per-sentence review of the docs lines this run added or changed, the reviewed sentences listed in the report; no repository-wide truth claim |

## 1. What this run changed in the docs (`git diff --stat eb9673a4 origin/dev -- docs README.md tests/README.md`)
6 files, +44/−16 lines: `docs/gps.md`, `docs/maintenance.md`, `docs/stacks/meshcore.md`, `docs/test-matrix.md`,
`docs/testlab.md`, `tests/README.md` (`git diff … | grep -c '^+[^+]'` = 44 added lines). Code landings since
eb9673a4 that changed no doc: b379911f (Graywolf curl reason; no doc quotes its message: `grep -rn 'releases API' docs` = 0) and a8e0e5ce (checker word list).

## 2. Findings on 768b9c6b
| id | where | what is untrue / out of rule | fix |
|---|---|---|---|
| D1 | `docs/architecture.md:339`, `:362`, `:363` | scan (a): 94 ok, 3 stale — `service_maintenance.py:2389` `uninstall` is at 2397 and `:1773` `update` at 1781 (b379911f, +8 lines); `service_params.py:2813` `enforce_identity` is at 2812 (c699bd16, −1 line) | the three numbers, re-measured on the packet's base |
| D2 | `docs/maintenance.md:96-97` | the dev-tool list (`pytest`, `ruff`, `bandit`, `pytest-cov`, `zstandard`) omits `pytest-xdist`, in `pyproject.toml`'s dev list since add7076c | add `pytest-xdist` |
| D3 | `docs/architecture.md:233` | "(before 0.12.0 it was ignored)": history | delete the parenthesis |
| D4 | `CHANGELOG.md` `## Unreleased` (8 bullets) | 4 tooling bullets (brief rule 3: tooling/tests get no entry): the slow-target release check (Graywolf download), "CI runs the test suite in parallel…", the two release-check bullets (readiness timeout; interactive start) | remove all four (the handler's line); the 4 operator bullets (sky view, 2 × MeshCore repeater dashboard, Graywolf curl reason) stay; no released section touched |

Scan (b) on 768b9c6b: 9 candidates, read: 8 are present-tense conditions ("no longer matches", "no longer holds",
"when no longer targeted") or the ruled recorded-exceptions list (`maintenance.md:154`); 1 is D3.

## 3. Packets
| packet | when | files | content |
|---|---|---|---|
| P1 docs | now, fresh origin/dev | `docs/architecture.md`, `docs/maintenance.md` + any file the review below corrects | D1–D3; plus the MANUAL review: every sentence containing an added/changed line of `git diff eb9673a4 <base> -- docs README.md tests/README.md`, each checked against the code or a command for truth, history, explanation and a second place for the same fact (`check_houserules.py --fact <its key literal>`); the report lists every reviewed sentence (file:line) with OK or the fix |
| P2 changelog | with P1, own commit | `CHANGELOG.md` (Unreleased only) | D4 |
| P3 final sweep | after the release items landed, before 0.12.10's release commit | what it names | both scans re-run + the same manual review for the docs lines added since P1's base; same report form |

## 4. Per packet (brief v2.1)
One commit per packet, `docsrun: <what>`; no CHANGELOG entry for P1/P3. Before the push:
`tools/check_houserules.py origin/dev..HEAD --fact "<each changed literal>"`, `tests/repo` (README drift), both ruff runs. Report + gate-1 packet with the scans
before/after and the reviewed-sentence list. Lands on dev only on the handler's line.

## 5. Not done
No restructuring, no new sections, no released CHANGELOG section, no `docs/live-tests/` record (dated by design),
no review of doc sentences this run did not touch.
````

## Commits (full messages, `git log --reverse --format=%B origin/dev..HEAD`)

### 3de37b99 docsrun: three stale owner line numbers, a missing dev tool, two clauses out

```text
docsrun: three stale owner line numbers, a missing dev tool, two clauses out

- docs/architecture.md invariant table: three owner line numbers moved by
  this cycle's code (service_maintenance.py `update` 1773 -> 1781 and
  `uninstall` 2389 -> 2397, after the Graywolf curl-reason change;
  service_params.py `enforce_identity` 2813 -> 2812, after the repeater
  login-secret change). The table's file:line check reads 97 ok, 0 stale.
- docs/maintenance.md: the unpinned dev tools include pytest-xdist, in
  pyproject.toml's dev list since CI runs the suite in parallel processes.
- docs/architecture.md: the version history in the `.git` symlink sentence
  is removed (the CHANGELOG holds it).
- docs/testlab.md: the reason clause after NomadNet's peer snapshot is
  removed (an explanation, not a checkable fact).

Docs only: no behaviour, no test, no CHANGELOG entry.

```

### 6a80255b docsrun: Unreleased keeps the operator's entries only

```text
docsrun: Unreleased keeps the operator's entries only

The four tooling entries leave the Unreleased section: the slow-target
check's Graywolf download, CI's parallel test processes, and the release
check's readiness timeout and interactive-start entries. Tooling and test
changes get no CHANGELOG entry (docs/maintenance.md, "Docs and CHANGELOG").
The four operator entries stay; no released section is touched.

```

## The report

> # Code report: docsrun (P1 docs + P2 CHANGELOG)
>
> Branch `cons/docsrun-r1` on `origin/dev` = `d77e34f4`. Plan: PLAN-docsrun v2 (gate 1 GREEN WITH NOTES; its note,
> "for each code landing record the doc sentences it can affect, or none with evidence", is applied below).
> Docs and CHANGELOG only: no code, no test.
>
> ## Commits
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 3de37b99 | docs/architecture.md, docs/maintenance.md, docs/testlab.md | none (docs only); the architecture-table `file:line` scan: 94 ok / 3 stale → 97 ok / 0 stale | n/a (no behaviour) |
> | 6a80255b | CHANGELOG.md (`## Unreleased` only) | none | n/a |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 12 lines   OK
> docs         3 files, +8 lines; fact "service_maintenance.py:1781" -> docs/architecture.md; fact "pytest-xdist" -> docs/maintenance.md; fact "PYTHONFAULTHANDLER=1" -> docs/testlab.md   OK
> tests        0 new tests; layers -; red-before 0/0   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> ## What changed
>
> | id | file:line (after) | change | evidence |
> |---|---|---|---|
> | D1 | docs/architecture.md:338, :361, :362 | `service_maintenance.py:2389` `uninstall` → 2397; `:1773` `update` → 1781; `service_params.py:2813` `enforce_identity` → 2812 | `grep -nE '    def (uninstall|update)\(' lhpc/core/service_maintenance.py` → 1781, 2397; `grep -n 'def enforce_identity' lhpc/core/service_params.py` → 2812 |
> | D2 | docs/maintenance.md:96-97 | `pytest-xdist` added to the unpinned dev tools | `grep -n pytest-xdist pyproject.toml` → 44 (dev list) |
> | D3 | docs/architecture.md:233 | "(before 0.12.0 it was ignored)" removed: history | — |
> | D5 | docs/testlab.md:148 | ", because its first start has hung on a loaded runner for a cause not yet measured" removed: a reason with history, not a checkable fact | found by the manual review below |
> | D4 | CHANGELOG.md Unreleased | the four tooling bullets removed (slow-target Graywolf download; CI parallel processes; the release check's readiness timeout; its interactive start); the four operator bullets stay | the handler's line on D4 |
>
> D5 is new against the plan's D1–D4: the plan's P1 covers "any file the review below corrects".
>
> ## Per code landing: the doc sentences it can affect (the gate-1 note)
>
> `git log --reverse eb9673a4..d77e34f4`, commits touching more than `code-review/`:
>
> | commit | what it changed | doc sentences it can affect | state |
> |---|---|---|---|
> | b0c4750a | slow-build times the manifest's Graywolf | docs/test-matrix.md:110 `deb-fetch` row | updated in the commit; reviewed S9 |
> | b379911f | Graywolf check keeps curl's rc + message (+8 lines in service_maintenance.py) | the architecture rows citing service_maintenance.py lines after 886; any doc quoting the old message | D1 (two rows); `grep -rn 'releases API\|network/API' docs` → 0 |
> | c8f9c631 | the budget test, tests/README rule 13 | tests/README.md rule 13 | reviewed S12 |
> | cf24d05a, a8e0e5ce | the checker | docs/maintenance.md "Docs and CHANGELOG" | reviewed S4; a8e0e5ce changed the checker's word list only: docs/architecture.md:225 uses the POSIX term "session leader", unchanged and still true (`grep -rn 'session leader' docs` → that one line) |
> | add7076c | xdist, the cancel rule, slow-build after testlab | docs/maintenance.md:26, :48-50, the dev-tool list | S2, S3 reviewed; D2 |
> | 46ffa57f | the sky-view memo | docs/gps.md:52-57 | reviewed S1 |
> | 2ea5b5b0, aa5410dd | release-check readiness, interactive timeout | docs/testlab.md:139-149 | S10, S11 reviewed; D5 |
> | 642224c4 | repeater presets installed | docs/stacks/meshcore.md:51, :82-84 | S6, S8 reviewed |
> | c699bd16 | repeater JWT secret kept (−1 line in service_params.py before `enforce_identity`) | docs/stacks/meshcore.md:15, :68-70; the architecture row citing `enforce_identity` | S5, S7 reviewed; D1 |
> | 819f6159, 06a6fac2 | tests only (tests/install, tests/stacks) | none: no doc names the changed cases (`grep -rn 'fresh interpreter\|os.fork' docs` → 0) | — |
>
> ## Manual review: every sentence holding a line this run added to the docs
>
> `git diff -U0 eb9673a4 d77e34f4 -- docs README.md tests/README.md` (44 added lines, 6 files). Each sentence was
> checked against the code or a command; for truth, history, explanation, and a second place for its fact.
>
> | id | where (on d77e34f4) | sentence (short) | checked against | verdict |
> |---|---|---|---|---|
> | S1 | docs/gps.md:52-57 | the console keeps each device's last satellite list ≤ 20 s; one `lhpc gps --monitor` run shows only its own read | `lhpc/core/gps.py:556` `NMEA_STALE_S = 20.0`, `:1024` `_SKY_MEMO` (process memory), `:1171` the age check | OK |
> | S2 | docs/maintenance.md:26-27 | CI runs `python -m pytest -q -rs -n auto --dist loadfile --cov=lhpc --cov-branch` | `.github/workflows/ci.yml:91` | OK |
> | S3 | docs/maintenance.md:48-50 | slow-build only after `testlab`; a newer `cons/*` push cancels that branch's superseded runs of both workflows; nothing else is cancelled | `testlab.yml:221` `needs: testlab`; `ci.yml:19`, `testlab.yml:30` `startsWith(github.ref, 'refs/heads/cons/')`; `pages.yml:27` `cancel-in-progress: false` | OK |
> | S4 | docs/maintenance.md:104-112 | the checker prints the block (CHANGELOG, docs, tests, patterns, markers, attribution, module budgets); HARD fails it; it checks form, not truth | `tools/check_houserules.py` rows `changelog`…`modules`, `--correction`, `--fact` (its `--help`) | OK |
> | S5 | docs/stacks/meshcore.md:15 | repeater secrets: `openhop_repeater_identity.key`, `_admin.txt`, `_jwt.txt` | `lhpc/core/meshcore_identity.py:35-40` | OK |
> | S6 | docs/stacks/meshcore.md:51 | `presets_file` among the controller-set config keys | `meshcore_host/config.py:103,250`; manifest `presets_file` param (hidden) | OK |
> | S7 | docs/stacks/meshcore.md:68-70 | key, dashboard password and login-signing secret minted once and reused; a restart signs no one out | `meshcore_identity.py:33-39` ("minted once"), `:253` `_MINTED_LENGTH` | OK |
> | S8 | docs/stacks/meshcore.md:82-84 | presets from the pinned checkout, copied into `state/openhop/` at each start; a picked preset cannot be saved there | `meshcore_host/repeater.py` `install_presets` (called at start; skips an identical file); manifest default `{runtime}/src/openhop-repeater/radio-presets.json`; `proxy_deny_paths` holds `/api/update_radio_config` | OK |
> | S9 | docs/test-matrix.md:110 | `graywolf-fetch.sh /tmp/gw <the manifest's graywolf version> --from-upstream` | the script's usage line `<dest-dir> <version> [--from-upstream]` | OK |
> | S10 | docs/testlab.md:139-142 | a readiness timeout is marked only when LHPC reports the component stopped or failed, else unmarked with the run-log tail | `testlab/lhpc_testlab/release.py:704` `readiness_failed` | OK |
> | S11 | docs/testlab.md:145-149 | a timeout is never retried; SIGABRT + faulthandler; NomadNet records sockets and every Reticulum stack; the lane runs with `PYTHONFAULTHANDLER=1` | `release.py:464`, `:500` `reticulum_peers_snapshot`, `:541`; `testlab/tests/release/conftest.py:93` | OK after D5 |
> | S12 | tests/README.md:110-115 | rule 13: caps lines and public methods of the family; never lowers a budget by itself | `tests/repo/test_module_budgets.py` (fixed `BUDGETS`, `<=` comparison) | OK |
>
> Not reviewed: doc sentences this run did not touch (plan §5).
>
> ## The 6-point block
>
> 1. **Contracts:** none changed (docs and CHANGELOG only).
> 2. **Invariants:** the architecture table's owners resolve at their cited lines (scan (a): 97 ok, 0 stale).
> 3. **Failure classes:** n/a; a line number moves with any later edit above it, so P3 re-runs scan (a) before
>    the 0.12.10 release commit.
> 4. **Test rules:** no test changed.
> 5. **Directories:** `pytest -q -p no:cacheprovider -n 8 tests/repo` → `578 passed, 1 skipped`; `ruff check lhpc
>    testlab` and `ruff check tests --select F,E9` → `All checks passed!`.
> 6. **Adversarial self-review:**
>    - (a) The first subject read "the docs … are true again", a claim the round-2 verdict ruled out; it was reworded
>      to what the commit does before the push.
>    - (b) D3's reflow kept every word of the remaining sentence (`git diff --word-diff` shows only the
>      parenthesis removed).
>    - (c) D5 removes text another item landed tonight; it is a reason clause, and its fact (the snapshot runs) stays.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | d77e34f4 |
> | architecture refs before/after | `python3 -I docsrun-scan.py` (run dir) on d77e34f4 / on 6a80255b | 94 ok, 3 stale / 97 ok, 0 stale |
> | history candidates before/after | same, part (b) | 9 / 8 |
> | added doc lines reviewed | `git diff -U0 eb9673a4 d77e34f4 -- docs README.md tests/README.md \| grep -c '^+[^+]'` | 44 |
> | Unreleased bullets before/after | `awk '/^## Unreleased/{f=1;next}/^## /{f=0}f' CHANGELOG.md \| grep -c '^- '` | 8 / 4 |
> | repo tests | the pytest command above | 578 passed, 1 skipped |

## The full diff (`git diff d77e34f4c775d85ba0822b373ec4243651be5cc9 6a80255b25e276d5da144b9bc4d455671f096212`)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ba902936..91ec4cfc 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -10,16 +10,8 @@ Fixed
   `config/secrets/` and is part of `lhpc secrets backup`.
 - MeshCore repeater dashboard: the radio presets list loads (it reported "Presets file not found"); radio settings
   are still changed in LHPC's Settings.
-- The slow-target release check times the download of the Graywolf release the manifest pins and no longer asks
-  GitHub's API for the newest one, so the API's rate limit can no longer stop a release candidate (the download itself
-  still comes from GitHub).
 - Graywolf's update check now says why GitHub could not be reached: curl's exit code and the end of its message,
   in the result and in the status it keeps.
-- CI runs the test suite in parallel processes, and the slow-build check starts only after the testlab job passed.
-- The release check no longer blames a stack when one of its services was still starting after its wait: a
-  readiness timeout names the stack only when LHPC reports the component stopped or failed.
-- The release check no longer starts an interactive program a second time after a timeout: a hung start fails
-  (unmarked), and NomadNet's records the stacks of every Reticulum process and the shared instance's sockets.
 
 ## 0.12.9
 
diff --git a/docs/architecture.md b/docs/architecture.md
index dbc40f22..94c9881d 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -230,10 +230,9 @@ leaves on disk and which test proves it: the [invariant table](#invariant-table)
   writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
   `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
   and caught at every boundary. A checkout's `.git` that is a symlink which does not resolve
-  (dangling, a loop) is treated as present and the tree is not reported clean (before 0.12.0 it
-  was ignored); it is never handed to git, which would walk up to an enclosing repository. These
-  presence decisions use `runtime_fs.probe_exists`, or `probe_stat` where the kind (directory,
-  symlink) decides: binary retire's check of each receipt file and owned folder, the
+  (dangling, a loop) is treated as present and the tree is not reported clean; it is never
+  handed to git, which would walk up to an enclosing repository. These presence decisions use
+  `runtime_fs.probe_exists`, or `probe_stat` where the kind (directory, symlink) decides: binary retire's check of each receipt file and owned folder, the
   binary->source switch's listing of a tree, a checkout's dirty/carry inventory (`.git`), the
   boot-restore markers and journal, and the config journal recovery. In each, only ENOENT/ENOTDIR
   read as absent, any other error refuses or keeps; the stdlib-only firewall helper applies the
@@ -336,7 +335,7 @@ the runtime root unless absolute.
 | install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py:1267` `stop`, `:1129` `verify_owned`; `lhpc/core/procident.py:96` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py:2523` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
 | install / recovery | path containment ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:74` `_walk_parent`, `:156` `atomic_write` | none | — | `tests/core/test_runtime_fs.py::test_symlinked_parent_refuses_every_runtime_op`, `::test_atomic_write_rejects_symlink_leaf` |
 | install / recovery | a path that cannot be examined is never absent: only ENOENT/ENOTDIR read as absent, any other error refuses or keeps; a `.git` symlink that does not resolve is present ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:740` `probe_exists`, `:756` `probe_stat`; `lhpc/core/firewall_helper.py:1066` `_read_json_state` (the stdlib-only helper's inline rule) | none | the receipt, journal or record is kept while its path cannot be examined | `tests/core/test_existence_probe.py::test_any_other_error_is_unknown_with_the_error`, `::test_an_unexaminable_path_is_never_absent`, `::test_a_git_symlink_that_does_not_resolve_is_never_clean`; `tests/host/test_firewall.py::test_a_journal_that_cannot_be_examined_is_not_absent` |
-| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2389` `uninstall` | admission, config stability (shared), every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
+| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2397` `uninstall` | admission, config stability (shared), every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
 | install / recovery | boot restore replays saved configuration through the gated start, honours stop notes ([operations](operations.md#not-a-supervisor)) | `lhpc/core/service_boot_restore.py:290` `boot_restore_run`, `:131` `_write_stop_intent` | admission and the normal start locks | `state/boot-restore.json`, `state/stop-intent/<stack>.json`; `lhpc/core/service_boot_restore.py:268` `_boot_journal_recovery` consumes an item left *attempting*, never retries it | `tests/core/test_boot_restore.py::test_crash_after_attempting_never_retries_but_pending_survives`, `::test_boot_restore_run_honors_stop_intent_end_to_end`, `::test_driver_admission_refusal_consumes_nothing` |
 | install / recovery | a wheel installed into a fresh venv runs ([Packaging](#safety-model)) | `lhpc/core/assets.py:20` `asset_path` | none | — | `tests/repo/test_packaging.py::test_data_assets_resolve`; gap: no test installs the wheel into a fresh venv |
 | binary channel | a failed install, or one interrupted before its commit, is rolled back to the previous one; one interrupted after its commit stays installed and recovery only completes its cleanup (backups, journal) ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:97` `binary_install`, `:601` `binary_recover`; `lhpc/core/binary_install.py:556` `rollback_files` | admission, the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `::test_an_open_binary_transaction_is_recovered_before_already_installed_is_said`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything`, `::test_committed_journal_leaves_the_receipt_authoritative` |
@@ -359,8 +358,8 @@ the runtime root unless absolute.
 | radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py:49` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
 | radio / band ownership | the SPI bus is shared through the daemon's `spi0.lock` by the daemon and the Reticulum node; meshtasticd takes no part and is admitted beside the daemon only on the opposite band ([Radios](#radios-bands-and-resource-claims)) | the daemon, not LHPC; LHPC models it as the `spi.bus.0` / `spi.bus.0.unlocked` claims | the daemon's flock `state/loraham/spi0.lock` | — | `tests/core/test_resources_conflicts.py::test_cooperative_peers_do_not_conflict`, `::test_meshtastic_conflicts_with_daemon_on_868_radio_not_spi`; gap: the lock itself (daemon side) |
 | radio / band ownership | bounded CONF replies, TX-mode read-back, `/tmp` socket peer check ([Daemon sockets](#safety-model)) | `lhpc/core/daemon_control.py:179` `parse_conf_reply`, `:519` `apply_set`; `lhpc/core/probes/backends.py:767` `_authenticate_tmp_peer` | none | — | `tests/stacks/test_daemon_bounds.py::test_oversized_response_rejected`, `::test_too_many_tokens_rejected`; `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent`; `tests/core/test_post_start.py::test_tx_mode_fails_when_readback_mismatches`; `tests/web/test_socket_peercred.py::test_foreign_uid_tmp_peer_is_refused` |
-| radio / band ownership | truthful outcomes: a start fails unless every required component is verified ready, and a failed start stops again what it launched or names what stayed up; a stop counts only when the process ceased and its endpoints are gone; an update with a failed part reports failure; the CLI exit status follows the result ([Truthful outcomes](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:900` `_start_coordinator`, `:1690` `_roll_back_start`, `:2621` `_stop_impl`; `lhpc/core/start_plan.py:77` `verify_start`; `lhpc/core/outcomes.py:71` `applied_ok`; `lhpc/core/service_maintenance.py:1773` `update`; `lhpc/adapters/cli/main.py:254` `_render` | the start, stop and update locks | owned records and markers cleared only on verified stop | `tests/core/test_outcomes.py::test_applied_ok_requires_all_verified`; `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified`, `::test_dependent_not_started_when_daemon_unready`; `tests/core/test_process_ownership.py::test_a_failed_start_leaves_nothing_of_the_stack_running`, `::test_a_failed_start_stops_the_daemon_only_if_it_launched_it`, `::test_a_failed_start_names_what_it_could_not_stop`; `tests/core/test_stop_propagation.py::test_stop_unverified_keeps_markers`; `tests/core/test_known_working.py::test_candidate_clear_failure_is_truthful_incomplete`; `tests/cli/test_cli.py::test_status_unknown_stack_exits_one`; gap: a daemon start's check of each band's CONF socket separately (only a daemon that is not ready at all is driven); the web flash agreeing with the CLI exit status (no test drives one result through both) |
-| radio / band ownership | no start without a resolvable identity ([Identity](#identity-and-callsigns)) | `lhpc/core/service_params.py:2813` `enforce_identity` | the start locks (re-judged under them) | — | `tests/core/test_identity.py::test_start_refuses_identity_before_boot_hook_and_feed_clear`, `::test_licensed_with_neither_value_is_refused`; `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` |
+| radio / band ownership | truthful outcomes: a start fails unless every required component is verified ready, and a failed start stops again what it launched or names what stayed up; a stop counts only when the process ceased and its endpoints are gone; an update with a failed part reports failure; the CLI exit status follows the result ([Truthful outcomes](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:900` `_start_coordinator`, `:1690` `_roll_back_start`, `:2621` `_stop_impl`; `lhpc/core/start_plan.py:77` `verify_start`; `lhpc/core/outcomes.py:71` `applied_ok`; `lhpc/core/service_maintenance.py:1781` `update`; `lhpc/adapters/cli/main.py:254` `_render` | the start, stop and update locks | owned records and markers cleared only on verified stop | `tests/core/test_outcomes.py::test_applied_ok_requires_all_verified`; `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified`, `::test_dependent_not_started_when_daemon_unready`; `tests/core/test_process_ownership.py::test_a_failed_start_leaves_nothing_of_the_stack_running`, `::test_a_failed_start_stops_the_daemon_only_if_it_launched_it`, `::test_a_failed_start_names_what_it_could_not_stop`; `tests/core/test_stop_propagation.py::test_stop_unverified_keeps_markers`; `tests/core/test_known_working.py::test_candidate_clear_failure_is_truthful_incomplete`; `tests/cli/test_cli.py::test_status_unknown_stack_exits_one`; gap: a daemon start's check of each band's CONF socket separately (only a daemon that is not ready at all is driven); the web flash agreeing with the CLI exit status (no test drives one result through both) |
+| radio / band ownership | no start without a resolvable identity ([Identity](#identity-and-callsigns)) | `lhpc/core/service_params.py:2812` `enforce_identity` | the start locks (re-judged under them) | — | `tests/core/test_identity.py::test_start_refuses_identity_before_boot_hook_and_feed_clear`, `::test_licensed_with_neither_value_is_refused`; `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` |
 | lab | a simulated reboot kills only owned groups and runs the real boot restore ([testlab](testlab.md)) | `testlab/lhpc_testlab/ops.py:283` `power` | admission (waited for) | the lab root's boot identity | `testlab/tests/unit/test_testlab.py::test_simulated_reboot_kills_owned_groups_and_runs_boot_restore`; `testlab/tests/acceptance/test_power_network.py::test_simulated_reboot_advances_boot_identity_and_recovers` |
 | lab | the lab's controls never signal a reused PID ([testlab](testlab.md)) | `testlab/lhpc_testlab/supervisor.py:118` `pid_alive`, `:132` `_nginx_master` | none | — | `testlab/tests/unit/test_testlab.py::test_nginx_ctl_never_signals_a_reused_pid`, `::test_a_reused_pid_naming_the_config_is_not_the_lab_nginx`, `::test_the_lab_nginx_master_is_verified_and_signalled`, `::test_check_does_not_take_a_reused_pid_for_the_fake_gpsd` |
 
diff --git a/docs/maintenance.md b/docs/maintenance.md
index eac5a8e5..f8d8af1a 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -94,7 +94,7 @@ repositories.
 ## Policy
 
 - **Freeze the config, float the tool.** Dev tools are unpinned (`pytest`, `ruff`, `bandit`,
-  `pytest-cov`, `zstandard`); ruff's rules are pinned in `[tool.ruff.lint] select`/`ignore`,
+  `pytest-cov`, `pytest-xdist`, `zstandard`); ruff's rules are pinned in `[tool.ruff.lint] select`/`ignore`,
   bandit runs `-lll`. When a tool complains, fix the code or change the config with a reason;
   never re-pin the tool.
 - **`-m contract` states what LHPC promises; the full suite protects it.** What to tag:
diff --git a/docs/testlab.md b/docs/testlab.md
index c724258f..718cbb34 100644
--- a/docs/testlab.md
+++ b/docs/testlab.md
@@ -145,8 +145,7 @@ subprocesses import `lhpc` from the editable install, i.e. whichever checkout wa
     Such a timeout is never retried (a test body is not re-run because it failed): it captures the
     program's stack (SIGABRT with Python's faulthandler) and fails. NomadNet's case also records,
     before anything is stopped, the shared instance's sockets and every Reticulum process's stack
-    (`reticulum_peers_snapshot`; the release lane runs with `PYTHONFAULTHANDLER=1`), because its
-    first start has hung on a loaded runner for a cause not yet measured.
+    (`reticulum_peers_snapshot`; the release lane runs with `PYTHONFAULTHANDLER=1`).
     A regression that only hangs is therefore unmarked: the release stops at its proof, nothing is
     released and nothing frozen (a deliberate trade). An automation may freeze a stack only on a
     marked failure.
```
