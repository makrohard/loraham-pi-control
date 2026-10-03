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
