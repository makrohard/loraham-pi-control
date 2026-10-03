# Code report — T3: the same decision across entry paths

Base `origin/integration/0.12.0` (f257831), after the T1 series on this branch. Test-only batch:
no production change. `git diff --stat f257831..HEAD -- lhpc testlab` → empty (checked before
this commit and again before the push).

## Correction 2 (gate 1 round 2: RED — finding B a second time)

Every fix is amended into the commit it belongs to: plan 4d72d4a → 348edae, tests 1d11b4b →
0f38ea2, and this report with the gate file. The T1 commits below them changed too (T1
Correction 2), so every T3 SHA changed.

### FINDING B — the web path's "child" was a thread

- **Verified against the repo: confirmed.** In round 2 the substituted `Lifecycle.spawn_job`
  ran `main(argv[3:])` in a `threading.Thread` and returned `(f"{name}.log", os.getpid())`.
  The parent therefore tracked the test process. The child's `verify_tracked(...,
  _os.getpid())` (`main.py:1326`) compared that marker with the same pid, and passed. A
  production bug that records the parent's pid instead of the child's passes that harness —
  shown below. The plan's limitations list did not name the lost property (a distinct child
  pid).
- **What changed (the preferred fix: a real child process).** Nothing on the web path is
  substituted any more except the routing of one argv. The parent is the real route → the real
  `spawn_start_job`, which builds the argv `[sys.executable, "-m", "lhpc", "_stack-start",
  target, "--web-result", log, "--attempt-id", aid, …]` (`service_lifecycle_ops.py:3770`; reused
  as built, not re-implemented) → the real `Lifecycle.spawn_job` (log created through the
  anchored API) → the production `Lifecycle._real_spawn`: `subprocess.Popen(argv, stdout=log,
  stderr=STDOUT, stdin=DEVNULL, start_new_session=True)`. That is a separate process with its
  own pid, detached (setsid).
  - The only web-specific routing: the box's Lifecycle spawn (already `kiss_box`'s TNC spawn)
    hands an argv whose `[1:4]` is `["-m", "lhpc", "_stack-start"]` to `_real_spawn`. While
    `_real_spawn` runs, `subprocess.Popen` is wrapped so the Popen object is kept. The test,
    the child's parent, waits for the process to exit (`Popen.wait`, no sleep) and reaps it.
    Teardown kills and reaps a child still running, then kills every TNC the child spawned
    (`killpg`; the child records their pids).
  - The parent's real `_track_or_terminate` writes the job marker, and the child's real
    `webjob_gate.verify_tracked` checks it against the child's own pid. The real
    `_web_admit_handshake` waits for the child's attempt record.
  - `_web` then reads the job marker and asserts `child.pid != os.getpid()` and
    `marker["pid"] == child.pid`.
  - The cross-path assertions (`_same` / `_decisions`) now compare the CLI's decision with one
    produced by a distinct process. The web path's `refusal` and `next_commands` are the
    child's own outermost start/restart result, recorded in the child by a delegating spy.
  - The child runs the production `main()` through `python -m lhpc`. Its `ControllerService()`
    is built the production way, through `LHPC_SYSTEM_PROVIDER=entry_host:build`. The new
    helper module `tests/golden/entry_host.py` rebuilds the box there from a JSON description
    the test writes: a FakeSystem with the box's READY reply and band owners, `_TncEndpoint`,
    and the TNC spawn as `wrap_spawn`.
  - A fixture's patch does not cross a process boundary. So `entry_host` re-applies in the
    child, with the test process's values, the suite's autouse isolation: the firewall readers
    and receipt (and a scenario's `firewall_status`), `updater_units._SYSTEM_ROOTS`,
    `config.HW_DEFAULT`, `display_available`, the three `RealProcFs` reads,
    `gps.local_gpsd_listening`, `read_kernel_time_state`, the download refusal, and the three
    bounded waits.
  - `LHPC_WEB_ADMIT_TIMEOUT_S` is raised from 3 s to 30 s. The handshake returns as soon as
    the admission is observed, so the higher bound only keeps a slow interpreter start from
    reading `pending`.
  - Every expected decision and rendering is unchanged: 13 passed, with all 9 `intended` and
    4 `known defect` tags as in round 2.
- **Limitations, now named (plan, `entry` docstring, here).** The child's stdout (the job
  log) is not asserted. The suite's pip and shell guards are not replicated in the child (they
  are checks, not host state). The `job` path still runs the runner in the test process over a
  marker naming the test process: that path is the runner alone, and its tracked process is the
  one that runs the gate.

### Negative control (the harness catches a parent that tracks itself)

Mutation, applied in a scratch copy of the tree and never committed: `spawn_start_job` tracks
the parent instead of the spawned pid. Both `procident.proc_identity(pid)` and
`_track_or_terminate(life, ln, pid, …)` take `os.getpid()` instead of `pid`
(`service_lifecycle_ops.py:3784/3786`). Command: `python -m pytest -q -p no:cacheprovider
tests/golden/test_same_decision_across_entry_paths.py -k "test_success or
test_interactive_launch"`, run in the scratch copy.

| harness | result |
|---|---|
| round 2 (thread child, `os.getpid()`) | `2 passed, 11 deselected` — blind |
| round 3 | `2 failed, 11 deselected`: `AssertionError: the parent tracked pid 11100, the child is 11102`, and the child exited 3 |
| round 3 with the marker assertion removed | `-k test_success`: `1 failed, 12 deselected`, `the web child decided nothing (rc 3)` — the child's own gate refused ("parent tracking not confirmed") |

The unmutated round-3 module: `13 passed`. The round-2 red-before mutation (`_stack-start`:
`ok = res.ok`, now also executed by the separate web child) → `1 failed, 12 passed`
(`test_interactive_launch`; web and job `started: False` against the CLI's `True`).

### "+524 lines" (report) vs "+1,525" (diff)

The round-2 module had 525 lines. `git show --numstat --format= 1d11b4b` → `525 0
tests/golden/test_same_decision_across_entry_paths.py`. The report said +524, which was wrong
by one. The diff's `@@ -0,0 +1,525 @@` is a hunk header, read "new file, from line 1, 525
lines" — not +1,525. Every count below is re-measured with its command.

### Numbers, measured (at the branch tip unless stated)

| figure | command | output |
|---|---|---|
| T3 code commit | `git show --stat --format= 0f38ea2` | `4 files changed, 758 insertions(+), 11 deletions(-)` |
| per file | `git show --numstat --format= 0f38ea2` | test module `550 0`, `entry_host.py` `184 0`, `conftest.py` `18 11`, `tests/README.md` `6 0` |
| T3 plan | `git show --numstat --format= 348edae` | `139 0 plans/PLAN-T3.md` |
| round-2 module, for comparison | `git show --numstat --format= 1d11b4b` | `525 0` (the report said 524) |
| production lines changed | `git diff --stat f257831..HEAD -- lhpc testlab \| wc -l` | `0` |
| cases | `python -m pytest --collect-only -q -p no:cacheprovider tests/golden/test_same_decision_across_entry_paths.py` | `13 tests collected` |
| tags | `grep -c '^    """intended:' <module>`; `grep -c '^    """known defect' <module>` | `9`; `4` |
| the module | `python -m pytest -q -p no:cacheprovider tests/golden/test_same_decision_across_entry_paths.py` | `13 passed` |
| golden + repo | `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` | `478 passed, 5 skipped` |
| golden set, three runs | `python -m pytest -q -p no:cacheprovider tests/golden` ×3 | `54 passed` ×3 |
| lint | `ruff check tests --select F,E9` | `All checks passed!` |
| leaked processes after a run | `ps -eo args \| grep -E "python3? -c import signal\|_stack-start" \| grep -v grep` | no output |

## Correction 1 (gate 1 round 1: RED)

Kept as the round-2 record. Where it describes the web child as a thread, Correction 2
supersedes it.

Every fix is amended into the commit it belongs to (plan 05719db → 4d72d4a, tests 12302e5 →
1d11b4b; this report and the gate file in this commit).

| finding | commit | what changed |
|---|---|---|
| A — "boot skips an interactive main" was tagged `intended` though it is a different decision | plan, tests | `test_interactive_launch` is `known defect T3-F3: boot restore skips an interactive main where the CLI, the console and the job runner launch it — decision differs`. The plan states the rule in one sentence: the only `intended` differences are rendering differences (same decision, different words or format); every decision difference is a known defect with its own id. Its open question 1 now asks W4 to decide the rule, instead of "keep". |
| B — the web harness was not observational (child run inside a wrapped `_web_admit_handshake`; `cli_main.ControllerService` replaced by the parent's service) | tests, plan | Both substitutions are gone. Only `Lifecycle.spawn_job` is substituted on the web path: the argv's `lhpc _stack-start …` runs through `main()` in a thread, concurrently with the real tracking and the real handshake, and its rc and any exception are collected after a join. Every `lhpc` process (CLI, web child, job runner, boot unit) builds its own `ControllerService()` the production way; the box's host reaches it through the production extension point `LHPC_SYSTEM_PROVIDER`. Every remaining substitution is named in the fixture docstring, the plan and the 6-point block, with what is not covered. |
| (1) `test_unverified_termination`'s web assertion was vacuous (`render.get("rc")` is always `None` for the web) | tests | Every test now asserts every path's full rendering. For this case: web `{"status": 302, "admission": "admitted", "child_rc": 1, "job": ("failed", True)}`. |
| (2) `test_band_owner` encoded a second, untagged disagreement | tests, plan, report | Tag `known defect T3-F4, T1-F1:`; T3-F4 is the refusal-class difference (web `blockers` vs the apply paths' refusal summary), T1-F1 the files difference. `test_firewall_gate` is likewise `known defect T3-F2, T1-F1:`. The conftest tag rule accepts a list of ids. Each id is defined above by exactly the difference it names. |
| (3)/(4) fixture and the 6-point sentence "the only substituted production call is `Lifecycle.spawn_job`" | tests, report | The sentence is replaced by the full list of substitutions (6-point 3), true for the amended diff. |
| precision | plan, report, README | Base named `origin/integration/0.12.0` (the base name corrected). The README sentence no longer says "only the rendering may differ"; it states the intended/known-defect rule. |

Red-before of the assertions made real. Each mutation was applied to `lhpc/` in two trees — the
original S1 branch (round-1 tests) and this branch (round-2 tests) — run, and reverted; none was
committed:

- (1) the parent reports an admitted job as `pending` (`spawn_start_job` returns
  `log, "pending", ""`) — `-k unverified`: round 1 `1 passed`, round 2 `1 failed`.
- B, concurrency: the child sleeps 4 s before its gate (the parent waits 3 s) — `-k test_success`:
  round 1 `1 passed` (its child had finished before the handshake), round 2 `1 failed` (admission
  `pending`).
- B, own service: `ControllerService.__init__` ignores the provider's manifest — `-k ambiguity`:
  round 1 `1 passed` (every path used the box's service), round 2 `1 failed`.
- full renderings: `autostart --run-service` exits 1 when an item failed — round 1 `4 failed, 9
  passed`, round 2 `7 failed, 6 passed`.

A probe run (not committed) confirmed the wiring: on each path the box's service is built in the
main thread without a provider and the path's own service with it; on the web path the child's
service is built in the thread `lhpc-web-child`.

Evidence: `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` on the branch →
`476 passed, 5 skipped`; the golden set three times in a row → `54 passed` each;
`ruff check tests --select F,E9` → `All checks passed!`.

## Commits

| sha | subject | files | tests | red-before |
|---|---|---|---|---|
| 348edae | T3: plan — the same decision across entry paths | `plans/PLAN-T3.md` | — | — |
| 0f38ea2 | T3: cross-path decision tests — CLI, console, job runner, boot-restore | `tests/golden/test_same_decision_across_entry_paths.py` (new), `tests/golden/entry_host.py` (new: the host provider, also for the separate web child), `tests/golden/conftest.py` (`kiss_box(root=…)`, `KissBox.live()`, the tag rule accepting several ids), `tests/README.md` (two sentences) | 13 | yes, by mutation (below), plus the negative control (Correction 2) |

Every decision in the plan's inventory has a test. Of the 13 cases, 9 are `intended:` (the
decision is the same on every path; only the rendering differs) and 4 are `known defect`:
`test_admission_refusal` (T3-F1), `test_firewall_gate` (T3-F2, T1-F1), `test_interactive_launch`
(T3-F3) and `test_band_owner` (T3-F4, T1-F1).

## Red-before (mutation: one path made to decide differently)

The mutation is in `lhpc/adapters/cli/main.py`, the `_stack-start` branch (the job runner):
`ok = res.ok or (op == "start" and manual_required_only(res.results))` → `ok = res.ok`. It was
applied, run and reverted with `git checkout -- lhpc/adapters/cli/main.py`, and never committed.

`python -m pytest -q -p no:cacheprovider tests/golden/test_same_decision_across_entry_paths.py`
→ `FAILED …::test_interactive_launch` — `1 failed, 12 passed` (re-run after Corrections 1 and
2: the same; since Correction 2 the web child executing the mutated `main.py` is a separate
process). The assertion diff shows `web` and `job` with `started: False` against `True` for the
CLI. The tree was clean afterwards.

## Findings recorded (not fixed in this batch)

- **T3-F1 (known defect, `test_admission_refusal`).** The CLI, the job runner and boot-restore
  return the typed `data["admission_blocked"]`. The console's parent refusal
  (`ControllerService.spawn_start_job`, `lhpc/core/service_lifecycle_ops.py:3730`, returning
  `(None, "blocked", reason)`) carries only the reason text, so the web cannot branch on the class.
  - Fix item: return the tag with the tuple, or an `ActionResult`.
- **T3-F2 (known defect, `test_firewall_gate`).** The CLI and the web refuse a firewall-gated
  start at their plan, where `firewall_gate_stack_start(render=False)`
  (`lhpc/core/service_firewall.py:1032`) does not re-render. Their next command names
  `config/files/firewall/firewall-apply.sh`, and that script is stale: it lacks the newly exposed
  listener (the test asserts the `loraham-kiss-tnc.tcp-8001` endpoint is absent there).
  - The job runner and boot-restore refuse at the apply (`render=True`), which re-renders the
    script.
  - Same input, different files, and the operator on the plan-first paths is pointed at a script
    that would not open the port.
  - Fix item: render on the refusal for every path, or have the plan name a command that renders
    first.
- **T3-F3 (known defect, `test_interactive_launch`).** Boot restore skips an interactive main
  where the CLI, the console and the job runner launch it — decision differs. The three run the
  start (config generated, dashboard marker set, never spawned) and count MANUAL_REQUIRED as
  success; boot-restore's plan skips the item ("interactive main — manual start",
  `boot_restore.py:288`): `started` False and no files.
  - Fix item (W4): one rule for every path — boot-restore replays the interactive main through
    the same start, or every path declines it.
- **T3-F4 (known defect, `test_band_owner`).** With a running band owner the web stops at its
  plan's `blockers` listing (the confirm page, refusal class `blockers`), while the CLI, the job
  runner and boot-restore refuse in the apply's preflight with "Cannot run 'kiss': meshtastic
  must be stopped first." — the refusal class differs.
  - Fix item: one typed band-owner refusal (T1-N1) that the plan and the apply both return.
- **T1-F1 across paths (`test_band_owner`, `test_firewall_gate`).** The apply paths reset the
  band's feed floor (`state/daemon-feed-floor-433`) before their preflight refusal. The
  plan-first web (band owner) and the CLI and web (firewall) do not, so the files differ.
- **Observation T3-N1 (intended, recorded).** A restart whose only shortfall is MANUAL_REQUIRED
  is a failure on the CLI, the web and the job runner alike; a start counts the same result as
  success (`main.py:1393`, `:1346`). Paths agree; the start/restart asymmetry is for W4 to
  decide.
- **Inventory note.** There is no separate "TX opt-in" refusal on the start path. TX is gated by
  the identity rule and, for POWER=20, by the daemon's high-power permission, a per-component
  BLOCKED after launch began (`service_lifecycle_ops.py:2041`); that gate had no test before
  this batch. The disagreements the brief cites (G3 start plan vs apply, F36 one-click vs CLI
  update warning) are covered for start by the firewall and band-owner cases. F36 concerns
  update, which is not an entry path of these decisions.

## Simplicity guardrails

- No production code. In the test: one `entry` fixture and one plain function per decision; the
  decision is a plain dict. No framework, registry or result type. The batch text's "one
  parametrised test per decision" is implemented as one function per decision calling `entry`
  for each path. The paths are different code, not permutations, and a per-path disagreement is
  asserted in one place (plan, "Simplicity").
- Line counts (measured; table in Correction 2): production 0 changed. Tests:
  `test_same_decision_across_entry_paths.py` +550, `entry_host.py` +184, `conftest.py` +18/−11,
  `tests/README.md` +6.
- Dependencies of the new module: `lhpc.adapters.cli.main`, `lhpc.adapters.web.app.create_app`,
  `lhpc.core.{config, jobresult, jobs, procident, paths, probes.backends, services}`; the
  directory's helper `entry_host` (rule 7); the standard library's `contextlib`, `signal`,
  `subprocess`, `threading`, `tomllib`; root fixture `csrf`; golden fixtures `kiss_box`,
  `prior_boot`, `uninstall_guard`, `interrupted_install`; `repo_paths.DATA`. `entry_host`:
  `lhpc.core.{binary_install, config, firewall, gps, lifecycle, outcomes, probes.backends,
  service_system, services, updater_units}` and, loaded by path in the child,
  `tests/golden/conftest.py` (`_READY`, `_TncEndpoint`, `KissBox`).
- Typed outcomes: none added.

## 6-point block

1. **CONTRACTS.** None changed. Contracts the tests rely on, each with its owner:
   - `spawn_start_job` → `(log | None, admission, reason)` (`service_lifecycle_ops.py:3730`).
   - The `_stack-start` argv and exit codes 0/1/3 (`main.py:622`, `:1309`).
   - The jobresult marker `state`/`admitted` (`lhpc/core/jobresult.py:210`).
   - `autostart --run-service` exit 0 iff `driver_completed` (`main.py:1407`).
   - The boot journal item states (`service_boot_restore.py:470/516`).
   - The CLI's exit 0/1 from `_render` (`main.py:228`).
2. **INVARIANTS + TESTS** (docs/architecture.md):
   - "Both adapters parse input, call one ControllerService method and render the ActionResult …
     validation, gating and results are identical" (Package layout): the whole module.
     T3-F1 to T3-F4 record where it does not hold today.
   - Identity enforcement "the CLI dry run, the web click and the locked mutation share one
     verdict": `test_missing_identity`.
   - Config as a transaction, "each lhpc process also finishes it eagerly at start":
     `test_pending_config_journal`.
   - Source transactions: `test_interrupted_install`.
   - Truthful outcomes, "CLI exit status and web flash agree": `test_success` and
     `test_unverified_termination` assert every path's full rendering (the CLI's exit code, the
     web's HTTP status, parent admission, child exit code and job state, the job runner's, and
     the boot journal); `test_interactive_launch` records where boot-restore decides
     differently (T3-F3).
   - Detached web jobs, the child's `verify_tracked` gate passing only once tracked: every web
     case that reaches the spawn runs a separate child process through the production spawn,
     the real gate against that process's own pid, and the real admission handshake; the test
     asserts the marker names the child's pid, not its own (negative control: Correction 2).
   - Boot restore replays only through the normal gated start: every boot case.
3. **KNOWN FAILURE CLASSES.**
   - Fakes and substitutions — every one (the `entry` fixture's docstring lists the same):
     (1) web only: the box's Lifecycle spawn routes an `lhpc _stack-start` argv to the
     production `Lifecycle._real_spawn` (a real detached `Popen`), with `subprocess.Popen`
     wrapped during that call only to keep the Popen object, so the test waits for and reaps
     the child; `LHPC_WEB_ADMIT_TIMEOUT_S` 30 s (production 3 s); (2) the host:
     `LHPC_SYSTEM_PROVIDER=entry_host:build` serves the box's FakeSystem and manifest to each
     process's own `ControllerService()` — in the test process the live box, in the separate
     child the box rebuilt from its description, with the suite's autouse isolation re-applied
     there (Correction 2 lists it); (3) `kiss_box`'s real TNC spawn, kernel-read endpoint and
     3 s endpoint wait; (4) `prior_boot`'s `LHPC_BOOT_ID_FILE` (the two boot-restore host gates
     are arranged, not stubbed — T1 Correction 2); (5) in `test_firewall_gate`, the firewall
     host readers `_fw_integration_state` and `firewall_status`; (6) spies on `start`,
     `restart`, `boot_restore_run` and `spawn_start_job` (and in the child on `start` and
     `restart`), which take `*a, **k` and delegate; (7) the suite's autouse isolation. Not
     covered: the child's stdout (the job log) is not asserted; the pip and shell guards are not
     replicated in the child.
   - EIO/EACCES/ENOTDIR probes and KeyboardInterrupt: not in scope (no probe or cleanup touched).
   - Same decision across CLI / web / detached job / boot-restore: this batch.
   - Stacked-only conflicts: only `tests/golden/` and one README sentence, the same files T1
     touched on this branch; no other batch's files.
4. **TEST RULES.**
   - Red-before by mutation (above).
   - Decision-bearing strings are compared by exact equality (whole summaries, whole renders —
     every test asserts every path's full rendering); no `startswith`.
   - Every seeding call asserts its return (`reserve`, `write_job_marker`, `build`,
     `save_config_bundle`).
   - No network beyond loopback: the web runs on Flask's test client, the child is a local
     interpreter on the same box (no socket of its own beyond the TNC's), and the TNC listens on
     a free 127.0.0.1 port.
5. **WHOLE TEST DIRECTORIES.**
   - `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` → `478 passed, 5 skipped`
     (after Correction 2).
   - `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` →
     `All checks passed!`.
   - No signature changed. The `kiss_box` fixture gained an optional keyword; its only users are
     in `tests/golden/`, which was run.
6. **ADVERSARIAL SELF-REVIEW.** Found and fixed before this report:
   - (a) The first harness re-wrapped the web seams on every call: nested parents, the child run
     twice, HTTP 500. The seams are now patched once per test.
   - (b) Boot's deciding result was the driver's, not the item's start. The start spy now counts
     depth separately from the boot spy.
   - (c) A web parent refusal was classified by the route's earlier plan (`ok`). The parent's
     refusal now wins when no child ran.
   - (c2) Correction 1 replaced the round-1 harness (child run inside a wrapped handshake, the
     parent's service shared with every path). Correction 2 replaced its thread child with a
     separate process (6-point 3).
   - (c3) Correction 2: the first draft patched the routing in the fixture's set-up. `kiss_box`
     re-patches `ControllerService._lifecycle` for every box it makes, so the parent spawned a
     TNC process as its "child": tracked, never admitted, handshake `pending`. The routing is
     now applied right after each box is made.
   - (c4) Correction 2: the child runs where `lhpc` resolves for its `PYTHONPATH`. The fixture
     puts the imported `lhpc`'s parent there, so a scratch copy's mutation reaches the child
     too (checked with the `ok = res.ok` mutation, which the web child then executes).
   - (c5) Correction 2: `subprocess` would reap a dropped Popen behind the test's back
     (`_cleanup` on the next Popen) and lose the exit status. The Popen object is kept and
     waited on.
   - (c6) Correction 2: the child was first registered for teardown only after `_web` returned,
     so a failing marker assertion would have left it unreaped. It is now registered when it is
     spawned. Checked: after the negative control's two failures, no `_stack-start` or TNC
     process was left (`ps`).
   - (c7) Correction 2: the files this branch adds were grepped for attribution words. The
     only hits are the POSIX sense of "session" (`start_new_session`, `needs_session`, "its own
     session" of a process) and the web session key; the prose added this round says "detached
     (setsid)".
   - (d) chat cannot be the identity case for boot-restore, because an interactive main is never
     replayed. graywolf, built through the real build, is used instead.
   - (e) The ambiguity case's evidence had no band and was skipped as band-ambiguous. It is now
     on 433.
   - (f) The firewall assertion first claimed the script was absent. It exists from bootstrap; the
     precise defect is that it is stale, and that is what is asserted.
   Remaining nit: `_EveryStepSucceeds` exists in both the build golden and this module (rule 7
   forbids importing a sibling test module; three lines). The docs and this report describe
   exactly the diff.

## Deviations

- **No CHANGELOG line.** The batch is test-only and FILES is `tests/` only (as T1).
- **Boot entry.** The boot path enters through `lhpc autostart --run-service` (which calls
  `boot_restore_run`) rather than `boot_restore_run` alone. The process entry is what finishes a
  pending config journal, and calling `boot_restore_run` directly would have recorded a
  disagreement that does not exist on a box.
