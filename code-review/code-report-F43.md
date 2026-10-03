# Code report F43 — a slow-target build proof before every release

Plan: `plans/PLAN-F43.md` v3 (review GREEN WITH NOTES). Base `main` e5187f70 (v0.11.10). Branch
`cons/F43-r7` (earlier corrections were on other branches).

## Commits

| # | sha | subject | files |
|---|---|---|---|
| plan | d61bdec | F43: plan v3 — SHA-free baseline keys, moved pins without a Zero row (amended 8e57d70d: §8 policy sentence says "that (component, op)" and "above limit/4 (= 50 % of the budget)"; nothing else changed) | `plans/PLAN-F43.md` |
| 1 | 421061b | the slow-target baseline file | `tests/data/slow-target-builds.toml` |
| 2 | 0226a3f | the budget test and its shared rule | `lhpc/core/slow_target.py`, `tests/install/test_slow_target_budget.py` |
| 3 | 19cb0fa | name the CLI-venv and upstream-fetch timeouts | `lhpc/core/service_binary_ops.py`, `lhpc/core/service_maintenance.py`, `lhpc/core/slow_target.py`, `tests/install/test_slow_target_budget.py` |
| 4 | — | **not implemented** (see Deviations) | — |
| 5 | 89c35a2 | the slow-build lane (row C) | `testlab/tests/slowbuild/{__init__,conftest,test_slow_build}.py` |
| 6 | f2c0679 | the slow-build job | `.github/workflows/testlab.yml` |
| 7 | 3491e6a | policy sentence, row A's recording table, the lane in the docs, CHANGELOG | `docs/maintenance.md`, `docs/test-matrix.md`, `docs/testlab.md`, `CHANGELOG.md` |
| 8 | 694cf00 | the adoption log times every git step | `lhpc/core/install.py`, `tests/install/test_source.py` |
| 9 | f3ec1ed | self-update pip sync and CLI venv steps print their time | `lhpc/core/service_selfupdate.py`, `lhpc/core/service_binary_ops.py`, `tests/install/test_selfupdate_service.py`, `tests/install/test_binary_install.py` |
| 10 | 7f08cd8 | calibrate.sh, the fixed calibration workload | `testlab/slowbuild/calibrate.sh`, `testlab/slowbuild/calib-src/{Makefile,a..h.c,main.c}` |

## Tests per commit

Command for every commit (run once per commit, checked out in place):
`python -m pytest -q -p no:cacheprovider tests/repo <the touched modules that exist at that commit>`
with touched modules = `tests/install/test_slow_target_budget.py tests/install/test_source.py
tests/install/test_binary_install.py tests/install/test_selfupdate_service.py
tests/stacks/test_graywolf_upstream.py testlab/tests/slowbuild`; plus `ruff check lhpc testlab`
and `ruff check tests --select F,E9` (both "All checks passed!" at every commit).

| commit | tests | red before? | result at the commit |
|---|---|---|---|
| 421061b | `tests/repo` (preservation) | n/a (data file) | 3 failed, 781 passed, 18 skipped — the 3 are the pre-existing `zstd` cases below |
| 0226a3f | `test_slow_target_budget.py::test_every_baseline_entry_is_well_formed`, `::test_every_calibration_entry_is_well_formed`, `::test_every_excluded_component_exists_and_says_why`, `::test_coverage_names_every_kind_of_operation`, `::test_coverage`, `::test_budget[*]`, `::test_the_limits_are_the_products`, `::test_every_required_entry_has_a_key_class_on_this_tree`, `::test_deps_key_ignores_the_version_and_follows_the_dependencies`, `::test_no_key_holds_an_lhpc_sha`, `::test_a_moved_key_is_carried_not_dropped`, `::test_a_carried_entry_from_two_minors_ago_is_no_baseline`, `::test_compare_*` (6), `::test_build_is_budgeted_on_its_quiet_gap`, `::test_calibration_*` (2) | yes — against the parent the module does not import (`lhpc.core.slow_target` absent): "1 error during collection" | 4 failed, 806 passed, 19 skipped: `test_coverage` **red by design** (45 operations unmeasured) + the 3 `zstd` cases; `test_budget[build]` SKIPPED by name ("L1 … lhpc.core.progress.STALL_S, which this tree does not have (F42)") |
| 19cb0fa | `::test_every_op_has_a_limit`, `::test_the_limits_are_the_products` (L5/L6 lines), `::test_budget[cli-venv]`, `::test_budget[deb-fetch]`; preservation `tests/stacks/test_graywolf_upstream.py`, `tests/install/test_binary_install.py` | yes — the new test file against the parent's product code: 3 failed (`test_coverage`, `test_every_op_has_a_limit`, `test_the_limits_are_the_products`) | 4 failed, 809 passed, 19 skipped (same 4) |
| 89c35a2 | `testlab/tests/slowbuild` (opt-in: 11 skipped by name without `LHPC_SLOW_BUILD=1`) | n/a (new lane). Run here with `LHPC_SLOW_BUILD=1`: `test_slow_build_env` FAILED ("SLOW_CPUS=None…", "the cgroup limits are unreadable…"), `test_slow_build_budget` FAILED ("67 budget failure(s)…") — the lane fails, not passes, outside the throttled box | 4 failed, 809 passed, 30 skipped |
| f2c0679 | `tests/repo/test_workflow_shell.py` (10 passed: every run block parses with `bash -n`, no quote in a shell comment); YAML loaded with PyYAML (jobs `testlab`, `release-verify`, `slow-build`; `if:` identical to release-verify's); the JUnit gate script run against a JUnit with all three cases skipped → exit 1, all three passed → exit 0 | preservation | 4 failed, 809 passed, 30 skipped |
| 3491e6a | `tests/repo` (incl. `test_version_consistent.py::test_changelog_leads_with_the_current_version`) | preservation | 4 failed, 809 passed, 30 skipped |
| 694cf00 | `tests/install/test_source.py::test_adoption_log_times_every_git_step[0]`, `[124]`; preservation `test_source.py`, `test_auto_install.py`, `test_install.py` (749 passed, 7 skipped) | yes — against the parent: 2 failed | 4 failed, 811 passed, 30 skipped |
| f3ec1ed | `tests/install/test_selfupdate_service.py::test_run_service_times_the_venv_sync`, `tests/install/test_binary_install.py::test_cli_venv_provisioning_times_every_step` | yes — against the parent: 2 failed (`assert 0 == 2` on the `[venv]` lines) | 4 failed, 813 passed, 30 skipped |
| 7f08cd8 | `tests/repo`, `testlab/tests/slowbuild` (skipped); `bash -n calibrate.sh`; `bash calibrate.sh` run once here: `cpu=9.6 io=24.8 mem=3.7 workload=sha256:ae7c6013…` (unthrottled, not evidence) | n/a | 4 failed, 813 passed, 30 skipped |

**Pre-existing, not this change:** `tests/install/test_binary_install.py` — the three parametrized
archive-extraction cases need the `zstd` binary, which this container lacks
(`FileNotFoundError: 'zstd'`). They fail identically with `lhpc/` checked out at e5187f70; the
fourth environment failure seen there (`test_binary_channel.py::test_doctor_is_quiet_for_a_healthy_binary_install`)
is outside the named modules and also fails on the base.

**Final summary lines at HEAD (7f08cd8):** `ruff check lhpc testlab` → All checks passed!;
`ruff check tests --select F,E9` → All checks passed!; pytest on the set above → `4 failed, 813
passed, 30 skipped` (1 by design + 3 pre-existing).

## Live proof needed (not run here, not claimed)

1. **Row C, the `slow-build` job.** Dispatch `testlab.yml` with `release_verify=true` on the
   branch (or push to `main`). Pass: the job is green, the JUnit `junit-slow-build.xml` has
   `test_slow_build_env`, `test_slow_build_calibrated`, `test_slow_build_budget` passed (the job's
   own gate checks it), and artifact `slow-build-evidence` holds `slow-target-builds.toml` with
   one `throttled-ci` entry per lane operation. **It cannot pass on this branch yet**: no Zero
   baseline or calibration exists (rule (2), "uncalibrated"), and builds carry no `[progress]
   longest quiet` line until F42 + change 4. Expected today: red, every missing item named.
   Unverified: that `docker --cpus/--memory` are enforced on the hosted arm runner
   (`test_slow_build_env` fails if not), the runner-minute cost, and that the self-update case
   runs end to end in the lab (`lhpc self-update --run-service` from the previous tag in a
   separate venv with the lab provider).
2. **Row A on `lhpc-e293`.** `bash testlab/slowbuild/calibrate.sh` and rows 6, 7, 8 plus a
   one-click self-update, recorded per `docs/test-matrix.md#slow-target-baseline`. Pass:
   `python -m pytest -q tests/install/test_slow_target_budget.py` green except a named L1 skip
   before F42.
3. **The calibration itself**: the first green `test_slow_build_calibrated` at `SLOW_CPUS=0.5`
   (or lower) against the Zero's `[[calibration]]` of the same workload hash.

## Self-audit proof per change

**1 — baseline file.** (a) Nothing unmeasured is in the baseline. (b) The file has only
`[excluded]`; every exclusion names its test-matrix row. (c) Read by nothing else; `tests/repo`
green. (d) Re-read the exclusions: `meshcom-gps-relay` has a source but no build — excluded with
the stack, as row 12 builds it on the Pi 5 only.

**2 — budget test + `lhpc/core/slow_target.py`.** (a) Every required (component, op) is measured
or excluded; every limit ≥ 2 × its slowest entry; a moved key is carried; §8 and §4b rules.
(b) `slow_target.required()`, `limit()`/`LIMITS`, `zero_baseline()`, `compare()`,
`calibration_failures()`; `test_coverage`, `test_budget`. (c) Product import only (no command,
no file write); pin validation untouched; the bot's pin moves change `pin:` keys only, which are
carried, never a new failure by themselves (`test_a_moved_key_is_carried_not_dropped`); no pin
literal in tests (`tests/repo/test_no_pin_literals_in_tests.py` green). (d) Re-read
`test_deps_key…`: it edited the repo's own `pyproject.toml` text by literal and would break when
a dependency changes — rewritten on a synthetic document (fixed in the commit).

**3 — constants.** (a) L5/L6 readable by the test, values unchanged. (b) `CLI_VENV_TIMEOUT_S`,
`UPSTREAM_FETCH_TIMEOUT_S` in the two call sites; `_l6_fetch()` reads what the fetch actually
gets (graywolf's manifest `build_timeout` 900 on this tree, else the constant). (c) Graywolf
update and binary install suites green (except the pre-existing `zstd` cases). (d) Re-read the
L6 call: the `or` keeps the old meaning for `build_timeout = 0`.

**5 — the lane.** (a) No measurement without the throttle; no evidence without exit 0, no
failure line and the op's own line; nothing measured passes the budget. (b) `_env_problems()`
asserted in `test_slow_build_env` and in every `_record()`; `_BAD`, `_QUIET`, `_CLONE`,
`_CHECKOUT`, `_VENV`, `_PIP_SYNC` assertions; `test_slow_build_budget` asserts no failure and a
non-empty op list. (c) Opt-in: skipped in every other lane; the release lane and its JUnit are
untouched. (d) Re-read the env check: a missing `SLOW_CPUS` read as 0 → FAIL, not skip.

**6 — workflow job.** (a) Row C on every release candidate; a skipped case cannot be green.
(b) `if:` copied from release-verify; JUnit non-empty check; the Python gate over three named
cases. (c) `release-verify` and `testlab` jobs unchanged; separate tmp volume and artifact name.
(d) Re-read `su lhpclab -c /tmp/lane.sh`: `su` may not keep `SLOW_CPUS`, which would only fail
the run — now passed explicitly in the `su -c` string (fixed in the commit).

**7 — docs.** (a) One place per fact: the policy in `maintenance.md`, the recording procedure in
`test-matrix.md`, the lane in `testlab.md`, the schema in the TOML header. (b) Text as listed.
(c) `test_version_consistent` (first numbered heading) still 0.11.10. (d) Re-read the
clone/checkout row: a shared source tree logs under the component that adopted it — said so
(fixed in the commit).

**8 — git timing.** (a) `[git] <what> <n> s` after every clone and post-clone step, success or
failure. (b) `took()` in `Installer._clone`. (c) Same calls/timeouts/results; write errors
swallowed as before; install/auto-install/source suites green. (d) Leading `\n` added because
streamed `git --progress` can end without a newline.

**9 — pip/venv timing.** (a) L4/L5 quantities visible. (b) the two `print(..., file=sys.stderr)`
lines. (c) Return values unchanged; self-update service suite green; on a box the line lands in
`logs/lhpc-selfupdate.log` through the unit's `StandardError=append`. (d) Re-read import order in
`service_binary_ops.py` (ruff isort).

**10 — calibrate.sh.** (a) One fixed workload, hashed. (b) `workload=` over the script + `*.c` +
`Makefile` under `calib-src/`; the build runs in a temp copy. (c) Not called by any CI lane but
slow-build. (d) The hash first included every file in `calib-src/`, so a local `make` there would
change it — narrowed to the tracked kinds.

## Deviations from the plan

1. **Change 4 not implemented.** `lhpc/core/progress.py` (F42) is not on this base (e5187f70);
   F42 has not landed. Consequences, all visible: the L1 budget
   case skips by name in ordinary CI; in the lane every build lacks the `[progress] longest
   quiet` line and FAILS as "not evidence"; `slow_target.limit("build")` raises
   `LimitUnavailable`, which the lane reports as a failure. Change 4 lands after F42.
2. **The §8 compare function lives in `lhpc/core/slow_target.py`**, not in the test file: the
   lane cannot import a test module (tests/README rule 7), CI's `test` job does not install the
   lab package; precedent `lhpc/core/build_regression.py`.
3. **Coverage is one test listing every missing (component, op)**, not one case per pair.
4. **Key choices the plan left open:** graywolf's `build` key is `deb:<version its fetch step
   downloads>`; `cli-venv` uses meshtastic's `pin_commit` as the plan says (a CLI version bump
   alone leaves it fresh; row C's E still gates it); clone/checkout are required once per source
   tree, named after its first non-excluded component.
5. **Docs addition:** `test-matrix.md` says any other entry the budget test names is recorded from
   the row that runs it on the Zero. Rows 6, 7, 8 alone do not produce daemon, RadioLib, chat,
   voice-cli, kiss or graywolf entries, which coverage and rule (2) require.
6. Review fixes were folded into commits 2, 6 and 7 before the push.

## Notes for the maintainer

- **Merge order matters.** On this branch CI `test` is red (`test_coverage`, 45 unmeasured
  operations) and `slow-build` cannot pass. Before merging: F42, then change 4, then row A
  committed into the baseline. Merging earlier turns `main` red and blocks every bot release if
  `slow-build` is a required check (§9 Q1).
- L1 is the only build limit budgeted; L2 (24 h) is not, per §4.

## Adversarial self-review

Hostile re-read of the whole diff; found and fixed:
- calibration entries' `date` was not validated, so a malformed baseline could crash
  `calibration_failures` instead of failing by name → validated in
  `test_every_calibration_entry_is_well_formed`.
- the deps-key test depended on the repo's literal dependency string → synthetic document.
- environment through `su` → passed explicitly.
- shared-source adoption log location undocumented → documented.
- the workflow's exit code alone would pass a run in which every case skipped → the JUnit gate
  (in commit 6 from the start; verified with a skipped and a passing JUnit).
Checked and left: a previous-minor lookup across a major bump finds nothing → "no Zero baseline"
FAIL (fails closed); `[venv]` is printed only when the runner returns (a raised exception prints
nothing, and the run then fails anyway).

## Correction 1

**Defect (the maintainer's local gate).** `tests/install/test_slow_target_budget.py::test_coverage`
was red by design on the branch itself ("no entry in slow-target-builds.toml for 45
operation(s): run row A…"). A release lane that is permanently red until row A blocks every release.
Also a rule break: 3491e6a added a `## Unreleased` CHANGELOG heading, which the repository never uses.

**Fix: the plan's own rule, stated as a BOOTSTRAP state, with no new mechanism.** While the baseline holds
**no `[[measured]]` entry at all** (first introduction, before any row A):
- `test_coverage` SKIPS visibly: `bootstrap: no row A yet — 45 operations unmeasured: loraham-daemon
  build, …, meshtastic-cli-venv cli-venv` (`slow_target.bootstrap_reason`, the one place the state
  is defined). Once one entry exists it enforces exactly as before: red for every unmeasured operation.
- In the slow-build lane every row-C case (`test_slow_build_env`, `test_slow_build_stack[*]`,
  `test_slow_build_selfupdate`) runs and judges the candidate unchanged. `test_slow_build_calibrated`
  records the run and skips by name only when its single failure is the missing Zero calibration.
  `test_slow_build_budget` waives only "no Zero baseline for …". Missing row-C evidence and a limit below
  twice a measurement still fail it. After that it skips with the bootstrap reason.
- The job's JUnit gate reads the same baseline file. In the bootstrap state, and only then, it accepts
  a skip of calibrated/budget whose message carries `bootstrap: no row A yet`. It now also requires every
  `test_slow_build_stack[*]` (at least one) and `test_slow_build_selfupdate` to have PASSED, so an
  all-skipped run is still refused.
- Documented once in `docs/maintenance.md` (next to the policy sentence) and in the baseline file's
  header. `docs/testlab.md`'s gate line was updated to match, because it named "those three cases" only.
- CHANGELOG: the line is now the last entry of `## 0.11.10`, and the `## Unreleased` heading is gone.

**Amended commits** (`git commit --fixup`, then `git rebase -i --autosquash e5187f70`. The sequence
editor was a script that kept git's autosquash order unchanged and added one `exec git commit --amend -F
<msg>` after each amended commit, so its message matches the change. With plain `GIT_SEQUENCE_EDITOR=true`
the messages would have stayed stale.):

| was | now | what changed |
|---|---|---|
| 421061b | 0417f81 | baseline header: the bootstrap sentence |
| 0226a3f | 78d3f5f | `slow_target.BOOTSTRAP`, `bootstrap_reason()`; `_check_coverage()`; `test_coverage` skips in bootstrap; two new tests |
| 89c35a2 | 49ee771 | calibrated and budget: the bootstrap skip after row C has judged |
| f2c0679 | e41ca53 | the JUnit gate (bootstrap skip accepted only in that state; stack and self-update cases required); new `tests/repo/test_slow_build_gate.py` |
| 3491e6a | 84ca33c | CHANGELOG line moved into 0.11.10; the bootstrap sentence in `docs/maintenance.md`; `docs/testlab.md` gate line |

The JUnit-gate test went into f2c0679 (the gate's own commit), not into 0226a3f, because it tests the
workflow text.

**Patch-ids (`git show <c> | git patch-id --stable`), before → after:**

| was → now | patch-id before | after | |
|---|---|---|---|
| 0c428be → 0c428be | 3f942d297087 | 3f942d297087 | same commit |
| d61bdec → d61bdec | 496cff7a8d52 | 496cff7a8d52 | same commit |
| 421061b → 0417f81 | 14c251cf07f8 | 655eb46b57f5 | amended |
| 0226a3f → 78d3f5f | bb3e099d18ee | 34fefe9bcadd | amended |
| 19cb0fa → 8192010 | b086c5780449 | b086c5780449 | SAME |
| 89c35a2 → 49ee771 | 82cc8e603a7f | 1728291ec213 | amended |
| f2c0679 → e41ca53 | 957b0aa5a5bb | 9812f85dd939 | amended |
| 3491e6a → 84ca33c | bf0d24d6c519 | d14dd8b3a592 | amended |
| 694cf00 → fdf6055 | 9ed7554ec23c | 9ed7554ec23c | SAME |
| f3ec1ed → ea30987 | 4d1bbd8a0749 | 4d1bbd8a0749 | SAME |
| 7f08cd8 → 24612cf | 1a57aff0edb4 | 1a57aff0edb4 | SAME |
| a273bb7 → (this commit) | cb5bd6292252 | — | amended (this section, gate-1 packet) |

The tree after the rebase is identical to the tree of the un-squashed fixup stack (`git diff` empty).

**Tests (red before).**
- `test_coverage_skips_visibly_while_bootstrapping` (empty baseline → skip whose reason is exactly
  `bootstrap: no row A yet — 2 operations unmeasured: a build, b clone`).
- `test_coverage_enforces_once_one_entry_is_measured` (one measured entry, one unmeasured operation →
  AssertionError naming `b clone` only; fully covered → passes).
- Red before: with the test file on the old `slow_target.py`, these two tests and `test_coverage` fail
  (3 failed, `AttributeError: bootstrap_reason`). At the parent, `test_coverage` was the reported
  red: 1 failed.
- `tests/repo/test_slow_build_gate.py` runs the gate's exact heredoc text over synthetic JUnit files
  and baselines (6 cases):
  - an all-skipped run is refused even in bootstrap;
  - bootstrap-skip plus passed row-C cases is accepted, with a `::warning::bootstrap`;
  - the same skips are refused once one entry is measured;
  - another skip reason is refused in bootstrap;
  - a failed or missing stack case is refused;
  - a fully passed run is accepted.
- Red before: all 6 fail on the old workflow (no such gate). The old gate script run by hand on the
  "bootstrap-skip + passed row C" JUnit exits 1 (`not passed …: test_slow_build_calibrated,
  test_slow_build_budget`).
- Run with `LHPC_SLOW_BUILD=1` outside the throttle, `test_slow_build_budget` still FAILS (missing
  evidence, L1 unavailable). The bootstrap waiver hides nothing but "no Zero baseline".

**Per amended commit** (`pytest -q tests/repo` + `tests/install/test_slow_target_budget.py` +
`testlab/tests/slowbuild` where present; `ruff check lhpc testlab`; `ruff check tests --select
F,E9`):

| commit | pytest | ruff |
|---|---|---|
| 0417f81 | 391 passed, 5 skipped | All checks passed! / All checks passed! |
| 78d3f5f | 418 passed, 7 skipped (`test_coverage` SKIPPED: bootstrap, 45 operations) | both passed |
| 8192010 | 421 passed, 7 skipped | both passed |
| 49ee771 | 421 passed, 18 skipped (lane opt-in) | both passed |
| e41ca53 | 429 passed, 18 skipped | both passed |
| 84ca33c | 429 passed, 18 skipped | both passed |
| HEAD (report) | 429 passed, 18 skipped | both passed |

No full-suite run was made. One process note: the per-commit loop ran longer than the tool's 2-minute
limit, so the harness moved it to the background. I waited for it and made no other change meanwhile.

**Adversarial self-review of the correction:**
- *Found and fixed:* `docs/testlab.md` still said the gate needs "those three cases" → updated.
- *Found and fixed:* the f2c0679 and 3491e6a commit messages described the old gate and the
  "Unreleased line" → reworded to match.
- *Found and fixed:* my first enforcing-test regex depended on pytest's message re-indentation →
  replaced with plain containment checks.
- *Checked, left:* the gate matches the bootstrap marker as a substring of the skip message. Only two
  named cases may use it, and only when the file has no `[[measured]]`, so no other skip can pass.
- *Checked, left:* the calibrated skip also covers a workload-hash mismatch ("uncalibrated"), but only
  in bootstrap, where no Zero calibration can exist for any workload.
- *Checked, left:* the gate and the lane read the same checked-out baseline (the container copies the
  same tree), so they cannot disagree about the state.
- **Still open, not caused by this correction (Deviation 1):** the `slow-build` job stays red on this
  branch until F42 + change 4. Every build lacks the `[progress] longest quiet` line, and
  `limit("build")` is unavailable. The ordinary suite is no longer red: `test_coverage` skips by name.
  The merge-order note above therefore changes: CI `test` is green; `slow-build` still needs F42 and
  change 4 before it can pass, but no longer needs row A.

## Correction 2

Four gate-1 findings, each amended into its commit (`git commit --fixup <sha>`, plus
`git commit --fixup=amend:<sha>` where the commit message had to change), then
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash e5187f70ae4e81a1081a835be06f84746222c3b6`, with no
conflicts. Point 4 had no commit (change 4 was "not implemented"), so it is a new commit. Autosquash
left that new commit after the report commit. I moved it in front by cherry-picking the two commits
in swapped order; they touch disjoint files. The tree after the rebase and the swap is identical to
the tree of the unsquashed fixup stack (`git diff <stack> HEAD` empty).

**Merge-order note, superseding Correction 1's last paragraph:** CI `test` is **red** on this branch
again, by design: `tests/install/test_slow_target_budget.py::test_budget[build]` FAILS because L1
(`lhpc.core.progress.STALL_S`) is not on this base. That is point 1: an unreadable limit fails. The
failure goes away only when F42 lands. `test_coverage` still skips with the bootstrap reason.

### Point 1: an unreadable limit FAILS (amended 78d3f5f → d48095f)

- **Change.** `slow_target.limit()` wraps a reader's `LimitUnavailable`, `ImportError` or
  `AttributeError` into `LimitUnavailable("<limit name> (read by slow_target.<reader>): …")`.
  `test_budget` now calls `_check_budget`, which `pytest.fail`s with `<op> of <every component
  required for that op>: the limit cannot be read — <limit name> (read by …): <why>`. The coverage
  bootstrap SKIP is untouched.
- **Tests.**
  - `tests/install/test_slow_target_budget.py::test_an_unreadable_limit_fails_naming_component_and_source`
  - `::test_limit_names_the_reader_when_the_product_lacks_it`
  - `::test_budget[build]` (now FAILS on this tree, by name: `build of graywolf, loraham-chat, …, rns:
    the limit cannot be read — L1 build stall (read by slow_target._l1_stall): L1 (build stall) is
    lhpc.core.progress.STALL_S, which this tree does not have (F42)`).
- **Red before.**
  - At the old commit, `test_budget[build]` was `SKIPPED [1] … L1 build stall cannot be checked on this
    tree`.
  - The new test file run against the old `slow_target.py` gives 2 failed: `test_budget[build]` (now a
    FAIL, not a skip) and `test_limit_names_the_reader_when_the_product_lacks_it`. The unwrapped
    `AttributeError` does not name the reader.
  - `test_an_unreadable_limit_fails_naming_component_and_source` exercises the new test-file helper, so
    its red-before is the old `test_budget`'s skip above.
- **What else could break, and the check.**
  - 38464fb (formerly 8192010) edits the same two files. Its patch-id is unchanged (b086c5780449), so
    its hunks still apply with the same context. That needed `_check_budget` placed after the new tests,
    so the lines before `test_every_op_has_a_limit` stay byte-identical.
  - The lane already turned `LimitUnavailable` into a failure; it now only gets a better message.

### Point 2: one verdict for every lane step, stdout and stderr (amended 49ee771 → 3b2e63f)

- **Change.** `testlab/tests/slowbuild/test_slow_build.py` has one helper:
  - `_judged(what, r)` FAILS the case when `r`'s stdout or stderr carries a rejection marker,
    quoting the line (`_reject`), or when the exit status is non-zero.
  - The marker set is `[stalled]`, `[timeout]`, `[fail]` and their longer forms: `[failed]`, and the
    job log's `[TIMED OUT after …]`, which the old pattern missed (case). The match is
    case-insensitive and anchored at the start of a line.
- **Where it applies.** Every step the lane runs goes through it:
  - install (`_install`)
  - build (`_build`; the build logs too, via `_reject`)
  - the adoption logs
  - deb fetch (`_deb_fetch`)
  - the CLI venv (`_cli_venv`, from the binary install's output)
  - the self-update helper (`_helper`)
  - `calibrate.sh` (`_calibrate`)

  I read the brief's "test" path as these remaining lane steps (install, calibrate). The lane runs
  no `lhpc test`.
- **Tests.** `testlab/tests/unit/test_slow_build_lane.py`, run in the ordinary unit lane (the lane
  module is loaded by path):
  - `::test_a_timeout_line_on_stderr_fails_every_path[install|build|deb-fetch|cli-venv|self-update|calibrate]`
  - `::test_a_marker_on_stdout_fails_every_path[…]`
  - `::test_a_non_zero_status_fails_every_path[…]`
  - `::test_every_marker_form_is_rejected[…]`
  - `::test_a_clean_step_passes_and_returns_both_streams` (the positive control: `0 failed` and
    `[failures: none]` mid-line are not markers)
- **Red before.**
  - The new test file against the old lane module: 26 failed (the step helpers do not exist).
  - A behavioural probe on the old lane module: a step returning rc 0 with `[timeout] step ran past its
    limit` on stderr. The old `_build` and `_deb_fetch` reached `_record`, i.e. they **accepted it as
    evidence**: `[('meshcore-cli', 'build'), ('graywolf', 'deb-fetch')]`.
  - The same probe on the new module: `Failed: building meshcore-cli: rejection marker — not evidence:
    '[timeout] step ran past its limit'`.
- **What else could break, and the check.** A successful step that prints a bracketed marker at the
  start of a line would now turn the lane red: a false FAIL, never a false pass. I searched `lhpc` and
  `lhpc_testlab` for such literals. The only hits are the job log's `[TIMED OUT …]`, the adoption log's
  `[fail] <step>`, and the daemon's `[fail]` reason. All three are written only on failure.

### Point 3: L4 on the introducing release (amended 49ee771 → 3b2e63f, 84ca33c → 6a4c9af)

- **Decision.** The brief's second rule: **"no evidence on the introducing release — measured from the
  next release on"**. I rejected the first (the candidate's own pip sync after the switch). The previous
  tag's helper has already synced the venv by then, so a second sync is a near no-op and would
  **under**-measure L4. That is a false-pass risk.
- **The rule, in one place.** It is in `docs/maintenance.md`'s policy paragraph, next to the bootstrap
  sentence. The evidence table in `docs/test-matrix.md` (`selfupdate-pip` row) points to it.
- **Implementation.**
  - The self-update case always records L3, the whole helper.
  - If the helper printed no pip sync line **and** the previous tag's
    `lhpc/core/service_selfupdate.py` lacks `[selfupdate] pip sync`, it notes the introducing release
    (`INTRODUCING`) and prints `L4_INTRODUCING`. Otherwise the missing line FAILS, as before.
  - The budget case's waivers are in one pure function, `_waived`. In the **bootstrap state only**, it
    waives the missing L4 evidence and appends `; lhpc-selfupdate selfupdate-pip (L4): no evidence on
    the introducing release — measured from the next release on` to the bootstrap skip reason. The
    reason still starts with `bootstrap: no row A yet`, so the JUnit gate needs no change.
  - Past bootstrap the failure stands, with that sentence added.
  - The job summary carries a `**NO EVIDENCE**` line.
  - The consequence is stated in the doc: row A cannot measure L4 on that release either, so the first
    complete row A comes with the next release.
- **Tests.**
  - `testlab/tests/unit/test_slow_build_lane.py::test_bootstrap_on_the_introducing_release_names_l4_and_waives_only_it`
  - `::test_missing_l4_evidence_fails_when_not_the_introducing_release`
  - `::test_past_bootstrap_the_introducing_release_still_fails_l4`
- **Red before.** All three fail on the old module (`_waived`, `L4_INTRODUCING` absent). The old lane
  failed the introducing release's self-update case outright ("no `[selfupdate] pip sync <n> s` line").
- **What else could break, and the check.**
  - A later release whose previous tag lacks the line would be waived too, but only in the bootstrap
    state, which no release after the first complete row A is in.
  - `tests/repo/test_slow_build_gate.py` (the gate's heredoc) is green: the skip message still carries
    the bootstrap marker.

### Point 4: `[progress] longest quiet` (new commit d6bfc57, plan §5 change 4)

- **Definition chosen.** The plan says "Watch keeps the longest gap without progress", and leaves open
  which instants count. Simplest reading, consistent with `progress.Watch`'s sampling: the largest
  `now - last progress` over the step, taken at every sample without progress and at the step's end.
  That is exactly the difference Watch compares with its stall limit, so `stall limit - longest
  quiet` is the margin, at the sampler's resolution. A step that never went quiet between samples
  reads at most one sample interval.
- **Code.** `lhpc/core/quiet_gap.py` (`QuietGap.sample(now, progressed)`, `finish(now)`, `line()`).
  - `jobs.run_job(quiet=True)` ends the step's log with `[progress] longest quiet <n> s` on success,
    failure and timeout. The line goes to the log only, never to the tail.
  - `lifecycle.build` passes `quiet=True` for every build step (CLI and auto-install).
  - The detached launcher prints it after every Build step, not after a Test step.
  - The budget test's use is unchanged and consistent: a build entry's `quiet_s` is the max over its
    step logs, budgeted against L1 (`slow_target.quantity`).
- **Deviation, stated.** `progress.Watch` does **not exist on this base**: it is F42's, which has not
  landed. So no sampler feeds `QuietGap` here. The line reports the step's **whole runtime**: nothing
  is observed between start and end. That is never less than a stall rule could have seen, so it can
  only make the budget stricter.
  - When F42 merges, its `Watch.check()` must call `QuietGap.sample(now, progressed)` and the runner
    must hand the gap to `run_job`. This is a small merge-time hook, untested here because Watch is
    absent.
  - Expect a one-line textual conflict in `lifecycle.build`'s `run_job(...)` call, where F42 adds
    `stall_s=stall_s`.
- **Tests.** `tests/core/test_quiet_gap.py`:
  - sampling semantics, 4 cases
  - the line in the lane's own pattern
  - `test_a_build_step_log_ends_with_its_longest_quiet_line` (success, failure, timeout; not in the tail)
  - `test_a_job_without_quiet_writes_no_line`
  - `test_the_quiet_line_reports_the_step_runtime_without_a_sampler`
  - `test_every_lifecycle_build_step_asks_for_the_line`
  - `test_the_web_launcher_prints_it_after_every_build_step`
  - `test_the_web_launcher_prints_none_for_a_test_run`
- **Red before.** Against the previous commit the module does not import (1 collection error). With
  `quiet_gap.py` present but `jobs.py`, `lifecycle.py` and `build_launcher_runtime.py` reverted, 4
  failed: the four wiring tests.
- **What else could break, and the check.** Every existing build log gains one last line. I searched
  the tests and the product for exact build-log contents, last-line parsing and launcher stdout
  equality. I found none: tails are unchanged, and the `run_job` fakes in `test_build_timeout.py`,
  `test_post_start.py` and `test_release_attribution.py` all take `**kw`. Those modules are **not run**
  here (the brief allows only touched modules), so CI is their check.

### Patch-ids (`git show <c> | git patch-id --stable`)

| before → after | patch-id before | after | |
|---|---|---|---|
| 0c428be → 0c428be | 3f942d297087 | 3f942d297087 | same commit |
| d61bdec → d61bdec | 496cff7a8d52 | 496cff7a8d52 | same commit |
| 0417f81 → 0417f81 | 655eb46b57f5 | 655eb46b57f5 | same commit |
| 78d3f5f → d48095f | 34fefe9bcadd | 88c6073904e3 | amended (point 1) |
| 8192010 → 38464fb | b086c5780449 | b086c5780449 | SAME |
| 49ee771 → 3b2e63f | 1728291ec213 | 899dd3cbf3fb | amended (points 2, 3) |
| e41ca53 → ddf3dd3 | 9812f85dd939 | 9812f85dd939 | SAME |
| 84ca33c → 6a4c9af | d14dd8b3a592 | 026fc59fac01 | amended (point 3) |
| fdf6055 → 6af68c9 | 9ed7554ec23c | 9ed7554ec23c | SAME |
| ea30987 → 20f7716 | 4d1bbd8a0749 | 4d1bbd8a0749 | SAME |
| 24612cf → 2b4680b | 1a57aff0edb4 | 1a57aff0edb4 | SAME |
| — → d6bfc57 | — | eccf5e187cf0 | new (point 4) |
| 67f2a90 → (this commit) | 4fd3d79dff4a | — | amended (this section, gate-1 packet) |

ea30987 is the commit the brief names for point 3. It is unchanged: the rule lives in the lane and
the docs, and the product's pip sync line stays as it was.

### Tests per amended commit

The command was `pytest -q tests/repo` plus whichever of `tests/install/test_slow_target_budget.py`,
`tests/core/test_quiet_gap.py`, `testlab/tests/unit/test_slow_build_lane.py` and
`testlab/tests/slowbuild` exist at the commit, then `ruff check lhpc testlab` and
`ruff check tests --select F,E9`. Each ran in a detached worktree with `PYTHONPATH` set to it, and I
verified that the worktree's own `lhpc` was imported.

| commit | pytest | ruff |
|---|---|---|
| d48095f | 1 failed, 420 passed, 6 skipped | All checks passed! / All checks passed! |
| 3b2e63f | 1 failed, 449 passed, 17 skipped | both passed |
| 6a4c9af | 1 failed, 457 passed, 17 skipped | both passed |
| d6bfc57 | 1 failed, 470 passed, 17 skipped | both passed |
| HEAD | 1 failed, 470 passed, 17 skipped | both passed |

The one failure at every commit is `test_budget[build]`, the intended point-1 failure (L1 unreadable
before F42). No full-suite run was made and no background job was used. Not run: `tests/core/test_jobs.py`,
`test_build_timeout.py`, `test_build_launcher_runtime.py`, `tests/core/test_post_start.py` and
`testlab/tests/unit/test_release_attribution.py`. They cover the product files point 4 touches, but
are not touched test modules.

### Adversarial self-review (before the push)

- *Found and fixed:* my first `_check_budget` raised `pytest.fail` inside the `except`, so the report
  carried a chained-exception banner. The fail is now raised outside it.
- *Found and fixed:* the first version kept the old assertion in `test_budget` as well, duplicating
  `_check_budget`. `test_budget` now only calls the helper. The helper sits last, which keeps the
  context of 38464fb's hunk byte-identical.
- *Found and fixed:* the old marker pattern was case-sensitive and missed `[TIMED OUT after …]` and
  `[failed]`. The new one matches those, but not `[failover]`-like words (`\b`).
- *Found and fixed:* the unit test first patched the global `time.monotonic`. It now patches only
  `jobs.time`.
- *Found and fixed:* three commit messages described the old behaviour ("L1 is skipped by name", no
  stderr scan, no L4 rule). They were reworded through `amend!` commits; autosquash applies them with
  `GIT_SEQUENCE_EDITOR=true`.
- *Checked, left:* the L4 waiver depends on the previous tag's source text. It can only waive in the
  bootstrap state, so a removed line in a later release still fails loudly.
- *Checked, left:* the adoption log's `[fail] <step>` line can appear on an install that later
  succeeds. The lane then fails, which is a false FAIL, never a false pass. This is unchanged from
  before.
- *Open:* point 4 reports the whole step runtime until F42's Watch feeds `QuietGap`, and the hook is
  untested here. `slow-build` still cannot pass on this branch before F42 (L1 unreadable).

## Correction 3

One gate-1 finding (one place per fact): the `selfupdate-pip` row of the recording table in
`docs/test-matrix.md` restated the L4 rule ("none on the release that introduces the line"). The
rule lives only in `docs/maintenance.md`, in the slow-target build row paragraph under Branches and
releases. The fix is amended into 6a4c9af with `git commit --fixup 6a4c9af`, then
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash e5187f70ae4e81a1081a835be06f84746222c3b6`. The
rebase had no conflicts. This report section is a new commit on top, so the earlier report commit
keeps its patch-id.

**Change (6a4c9af → 84b3612, one table cell).** The cell now ends with
`which release has it: see [maintenance](maintenance.md#branches-and-releases), the slow-target build row`.
That is a pure reference in the repository's usual `[maintenance](maintenance.md#…)` link style, and
it does not state the rule again. The commit message stays the same. It already says that the
recording table's `selfupdate-pip` row points to the rule.

**Other restatements: none.** I ran
`grep -rn -i -E "introduc|next release|selfupdate-pip|L4\b" docs README* CHANGELOG.md testlab/*.md`
and `grep -rn -i "pip sync" docs README* CHANGELOG.md`. The L4 rule shows up only at
`docs/maintenance.md:159–165`, which is the single place. The only other hit is the table cell fixed
here. `maintenance.md:137` ("introduces the narrower range") matches the word "introduc" but is about
something else. `docs/testlab.md` and the CHANGELOG line do not state the rule.

**Patch-ids (`git show <c> | git patch-id --stable`, first 12 hex digits).** Only the amended commit
changed:

| old | new | patch-id old | patch-id new |
|---|---|---|---|
| 0c428be | 0c428be | 3f942d297087 | 3f942d297087 |
| d61bdec | d61bdec | 496cff7a8d52 | 496cff7a8d52 |
| 0417f81 | 0417f81 | 655eb46b57f5 | 655eb46b57f5 |
| d48095f | d48095f | 88c6073904e3 | 88c6073904e3 |
| 38464fb | 38464fb | b086c5780449 | b086c5780449 |
| 3b2e63f | 3b2e63f | 899dd3cbf3fb | 899dd3cbf3fb |
| ddf3dd3 | ddf3dd3 | 9812f85dd939 | 9812f85dd939 |
| **6a4c9af** | **84b3612** | 026fc59fac01 | **9574802d6951** |
| 6af68c9 | 9632383 | 9ed7554ec23c | 9ed7554ec23c |
| 20f7716 | 66814fe | 4d1bbd8a0749 | 4d1bbd8a0749 |
| 2b4680b | ffdb337 | 1a57aff0edb4 | 1a57aff0edb4 |
| d6bfc57 | fa22a22 | eccf5e187cf0 | eccf5e187cf0 |
| a1c658f | dbcc85e | a02b837a518a | a02b837a518a |

**No code change.** `git diff 6a4c9af 84b3612 -- lhpc tests testlab .github` is empty (0 bytes).
`git diff 6a4c9af 84b3612 --stat` shows `docs/test-matrix.md | 2 +-` only, and
`git diff a1c658f dbcc85e --stat` shows the same, which is the whole branch delta before this report.

**Checks.**
- `python3 -m pytest -q tests/repo`: 403 passed, 5 skipped.
- `ruff check lhpc testlab`: all checks passed.
- No full-suite run was made and no background job was used.

**Merge-order note:** unchanged from Correction 2. CI `test` stays red on `test_budget[build]` until
F42 lands.

## Correction 4 (rebase onto 0.11.11 + longest quiet on F42)

**Base.** The branch is rebuilt on `release/0.11.11-bundle` at 045724db (the 0.11.11 release head,
with F42's `lhpc/core/progress.py`: `Watch`, `SessionSampler`, `build_limits`; `_run_step` returns
`(rc, reason, unverified)`). The nine first fix commits are cherry-picked in order (no `-x`), every
commit is re-authored (`--reset-author`) as the repository owner. The two plan commits
(0c428be, d61bdec) are not on this branch, as asked; the commit messages still name
`plans/PLAN-F43.md` as their source. The tenth commit (fa22a22) is not picked: it is
re-implemented on F42 (2e15657).

**Conflicts in the picks.**
- 84b3612 (`CHANGELOG.md`): the 0.11.11 section is kept intact; the F43 line goes under a new
  `## 0.11.12` heading at the top (the repository uses no "Unreleased" heading). The commit message
  says so instead of "the last of the 0.11.10 entries".
- 66814fe (`lhpc/core/service_binary_ops.py`): F42 added `import traceback` where the pick adds
  `import time`; both are kept, sorted (`import time` before `import traceback`).

**Patch-ids** (`git show <c> | git patch-id --stable`, first 12 hex digits):

| source | new | patch-id source | patch-id new | equal |
|---|---|---|---|---|
| 0417f81 | 4244114 | 655eb46b57f5 | 655eb46b57f5 | yes |
| d48095f | aed5b7f | 88c6073904e3 | 88c6073904e3 | yes |
| 38464fb | 24bbd24 | b086c5780449 | 92f8151b313b | context only: `-U0` 80bfa75d92d6 = 80bfa75d92d6 |
| 3b2e63f | 8c7f131 | 899dd3cbf3fb | 899dd3cbf3fb | yes |
| ddf3dd3 | cd7de3e | 9812f85dd939 | 9812f85dd939 | yes |
| 84b3612 | 36631a5 | 9574802d6951 | 7fdef16d78b6 | CHANGELOG only: without `CHANGELOG.md` 460bbba3fc2f = 460bbba3fc2f |
| 9632383 | a3019c4 | 9ed7554ec23c | 9ed7554ec23c | yes |
| 66814fe | c1bbc29 | 4d1bbd8a0749 | ec31e4b99b54 | context only: `-U0` 87008bcca4e3 = 87008bcca4e3 |
| ffdb337 | f15bcc2 | 1a57aff0edb4 | 1a57aff0edb4 | yes |

Six are equal as they stand. 38464fb and 66814fe differ only in diff context: the
`service_binary_ops.py` import block of the base has F42's `import traceback`; with zero context
lines (`git show -U0 <c> | git patch-id --stable`) they are equal. 84b3612 differs only in the
CHANGELOG hunk moved under `## 0.11.12`; the rest of the commit
(`git show <c> -- . ':!CHANGELOG.md' | git patch-id --stable`) is equal.

**The re-implemented commit (fa22a22 → 2e15657): every build step's log ends with its longest
quiet period, from F42's Watch.**
- `progress.Watch` keeps the largest `now - last progress` it computes at a sample without
  progress (one line in `check()`, after the progress branch, before the unchanged stall verdict).
  `Watch.longest_quiet()` returns that, or the quiet tail up to now if it is longer. Without a
  sampler nothing is observed after the start, so it is the whole runtime.
  `progress.quiet_line(s)` renders `[progress] longest quiet <s:.1f> s`. The separate
  `lhpc/core/quiet_gap.py` of fa22a22 is gone: no second sampler and no second stall rule; `stall_s`,
  `sample_s` and the ceiling are unchanged.
- CLI `lhpc build` and auto-install: `run_streaming` (fast and controlled path) returns the
  Watch's value as `CommandResult.longest_quiet_s` (new field, default None). `run_job` writes the
  line at the end of the log whenever it runs a step with `stall_s` (that is every lifecycle build
  step), on success, failure and timeout, after the timeout marker; log only, never the tail.
  A runner without the value (fakes, `run()`) gives no line: no evidence, never passing evidence.
- Web launcher: `_run_step` prints the line after every Build step (`stall_s` set), stopped or not,
  and none after a Test step. Its `(rc, reason, unverified)` return is unchanged.
- The slow-build lane's reader (`_QUIET` in `testlab/tests/slowbuild/test_slow_build.py`) is
  unchanged and reads the line.

**Tests (2e15657).** `tests/core/test_longest_quiet.py` (13) drives a `Watch` with the fake clock
and a fixed-sequence sampler, as `tests/core/test_progress.py` does: progress at 15 s, quiet at 30
and 45 s, progress at 60 s, quiet at 75 s, read at 80 s gives 30.0; a quiet tail gives 85.0;
continuous progress gives 7.0 (one partial interval); no sampler gives the whole runtime; the stall
verdicts with the accessor are `[None, None, None, "stalled"]` as before. Then the line in the
lane's pattern; the job log on success, failure and timeout (last line, not in the tail); no line
without `stall_s`; both `run_streaming` paths return the Watch's value; a real `run_job` build log
ends with it; the web launcher prints it after both Build steps and after a stopped Build step, and
not for a Test run. `testlab/tests/unit/test_slow_build_lane.py` gains one test: the lane's build
reader records `quiet_s = 41.5` from a log that `run_job` wrote for a step whose runner reported
41.5.

**Red before** (the nine picks, i.e. 2e15657's `lhpc/` changes reverted, its tests kept):
`tests/core/test_longest_quiet.py`: 12 failed, 1 passed (the passing one is the Test-run negative
case). Causes: `'Watch' object has no attribute 'longest_quiet'`, `module 'lhpc.core.progress' has
no attribute 'quiet_line'`, `CommandResult.__init__() got an unexpected keyword argument
'longest_quiet_s'`. `testlab/tests/unit/test_slow_build_lane.py`: 1 failed (the new reader test),
26 passed. After 2e15657: 13 passed and 27 passed.

**Checks.**
- Touched modules (`tests/core/test_longest_quiet.py`, `test_progress.py`, `test_jobs.py`,
  `test_build_launcher_runtime.py`, `test_bounded_runner.py`, `test_build_timeout.py`,
  `tests/install/test_binary_install.py`, `test_selfupdate_service.py`,
  `test_slow_target_budget.py`, `test_source.py`): 615 passed, 14 skipped, 3 failed. The 3 are the
  archive-extraction cases of `test_binary_install.py`; they need the `zstd` program, which this
  container lacks, and fail the same way on 045724db.
- `testlab/tests/unit/test_slow_build_lane.py`: 27 passed.
- `tests/repo`: 407 passed, 5 skipped, **1 failed**:
  `test_version_consistent.py::test_changelog_leads_with_the_current_version`. It requires the first
  `## X.Y.Z` heading of `CHANGELOG.md` to equal `lhpc.version.__version__` (0.11.11), and the
  requested `## 0.11.12` heading is now first. It turns green with the 0.11.12 version bump, or if
  the F43 line goes back into the 0.11.11 section. This is a decision for the maintainer; no
  version file was changed here.
- `ruff check lhpc testlab` and `ruff check tests --select F,E9`: all checks passed.
- No full-suite run and no background job.

**Merge-order note.** F42 is in the base now, so `test_budget[build]` of
`tests/install/test_slow_target_budget.py` passes here (it was red until F42 landed).

**Adversarial self-review (before the push).**
- Log position on the fast path: the child writes through its own copy of the log fd; `run_job`
  then writes through `log_fh`. That is the same pattern as the existing timeout marker, and
  `test_the_cli_build_log_carries_the_watch_value` runs a real child and checks the last line.
- Web log order: after a stopped step, `step timed out …` (stderr) now precedes the quiet line
  (stdout). Nothing reads the web log's last line; the job detail is taken from `detail[0]`.
  The F42 tests in `test_build_launcher_runtime.py` still pass.
- Stall rule: the only change in `Watch.check()` is the `max()` on the no-progress branch; the
  verdict expression is the same. Covered by
  `test_the_accessor_leaves_the_stall_rule_unchanged` and the unchanged `test_progress.py`.
- A `run_job` caller with `stall_s` other than `lifecycle.build`: none (`grep stall_s=` in `lhpc`).
- Unknown samples count as progress and do not add to the quiet period (F42's bias toward
  "alive"); a sampler-less build reads its whole runtime, which is never less than a stall rule
  could have seen.
- Found: the CHANGELOG heading makes one `tests/repo` test red (above). Not changed, as the heading
  was requested explicitly; flagged for the maintainer.
- Identity: `git log --format='%an %cn%n%B' 045724db..HEAD` shows only the owner as author and
  committer and no co-author or session line.

## Correction 5 (two lane defects from the first slow-build run)

**Trigger.** The slow-build job's first real run on the 0.11.12 candidate (testlab run
37145791526, job 111269336657) was RED: 7 failed, 3 passed, 1 bootstrap skip, in 6:01. Both
causes were in the lane's own code (commit 8c7f131). Both were checked against the code before
anything was changed.

**Defect 1: a checkout faster than the log's resolution aborted the stack.** The adoption log
writes `[git] <what> {s:.1f} s` (`lhpc/core/install.py:2050`), so a checkout under 50 ms reads
`[git] checkout <ref> 0.0 s`. `_adoption` passed that to `_record`. `_record` stored
`seconds = 0.0`, and `stt.entry_errors` rejected the entry with "seconds must be a positive
number". The stack's case then stopped at its first checkout, so no build ran for kiss, chat,
voice, meshcore or reticulum. Every one of those operations then failed the budget case again
with "no row C evidence for …".
- Fix (`testlab/tests/slowbuild/test_slow_build.py`): new constants `LOG_RESOLUTION_S = 0.1` and
  `BELOW_RESOLUTION = "below log resolution"`. In `_record`, when the rounded duration is below
  the floor, the entry gets `seconds = 0.1` and `note = "below log resolution"`. The validator
  needs no change, because 0.1 is a positive number, and it still rejects 0.0 for every op. The
  product's `.1f` format is unchanged. The floor can never give a false PASS: it is never smaller
  than the true duration it stands for.
- Tests (`testlab/tests/unit/test_slow_build_lane.py`):
  `test_a_checkout_below_log_resolution_is_evidence_at_the_floor` feeds an adoption log with
  `[git] clone 12.3 s` and `[git] checkout v1.2.3 0.0 s` through `_adoption`. The result is one
  checkout entry at 0.1 with the note, accepted by `entry_errors`, and the clone entry is
  recorded as well, at 12.3 and without a note.
  `test_the_validator_still_rejects_a_zero` shows that `entry_errors` accepts the floor entry
  and rejects `seconds = 0.0` for checkout, clone and build.

**Defect 2: the self-update helper was refused by the unit-plumbing guard.** `_helper` ran
`lhpc self-update --run-service` with the lab environment. `lab_env` removes `INVOCATION_ID` on
purpose, and `_unit_plumbing_refusal` (`lhpc/adapters/cli/main.py`, guard since 0.11.10) returns
rc 2 when that variable is missing. So the helper failed on every tree, and selfupdate-helper and
selfupdate-pip never got evidence.
- Fix: `_helper` now runs the service entry the way its systemd unit does,
  `env={**env, "INVOCATION_ID": "slow-build-lane"}`, and its docstring says why. Only the helper
  subprocess gets the marker. Every other `lhpc` call of the lane keeps `lab_env`'s environment,
  which has no marker.
- Test: `test_the_helper_runs_past_the_unit_plumbing_guard` runs `_helper` on a stub `lhpc`. The
  stub calls the real `lhpc.adapters.cli.main._unit_plumbing_refusal` and prints the helper's
  `[selfupdate] pip sync 4.2 s` line only when the guard lets it through. The test runs with
  `INVOCATION_ID` removed from the caller's environment and expects the lane to read `4.2`.

**Red before** (the same three tests, with `test_slow_build.py` reset to the pre-correction
version): 3 failed, 27 passed.
- floor test: `AssertionError: ['seconds must be a positive number']`, the same error as in the
  CI log.
- helper test: `the self-update helper failed (rc 2) — not evidence: ERR   --run-service is unit
  plumbing, … refused because the systemd invocation marker (INVOCATION_ID) is absent`, the same
  error as in the CI log.
- validator test: fails only because the constant `BELOW_RESOLUTION` does not exist yet. This
  test guards against an overly wide fix; it is not a red-before proof of a defect.

After the fix: 30 passed.

**Not touched.** `LANE_OPS`, the `L4_INTRODUCING` waiver and `_waived` are unchanged. The local
run below gives no reason to change them.

**The local lane run: what ran and what did not.** The cloud container has no Docker daemon and
no cgroup-v2 `cpu.max` or `memory.max`. Starting a daemon would have needed a background job, so
the throttled container of the CI job could not run here. What ran instead is the lane module
itself, `python -m pytest testlab/tests/slowbuild -v`, with `LHPC_SLOW_BUILD=1` and the
`lhpc_testlab` provider, against the real lane components: real `lhpc install` and `lhpc build`
of every lane stack, real adoption logs, the real self-update from the previous tag, and the real
`calibrate.sh`. One local pytest plugin replaces `_env_problems` with "no problems".
**Nothing this run measured is evidence**: the box is not throttled, it is x86_64 and not
aarch64, and the entries say `cpus=?`.

Environment set-up on this box (local only, nothing committed):
- the apt packages of `.devcontainer/Dockerfile`;
- the agent proxy's CA in the system trust store and in a pip config, so the product's own pip
  steps can reach PyPI;
- `python3` set to 3.12 so that Ubuntu's `python3-libgpiod` and `python3-spidev` are importable
  (this box's default `python3` is 3.11, the lab image's is 3.13);
- a link `python3-libgpiod.list` to `python3-libgpiod:amd64.list` in `/var/lib/dpkg/info`. On
  Ubuntu 24.04 that package is `Multi-Arch: same`, so its receipt carries the architecture. See
  the note below.

Result: **7 passed, 1 skipped, 3 failed** (3:54).
- Passed: env (lifted), stacks kiss, chat, voice, meshcore and reticulum, and selfupdate.
- Skipped: calibrated, with the bootstrap reason (no Zero calibration yet).
- Failed: graywolf, meshtastic and budget.
- Defect 1 in the real run: the real adoption logs contained ten different refs with
  `[git] checkout <ref> 0.0 s`. Each was recorded at 0.1 with the note, and the stacks went on
  to build. The evidence file holds 36 `[[measured]]` entries.
- Defect 2 in the real run: the helper got past the guard, updated a clone of v0.11.10 (the
  previous tag reachable from this branch) to the candidate, and recorded selfupdate-helper.
  v0.11.10 has no pip sync line, so L4 took the `L4_INTRODUCING` path, as it will on the
  0.11.12 candidate (v0.11.11 has no pip sync line either: `git show
  v0.11.11:lhpc/core/service_selfupdate.py` has no match).
- `test_slow_build_stack[graywolf]`: the upstream release check gets HTTP 403 from the GitHub
  API through this container's proxy ("network/API error"), so deb-fetch did not run.
- `test_slow_build_stack[meshtastic]`: this is an x86 box, so there is no aarch64 binary channel
  and no CLI venv.
- `test_slow_build_budget`: exactly 2 failures, `no row C evidence for graywolf deb-fetch` and
  `no row C evidence for meshtastic-cli-venv cli-venv`. These are the two operations the
  environment blocked. Every other "no row C evidence" of the CI run is gone, and every
  "no Zero baseline" is waived as bootstrap.

**Not proven here:** a GREEN lane. That needs the CI job (aarch64, throttled, with GitHub API
access). The graywolf deb-fetch and the Meshtastic CLI venv have not run anywhere in this round.

**Note for the maintainer (outside F43, not verified on the target OS).** The reticulum
requirement checks `check_file = "/var/lib/dpkg/info/python3-libgpiod.list"`. On Ubuntu 24.04
the package is `Multi-Arch: same`, and its receipt is `python3-libgpiod:amd64.list`, so the
check refuses the install there even though the package is installed. Whether Debian trixie or
Raspberry Pi OS name it the same way was not checked here. The lab image and the release-verify
runs pass reticulum.

**Commits.** The two fixes are amended into the lane commit: `git commit --fixup 8c7f1318`, then
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash 045724db`. This report is a new commit on
top. Patch-ids (`git show <c> | git patch-id --stable`, first 12 hex digits):

| before | after | patch-id before | patch-id after | equal |
|---|---|---|---|---|
| 4244114 | 4244114 | 655eb46b57f5 | 655eb46b57f5 | yes |
| aed5b7f | aed5b7f | 88c6073904e3 | 88c6073904e3 | yes |
| 24bbd24 | 24bbd24 | 92f8151b313b | 92f8151b313b | yes |
| 8c7f131 | e6fccb2 | 899dd3cbf3fb | ca7fa07c376c | amended (this correction) |
| cd7de3e | 4a829a7 | 9812f85dd939 | 9812f85dd939 | yes |
| 36631a5 | d41d002 | 7fdef16d78b6 | 7fdef16d78b6 | yes |
| a3019c4 | 6a775d0 | 9ed7554ec23c | 9ed7554ec23c | yes |
| c1bbc29 | c395552 | ec31e4b99b54 | ec31e4b99b54 | yes |
| f15bcc2 | 7474eb8 | 1a57aff0edb4 | 1a57aff0edb4 | yes |
| 2e15657 | eee681c | e09b180f27e1 | e09b180f27e1 | yes |
| ba323bb | b541243 | f9a8ee7603b7 | f9a8ee7603b7 | yes |

`git diff ba323bb b541243` (the head before this report commit) touches only
`testlab/tests/slowbuild/test_slow_build.py` (+11 −2) and
`testlab/tests/unit/test_slow_build_lane.py` (+53).

**Checks.**
- `testlab/tests/unit/test_slow_build_lane.py`: 30 passed on the head, and 29 passed at e6fccb2
  itself. At that commit the module has no quiet-line test yet; it was run from a worktree,
  with `lhpc` imported from the head.
- `tests/repo`: 442 passed, **1 failed**. The failure is
  `test_version_consistent.py::test_changelog_leads_with_the_current_version`, the known
  `## 0.11.12` heading against version 0.11.11 (Correction 4). It fails the same way without
  this correction.
- Touched signatures: none. `_record`, `_helper` and `_adoption` keep their signatures. A grep of
  `tests/` and `testlab/` finds them only in the lane and its unit module; the other `_record`
  matches are unrelated helpers.
- `ruff check lhpc testlab` and `ruff check tests --select F,E9`: all checks passed (after one
  PIE807 fix in the new test: `list` instead of `lambda: []`).
- No full-suite run and no background job.
- Identity: `git log --format='%an %cn%n%B' 045724db..HEAD` shows only the owner as author and
  committer, and no co-author, session or AI-attribution line.

**Adversarial self-review (before the push).**
- The floor applies to every op in `_record`, not only checkout. Clone, checkout and cli-venv
  are read from `.1f` log lines and can read 0.0 for the same reason. The ops timed with
  `time.monotonic()` cannot realistically be under 50 ms. In every case 0.1 is at least the
  true duration, so it can never move a budget toward PASS. The validator is unchanged, and zero
  stays rejected for every op (test).
- The `note` key is not in the format comment of `tests/data/slow-target-builds.toml`. That file
  belongs to commit 4244114, and the comment was left alone so the other patch-ids stay the
  same. `entry_errors` and the budget rule ignore unknown keys, so an entry with `note` is valid
  when it is copied into the baseline. The maintainer may want one comment line there.
- `slow-build-summary.md` prints E with `.0f` (code from before this correction), so a floor
  entry shows `E=0 s`. This is cosmetic only: the evidence file holds 0.1 and the note.
- `INVOCATION_ID` goes only to the helper subprocess. `lab_env` still removes it for every other
  step, so the lane does not hide the guard anywhere else. The value `slow-build-lane` cannot be
  mistaken for a real systemd invocation id in a log.
- The helper unit test calls the real guard function, not the full CLI dispatch. The local lane
  run covers the dispatch: `lhpc self-update --run-service` ran past the guard and updated the
  clone.
- Found and fixed: one ruff PIE807 in the new test.
- Found and not changed: the Multi-Arch receipt name of python3-libgpiod on Ubuntu (the note
  above). It is outside this correction and not verified on the target OS.

## Correction 6 (calibrate.sh's work dir on the Zero)

**Trigger.** The first real row A on a Pi Zero 2 W: `bash testlab/slowbuild/calibrate.sh` died
after 105 s with `OSError: [Errno 28] No space left on device`. Line 16 created the work dir
with `mktemp -d "${TMPDIR:-/tmp}/lhpc-calib.XXXXXX"`; on the Zero `/tmp` is a 208 MB tmpfs and
the io part writes 256 MB. Even where it fits, io on tmpfs times RAM, not the SD card.

**Fix (`testlab/slowbuild/calibrate.sh`).**
- The work dir defaults to `<runtime root>/state/lhpc-calib` when the script is the checkout
  inside an LHPC install (`<root>/src/loraham-pi-control` with `<root>/state` present), else
  `$HOME/.cache/lhpc-calib`. `TMPDIR` is not consulted. `--work-dir DIR` overrides it.
- The script creates the dir (`mkdir -p` of the parent, then `mkdir`) and removes it at exit.
  A dir that already exists is refused ("already exists (an interrupted run?): remove it") and
  never touched, so the script can only remove a dir it created.
- Before any timed part: `stat -f -c %T` of the work dir; tmpfs or ramfs is refused with one
  line naming the dir and the type. Then `df -Pk`: less than the io size + 64 MB free is refused
  with both numbers. Every refusal is exit 1 and removes the created dir.
- `--io-mb N` (default 256, unchanged) sets the io size; `io_mb=<N>` is part of the `workload`
  hash, so a calibration taken at a test size never matches the real workload.

**Docs.** `docs/maintenance.md` (the slow-target build row) names the work dir
`$HOME/loraham-pi-control/state/lhpc-calib` on the SD card, its 256 MB + 64 MB need, and the two
refusals. The calibration row of `docs/test-matrix.md` (row A's recording table) says to run the
script from the installed checkout, so the work dir is `state/lhpc-calib`.

**Tests (`testlab/tests/unit/test_calibrate.py`, new, 6 cases).** A work dir under `/dev/shm`
(tmpfs) is refused with the message and removed; `--io-mb 2**30` on a disk dir is refused with
"… MB free, the io part needs … MB + 64 MB"; a disk dir with `--io-mb 1` and `2` runs, prints the
`cpu= io= mem= workload=` line, removes the dir, and the two hashes differ; an existing dir is
refused and its content kept; the default dir is `<root>/state/lhpc-calib` in a fake install and
`$HOME/.cache/lhpc-calib` outside one, with `TMPDIR=/dev/shm` set (both via the early free-space
refusal, which names the dir).

**Red before.** The tmpfs case and the existing-dir case against the old script: 2 failed (the
old script ignored its arguments, wrote 256 MB under `/tmp` and exited 0 with
`cpu=7.4 io=28.5 mem=4.0 workload=…`). After the fix: 6 passed (≈22 s), on the head and on the
amended commit itself (worktree).

**Commits.** Amended into the calibrate.sh commit: `git commit --fixup 7474eb84`, then
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash 045724db`. This report is a new commit on
top. Patch-ids (`git show <c> | git patch-id --stable`, first 12 hex digits):

| before | after | patch-id before | patch-id after | equal |
|---|---|---|---|---|
| 4244114 | 4244114 | 655eb46b57f5 | 655eb46b57f5 | yes |
| aed5b7f | aed5b7f | 88c6073904e3 | 88c6073904e3 | yes |
| 24bbd24 | 24bbd24 | 92f8151b313b | 92f8151b313b | yes |
| e6fccb2 | e6fccb2 | ca7fa07c376c | ca7fa07c376c | yes |
| 4a829a7 | 4a829a7 | 9812f85dd939 | 9812f85dd939 | yes |
| d41d002 | d41d002 | 7fdef16d78b6 | 7fdef16d78b6 | yes |
| 6a775d0 | 6a775d0 | 9ed7554ec23c | 9ed7554ec23c | yes |
| c395552 | c395552 | ec31e4b99b54 | ec31e4b99b54 | yes |
| 7474eb8 | d53c2b4 | 1a57aff0edb4 | 0f8760b50b61 | amended (this correction) |
| eee681c | 7de65df | e09b180f27e1 | e09b180f27e1 | yes |
| b541243 | 5cdf75a | f9a8ee7603b7 | f9a8ee7603b7 | yes |
| ee24bda | 47cbfe6 | 3047a7355077 | 3047a7355077 | yes |

`git diff ee24bda 47cbfe6` touches only `testlab/slowbuild/calibrate.sh` (+60 −8),
`testlab/tests/unit/test_calibrate.py` (+96), `docs/maintenance.md` (+4 −1) and
`docs/test-matrix.md` (+1 −1).

**Checks.**
- `testlab/tests/unit/test_calibrate.py`: 6 passed.
- `tests/repo` + `testlab/tests/unit/test_slow_build_lane.py` + the new module: 443 passed,
  5 skipped, **1 failed**: `test_version_consistent.py::test_changelog_leads_with_the_current_version`,
  the known `## 0.11.12` heading against version 0.11.11 (Correction 4); it fails the same way
  without this correction.
- `ruff check lhpc testlab`: all checks passed. `bash -n calibrate.sh`: ok. The repository does
  not use shellcheck (no config, no CI step), and it is not installed here.
- No full-suite run and no background job. (One pytest run of the red-before check against the
  old script ran past the foreground limit and was moved to the background by the tool; it was
  stopped, and the red-before was re-run in the foreground on the two fast cases above.)
- Identity: `git log --format='%an %cn%n%B' 045724db..HEAD` shows only the owner as author and
  committer, and no co-author, session or AI-attribution line.

**Live proof needed (not run here, not claimed).** Row A on the Zero with the amended script, and
a row C run with it.

**Adversarial self-review (before the push).**
- Row C moves: the lane calls the script without arguments from `/tmp/repo`, which is not an
  install, so its io part now runs in `$HOME/.cache/lhpc-calib` on the container's overlay file
  system instead of the `/tmp` Docker volume. Both are on the runner's disk; overlay adds
  copy-up/metadata cost, so if anything the container reads slower, which is the conservative
  side for "the container must be at least as slow as the Zero". The lane was not changed (it
  belongs to e6fccb2, whose patch-id is kept). The maintainer may prefer the lane to pass
  `--work-dir /tmp/lhpc-calib` to keep the old volume.
- The workload hash changed for the default run (the script changed, and `io_mb=256` is now
  hashed). No `[[calibration]]` entry is recorded yet, so nothing is orphaned.
- Free space is checked, inodes are not: the io part needs 65 536 inodes. An ext4 SD card has
  far more; a nearly full one could still fail with ENOSPC on inodes. Not handled, noted.
- The 64 MB margin covers directory blocks for 65 536 entries and the cpu part's objects; it
  is not derived from a measurement.
- A run killed with SIGKILL (or power loss) leaves the work dir; the next run refuses it and
  says to remove it. Deliberate: the script never removes a dir it did not create.
- The install detection is by location (`<root>/src/loraham-pi-control` with `<root>/state`),
  not by `LHPC_RUNTIME_ROOT`; on the Zero the documented recipe runs the installed checkout, so
  the two agree. A dev checkout with a separate root falls back to `$HOME/.cache`, still disk.
- The tmpfs test skips where `/dev/shm` is not tmpfs, and the disk tests skip where pytest's tmp
  dir is tmpfs; on such a box the module proves less. The CI runners and this box have both.
- The disk test runs the whole script twice (cpu build and the 700 MB mem part), ≈20 s and
  700 MB of RAM, in the testlab unit job. Not reduced: `--io-mb` is the only knob asked for.
- Found and fixed before the commit: three lines over 100 columns in the script.

## Correction 7 (the second real slow-build run and row A part 1)

**Trigger.** The slow-build job's second real run (testlab 37148794385, job 111278122002, on the
candidate 042716fe) was 8 passed / 2 failed / 1 bootstrap skip, and row A part 1 (30
`[[measured]]` + 1 `[[calibration]]` entries from a Pi Zero 2 W, the data commit) made three
more gaps visible. Base: `f43/baseline-rowA` = 0b90fee. Every fix is amended into the commit it
belongs to; the data commit stays last and unchanged.

### GAP 1 — the `lhpc` label of a candidate (amended into the budget-test commit)

Row A measured a candidate, so its entries say `lhpc = "release/0.11.12-bundle (3d0943a1)"`. The
old rule (`minor(entry) is None` → "lhpc must name the release") matched only a leading
`vX.Y.`, so 29 of the 30 entries were malformed and `test_every_baseline_entry_is_well_formed`
was red on the data commit.

- `lhpc/core/slow_target.py`: `_LHPC_RE` accepts a tag `vX.Y.Z (sha)` or a candidate ref
  `release/<name> (sha)`, the short SHA (7–40 hex) mandatory in both. A self-update entry may name
  the update it timed, `<from tag> -> <to tag or ref> (sha)`: the data commit's
  `selfupdate-helper` entry is `"v0.11.10 -> v0.11.11 (35923b80)"`, which the old prefix rule
  accepted, and the data stays as it is, so this third form is accepted too (a deviation from the
  two forms asked for; see the self-review). `minor()` reads the (target) tag's X.Y, or the first
  X.Y.Z inside a candidate ref's name (`release/0.11.12-bundle` → (0, 11)); a ref without one has
  no minor, so its entries are only ever FRESH by key, never carried.
- `tests/data/slow-target-builds.toml` (the schema commit): the header documents the forms.
- Test `test_the_lhpc_label_takes_a_tag_or_a_candidate_ref`: five well-formed labels, nine
  rejected ones (no SHA, a non-hex SHA, `release/` alone, `main (...)`, `v0.12 (...)`, empty, an
  update without SHA or from a non-tag), the minor of each form.

### Coverage: the documented waiver and exclusions are GREEN (same commit)

- `slow_target.PIP_SYNC_SINCE = "0.12.0"` (the first release whose helper prints
  `[selfupdate] pip sync`; it was "0.11.12" until the 0.11.12 patch was folded into 0.12.0 — then the
  introducing release is 0.12.0, and `test_the_introducing_release_is_a_changelog_release` caught the stale value on
  the integration tree; fixed up into the budget-test commit) and `slow_target.waiver(component, op, version)`: L4
  (`lhpc-selfupdate selfupdate-pip`) needs no measurement on a tree up to and including that
  release, because the update to it runs the previous release's helper, on the Zero and in the
  lane alike. Excluded components are still left out by `required()`.
- `_check_coverage` lists only the genuinely unmeasured pairs, prints each waiver by name
  (`waived: lhpc-selfupdate selfupdate-pip (L4): no evidence up to the introducing release 0.11.12
  — measured from the next release on`) and returns the waivers.
- Tests: the waiver holds at 0.11.11 and 0.11.12 and not at 0.11.13 or 1.0.0; an excluded
  component is never required and a measured entry of one is harmless; only `b clone` is listed
  when `a build` is measured and L4 is waived; `PIP_SYNC_SINCE` is a CHANGELOG release section
  once the tree's version reaches it.
- On this tree `test_coverage` is still RED, by name, for exactly the 16 pairs row A part 2 is
  measuring (loraham-daemon, radiolib, loraham-chat, loraham-voice-cli, loraham-kiss-tnc: build +
  clone + checkout each; graywolf build). It was 17 before; the 17th was L4. Simulated: adding
  those 16 entries makes coverage GREEN with the one L4 waiver printed. The data is the handler's.
- The lane's budget case (amended into the lane commit) now applies the same waiver past
  bootstrap: when the previous tag's helper lacks the line AND `stt.waiver` holds for this
  version, the L4 pair's missing evidence and missing Zero baseline are dropped, and the job
  summary still says `**NO EVIDENCE** lhpc-selfupdate selfupdate-pip (L4) …`. Before, the waiver
  held only in bootstrap, so with row A in the baseline the 0.11.12 lane could never be green on a
  pair nobody can measure. A previous tag without the line on a version past `PIP_SYNC_SINCE` is
  not waived (a lost line is a defect). `docs/maintenance.md` says so.

### GAP 2 — the helper on a runtime with canonical units (amended into the lane commit)

The helper applied the update, then exited 1: "the managed systemd units could NOT be refreshed —
units not canonical: lhpc-boot-restore.service: missing; …". The lane's runtime had no units at
all; a box has the full set, written by install.sh.

**What I did: the faithful fix, no skip.** `test_slow_build_selfupdate` now lays the runtime out
as a box and installs the units the way install.sh does:

- the checkout at `<root>/src/loraham-pi-control`, the venv at `<root>/venv/lhpc` (the lab root
  is initialised first; its `src/` is empty);
- `_install_units(python, root, env)`: each unit of the installed release's
  `updater_units.ALL_UNITS`, rendered by that release's own
  `python -m lhpc.core.updater_units render <kind> <root> <checkout> <venv>` (install.sh's
  `render_unit`) into `$HOME/.config/systemd/user`;
- `_box_env(root, home)`: the lab env with its own `$HOME`, so the lane never writes the
  container user's or a developer's `$HOME/.config/systemd/user`; `XDG_CACHE_HOME` stays the user's
  (the pip cache).

`systemctl --user` is not needed and not run: the helper is sandboxed and never calls it; after
the update it only VERIFIES the unit files (`updater_units verify-set`, file reads, in a
subprocess of the box's venv python). The unit refresh is not stubbed.

The box layout made the helper's controller-identity check apply (before, the checkout outside
the root read as "not self-hosted", so it was skipped). It demands that `origin` is the approved
canonical remote; the lane sets `origin` to the manifest's controller remote and serves it from
the local candidate remote with git's own `url.<local>.insteadOf <canonical>` in that checkout
only. The identity check itself runs unchanged.

- Tests: `test_the_helper_runtime_has_the_units_its_verification_requires` (the helper's own
  `verify-set` reports `lhpc-boot-restore.service: missing` on the bare runtime, as in the run, and
  `ok` after `_install_units`; the units carry the box's `<root>/venv/lhpc/bin/lhpc`);
  `test_the_lane_never_writes_the_real_home_units`.
- **Local end-to-end run of the lane's own `test_slow_build_selfupdate`** (working tree, not
  throttled; `_env_problems` patched out; a local, unpushed tag v0.11.11 at 045724db, deleted
  afterwards): the helper exited 0 and produced its evidence: `selfupdate-helper` 3.6 s, key
  `deps:bf671c36…`, and `lhpc-selfupdate selfupdate-pip (L4): no evidence on the introducing
  release … (v0.11.11 has no pip sync line)`. One local-only workaround: this sandbox reaches PyPI
  only through a proxy, and the product runner's fixed environment drops the proxy variables, so
  the driver wrote a pip config into the box `$HOME`. The hosted runner needs no proxy.

### GAP 3 — the throttled container was faster than the Zero (amended into the job commit)

Row A's calibration (Zero): cpu 99.7 s, io 489.8 s, mem 110.1 s. Row C at `--cpus 0.5
--memory 416m`, no disk throttle: cpu 57.8, io 31.4, mem 12.2. The claim "slowed down below a Pi
Zero 2 W" was false on every axis.

**The throttle, as named constants in the job's `env`, each with its rationale:**

| constant | value | why |
|---|---|---|
| `SLOW_CPUS` | 0.25 | the cpu part is CPU-time bound: 0.5 × 57.8 / 99.7 = 0.29 CPUs is the Zero; 0.25 leaves ~15 % |
| `SLOW_MEM` / `SLOW_SWAP` | 416m / 1184m | unchanged: what a Zero leaves free; the rest of the 700 MB mem part swaps |
| `SLOW_WRITE_IOPS` | 120 | the Zero syncs ~134 files/s (65536 / 489.8 s); on a journaled ext4 a synced 4 KiB file costs ~1.2 writes charged to the container (measured), so 120 IOPS ≈ 100 files/s |
| `SLOW_READ_IOPS` | 1200 | reads pace the mem part's swap-ins (the Zero swaps into zram): measured mem 608 s at 300, 186 s at 1000, 97 s at 2000; 1200 keeps it above 110 s |

- A new step "Resolve the disks to throttle" finds every whole disk behind Docker's storage
  (`docker info -f '{{.DockerRootDir}}'`: the container's root and its `/tmp` volume) and behind
  each active swap area (`/proc/swaps`), maps a partition to its disk (`io.max` takes whole
  disks) and passes `--device-write-iops`/`--device-read-iops` for each. No disk resolved fails
  the step. `docker run` takes `--cpus/--memory/--memory-swap` from the constants and hands all of
  them to the lane.
- The lane's `test_slow_build_env` now also proves the disk throttle: `_io_problems` reads the
  cgroup's `io.max` and fails unless one disk has `wiops <= SLOW_WRITE_IOPS` and `riops <=
  SLOW_READ_IOPS` (missing variable, unreadable file, `max` or a looser value all fail). The
  evidence's `host` names cpus, mem, wiops and riops.
- The calibration check is unchanged: it compares against the baseline's `[[calibration]]` entry
  and FAILS when any axis is faster.
- `CHANGELOG.md`, `docs/testlab.md`: "throttled to a Pi Zero 2 W's CPU, SD card and memory … and
  each run proves it is no faster than a real Zero on a fixed workload". The claim holds only
  through the calibration gate, which turns the release check red when it does not.
- Tests (`tests/repo/test_slow_build_gate.py`): every axis is a named constant, applied by
  `docker run` and handed to the lane; `SLOW_CPUS <= 0.5 × 57.8 / 99.7` and
  `SLOW_WRITE_IOPS <= 134`. Lane unit tests: the `io.max` proof, five cases, and the named
  variables.

**What ran locally (Docker, measured for this correction; x86, 4 cores, cgroup v1, not the arm runner).**
`calibrate.sh` in a `gcc:14` container (same script, same workload hash), work dir and swap file
on a journaled ext4 loop disk, throttled as the job throttles:

| run | cpu | io | mem |
|---|---|---|---|
| `--cpus 0.5 --memory 416m`, no disk throttle (the old job) | 83.3 | 25.2 | 8.1 |
| 0.25 / 416m / 120 w / 300 r | 158.0 | 614.6 | 608.3 |
| mem part alone, 0.25 / 416m / 120 w / 2000 r | — | — | 97.1 |
| mem part alone, 0.25 / 416m / 120 w / 1000 r | — | — | 185.7 |
| **0.25 / 416m / 120 w / 1200 r (the job's values)** | **163.0** | **614.6** (carried over from the 300-IOPS run; the first lane run measures it) | **156.1** |
| the Zero (row A) | 99.7 | 489.8 | 110.1 |

This host's CPU is slower per quota than the runner's (83.3 vs 57.8 at 0.5), so on the runner
cpu should land near 163 × 57.8 / 83.3 ≈ 113 s, still above 99.7. An unjournaled ext4 (this
host's root) charges ~4 writes per synced file (2048 files: 68 s at 120 IOPS); a journaled one
~1.2 (20.5 s). The runner's numbers are not known here.

**What the lane must prove (not run here, not claimed).** The first slow-build run on the
amended job: `test_slow_build_env` with the IOPS limits in force, `test_slow_build_calibrated`
at or above the Zero on all three axes on the runner, and the lane's total time (the last run
took 6 min; at 0.25 CPU and SD-card IOPS expect several times that, inside the 330 min limit).
If an axis stays faster, tighten that constant; if the runner cannot be throttled enough on an
axis, the CHANGELOG/doc sentence must say so with the measured factor.

### Found while measuring: the Zero calibrated a different workload (for the handler)

The Zero's `[[calibration]]` is for `workload = sha256:ae7c6013…`: the candidate's
`calibrate.sh` (042716fe/3d0943a1 still carries the pre-Correction-6 script with
`mktemp -d "${TMPDIR:-/tmp}/…"`). This branch's script (Correction 6: disk work dir, `--io-mb`,
`io_mb` hashed) is `workload = sha256:17b2f715…`. `calibration_failures` matches by workload, so
on this branch the lane's calibration case FAILS "uncalibrated: run the Zero row (no zero2w
[[calibration]] for sha256:17b2f715…)" until the Zero runs THIS script (from the installed
checkout, `bash testlab/slowbuild/calibrate.sh`) and its line is committed. Correction 6's
self-review said the hash change orphaned nothing; the Zero has since recorded the old one. The
two scripts do the same work (same make, same 256 MB of synced 4 KiB files, same 700 MB touch),
so the Zero's 99.7/489.8/110.1 are the right yardstick for the tuning above, but the gate must
compare against a calibration of the same bytes. I did not change the hash rule or the data.

**Consequence, exactly.** The job's JUnit gate requires `test_slow_build_calibrated` PASSED, so every
slow-build run (the release check) is RED while the tree under test has no `zero2w` calibration of
this script. **Resolved since:** the Zero ran this script (workload `sha256:17b2f715…`, script blob
`b4ff9d6f…`, work dir on disk): cpu 98.7, io 492.1, mem 103.2. Its `[[calibration]]` is the third data
commit on `f43/baseline-rowA` (4638cdd8), not on this branch (whose baseline carries only the
`ae7c6013…` calibration); it is stacked with this branch in the integration delta, so this branch's
own lane run would still read "uncalibrated". `calibration_failures` takes the
`zero2w` entries whose `workload` equals the script's hash, newest by `date` — never by position — so
the older entry (`ae7c6013…`) no longer applies to this script.

### Commits and patch-ids

`git commit --fixup <sha>` per target, then
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash 045724db…` (repeated for the follow-up
fixes listed at the end), and this report as a new commit before the data commit. `git show <c> | git patch-id
--stable`, first 12 hex digits:

| before | after | patch-id before | patch-id after | equal |
|---|---|---|---|---|
| 4244114 | e22afab | 655eb46b57f5 | 1b929129d3e7 | amended (header: label forms, host example) |
| aed5b7f | d61c7ed | 88c6073904e3 | a1f1d2e26cea | amended (GAP 1, coverage) |
| 24bbd24 | ab4a568 | 92f8151b313b | 92f8151b313b | yes |
| e6fccb2 | e16bfd9 | ca7fa07c376c | 05882c04af50 | amended (GAP 2, L4 waiver, io proof) |
| 4a829a7 | 69157c6 | 9812f85dd939 | 80739b76a63b | amended (GAP 3) |
| d41d002 | eeb7de4 | 7fdef16d78b6 | b8bae5ba7713 | amended (docs, CHANGELOG) |
| 6a775d0 | 180f592 | 9ed7554ec23c | 9ed7554ec23c | yes |
| c395552 | 30b43b4 | ec31e4b99b54 | ec31e4b99b54 | yes |
| d53c2b4 | cac0ab1 | 0f8760b50b61 | 0f8760b50b61 | yes |
| 7de65df | b3e8c31 | e09b180f27e1 | e09b180f27e1 | yes |
| 5cdf75a | 481a687 | f9a8ee7603b7 | f9a8ee7603b7 | yes |
| 47cbfe6 | 59b79b7 | 3047a7355077 | 3047a7355077 | yes |
| 8b46fb8 | 76dee07 | 9c1cd38debd8 | 9c1cd38debd8 | yes |
| 0b90fee | (last, after this report) | 45fcff836baf | 45fcff836baf | yes — the data commit, unchanged |

`git diff 0b90fee <head minus this report>` touches only the ten files of the five amendments
(+401 −49).

### Red before, green after

The final test files run against the pre-correction code (worktree at 0b90fee with only the
three test modules replaced): **20 failed**, 63 passed —
`test_every_baseline_entry_is_well_formed`, the label test, the 6 coverage/waiver tests (no
`stt.waiver`), the 2 lane L4-waiver tests, the 2 helper-unit tests, the 7 io-proof tests, the 2
throttle tests of the job. After: 82 passed, 1 failed (`test_coverage`, the 16 pairs above).
Each amended commit checked out on its own (worktree): its slow-target modules pass (10 / 42+1
bootstrap skip / 83+1 / 91+1 / 91+1).

### Checks

- `tests/install/test_slow_target_budget.py`, `testlab/tests/unit/test_slow_build_lane.py`,
  `testlab/tests/unit/test_calibrate.py`, `testlab/tests/slowbuild` (opt-in, skipped), `tests/repo`:
  489 passed, 16 skipped, **2 failed**: `test_coverage` (the 16 unmeasured pairs, until row A part
  2) and `test_version_consistent.py::test_changelog_leads_with_the_current_version` (the known
  `## 0.11.12` heading against 0.11.11, Correction 4; red without this correction too).
- `ruff check lhpc testlab`: all checks passed. Workflow lint: `tests/repo/test_workflow_shell.py`
  (every run block parses with `bash -n`), PyYAML loads `testlab.yml` (jobs `testlab`,
  `release-verify`, `slow-build`; the new step and env present); the disk-resolution function run
  by hand on this host resolves Docker's storage and the swap file to their disk.
- No full-suite run. Background: the long local Docker calibrations ran past the tool's 10-minute
  foreground limit and had to run in the background (measurements, not test runs); every test
  run was in the foreground.
- Identity: `git log --format='%an %cn%n%B' 045724db..HEAD`: only the owner, no co-author,
  session or AI-attribution line.

### Adversarial self-review (before the push)

- **The L4 waiver past bootstrap reverses an earlier design** ("only the bootstrap state waives
  it"). It is needed: row A was taken on the introducing release, so neither row can ever measure
  L4 there, and without it the 0.11.12 lane is red for good. It is bounded twice (the previous
  tag lacks the line AND the version is not past `PIP_SYNC_SINCE`) and stays visible in the job
  summary. A reviewer may prefer it to stay red and be waived by hand.
- `PIP_SYNC_SINCE` is a release number in code. If 0.11.12 is renumbered, the guard test catches
  it only once the tree's version reaches the constant; before that it cannot tell.
- The third label form (`<from> -> <to> (sha)`) goes beyond the two forms asked for; without it
  the unchanged data commit's self-update entry is malformed.
- A candidate ref without an X.Y.Z (`release/bundle`) is well-formed but has no minor: its
  entries never carry to a moved key. Deliberate; noted in `minor()`.
- GAP 2's `insteadOf` is a harness redirect of the canonical remote to a local one, in the lane's
  throwaway checkout only; the identity check, the update, the pip sync and the unit verification
  all run unchanged. The box `$HOME` holds no git or pip config of the lab user: on the runner
  neither is needed; locally pip needed a proxy config (driver only, not in the lane).
- GAP 3's numbers come from an x86 VM with cgroup v1, not the arm runner with cgroup v2. Too
  fast is caught by the calibration gate. Too slow is not caught; it inflates row C, which can
  only make the budget rule stricter (a false "limit < 2 x measured" or "row C near the budget"),
  never a false pass. The mem part's swap goes to a throttled disk, where the Zero uses zram: a
  different mechanism tuned to the same time, so swap-heavy builds in row C may be slower than on
  the Zero.
- `_io_problems` is satisfied by ONE throttled disk; with Docker's storage and swap on two disks,
  it does not prove both. The job throttles every disk it resolved and prints them.
- A swap area on zram or on a disk `findmnt` cannot map is skipped by the resolution step, not
  failed; then swap is unthrottled (the mem part comes out faster, and the gate turns red).
- The lane will take much longer than 6 min; 330 min should hold, not measured.
- The calibration hash mismatch above is a fourth gap that only data can close; it is handed to
  the handler, not hidden.
- Found and fixed before the commit: the CHANGELOG guard failed on the commits before the
  CHANGELOG section existed (now: a section once the version reaches the release); the waivers
  were printed only on success (now before the assertion, so a red coverage run names them
  too); two ruff findings in the new tests; the baseline header's `host` example (now names
  cpus, mem and both IOPS) and a `~`-style home path in a lane docstring (now `$HOME`).
