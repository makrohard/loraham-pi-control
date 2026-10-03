# Code report F43 — a slow-target build proof before every release

Plan: `plans/PLAN-F43.md` v3 (review GREEN WITH NOTES). Base `main` e5187f70 (v0.11.10). Branch
`claude/intelligent-cerf-sxidoe`, started from `claude/intelligent-cerf-5ynf7b`.

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
   F42 (`claude/busy-heisenberg-gksfeg`) has not landed. Consequences, all visible: the L1 budget
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
