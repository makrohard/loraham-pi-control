# Gate-1 code review request — T3, round 3

Please judge round 3 of batch T3 of the loraham-pi-control architecture consolidation: tests that drive the same start/restart scenario through every entry path (the CLI, the console route with its detached job, the job runner alone, the boot-restore unit) and compare the decision — started or not, the refusal class, the files touched, the live processes; a rendering difference is intended, every decision difference is recorded as a known defect with its own id. Round 2 was judged RED a second time on finding B: the web path's "child" was a thread sharing the test process's pid, so a parent that tracks its own pid instead of the child's passed. The section "Round 2 RED → round 3" below gives the finding, its verification against the repository, what changed (the child is now a separate process spawned through the production detached entry), the negative control's result and the measured numbers. Judge the amended plan (below) and each amended code commit (the full amended diff is at the end, preceded by the round-3 interdiff of the T3 files) against the plan, the batch's rules (test-only: no production change; every case tagged `intended` or `known defect <id>`, the tag naming exactly the difference the assertion encodes; red-before shown by a deliberate mutation; no startswith-only assertions on decision-bearing strings; no network beyond loopback) and the report's 6-point block. Look in particular for an assertion that cannot fail, a case whose tag does not match what it records, a fixture that changes behaviour instead of observing it, a substitution the plan, the report or a docstring does not name, and a claim of observational fidelity the harness does not have.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

Answer form (one row per commit, then one final line):

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| … | … | … |

Final line: GREEN / GREEN WITH NOTES / RED

## Round 2 RED → round 3

Round 2 verdict: RED — finding B (the web harness is behaviour-changing, not observational),
and the report's "+524 lines" against the diff. Each fix is amended into the commit it belongs
to. Round-2 → round-3 commits: 4d72d4a → 348edae (plan), 1d11b4b → 0f38ea2 (tests), 587238c → this
commit (report and this file). The T1 series below them was amended as well (T1 round 3: the
`prior_boot` stubs removed, one restart tag corrected — see the T1 packet), so every T3 SHA
changed.

### The finding

The substituted `Lifecycle.spawn_job` ran the child `lhpc _stack-start …` in a thread and
returned `os.getpid()`. Parent and child shared one pid, so `verify_tracked` checked the shared
process, not a detached child. A production bug that records the parent's pid instead of the
child's passed the harness, and the plan's limitations list did not name the loss of a distinct
child pid.

### Verification against the repository

Confirmed. Round 2's `spawn_job` replacement was `web["thread"] =
threading.Thread(target=child, args=(argv[3:],)); …; return f"{name}.log", os.getpid()`. The
parent's `spawn_start_job` then did `ident = procident.proc_identity(pid)` and
`_track_or_terminate(life, ln, pid, …)` with that pid (`service_lifecycle_ops.py:3784/3786`).
The child's gate `webjob_gate.verify_tracked(…, _os.getpid())` (`main.py:1326`) compared the
marker with the same pid. The negative control below shows that this harness passes a parent
that tracks itself.

### What changed

The preferred fix: the web path spawns a real child process, through the production detached
entry.

- The real route calls the real `spawn_start_job`. It builds the argv itself
  (`[sys.executable, "-m", "lhpc", "_stack-start", target, "--web-result", log, "--attempt-id",
  aid, …]`, `service_lifecycle_ops.py:3770`), and the harness reuses it as built. It then calls
  the real `Lifecycle.spawn_job`, and that calls the production `Lifecycle._real_spawn`:
  `subprocess.Popen(argv, stdout=<job log>, stderr=STDOUT, stdin=DEVNULL,
  start_new_session=True)`. The child has its own pid and is detached (setsid), in the test's lab runtime
  (the path's runtime root).
- The only web-specific routing: the box's Lifecycle spawn (already `kiss_box`'s TNC spawn)
  hands an `lhpc _stack-start` argv to `_real_spawn`. During that call `subprocess.Popen` is
  wrapped only to keep the Popen object.
- The parent's real tracking writes the job marker with the attempt id. The child's real
  `verify_tracked` checks it against its own pid, and the real handshake waits for its attempt
  record.
- The test is the child's parent. It waits on the process's exit (`Popen.wait`, no sleep), then
  reads the attempt record and the job marker. It asserts `child.pid != os.getpid()` and
  `marker["pid"] == child.pid`.
- Teardown kills and reaps a child still running, and kills every TNC process the child spawned.
- The cross-path assertions now compare the CLI's decision with the web decision produced by
  that distinct process. The web `refusal`/`next_commands` are the child's own outermost
  start/restart result, recorded in the child by a delegating spy.
- The child's `ControllerService()` is built the production way, through
  `LHPC_SYSTEM_PROVIDER=entry_host:build`. The new directory helper `tests/golden/entry_host.py`
  rebuilds the box there from a JSON description and re-applies the suite's autouse isolation,
  because a fixture's patch does not cross a process boundary. Each re-applied item is named in
  its docstring, the plan and the report.
- `LHPC_WEB_ADMIT_TIMEOUT_S` is raised to 30 s (production 3 s). The handshake returns as soon
  as the admission is observed.
- Limitations, named in the plan, the `entry` docstring and the report: the child's stdout is
  not asserted; the pip and shell guards are not replicated in the child; the `job` path still
  runs the runner in the test process (it is the runner alone, and the tracked process is the one
  that runs the gate).
- Every expected decision and rendering is unchanged: `13 passed`, 9 `intended`, 4 `known
  defect`.

### The negative control's result

Mutation, applied in a scratch copy of the tree and never committed: `spawn_start_job` tracks
the parent's pid. `procident.proc_identity(pid)` and `_track_or_terminate(life, ln, pid, …)`
take `os.getpid()` instead of `pid`. Command: `python -m pytest -q -p no:cacheprovider
tests/golden/test_same_decision_across_entry_paths.py -k "test_success or
test_interactive_launch"`.

| harness | result |
|---|---|
| round 2 (thread child) | `2 passed, 11 deselected` — the bug passes |
| round 3 | `2 failed, 11 deselected` — `AssertionError: the parent tracked pid 11100, the child is 11102`; the child exited 3 |
| round 3, marker assertion removed | `-k test_success`: `1 failed, 12 deselected` — `the web child decided nothing (rc 3)`: the child's own gate refused |

The round-2 red-before mutation (`_stack-start`: `ok = res.ok`), which the separate web child
now executes, → `1 failed, 12 passed` (`test_interactive_launch`).

### The line count

Round 2's module had 525 lines (`git show --numstat --format= 1d11b4b` → `525 0`). The report
said +524, which was wrong by one. The diff's `@@ -0,0 +1,525 @@` is a hunk header (new file,
525 lines), not +1,525. The report now carries a "Numbers, measured" table (figure | command |
output); its main figures:

| figure | command | output |
|---|---|---|
| T3 code commit | `git show --stat --format= 0f38ea2` | `4 files changed, 758 insertions(+), 11 deletions(-)` |
| per file | `git show --numstat --format= 0f38ea2` | module 550, `entry_host.py` 184, `conftest.py` +18/−11, README 6 |
| production lines changed | `git diff --stat f257831..HEAD -- lhpc testlab \| wc -l` | `0` |
| the module | `python -m pytest -q -p no:cacheprovider tests/golden/test_same_decision_across_entry_paths.py` | `13 passed` |
| golden + repo | `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` | `478 passed, 5 skipped` |
| golden set, three runs | `python -m pytest -q -p no:cacheprovider tests/golden` ×3 | `54 passed` ×3 |
| lint | `ruff check tests --select F,E9` | `All checks passed!` |

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

One module, `tests/golden/test_same_decision_across_entry_paths.py`, and its host provider
`tests/golden/entry_host.py` (a helper module of the directory, rule 7). An `entry(path, op,
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
  production extension point `LHPC_SYSTEM_PROVIDER`, named `entry_host:build` — a helper module
  `tests/golden/entry_host.py`. The web parent is the console's long-lived service, `box.svc`.
- The web path runs the real route, the real `spawn_start_job` parent (it builds the argv), its
  real `Lifecycle.spawn_job` and the production `Lifecycle._real_spawn`: a real
  `subprocess.Popen` of `python -m lhpc _stack-start …`, detached (setsid), with its own pid and
  its output appended to the real job log. The parent's real tracking writes the job marker, the child's
  real `webjob_gate.verify_tracked` checks it against its own pid, and the real admission
  handshake waits for the child's attempt record. The only web-specific routing: the box's
  Lifecycle spawn (already `kiss_box`'s TNC spawn) hands an `lhpc _stack-start` argv to
  `_real_spawn` instead, and keeps the Popen object so the test, the child's parent, waits for
  the process to exit and reaps it (teardown kills and reaps a child still running, and every
  TNC it spawned). The test waits on the process's exit, never on a sleep. It then asserts that
  the job marker names the child's pid and that it differs from the test process's pid.
- In the child, `entry_host` rebuilds the box from a JSON description the test wrote: a
  FakeSystem with the box's daemon reply and band owners, the box's TNC endpoint reader and TNC
  spawn (`wrap_spawn`), and — because a fixture's patch does not cross a process boundary — the
  suite's autouse host isolation with the test process's values (firewall readers and receipt,
  systemd roots, `HW_DEFAULT`, `display_available`, the three `RealProcFs` reads, the gpsd probe,
  the kernel clock reader, the download refusal, the bounded waits). A delegating spy there
  records the child's outermost start/restart decision to a file; that is the web path's
  `refusal` whenever a child ran.
- Substituted on every path, besides the host: `kiss_box`'s real TNC process and its
  kernel-read endpoint; `prior_boot`'s boot id (through the production `LHPC_BOOT_ID_FILE`; the
  two boot-restore host gates are arranged, not stubbed — see T1); the firewall host readers in
  `test_firewall_gate` (class-wide, as a host's state is the same for every process on it);
  spies on `start`/`restart`/`boot_restore_run`/`spawn_start_job`, which delegate;
  `LHPC_WEB_ADMIT_TIMEOUT_S` raised from 3 s to 30 s (the handshake returns as soon as the
  child's admission is observed; the bound only keeps a slow interpreter start from reading
  `pending`).
- Limitations: the child's stdout (the job log) is not asserted; the pip and shell guards of the
  suite are not replicated in the child (they fail a test, they are not host state). The `job`
  path runs the runner in the test process over a marker naming the test process — that path
  is the runner alone, and its tracked process is the one that runs the gate.
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

- **A child that is not distinct.** Round 2 ran the child in a thread and returned this
  process's pid, so a parent that tracked ITSELF instead of the child passed. Now the child is a
  separate process: mutation "the parent tracks `os.getpid()` instead of the spawned pid"
  (applied in a scratch copy, run, never committed) fails `test_success` and
  `test_interactive_launch` — at the marker assertion, and, with that assertion removed, at the
  child's own gate (it exits 3, "parent tracking not confirmed", and decides nothing). The same
  mutation passes the round-2 harness.
- **A leaked process.** The child is reaped by the test (its parent); teardown kills and reaps a
  child still running and every TNC the child spawned (their pids are recorded by the child).
- **Host leakage.** `LHPC_RUNTIME_ROOT` is set per path; `INVOCATION_ID` only on the boot path.
  The provider and every patch are undone by `monkeypatch` at the end of each test; the child
  inherits the test's environment (isolated HOME and XDG roots, `PYTHONPATH` naming
  `tests/golden` and the imported `lhpc`) and re-applies the suite's isolation itself.
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

## The report

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

## Round-3 interdiff (round-2 tests commit 1d11b4b → round-3 0f38ea2, the T3 files; the T1 conftest changes are in the T1 packet)

```diff
diff --git a/tests/README.md b/tests/README.md
index be74974..06144e1 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -55,6 +55,8 @@ in a file named after when or how a defect was found:
   every entry path (the CLI, the console route with its detached job, the job runner alone, the
   boot-restore unit) and compares the decisions: a rendering difference is `intended`, a decision
   difference is a `known defect` naming one id per difference (`known defect T3-F4, T1-F1:`).
+  Its helper `golden/entry_host.py` is the `LHPC_SYSTEM_PROVIDER` that hands every process —
+  the console's detached child included, a separate process — the test's box.
 
 ## The rules
 
diff --git a/tests/golden/entry_host.py b/tests/golden/entry_host.py
new file mode 100644
index 0000000..4004335
--- /dev/null
+++ b/tests/golden/entry_host.py
@@ -0,0 +1,184 @@
+"""The host provider of the entry-path harness (`test_same_decision_across_entry_paths.py`).
+
+`LHPC_SYSTEM_PROVIDER=entry_host:build` names it for every `lhpc` process a path runs; it hands
+each one the current box's host the way a lab host reaches every process:
+
+- in the test process (the CLI, the job runner and the boot unit run there, through `main()`):
+  the live box itself (`LIVE`) — its FakeSystem and the manifest the scenario names;
+- in a SEPARATE process (the web path's detached `lhpc _stack-start` child, spawned by the
+  production `Lifecycle._real_spawn`): the same box rebuilt from the description the test wrote
+  (`describe`, named by `$GOLDEN_ENTRY_HOST`) — a FakeSystem with the box's daemon reply and band
+  owners, the box's TNC endpoint reader, and `wrap_spawn` launching the box's TNC process (the
+  substitution `kiss_box` makes in the test process). A fixture's patch does not cross a
+  process boundary, so `_install` sets in that process what the test process has from the
+  suite's autouse fixtures (tests/conftest.py) and `kiss_box`, with the test process's values:
+  the firewall readers (`_fw_integration_state`, `_fw_units_enabled`, `firewall.RECEIPT_PATH`,
+  and a scenario's `firewall_status`), `updater_units._SYSTEM_ROOTS`, `config.HW_DEFAULT`,
+  `display_available`, the three `RealProcFs` host reads, `gps.local_gpsd_listening`,
+  `read_kernel_time_state`, the binary-channel download refusal, and the bounded waits
+  (`ENDPOINT_VERIFY_TIMEOUT_S`, `DAEMON_VERIFY_TIMEOUT_S`, `Lifecycle.OBSERVE_TIMEOUT_S`) — plus
+  a recorder of its outermost start/restart decision (a delegating spy, as in the test
+  process). Not replicated: the pip and shell guards (checks that fail a test, not host state).
+  Every TNC pid it spawns is appended to the description's `pids` file, so the test kills them
+  at teardown.
+"""
+
+import json
+import os
+import sys
+import types
+from pathlib import Path
+
+HOST_ENV = "GOLDEN_ENTRY_HOST"
+LIVE = None                      # the test process's current box (set by the harness)
+_TYPED = ("admission_blocked", "enforce_fields", "firewall_gate", "reason")
+
+
+def classify(res):
+    """The refusal class of the result that decided."""
+    from lhpc.core.outcomes import manual_required_only
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
+def describe(box, out_dir: Path) -> Path:
+    """Write the box's host description for a separate process; returns its path."""
+    from lhpc.core import config as cfgmod
+    from lhpc.core import firewall as fwm
+    from lhpc.core import updater_units
+    from lhpc.core.lifecycle import Lifecycle
+    from lhpc.core.services import ControllerService
+    out_dir.mkdir(parents=True, exist_ok=True)
+    fw = ControllerService._fw_integration_state(box.svc)
+    desc = {"root": str(box.root), "port": box.port, "listens": box.listens, "term": box.term,
+            "cmdlines": {str(k): v for k, v in box.fake.cmdlines_data.items()},
+            "manifest": str(box.manifest) if box.manifest else None,
+            "waits": {"endpoint": ControllerService.ENDPOINT_VERIFY_TIMEOUT_S,
+                      "daemon": ControllerService.DAEMON_VERIFY_TIMEOUT_S,
+                      "observe": Lifecycle.OBSERVE_TIMEOUT_S},
+            "hw_default": cfgmod.HW_DEFAULT,
+            "fw_integration": fw, "fw_receipt": fwm.RECEIPT_PATH,
+            "fw_status": ControllerService.firewall_status(box.svc) if fw == "present" else None,
+            "systemd_roots": [str(r) for r in updater_units._SYSTEM_ROOTS],
+            "record": str(out_dir / "decision.json"), "pids": str(out_dir / "pids")}
+    path = out_dir / "host.json"
+    path.write_text(json.dumps(desc))
+    return path
+
+
+def read_decision(desc_path: Path):
+    """The separate process's recorded decision, or None when it decided nothing."""
+    rec = Path(json.loads(Path(desc_path).read_text())["record"])
+    return json.loads(rec.read_text()) if rec.exists() else None
+
+
+def spawned_pids(desc_path: Path) -> list[int]:
+    p = Path(json.loads(Path(desc_path).read_text())["pids"])
+    return [int(x) for x in p.read_text().split()] if p.exists() else []
+
+
+def build(paths):
+    if LIVE is not None:
+        return types.SimpleNamespace(system=LIVE.fake.system, manifest_path=LIVE.manifest,
+                                     wrap_spawn=None)
+    return _rebuilt(json.loads(Path(os.environ[HOST_ENV]).read_text()))
+
+
+def _golden():
+    """The golden set's own box definitions (tests/golden/conftest.py), loaded by path."""
+    import importlib.util
+    mod = sys.modules.get("golden_conftest")
+    if mod is None:
+        spec = importlib.util.spec_from_file_location("golden_conftest",
+                                                      Path(__file__).with_name("conftest.py"))
+        mod = importlib.util.module_from_spec(spec)
+        sys.modules["golden_conftest"] = mod
+        spec.loader.exec_module(mod)
+    return mod
+
+
+_installed: list = []
+
+
+def _install(d):
+    """The separate process's patches — once per process."""
+    if _installed:
+        return
+    _installed.append(True)
+    from lhpc.core import binary_install, gps, service_system, updater_units
+    from lhpc.core import config as cfgmod
+    from lhpc.core import firewall as fwm
+    from lhpc.core.lifecycle import Lifecycle
+    from lhpc.core.probes import backends
+    from lhpc.core.services import ControllerService
+    ControllerService._fw_integration_state = lambda self: d["fw_integration"]
+    ControllerService._fw_units_enabled = lambda self: False
+    if d["fw_status"] is not None:
+        ControllerService.firewall_status = lambda self: dict(d["fw_status"])
+    fwm.RECEIPT_PATH = d["fw_receipt"]
+    updater_units._SYSTEM_ROOTS = tuple(Path(r) for r in d["systemd_roots"])
+    cfgmod.HW_DEFAULT = d["hw_default"]
+    ControllerService.display_available = staticmethod(lambda: True)
+    backends.RealProcFs.cmdlines = lambda self: {}
+    backends.RealProcFs.tcp_listeners = lambda self: []
+    backends.RealProcFs.owner_pid = lambda self, inode, budget_s: (None, False)
+    gps.local_gpsd_listening = lambda: False
+    service_system.read_kernel_time_state = lambda: {"synced": True, "maxerror_us": 1000}
+
+    def _refuse(url, *_a, **_k):
+        raise AssertionError(f"a real binary-channel download from the web child: {url}")
+    binary_install._http_get = binary_install._open_stream = _refuse
+    ControllerService.ENDPOINT_VERIFY_TIMEOUT_S = d["waits"]["endpoint"]
+    ControllerService.DAEMON_VERIFY_TIMEOUT_S = d["waits"]["daemon"]
+    Lifecycle.OBSERVE_TIMEOUT_S = d["waits"]["observe"]
+    depth = [0]
+
+    def _spy(real):                # the outermost start/restart result → the record file
+        def wrapper(self, *a, **k):
+            depth[0] += 1
+            try:
+                res = real(self, *a, **k)
+            finally:
+                depth[0] -= 1
+            if depth[0] == 0:
+                Path(d["record"]).write_text(json.dumps(
+                    {"refusal": classify(res), "next_commands": list(res.next_commands)}))
+            return res
+        return wrapper
+    ControllerService.start = _spy(ControllerService.start)
+    ControllerService.restart = _spy(ControllerService.restart)
+
+
+def _rebuilt(d):
+    from lhpc.core.probes.backends import FakeSystem
+    golden = _golden()
+    _install(d)
+    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": golden._READY},
+                      cmdlines_data={int(k): v for k, v in d["cmdlines"].items()})
+    fake.listeners = golden._TncEndpoint(d["port"])
+    box = golden.KissBox(Path(d["root"]), fake, None, d["port"])
+    box.listens, box.term = d["listens"], d["term"]
+    procs: list = []
+    tnc = box.spawn(procs)
+
+    def wrap_spawn(_real):         # the box's TNC process instead of the component binary
+        def _spawn(argv, log, cwd=None, env=None):
+            pid = tnc(argv, log, cwd, env)
+            with open(d["pids"], "a") as fh:
+                fh.write(f"{pid}\n")
+            return pid
+        return _spawn
+    return types.SimpleNamespace(system=fake.system, manifest_path=d["manifest"],
+                                 wrap_spawn=wrap_spawn)
diff --git a/tests/golden/test_same_decision_across_entry_paths.py b/tests/golden/test_same_decision_across_entry_paths.py
index 185a1c4..2e7614e 100644
--- a/tests/golden/test_same_decision_across_entry_paths.py
+++ b/tests/golden/test_same_decision_across_entry_paths.py
@@ -4,16 +4,17 @@ One scenario is driven through every entry path that can start a stack, each on
 
 - `cli`  — `lhpc stack <op> <target> --yes` (`main()`: the plan, then the apply);
 - `web`  — `POST /action` on the console: the plan in the route, then the real `spawn_start_job`
-           parent and its real admission handshake, while the `lhpc _stack-start` child it
-           spawns runs concurrently in a thread of this process (see `entry`);
+           parent, its real tracking and admission handshake, and the `lhpc _stack-start` child
+           it spawns as a SEPARATE detached process (its own pid, detached by setsid) through the
+           production `Lifecycle._real_spawn` (see `entry`);
 - `job`  — the detached job runner alone (`lhpc _stack-start …` over a tracked attempt);
 - `boot` — the boot-restore unit (`lhpc autostart --run-service`) replaying a prior boot's record.
 
 Every `lhpc` process a path runs (the CLI, the web's child, the job runner, the boot unit) enters
 through `main()` and builds its OWN `ControllerService()` the production way; the box's host (the
 FakeSystem, and the manifest a scenario names) reaches it through the production extension point
-`LHPC_SYSTEM_PROVIDER`, as a lab host reaches every process. The web parent is the console's
-long-lived service (`box.svc`).
+`LHPC_SYSTEM_PROVIDER` (`entry_host.py`), as a lab host reaches every process. The web parent is
+the console's long-lived service (`box.svc`).
 
 The DECISION compared across paths: `started`, the `refusal` class (the typed `data` key where
 one exists, `blockers` for a plan that lists owners, `manual_required`/`unverified`/`ok` from the
@@ -24,24 +25,33 @@ afterwards. The RENDERING (exit code, HTTP status and admission, job state, jour
 asserted per path as well. A rendering difference (same decision, different words or format) is
 `intended:`; a DECISION difference between paths is always a `known defect <id>:` naming exactly
 that difference — never `intended`.
+
+What the web path observes of its child is what a separate process leaves behind: its exit
+status (the test is its parent and reaps it), the attempt record and job marker it was tracked
+by, the files it wrote, and its outermost start/restart decision (recorded by `entry_host`'s
+delegating spy in that process). The job marker the parent wrote must name the child's pid, which
+differs from the test process's — a parent that tracked itself instead fails here and at the
+child's own gate.
 """
 
+import contextlib
 import json
 import os
 import re
-import sys
+import signal
+import subprocess
 import threading
-import types
+import tomllib
 import uuid
+from pathlib import Path
 
+import entry_host
 import pytest
 
 from lhpc.adapters.cli import main as cli_main
 from lhpc.adapters.web.app import create_app
 from lhpc.core import config as cfgmod
 from lhpc.core import jobresult, jobs, procident
-from lhpc.core.lifecycle import Lifecycle
-from lhpc.core.outcomes import manual_required_only
 from lhpc.core.paths import Paths
 from lhpc.core.probes.backends import CommandResult
 from lhpc.core.services import ControllerService
@@ -51,29 +61,10 @@ pytestmark = pytest.mark.needs_session
 PATHS = ("cli", "web", "job", "boot")
 _BOOKKEEPING = re.compile(r"^(state/(jobresults|jobs|owned|locks)/|state/boot-restore\.json$|"
                           r"logs/web-|config/secrets/web_session\.key$|config/\.lock$)")
-_TYPED = ("admission_blocked", "enforce_fields", "firewall_gate", "reason")
 KISS_STARTED = ["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433",
                 "state/running/kiss.band"]
 
 
-def _classify(res):
-    """The refusal class of the result that decided."""
-    data = res.data or {}
-    for key in _TYPED:
-        if data.get(key):
-            return key
-    if data.get("blockers"):
-        return "blockers"
-    if res.ok:
-        return "ok"
-    if res.results and manual_required_only(res.results):
-        return "manual_required"
-    for outcome in ("unverified", "blocked"):
-        if any(r.outcome.value == outcome for r in res.results):
-            return outcome
-    return res.summary
-
-
 def _files(root):
     return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
             if p.is_file() and not _BOOKKEEPING.match(str(p.relative_to(root)))}
@@ -87,20 +78,25 @@ def entry(kiss_box, prior_boot, monkeypatch, csrf, tmp_path):
     `box.svc`); `evidence` is the prior boot's record the `boot` path replays.
 
     Substituted for every path (all listed here):
-    - the host: `LHPC_SYSTEM_PROVIDER` names a provider serving the current box's FakeSystem and
+    - the host: `LHPC_SYSTEM_PROVIDER=entry_host:build` serves the current box's FakeSystem and
       manifest, so each process's own `ControllerService()` runs on the box (production reads the
-      same variable; unset, it builds the real system);
+      same variable; unset, it builds the real system) — in a separate process, the box rebuilt
+      from its description (`entry_host`);
     - `kiss_box`'s own substitutions (its real TNC process spawn and endpoint reader, the 3 s
-      endpoint wait) and `prior_boot`'s (this boot's id, the two boot-restore host gates);
+      endpoint wait) and `prior_boot`'s (this boot's id through `LHPC_BOOT_ID_FILE`);
     - the spies on `ControllerService.start`/`restart`/`boot_restore_run`/`spawn_start_job`,
       which delegate.
-    Substituted for the web path only: `Lifecycle.spawn_job` — instead of a detached process it
-    runs the argv's `lhpc _stack-start …` through `main()` in a thread of this process and returns
-    this process's pid, so the parent tracks a real identity and the child's real gate verifies
-    it. The parent's handshake, admission and tracking are the real ones and run concurrently
-    with the child. Not covered: what a separate process alone changes — its own module state,
-    environment, stdout log file and signal disposition."""
-    decided, booted, web, host = [], [], {}, {}
+    For the web path only: the box's Lifecycle spawn (already `kiss_box`'s TNC spawn) hands an
+    `lhpc _stack-start …` argv to the production `Lifecycle._real_spawn` — the real
+    `subprocess.Popen` of the argv the real `spawn_start_job` built, detached (setsid), its
+    output appended to the real job log — keeping the Popen object so the test, its parent,
+    waits for it and reaps it (also at teardown, with every TNC the child spawned).
+    `LHPC_WEB_ADMIT_TIMEOUT_S` (the parent's bounded handshake wait, production 3 s) is raised to
+    30 s: the handshake returns as soon as the child's admission is observed, so it only keeps a
+    slow interpreter start from reading `pending`. In the child, `entry_host` re-applies the
+    suite's autouse isolation (listed in its docstring). Not covered: the child's stdout is the
+    job log, which no assertion reads; the suite's pip and shell guards do not run in the child."""
+    decided, booted, web = [], [], {}
     depth = threading.local()
 
     def _spy(real, into):          # the outermost start/restart result; boot-restore's own
@@ -120,62 +116,88 @@ def entry(kiss_box, prior_boot, monkeypatch, csrf, tmp_path):
     monkeypatch.setattr(ControllerService, "boot_restore_run",
                         _spy(ControllerService.boot_restore_run, booted))
 
-    provider = types.ModuleType("golden_entry_host")
-    provider.build = lambda paths: types.SimpleNamespace(
-        system=host["box"].fake.system, manifest_path=host["box"].manifest, wrap_spawn=None)
-    monkeypatch.setitem(sys.modules, provider.__name__, provider)
-    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", f"{provider.__name__}:build")
-
-    def child(args):
-        try:
-            web["child_rc"] = cli_main.main(args)
-        except BaseException as exc:   # re-raised in the test's thread by `_web`
-            web["child_exc"] = exc
-
-    def spawn_job(self, name, argv, cwd, env=None):
-        assert argv[1:3] == ["-m", "lhpc"], argv
-        web["thread"] = threading.Thread(target=child, args=(argv[3:],), name="lhpc-web-child")
-        web["thread"].start()
-        return f"{name}.log", os.getpid()
+    import lhpc
+    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "entry_host:build")
+    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
+        [str(Path(entry_host.__file__).parent), str(Path(lhpc.__file__).parents[1]),
+         *filter(None, [os.environ.get("PYTHONPATH")])]))
+    monkeypatch.setenv("LHPC_WEB_ADMIT_TIMEOUT_S", "30")
+
+    children, descriptions = [], []
+
+    def routed(tnc_lifecycle):          # kiss_box's Lifecycle, `lhpc _stack-start` routed out
+        def lifecycle(self):
+            lc = tnc_lifecycle(self)
+            tnc = lc._spawn
+
+            def spawn(argv, log, cwd=None, env=None):
+                if argv[1:4] != ["-m", "lhpc", "_stack-start"]:
+                    return tnc(argv, log, cwd=cwd, env=env)
+                real_popen = subprocess.Popen
+
+                def keep(*a, **k):                         # keep the Popen: the test reaps it
+                    web["child"] = real_popen(*a, **k)
+                    children.append(web["child"])
+                    return web["child"]
+                with monkeypatch.context() as m:
+                    m.setattr(subprocess, "Popen", keep)
+                    return lc._real_spawn(argv, log, cwd=cwd, env=env)
+            lc._spawn = spawn
+            return lc
+        return lifecycle
 
     real_spawn_start = ControllerService.spawn_start_job
 
     def spawn_start(self, *a, **k):            # a spy: records the parent's (log, admission, why)
         web["parent"] = real_spawn_start(self, *a, **k)
         return web["parent"]
-    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
     monkeypatch.setattr(ControllerService, "spawn_start_job", spawn_start)
 
     def _run(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
              callsign=True):
         box = kiss_box(root=tmp_path / path, callsign=callsign)
         box.manifest = None
-        host["box"] = box
+        monkeypatch.setattr(ControllerService, "_lifecycle", routed(ControllerService._lifecycle))
+        monkeypatch.setattr(entry_host, "LIVE", box)
         monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(box.root))
         if path == "boot":
             prior_boot(box.root, stack=evidence[0], comp=evidence[1], band=evidence[2])
         if setup:
             setup(box)
         box.svc.invalidate_snapshot()
+        desc = entry_host.describe(box, tmp_path / f"{path}-host")
+        descriptions.append(desc)
+        monkeypatch.setenv(entry_host.HOST_ENV, str(desc))
         before = _files(box.root)
         decided.clear()
         booted.clear()
         web.clear()
+        web["desc"] = desc
         render, started = _DRIVE[path](box, op, target, web, csrf, monkeypatch)
         after = _files(box.root)
-        parent = web.get("parent")
-        if parent and parent[1] == "blocked" and "child_rc" not in web:
-            refusal = parent[2]            # the web parent refused: no child ever decided
+        parent, child = web.get("parent"), web.get("decision")
+        if "child" in web:                 # the separate process decided (or refused at its gate)
+            assert child is not None, f"the web child decided nothing (rc {render['child_rc']})"
+            refusal, told = child["refusal"], child["next_commands"]
+        elif parent and parent[1] == "blocked":
+            refusal, told = parent[2], []  # the web parent refused: no child was spawned
         elif decided:
-            refusal = _classify(decided[-1])
+            refusal, told = entry_host.classify(decided[-1]), list(decided[-1].next_commands)
         else:                              # boot-restore planned no start at all
-            refusal = _classify(booted[-1])
-        told = list(decided[-1].next_commands) if decided else []
+            refusal, told = entry_host.classify(booted[-1]), []
         return {"started": started, "refusal": refusal, "live": box.live(), "render": render,
                 "files": sorted(k for k in before.keys() | after.keys()
                                 if before.get(k) != after.get(k)),
                 "next_commands": told, "root": box.root}
-    return _run
+    yield _run
+    for c in children:                     # reap the web children, then kill what they spawned
+        if c.poll() is None:
+            c.kill()
+        c.wait(timeout=10)
+    for d in descriptions:
+        for pid in entry_host.spawned_pids(d):
+            with contextlib.suppress(OSError):
+                os.killpg(pid, signal.SIGKILL)
 
 
 def _job_state(svc, op, target):
@@ -189,8 +211,8 @@ def _cli(box, op, target, web, csrf, monkeypatch):
 
 
 def _job(box, op, target, web, csrf, monkeypatch):
-    """The attempt as the web parent leaves it — reserved, its job marker naming this process
-    (the in-process child) — then the runner, with the band the parent freezes."""
+    """The attempt as the web parent leaves it — reserved, its job marker naming the process that
+    runs the runner (this one) — then the runner, with the band the parent freezes."""
     svc = box.svc
     log, aid = f"web-{op}-{target}.log", uuid.uuid4().hex
     assert jobresult.reserve(svc._paths, log, aid, op, target, svc.stack_of(target) or target, [])
@@ -218,14 +240,17 @@ def _web(box, op, target, web, csrf, monkeypatch):
     client = create_app(service_factory=lambda: box.svc).test_client()
     r = client.post("/action", data={"_csrf": csrf(client, "/"), "op": op, "target": target,
                                      "from": "dash"})
-    if "thread" in web:                    # the child the parent spawned, run to its end
-        web["thread"].join(timeout=120)
-        assert not web["thread"].is_alive(), "the web child did not finish"
-        if "child_exc" in web:
-            raise web["child_exc"]
+    if "child" in web:                     # the separate process the parent spawned, to its end
+        child = web["child"]
+        child.wait(timeout=120)
+        log = f"web-{op}-{target}.log"
+        marker = tomllib.loads((box.root / "state" / "jobs" / f"{log}.job").read_text())
+        assert child.pid != os.getpid() and marker["pid"] == child.pid, (
+            f"the parent tracked pid {marker['pid']}, the child is {child.pid}")
+        web["decision"] = entry_host.read_decision(web["desc"])
     state = _job_state(box.svc, op, target)
     return ({"status": r.status_code, "admission": (web.get("parent") or (0, None))[1],
-             "child_rc": web.get("child_rc"), "job": state},
+             "child_rc": web["child"].returncode if "child" in web else None, "job": state},
             bool(state and state[0] == "done"))
 
 
```

## Full amended diff of the code commits (0f38ea2)

```diff
diff --git a/tests/README.md b/tests/README.md
index 47b4a6c..06144e1 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -51,6 +51,12 @@ in a file named after when or how a defect was found:
   contract) or `known defect <finding id>:` (recorded as is, changed only by the fix of that
   finding). Pinning the step order is its purpose, so it is the one place that names coordinator
   steps: through `ORDER_SEAMS` in its `conftest.py`, which a refactor that renames a step updates.
+  Beside it, `golden/test_same_decision_across_entry_paths.py` drives each start decision through
+  every entry path (the CLI, the console route with its detached job, the job runner alone, the
+  boot-restore unit) and compares the decisions: a rendering difference is `intended`, a decision
+  difference is a `known defect` naming one id per difference (`known defect T3-F4, T1-F1:`).
+  Its helper `golden/entry_host.py` is the `LHPC_SYSTEM_PROVIDER` that hands every process —
+  the console's detached child included, a separate process — the test's box.
 
 ## The rules
 
diff --git a/tests/golden/conftest.py b/tests/golden/conftest.py
index 3f69c08..05c153e 100644
--- a/tests/golden/conftest.py
+++ b/tests/golden/conftest.py
@@ -223,11 +223,11 @@ def run_op(phases):
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
@@ -316,6 +316,12 @@ class KissBox:
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
@@ -325,22 +331,23 @@ class KissBox:
 
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
diff --git a/tests/golden/entry_host.py b/tests/golden/entry_host.py
new file mode 100644
index 0000000..4004335
--- /dev/null
+++ b/tests/golden/entry_host.py
@@ -0,0 +1,184 @@
+"""The host provider of the entry-path harness (`test_same_decision_across_entry_paths.py`).
+
+`LHPC_SYSTEM_PROVIDER=entry_host:build` names it for every `lhpc` process a path runs; it hands
+each one the current box's host the way a lab host reaches every process:
+
+- in the test process (the CLI, the job runner and the boot unit run there, through `main()`):
+  the live box itself (`LIVE`) — its FakeSystem and the manifest the scenario names;
+- in a SEPARATE process (the web path's detached `lhpc _stack-start` child, spawned by the
+  production `Lifecycle._real_spawn`): the same box rebuilt from the description the test wrote
+  (`describe`, named by `$GOLDEN_ENTRY_HOST`) — a FakeSystem with the box's daemon reply and band
+  owners, the box's TNC endpoint reader, and `wrap_spawn` launching the box's TNC process (the
+  substitution `kiss_box` makes in the test process). A fixture's patch does not cross a
+  process boundary, so `_install` sets in that process what the test process has from the
+  suite's autouse fixtures (tests/conftest.py) and `kiss_box`, with the test process's values:
+  the firewall readers (`_fw_integration_state`, `_fw_units_enabled`, `firewall.RECEIPT_PATH`,
+  and a scenario's `firewall_status`), `updater_units._SYSTEM_ROOTS`, `config.HW_DEFAULT`,
+  `display_available`, the three `RealProcFs` host reads, `gps.local_gpsd_listening`,
+  `read_kernel_time_state`, the binary-channel download refusal, and the bounded waits
+  (`ENDPOINT_VERIFY_TIMEOUT_S`, `DAEMON_VERIFY_TIMEOUT_S`, `Lifecycle.OBSERVE_TIMEOUT_S`) — plus
+  a recorder of its outermost start/restart decision (a delegating spy, as in the test
+  process). Not replicated: the pip and shell guards (checks that fail a test, not host state).
+  Every TNC pid it spawns is appended to the description's `pids` file, so the test kills them
+  at teardown.
+"""
+
+import json
+import os
+import sys
+import types
+from pathlib import Path
+
+HOST_ENV = "GOLDEN_ENTRY_HOST"
+LIVE = None                      # the test process's current box (set by the harness)
+_TYPED = ("admission_blocked", "enforce_fields", "firewall_gate", "reason")
+
+
+def classify(res):
+    """The refusal class of the result that decided."""
+    from lhpc.core.outcomes import manual_required_only
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
+def describe(box, out_dir: Path) -> Path:
+    """Write the box's host description for a separate process; returns its path."""
+    from lhpc.core import config as cfgmod
+    from lhpc.core import firewall as fwm
+    from lhpc.core import updater_units
+    from lhpc.core.lifecycle import Lifecycle
+    from lhpc.core.services import ControllerService
+    out_dir.mkdir(parents=True, exist_ok=True)
+    fw = ControllerService._fw_integration_state(box.svc)
+    desc = {"root": str(box.root), "port": box.port, "listens": box.listens, "term": box.term,
+            "cmdlines": {str(k): v for k, v in box.fake.cmdlines_data.items()},
+            "manifest": str(box.manifest) if box.manifest else None,
+            "waits": {"endpoint": ControllerService.ENDPOINT_VERIFY_TIMEOUT_S,
+                      "daemon": ControllerService.DAEMON_VERIFY_TIMEOUT_S,
+                      "observe": Lifecycle.OBSERVE_TIMEOUT_S},
+            "hw_default": cfgmod.HW_DEFAULT,
+            "fw_integration": fw, "fw_receipt": fwm.RECEIPT_PATH,
+            "fw_status": ControllerService.firewall_status(box.svc) if fw == "present" else None,
+            "systemd_roots": [str(r) for r in updater_units._SYSTEM_ROOTS],
+            "record": str(out_dir / "decision.json"), "pids": str(out_dir / "pids")}
+    path = out_dir / "host.json"
+    path.write_text(json.dumps(desc))
+    return path
+
+
+def read_decision(desc_path: Path):
+    """The separate process's recorded decision, or None when it decided nothing."""
+    rec = Path(json.loads(Path(desc_path).read_text())["record"])
+    return json.loads(rec.read_text()) if rec.exists() else None
+
+
+def spawned_pids(desc_path: Path) -> list[int]:
+    p = Path(json.loads(Path(desc_path).read_text())["pids"])
+    return [int(x) for x in p.read_text().split()] if p.exists() else []
+
+
+def build(paths):
+    if LIVE is not None:
+        return types.SimpleNamespace(system=LIVE.fake.system, manifest_path=LIVE.manifest,
+                                     wrap_spawn=None)
+    return _rebuilt(json.loads(Path(os.environ[HOST_ENV]).read_text()))
+
+
+def _golden():
+    """The golden set's own box definitions (tests/golden/conftest.py), loaded by path."""
+    import importlib.util
+    mod = sys.modules.get("golden_conftest")
+    if mod is None:
+        spec = importlib.util.spec_from_file_location("golden_conftest",
+                                                      Path(__file__).with_name("conftest.py"))
+        mod = importlib.util.module_from_spec(spec)
+        sys.modules["golden_conftest"] = mod
+        spec.loader.exec_module(mod)
+    return mod
+
+
+_installed: list = []
+
+
+def _install(d):
+    """The separate process's patches — once per process."""
+    if _installed:
+        return
+    _installed.append(True)
+    from lhpc.core import binary_install, gps, service_system, updater_units
+    from lhpc.core import config as cfgmod
+    from lhpc.core import firewall as fwm
+    from lhpc.core.lifecycle import Lifecycle
+    from lhpc.core.probes import backends
+    from lhpc.core.services import ControllerService
+    ControllerService._fw_integration_state = lambda self: d["fw_integration"]
+    ControllerService._fw_units_enabled = lambda self: False
+    if d["fw_status"] is not None:
+        ControllerService.firewall_status = lambda self: dict(d["fw_status"])
+    fwm.RECEIPT_PATH = d["fw_receipt"]
+    updater_units._SYSTEM_ROOTS = tuple(Path(r) for r in d["systemd_roots"])
+    cfgmod.HW_DEFAULT = d["hw_default"]
+    ControllerService.display_available = staticmethod(lambda: True)
+    backends.RealProcFs.cmdlines = lambda self: {}
+    backends.RealProcFs.tcp_listeners = lambda self: []
+    backends.RealProcFs.owner_pid = lambda self, inode, budget_s: (None, False)
+    gps.local_gpsd_listening = lambda: False
+    service_system.read_kernel_time_state = lambda: {"synced": True, "maxerror_us": 1000}
+
+    def _refuse(url, *_a, **_k):
+        raise AssertionError(f"a real binary-channel download from the web child: {url}")
+    binary_install._http_get = binary_install._open_stream = _refuse
+    ControllerService.ENDPOINT_VERIFY_TIMEOUT_S = d["waits"]["endpoint"]
+    ControllerService.DAEMON_VERIFY_TIMEOUT_S = d["waits"]["daemon"]
+    Lifecycle.OBSERVE_TIMEOUT_S = d["waits"]["observe"]
+    depth = [0]
+
+    def _spy(real):                # the outermost start/restart result → the record file
+        def wrapper(self, *a, **k):
+            depth[0] += 1
+            try:
+                res = real(self, *a, **k)
+            finally:
+                depth[0] -= 1
+            if depth[0] == 0:
+                Path(d["record"]).write_text(json.dumps(
+                    {"refusal": classify(res), "next_commands": list(res.next_commands)}))
+            return res
+        return wrapper
+    ControllerService.start = _spy(ControllerService.start)
+    ControllerService.restart = _spy(ControllerService.restart)
+
+
+def _rebuilt(d):
+    from lhpc.core.probes.backends import FakeSystem
+    golden = _golden()
+    _install(d)
+    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": golden._READY},
+                      cmdlines_data={int(k): v for k, v in d["cmdlines"].items()})
+    fake.listeners = golden._TncEndpoint(d["port"])
+    box = golden.KissBox(Path(d["root"]), fake, None, d["port"])
+    box.listens, box.term = d["listens"], d["term"]
+    procs: list = []
+    tnc = box.spawn(procs)
+
+    def wrap_spawn(_real):         # the box's TNC process instead of the component binary
+        def _spawn(argv, log, cwd=None, env=None):
+            pid = tnc(argv, log, cwd, env)
+            with open(d["pids"], "a") as fh:
+                fh.write(f"{pid}\n")
+            return pid
+        return _spawn
+    return types.SimpleNamespace(system=fake.system, manifest_path=d["manifest"],
+                                 wrap_spawn=wrap_spawn)
diff --git a/tests/golden/test_same_decision_across_entry_paths.py b/tests/golden/test_same_decision_across_entry_paths.py
new file mode 100644
index 0000000..2e7614e
--- /dev/null
+++ b/tests/golden/test_same_decision_across_entry_paths.py
@@ -0,0 +1,550 @@
+"""Same input, same decision — whichever entry path runs it.
+
+One scenario is driven through every entry path that can start a stack, each on a fresh box:
+
+- `cli`  — `lhpc stack <op> <target> --yes` (`main()`: the plan, then the apply);
+- `web`  — `POST /action` on the console: the plan in the route, then the real `spawn_start_job`
+           parent, its real tracking and admission handshake, and the `lhpc _stack-start` child
+           it spawns as a SEPARATE detached process (its own pid, detached by setsid) through the
+           production `Lifecycle._real_spawn` (see `entry`);
+- `job`  — the detached job runner alone (`lhpc _stack-start …` over a tracked attempt);
+- `boot` — the boot-restore unit (`lhpc autostart --run-service`) replaying a prior boot's record.
+
+Every `lhpc` process a path runs (the CLI, the web's child, the job runner, the boot unit) enters
+through `main()` and builds its OWN `ControllerService()` the production way; the box's host (the
+FakeSystem, and the manifest a scenario names) reaches it through the production extension point
+`LHPC_SYSTEM_PROVIDER` (`entry_host.py`), as a lab host reaches every process. The web parent is
+the console's long-lived service (`box.svc`).
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
+
+What the web path observes of its child is what a separate process leaves behind: its exit
+status (the test is its parent and reaps it), the attempt record and job marker it was tracked
+by, the files it wrote, and its outermost start/restart decision (recorded by `entry_host`'s
+delegating spy in that process). The job marker the parent wrote must name the child's pid, which
+differs from the test process's — a parent that tracked itself instead fails here and at the
+child's own gate.
+"""
+
+import contextlib
+import json
+import os
+import re
+import signal
+import subprocess
+import threading
+import tomllib
+import uuid
+from pathlib import Path
+
+import entry_host
+import pytest
+
+from lhpc.adapters.cli import main as cli_main
+from lhpc.adapters.web.app import create_app
+from lhpc.core import config as cfgmod
+from lhpc.core import jobresult, jobs, procident
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import CommandResult
+from lhpc.core.services import ControllerService
+
+pytestmark = pytest.mark.needs_session
+
+PATHS = ("cli", "web", "job", "boot")
+_BOOKKEEPING = re.compile(r"^(state/(jobresults|jobs|owned|locks)/|state/boot-restore\.json$|"
+                          r"logs/web-|config/secrets/web_session\.key$|config/\.lock$)")
+KISS_STARTED = ["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433",
+                "state/running/kiss.band"]
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
+    - the host: `LHPC_SYSTEM_PROVIDER=entry_host:build` serves the current box's FakeSystem and
+      manifest, so each process's own `ControllerService()` runs on the box (production reads the
+      same variable; unset, it builds the real system) — in a separate process, the box rebuilt
+      from its description (`entry_host`);
+    - `kiss_box`'s own substitutions (its real TNC process spawn and endpoint reader, the 3 s
+      endpoint wait) and `prior_boot`'s (this boot's id through `LHPC_BOOT_ID_FILE`);
+    - the spies on `ControllerService.start`/`restart`/`boot_restore_run`/`spawn_start_job`,
+      which delegate.
+    For the web path only: the box's Lifecycle spawn (already `kiss_box`'s TNC spawn) hands an
+    `lhpc _stack-start …` argv to the production `Lifecycle._real_spawn` — the real
+    `subprocess.Popen` of the argv the real `spawn_start_job` built, detached (setsid), its
+    output appended to the real job log — keeping the Popen object so the test, its parent,
+    waits for it and reaps it (also at teardown, with every TNC the child spawned).
+    `LHPC_WEB_ADMIT_TIMEOUT_S` (the parent's bounded handshake wait, production 3 s) is raised to
+    30 s: the handshake returns as soon as the child's admission is observed, so it only keeps a
+    slow interpreter start from reading `pending`. In the child, `entry_host` re-applies the
+    suite's autouse isolation (listed in its docstring). Not covered: the child's stdout is the
+    job log, which no assertion reads; the suite's pip and shell guards do not run in the child."""
+    decided, booted, web = [], [], {}
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
+    import lhpc
+    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "entry_host:build")
+    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
+        [str(Path(entry_host.__file__).parent), str(Path(lhpc.__file__).parents[1]),
+         *filter(None, [os.environ.get("PYTHONPATH")])]))
+    monkeypatch.setenv("LHPC_WEB_ADMIT_TIMEOUT_S", "30")
+
+    children, descriptions = [], []
+
+    def routed(tnc_lifecycle):          # kiss_box's Lifecycle, `lhpc _stack-start` routed out
+        def lifecycle(self):
+            lc = tnc_lifecycle(self)
+            tnc = lc._spawn
+
+            def spawn(argv, log, cwd=None, env=None):
+                if argv[1:4] != ["-m", "lhpc", "_stack-start"]:
+                    return tnc(argv, log, cwd=cwd, env=env)
+                real_popen = subprocess.Popen
+
+                def keep(*a, **k):                         # keep the Popen: the test reaps it
+                    web["child"] = real_popen(*a, **k)
+                    children.append(web["child"])
+                    return web["child"]
+                with monkeypatch.context() as m:
+                    m.setattr(subprocess, "Popen", keep)
+                    return lc._real_spawn(argv, log, cwd=cwd, env=env)
+            lc._spawn = spawn
+            return lc
+        return lifecycle
+
+    real_spawn_start = ControllerService.spawn_start_job
+
+    def spawn_start(self, *a, **k):            # a spy: records the parent's (log, admission, why)
+        web["parent"] = real_spawn_start(self, *a, **k)
+        return web["parent"]
+    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn_start)
+
+    def _run(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
+             callsign=True):
+        box = kiss_box(root=tmp_path / path, callsign=callsign)
+        box.manifest = None
+        monkeypatch.setattr(ControllerService, "_lifecycle", routed(ControllerService._lifecycle))
+        monkeypatch.setattr(entry_host, "LIVE", box)
+        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(box.root))
+        if path == "boot":
+            prior_boot(box.root, stack=evidence[0], comp=evidence[1], band=evidence[2])
+        if setup:
+            setup(box)
+        box.svc.invalidate_snapshot()
+        desc = entry_host.describe(box, tmp_path / f"{path}-host")
+        descriptions.append(desc)
+        monkeypatch.setenv(entry_host.HOST_ENV, str(desc))
+        before = _files(box.root)
+        decided.clear()
+        booted.clear()
+        web.clear()
+        web["desc"] = desc
+        render, started = _DRIVE[path](box, op, target, web, csrf, monkeypatch)
+        after = _files(box.root)
+        parent, child = web.get("parent"), web.get("decision")
+        if "child" in web:                 # the separate process decided (or refused at its gate)
+            assert child is not None, f"the web child decided nothing (rc {render['child_rc']})"
+            refusal, told = child["refusal"], child["next_commands"]
+        elif parent and parent[1] == "blocked":
+            refusal, told = parent[2], []  # the web parent refused: no child was spawned
+        elif decided:
+            refusal, told = entry_host.classify(decided[-1]), list(decided[-1].next_commands)
+        else:                              # boot-restore planned no start at all
+            refusal, told = entry_host.classify(booted[-1]), []
+        return {"started": started, "refusal": refusal, "live": box.live(), "render": render,
+                "files": sorted(k for k in before.keys() | after.keys()
+                                if before.get(k) != after.get(k)),
+                "next_commands": told, "root": box.root}
+    yield _run
+    for c in children:                     # reap the web children, then kill what they spawned
+        if c.poll() is None:
+            c.kill()
+        c.wait(timeout=10)
+    for d in descriptions:
+        for pid in entry_host.spawned_pids(d):
+            with contextlib.suppress(OSError):
+                os.killpg(pid, signal.SIGKILL)
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
+    """The attempt as the web parent leaves it — reserved, its job marker naming the process that
+    runs the runner (this one) — then the runner, with the band the parent freezes."""
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
+    if "child" in web:                     # the separate process the parent spawned, to its end
+        child = web["child"]
+        child.wait(timeout=120)
+        log = f"web-{op}-{target}.log"
+        marker = tomllib.loads((box.root / "state" / "jobs" / f"{log}.job").read_text())
+        assert child.pid != os.getpid() and marker["pid"] == child.pid, (
+            f"the parent tracked pid {marker['pid']}, the child is {child.pid}")
+        web["decision"] = entry_host.read_decision(web["desc"])
+    state = _job_state(box.svc, op, target)
+    return ({"status": r.status_code, "admission": (web.get("parent") or (0, None))[1],
+             "child_rc": web["child"].returncode if "child" in web else None, "job": state},
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
