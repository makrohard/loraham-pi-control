# Gate-1 code review request — T3

Please judge batch T3 of the loraham-pi-control architecture consolidation: tests that drive the same start/restart scenario through every entry path (the CLI, the console route with its detached job, the job runner alone, the boot-restore unit) and assert the same decision — started or not, the refusal class, the files touched, the live processes — allowing only the rendering to differ; real disagreements are recorded as known defects. Judge
the plan (below) and each code commit (the full diff is at the end) against the plan, the
batch's rules (test-only: no production change; every case tagged `intended` or
`known defect <id>`; red-before shown by a deliberate mutation; no startswith-only assertions
on decision-bearing strings; no network) and the report's 6-point block. Look in particular
for an assertion that cannot fail, a case whose tag does not match what it records, a fixture
that changes behaviour instead of observing it, and a claim in the report the diff does not
support.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

Answer form (one row per commit, then one final line):

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| … | … | … |

Final line: GREEN / GREEN WITH NOTES / RED

## The plan

# PLAN-T3 — the same decision across entry paths

Batch T3 of the architecture consolidation (next 0.x.y release). Test-only: `tests/` (plus this
plan and the code-review files); `lhpc/` and `testlab/` unchanged. Base: `origin/integration/1.0`,
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
job state, journal item).

- The web path runs the real route, the real `spawn_start_job` parent and the real handshake.
  Only `Lifecycle.spawn_job` is replaced: it returns this process's pid, so the parent tracks a
  real identity, and the child `_stack-start` runs in-process before the handshake reads its
  marker.
- Same `ControllerService` per path: `cli_main.ControllerService` returns the box's service.
- `conftest.py` gains a `root` parameter on `kiss_box` and `KissBox.live()`.
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
  child's pid and identity; this process is a real session member (`needs_session`). The only
  substitution is the spawn itself.
- **Host leakage.** `LHPC_RUNTIME_ROOT` is set per path; `INVOCATION_ID` only on the boot path.
  The firewall host readers are replaced on the scenario's box instance only.
- **Classifying by summary.** It is used only where no typed field exists (finding T1-N1). The
  comparison is exact equality across paths, never a substring.

## Findings expected (recorded as `known defect`, not fixed)

- T3-F1: the web parent's admission refusal is untyped (reason text only).
- T3-F2: the CLI and web firewall-gate refusal names a stale apply script; the job and boot
  apply re-render it.
- T1-F1 reappears as a file difference (band owner, firewall gate).

## Open questions (recommendation)

1. Boot-restore passes over an interactive main (no plan item). Recommendation: keep. It is
   documented as "interactive main — manual start", and the test asserts that skip.
2. Restart counts MANUAL_REQUIRED as failure on every path, while start counts it as success.
   Recommendation: a product decision for W4; the test records today's agreement.

## Commits

```
05719db T3: plan — the same decision across entry paths
12302e5 T3: cross-path decision tests — CLI, console, job runner, boot-restore
```

## The report

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

## Full diff of the code commits

```diff
diff --git a/tests/README.md b/tests/README.md
index 47b4a6c..67c2e59 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -51,6 +51,9 @@ in a file named after when or how a defect was found:
   contract) or `known defect <finding id>:` (recorded as is, changed only by the fix of that
   finding). Pinning the step order is its purpose, so it is the one place that names coordinator
   steps: through `ORDER_SEAMS` in its `conftest.py`, which a refactor that renames a step updates.
+  Beside it, `golden/test_same_decision_across_entry_paths.py` drives each start decision through
+  every entry path (the CLI, the console route with its detached job, the job runner alone, the
+  boot-restore unit) and asserts the same decision — only the rendering may differ.
 
 ## The rules
 
diff --git a/tests/golden/conftest.py b/tests/golden/conftest.py
index f49aabb..f605af6 100644
--- a/tests/golden/conftest.py
+++ b/tests/golden/conftest.py
@@ -244,23 +244,31 @@ class KissBox:
     def owned(self) -> list[str]:
         return owned(self.root)
 
+    def live(self) -> list[str]:
+        """The components whose LHPC-owned process is alive (a prior boot's record is not)."""
+        d = self.root / "state" / "owned"
+        recs = [json.loads(p.read_text()) for p in d.glob("*.json")] if d.is_dir() else []
+        return sorted(r["component"] for r in recs if _alive(r["pid"]))
+
 
 @pytest.fixture
 def kiss_box(tmp_path, monkeypatch, real_spawn, set_call):
-    """`kiss_box(callsign=True)` → a KissBox: kiss installed and built, daemon READY on 433, the
-    TNC's endpoint following its process, a callsign saved (unless callsign=False)."""
-    def _make(*, callsign=True):
-        (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
-        (tmp_path / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
+    """`kiss_box(callsign=True, root=tmp_path)` → a KissBox: kiss installed and built, daemon
+    READY on 433, the TNC's endpoint following its process, a callsign saved (unless
+    callsign=False)."""
+    def _make(*, callsign=True, root=None):
+        root = Path(root or tmp_path)
+        (root / "src" / "loraham-kiss-tnc").mkdir(parents=True)
+        (root / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
         fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _READY})
-        fake.listeners = _TncEndpoint(tmp_path)
-        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
+        fake.listeners = _TncEndpoint(root)
+        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
         svc.bootstrap(apply=True)
         monkeypatch.setattr(ControllerService, "_lifecycle", lambda s: Lifecycle(
             s._paths, s.stacks(), s.config(), s._system, spawn=real_spawn))
         if callsign:
             set_call(svc)
-        return KissBox(tmp_path, fake, svc)
+        return KissBox(root, fake, svc)
     return _make
 
 
diff --git a/tests/golden/test_same_decision_across_entry_paths.py b/tests/golden/test_same_decision_across_entry_paths.py
new file mode 100644
index 0000000..dd32d34
--- /dev/null
+++ b/tests/golden/test_same_decision_across_entry_paths.py
@@ -0,0 +1,464 @@
+"""Same input, same decision — whichever entry path runs it.
+
+One scenario is driven through every entry path that can start a stack, each on a fresh box:
+
+- `cli`  — `lhpc stack <op> <target> --yes` (`main()`: the plan, then the apply);
+- `web`  — `POST /action` on the console: the plan in the route, then the real `spawn_start_job`
+           parent and handshake, with the spawned `lhpc _stack-start` child run here in-process;
+- `job`  — the detached job runner alone (`lhpc _stack-start …` over a tracked attempt);
+- `boot` — the boot-restore unit (`lhpc autostart --run-service`) replaying a prior boot's record.
+
+The DECISION compared across paths: `started`, the `refusal` class (the typed `data` key where
+one exists, `blockers` for a plan that lists owners, `manual_required`/`unverified`/`ok` from the
+outcomes, else the deciding result's summary), the `files` the path left under the runtime root
+(each path's own bookkeeping — attempt and job markers, the boot journal and its evidence, the
+web session key and job logs, lock files — excluded) and the LHPC-owned processes `live`
+afterwards. Only the rendering may differ — exit code, flash or confirm page, job state, journal
+item — and each path's rendering is asserted as well.
+
+Every case's docstring opens with `intended:` or `known defect <id>:` (tests/README.md, `golden/`).
+"""
+
+import json
+import os
+import re
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
+    callsign=True)` → {started, refusal, files, live, render}. `setup(box)` prepares the scenario
+    on the path's fresh box (it may replace `box.svc`); `evidence` is the prior boot's record the
+    `boot` path replays."""
+    decided, booted, web = [], [], {}
+    depth = [0]
+
+    def _spy(real, into):          # the outermost start/restart result; boot-restore's own
+        def wrapper(self, *a, **k):
+            depth[0] += into is decided
+            try:
+                res = real(self, *a, **k)
+            finally:
+                depth[0] -= into is decided
+            if into is booted or depth[0] == 0:
+                into.append(res)
+            return res
+        return wrapper
+    monkeypatch.setattr(ControllerService, "start", _spy(ControllerService.start, decided))
+    monkeypatch.setattr(ControllerService, "restart", _spy(ControllerService.restart, decided))
+    monkeypatch.setattr(ControllerService, "boot_restore_run",
+                        _spy(ControllerService.boot_restore_run, booted))
+
+    # web: the parent spawns "this process"; its handshake runs the child it would have spawned
+    real_handshake, real_spawn_start = (ControllerService._web_admit_handshake,
+                                        ControllerService.spawn_start_job)
+
+    def spawn_job(self, name, argv, cwd, env=None):
+        web["argv"] = argv
+        return f"{name}.log", os.getpid()
+
+    def handshake(self, log, aid):
+        web["child_rc"] = cli_main.main(web["argv"][3:])
+        return real_handshake(self, log, aid)
+
+    def spawn_start(self, *a, **k):
+        web["parent"] = real_spawn_start(self, *a, **k)
+        return web["parent"]
+    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
+    monkeypatch.setattr(ControllerService, "_web_admit_handshake", handshake)
+    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn_start)
+    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "2")
+    monkeypatch.setenv("LHPC_WEB_ADMIT_TIMEOUT_S", "2")
+
+    def _run(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
+             callsign=True):
+        box = kiss_box(root=tmp_path / path, callsign=callsign)
+        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(box.root))
+        monkeypatch.setattr(cli_main, "ControllerService", lambda: box.svc)
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
+    """kiss saved to listen beyond loopback; the firewall installed but not verified current
+    (the host's integration state and its check are host readers — set on this box only)."""
+    cfgmod.save_stack_config(box.svc._paths, "kiss", {"kiss_host": "0.0.0.0", "kiss_port": "9001"},
+                             box.svc._config_band("kiss", ""))
+    box.svc._invalidate_config()
+    box.svc._fw_integration_state = lambda: "present"
+    box.svc.firewall_status = lambda: {"config_ok": True, "live_ok": False}
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
+    box.svc = ControllerService(manifest_path=DATA / "scope2_manifest.toml",
+                                system=box.fake.system, paths=Paths(runtime_root=box.root))
+    cfgmod.update_stack_config(box.svc._paths, "ostack2", {"rp": "LEGACY"})
+
+
+
+# --- the decisions -----------------------------------------------------------------------------
+
+REFUSED_NOTHING = {"started": False, "files": [], "live": []}
+
+
+def test_success(entry):
+    """intended: a clean start succeeds on every path with the same files and the TNC live."""
+    res = _all(entry, "start", "kiss")
+    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
+    assert _renders(res) == {
+        "cli": {"rc": 0},
+        "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
+        "job": {"rc": 0, "job": ("done", True)},
+        "boot": {"rc": 0, "journal": "done", "items": ["succeeded"], "skipped": []}}
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
+    """known defect T1-F1: a running band owner stops every path; the web asks first (the confirm
+    page, nothing written) while the CLI, the job runner and boot-restore refuse in the apply's
+    preflight AFTER resetting the band's feed floor — the same input leaves different files."""
+    res = _all(entry, "start", "kiss", _band_owner)
+    refusal = "Cannot run 'kiss': meshtastic must be stopped first."
+    floor = {"started": False, "files": ["state/daemon-feed-floor-433"], "live": []}
+    assert _decisions(res) == {
+        "cli": {"refusal": refusal, **floor},
+        "web": {"refusal": "blockers", **REFUSED_NOTHING},
+        "job": {"refusal": refusal, **floor},
+        "boot": {"refusal": refusal, **floor}}
+    assert _renders(res)["web"] == {"status": 200, "admission": None, "child_rc": None,
+                                    "job": None}
+
+
+def test_firewall_gate(entry):
+    """known defect T3-F2: the firewall gate refuses on every path, but the CLI and the web
+    refuse at their plan (render=False) and name the apply script as the remedy WITHOUT
+    re-rendering it — the script they point at is stale and does not carry the newly exposed
+    listener — while the job runner and boot-restore refuse at the apply, which re-renders it
+    (and, T1-F1, has reset the feed floor): the same input leaves different files."""
+    res = _all(entry, "start", "kiss", _firewall_pending)
+    applied = {"started": False, "live": [], "refusal": "firewall_gate",
+               "files": ["config/files/firewall/firewall-apply.sh", "state/daemon-feed-floor-433"]}
+    assert _decisions(res) == {"cli": {"refusal": "firewall_gate", **REFUSED_NOTHING},
+                               "web": {"refusal": "firewall_gate", **REFUSED_NOTHING},
+                               "job": applied, "boot": applied}
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
+    assert _renders(res)["boot"] == {"rc": 0, "journal": "failed", "items": ["failed"],
+                                     "skipped": []}
+
+
+def test_tx_without_high_power_permission(entry):
+    """intended: a stack saved to transmit at POWER=20 without the running daemon's high-power
+    permission is BLOCKED on every path (no path's plan checks it; each apply does), nothing
+    spawned."""
+    res = _all(entry, "start", "kiss", _tx_without_permission)
+    _same(res, started=False, refusal="blocked", files=["state/daemon-feed-floor-433"], live=[])
+
+
+def test_pending_config_journal(entry):
+    """intended: a config journal a crashed save left behind is finished by every path's process
+    at startup (the CLI, the web's child, the job runner, the boot unit) — the torn file restored,
+    the journal gone — and the start then succeeds alike."""
+    res = _all(entry, "start", "kiss", _pending_config_journal)
+    _same(res, started=True, refusal="ok", live=["loraham-kiss-tnc"],
+          files=sorted(["config/stacks/kiss.toml", "state/config-txn.json", *KISS_STARTED]))
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
+
+
+def test_interactive_launch(entry):
+    """intended: an interactive main is never spawned; the CLI, the web and the job runner count
+    a start whose only shortfall is MANUAL_REQUIRED as success (exit 0, job done), with the same
+    files. Boot-restore never replays an interactive main: its plan skips it, by design."""
+    res = _all(entry, "start", "chat", _chat_installed,
+               evidence=("chat", "loraham-chat", "433"))
+    manual = {"started": True, "refusal": "manual_required", "live": [],
+              "files": ["config/files/lorachat.conf", "state/daemon-feed-floor-433",
+                        "state/interactive/chat.show"]}
+    assert _decisions(res) == {"cli": manual, "web": manual, "job": manual,
+                               "boot": {"started": False, "refusal": "ok", "files": [],
+                                        "live": []}}
+    assert _renders(res) == {
+        "cli": {"rc": 0},
+        "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
+        "job": {"rc": 0, "job": ("done", True)},
+        "boot": {"rc": 0, "journal": "no-plan", "items": [],
+                 "skipped": ["interactive main — manual start"]}}
+
+
+def test_unverified_termination(entry):
+    """intended: a TNC whose endpoint never comes up is cleaned up and reported UNVERIFIED on
+    every path — a failure everywhere (exit 1, job failed, boot item failed)."""
+    res = _all(entry, "start", "kiss", lambda b: b.endpoint_never_up())
+    _same(res, started=False, refusal="unverified", live=[],
+          files=["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433"])
+    assert {p: r["render"].get("rc") for p, r in res.items()} == {
+        "cli": 1, "web": None, "job": 1, "boot": 0}
+    assert res["web"]["render"]["job"] == ("failed", True)
+    assert res["boot"]["render"]["items"] == ["failed"]
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
