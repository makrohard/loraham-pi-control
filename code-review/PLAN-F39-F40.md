# PLAN — fix group F39/F40 (MeshCore build timeouts) · options A + B + C

Base `e5187f70` (v0.11.10). Read-only on the code. Source analysis:
`code-review/F39-F40-meshcore-build-timeouts.md` (branch `code-review/brief`). Every file:line below was
re-checked at `e5187f70`. A scratch prototype of A+B+C (not committed) was used to measure the diff size and to
run the red/green tests below. `tests/core` + `tests/web` passed 2754/2754 with it. With B alone, `stacks install
core web repo` showed the same 94 failures as the unmodified base: 89 in `test_bootstrap_deps` (root/apt sandbox),
4 binary-install and 1 released-tags (shallow clone). These come from the environment, not from B.

## 1. Analysis

### 1.1 Corrections to the analysis
| Claim in the analysis | At e5187f70 | Effect on the plan |
|---|---|---|
| "meshcore-node's host test is also capped at 900 s … `lifecycle.py:1491`" | True (`test_timeout = 900.0`, manifest :2227). The **default** is `TEST_TIMEOUT_S = 600.0` (`lifecycle.py:1479`), not 900 | A's test default is 600 |
| Option A "~20 lines, low risk" | ~30 lines (prototype: +36/−12 including the test). The analysis **misses a behaviour change**: the web path drops from a flat 1800 s to the CLI values. Builds without `build_timeout` go to 900 s, `nomadnet`/`lxmd`/`graywolf` (900 declared) go to 900 s, tests without `test_timeout` go to 600 s, and meshcore-webui's test goes to **120 s** (:2628) | Risk row in §2.A; open question Q1 |
| The analysis's regression-test idea 3 (optional): "every component whose `build_steps` run `pip install` declares `build_timeout >= 1800`" | Would go red for `nomadnet` and `lxmd` (900 declared, :3262, :3308), so it widens the group | Dropped (Q3) |
| Manifest comment :2224 "overran the 600 s default" | Stale: the default is 900 (`lifecycle.py:331`) | Not touched (cosmetic, outside the group) |
| Everything else checked (two runners, both message formats, meshcore-cli without timeout/marker, `is_built` falling back to `bin`, launcher ignoring the manifest, `_record()` in `finally`) | Confirmed: `jobs.py:152-153`, `build_launcher_runtime.py:34-44,152,174,300,309,340`, `service_lifecycle_ops.py:3494,4767-4795`, manifest :2728-2738 | — |

### 1.2 F39 — `[TIMED OUT after 900s — job was KILLED; result is INCOMPLETE]`
- Path: only `run_job` writes this, with format `{timeout:.0f}` (`jobs.py:152-153`). The 900 comes from
  `comp.build_timeout or BUILD_TIMEOUT_S` (`lifecycle.py:369`, `:331`) or from meshcore-node's `test_timeout` 900.
- Callers: `Lifecycle.build()` is used by CLI `lhpc build`/`install`, auto-install, HMAC apply, and **web Install**
  (it spawns `python -m lhpc install`, `service_lifecycle_ops.py:3456-3466`).
- Defect: meshcore-cli (manifest :2706-2738) declares neither `build_timeout` nor `build_marker`. Its unpinned
  `pip install .` is the only MeshCore build step on the 900 s default, while its siblings have 1800 (:2226, :2626).
  Without a marker, `is_built` falls back to `.venv/bin/meshcli` existing (`service_lifecycle_ops.py:4788-4795`),
  so a timeout in step 3 (compileall, after pip finished) reads "built".
- What a test sees today: `comp.build_timeout == 0.0` and `comp.build_marker == ""` for meshcore-cli.

### 1.3 F40 — `step timed out after 1800.0s: <argv>`
- Path: only the detached launcher writes this (`build_launcher_runtime.py:152`; `{}` of a float, hence `1800.0`).
  It is spawned by the **web Build/Test** buttons only (`spawn_web_job` → `_spawn_build` →
  `render_build_launcher`, `service_lifecycle_ops.py:3494`; caller `adapters/web/app.py:1666`).
- Defect A: the launcher's step timeout comes only from `LHPC_BUILD_STEP_TIMEOUT_S`, default 1800
  (`build_launcher_runtime.py:38`). `render_build_launcher` (`commands.py:930-993`) carries no timeout, and no unit
  sets the variable (grep of `deploy/`, `lhpc/`, `tools/`: no hits). So a web Build caps meshtastic (21600, :1220)
  and meshcom-qemu (28800, :1943) at 1800 s per step. The CLI honours them (`tests/core/test_build_timeout.py:62-77`).
- Defect C: the attempt record detail for a proven-terminated timeout reads `step failed: <argv>` (`:309`). The
  banner never says "timed out". The unproven branch (`:306`) already does.

### 1.4 Why both messages can appear on the same box
The formats prove the paths: `900s` (`:.0f`) can only come from `run_job`, and `1800.0s` (raw float) only from the
launcher. So the operator used **both** paths: a web **Build** (F40), and a CLI `lhpc build`/`install`, auto-install
or a web **Install** (F39). The findings' channel labels are therefore swapped, as the analysis says. The argv
`.venv/bin/pip install .` exists only in meshcore-cli (:2730; meshcore-node's is `install . pytest pytest-asyncio`,
:2240). So both are most likely one slow `pip install .` of meshcore-cli:
- On the CLI path it hits the 900 s default, because meshcore-cli has no `build_timeout`.
- On the web path it hits the flat 1800 s, and there it runs in parallel with meshcore-node's and webui's pip jobs
  (`service_lifecycle_ops.py:3538-3575`), all niced and on ionice idle.

A step that exceeded 1800 s once will exceed 900 s too. Nothing in the code makes the two paths disagree on the
step itself. The slowness is environmental: ~8 min for the whole stack is documented (`docs/stacks/meshcore.md:162`),
and the e293 auto-install took 14 min 12 s including the meshcore source build (`docs/live-tests/live-test.md:65`).
The likely causes are armv7 source builds, a slow index or 3 parallel pip jobs on 512 MB. So **B alone does not make
the box's build pass**: it removes the 900/1800 inconsistency and gives the honest message. Whether 1800 s is enough
on that box needs the box (Q2).

## 2. The changes

### A — the web launcher honours the manifest timeouts (F40)
- Functions:
  - `commands.render_build_launcher` gets `step_timeout: float | None = None` and puts it into the spec only when
    given.
  - `service_lifecycle_ops._spawn_build` passes `(c.build_timeout or Lifecycle.BUILD_TIMEOUT_S)` for op build and
    `(c.test_timeout or Lifecycle.TEST_TIMEOUT_S)` for op test. These are the same expressions as
    `lifecycle.py:369/1491`.
  - `build_launcher_runtime._step_timeout(spec_value=None)`: the env var if set, else the spec value, else 1800.
    It is called as `_step_timeout(spec.get("step_timeout"))` at `:174`.
- New behaviour: a web Build/Test step gets the same per-step timeout as the CLI path. `LHPC_BUILD_STEP_TIMEOUT_S`
  still overrides when set, and a malformed, ≤0 or NaN value (from env or spec) still exits 3, never unlimited.
- Diff: ~20 lines over 3 files (prototype: launcher +11/−9, commands +4/−1, ops +4/−2).
- Risk and how it is ruled out:
  - (a) Direct callers of `render_build_launcher` without the argument (tests, `test_runtime_fs`,
    `test_structured_exec`) keep 1800, because the key is absent.
  - (b) The tests that force a timeout via the env var (`test_webjob.py:115`, `test_build_launcher_runtime.py:94,367`)
    keep working because env wins. All of them are green in the prototype.
  - (c) Shorter web caps for components without a declared value. These are exactly the values the CLI, auto-install
    and the live tests already run with, e.g. the e293/Pi 5 auto-install including the meshcore build and
    meshcore-webui's 120 s test. So no budget is introduced that the CLI does not already enforce. B lifts
    meshcore-cli, the one MeshCore build on 900. See Q1 for the remaining drop.
  - (d) An env-less service still gets a timeout: the spec always carries a value > 0.

### B — meshcore-cli declares its budget and a completion marker (F39)
- Change: in `lhpc/data/manifest.example.toml`, after `bin = ".venv/bin/meshcli"` (:2738), add
  `build_marker = ".venv/.lhpc-build-complete"` and `build_timeout = 1800.0`, with a one-line comment: same slow
  venv+pip build as its siblings, and the marker gates `is_built`.
- New behaviour: meshcore-cli builds get 1800 s per step on every path, and only a build whose 3 steps all passed
  reads "built".
- Diff: 3 lines.
- Risk and how it is ruled out:
  - (a) Frozen-manifest and pin tests:
    - `test_released_manifests_still_parse` only parses old tags and needs `historical - current` non-empty. Adding
      keys removes none.
    - `test_current_manifest_survives_the_runner_capture_cap`: 187 KB vs the 1 MiB cap.
    - `test_build_steps_reference_pins`, `test_no_pin_literals_in_tests` and `tests/install/test_pin_consistency`
      don't read either key. The prototype showed no new failure in `tests/repo`, `tests/install`, `tests/stacks`,
      `tests/core` or `tests/web`.
    - No test fakes meshcore-cli "built" through `meshcli` (grep).
  - (b) **Upgrade cost:** a box whose meshcore-cli was built before the update has no marker, so after the update it
    reads NOT built and needs one `lhpc build meshcore-cli`. This is the same contract every marker introduction had
    (`is_built` docstring :4776-4778). It goes into the CHANGELOG, and Q4 covers it.
  - (c) MeshCore has no binary channel (`[stack.binary]` only for daemon :38 and meshtastic :1091), so no artifact
    is affected.

### C — the attempt record says "timed out" (F40)
- Functions: `build_launcher_runtime._run_step` returns `(rc, timed_out, unverified)` (2 return lines), and
  `run()`'s single call site (`:300`) writes `detail = "timed out after {step_timeout:.0f}s: <argv>"` when
  `timed_out`, else `"step failed: <argv>"` (`:309`).
- New behaviour: a proven-terminated timeout's banner says `timed out after 1800s: …` instead of `step failed: …`.
- Diff: ~5 lines.
- Risk and how it is ruled out:
  - A flag rather than testing `rc == 124` means a step that really exits 124 is not mislabelled.
  - `_run_step` has no other caller (grep), and no code parses `step failed` (grep; `test_webjob.py:158` only
    writes it as fixture text).
  - The unsafe branch is unchanged.

## 3. Tests (each RED before, GREEN after)
| Change | module::name | Asserts | Why red before |
|---|---|---|---|
| A | `tests/web/test_webjob.py::test_spawn_web_job_launcher_carries_the_manifest_timeout` (parametrized `build/meshcom → 28800.0`, `test/meshcore → 900.0`) | wraps `commands.render_build_launcher` to record `step_timeout` per target; `_fake_spawn` + `_track_or_terminate` "terminated" (as at :171-183) | the kwarg is never passed → `None`. **Run red in the prototype (2 failed), green with A** |
| A | `tests/core/test_build_launcher_runtime.py::test_run_uses_the_spec_step_timeout` | `blr.run(_spec(...steps=[sleep 5], step_timeout=0.3))` with the env unset → `SystemExit` within a few seconds and `_run_step` saw 0.3 (monkeypatched recorder) | the spec key is ignored, so 1800 is used |
| A | same module `::test_env_step_timeout_overrides_the_spec` | env `0.2`, spec `999` → recorder sees 0.2 | — (guards precedence; fails if A inverts it) |
| A | same module `::test_run_nonpositive_spec_timeout_fails_safe` (parametrized `0`, `-5`, `nan`) | env unset → `SystemExit` code 3, no step ran | spec ignored → the step runs (`touch` marker exists) |
| B | `tests/core/test_build_timeout.py::test_meshcore_cli_declares_build_budget_and_marker` | `comp.build_timeout >= 1800.0` and `comp.build_marker == ".venv/.lhpc-build-complete"`; with marker absent and `.venv/bin/meshcli` present → `is_built` False | 0.0 / "" and `is_built` True via `bin` |
| C | `tests/web/test_webjob.py::test_proven_terminated_timeout_step_records_failed` (extend :132) | additionally `"timed out after" in detail` of `_read_raw(...)` | detail is `step failed: sleep 5` |

The `_spec` helper in `test_build_launcher_runtime.py` gets a `step_timeout=None` passthrough (a test-helper line
only). Run `pytest tests/core tests/web tests/repo tests/install tests/stacks -n 8` before each push.

## 4. Docs / CHANGELOG
- `docs/maintenance.md:269-270`: "The per-step build timeout defaults to 900 s; the manifest raises it per
  component (`build_timeout` …)". This is untrue for web Build today and becomes true with A. Add: "— the same on
  the command line and the web Build/Test buttons (host tests: 600 s, `test_timeout`)". This is the one place for
  the fact; `docs/adding-a-stack.md:82` points at the key and stays.
- No doc names `LHPC_BUILD_STEP_TIMEOUT_S` (grep), so nothing to change for it.
- CHANGELOG (next patch section), in the operator's words:
  - "A web *Build* or *Test* now gets the same time limit per step as `lhpc build`/`lhpc test`, set per component in
    the manifest. Meshtastic and the MeshCom QEMU firmware no longer stop after 30 minutes when built from the web.
    Components without their own limit now stop after 15 minutes per build step (10 for a host test) on the web as
    well."
  - "MeshCore CLI gets the same 30-minute build limit as the rest of MeshCore, and reads *built* only after a
    complete build. After the update, build it once (`lhpc build meshcore-cli`)."
  - "A timed-out web job now says *timed out after N s* instead of *step failed*."

## 5. Commit order
Each commit is green on its own; no dependency except that the CHANGELOG/doc line rides the last one.
1. `F40: web Build/Test honours the manifest build_timeout/test_timeout` (A plus its 4 tests and the
   `docs/maintenance.md` sentence).
2. `F39: meshcore-cli declares build_timeout 1800 and a build marker` (B plus its test).
3. `F40: a timed-out web job records "timed out after Ns"` (C plus the extended test plus the 3 CHANGELOG lines).

## 6. Live proof (Pi 5, operator-visible, needed)
1. After the update, the dashboard shows meshcore-cli NOT built (B's upgrade cost is visible). Press web **Build**
   on MeshCore: three jobs. Every log ends cleanly, the meshcore-cli card flips to built, and
   `.venv/.lhpc-build-complete` exists under `src/meshcore-cli`.
2. Timeout honesty without waiting 30 min: run `sudo systemctl edit lhpc-web` with
   `Environment=LHPC_BUILD_STEP_TIMEOUT_S=5`, restart, and Build MeshCore. The banner reads
   `timed out after 5s: .venv/bin/pip install …` (not `step failed`), and the log tail has `step timed out after 5.0s`.
   Then remove the override, restart, and Build again: green. This also proves the env override still wins.
3. Spec value reaches the launcher: during step 1 of a web Build, `grep step_timeout <runtime>/state/…/<uid>.py` on
   the launcher shows `1800.0` for meshcore-cli and `28800.0` for a meshcom Build.

## 7. Open questions (recommendation)
- **Q1** A shortens the web cap from 1800 to the CLI values for components without a declared value, and for
  nomadnet/lxmd/graywolf (900) and meshcore-webui's test (120). → **Accept.** It is the CLI rule the live tests
  already pass. Don't add a launcher-only floor, because that is the two-rule state F40 is about.
- **Q2** Is 1800 s enough on the box that produced F39/F40? Its step exceeded 1800 on the web path. → Ship A+B+C
  (honest and consistent), then read `uname -m`, `/etc/pip.conf` and the step's duration in the fixed log on that
  box. Raise meshcore-cli's budget or do option G (constraints) only on evidence.
- **Q3** The generic test "every pip-building component declares ≥1800" → **No.** It goes red for nomadnet and lxmd,
  which would widen the group. File it as a separate finding if wanted.
- **Q4** B's one-time "NOT built" after the update → **Accept with the CHANGELOG line.** A migration that writes the
  marker for an existing venv would certify a build nobody verified.
- **Q5** The stale comment at manifest :2224 ("600 s default") → fix it in commit 2 only if the maintainer wants
  (comment-only, no behaviour change).

## 8. Self-check
- Re-read against `e5187f70`:
  - `jobs.py:152-153`; `lifecycle.py:331,369,1479,1491`.
  - `build_launcher_runtime.py:34-44,117-153,174,300-310,340`; `commands.py:930-993,999`.
  - `service_lifecycle_ops.py:3456-3466,3474-3511,3538-3575,4758-4795`; `app.py:1666`.
  - manifest :2223-2227, :2626-2628, :2706-2738, :3262, :3308; `docs/maintenance.md:269`.
  - `tests/web/test_webjob.py:115-183`, `tests/core/test_build_launcher_runtime.py:93-97,358-370`.
- Prototype numbers: A's new webjob test 2 red → 2 green. core+web 2754 passed with A+B+C. B adds no failure to the
  94 environmental ones.
- Not verified:
  - the box's architecture, pip.conf and real step durations, the exact log names (which component F39 hit, and
    whether it was meshcore-node's 900 s test);
  - whether the operator pressed Build and Install/CLI as inferred in §1.4 (only the message formats prove it);
  - `test_released_manifests_still_parse` with full tags (shallow clone here; the reasoning in §2.B(a) stands).
