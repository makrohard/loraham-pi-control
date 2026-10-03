# Code report — T3: the same decision across entry paths

Base `origin/integration/1.0` (f257831), after the T1 series on this branch. Test-only batch: no
production change. `git diff --stat origin/integration/1.0..HEAD -- lhpc testlab` → empty
(checked before this commit and again before the push).

## Commits

| sha | subject | files | tests | red-before |
|---|---|---|---|---|
| 05719db | T3: plan — the same decision across entry paths | `plans/PLAN-T3.md` | — | — |
| 12302e5 | T3: cross-path decision tests — CLI, console, job runner, boot-restore | `tests/golden/test_same_decision_across_entry_paths.py` (new), `tests/golden/conftest.py` (`kiss_box(root=…)`, `KissBox.live()`), `tests/README.md` (one sentence) | 13 | yes, by mutation (below) |

Every decision in the plan's inventory has a test. Of the 13 cases, 10 are `intended:` and 3 are
`known defect`: T3-F1, T3-F2, and T1-F1 seen across paths.

## Red-before (mutation: one path made to decide differently)

The mutation is in `lhpc/adapters/cli/main.py`, the `_stack-start` branch (the job runner):
`ok = res.ok or (op == "start" and manual_required_only(res.results))` → `ok = res.ok`. It was
applied, run and reverted with `git checkout -- lhpc/adapters/cli/main.py`, and never committed.

`python -m pytest -q -p no:cacheprovider tests/golden/test_same_decision_across_entry_paths.py`
→ `FAILED …::test_interactive_launch` — `1 failed, 12 passed`. The assertion diff shows `web`
and `job` with `started: False` against `True` for the CLI. The tree was clean afterwards.

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
- **T1-F1 across paths (`test_band_owner`, `test_firewall_gate`).** The apply paths reset the
  band's feed floor before their preflight refusal. The plan-first web (band owner) and the CLI
  and web (firewall) do not, so the files differ.
- **Observation T3-N1 (intended, recorded).** A restart whose only shortfall is MANUAL_REQUIRED
  is a failure on the CLI, the web and the job runner alike; a start counts the same result as
  success (`main.py:1393`, `:1346`). Paths agree; the start/restart asymmetry is for W4 to
  decide.
- **Observation (intended).** Boot-restore never replays an interactive main: its plan skips it
  with "interactive main — manual start" (`boot_restore.py:288`), and the test asserts the skip.
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
- Line counts: production 0 changed. Tests: `test_same_decision_across_entry_paths.py` +464,
  `conftest.py` +16/−8, `tests/README.md` +3.
- Dependencies of the new module: `lhpc.adapters.cli.main`, `lhpc.adapters.web.app.create_app`,
  `lhpc.core.{config, jobresult, jobs, procident, lifecycle, outcomes, paths, probes.backends,
  services}`; root fixtures `csrf`; golden fixtures `kiss_box`, `prior_boot`,
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
     T3-F1/T3-F2 record where it does not hold today.
   - Identity enforcement "the CLI dry run, the web click and the locked mutation share one
     verdict": `test_missing_identity`.
   - Config as a transaction, "each lhpc process also finishes it eagerly at start":
     `test_pending_config_journal`.
   - Source transactions: `test_interrupted_install`.
   - Truthful outcomes, "CLI exit status and web flash agree": `test_success`,
     `test_unverified_termination`, `test_interactive_launch`.
   - Detached web jobs, the child's `verify_tracked` gate passing only once tracked: every web
     case runs the real gate.
   - Boot restore replays only through the normal gated start: every boot case.
3. **KNOWN FAILURE CLASSES.**
   - Fakes: the real FakeSystem and Lifecycle; the only substituted production call is
     `Lifecycle.spawn_job`, with its real signature `(self, name, argv, cwd, env=None)`. The
     spies take `*a, **k` and delegate.
   - EIO/EACCES/ENOTDIR probes and KeyboardInterrupt: not in scope (no probe or cleanup touched).
   - Same decision across CLI / web / detached job / boot-restore: this batch.
   - Stacked-only conflicts: only `tests/golden/` and one README sentence, the same files T1
     touched on this branch; no other batch's files.
4. **TEST RULES.**
   - Red-before by mutation (above).
   - Decision-bearing strings are compared by exact equality (whole summaries, whole renders);
     no `startswith`.
   - Every seeding call asserts its return (`reserve`, `write_job_marker`, `build`,
     `save_config_bundle`).
   - No network: the web runs on Flask's test client and the child in-process.
5. **WHOLE TEST DIRECTORIES.**
   - `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` → `476 passed, 5 skipped
     in 25.82s`.
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
