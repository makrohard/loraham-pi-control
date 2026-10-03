# Code report — T3: the same decision across entry paths

Base `origin/integration/0.12.0` (f257831), after the T1 series on this branch. Test-only batch:
no production change. `git diff --stat f257831..HEAD -- lhpc testlab` → empty (checked before
this commit and again before the push).

## Correction 1 (gate 1 round 1: RED)

Every fix is amended into the commit it belongs to (plan 05719db → 4d72d4a, tests 12302e5 →
1d11b4b; this report and the gate file in this commit).

| finding | commit | what changed |
|---|---|---|
| A — "boot skips an interactive main" was tagged `intended` though it is a different decision | plan, tests | `test_interactive_launch` is `known defect T3-F3: boot restore skips an interactive main where the CLI, the console and the job runner launch it — decision differs`. The plan states the rule in one sentence: the only `intended` differences are rendering differences (same decision, different words or format); every decision difference is a known defect with its own id. Its open question 1 now asks W4 to decide the rule, instead of "keep". |
| B — the web harness was not observational (child run inside a wrapped `_web_admit_handshake`; `cli_main.ControllerService` replaced by the parent's service) | tests, plan | Both substitutions are gone. Only `Lifecycle.spawn_job` is substituted on the web path: the argv's `lhpc _stack-start …` runs through `main()` in a thread, concurrently with the real tracking and the real handshake, and its rc and any exception are collected after a join. Every `lhpc` process (CLI, web child, job runner, boot unit) builds its own `ControllerService()` the production way; the box's host reaches it through the production extension point `LHPC_SYSTEM_PROVIDER`. Every remaining substitution is named in the fixture docstring, the plan and the 6-point block, with what is not covered. |
| (1) `test_unverified_termination`'s web assertion was vacuous (`render.get("rc")` is always `None` for the web) | tests | Every test now asserts every path's full rendering. For this case: web `{"status": 302, "admission": "admitted", "child_rc": 1, "job": ("failed", True)}`. |
| (2) `test_band_owner` encoded a second, untagged disagreement | tests, plan, report | Tag `known defect T3-F4, T1-F1:`; T3-F4 is the refusal-class difference (web `blockers` vs the apply paths' refusal summary), T1-F1 the files difference. `test_firewall_gate` is likewise `known defect T3-F2, T1-F1:`. The conftest tag rule accepts a list of ids. Each id is defined above by exactly the difference it names. |
| (3)/(4) fixture and the 6-point sentence "the only substituted production call is `Lifecycle.spawn_job`" | tests, report | The sentence is replaced by the full list of substitutions (6-point 3), true for the amended diff. |
| precision | plan, report, README | Base named `origin/integration/0.12.0` (was `integration/1.0`). The README sentence no longer says "only the rendering may differ"; it states the intended/known-defect rule. |

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
| 4d72d4a | T3: plan — the same decision across entry paths | `plans/PLAN-T3.md` | — | — |
| 1d11b4b | T3: cross-path decision tests — CLI, console, job runner, boot-restore | `tests/golden/test_same_decision_across_entry_paths.py` (new), `tests/golden/conftest.py` (`kiss_box(root=…)`, `KissBox.live()`, the tag rule accepting several ids), `tests/README.md` (one sentence) | 13 | yes, by mutation (below) |

Every decision in the plan's inventory has a test. Of the 13 cases, 9 are `intended:` (the
decision is the same on every path; only the rendering differs) and 4 are `known defect`:
`test_admission_refusal` (T3-F1), `test_firewall_gate` (T3-F2, T1-F1), `test_interactive_launch`
(T3-F3) and `test_band_owner` (T3-F4, T1-F1).

## Red-before (mutation: one path made to decide differently)

The mutation is in `lhpc/adapters/cli/main.py`, the `_stack-start` branch (the job runner):
`ok = res.ok or (op == "start" and manual_required_only(res.results))` → `ok = res.ok`. It was
applied, run and reverted with `git checkout -- lhpc/adapters/cli/main.py`, and never committed.

`python -m pytest -q -p no:cacheprovider tests/golden/test_same_decision_across_entry_paths.py`
→ `FAILED …::test_interactive_launch` — `1 failed, 12 passed` (re-run after Correction 1: the
same). The assertion diff shows `web` and `job` with `started: False` against `True` for the
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
- Line counts: production 0 changed. Tests: `test_same_decision_across_entry_paths.py` +524,
  `conftest.py` +18/−11, `tests/README.md` +4.
- Dependencies of the new module: `lhpc.adapters.cli.main`, `lhpc.adapters.web.app.create_app`,
  `lhpc.core.{config, jobresult, jobs, procident, lifecycle, outcomes, paths, probes.backends,
  services}`; the standard library's `threading`, `types`, `sys`; root fixtures `csrf`; golden fixtures `kiss_box`, `prior_boot`,
  `uninstall_guard`, `interrupted_install`; `repo_paths.DATA`.
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
     case that reaches the spawn runs the real gate and the real admission handshake, the
     parent and the child running concurrently.
   - Boot restore replays only through the normal gated start: every boot case.
3. **KNOWN FAILURE CLASSES.**
   - Fakes and substitutions — every one (the `entry` fixture's docstring lists the same):
     (1) web only: `Lifecycle.spawn_job`, real signature `(self, name, argv, cwd, env=None)`; it
     runs the argv's `lhpc _stack-start …` through `main()` in a thread of this process and
     returns this process's pid; (2) the host: `LHPC_SYSTEM_PROVIDER` serves the box's
     FakeSystem and manifest to each process's own `ControllerService()`; (3) `kiss_box`'s real
     TNC spawn, kernel-read endpoint and 3 s endpoint wait; (4) `prior_boot`'s boot id and two
     boot-restore host gates; (5) in `test_firewall_gate`, the firewall host readers
     `_fw_integration_state` and `firewall_status`; (6) spies on `start`, `restart`,
     `boot_restore_run` and `spawn_start_job`, which take `*a, **k` and delegate; (7) the suite's
     autouse isolation. Not covered: what a separate process alone changes (its own module
     state, environment, stdout log file, signal disposition).
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
   - No network beyond loopback: the web runs on Flask's test client, the child in a thread
     of this process, and the TNC listens on a free 127.0.0.1 port.
5. **WHOLE TEST DIRECTORIES.**
   - `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` → `476 passed, 5 skipped`
     (after Correction 1).
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
     parent's service shared with every path) with the observational one in (6-point 3).
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
