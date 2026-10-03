# CODE REPORT — fix group F39/F40 (MeshCore build timeouts)

Base `origin/main` e5187f70 (v0.11.10). Implements `code-review/PLAN-F39-F40.md` with
`PLAN-F39-F40-DELTA.md` (DELTA 1 + 2): commit order B → A → C.

Environment: Python 3.11 venv with `pip install -e .[dev]` plus `pytest-xdist` (for `-n 8`).
"Red before" means: the new/extended test run with the commit's test changes but the parent
commit's `lhpc/` (`git stash push lhpc`), so the test fails against the unfixed code.

## 1. Commits

### 1 · a8b6f74 `F39: meshcore-cli declares build_timeout 1800 and a build marker` (B)
Files: `lhpc/data/manifest.example.toml` (+4/−1), `tests/core/test_build_timeout.py` (+14).
- meshcore-cli: `build_marker = ".venv/.lhpc-build-complete"`, `build_timeout = 1800.0`, plus a
  one-line comment.
- Q5 / DELTA 3: the stale meshcore-node comment "overran the 600 s default" now says 900 s.

| test | red before | command → result |
|---|---|---|
| `tests/core/test_build_timeout.py::test_meshcore_cli_declares_build_budget_and_marker` | **yes**: `AssertionError: assert 0.0 >= 1800.0` | `python -m pytest -q -p no:cacheprovider tests/core/test_build_timeout.py::test_meshcore_cli_declares_build_budget_and_marker` → base: `1 failed`; with B: `1 passed` |

Green on its own: `python -m pytest -q -p no:cacheprovider -n 8 tests/core/test_build_timeout.py tests/repo` →
`415 passed, 5 skipped`. `tests/stacks tests/install` → `93 failed, 2260 passed, 15 skipped`, which is
**identical on e5187f70** (same run on the base: `93 failed, 2260 passed, 15 skipped`). The 93 are
environmental: 89 in `test_bootstrap_deps` (root/apt sandbox), 3 in `test_binary_install::test_extract_rejects_hostile_archive`,
and 1 in `test_binary_channel::test_doctor_is_quiet_for_a_healthy_binary_install`. The plan's 94th (released tags) is
skipped here, not failed. `ruff check lhpc testlab` and `ruff check tests --select F,E9`: `All checks passed!`

### 2 · b802795 `F40: web Build/Test honours the manifest build_timeout/test_timeout` (A)
Files: `lhpc/core/build_launcher_runtime.py`, `lhpc/core/commands.py`, `lhpc/core/service_lifecycle_ops.py`,
`docs/maintenance.md`, `tests/core/test_build_launcher_runtime.py`, `tests/web/test_webjob.py`.

| test | red before | result before → after |
|---|---|---|
| `tests/web/test_webjob.py::test_spawn_web_job_launcher_carries_the_manifest_timeout[build-meshcom-meshcom-qemu-28800.0]` | **yes** (step_timeout `None`) | FAILED → passed |
| `…::test_spawn_web_job_launcher_carries_the_manifest_timeout[test-meshcore-meshcore-node-900.0]` | **yes** | FAILED → passed |
| `tests/core/test_build_launcher_runtime.py::test_run_uses_the_spec_step_timeout` | **yes** (the spec is ignored, `sleep 5` exits 0, so no SystemExit) | FAILED → passed |
| `…::test_env_step_timeout_overrides_the_spec` | **preservation** | passed → passed |
| `…::test_run_nonpositive_spec_timeout_fails_safe[0]`, `[-5]`, `[nan]`, `[inf]`, `[-inf]` | **yes**, all 5 (the step ran) | FAILED → passed |
| `…::test_env_step_timeout_rejects_inf[inf]` | **yes** (`t > 0` let `inf` through) | FAILED → passed |
| `…::test_env_step_timeout_rejects_inf[-inf]` | **preservation** (see Deviations 2) | passed → passed |
| `…::test_env_step_timeout_rejects_nan` | **preservation** (DELTA 2) | passed → passed |

Red-before command (parent `lhpc/` stashed):
`python -m pytest -q -p no:cacheprovider -n 8 tests/web/test_webjob.py::test_spawn_web_job_launcher_carries_the_manifest_timeout tests/core/test_build_launcher_runtime.py -k "spec_timeout or spec_step_timeout or overrides_the_spec or rejects_inf or rejects_nan or carries_the_manifest" -rA`
→ `9 failed, 3 passed` (the 3 passes are exactly the three preservation cases above). After A: the same tests all
pass. Green on its own: `python -m pytest -q -p no:cacheprovider -n 8 tests/core tests/web tests/repo` →
`3156 passed, 11 skipped`. Ruff: both clean.

### 3 · 52c46da `F40: a timed-out web job records "timed out after Ns"` (C)
Files: `lhpc/core/build_launcher_runtime.py`, `tests/web/test_webjob.py`.

| test | red before | command → result |
|---|---|---|
| `tests/web/test_webjob.py::test_proven_terminated_timeout_step_records_failed` (extended) | **yes**: `AssertionError: assert 'timed out after' in 'step failed: sleep 5'` | `python -m pytest -q -p no:cacheprovider tests/web/test_webjob.py::test_proven_terminated_timeout_step_records_failed` → parent: `1 failed`; with C: `1 passed` |

Green on its own: `tests/core tests/web tests/repo` → `3156 passed, 11 skipped`. Ruff: both clean.
The CHANGELOG lines were not committed (see Deviations 1).

## 2. SELF-AUDIT PROOF

### B — meshcore-cli budget and marker (F39)
- (a) **Guarantee.** meshcore-cli builds get 1800 s per step on every path. Only a build whose three steps
  all passed reads "built".
- (b) **Enforced by.** `manifest.example.toml` meshcore-cli: `build_timeout = 1800.0` and
  `build_marker = ".venv/.lhpc-build-complete"`.
  - The CLI path reads it via `lifecycle.py:369` (`comp.build_timeout or self.BUILD_TIMEOUT_S`).
  - The web path reads it through A's `_spawn_build`.
  - `is_built` (`service_lifecycle_ops.py` `is_built`, marker branch) now takes precedence over `bin`.
- (c) **What else it could break, and the check.**
  - Frozen-manifest, pin and size tests: `tests/repo` 415 passed; `tests/install` + `tests/stacks` show the same
    93 environmental failures as the base (counts identical).
  - Anything faking meshcore-cli "built" via `meshcli`: `grep -rn "meshcli\|meshcore-cli" tests | grep -i built`
    finds only the new test.
  - `testlab/tests/release/test_release_verify.py::test_release_meshcore_cli` runs a real install and build, which
    writes the marker. It cannot run here (`lhpc_testlab` is not installed); it runs on the release box.
  - Binary channel: none for MeshCore. Upgrade cost: one `lhpc build meshcore-cli` (Deviations 1, CHANGELOG line 2).
  - A failed rebuild after a successful one clears the marker. This is the existing generic contract:
    `tests/core/test_build_timeout.py::test_rebuild_removes_stale_marker_before_running` and
    `::test_meshcom_failed_rebuild_leaves_no_marker` on the CLI path, and the launcher's stale-marker removal before
    step one on the web path. Both are component-agnostic: they key on `comp.build_marker`.
- (d) **Re-read.** The new test creates only `.venv/bin/meshcli`. I checked that meshcore-cli has no
  `build_inputs`/`asset_inputs`, so the `is_built` False comes from the missing marker and not from a sidecar
  mismatch. It fails before B because `is_built` falls back to `bin`, and the red run showed the first assert
  (`0.0 >= 1800.0`) failing.

### A — web launcher honours the manifest timeouts (F40)
- (a) **Guarantee.** A web Build/Test step gets the same per-step timeout as the CLI path. `LHPC_BUILD_STEP_TIMEOUT_S`
  still overrides when set. A malformed, ≤0 or non-finite value (`nan`, `inf`, `-inf`), from the env or the spec,
  exits 3 and is never unlimited.
- (b) **Enforced by.**
  - `service_lifecycle_ops.py` `_spawn_build`:
    `step_timeout = ((c.build_timeout or life.BUILD_TIMEOUT_S) if op == "build" else (c.test_timeout or life.TEST_TIMEOUT_S))`,
    passed as `render_build_launcher(..., step_timeout=step_timeout)`.
  - `commands.render_build_launcher`: `if step_timeout is not None: spec["step_timeout"] = step_timeout`.
  - `build_launcher_runtime._step_timeout(spec_value)`: env if set, else spec, else 1800, then
    `if not (math.isfinite(t) and t > 0): raise ValueError` → `SystemExit(3)`. It is called as
    `_step_timeout(spec.get("step_timeout"))` in `run()`, before any lock or step.
- (c) **What else it could break, and the check.**
  - Other callers of `render_build_launcher`: `grep -rn "render_build_launcher(" lhpc testlab` finds only
    `_spawn_build`. Test callers without the argument keep 1800 because the key is absent
    (`test_build_launcher_step_timeout_kills_child_group` and the other render tests are green).
  - Env-forced timeout tests (`test_webjob.py::test_unverified_timeout_step_records_unsafe`,
    `::test_proven_terminated_timeout_step_records_failed`, `test_build_launcher_runtime.py::test_run_malformed_timeout_fails_safe`,
    `::test_build_launcher_step_timeout_kills_child_group`): green, because env wins.
  - `_spawn_build` only runs for op build/test (`spawn_fns` at the "ordered job list"; install takes `_spawn_install`).
  - Other components' timeouts: the web cap now equals the CLI cap. This is the Q1 drop the plan accepts, and
    CHANGELOG line 1 states it.
  - Image builds / service_maintenance (`getattr(main, "build_timeout", 900.0)`) do not use this launcher: untouched.
  - `life.BUILD_TIMEOUT_S` vs the plan's `Lifecycle.BUILD_TIMEOUT_S`: `life` is the `Lifecycle` instance, and this
    is the same expression `lifecycle.py:369/1491` uses (`self.…`).
- (d) **Re-read.** The spec is embedded with `repr(spec)` into the generated `.py`. `repr(1800.0)` is a valid
  literal, but a manifest that declared `build_timeout = inf` or `nan` (TOML allows both; `manifest.py:857` does no
  finiteness check) would render `inf`/`nan`, which is a `NameError` when the launcher starts. That fails the job
  (never unlimited), but as a startup failure rather than the clean exit 3. No shipped manifest declares one. The CLI
  path has the same unvalidated input. I left it as is because it is outside the plan (see Deviations 3).

### C — the attempt record says "timed out" (F40)
- (a) **Guarantee.** A proven-terminated timeout's banner says `timed out after <N>s: <argv>` instead of
  `step failed: …`. A step that really exits 124 is not mislabelled. The unproven branch is unchanged.
- (b) **Enforced by.**
  - `_run_step` returns `(rc, False, False)` on a normal exit and `(124, True, not result.ok)` on timeout.
  - `run()`: `elif timed_out: detail[0] = (f"timed out after {step_timeout:.0f}s: " + " ".join(argv))[:200]`.
- (c) **What else it could break, and the check.**
  - `_run_step` callers: `grep -rn "_run_step" lhpc tests testlab` finds only `run()` plus tests that ignore the
    return value (`test_run_step_spawns_child_with_oom_preexec`, `test_oom_preexec_actually_raises_child_score`)
    and A's recorder, which passes the tuple through.
  - Nothing parses `step failed`: `grep -rn "step failed"` in lhpc/tests/testlab finds only the launcher line and
    fixture text `test_webjob.py:160`. `lhpc/adapters/web` has no detail parsing.
  - `test_unverified_timeout_step_records_unsafe` is green, so the unsafe branch is unchanged.
- (d) **Re-read.** The branch order is `if unverified` → `elif timed_out` → `else`, so an unverified timeout still
  records `unsafe` and its own text. `{step_timeout:.0f}` rounds a sub-second test value to `0s`. That is cosmetic
  and only possible through the env override; manifest values are whole seconds.

## 3. Deviations from the plan

1. **CHANGELOG lines not committed.** The plan puts three lines into the "next patch section". The repo has no
   such section between releases (fix commits like aa693d4 leave `CHANGELOG.md` alone), and the brief forbids
   `## Unreleased`. Adding `## 0.11.11` without a version bump turns
   `tests/repo/test_version_consistent.py` red: the first `## X.Y.Z` must equal `__version__` 0.11.10. A version
   bump is outside the plan. So commit 3 carries no CHANGELOG change, and the lines below go in at the release
   bump, verbatim, in the operator's words:
   - A web *Build* or *Test* now gets the same time limit per step as `lhpc build`/`lhpc test`, set per component in
     the manifest. Meshtastic and the MeshCom QEMU firmware no longer stop after 30 minutes when built from the
     web. Components without their own limit now stop after 15 minutes per build step (10 for a host test) on the
     web as well.
   - MeshCore CLI gets the same 30-minute build limit as the rest of MeshCore, and reads *built* only after a
     complete build. After the update, build it once (`lhpc build meshcore-cli`).
   - A timed-out web job now says *timed out after N s* instead of *step failed*.

   Per the review's Q2 note: these lines do not claim that 1800 s fixes the field failure. One pip step exceeded
   1800 s once, and the box gets measured after the release.
2. **Test classification detail (DELTA 2).** `test_env_step_timeout_rejects_inf` is parametrized `inf`/`-inf`.
   Only `[inf]` is red before. `[-inf]` is green before, because `-inf > 0` is already False, so it counts as a
   preservation case. No code change follows from this.
3. **Not a deviation, a residual.** A non-finite *manifest* timeout fails at launcher start rather than with exit 3
   (A's self-audit (d)). It is not fixed here, because the plan specifies validation in the launcher only.
4. `life.BUILD_TIMEOUT_S`/`life.TEST_TIMEOUT_S` instead of the plan's `Lifecycle.…`. These are the same class
   attributes through the instance, and the same expression as `lifecycle.py:369/1491`.

## 4. Summary lines (at 52c46da)
- `python -m pytest -q -p no:cacheprovider -n 8 tests/core tests/web tests/repo` → `3156 passed, 11 skipped in 108.23s`
- `python -m pytest -q -p no:cacheprovider -n 8 tests/core tests/web tests/repo tests/install tests/stacks` →
  `93 failed, 5416 passed, 26 skipped in 129.79s`. All 93 are the environmental base failures listed in commit 1
  (89 `test_bootstrap_deps`, 3 `test_binary_install::test_extract_rejects_hostile_archive`, 1
  `test_binary_channel::test_doctor_is_quiet_for_a_healthy_binary_install`). The same 93 fail on e5187f70.
- `ruff check lhpc testlab` → `All checks passed!`
- `ruff check tests --select F,E9` → `All checks passed!`
