# Gate-1 code review request — T3, round 2

Please judge round 2 of batch T3 of the loraham-pi-control architecture consolidation: tests that drive the same start/restart scenario through every entry path (the CLI, the console route with its detached job, the job runner alone, the boot-restore unit) and compare the decision — started or not, the refusal class, the files touched, the live processes; a rendering difference is intended, every decision difference is recorded as a known defect with its own id. Round 1 was judged RED; the section "Round 1 RED → round 2" below lists each finding, what changed and the evidence. Judge the amended plan (below) and each amended code commit (the full amended diff is at the end, preceded by the round-2 interdiff) against the plan, the batch's rules (test-only: no production change; every case tagged `intended` or `known defect <id>`, the tag naming exactly the difference the assertion encodes; red-before shown by a deliberate mutation; no startswith-only assertions on decision-bearing strings; no network beyond loopback) and the report's 6-point block. Look in particular for an assertion that cannot fail, a case whose tag does not match what it records, a fixture that changes behaviour instead of observing it, and a claim in the plan or report the diff does not support.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

Answer form (one row per commit, then one final line):

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| … | … | … |

Final line: GREEN / GREEN WITH NOTES / RED

## Round 1 RED → round 2

Round 1 verdict: RED — findings A and B on the plan, (1)–(4) on the tests, and the precision
checklist. Each fix is amended into the commit it belongs to. Round-1 → round-2 commits:
05719db → 4d72d4a (plan), 12302e5 → 1d11b4b (tests); both now sit on the round-2 T1 series
(T1's harness changed: the TNC is a real process with a kernel-read endpoint — see the T1
packet).

### FINDING A — "boot skips an interactive main" was tagged `intended`

- Changed (plan, tests): `test_interactive_launch` is now `known defect T3-F3: boot restore
  skips an interactive main where the CLI, the console and the job runner launch it — decision
  differs`. The plan states the rule in one sentence: the only `intended` differences are
  rendering differences (same decision, different words or format); every decision difference
  is a known defect with its own id. The module docstring and the README sentence say the same.
  The plan's open question 1 now asks W4 to decide the rule instead of recommending "keep".
- Tags after round 2: 9 `intended:` (decision identical on every path; asserted with `_same`),
  4 known defects: `test_admission_refusal` T3-F1 (refusal class: typed vs reason text),
  `test_firewall_gate` T3-F2, T1-F1 (files: re-rendered script; feed floor),
  `test_interactive_launch` T3-F3 (started, files), `test_band_owner` T3-F4, T1-F1 (refusal
  class: `blockers` vs the apply's summary; files: feed floor).

### FINDING B — the web harness was not observational

- Changed (tests, plan): the wrapped `_web_admit_handshake` and the replacement of
  `cli_main.ControllerService` are gone. On the web path only `Lifecycle.spawn_job` is
  substituted: it starts the argv's `lhpc _stack-start …` through `main()` in a thread of this
  process and returns this process's pid. The parent's real tracking and real admission
  handshake run concurrently with the child, gated by the real `verify_tracked`; `_web` joins
  the thread and re-raises any exception it raised. Every `lhpc` process a path runs (the CLI,
  the web child, the job runner, the boot unit) builds its own `ControllerService()` the
  production way; the box's host (FakeSystem and manifest) reaches it through the production
  extension point `LHPC_SYSTEM_PROVIDER` (a provider module registered for the test). Every
  remaining substitution is named in the `entry` docstring, the plan's "Change" section and the
  report's 6-point block, together with what is not covered (what a separate process alone
  changes: module state, environment, stdout log file, signal disposition).
- Evidence (each mutation applied to `lhpc/` in the round-1 and the round-2 tree, run, reverted):
  child sleeps 4 s before its gate (the parent's handshake waits 3 s) — `-k test_success`:
  round 1 `1 passed`, round 2 `1 failed` (admission `pending`); `ControllerService.__init__`
  ignores the provider's manifest — `-k ambiguity`: round 1 `1 passed`, round 2 `1 failed`. A
  probe run (not committed) showed each path's own service built with the provider, the web
  child's in the thread `lhpc-web-child`.

### Test findings (1)–(4)

- (1) `test_unverified_termination`'s web assertion could not fail. Every test now asserts every
  path's full rendering; here the web is `{"status": 302, "admission": "admitted", "child_rc": 1,
  "job": ("failed", True)}`. Mutation "the parent reports an admitted job as pending" — round 1
  `1 passed`, round 2 `1 failed`. Mutation "the boot unit exits 1 when an item failed" — round 1
  `4 failed, 9 passed`, round 2 `7 failed, 6 passed`.
- (2) `test_band_owner` gets its own id T3-F4 next to T1-F1; `test_firewall_gate` likewise names
  T1-F1 next to T3-F2; the conftest tag rule accepts a list of ids. The report defines each id
  by exactly the difference it names.
- (3)/(4) see FINDING B; the report's sentence "the only substituted production call is
  `Lifecycle.spawn_job`" is replaced by the full list of substitutions.

### Precision checklist

- Base named `origin/integration/0.12.0` (f257831), not `integration/1.0`.
- The plan's "Same ControllerService per path", "the only substitution is the spawn itself" and
  "firewall host readers replaced on the box instance only" sentences are replaced by what the
  diff does (own services; the named substitutions; the firewall readers patched class-wide in
  `test_firewall_gate`).
- Report: Correction 1 added; counts (9 intended / 4 known defects), line counts, dependencies,
  no-network and invariant sentences describe the amended diff.

### Runs (round 2)

- `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` → `476 passed, 5 skipped`;
  the golden set three times in a row → `54 passed` each.
- `ruff check tests --select F,E9` → `All checks passed!`; `git diff --stat f257831..HEAD -- lhpc
  testlab` → empty.
- The round-1 red-before mutation (`_stack-start`: `ok = res.ok`) → round 2 `1 failed, 12
  passed` (`test_interactive_launch`), as in round 1.

## The plan

# PLAN-T3 — the same decision across entry paths

Batch T3 of the architecture consolidation (next 0.x.y release). Test-only: `tests/` (plus this
plan and the code-review files); `lhpc/` and `testlab/` unchanged. Base: `origin/integration/0.12.0` (f257831),
after the T1 series on the same branch (it reuses T1's `tests/golden/conftest.py`).

## Entry paths (file:line)

| path | entry | what it calls |
|---|---|---|
| cli | `lhpc/adapters/cli/main.py:1381` (`stack start/restart`), `_apply_flow` `:106` | `run_action(apply=False)` then `(apply=True)`; start counts `manual_required_only` as ok (`:1393`) |
| web | `lhpc/adapters/web/app.py:1560` `POST /action` | `operation_band` freeze (`:1576`); installed/built pre-check for a start (`:1615`); plan (`:1630`); `spawn_start_job` (`:1682`) |
| job | parent `service_lifecycle_ops.py:3730` `spawn_start_job`; child `main.py:1309` `_stack-start` | child: `webjob_gate.verify_tracked`, then `start/restart(apply=True, _before_*_locked=mark_running)`; ok = `res.ok or manual_required_only` for a start (`:1346`) |
| boot | `main.py:1407` `autostart --run-service` → `service_boot_restore.py:280` | per item `start(apply=True, _before_start_locked=claim hook)` (`:560`, hook `:470`); never plans, never restarts |

Every `lhpc` process first finishes a pending config journal (`main.py:1021`, `config.py:1871`).

## Decisions (where decided, file:line) → test

| decision | owner | test |
|---|---|---|
| missing identity / callsign | `service_lifecycle_ops.py:2903` → `service_params.py:2960` (plan, and under the locks) | `test_missing_identity` (graywolf) |
| admission | `services.py:2508` / `service_selfupdate.py:1110`; the web parent's own `_admit` (`service_lifecycle_ops.py:3730`) | `test_admission_refusal` |
| interrupted install | `services.py:2661` (apply only, under the index lock) | `test_interrupted_install` |
| band ownership | `run_blockers` `service_lifecycle_ops.py:356`, preflight `:2944`; plan lists `blockers` | `test_band_owner` |
| firewall gate | `service_firewall.py:1032` (plan `render=False`, apply `render=True`) | `test_firewall_gate` |
| config ambiguity | `service_params.py:746` (plan and preflight) | `test_config_ambiguity` |
| TX opt-in | no separate start gate: TX needs the identity (above) and, for POWER=20, the daemon's high-power permission, gated per component after launch began (`service_lifecycle_ops.py:2041`) | `test_tx_without_high_power_permission` |
| pending config journal | process startup, not the start (`main.py:1021`) | `test_pending_config_journal` |
| damaged client index | `pki.py:71/280` — certificate operations only; no start path decides on it | `test_damaged_client_index` |
| success, interactive launch | CLI `main.py:1393`, job `:1346`, boot `_boot_start_ok` `service_boot_restore.py:513`; boot skips an interactive main (`boot_restore.py:288`) | `test_interactive_launch`, `test_success`, `test_restart_of_an_interactive_stack` |
| unverified termination | the start loop's endpoint verify + cleanup stop | `test_unverified_termination` |

## Change

One module, `tests/golden/test_same_decision_across_entry_paths.py`. An `entry(path, op,
target, setup)` fixture runs the scenario on a fresh `kiss_box` per path and returns the
decision: `started`, the refusal class, the files changed (each path's bookkeeping excluded) and
the live owned processes. It also returns the rendering (exit code, HTTP status and admission,
job state, journal item), which every test asserts in full.

The rule the tags follow: the only `intended` differences between paths are RENDERING
differences (same decision, different words or format); every DECISION difference (started,
refusal class, files, live processes) is a `known defect` with its own id that names exactly
that difference.

- Every `lhpc` process a path runs (the CLI, the web's child, the job runner, the boot unit)
  enters through `main()` and builds its own `ControllerService()` the production way. The box's
  host (its FakeSystem, and the manifest a scenario names) reaches that construction through the
  production extension point `LHPC_SYSTEM_PROVIDER`. The web parent is the console's long-lived
  service, `box.svc`.
- The web path runs the real route, the real `spawn_start_job` parent, its real tracking and its
  real admission handshake. Substituted there: `Lifecycle.spawn_job` only. Instead of a detached
  process it runs the argv's `lhpc _stack-start …` through `main()` in a thread of this process
  and returns this process's pid, so the parent tracks a real identity, the child's real gate
  verifies it, and parent and child run concurrently. Not covered: what a separate process alone
  changes (its own module state, environment, stdout log file and signal disposition).
- Substituted on every path, besides the spawn: the host (above); `kiss_box`'s real TNC process
  and its kernel-read endpoint; `prior_boot`'s boot id and two boot-restore host gates; the
  firewall host readers in `test_firewall_gate` (class-wide, as a host's state is the same for
  every process on it); spies on `start`/`restart`/`boot_restore_run`/`spawn_start_job`, which
  delegate.
- `conftest.py` gains a `root` parameter on `kiss_box`, `KissBox.live()`, and a tag rule that
  accepts several finding ids (`known defect T3-F4, T1-F1:`).
- `tests/README.md`: one sentence.

## Simplicity

The batch text asks for "one parametrised test per decision". The simpler form is one plain
function per decision calling the shared `entry` fixture for each path. The four paths really
are different code, not permutations, and a disagreement is then asserted per path in one place.
No framework, registry or result type: the decision is a plain dict.

## Red-before

These tests are characterization: they pass on the base. The proof that a path deciding
differently is caught is a mutation (applied, run, reverted; never committed). In `main.py`
`_stack-start`, `ok = res.ok`, so the job runner counts MANUAL_REQUIRED as failure:
`test_interactive_launch` must fail (web and job then disagree with the CLI).

## Risks and how they are ruled out

- **In-process child under the web parent.** The gate needs the parent's job marker to name the
  child's pid and identity; this process is a real session member (`needs_session`). The child
  runs in a thread with its own `ControllerService`, gated by the parent's marker exactly as a
  separate process is; the test joins it and re-raises anything it raised.
- **Host leakage.** `LHPC_RUNTIME_ROOT` is set per path; `INVOCATION_ID` only on the boot path.
  The provider and every patch are undone by `monkeypatch` at the end of each test.
- **Classifying by summary.** It is used only where no typed field exists (finding T1-N1). The
  comparison is exact equality across paths, never a substring.

## Findings expected (recorded as `known defect`, not fixed)

- T3-F1: the web parent's admission refusal is untyped (reason text only).
- T3-F2: the CLI and web firewall-gate refusal names a stale apply script; the job and boot
  apply re-render it.
- T3-F3: boot restore skips an interactive main where the CLI, the console and the job runner
  launch it — decision differs (started, files).
- T3-F4: with a running band owner the web stops at its plan's `blockers` listing, while the
  CLI, the job runner and boot-restore refuse in the apply's preflight with the full refusal
  summary — the refusal class differs.
- T1-F1 reappears as a file difference (band owner, firewall gate).

## Open questions (recommendation)

1. Boot-restore passes over an interactive main (no plan item), the other paths launch it
   (T3-F3). Which one is right is a product decision for W4; recommendation: boot-restore
   replays it through the same start, so a reboot restores the dashboard's manual-start prompt
   as the other paths set it.
2. Restart counts MANUAL_REQUIRED as failure on every path, while start counts it as success.
   Recommendation: a product decision for W4; the test records today's agreement.

## Commits

```
4d72d4a T3: plan — the same decision across entry paths
1d11b4b T3: cross-path decision tests — CLI, console, job runner, boot-restore
```

## The report

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

## Round-2 interdiff (round 1 → round 2)

```diff
1:  05719db ! 1:  4d72d4a T3: plan — the same decision across entry paths
    @@ plans/PLAN-T3.md (new)
     +# PLAN-T3 — the same decision across entry paths
     +
     +Batch T3 of the architecture consolidation (next 0.x.y release). Test-only: `tests/` (plus this
    -+plan and the code-review files); `lhpc/` and `testlab/` unchanged. Base: `origin/integration/1.0`,
    ++plan and the code-review files); `lhpc/` and `testlab/` unchanged. Base: `origin/integration/0.12.0` (f257831),
     +after the T1 series on the same branch (it reuses T1's `tests/golden/conftest.py`).
     +
     +## Entry paths (file:line)
    @@ plans/PLAN-T3.md (new)
     +target, setup)` fixture runs the scenario on a fresh `kiss_box` per path and returns the
     +decision: `started`, the refusal class, the files changed (each path's bookkeeping excluded) and
     +the live owned processes. It also returns the rendering (exit code, HTTP status and admission,
    -+job state, journal item).
    -+
    -+- The web path runs the real route, the real `spawn_start_job` parent and the real handshake.
    -+  Only `Lifecycle.spawn_job` is replaced: it returns this process's pid, so the parent tracks a
    -+  real identity, and the child `_stack-start` runs in-process before the handshake reads its
    -+  marker.
    -+- Same `ControllerService` per path: `cli_main.ControllerService` returns the box's service.
    -+- `conftest.py` gains a `root` parameter on `kiss_box` and `KissBox.live()`.
    ++job state, journal item), which every test asserts in full.
    ++
    ++The rule the tags follow: the only `intended` differences between paths are RENDERING
    ++differences (same decision, different words or format); every DECISION difference (started,
    ++refusal class, files, live processes) is a `known defect` with its own id that names exactly
    ++that difference.
    ++
    ++- Every `lhpc` process a path runs (the CLI, the web's child, the job runner, the boot unit)
    ++  enters through `main()` and builds its own `ControllerService()` the production way. The box's
    ++  host (its FakeSystem, and the manifest a scenario names) reaches that construction through the
    ++  production extension point `LHPC_SYSTEM_PROVIDER`. The web parent is the console's long-lived
    ++  service, `box.svc`.
    ++- The web path runs the real route, the real `spawn_start_job` parent, its real tracking and its
    ++  real admission handshake. Substituted there: `Lifecycle.spawn_job` only. Instead of a detached
    ++  process it runs the argv's `lhpc _stack-start …` through `main()` in a thread of this process
    ++  and returns this process's pid, so the parent tracks a real identity, the child's real gate
    ++  verifies it, and parent and child run concurrently. Not covered: what a separate process alone
    ++  changes (its own module state, environment, stdout log file and signal disposition).
    ++- Substituted on every path, besides the spawn: the host (above); `kiss_box`'s real TNC process
    ++  and its kernel-read endpoint; `prior_boot`'s boot id and two boot-restore host gates; the
    ++  firewall host readers in `test_firewall_gate` (class-wide, as a host's state is the same for
    ++  every process on it); spies on `start`/`restart`/`boot_restore_run`/`spawn_start_job`, which
    ++  delegate.
    ++- `conftest.py` gains a `root` parameter on `kiss_box`, `KissBox.live()`, and a tag rule that
    ++  accepts several finding ids (`known defect T3-F4, T1-F1:`).
     +- `tests/README.md`: one sentence.
     +
     +## Simplicity
    @@ plans/PLAN-T3.md (new)
     +## Risks and how they are ruled out
     +
     +- **In-process child under the web parent.** The gate needs the parent's job marker to name the
    -+  child's pid and identity; this process is a real session member (`needs_session`). The only
    -+  substitution is the spawn itself.
    ++  child's pid and identity; this process is a real session member (`needs_session`). The child
    ++  runs in a thread with its own `ControllerService`, gated by the parent's marker exactly as a
    ++  separate process is; the test joins it and re-raises anything it raised.
     +- **Host leakage.** `LHPC_RUNTIME_ROOT` is set per path; `INVOCATION_ID` only on the boot path.
    -+  The firewall host readers are replaced on the scenario's box instance only.
    ++  The provider and every patch are undone by `monkeypatch` at the end of each test.
     +- **Classifying by summary.** It is used only where no typed field exists (finding T1-N1). The
     +  comparison is exact equality across paths, never a substring.
     +
    @@ plans/PLAN-T3.md (new)
     +- T3-F1: the web parent's admission refusal is untyped (reason text only).
     +- T3-F2: the CLI and web firewall-gate refusal names a stale apply script; the job and boot
     +  apply re-render it.
    ++- T3-F3: boot restore skips an interactive main where the CLI, the console and the job runner
    ++  launch it — decision differs (started, files).
    ++- T3-F4: with a running band owner the web stops at its plan's `blockers` listing, while the
    ++  CLI, the job runner and boot-restore refuse in the apply's preflight with the full refusal
    ++  summary — the refusal class differs.
     +- T1-F1 reappears as a file difference (band owner, firewall gate).
     +
     +## Open questions (recommendation)
     +
    -+1. Boot-restore passes over an interactive main (no plan item). Recommendation: keep. It is
    -+   documented as "interactive main — manual start", and the test asserts that skip.
    ++1. Boot-restore passes over an interactive main (no plan item), the other paths launch it
    ++   (T3-F3). Which one is right is a product decision for W4; recommendation: boot-restore
    ++   replays it through the same start, so a reboot restores the dashboard's manual-start prompt
    ++   as the other paths set it.
     +2. Restart counts MANUAL_REQUIRED as failure on every path, while start counts it as success.
     +   Recommendation: a product decision for W4; the test records today's agreement.
2:  12302e5 ! 2:  1d11b4b T3: cross-path decision tests — CLI, console, job runner, boot-restore
    @@ tests/README.md: in a file named after when or how a defect was found:
        steps: through `ORDER_SEAMS` in its `conftest.py`, which a refactor that renames a step updates.
     +  Beside it, `golden/test_same_decision_across_entry_paths.py` drives each start decision through
     +  every entry path (the CLI, the console route with its detached job, the job runner alone, the
    -+  boot-restore unit) and asserts the same decision — only the rendering may differ.
    ++  boot-restore unit) and compares the decisions: a rendering difference is `intended`, a decision
    ++  difference is a `known defect` naming one id per difference (`known defect T3-F4, T1-F1:`).
      
      ## The rules
      
     
      ## tests/golden/conftest.py ##
    +@@ tests/golden/conftest.py: def run_op(phases):
    + @pytest.fixture(autouse=True)
    + def _tagged(request):
    +     """Every golden case says what it records: its docstring opens with `intended:` or
    +-    `known defect <finding id>:`."""
    ++    `known defect <finding id>[, <finding id>…]:` (one id per difference it records)."""
    +     doc = (request.function.__doc__ or "").strip()
    +-    assert re.match(r"(intended|known defect [\w-]+):", doc), (
    ++    assert re.match(r"(intended|known defect [\w-]+(, [\w-]+)*):", doc), (
    +         f"{request.node.name}: a golden case's docstring must open with 'intended:' or "
    +-        "'known defect <id>:'")
    ++        "'known defect <id>[, <id>…]:'")
    + 
    + 
    + # --- the one box the lifecycle goldens run on ----------------------------------------------------
     @@ tests/golden/conftest.py: class KissBox:
          def owned(self) -> list[str]:
              return owned(self.root)
    @@ tests/golden/conftest.py: class KissBox:
     +        recs = [json.loads(p.read_text()) for p in d.glob("*.json")] if d.is_dir() else []
     +        return sorted(r["component"] for r in recs if _alive(r["pid"]))
     +
    +     def tnc_alive(self) -> bool:
    +         """The TNC's owned process is alive (observed in /proc: a zombie is not)."""
    +         d = self.root / "state" / "owned"
    +@@ tests/golden/conftest.py: class KissBox:
      
      @pytest.fixture
    - def kiss_box(tmp_path, monkeypatch, real_spawn, set_call):
    --    """`kiss_box(callsign=True)` → a KissBox: kiss installed and built, daemon READY on 433, the
    --    TNC's endpoint following its process, a callsign saved (unless callsign=False)."""
    + def kiss_box(tmp_path, monkeypatch, set_call):
    +-    """`kiss_box(callsign=True)` → a KissBox: kiss installed and built, daemon READY on 433, a
    +-    callsign saved (unless callsign=False); the TNC is a real process listening on a real loopback
    +-    port (`_TncEndpoint`). The start's endpoint wait is bounded at 3 s (production: 6 s) — what it
    ++    """`kiss_box(callsign=True, root=tmp_path)` → a KissBox: kiss installed and built, daemon
    ++    READY on 433, a callsign saved (unless callsign=False); the TNC is a real process listening on
    ++    a real loopback port (`_TncEndpoint`). The start's endpoint wait is bounded at 3 s (production: 6 s) — what it
    +     sees is observed. Every TNC process the box spawned is killed (its session) at teardown."""
    +     procs: list = []
    +     monkeypatch.setattr(ControllerService, "ENDPOINT_VERIFY_TIMEOUT_S", 3.0)
    + 
     -    def _make(*, callsign=True):
     -        (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
     -        (tmp_path / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
    -+    """`kiss_box(callsign=True, root=tmp_path)` → a KissBox: kiss installed and built, daemon
    -+    READY on 433, the TNC's endpoint following its process, a callsign saved (unless
    -+    callsign=False)."""
     +    def _make(*, callsign=True, root=None):
     +        root = Path(root or tmp_path)
     +        (root / "src" / "loraham-kiss-tnc").mkdir(parents=True)
     +        (root / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
              fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _READY})
    --        fake.listeners = _TncEndpoint(tmp_path)
    +         port = _free_port()
    +         fake.listeners = _TncEndpoint(port)
     -        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    -+        fake.listeners = _TncEndpoint(root)
     +        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
              svc.bootstrap(apply=True)
    +-        box = KissBox(tmp_path, fake, svc, port)
    ++        box = KissBox(root, fake, svc, port)
    +         spawn = box.spawn(procs)
              monkeypatch.setattr(ControllerService, "_lifecycle", lambda s: Lifecycle(
    -             s._paths, s.stacks(), s.config(), s._system, spawn=real_spawn))
    -         if callsign:
    -             set_call(svc)
    --        return KissBox(tmp_path, fake, svc)
    -+        return KissBox(root, fake, svc)
    -     return _make
    - 
    - 
    +             s._paths, s.stacks(), s.config(), s._system, spawn=spawn))
     
      ## tests/golden/test_same_decision_across_entry_paths.py (new) ##
     @@
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +
     +- `cli`  — `lhpc stack <op> <target> --yes` (`main()`: the plan, then the apply);
     +- `web`  — `POST /action` on the console: the plan in the route, then the real `spawn_start_job`
    -+           parent and handshake, with the spawned `lhpc _stack-start` child run here in-process;
    ++           parent and its real admission handshake, while the `lhpc _stack-start` child it
    ++           spawns runs concurrently in a thread of this process (see `entry`);
     +- `job`  — the detached job runner alone (`lhpc _stack-start …` over a tracked attempt);
     +- `boot` — the boot-restore unit (`lhpc autostart --run-service`) replaying a prior boot's record.
     +
    ++Every `lhpc` process a path runs (the CLI, the web's child, the job runner, the boot unit) enters
    ++through `main()` and builds its OWN `ControllerService()` the production way; the box's host (the
    ++FakeSystem, and the manifest a scenario names) reaches it through the production extension point
    ++`LHPC_SYSTEM_PROVIDER`, as a lab host reaches every process. The web parent is the console's
    ++long-lived service (`box.svc`).
    ++
     +The DECISION compared across paths: `started`, the `refusal` class (the typed `data` key where
     +one exists, `blockers` for a plan that lists owners, `manual_required`/`unverified`/`ok` from the
     +outcomes, else the deciding result's summary), the `files` the path left under the runtime root
     +(each path's own bookkeeping — attempt and job markers, the boot journal and its evidence, the
     +web session key and job logs, lock files — excluded) and the LHPC-owned processes `live`
    -+afterwards. Only the rendering may differ — exit code, flash or confirm page, job state, journal
    -+item — and each path's rendering is asserted as well.
    -+
    -+Every case's docstring opens with `intended:` or `known defect <id>:` (tests/README.md, `golden/`).
    ++afterwards. The RENDERING (exit code, HTTP status and admission, job state, journal item) is
    ++asserted per path as well. A rendering difference (same decision, different words or format) is
    ++`intended:`; a DECISION difference between paths is always a `known defect <id>:` naming exactly
    ++that difference — never `intended`.
     +"""
     +
     +import json
     +import os
     +import re
    ++import sys
    ++import threading
    ++import types
     +import uuid
     +
     +import pytest
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +@pytest.fixture
     +def entry(kiss_box, prior_boot, monkeypatch, csrf, tmp_path):
     +    """`entry(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
    -+    callsign=True)` → {started, refusal, files, live, render}. `setup(box)` prepares the scenario
    -+    on the path's fresh box (it may replace `box.svc`); `evidence` is the prior boot's record the
    -+    `boot` path replays."""
    -+    decided, booted, web = [], [], {}
    -+    depth = [0]
    ++    callsign=True)` → {started, refusal, files, live, render, next_commands, root}. `setup(box)`
    ++    prepares the scenario on the path's fresh box (it may set `box.manifest` and replace
    ++    `box.svc`); `evidence` is the prior boot's record the `boot` path replays.
    ++
    ++    Substituted for every path (all listed here):
    ++    - the host: `LHPC_SYSTEM_PROVIDER` names a provider serving the current box's FakeSystem and
    ++      manifest, so each process's own `ControllerService()` runs on the box (production reads the
    ++      same variable; unset, it builds the real system);
    ++    - `kiss_box`'s own substitutions (its real TNC process spawn and endpoint reader, the 3 s
    ++      endpoint wait) and `prior_boot`'s (this boot's id, the two boot-restore host gates);
    ++    - the spies on `ControllerService.start`/`restart`/`boot_restore_run`/`spawn_start_job`,
    ++      which delegate.
    ++    Substituted for the web path only: `Lifecycle.spawn_job` — instead of a detached process it
    ++    runs the argv's `lhpc _stack-start …` through `main()` in a thread of this process and returns
    ++    this process's pid, so the parent tracks a real identity and the child's real gate verifies
    ++    it. The parent's handshake, admission and tracking are the real ones and run concurrently
    ++    with the child. Not covered: what a separate process alone changes — its own module state,
    ++    environment, stdout log file and signal disposition."""
    ++    decided, booted, web, host = [], [], {}, {}
    ++    depth = threading.local()
     +
     +    def _spy(real, into):          # the outermost start/restart result; boot-restore's own
     +        def wrapper(self, *a, **k):
    -+            depth[0] += into is decided
    ++            n = getattr(depth, "n", 0)
    ++            depth.n = n + (into is decided)
     +            try:
     +                res = real(self, *a, **k)
     +            finally:
    -+                depth[0] -= into is decided
    -+            if into is booted or depth[0] == 0:
    ++                depth.n = n
    ++            if into is booted or n == 0:
     +                into.append(res)
     +            return res
     +        return wrapper
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +    monkeypatch.setattr(ControllerService, "boot_restore_run",
     +                        _spy(ControllerService.boot_restore_run, booted))
     +
    -+    # web: the parent spawns "this process"; its handshake runs the child it would have spawned
    -+    real_handshake, real_spawn_start = (ControllerService._web_admit_handshake,
    -+                                        ControllerService.spawn_start_job)
    ++    provider = types.ModuleType("golden_entry_host")
    ++    provider.build = lambda paths: types.SimpleNamespace(
    ++        system=host["box"].fake.system, manifest_path=host["box"].manifest, wrap_spawn=None)
    ++    monkeypatch.setitem(sys.modules, provider.__name__, provider)
    ++    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", f"{provider.__name__}:build")
    ++
    ++    def child(args):
    ++        try:
    ++            web["child_rc"] = cli_main.main(args)
    ++        except BaseException as exc:   # re-raised in the test's thread by `_web`
    ++            web["child_exc"] = exc
     +
     +    def spawn_job(self, name, argv, cwd, env=None):
    -+        web["argv"] = argv
    ++        assert argv[1:3] == ["-m", "lhpc"], argv
    ++        web["thread"] = threading.Thread(target=child, args=(argv[3:],), name="lhpc-web-child")
    ++        web["thread"].start()
     +        return f"{name}.log", os.getpid()
     +
    -+    def handshake(self, log, aid):
    -+        web["child_rc"] = cli_main.main(web["argv"][3:])
    -+        return real_handshake(self, log, aid)
    ++    real_spawn_start = ControllerService.spawn_start_job
     +
    -+    def spawn_start(self, *a, **k):
    ++    def spawn_start(self, *a, **k):            # a spy: records the parent's (log, admission, why)
     +        web["parent"] = real_spawn_start(self, *a, **k)
     +        return web["parent"]
     +    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
    -+    monkeypatch.setattr(ControllerService, "_web_admit_handshake", handshake)
     +    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn_start)
    -+    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "2")
    -+    monkeypatch.setenv("LHPC_WEB_ADMIT_TIMEOUT_S", "2")
     +
     +    def _run(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
     +             callsign=True):
     +        box = kiss_box(root=tmp_path / path, callsign=callsign)
    ++        box.manifest = None
    ++        host["box"] = box
     +        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(box.root))
    -+        monkeypatch.setattr(cli_main, "ControllerService", lambda: box.svc)
     +        if path == "boot":
     +            prior_boot(box.root, stack=evidence[0], comp=evidence[1], band=evidence[2])
     +        if setup:
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +    client = create_app(service_factory=lambda: box.svc).test_client()
     +    r = client.post("/action", data={"_csrf": csrf(client, "/"), "op": op, "target": target,
     +                                     "from": "dash"})
    ++    if "thread" in web:                    # the child the parent spawned, run to its end
    ++        web["thread"].join(timeout=120)
    ++        assert not web["thread"].is_alive(), "the web child did not finish"
    ++        if "child_exc" in web:
    ++            raise web["child_exc"]
     +    state = _job_state(box.svc, op, target)
     +    return ({"status": r.status_code, "admission": (web.get("parent") or (0, None))[1],
     +             "child_rc": web.get("child_rc"), "job": state},
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +
     +
     +def _firewall_pending(box):
    -+    """kiss saved to listen beyond loopback; the firewall installed but not verified current
    -+    (the host's integration state and its check are host readers — set on this box only)."""
    ++    """kiss saved to listen beyond loopback (the host's firewall state: `firewall_pending_host`)."""
     +    cfgmod.save_stack_config(box.svc._paths, "kiss", {"kiss_host": "0.0.0.0", "kiss_port": "9001"},
     +                             box.svc._config_band("kiss", ""))
     +    box.svc._invalidate_config()
    -+    box.svc._fw_integration_state = lambda: "present"
    -+    box.svc.firewall_status = lambda: {"config_ok": True, "live_ok": False}
    ++
    ++
    ++def firewall_pending_host(monkeypatch):
    ++    """The firewall installed but not verified current. The integration state and its check are
    ++    host readers: set for every service of the test (the parent's and each process's own), as a
    ++    host's state is the same for every process on it."""
    ++    monkeypatch.setattr(ControllerService, "_fw_integration_state", lambda self: "present")
    ++    monkeypatch.setattr(ControllerService, "firewall_status",
    ++                        lambda self: {"config_ok": True, "live_ok": False})
     +
     +
     +def _pending_config_journal(box):
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +def _ambiguous(box):
     +    """The two-component test manifest, with a flat legacy value both components declare."""
     +    from repo_paths import DATA
    -+    box.svc = ControllerService(manifest_path=DATA / "scope2_manifest.toml",
    -+                                system=box.fake.system, paths=Paths(runtime_root=box.root))
    ++    box.manifest = DATA / "scope2_manifest.toml"
    ++    box.svc = ControllerService(manifest_path=box.manifest, system=box.fake.system,
    ++                                paths=Paths(runtime_root=box.root))
     +    cfgmod.update_stack_config(box.svc._paths, "ostack2", {"rp": "LEGACY"})
     +
     +
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +# --- the decisions -----------------------------------------------------------------------------
     +
     +REFUSED_NOTHING = {"started": False, "files": [], "live": []}
    ++SUCCEEDED = {"cli": {"rc": 0},
    ++             "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
    ++             "job": {"rc": 0, "job": ("done", True)},
    ++             "boot": {"rc": 0, "journal": "done", "items": ["succeeded"], "skipped": []}}
    ++FAILED_AFTER_ADMISSION = {
    ++    "cli": {"rc": 1},
    ++    "web": {"status": 302, "admission": "admitted", "child_rc": 1, "job": ("failed", True)},
    ++    "job": {"rc": 1, "job": ("failed", True)},
    ++    "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}
     +
     +
     +def test_success(entry):
     +    """intended: a clean start succeeds on every path with the same files and the TNC live."""
     +    res = _all(entry, "start", "kiss")
     +    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
    -+    assert _renders(res) == {
    -+        "cli": {"rc": 0},
    -+        "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
    -+        "job": {"rc": 0, "job": ("done", True)},
    -+        "boot": {"rc": 0, "journal": "done", "items": ["succeeded"], "skipped": []}}
    ++    assert _renders(res) == SUCCEEDED
     +
     +
     +def test_missing_identity(entry):
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +
     +
     +def test_band_owner(entry):
    -+    """known defect T1-F1: a running band owner stops every path; the web asks first (the confirm
    -+    page, nothing written) while the CLI, the job runner and boot-restore refuse in the apply's
    -+    preflight AFTER resetting the band's feed floor — the same input leaves different files."""
    ++    """known defect T3-F4, T1-F1: a running band owner stops every path, but not with the same
    ++    decision. T3-F4 (refusal class): the web stops at its plan's `blockers` listing (the confirm
    ++    page) while the CLI, the job runner and boot-restore refuse in the apply's preflight with
    ++    "Cannot run 'kiss': meshtastic must be stopped first.". T1-F1 (files): those three reset the
    ++    band's feed floor before that refusal (state/daemon-feed-floor-433), the web writes nothing."""
     +    res = _all(entry, "start", "kiss", _band_owner)
     +    refusal = "Cannot run 'kiss': meshtastic must be stopped first."
     +    floor = {"started": False, "files": ["state/daemon-feed-floor-433"], "live": []}
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +        "web": {"refusal": "blockers", **REFUSED_NOTHING},
     +        "job": {"refusal": refusal, **floor},
     +        "boot": {"refusal": refusal, **floor}}
    -+    assert _renders(res)["web"] == {"status": 200, "admission": None, "child_rc": None,
    -+                                    "job": None}
    -+
    -+
    -+def test_firewall_gate(entry):
    -+    """known defect T3-F2: the firewall gate refuses on every path, but the CLI and the web
    -+    refuse at their plan (render=False) and name the apply script as the remedy WITHOUT
    -+    re-rendering it — the script they point at is stale and does not carry the newly exposed
    -+    listener — while the job runner and boot-restore refuse at the apply, which re-renders it
    -+    (and, T1-F1, has reset the feed floor): the same input leaves different files."""
    ++    assert _renders(res) == {
    ++        "cli": {"rc": 1},
    ++        "web": {"status": 200, "admission": None, "child_rc": None, "job": None},
    ++        "job": {"rc": 1, "job": ("failed", True)},
    ++        "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}
    ++
    ++
    ++def test_firewall_gate(entry, monkeypatch):
    ++    """known defect T3-F2, T1-F1: the firewall gate refuses on every path with the same class,
    ++    but the files differ. T3-F2: the CLI and the web refuse at their plan (render=False) and name
    ++    the apply script as the remedy WITHOUT re-rendering it — the script they point at is stale
    ++    and does not carry the newly exposed listener — while the job runner and boot-restore refuse
    ++    at the apply, which re-renders it (config/files/firewall/firewall-apply.sh). T1-F1: those two
    ++    have also reset the feed floor (state/daemon-feed-floor-433)."""
    ++    firewall_pending_host(monkeypatch)
     +    res = _all(entry, "start", "kiss", _firewall_pending)
     +    applied = {"started": False, "live": [], "refusal": "firewall_gate",
     +               "files": ["config/files/firewall/firewall-apply.sh", "state/daemon-feed-floor-433"]}
     +    assert _decisions(res) == {"cli": {"refusal": "firewall_gate", **REFUSED_NOTHING},
     +                               "web": {"refusal": "firewall_gate", **REFUSED_NOTHING},
     +                               "job": applied, "boot": applied}
    ++    assert _renders(res) == {
    ++        "cli": {"rc": 1},
    ++        "web": {"status": 302, "admission": None, "child_rc": None, "job": None},
    ++        "job": {"rc": 1, "job": ("failed", True)},
    ++        "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}
     +    exposed = '"id": "loraham-kiss-tnc.tcp-8001", "port": 9001'
     +    for path in PATHS:
     +        script = res[path]["root"] / "config" / "files" / "firewall" / "firewall-apply.sh"
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +    _same(res, refusal="Cannot start 'ostack2': run parameter 'rp' is ambiguous — declared by "
     +                       "more than one component and stored only as a flat value; set a "
     +                       "component-scoped value for 'dep'", **REFUSED_NOTHING)
    -+    assert _renders(res)["boot"] == {"rc": 0, "journal": "failed", "items": ["failed"],
    -+                                     "skipped": []}
    ++    assert _renders(res) == {**FAILED_AFTER_ADMISSION, "web": {
    ++        "status": 302, "admission": None, "child_rc": None, "job": None}}
     +
     +
     +def test_tx_without_high_power_permission(entry):
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +    spawned."""
     +    res = _all(entry, "start", "kiss", _tx_without_permission)
     +    _same(res, started=False, refusal="blocked", files=["state/daemon-feed-floor-433"], live=[])
    ++    assert _renders(res) == FAILED_AFTER_ADMISSION
     +
     +
     +def test_pending_config_journal(entry):
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +    res = _all(entry, "start", "kiss", _pending_config_journal)
     +    _same(res, started=True, refusal="ok", live=["loraham-kiss-tnc"],
     +          files=sorted(["config/stacks/kiss.toml", "state/config-txn.json", *KISS_STARTED]))
    ++    assert _renders(res) == SUCCEEDED
     +    for r in res.values():
     +        assert not (r["root"] / "state" / "config-txn.json").exists()
     +        assert "# torn" not in (r["root"] / "config" / "stacks" / "kiss.toml").read_text()
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +    path decides on it, so every path starts alike."""
     +    res = _all(entry, "start", "kiss", _damaged_client_index)
     +    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
    ++    assert _renders(res) == SUCCEEDED
     +
     +
     +def test_interactive_launch(entry):
    -+    """intended: an interactive main is never spawned; the CLI, the web and the job runner count
    -+    a start whose only shortfall is MANUAL_REQUIRED as success (exit 0, job done), with the same
    -+    files. Boot-restore never replays an interactive main: its plan skips it, by design."""
    ++    """known defect T3-F3: boot restore skips an interactive main where the CLI, the console and
    ++    the job runner launch it — decision differs. Those three run the start (config generated,
    ++    dashboard marker set, never spawned) and count its only shortfall, MANUAL_REQUIRED, as
    ++    success (exit 0, job done); boot-restore's plan skips the item ("interactive main — manual
    ++    start"): no start, no files."""
     +    res = _all(entry, "start", "chat", _chat_installed,
     +               evidence=("chat", "loraham-chat", "433"))
     +    manual = {"started": True, "refusal": "manual_required", "live": [],
    @@ tests/golden/test_same_decision_across_entry_paths.py (new)
     +    assert _decisions(res) == {"cli": manual, "web": manual, "job": manual,
     +                               "boot": {"started": False, "refusal": "ok", "files": [],
     +                                        "live": []}}
    -+    assert _renders(res) == {
    -+        "cli": {"rc": 0},
    -+        "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
    -+        "job": {"rc": 0, "job": ("done", True)},
    -+        "boot": {"rc": 0, "journal": "no-plan", "items": [],
    -+                 "skipped": ["interactive main — manual start"]}}
    ++    assert _renders(res) == {**SUCCEEDED, "boot": {
    ++        "rc": 0, "journal": "no-plan", "items": [], "skipped": ["interactive main — manual start"]}}
     +
     +
     +def test_unverified_termination(entry):
    -+    """intended: a TNC whose endpoint never comes up is cleaned up and reported UNVERIFIED on
    -+    every path — a failure everywhere (exit 1, job failed, boot item failed)."""
    ++    """intended: a TNC that is alive but never opens its endpoint is cleaned up and reported
    ++    UNVERIFIED on every path; each renders it as a failure — the CLI and the web's child exit 1,
    ++    the web and job attempts end `failed` (admitted), the boot item `failed` (the boot unit itself
    ++    exits 0: its driver completed)."""
     +    res = _all(entry, "start", "kiss", lambda b: b.endpoint_never_up())
     +    _same(res, started=False, refusal="unverified", live=[],
     +          files=["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433"])
    -+    assert {p: r["render"].get("rc") for p, r in res.items()} == {
    -+        "cli": 1, "web": None, "job": 1, "boot": 0}
    -+    assert res["web"]["render"]["job"] == ("failed", True)
    -+    assert res["boot"]["render"]["items"] == ["failed"]
    ++    assert _renders(res) == FAILED_AFTER_ADMISSION
     +
     +
     +def test_restart_of_an_interactive_stack(entry):
```

## Full amended diff of the code commits

```diff
diff --git a/tests/README.md b/tests/README.md
index 47b4a6c..be74974 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -51,6 +51,10 @@ in a file named after when or how a defect was found:
   contract) or `known defect <finding id>:` (recorded as is, changed only by the fix of that
   finding). Pinning the step order is its purpose, so it is the one place that names coordinator
   steps: through `ORDER_SEAMS` in its `conftest.py`, which a refactor that renames a step updates.
+  Beside it, `golden/test_same_decision_across_entry_paths.py` drives each start decision through
+  every entry path (the CLI, the console route with its detached job, the job runner alone, the
+  boot-restore unit) and compares the decisions: a rendering difference is `intended`, a decision
+  difference is a `known defect` naming one id per difference (`known defect T3-F4, T1-F1:`).
 
 ## The rules
 
diff --git a/tests/golden/conftest.py b/tests/golden/conftest.py
index 046d07b..9916aba 100644
--- a/tests/golden/conftest.py
+++ b/tests/golden/conftest.py
@@ -200,11 +200,11 @@ def run_op(phases):
 @pytest.fixture(autouse=True)
 def _tagged(request):
     """Every golden case says what it records: its docstring opens with `intended:` or
-    `known defect <finding id>:`."""
+    `known defect <finding id>[, <finding id>…]:` (one id per difference it records)."""
     doc = (request.function.__doc__ or "").strip()
-    assert re.match(r"(intended|known defect [\w-]+):", doc), (
+    assert re.match(r"(intended|known defect [\w-]+(, [\w-]+)*):", doc), (
         f"{request.node.name}: a golden case's docstring must open with 'intended:' or "
-        "'known defect <id>:'")
+        "'known defect <id>[, <id>…]:'")
 
 
 # --- the one box the lifecycle goldens run on ----------------------------------------------------
@@ -292,6 +292,12 @@ class KissBox:
     def owned(self) -> list[str]:
         return owned(self.root)
 
+    def live(self) -> list[str]:
+        """The components whose LHPC-owned process is alive (a prior boot's record is not)."""
+        d = self.root / "state" / "owned"
+        recs = [json.loads(p.read_text()) for p in d.glob("*.json")] if d.is_dir() else []
+        return sorted(r["component"] for r in recs if _alive(r["pid"]))
+
     def tnc_alive(self) -> bool:
         """The TNC's owned process is alive (observed in /proc: a zombie is not)."""
         d = self.root / "state" / "owned"
@@ -301,22 +307,23 @@ class KissBox:
 
 @pytest.fixture
 def kiss_box(tmp_path, monkeypatch, set_call):
-    """`kiss_box(callsign=True)` → a KissBox: kiss installed and built, daemon READY on 433, a
-    callsign saved (unless callsign=False); the TNC is a real process listening on a real loopback
-    port (`_TncEndpoint`). The start's endpoint wait is bounded at 3 s (production: 6 s) — what it
+    """`kiss_box(callsign=True, root=tmp_path)` → a KissBox: kiss installed and built, daemon
+    READY on 433, a callsign saved (unless callsign=False); the TNC is a real process listening on
+    a real loopback port (`_TncEndpoint`). The start's endpoint wait is bounded at 3 s (production: 6 s) — what it
     sees is observed. Every TNC process the box spawned is killed (its session) at teardown."""
     procs: list = []
     monkeypatch.setattr(ControllerService, "ENDPOINT_VERIFY_TIMEOUT_S", 3.0)
 
-    def _make(*, callsign=True):
-        (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
-        (tmp_path / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
+    def _make(*, callsign=True, root=None):
+        root = Path(root or tmp_path)
+        (root / "src" / "loraham-kiss-tnc").mkdir(parents=True)
+        (root / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
         fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _READY})
         port = _free_port()
         fake.listeners = _TncEndpoint(port)
-        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
+        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
         svc.bootstrap(apply=True)
-        box = KissBox(tmp_path, fake, svc, port)
+        box = KissBox(root, fake, svc, port)
         spawn = box.spawn(procs)
         monkeypatch.setattr(ControllerService, "_lifecycle", lambda s: Lifecycle(
             s._paths, s.stacks(), s.config(), s._system, spawn=spawn))
diff --git a/tests/golden/test_same_decision_across_entry_paths.py b/tests/golden/test_same_decision_across_entry_paths.py
new file mode 100644
index 0000000..185a1c4
--- /dev/null
+++ b/tests/golden/test_same_decision_across_entry_paths.py
@@ -0,0 +1,525 @@
+"""Same input, same decision — whichever entry path runs it.
+
+One scenario is driven through every entry path that can start a stack, each on a fresh box:
+
+- `cli`  — `lhpc stack <op> <target> --yes` (`main()`: the plan, then the apply);
+- `web`  — `POST /action` on the console: the plan in the route, then the real `spawn_start_job`
+           parent and its real admission handshake, while the `lhpc _stack-start` child it
+           spawns runs concurrently in a thread of this process (see `entry`);
+- `job`  — the detached job runner alone (`lhpc _stack-start …` over a tracked attempt);
+- `boot` — the boot-restore unit (`lhpc autostart --run-service`) replaying a prior boot's record.
+
+Every `lhpc` process a path runs (the CLI, the web's child, the job runner, the boot unit) enters
+through `main()` and builds its OWN `ControllerService()` the production way; the box's host (the
+FakeSystem, and the manifest a scenario names) reaches it through the production extension point
+`LHPC_SYSTEM_PROVIDER`, as a lab host reaches every process. The web parent is the console's
+long-lived service (`box.svc`).
+
+The DECISION compared across paths: `started`, the `refusal` class (the typed `data` key where
+one exists, `blockers` for a plan that lists owners, `manual_required`/`unverified`/`ok` from the
+outcomes, else the deciding result's summary), the `files` the path left under the runtime root
+(each path's own bookkeeping — attempt and job markers, the boot journal and its evidence, the
+web session key and job logs, lock files — excluded) and the LHPC-owned processes `live`
+afterwards. The RENDERING (exit code, HTTP status and admission, job state, journal item) is
+asserted per path as well. A rendering difference (same decision, different words or format) is
+`intended:`; a DECISION difference between paths is always a `known defect <id>:` naming exactly
+that difference — never `intended`.
+"""
+
+import json
+import os
+import re
+import sys
+import threading
+import types
+import uuid
+
+import pytest
+
+from lhpc.adapters.cli import main as cli_main
+from lhpc.adapters.web.app import create_app
+from lhpc.core import config as cfgmod
+from lhpc.core import jobresult, jobs, procident
+from lhpc.core.lifecycle import Lifecycle
+from lhpc.core.outcomes import manual_required_only
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import CommandResult
+from lhpc.core.services import ControllerService
+
+pytestmark = pytest.mark.needs_session
+
+PATHS = ("cli", "web", "job", "boot")
+_BOOKKEEPING = re.compile(r"^(state/(jobresults|jobs|owned|locks)/|state/boot-restore\.json$|"
+                          r"logs/web-|config/secrets/web_session\.key$|config/\.lock$)")
+_TYPED = ("admission_blocked", "enforce_fields", "firewall_gate", "reason")
+KISS_STARTED = ["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433",
+                "state/running/kiss.band"]
+
+
+def _classify(res):
+    """The refusal class of the result that decided."""
+    data = res.data or {}
+    for key in _TYPED:
+        if data.get(key):
+            return key
+    if data.get("blockers"):
+        return "blockers"
+    if res.ok:
+        return "ok"
+    if res.results and manual_required_only(res.results):
+        return "manual_required"
+    for outcome in ("unverified", "blocked"):
+        if any(r.outcome.value == outcome for r in res.results):
+            return outcome
+    return res.summary
+
+
+def _files(root):
+    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
+            if p.is_file() and not _BOOKKEEPING.match(str(p.relative_to(root)))}
+
+
+@pytest.fixture
+def entry(kiss_box, prior_boot, monkeypatch, csrf, tmp_path):
+    """`entry(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
+    callsign=True)` → {started, refusal, files, live, render, next_commands, root}. `setup(box)`
+    prepares the scenario on the path's fresh box (it may set `box.manifest` and replace
+    `box.svc`); `evidence` is the prior boot's record the `boot` path replays.
+
+    Substituted for every path (all listed here):
+    - the host: `LHPC_SYSTEM_PROVIDER` names a provider serving the current box's FakeSystem and
+      manifest, so each process's own `ControllerService()` runs on the box (production reads the
+      same variable; unset, it builds the real system);
+    - `kiss_box`'s own substitutions (its real TNC process spawn and endpoint reader, the 3 s
+      endpoint wait) and `prior_boot`'s (this boot's id, the two boot-restore host gates);
+    - the spies on `ControllerService.start`/`restart`/`boot_restore_run`/`spawn_start_job`,
+      which delegate.
+    Substituted for the web path only: `Lifecycle.spawn_job` — instead of a detached process it
+    runs the argv's `lhpc _stack-start …` through `main()` in a thread of this process and returns
+    this process's pid, so the parent tracks a real identity and the child's real gate verifies
+    it. The parent's handshake, admission and tracking are the real ones and run concurrently
+    with the child. Not covered: what a separate process alone changes — its own module state,
+    environment, stdout log file and signal disposition."""
+    decided, booted, web, host = [], [], {}, {}
+    depth = threading.local()
+
+    def _spy(real, into):          # the outermost start/restart result; boot-restore's own
+        def wrapper(self, *a, **k):
+            n = getattr(depth, "n", 0)
+            depth.n = n + (into is decided)
+            try:
+                res = real(self, *a, **k)
+            finally:
+                depth.n = n
+            if into is booted or n == 0:
+                into.append(res)
+            return res
+        return wrapper
+    monkeypatch.setattr(ControllerService, "start", _spy(ControllerService.start, decided))
+    monkeypatch.setattr(ControllerService, "restart", _spy(ControllerService.restart, decided))
+    monkeypatch.setattr(ControllerService, "boot_restore_run",
+                        _spy(ControllerService.boot_restore_run, booted))
+
+    provider = types.ModuleType("golden_entry_host")
+    provider.build = lambda paths: types.SimpleNamespace(
+        system=host["box"].fake.system, manifest_path=host["box"].manifest, wrap_spawn=None)
+    monkeypatch.setitem(sys.modules, provider.__name__, provider)
+    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", f"{provider.__name__}:build")
+
+    def child(args):
+        try:
+            web["child_rc"] = cli_main.main(args)
+        except BaseException as exc:   # re-raised in the test's thread by `_web`
+            web["child_exc"] = exc
+
+    def spawn_job(self, name, argv, cwd, env=None):
+        assert argv[1:3] == ["-m", "lhpc"], argv
+        web["thread"] = threading.Thread(target=child, args=(argv[3:],), name="lhpc-web-child")
+        web["thread"].start()
+        return f"{name}.log", os.getpid()
+
+    real_spawn_start = ControllerService.spawn_start_job
+
+    def spawn_start(self, *a, **k):            # a spy: records the parent's (log, admission, why)
+        web["parent"] = real_spawn_start(self, *a, **k)
+        return web["parent"]
+    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
+    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn_start)
+
+    def _run(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
+             callsign=True):
+        box = kiss_box(root=tmp_path / path, callsign=callsign)
+        box.manifest = None
+        host["box"] = box
+        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(box.root))
+        if path == "boot":
+            prior_boot(box.root, stack=evidence[0], comp=evidence[1], band=evidence[2])
+        if setup:
+            setup(box)
+        box.svc.invalidate_snapshot()
+        before = _files(box.root)
+        decided.clear()
+        booted.clear()
+        web.clear()
+        render, started = _DRIVE[path](box, op, target, web, csrf, monkeypatch)
+        after = _files(box.root)
+        parent = web.get("parent")
+        if parent and parent[1] == "blocked" and "child_rc" not in web:
+            refusal = parent[2]            # the web parent refused: no child ever decided
+        elif decided:
+            refusal = _classify(decided[-1])
+        else:                              # boot-restore planned no start at all
+            refusal = _classify(booted[-1])
+        told = list(decided[-1].next_commands) if decided else []
+        return {"started": started, "refusal": refusal, "live": box.live(), "render": render,
+                "files": sorted(k for k in before.keys() | after.keys()
+                                if before.get(k) != after.get(k)),
+                "next_commands": told, "root": box.root}
+    return _run
+
+
+def _job_state(svc, op, target):
+    rec = jobresult.read_one(svc._paths, f"web-{op}-{target}.log")
+    return None if rec is None else (rec["state"], rec["admitted"])
+
+
+def _cli(box, op, target, web, csrf, monkeypatch):
+    rc = cli_main.main(["stack", op, target, "--yes"])
+    return {"rc": rc}, rc == 0
+
+
+def _job(box, op, target, web, csrf, monkeypatch):
+    """The attempt as the web parent leaves it — reserved, its job marker naming this process
+    (the in-process child) — then the runner, with the band the parent freezes."""
+    svc = box.svc
+    log, aid = f"web-{op}-{target}.log", uuid.uuid4().hex
+    assert jobresult.reserve(svc._paths, log, aid, op, target, svc.stack_of(target) or target, [])
+    assert jobs.write_job_marker(svc._paths, log, os.getpid(), target, op,
+                                 ident=procident.proc_identity(os.getpid()), attempt_id=aid)
+    argv = ["_stack-start", target, "--web-result", log, "--attempt-id", aid]
+    band = svc.operation_band(target, "")
+    argv += (["--band", band] if band else []) + (["--restart"] if op == "restart" else [])
+    rc = cli_main.main(argv)
+    state = _job_state(svc, op, target)
+    return {"rc": rc, "job": state}, state[0] == "done"
+
+
+def _boot(box, op, target, web, csrf, monkeypatch):
+    monkeypatch.setenv("INVOCATION_ID", "golden")
+    rc = cli_main.main(["autostart", "--run-service"])
+    p = box.root / "state" / "boot-restore.json"
+    j = json.loads(p.read_text()) if p.exists() else {"state": None, "items": [], "skipped": []}
+    items = [i["state"] for i in j["items"]]
+    return ({"rc": rc, "journal": j["state"], "items": items,
+             "skipped": [s["reason"] for s in j.get("skipped", [])]}, items == ["succeeded"])
+
+
+def _web(box, op, target, web, csrf, monkeypatch):
+    client = create_app(service_factory=lambda: box.svc).test_client()
+    r = client.post("/action", data={"_csrf": csrf(client, "/"), "op": op, "target": target,
+                                     "from": "dash"})
+    if "thread" in web:                    # the child the parent spawned, run to its end
+        web["thread"].join(timeout=120)
+        assert not web["thread"].is_alive(), "the web child did not finish"
+        if "child_exc" in web:
+            raise web["child_exc"]
+    state = _job_state(box.svc, op, target)
+    return ({"status": r.status_code, "admission": (web.get("parent") or (0, None))[1],
+             "child_rc": web.get("child_rc"), "job": state},
+            bool(state and state[0] == "done"))
+
+
+_DRIVE = {"cli": _cli, "web": _web, "job": _job, "boot": _boot}
+
+
+def _all(entry, op, target, setup=None, **kw):
+    paths = PATHS if op == "start" else PATHS[:3]          # boot-restore only ever starts
+    return {p: entry(p, op, target, setup, **kw) for p in paths}
+
+
+def _decisions(results):
+    return {p: {k: r[k] for k in ("started", "refusal", "files", "live")}
+            for p, r in results.items()}
+
+
+def _same(results, **decision):
+    """Every path decided exactly `decision`."""
+    assert _decisions(results) == {p: decision for p in results}
+
+
+def _renders(results):
+    return {p: r["render"] for p, r in results.items()}
+
+
+# --- scenario set-ups --------------------------------------------------------------------------
+
+class _EveryStepSucceeds(dict):
+    def get(self, argv, default=None):
+        return CommandResult(0, "", "")
+
+
+def _graywolf_built(box):
+    """graywolf — a licensed stack, its callsign enforced — built by the real build with every
+    step answered rc 0, so the console's installed/built pre-check passes like the others."""
+    commands, box.fake.commands = box.fake.commands, _EveryStepSucceeds()
+    assert box.svc.build("graywolf", apply=True).ok
+    box.fake.commands = commands
+
+
+def _chat_installed(box):
+    (box.root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
+    (box.root / "src" / "LoRaHAM_Daemon" / "loraham_chat").write_text("#bin")
+
+
+def _band_owner(box):
+    box.fake.cmdlines_data[300] = ["meshtasticd"]
+
+
+def _firewall_pending(box):
+    """kiss saved to listen beyond loopback (the host's firewall state: `firewall_pending_host`)."""
+    cfgmod.save_stack_config(box.svc._paths, "kiss", {"kiss_host": "0.0.0.0", "kiss_port": "9001"},
+                             box.svc._config_band("kiss", ""))
+    box.svc._invalidate_config()
+
+
+def firewall_pending_host(monkeypatch):
+    """The firewall installed but not verified current. The integration state and its check are
+    host readers: set for every service of the test (the parent's and each process's own), as a
+    host's state is the same for every process on it."""
+    monkeypatch.setattr(ControllerService, "_fw_integration_state", lambda self: "present")
+    monkeypatch.setattr(ControllerService, "firewall_status",
+                        lambda self: {"config_ok": True, "live_ok": False})
+
+
+def _pending_config_journal(box):
+    """A Settings save crashed mid-write: kiss.toml torn, its journal (with the pre-image) left."""
+    f = box.root / "config" / "stacks" / "kiss.toml"
+    cfgmod.save_stack_config(box.svc._paths, "kiss", {"kiss_port": "8001"}, "")
+    pre = f.read_text()
+    f.write_text("# torn\n")
+    (box.root / "state" / "config-txn.json").write_text(json.dumps({"version": 1, "targets": [
+        {"kind": "stack", "rel": "config/stacks/kiss.toml", "pre": pre, "existed": True,
+         "mode": 0o644}]}))
+
+
+def _damaged_client_index(box):
+    p = box.root / "config" / "tls" / "client-ca" / "client-index.json"
+    p.parent.mkdir(parents=True, exist_ok=True)
+    p.write_bytes(b"{not json")
+
+
+def _tx_without_permission(box):
+    """kiss saved to transmit at POWER=20 (the switch saved on) while the running daemon does
+    not report the high-power permission — the explicit TX authorisation the start gates on."""
+    assert box.svc.save_config_bundle("daemon", values={"hipower_433": "on"}).ok
+    cfgmod.update_stack_config(box.svc._paths, "kiss", {"dp_433_POWER": "20"})
+    box.svc._invalidate_config()
+
+
+def _ambiguous(box):
+    """The two-component test manifest, with a flat legacy value both components declare."""
+    from repo_paths import DATA
+    box.manifest = DATA / "scope2_manifest.toml"
+    box.svc = ControllerService(manifest_path=box.manifest, system=box.fake.system,
+                                paths=Paths(runtime_root=box.root))
+    cfgmod.update_stack_config(box.svc._paths, "ostack2", {"rp": "LEGACY"})
+
+
+
+# --- the decisions -----------------------------------------------------------------------------
+
+REFUSED_NOTHING = {"started": False, "files": [], "live": []}
+SUCCEEDED = {"cli": {"rc": 0},
+             "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
+             "job": {"rc": 0, "job": ("done", True)},
+             "boot": {"rc": 0, "journal": "done", "items": ["succeeded"], "skipped": []}}
+FAILED_AFTER_ADMISSION = {
+    "cli": {"rc": 1},
+    "web": {"status": 302, "admission": "admitted", "child_rc": 1, "job": ("failed", True)},
+    "job": {"rc": 1, "job": ("failed", True)},
+    "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}
+
+
+def test_success(entry):
+    """intended: a clean start succeeds on every path with the same files and the TNC live."""
+    res = _all(entry, "start", "kiss")
+    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
+    assert _renders(res) == SUCCEEDED
+
+
+def test_missing_identity(entry):
+    """intended: a licensed stack with no callsign is refused by every path before anything is
+    written — the CLI and the web at their plan, the job and boot-restore at the apply's recheck,
+    before the hook (the attempt is not admitted, the boot item stays pending)."""
+    res = _all(entry, "start", "graywolf", _graywolf_built, callsign=False,
+               evidence=("graywolf", "graywolf", "433"))
+    _same(res, refusal="enforce_fields", **REFUSED_NOTHING)
+    assert _renders(res) == {
+        "cli": {"rc": 1},
+        "web": {"status": 302, "admission": None, "child_rc": None, "job": None},
+        "job": {"rc": 1, "job": ("failed", False)},
+        "boot": {"rc": 0, "journal": "failed", "items": ["pending"], "skipped": []}}
+
+
+def test_admission_refusal(entry, uninstall_guard):
+    """known defect T3-F1: every path refuses with nothing written, but the web parent's refusal
+    (`spawn_start_job` → `(None, "blocked", reason)`) carries the reason text only — the typed
+    class `admission_blocked` the CLI, the job runner and boot-restore return is lost there."""
+    res = _all(entry, "start", "kiss", lambda b: uninstall_guard(b.root))
+    reason = ("A controller uninstall is in progress (.lhpc-uninstalling) — refusing to start "
+              "new work. Let it finish, or recover it.")
+    assert _decisions(res) == {
+        "cli": {"refusal": "admission_blocked", **REFUSED_NOTHING},
+        "web": {"refusal": reason, **REFUSED_NOTHING},
+        "job": {"refusal": "admission_blocked", **REFUSED_NOTHING},
+        "boot": {"refusal": "admission_blocked", **REFUSED_NOTHING}}
+    assert _renders(res) == {
+        "cli": {"rc": 1},
+        "web": {"status": 302, "admission": "blocked", "child_rc": None, "job": None},
+        "job": {"rc": 1, "job": ("failed", False)},
+        "boot": {"rc": 1, "journal": None, "items": [], "skipped": []}}
+
+
+def test_interrupted_install(entry, interrupted_install):
+    """intended: an unresolved source-transaction journal refuses the start on every path, under
+    the index lock, before the hook — nothing written (the boot item stays pending)."""
+    res = _all(entry, "start", "kiss", lambda b: interrupted_install(b.root))
+    _same(res, refusal="Cannot start 'kiss': an unresolved source-transaction journal is present "
+                       "— resolve it before starting", **REFUSED_NOTHING)
+    assert _renders(res) == {
+        "cli": {"rc": 1},
+        "web": {"status": 302, "admission": "blocked", "child_rc": 1, "job": ("failed", False)},
+        "job": {"rc": 1, "job": ("failed", False)},
+        "boot": {"rc": 0, "journal": "failed", "items": ["pending"], "skipped": []}}
+
+
+def test_band_owner(entry):
+    """known defect T3-F4, T1-F1: a running band owner stops every path, but not with the same
+    decision. T3-F4 (refusal class): the web stops at its plan's `blockers` listing (the confirm
+    page) while the CLI, the job runner and boot-restore refuse in the apply's preflight with
+    "Cannot run 'kiss': meshtastic must be stopped first.". T1-F1 (files): those three reset the
+    band's feed floor before that refusal (state/daemon-feed-floor-433), the web writes nothing."""
+    res = _all(entry, "start", "kiss", _band_owner)
+    refusal = "Cannot run 'kiss': meshtastic must be stopped first."
+    floor = {"started": False, "files": ["state/daemon-feed-floor-433"], "live": []}
+    assert _decisions(res) == {
+        "cli": {"refusal": refusal, **floor},
+        "web": {"refusal": "blockers", **REFUSED_NOTHING},
+        "job": {"refusal": refusal, **floor},
+        "boot": {"refusal": refusal, **floor}}
+    assert _renders(res) == {
+        "cli": {"rc": 1},
+        "web": {"status": 200, "admission": None, "child_rc": None, "job": None},
+        "job": {"rc": 1, "job": ("failed", True)},
+        "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}
+
+
+def test_firewall_gate(entry, monkeypatch):
+    """known defect T3-F2, T1-F1: the firewall gate refuses on every path with the same class,
+    but the files differ. T3-F2: the CLI and the web refuse at their plan (render=False) and name
+    the apply script as the remedy WITHOUT re-rendering it — the script they point at is stale
+    and does not carry the newly exposed listener — while the job runner and boot-restore refuse
+    at the apply, which re-renders it (config/files/firewall/firewall-apply.sh). T1-F1: those two
+    have also reset the feed floor (state/daemon-feed-floor-433)."""
+    firewall_pending_host(monkeypatch)
+    res = _all(entry, "start", "kiss", _firewall_pending)
+    applied = {"started": False, "live": [], "refusal": "firewall_gate",
+               "files": ["config/files/firewall/firewall-apply.sh", "state/daemon-feed-floor-433"]}
+    assert _decisions(res) == {"cli": {"refusal": "firewall_gate", **REFUSED_NOTHING},
+                               "web": {"refusal": "firewall_gate", **REFUSED_NOTHING},
+                               "job": applied, "boot": applied}
+    assert _renders(res) == {
+        "cli": {"rc": 1},
+        "web": {"status": 302, "admission": None, "child_rc": None, "job": None},
+        "job": {"rc": 1, "job": ("failed", True)},
+        "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}
+    exposed = '"id": "loraham-kiss-tnc.tcp-8001", "port": 9001'
+    for path in PATHS:
+        script = res[path]["root"] / "config" / "files" / "firewall" / "firewall-apply.sh"
+        assert f"sudo bash {script}" in res[path]["next_commands"]
+        assert (exposed in script.read_text()) is (path in ("job", "boot")), path
+
+
+def test_config_ambiguity(entry):
+    """intended: an ambiguous flat value refuses the start on every path with nothing written —
+    at the plan (CLI, web) or the apply's preflight (job, boot-restore, after the hook: the boot
+    item is consumed as failed)."""
+    res = _all(entry, "start", "ostack2", _ambiguous, evidence=("ostack2", "tgt", "433"))
+    _same(res, refusal="Cannot start 'ostack2': run parameter 'rp' is ambiguous — declared by "
+                       "more than one component and stored only as a flat value; set a "
+                       "component-scoped value for 'dep'", **REFUSED_NOTHING)
+    assert _renders(res) == {**FAILED_AFTER_ADMISSION, "web": {
+        "status": 302, "admission": None, "child_rc": None, "job": None}}
+
+
+def test_tx_without_high_power_permission(entry):
+    """intended: a stack saved to transmit at POWER=20 without the running daemon's high-power
+    permission is BLOCKED on every path (no path's plan checks it; each apply does), nothing
+    spawned."""
+    res = _all(entry, "start", "kiss", _tx_without_permission)
+    _same(res, started=False, refusal="blocked", files=["state/daemon-feed-floor-433"], live=[])
+    assert _renders(res) == FAILED_AFTER_ADMISSION
+
+
+def test_pending_config_journal(entry):
+    """intended: a config journal a crashed save left behind is finished by every path's process
+    at startup (the CLI, the web's child, the job runner, the boot unit) — the torn file restored,
+    the journal gone — and the start then succeeds alike."""
+    res = _all(entry, "start", "kiss", _pending_config_journal)
+    _same(res, started=True, refusal="ok", live=["loraham-kiss-tnc"],
+          files=sorted(["config/stacks/kiss.toml", "state/config-txn.json", *KISS_STARTED]))
+    assert _renders(res) == SUCCEEDED
+    for r in res.values():
+        assert not (r["root"] / "state" / "config-txn.json").exists()
+        assert "# torn" not in (r["root"] / "config" / "stacks" / "kiss.toml").read_text()
+
+
+def test_damaged_client_index(entry):
+    """intended: a damaged client-certificate index gates certificate operations only — no start
+    path decides on it, so every path starts alike."""
+    res = _all(entry, "start", "kiss", _damaged_client_index)
+    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
+    assert _renders(res) == SUCCEEDED
+
+
+def test_interactive_launch(entry):
+    """known defect T3-F3: boot restore skips an interactive main where the CLI, the console and
+    the job runner launch it — decision differs. Those three run the start (config generated,
+    dashboard marker set, never spawned) and count its only shortfall, MANUAL_REQUIRED, as
+    success (exit 0, job done); boot-restore's plan skips the item ("interactive main — manual
+    start"): no start, no files."""
+    res = _all(entry, "start", "chat", _chat_installed,
+               evidence=("chat", "loraham-chat", "433"))
+    manual = {"started": True, "refusal": "manual_required", "live": [],
+              "files": ["config/files/lorachat.conf", "state/daemon-feed-floor-433",
+                        "state/interactive/chat.show"]}
+    assert _decisions(res) == {"cli": manual, "web": manual, "job": manual,
+                               "boot": {"started": False, "refusal": "ok", "files": [],
+                                        "live": []}}
+    assert _renders(res) == {**SUCCEEDED, "boot": {
+        "rc": 0, "journal": "no-plan", "items": [], "skipped": ["interactive main — manual start"]}}
+
+
+def test_unverified_termination(entry):
+    """intended: a TNC that is alive but never opens its endpoint is cleaned up and reported
+    UNVERIFIED on every path; each renders it as a failure — the CLI and the web's child exit 1,
+    the web and job attempts end `failed` (admitted), the boot item `failed` (the boot unit itself
+    exits 0: its driver completed)."""
+    res = _all(entry, "start", "kiss", lambda b: b.endpoint_never_up())
+    _same(res, started=False, refusal="unverified", live=[],
+          files=["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433"])
+    assert _renders(res) == FAILED_AFTER_ADMISSION
+
+
+def test_restart_of_an_interactive_stack(entry):
+    """intended: for a restart the CLI, the web and the job runner agree that a MANUAL_REQUIRED
+    result is a failure (exit 1, job failed) — unlike a start (above), where all three count it
+    as success. Boot-restore never restarts."""
+    res = _all(entry, "restart", "chat", _chat_installed)
+    _same(res, started=False, refusal="manual_required", live=[],
+          files=["config/files/lorachat.conf", "state/daemon-feed-floor-433",
+                 "state/interactive/chat.show"])
+    assert _renders(res) == {
+        "cli": {"rc": 1},
+        "web": {"status": 302, "admission": "admitted", "child_rc": 1, "job": ("failed", True)},
+        "job": {"rc": 1, "job": ("failed", True)}}
```
