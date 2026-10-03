# Gate-1 code review request — T1

Please judge batch T1 of the loraham-pi-control architecture consolidation: golden (characterization) tests of the six coordinating operations — start, restart, stop, save_config_bundle, build, boot-restore — recording for fixed scenarios the result fields, the files written/removed, the markers and journal states, and the order of the phases. Judge
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

# PLAN-T1 — golden / characterization tests of the six coordinating operations

Batch T1 of the architecture consolidation (next 0.x.y release). Test-only: the diff touches
`tests/` (plus this plan and the code-review report files); `lhpc/` and `testlab/` stay unchanged.
Base: `origin/integration/1.0`.

## Goal

One module per operation under `tests/golden/` records, for 6–8 fixed scenarios, what the
operation does TODAY: the result fields (ok, summary, detail heads, data keys, next_commands,
per-component outcomes), the files written/removed under the runtime root, the markers and
journal states, and the ORDER of its phases (admission → locks → recheck → mutation →
verification → finalization). Each case is tagged in its docstring `intended:` or
`known defect <id>:`. Later refactors (W2a build plan, W2c unit migration, W3 config-save split,
W4 start/restart) run this set first.

## Shared harness — `tests/golden/conftest.py` (new)

- `phases`: wraps the seams in ONE table, `ORDER_SEAMS` (delegating to the real code), and logs
  `phase:subject` labels; admission/lock/recheck on first occurrence, mutations each time.
- `run_op(root, fn)` → `Run`: `fields` (decision-bearing ActionResult fields), `files` (tree diff,
  lock files excluded, owned-record pid/nonce normalized), `phases`, `kinds`.
- `kiss_box`: kiss installed + built over a FakeSystem daemon READY on 433; the TNC spawns a real
  `sleep` (the existing `real_spawn` fixture), and its ready endpoint follows that process, so a
  start sees it come up and a stop sees it go (no endpoint toggling by hand).
- `prior_boot`, `uninstall_guard`, `interrupted_install`, `held_lock` (a lock held by another
  process, so contention is real).
- `_tagged` (autouse): every golden case's docstring opens with its tag.

## Per operation (code path today → scenarios)

| op | owner (file:line) | scenarios |
|---|---|---|
| start | `service_lifecycle_ops.py:641` (`start`), `:783/:883` (`_start_impl[_inner]`), `services.py:2558/2635` (admission, lifecycle guard), `lifecycle.py:437` | plan+apply happy; missing callsign (chat); admission; interrupted install; band owner; unverified termination; entry hook; interactive launch |
| stop | `service_lifecycle_ops.py:2373/2426`, `lifecycle.py:1255` | plan; running; nothing runs; unowned process; no cessation; contended lock |
| restart | `service_lifecycle_ops.py:2680/3001` | plan; running; unverified stop aborts; hook refusal; admission; band owner |
| save_config_bundle | `service_params.py:1054`, `config.py:76/1844/1891/1901` | stopped; running (restart marker); validation; journal recovered; journal unrecoverable; write fails mid-way; malformed per-stack file |
| build | `service_lifecycle_ops.py:3166`, `services.py:359` (source guard), `lifecycle.py:350` | plan+apply; failed step; marker written last (meshcore); not installed; admission; interrupted install; contended source lock |
| boot-restore | `service_boot_restore.py:280/307/470/516/560` | restore; nothing to restore; disabled; stop intent; refused before hook (pending); failed after hook (consumed); admission |

Six modules: `test_golden_{start,stop,restart,save_config_bundle,build,boot_restore}.py`.

## Tests, red-before

Golden tests characterize existing behaviour, so "red before" means: each module fails on a
deliberately broken copy of the behaviour. One mutation per operation (applied, run, reverted;
never committed): start — skip the authoritative recheck under the locks; stop — no stop intent;
restart — start after an unverified stop; save — no journal before the writes; build — marker
stamped before the inputs sidecar; boot-restore — the `attempting` claim not made durable.

## Risks and how they are ruled out

- **Pinning private names (rule 1)** — the golden set's purpose is the step order. The names live
  only in `ORDER_SEAMS` (one table); the README says so. Every wrapper delegates, so behaviour
  is unchanged.
- **Flakiness from real processes** — the spawned `sleep` is reaped by the existing autouse
  fixture; the endpoint follows `/proc/<pid>/stat` (a zombie counts as gone); the only patched
  timing is "the process outlives SIGTERM" (`_wait_ceased`), a collaborator stub with a comment.
- **Host leakage** — the existing autouse isolation (runtime root, HOME, firewall, process table)
  applies; the boot id and the two boot-restore host gates are pinned by `prior_boot`.
- **Tmp paths in assertions** — the tree diff is runtime-relative; detail lines are compared as
  `[tag] subject` heads, or by `re.fullmatch` where a path is part of the line.
- **File-disjointness** — only new files under `tests/golden/` plus `tests/README.md`.

## Findings expected (recorded, not fixed)

- T1-F1: a start the preflight refuses (band owner; same path for the firewall gate and config
  ambiguity) has already reset the band's daemon feed floor (`state/daemon-feed-floor-<band>`).

## Seams that do not exist (finding, not added)

- A start/restart has no single typed "refusal class" field: admission, enforce_fields,
  firewall_gate and reason are typed; band owner, interrupted install and lock contention are
  identified by their summary only.

## Open questions (with recommendation)

1. Compare summaries byte for byte? Recommended yes for the golden set: it records what the
   operator sees today; a deliberate wording change updates one expected string.
2. Golden location: `tests/golden/` (a new layer directory) rather than spread over `core/` —
   recommended, so "run the golden set first" is one path.

## Commits

```
f42f220 T1: plan — golden tests of the six coordinating operations
6b18719 T1: golden harness and the start golden module
a47c536 T1: stop and restart golden modules
adcae90 T1: save_config_bundle golden module
8078c54 T1: build golden module
80afd93 T1: boot-restore golden module
```

## The report

# Code report — T1: golden / characterization tests of the six coordinating operations

Base `origin/integration/1.0` (f257831). Test-only batch: no production change.
`git diff --stat origin/integration/1.0..HEAD -- lhpc testlab` → empty (checked before this
commit and again before the push).

## Commits

| sha | subject | files | tests | red-before |
|---|---|---|---|---|
| f42f220 | T1: plan — golden tests of the six coordinating operations | `plans/PLAN-T1.md` | — | — |
| 6b18719 | T1: golden harness and the start golden module | `tests/golden/conftest.py`, `tests/golden/test_golden_start.py`, `tests/README.md` | 8 | yes, mutation "start" |
| a47c536 | T1: stop and restart golden modules | `tests/golden/test_golden_stop.py`, `tests/golden/test_golden_restart.py` | 6 + 6 | yes, mutations "stop", "restart" |
| adcae90 | T1: save_config_bundle golden module | `tests/golden/test_golden_save_config_bundle.py` | 7 | yes, mutation "save_config_bundle" |
| 8078c54 | T1: build golden module | `tests/golden/test_golden_build.py` | 7 | yes, mutation "build" |
| 80afd93 | T1: boot-restore golden module | `tests/golden/test_golden_boot_restore.py` | 7 | yes, mutation "boot-restore" |

Every case's docstring opens with its tag. 40 are `intended:` and 1 is
`known defect T1-F1:` (start, band owner).

## Red-before (golden meaning: the module fails on a deliberately broken copy)

Each mutation was applied to the working tree, the module was run, and the mutation was reverted
with `git checkout -- <file>`. None was committed. Command per mutation:
`python -m pytest -q -p no:cacheprovider tests/golden/test_golden_<op>.py`.

| op | mutation (file) | result |
|---|---|---|
| start | the authoritative recheck under the locks skipped: `_r = None` in place of `self._start_outer_refusal(...)` (`lhpc/core/service_lifecycle_ops.py`) | 6 failed, 2 passed |
| stop | `self._write_stop_intent([target])` → `pass` (`service_lifecycle_ops.py`) | 2 failed, 4 passed |
| restart | `if not stopped.ok:` → `if False:`, i.e. start after an unverified stop (`service_lifecycle_ops.py`) | 1 failed, 5 passed |
| save_config_bundle | the journal write before the targets → `pass` (`lhpc/core/config.py`) | 5 failed, 2 passed |
| build | completion marker stamped before the inputs sidecar (`lhpc/core/lifecycle.py`) | 1 failed, 6 passed |
| boot-restore | the claim hook's durable `attempting` write → `if False:` (`lhpc/core/service_boot_restore.py`) | 1 failed, 6 passed |

The tree was clean after the runs (`git status --short` showed only the new test files).

## Findings recorded (not fixed in this batch)

- **T1-F1 (known defect, recorded in `test_golden_start.py::test_refused_by_band_owner`).**
  `ControllerService.start` resets the band's daemon feed floor (`clear_daemon_feed`, writes
  `state/daemon-feed-floor-433`) under the locks, right after the hook and before
  `_start_preflight_refusal`. A start that the preflight then refuses has already done that
  mutation. The preflight covers the firewall gate, config ambiguity and the band owner. Owner:
  `lhpc/core/service_lifecycle_ops.py`, the feed clear in `start()`
  (`for _b in sorted(self._operation_bands(...)): self.clear_daemon_feed(_b)`) before
  `_start_impl` → `_start_preflight_refusal`. The fix item is to move the preflight before the
  feed clear, or the clear after it.
- **T1-N1 (missing seam, no code added).** start/restart refusals have no single typed class.
  `admission_blocked`, `enforce_fields`, `firewall_gate` and `reason` are typed; the band owner,
  interrupted install, lock contention and runtime-root refusals are identifiable only by their
  summary. The golden set compares those summaries exactly. A refactor that wants a typed
  refusal adds it, and the goldens then gain one `data_keys` entry each.
- **Observation (intended per design, noted for reviewers).** Stopping a stack that is not
  running still releases the daemon band no other client needs: the daemon leg runs and the
  feed floor is reset (`test_golden_stop.py::test_stop_when_nothing_runs`). The restart's stop
  leg does the same, so the daemon outcome rows read `stopped` then `verified`.

## Simplicity guardrails

- No new abstraction in production code; no production code at all. In the tests: plain
  fixtures and functions; the one class is `Run`, a record of one run with no behaviour. The
  others are `KissBox` (the box handle) and `_TncEndpoint` (an iterable the FakeSystem reads as
  its listener table). No framework, no registry, no result type.
- Extractions: none. Line counts: production before/after are unchanged (0 lines touched).
  Tests: +1277 lines in `tests/golden/` (conftest 332, six modules 131–197 each) and +7 in
  `tests/README.md`.
- Dependencies of the new tests: `lhpc.core.{services, lifecycle, config, boot_restore,
  reslock, restart_required, paths, probes.backends, service_base, updater_units,
  service_boot_restore}` and the existing root fixtures `real_spawn`, `set_call`.
- Typed outcomes: none added.

## 6-point block

1. **CONTRACTS.** None changed. Contracts the goldens now pin, each with its owner:
   - `ActionResult` fields (`lhpc/core/service_base.py:110`).
   - `Outcome` values (`lhpc/core/outcomes.py:16`).
   - start/stop/restart signatures and the `_before_start_locked`/`_before_restart_locked` hook
     contract: runs after every lock and the recheck, before the first mutation; an
     `ActionResult` return cancels (`service_lifecycle_ops.py:641/2373/2680`).
   - Lock keys and their sorted acquisition (`services.py:2443/2635`).
   - Config journal path and format `state/config-txn.json` (`config.py:1744/1891`).
   - Restart-required marker schema (`restart_required.py:103`).
   - Build marker text `"lhpc build complete\n"` plus `consumed` lines (`lifecycle.py:43`).
   - Boot-restore journal states (`boot_restore.py:156`, `service_boot_restore.py:280/470/516`).
2. **INVARIANTS + TESTS** (docs/architecture.md, Safety model):
   - Identity-verified stopping: `test_golden_stop.py::test_unowned_process_is_manual_required`
     and `::test_process_that_does_not_cease`.
   - Locking (a contended operation refuses at once, naming the holder):
     `test_golden_stop.py::test_contended_lock_refuses` and
     `test_golden_build.py::test_contended_source_lock_refuses`.
   - Config as a transaction (validate first, journal, roll back, recover before a writer):
     `test_golden_save_config_bundle.py`, all 7 cases.
   - Truthful outcomes (verified only when ceased AND endpoint gone; UNVERIFIED cleanup):
     `test_golden_start.py::test_unverified_termination_is_cleaned_up` and
     `test_golden_restart.py::test_unverified_stop_aborts_before_any_start`.
   - Source transactions block mutation: the `*_interrupted_install` cases in start, build and
     boot-restore.
   - Boot restore replays only through the gated start: `test_golden_boot_restore.py`.
   No invariant is affected by the diff (test-only).
3. **KNOWN FAILURE CLASSES.**
   - Fakes: the real `FakeSystem` and the real `Lifecycle` with the existing `real_spawn`, so the
     ownership record carries a real `/proc` identity. No hand-written fake of a production
     signature; every `ORDER_SEAMS` wrapper takes `*args, **kwargs` and delegates.
   - Probes on EIO/EACCES/ENOTDIR: not in scope (no probe touched); the ENOSPC write failure is
     covered (`test_failed_write_rolls_back`).
   - KeyboardInterrupt through cleanup: not in scope (no cleanup code touched).
   - Same decision across entry paths: batch T3.
   - Stacked-only conflicts: the new files are only under `tests/golden/`, plus three
     paragraph-level lines in `tests/README.md` (the `golden/` bullet). No `lhpc/` or `testlab/`
     file. CHANGELOG not touched (see deviations).
4. **TEST RULES.**
   - Red-before: per module, by mutation (table above).
   - Decision-bearing strings are compared with `==` or `re.fullmatch`, never `startswith` alone.
   - No unchecked error return: every seeding call asserts `.ok`.
   - No network: the `held_lock` helper spawns a local interpreter and the TNC is a local
     `sleep`.
5. **WHOLE TEST DIRECTORIES.**
   - `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` → `461 passed, 5 skipped
     in 15.02s`. The golden set alone was run three times in a row: `41 passed` each.
   - `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` →
     `All checks passed!`.
   - No signature changed, so there is nothing to grep for.
   - `tests/README.md` lives in the `tests/` root, but running the whole `tests/` directory would
     be the forbidden full-suite run. The README's one consumer, the repo hygiene set
     (`tests/repo`), was run.
6. **ADVERSARIAL SELF-REVIEW.** Found and fixed before this report:
   - (a) A static listener made every stop read `endpoint_still_present` and a second start
     spawn a duplicate. The endpoint now follows the owned process.
   - (b) The first `held_lock` helper passed a `str` root and failed. Fixed to `Path`.
   - (c) `startswith` on the busy summary was replaced by `re.fullmatch`.
   - (d) The build-marker order was asserted only by the final files. It now records the two
     stamps in the phase log, so the mutation can fail it.
   - (e) Two unused-variable and import-order nits.
   The docs and this report describe exactly the diff.

## Deviations

- **No CHANGELOG line.** The batch is test-only, an operator sees no change, and FILES limits
  T1 to `tests/`. The batch's FILES list wins over the general rule; the handler adds a line at
  the release commit if wanted.
- **One harness file instead of a helper module.** Rule 7 forbids sibling-test imports, so the
  shared code is fixtures in `tests/golden/conftest.py`; the tests import nothing from it.
- **Private names.** The golden set names private coordinator steps by design. They are
  confined to `ORDER_SEAMS`, and `tests/README.md` states the exception.

## Full diff of the code commits

```diff
diff --git a/tests/README.md b/tests/README.md
index 0d44446..47b4a6c 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -44,6 +44,13 @@ in a file named after when or how a defect was found:
 - **`host/`** — the machine LHPC runs on: firewall, network, power, PKI, systemd units, host metrics,
   deployment scripts.
 - **`repo/`** — invariants of the repository itself: packaging, versions, README drift, suite hygiene.
+- **`golden/`** — the golden set: one characterization module per coordinating operation (start,
+  restart, stop, save_config_bundle, build, boot-restore), recording for a few fixed scenarios the
+  result fields, the files written/removed and the ORDER of its phases. Every refactor of those
+  operations runs it first. Each case's docstring opens with `intended:` (the behaviour is the
+  contract) or `known defect <finding id>:` (recorded as is, changed only by the fix of that
+  finding). Pinning the step order is its purpose, so it is the one place that names coordinator
+  steps: through `ORDER_SEAMS` in its `conftest.py`, which a refactor that renames a step updates.
 
 ## The rules
 
diff --git a/tests/golden/conftest.py b/tests/golden/conftest.py
new file mode 100644
index 0000000..f49aabb
--- /dev/null
+++ b/tests/golden/conftest.py
@@ -0,0 +1,332 @@
+"""Shared fixtures of the golden (characterization) set — see tests/README.md, `golden/`.
+
+A golden case records what one coordinating operation does TODAY, for one fixed scenario: the
+result fields, the files it wrote/removed under the runtime root, and the ORDER of its phases
+(admission → locks → recheck → mutation → verification → finalization). Each case is tagged in its
+docstring: "intended" (the behaviour is the contract) or "known defect <id>" (recorded as is).
+
+The order is observed through the seams named in `ORDER_SEAMS` below — the ONE place a refactor
+that renames a coordinator step updates. That is the golden set's sanctioned exception to rule 1
+(no pinning helper names): pinning the step order is its purpose, and it pins it here only.
+"""
+
+from __future__ import annotations
+
+import contextlib
+import json
+import re
+import subprocess
+import sys
+from pathlib import Path
+
+import pytest
+
+from lhpc.core import boot_restore, reslock, restart_required
+from lhpc.core import config as cfgmod
+from lhpc.core import lifecycle as lifecycle_mod
+from lhpc.core.lifecycle import Lifecycle
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem, Listener
+from lhpc.core.services import ControllerService
+
+
+def _rel(paths, path) -> str:
+    return str(Path(path).relative_to(paths.runtime_root))
+
+
+def _journal(paths, journal) -> str:
+    items = ",".join(f"{i.get('target')}={i.get('state')}" for i in journal.get("items") or [])
+    return f"journal:{journal.get('state')}[{items}]"
+
+
+# (owner, attribute, phase label). The label is a format string whose "{0}"/"{1}" take the call's
+# first positional arguments after self (stringified: an object's `.id`, else str), e.g. the lock
+# key or the component — or a callable of those arguments.
+ORDER_SEAMS = (
+    (ControllerService, "_admission_guard", "admission"),
+    (ControllerService, "_admit", "admission"),
+    (ControllerService, "_config_stable", "config-stable"),
+    (ControllerService, "_acquire_key", "lock:{1}"),
+    (reslock, "operation_lock", "lock:{1}"),
+    (ControllerService, "_start_outer_refusal", "recheck:start"),
+    (ControllerService, "_identity_refusal", "recheck:identity"),
+    (ControllerService, "_start_preflight_refusal", "recheck:preflight"),
+    (ControllerService, "clear_daemon_feed", "mutate:feed-floor:{0}"),
+    (ControllerService, "write_config_files", "mutate:config-files:{0}"),
+    (Lifecycle, "start", "mutate:spawn:{1}"),
+    (Lifecycle, "stop", "mutate:signal:{0}"),
+    (Lifecycle, "_invalidate_build_marker", "mutate:invalidate-marker"),
+    (lifecycle_mod, "run_job", "mutate:build-step"),
+    (ControllerService, "_verify_band_up", "verify:conf:{0}"),
+    (ControllerService, "_ready_endpoints_present", "verify:endpoints:{0}"),
+    (ControllerService, "_run_post_start", "verify:post-start"),
+    (ControllerService, "_set_running_band", "final:running-band:{1}"),
+    (ControllerService, "_capture_start_composition", "final:known-working:{0}"),
+    (restart_required, "clear_marker", "final:clear-restart-marker:{1}"),
+    (ControllerService, "_clear_stop_intent", "final:clear-stop-intent:{0}"),
+    (ControllerService, "_write_stop_intent", "final:stop-intent:{0}"),
+    # config transaction (save_config_bundle)
+    (cfgmod, "config_lock", "lock:config"),
+    (cfgmod, "_finish_pending_journal", "recheck:config-journal"),
+    (cfgmod, "_atomic_write", lambda paths, path, *a: f"mutate:write:{_rel(paths, path)}"),
+    # boot-restore journal (each durable write, with its run and item states)
+    (boot_restore, "write_journal", _journal),
+)
+
+# Phases whose repeats are not behaviour (re-entrant guards, a recheck run twice): recorded on
+# their first occurrence only. Mutations, verifications and finalizations are recorded each time.
+_ONCE = ("admission", "lock", "config-stable", "recheck")
+
+
+def _name(x) -> str:
+    return getattr(x, "id", None) or str(x)
+
+
+@pytest.fixture
+def phases(monkeypatch):
+    """The ordered phase log of the operation under test: every `ORDER_SEAMS` call, delegating to
+    the real implementation. Admission, lock and recheck labels are recorded on their FIRST
+    occurrence only (`_ONCE`). Call `phases.clear()` after the scenario's set-up."""
+    log: list[str] = []
+
+    def _wrap(owner, attr, label):
+        real = getattr(owner, attr)
+        is_method = isinstance(owner, type)
+
+        def wrapper(*args, **kwargs):
+            pos = args[1:] if is_method else args
+            text = (label(*pos) if callable(label)
+                    else label.format(*[_name(a) for a in pos], *([""] * 3)))
+            if not (text.split(":", 1)[0] in _ONCE and text in log):
+                log.append(text)
+            return real(*args, **kwargs)
+        monkeypatch.setattr(owner, attr, wrapper)
+
+    for owner, attr, label in ORDER_SEAMS:
+        _wrap(owner, attr, label)
+    return log
+
+
+def phase_kinds(log):
+    """The phase sequence with consecutive repeats folded: ['admission', 'lock', 'recheck', …]."""
+    out: list[str] = []
+    for e in log:
+        k = e.split(":", 1)[0]
+        if k == "config-stable":
+            k = "lock"
+        if not out or out[-1] != k:
+            out.append(k)
+    return out
+
+
+_OWNED = re.compile(r"__\d+__[0-9a-f]{32}\.json$")
+
+
+def tree(root: Path) -> dict[str, str]:
+    """Every file under `root` → its text (or '<bytes>'), runtime-relative; an ownership record's
+    pid and nonce read `<pid>__<nonce>`. Lock files are left out: which locks an operation takes,
+    and when, is the `phases` log's job (a lock file outlives its lock by design)."""
+    out = {}
+    for p in sorted(Path(root).rglob("*")):
+        rel = str(p.relative_to(root))
+        if not p.is_file() or p.is_symlink() or rel.startswith("state/locks/") or rel == "config/.lock":
+            continue
+        try:
+            text = p.read_text()
+        except (UnicodeDecodeError, OSError):
+            text = "<bytes>"
+        out[_OWNED.sub("__<pid>__<nonce>.json", rel)] = text
+    return out
+
+
+def tree_diff(before: dict, after: dict) -> dict:
+    """{'added': [...], 'removed': [...], 'changed': [...]} — sorted runtime-relative paths."""
+    return {
+        "added": sorted(set(after) - set(before)),
+        "removed": sorted(set(before) - set(after)),
+        "changed": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
+    }
+
+
+def owned(root: Path) -> list[str]:
+    """The components with an ownership record under state/owned (sorted)."""
+    d = Path(root) / "state" / "owned"
+    return sorted(json.loads(p.read_text())["component"] for p in d.glob("*.json")) if d.is_dir() else []
+
+
+_HEAD = re.compile(r"^\s*\[([^\]]+)\]\s*([^\s:]*)")
+_DAEMON_PARAM = re.compile(r"^\s*\[(ok|warn)\] (433|868): ")
+
+
+def result_fields(res) -> dict:
+    """The decision-bearing fields of an ActionResult, for an exact comparison: `heads` is each
+    detail line's `[tag] subject` (paths and prose dropped; the daemon's per-parameter push lines
+    are owned by tests/stacks/test_daemon_params.py and left out)."""
+    heads = []
+    for d in res.details:
+        m = _HEAD.match(d)
+        if m and not _DAEMON_PARAM.match(d):
+            heads.append(f"[{m.group(1)}] {m.group(2)}".strip())
+    return {"ok": res.ok, "summary": res.summary, "data_keys": sorted(res.data or {}),
+            "next_commands": list(res.next_commands), "heads": heads,
+            "outcomes": [(r.component, r.outcome.value) for r in res.results]}
+
+
+class Run:
+    """One operation run: `res` (the ActionResult), `fields` (`result_fields`), `files` (the
+    `tree_diff` it caused), `phases` (a copy of the phase log it produced) and `kinds` (that
+    log folded to its phase sequence)."""
+
+    def __init__(self, res, files, log):
+        self.res, self.fields, self.files, self.phases = res, result_fields(res), files, list(log)
+        self.kinds = phase_kinds(log)
+
+
+@pytest.fixture
+def run_op(phases):
+    """`run_op(root, fn)` → Run: clears the phase log, calls `fn()`, and records what changed."""
+    def _run(root, fn):
+        phases.clear()
+        before = tree(root)
+        res = fn()
+        return Run(res, tree_diff(before, tree(root)), phases)
+    return _run
+
+
+@pytest.fixture(autouse=True)
+def _tagged(request):
+    """Every golden case says what it records: its docstring opens with `intended:` or
+    `known defect <finding id>:`."""
+    doc = (request.function.__doc__ or "").strip()
+    assert re.match(r"(intended|known defect [\w-]+):", doc), (
+        f"{request.node.name}: a golden case's docstring must open with 'intended:' or "
+        "'known defect <id>:'")
+
+
+# --- the one box the lifecycle goldens run on ----------------------------------------------------
+# kiss over a daemon already serving 433: the daemon is not spawned (only its CONF SETs run), the
+# TNC is endpoint-ready on 127.0.0.1:8001 and spawns a real detached `sleep` (ownership needs a
+# complete /proc identity), so start → stop → restart run their real coordinators end to end.
+
+_READY = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
+
+
+def _alive(pid: int) -> bool:
+    try:
+        with open(f"/proc/{pid}/stat") as f:
+            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
+    except (OSError, IndexError):
+        return False
+
+
+class _TncEndpoint:
+    """The TNC's ready listener 127.0.0.1:8001, present exactly while an LHPC-owned TNC process is
+    alive — the real readiness evidence, so a stop sees it vanish and a start sees it appear.
+    `up=False` models a TNC that never opens its port."""
+
+    def __init__(self, root: Path):
+        self.root, self.up = Path(root), True
+
+    def __iter__(self):
+        d = self.root / "state" / "owned"
+        live = self.up and d.is_dir() and any(
+            _alive(json.loads(p.read_text())["pid"]) for p in d.glob("loraham-kiss-tnc__*.json"))
+        return iter([Listener(family="ipv4", ip="127.0.0.1", port=8001, inode=1)] if live else [])
+
+
+class KissBox:
+    def __init__(self, root: Path, fake: FakeSystem, svc: ControllerService):
+        self.root, self.fake, self.svc = root, fake, svc
+
+    def endpoint_never_up(self) -> None:
+        self.fake.listeners.up = False
+
+    def owned(self) -> list[str]:
+        return owned(self.root)
+
+
+@pytest.fixture
+def kiss_box(tmp_path, monkeypatch, real_spawn, set_call):
+    """`kiss_box(callsign=True)` → a KissBox: kiss installed and built, daemon READY on 433, the
+    TNC's endpoint following its process, a callsign saved (unless callsign=False)."""
+    def _make(*, callsign=True):
+        (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
+        (tmp_path / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
+        fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _READY})
+        fake.listeners = _TncEndpoint(tmp_path)
+        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
+        svc.bootstrap(apply=True)
+        monkeypatch.setattr(ControllerService, "_lifecycle", lambda s: Lifecycle(
+            s._paths, s.stacks(), s.config(), s._system, spawn=real_spawn))
+        if callsign:
+            set_call(svc)
+        return KissBox(tmp_path, fake, svc)
+    return _make
+
+
+@pytest.fixture
+def prior_boot(monkeypatch):
+    """`prior_boot(root, stack="kiss", comp="loraham-kiss-tnc", band="433")` → the path of an
+    ownership record a PREVIOUS boot left behind (its process gone), the evidence boot-restore
+    replays; this boot's id is pinned, restore is enabled and the web integration reads proven
+    (the two host gates boot-restore checks before it plans)."""
+    from lhpc.core import service_boot_restore as sbr
+    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
+    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
+    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))
+
+    def _plant(root, stack="kiss", comp="loraham-kiss-tnc", band="433", pid=999999):
+        rec = {"launch_id": f"{comp}__{band or 'x'}__{pid}__{'ab' * 16}", "stack": stack,
+               "component": comp, "band": band, "pid": pid, "role": "", "launched_at": 1000,
+               "version": 1, "requested_target": stack, "start_scope": "stack",
+               "boot_id": "OLDBOOT", "starttime": "123", "pgid": pid, "sid": pid}
+        d = Path(root) / "state" / "owned"
+        d.mkdir(parents=True, exist_ok=True)
+        (d / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
+        return d / f"{rec['launch_id']}.json"
+    return _plant
+
+
+@pytest.fixture
+def uninstall_guard():
+    """`uninstall_guard(root)` plants the uninstall guard file, which closes task admission."""
+    from lhpc.core import updater_units
+
+    def _plant(root):
+        (Path(root) / updater_units.UNINSTALL_GUARD).write_text('{"pid": 1, "nonce": "x"}')
+    return _plant
+
+
+@pytest.fixture
+def interrupted_install():
+    """`interrupted_install(root)` leaves an unresolved source-transaction journal behind, as a
+    crashed install/update does; it blocks every source-touching operation until resolved."""
+    def _plant(root):
+        d = Path(root) / "state" / "source-txn"
+        d.mkdir(parents=True, exist_ok=True)
+        (d / "garbage.json").write_text("{ not valid")
+    return _plant
+
+
+@pytest.fixture
+def held_lock():
+    """`with held_lock(paths, key):` holds that operation lock from ANOTHER process, so the
+    contention is real (an in-process holder is waited on, not refused)."""
+    return _held_lock
+
+
+@contextlib.contextmanager
+def _held_lock(paths, key):
+    root = str(paths.runtime_root)
+    code = ("import sys\nfrom pathlib import Path\nfrom lhpc.core import reslock\n"
+            "from lhpc.core.paths import Paths\n"
+            f"with reslock.operation_lock(Paths(runtime_root=Path({root!r})), {key!r}, 'golden', 'x'):\n"
+            "    print('held', flush=True); sys.stdin.read()\n")
+    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
+                         stdout=subprocess.PIPE, text=True)
+    try:
+        assert p.stdout.readline().strip() == "held"
+        yield
+    finally:
+        p.stdin.close()
+        p.wait(timeout=10)
diff --git a/tests/golden/test_golden_boot_restore.py b/tests/golden/test_golden_boot_restore.py
new file mode 100644
index 0000000..1a25d3c
--- /dev/null
+++ b/tests/golden/test_golden_boot_restore.py
@@ -0,0 +1,163 @@
+"""Golden: `ControllerService.boot_restore_run` — what the boot-restore driver does today, in order.
+
+Phases: driver admission → the journal (`state/boot-restore.json`) written `running` with every
+item `pending` → per item the normal gated start, whose hook (after the start's locks and
+recheck) durably marks the item `attempting` → the item settled `succeeded`/`failed` with its
+evidence (the prior boot's ownership record) pruned → the run's final state. A start refused
+BEFORE its hook leaves the item `pending` and its evidence in place (the run is truncated); one
+refused after it consumes the evidence. The two host gates (restore enabled, web integration
+proven) and the boot id are pinned by `prior_boot`.
+"""
+
+import json
+
+import pytest
+
+from lhpc.core.services import ControllerService
+
+pytestmark = pytest.mark.needs_session
+
+EVIDENCE = "state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json"
+START_LOCKS = ["config-stable", "lock:source-txn-index", "lock:claim.loraham.daemon-socket.433",
+               "lock:claim.loraham.radio.433", "lock:claim.tcp.port.8001", "lock:lifecycle.kiss",
+               "lock:source.src/loraham-daemon", "lock:source.src/loraham-kiss-tnc",
+               "recheck:start", "recheck:identity"]
+DRIVER = ["admission", "lock:controller-task-admission"]
+
+
+def _journal(root):
+    j = json.loads((root / "state" / "boot-restore.json").read_text())
+    return j["state"], [(i["target"], i["band"], i["state"]) for i in j["items"]], j
+
+
+def _fields(summary, ok=True, heads=()):
+    return {"ok": ok, "summary": summary, "data_keys": ["driver_completed"], "next_commands": [],
+            "heads": list(heads), "outcomes": []}
+
+
+def test_restores_the_prior_boot(kiss_box, prior_boot, run_op):
+    """intended: pending → attempting (inside the start, after its locks and recheck, before its
+    first mutation) → succeeded → done; the old evidence is replaced by the new launch."""
+    box = kiss_box()
+    prior_boot(box.root)
+    run = run_op(box.root, box.svc.boot_restore_run)
+    assert run.fields == _fields("Boot restore: 1 restored.")
+    assert run.res.data == {"driver_completed": True}
+    assert run.phases == DRIVER + ["journal:running[kiss=pending]"] + START_LOCKS + [
+        "journal:running[kiss=attempting]", "mutate:feed-floor:433", "recheck:preflight",
+        "mutate:spawn:loraham-kiss-tnc", "verify:endpoints:loraham-kiss-tnc", "verify:post-start",
+        "final:running-band:433", "final:known-working:kiss", "final:clear-restart-marker:kiss",
+        "final:clear-stop-intent:kiss", "journal:running[kiss=succeeded]",
+        "journal:done[kiss=succeeded]"]
+    state, items, j = _journal(box.root)
+    assert (state, items) == ("done", [("kiss", "433", "succeeded")])
+    assert j["items"][0]["result"] == {"ok": True, "summary": "Run applied for 'kiss'."}
+    # the prior boot's record (pid 999999) is gone; the new launch's record has the same shape
+    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
+                                   "state/boot-restore.json", "state/daemon-feed-floor-433",
+                                   "state/running/kiss.band"],
+                         "removed": [], "changed": [EVIDENCE]}
+    assert box.owned() == ["loraham-kiss-tnc"]
+
+
+def test_nothing_to_restore(kiss_box, prior_boot, run_op):
+    """intended: no evidence → a `no-plan` journal, ok, driver completed."""
+    box = kiss_box()
+    run = run_op(box.root, box.svc.boot_restore_run)
+    assert run.fields == _fields("Boot restore: nothing to restore.")
+    assert run.phases == DRIVER + ["journal:no-plan[]"]
+    assert _journal(box.root)[:2] == ("no-plan", [])
+    assert run.files == {"added": ["state/boot-restore.json"], "removed": [], "changed": []}
+
+
+def test_disabled_retires_the_evidence(kiss_box, prior_boot, run_op, monkeypatch):
+    """intended: restore disabled → nothing started, the item `cancelled` with the reason, the
+    old evidence retired, journal `disabled`."""
+    box = kiss_box()
+    prior_boot(box.root)
+    monkeypatch.setattr(ControllerService, "boot_restore_enabled",
+                        lambda self: (False, "disabled by [boot] restore"))
+    run = run_op(box.root, box.svc.boot_restore_run)
+    assert run.fields == _fields("Boot restore disabled (disabled by [boot] restore) — nothing "
+                                 "started; 1 old evidence record(s) retired.")
+    assert run.phases == DRIVER + ["journal:disabled[kiss=cancelled]"] * 2
+    assert _journal(box.root)[:2] == ("disabled", [("kiss", "", "cancelled")])
+    assert run.files == {"added": ["state/boot-restore.json"], "removed": [EVIDENCE],
+                         "changed": []}
+
+
+def test_operator_stop_intent_is_honoured(kiss_box, prior_boot, run_op):
+    """intended: a stack the operator stopped after its recorded launch is skipped (reason in the
+    journal) and its evidence pruned; nothing is started."""
+    box = kiss_box()
+    prior_boot(box.root)
+    box.svc._write_stop_intent(["kiss"])
+    run = run_op(box.root, box.svc.boot_restore_run)
+    assert run.fields == _fields("Boot restore: nothing to restore (1 skipped — see the log).")
+    assert run.phases == DRIVER + ["journal:no-plan[]"] * 2
+    state, items, j = _journal(box.root)
+    assert (state, items) == ("no-plan", [])
+    assert [s["stack"] for s in j["skipped"]] == ["kiss"]
+    assert run.files == {"added": ["state/boot-restore.json"], "removed": [EVIDENCE],
+                         "changed": []}
+
+
+def test_refused_before_the_hook_stays_pending(kiss_box, prior_boot, run_op,
+                                               interrupted_install):
+    """intended: a start refused before its hook (here an interrupted install, decided under the
+    index lock) leaves the item `pending` and the evidence in place; the run reads truncated."""
+    box = kiss_box()
+    prior_boot(box.root)
+    interrupted_install(box.root)
+    run = run_op(box.root, box.svc.boot_restore_run)
+    assert run.fields == _fields("Boot restore: 0 restored, 1 pending (run truncated — restart "
+                                 "the unit to continue).", ok=False)
+    assert run.phases == DRIVER + ["journal:running[kiss=pending]", "config-stable",
+                                   "lock:source-txn-index", "journal:failed[kiss=pending]"]
+    assert _journal(box.root)[:2] == ("failed", [("kiss", "433", "pending")])
+    assert run.files == {"added": ["state/boot-restore.json"], "removed": [], "changed": []}
+
+
+def test_failed_after_the_hook_consumes_the_evidence(kiss_box, prior_boot, run_op):
+    """intended: a start that fails after its hook (the endpoint never came up — UNVERIFIED,
+    cleaned up) settles the item `failed` with the component outcome, and consumes the
+    evidence: the next boot does not retry it."""
+    box = kiss_box()
+    prior_boot(box.root)
+    box.endpoint_never_up()
+    run = run_op(box.root, box.svc.boot_restore_run)
+    assert run.fields == _fields("Boot restore: 0 restored, 1 failed (evidence consumed — lhpc "
+                                 "stack start <id>).", ok=False, heads=())
+    assert run.res.details == [
+        "kiss: loraham-kiss-tnc unverified — ready endpoint(s) never came up (127.0.0.1:8001: "
+        "absent (family=ipv4)); cleanup: stopped"]
+    assert run.phases[-3:] == ["mutate:signal:loraham-kiss-tnc", "journal:running[kiss=failed]",
+                               "journal:failed[kiss=failed]"]
+    state, items, j = _journal(box.root)
+    assert (state, items) == ("failed", [("kiss", "433", "failed")])
+    assert j["items"][0]["result"]["components"] == [
+        {"component": "loraham-kiss-tnc", "outcome": "unverified",
+         "reason": "ready endpoint(s) never came up (127.0.0.1:8001: absent (family=ipv4)); "
+                   "cleanup: stopped"}]
+    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
+                                   "state/boot-restore.json", "state/daemon-feed-floor-433"],
+                         "removed": [EVIDENCE], "changed": []}
+    assert box.owned() == []
+
+
+def test_refused_by_admission(kiss_box, prior_boot, run_op, uninstall_guard):
+    """intended: driver admission refused → no journal, nothing consumed, the rerun command named
+    (typed: data["admission_blocked"]; no driver_completed, so the unit fails)."""
+    box = kiss_box()
+    prior_boot(box.root)
+    uninstall_guard(box.root)
+    run = run_op(box.root, box.svc.boot_restore_run)
+    assert run.fields == {
+        "ok": False, "data_keys": ["admission_blocked"], "heads": [], "outcomes": [],
+        "next_commands": ["systemctl --user restart lhpc-boot-restore.service"],
+        "summary": "Boot restore not started: A controller uninstall is in progress "
+                   "(.lhpc-uninstalling) — refusing to start new work. Let it finish, or recover "
+                   "it. — no stack was restored and nothing was consumed; rerun with: systemctl "
+                   "--user restart lhpc-boot-restore.service"}
+    assert run.phases == DRIVER
+    assert run.files == {"added": [], "removed": [], "changed": []}
diff --git a/tests/golden/test_golden_build.py b/tests/golden/test_golden_build.py
new file mode 100644
index 0000000..15b63db
--- /dev/null
+++ b/tests/golden/test_golden_build.py
@@ -0,0 +1,176 @@
+"""Golden: `ControllerService.build` — what a CLI build does today, in order.
+
+Phases: the static refusals (controller id, binary channel, not installed — no lock, no write) →
+the plan (apply=False) → task admission → the source-txn index lock (recover-scan, refuse an
+unresolved journal) → every source lock → per component: invalidate a stale completion marker,
+run each step through the runner into its own log, then write the build-inputs sidecar and the
+completion marker last. The detached web build is a different code path (a rendered launcher,
+tests/core/test_build_launcher_runtime.py).
+"""
+
+import re
+
+from lhpc.core import lifecycle as lifecycle_mod
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import CommandResult, FakeSystem
+from lhpc.core.services import ControllerService
+
+CHAT_STEP = ("gcc", "clients/chat/lorachat_ncurses_113.c", "-o", "loraham_chat", "-lncurses",
+             "-lpthread")
+LOCKS = ["admission", "lock:controller-task-admission", "lock:source-txn-index",
+         "lock:source.src/LoRaHAM_Daemon"]
+NOTHING = {"added": [], "removed": [], "changed": []}
+
+
+class _EveryStepSucceeds(dict):
+    """A runner table answering every argv with rc 0 — the build recipe itself is the manifest's."""
+
+    def get(self, argv, default=None):
+        return CommandResult(0, "ok\n", "")
+
+
+def _box(root, commands, *, installed=True):
+    fake = FakeSystem(commands=commands)
+    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
+    svc.bootstrap(apply=True)
+    if installed:
+        (root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
+    return fake, svc
+
+
+def _steps(fake):
+    return [c for c in fake.calls if c[0] not in ("systemctl", "git")]
+
+
+def test_plan_then_build(tmp_path, run_op):
+    """intended: the plan lists each component's argv and changes nothing; the apply takes
+    admission, the index lock and the source lock, then runs the one step into its log."""
+    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "built\n", "")})
+    plan = run_op(tmp_path, lambda: svc.build("chat"))
+    assert plan.fields == {"ok": True, "summary": "Build plan for 'chat': 1 component(s).",
+                           "data_keys": ["changes"], "next_commands": ["lhpc build chat --yes"],
+                           "heads": ["[build] loraham-chat"], "outcomes": []}
+    assert plan.phases == [] and plan.files == NOTHING and _steps(fake) == []
+
+    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
+    assert run.fields == {"ok": True, "summary": "Build succeeded for 'chat'.", "data_keys": [],
+                          "next_commands": ["lhpc status chat"],
+                          "heads": ["[log] loraham-chat", "[succeeded] build"], "outcomes": []}
+    assert run.phases == LOCKS + ["mutate:build-step"]
+    assert run.files == {"added": ["logs/build-loraham-chat.log"], "removed": [], "changed": []}
+    assert (tmp_path / "logs/build-loraham-chat.log").read_text() == "built\n"
+    assert _steps(fake) == [list(CHAT_STEP)]
+
+
+def test_failed_step(tmp_path, run_op):
+    """intended: a failing step fails the build with its rc and log tail; the log is the step's
+    output."""
+    _fake, svc = _box(tmp_path, {})
+    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
+    assert run.fields == {"ok": False, "summary": "Build FAILED for 'chat'.", "data_keys": [],
+                          "next_commands": ["lhpc status chat"],
+                          "heads": ["[log] loraham-chat", "[failed] build"], "outcomes": []}
+    assert re.fullmatch(r"  \[failed\] build loraham-chat \(rc 127, log /\S+/logs/"
+                        r"build-loraham-chat\.log\)", run.res.details[1])
+    assert run.res.details[2:] == ["      no fake"]
+    assert run.phases == LOCKS + ["mutate:build-step"]
+    assert run.files == {"added": ["logs/build-loraham-chat.log"], "removed": [], "changed": []}
+
+
+def test_completion_marker_is_written_last(tmp_path, run_op, phases, monkeypatch):
+    """intended: per marker-gated component: the stale marker is invalidated before its first
+    step, each step logs into its own file, and the inputs sidecar then the completion marker are
+    stamped only after its last step (the consumed-source lines record each source's revision)."""
+    _fake, svc = _box(tmp_path, _EveryStepSucceeds(), installed=False)
+    real_write = lifecycle_mod.runtime_fs.atomic_write
+
+    def stamp(paths, path, *a, **k):          # the build's two stamps, into the phase log
+        name = str(path).rsplit("/", 1)[-1]
+        if name in (".lhpc-build-inputs", ".lhpc-build-complete"):
+            phases.append("final:stamp:" + name)
+        return real_write(paths, path, *a, **k)
+    monkeypatch.setattr(lifecycle_mod.runtime_fs, "atomic_write", stamp)
+    for comp in svc.stack("meshcore").components:
+        if comp.source:
+            (tmp_path / comp.source.path).mkdir(parents=True, exist_ok=True)
+    run = run_op(tmp_path, lambda: svc.build("meshcore", apply=True))
+    assert run.fields["ok"] is True and run.fields["summary"] == "Build succeeded for 'meshcore'."
+    assert [h for h in run.fields["heads"] if h.startswith("[succeeded]")] == [
+        "[succeeded] build"] * 3
+    per_comp = [["mutate:invalidate-marker"] + ["mutate:build-step"] * n
+                + ["final:stamp:.lhpc-build-inputs", "final:stamp:.lhpc-build-complete"]
+                for n in (5, 3, 3)]                   # meshcore-node, meshcore-webui, meshcore-cli
+    assert run.phases == ["admission", "lock:controller-task-admission", "lock:source-txn-index",
+                          "lock:source.src/meshcore-cli", "lock:source.src/meshcore-webui",
+                          "lock:source.src/openhop-core"] + sum(per_comp, [])
+    markers = {
+        "src/openhop-core/.venv/.lhpc-build-complete":
+            "lhpc build complete\nconsumed meshcore-node ok\nconsumed openhop-repeater-src ok\n",
+        "src/meshcore-webui/backend/.venv/.lhpc-build-complete": "lhpc build complete\n",
+        "src/meshcore-cli/.venv/.lhpc-build-complete": "lhpc build complete\n",
+    }
+    for rel, text in markers.items():
+        assert (tmp_path / rel).read_text() == text
+    assert sorted(f for f in run.files["added"] if not f.startswith("logs/")) == sorted(
+        list(markers) + ["src/meshcore-cli/.venv/bin/.lhpc-build-inputs",
+                         "src/meshcore-webui/backend/.venv/bin/.lhpc-build-inputs",
+                         "src/openhop-core/.venv/bin/.lhpc-build-inputs"])
+    assert [f for f in run.files["added"] if f.startswith("logs/")] == (
+        [f"logs/build-meshcore-cli-{i}.log" for i in range(3)]
+        + [f"logs/build-meshcore-node-{i}.log" for i in range(5)]
+        + [f"logs/build-meshcore-webui-{i}.log" for i in range(3)])
+
+
+def test_refused_not_installed(tmp_path, run_op):
+    """intended: a component with no source is refused before admission — no lock, no write,
+    no step run."""
+    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")}, installed=False)
+    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
+    assert run.fields == {"ok": False,
+                          "summary": "Refusing to build 'chat': loraham-chat is not installed.",
+                          "data_keys": [], "next_commands": ["lhpc install chat"],
+                          "heads": ["[not-installed] loraham-chat"], "outcomes": []}
+    assert run.phases == [] and run.files == NOTHING and _steps(fake) == []
+
+
+def test_refused_by_admission(tmp_path, run_op, uninstall_guard):
+    """intended: admission refuses (typed: data["admission_blocked"]) before the source locks."""
+    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")})
+    uninstall_guard(tmp_path)
+    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
+    assert run.fields == {
+        "ok": False, "data_keys": ["admission_blocked"], "next_commands": [], "heads": [],
+        "outcomes": [],
+        "summary": "A controller uninstall is in progress (.lhpc-uninstalling) — refusing to "
+                   "start new work. Let it finish, or recover it."}
+    assert run.res.data["admission_blocked"] == "uninstalling"
+    assert run.phases == ["admission", "lock:controller-task-admission"]
+    assert run.files == NOTHING and _steps(fake) == []
+
+
+def test_refused_by_interrupted_install(tmp_path, run_op, interrupted_install):
+    """intended: an unresolved source-transaction journal refuses under the index lock, before
+    any source lock or step."""
+    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")})
+    interrupted_install(tmp_path)
+    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
+    assert run.fields == {
+        "ok": False, "data_keys": [], "next_commands": ["lhpc status chat"], "heads": [],
+        "outcomes": [],
+        "summary": "Build blocked for 'chat': an unresolved source-transaction journal is "
+                   "present — resolve it before any source operation"}
+    assert run.phases == ["admission", "lock:controller-task-admission", "lock:source-txn-index"]
+    assert run.files == NOTHING and _steps(fake) == []
+
+
+def test_contended_source_lock_refuses(tmp_path, run_op, held_lock):
+    """intended: another process holding the source lock refuses the build at once, naming the
+    holder, with no step run."""
+    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")})
+    with held_lock(svc._paths, "source.src/LoRaHAM_Daemon"):
+        run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
+    assert run.fields["ok"] is False and run.fields["outcomes"] == []
+    assert re.fullmatch(r"Build blocked for 'chat': resource 'source\.src-loraham_daemon' is "
+                        r"busy: golden on 'x' \(pid \d+\)", run.fields["summary"])
+    assert run.phases == LOCKS
+    assert run.files == NOTHING and _steps(fake) == []
diff --git a/tests/golden/test_golden_restart.py b/tests/golden/test_golden_restart.py
new file mode 100644
index 0000000..de9dc58
--- /dev/null
+++ b/tests/golden/test_golden_restart.py
@@ -0,0 +1,137 @@
+"""Golden: `ControllerService.restart` — what a restart does today, in order.
+
+Phases: admission → config-stability guard → the identity recheck (BEFORE the operation locks,
+unlike start) → the union of the stop and start locks → the preflight → the hook → the public
+`stop` (signal, daemon release, feed floor, restart-marker clear) → the public `start` (spawn,
+verify, finalize). A stop that is not verified aborts the restart before anything is started.
+"""
+
+import pytest
+
+from lhpc.core.lifecycle import Lifecycle
+from lhpc.core.service_base import ActionResult
+
+pytestmark = pytest.mark.needs_session
+
+LOCKS = ["admission", "lock:controller-task-admission", "config-stable", "recheck:identity",
+         "lock:source-txn-index", "lock:claim.loraham.daemon-socket.433",
+         "lock:claim.loraham.radio.433", "lock:claim.tcp.port.8001", "lock:lifecycle.kiss",
+         "lock:source.src/loraham-daemon", "lock:source.src/loraham-kiss-tnc"]
+STOP_LEG = ["mutate:signal:loraham-kiss-serial", "mutate:signal:loraham-kiss-tnc",
+            "lock:claim.audio.default", "lock:claim.tcp.port.12323", "lock:claim.tcp.port.18083",
+            "lock:claim.tcp.port.7000", "lock:claim.tcp.port.8080", "lock:lifecycle.chat",
+            "lock:lifecycle.daemon", "lock:lifecycle.graywolf", "lock:lifecycle.meshcom",
+            "lock:lifecycle.voice", "mutate:signal:loraham-daemon", "mutate:feed-floor:433",
+            "final:clear-restart-marker:kiss"]
+START_LEG = ["recheck:start", "mutate:feed-floor:433", "mutate:spawn:loraham-kiss-tnc",
+             "verify:endpoints:loraham-kiss-tnc", "verify:post-start", "final:running-band:433",
+             "final:known-working:kiss", "final:clear-restart-marker:kiss",
+             "final:clear-stop-intent:kiss"]
+NOTHING = {"added": [], "removed": [], "changed": []}
+
+
+def _running(kiss_box, **kw):
+    box = kiss_box(**kw)
+    assert box.svc.start("kiss", apply=True).ok
+    return box
+
+
+def test_plan_is_read_only(kiss_box, run_op):
+    """intended: the restart plan merges the stop and start plans; no lock, no write."""
+    box = _running(kiss_box)
+    plan = run_op(box.root, lambda: box.svc.restart("kiss"))
+    assert plan.fields == {
+        "ok": True, "summary": "Restart plan for 'kiss': stop then run.",
+        "data_keys": ["blockers", "changes", "commands", "dependents", "optional_restarted",
+                      "other_bands"],
+        "next_commands": ["lhpc stack restart kiss --yes"],
+        "heads": ["[daemon] start/ensure", "[start] loraham-kiss-tnc"], "outcomes": []}
+    assert plan.phases == ["recheck:identity", "recheck:preflight"]
+    assert plan.files == NOTHING
+
+
+def test_restart_a_running_stack(kiss_box, run_op):
+    """intended: all locks and rechecks first, then a verified stop, then a verified start; the
+    old ownership record is replaced by the new one."""
+    box = _running(kiss_box)
+    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
+    assert run.fields == {
+        "ok": True, "summary": "Restarted 'kiss'. Run applied for 'kiss'.", "data_keys": [],
+        "next_commands": ["lhpc status kiss", "lhpc logs kiss", "lhpc stack stop kiss"],
+        "heads": ["[already_stopped] loraham-kiss-serial", "[stopped] loraham-kiss-tnc",
+                  "[stopped daemon] daemon", "[ok] daemon", "[log] loraham-kiss-tnc",
+                  "[verified] loraham-kiss-tnc"],
+        "outcomes": [("loraham-kiss-serial", "already_stopped"), ("loraham-kiss-tnc", "stopped"),
+                     ("loraham-daemon", "stopped"), ("loraham-daemon", "verified"),
+                     ("loraham-kiss-tnc", "verified")]}
+    assert run.phases == LOCKS + ["recheck:preflight"] + STOP_LEG + START_LEG
+    assert run.kinds == ["admission", "lock", "recheck", "lock", "recheck", "mutate", "lock",
+                         "mutate", "final", "recheck", "mutate", "verify", "final"]
+    # the record's pid/nonce changed; the normalized name did not
+    assert run.files == {"added": [], "removed": [], "changed": [
+        "state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json"]}
+    assert box.owned() == ["loraham-kiss-tnc"]
+
+
+def test_unverified_stop_aborts_before_any_start(kiss_box, run_op, monkeypatch):
+    """intended: a stop that is not verified (the process outlives SIGTERM) aborts the restart —
+    nothing is spawned, the ownership record is retained."""
+    box = _running(kiss_box)
+    # Stub the collaborator, not the coordinator: the process "outlives" its SIGTERM.
+    monkeypatch.setattr(Lifecycle, "_wait_ceased", lambda self, rec, timeout=0.0: False)
+    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "summary": "Restart aborted for 'kiss': stop was not verified.",
+        "data_keys": [], "next_commands": ["lhpc status kiss"],
+        "heads": ["[already_stopped] loraham-kiss-serial", "[still_running] loraham-kiss-tnc",
+                  "[aborted] not"],
+        "outcomes": [("loraham-kiss-serial", "already_stopped"),
+                     ("loraham-kiss-tnc", "still_running")]}
+    assert run.phases == LOCKS + ["recheck:preflight"] + STOP_LEG[:2]
+    assert run.files == NOTHING
+    assert box.owned() == ["loraham-kiss-tnc"]
+
+
+def test_entry_hook_refusal_stops_nothing(kiss_box, run_op, phases):
+    """intended: the job runner's hook (`_before_restart_locked`) runs after every lock and the
+    preflight, before the stop; its refusal leaves the running stack up."""
+    box = _running(kiss_box)
+    seen = []
+
+    def hook():
+        seen.append(list(phases))
+        return ActionResult(False, "superseded")
+    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True,
+                                                   _before_restart_locked=hook))
+    assert run.fields == {"ok": False, "summary": "superseded", "data_keys": [],
+                          "next_commands": [], "heads": [], "outcomes": []}
+    assert seen == [LOCKS] and run.phases == LOCKS
+    assert run.files == NOTHING and box.owned() == ["loraham-kiss-tnc"]
+
+
+def test_refused_by_admission(kiss_box, run_op, uninstall_guard):
+    """intended: admission refuses the restart before any other lock; the stack stays up."""
+    box = _running(kiss_box)
+    uninstall_guard(box.root)
+    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "data_keys": ["admission_blocked"], "next_commands": [], "heads": [],
+        "outcomes": [],
+        "summary": "A controller uninstall is in progress (.lhpc-uninstalling) — refusing to "
+                   "start new work. Let it finish, or recover it."}
+    assert run.phases == ["admission", "lock:controller-task-admission"]
+    assert run.files == NOTHING and box.owned() == ["loraham-kiss-tnc"]
+
+
+def test_band_owner_refuses_before_the_stop(kiss_box, run_op):
+    """intended: a band owner refuses the restart in its preflight, before the stop — the
+    running stack is left up and nothing is written."""
+    box = _running(kiss_box)
+    box.fake.cmdlines_data[300] = ["meshtasticd"]
+    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "summary": "Cannot run 'kiss': meshtastic must be stopped first.",
+        "data_keys": [], "next_commands": ["lhpc stack stop meshtastic"], "heads": [],
+        "outcomes": []}
+    assert run.phases == LOCKS + ["recheck:preflight"]
+    assert run.files == NOTHING and box.owned() == ["loraham-kiss-tnc"]
diff --git a/tests/golden/test_golden_save_config_bundle.py b/tests/golden/test_golden_save_config_bundle.py
new file mode 100644
index 0000000..3c3d878
--- /dev/null
+++ b/tests/golden/test_golden_save_config_bundle.py
@@ -0,0 +1,141 @@
+"""Golden: `ControllerService.save_config_bundle` — what a Settings save does today, in order.
+
+Phases: validation of the whole submission (no lock, no write on a refusal) → the exclusive
+config lock → finish/refuse a pending journal → write the journal (`state/config-txn.json`) →
+render every target under the lock (the rechecks live in the renderers) → atomic writes in order
+(per-stack file, then the restart-required marker) → journal removed. Any failure restores every
+pre-image. There is no admission and no operation lock: a save never touches a process.
+"""
+
+import json
+import re
+
+from lhpc.core import config as cfgmod
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+
+FILE = "config/stacks/chat.toml"
+WRITES = ["lock:config", "recheck:config-journal", "mutate:write:state/config-txn.json",
+          "mutate:write:" + FILE]
+SAVED = {"ok": True, "summary": "Config saved for 'chat'.", "data_keys": [],
+         "next_commands": ["lhpc stack start chat"], "heads": [], "outcomes": []}
+REFUSED = {"ok": False, "summary": "Config not saved for 'chat'.", "next_commands": [],
+           "heads": [], "outcomes": []}
+NOTHING = {"added": [], "removed": [], "changed": []}
+
+
+def _svc(root, running=False):
+    cmd = {555: ["loraham_chat"]} if running else {}
+    svc = ControllerService(system=FakeSystem(cmdlines_data=cmd).system,
+                            paths=Paths(runtime_root=root))
+    svc.bootstrap(apply=True)
+    return svc
+
+
+def _text(freq):
+    return f'# chat configuration (managed by lhpc — git-ignored).\nfile_tx_freq = "{freq}"\n'
+
+
+def test_save_while_stopped(tmp_path, run_op):
+    """intended: journal first, then the per-stack file; the journal is gone afterwards; a stopped
+    stack gets no restart marker, only the "applies on the next Run" hint."""
+    svc = _svc(tmp_path)
+    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}))
+    assert run.fields == SAVED
+    assert run.res.details == ["Start-time change — applies on the next Run."]
+    assert run.phases == WRITES
+    assert run.files == {"added": [FILE], "removed": [], "changed": []}
+    assert (tmp_path / FILE).read_text() == _text("434.500")
+
+
+def test_save_while_running_writes_the_restart_marker(tmp_path, run_op):
+    """intended: a live consumer turns a restart-mode change into the restart-required marker,
+    written in the same transaction after the per-stack file, carrying the launched value."""
+    svc = _svc(tmp_path, running=True)
+    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}))
+    assert run.fields == SAVED
+    assert run.res.details == ["Restart the stack to apply."]
+    assert run.phases == WRITES + ["mutate:write:state/restart-required/chat.json"]
+    assert run.files == {"added": [FILE, "state/restart-required/chat.json"], "removed": [],
+                         "changed": []}
+    marker = json.loads((tmp_path / "state/restart-required/chat.json").read_text())
+    marker.pop("created_at")
+    assert marker == {"version": 1, "stack": "chat", "mode": "restart", "params": ["tx_freq"],
+                      "band": "", "launched": {"|loraham-chat|tx_freq": "433.775"}}
+
+
+def test_refused_by_validation(tmp_path, run_op):
+    """intended: an unknown field refuses the whole submission before the lock — zero writes."""
+    svc = _svc(tmp_path)
+    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"bogus": "1"}))
+    assert run.fields == {**REFUSED, "data_keys": []}
+    assert run.res.details == ["unknown config field: 'bogus'"]
+    assert run.phases == [] and run.files == NOTHING
+
+
+def test_pending_journal_is_recovered_first(tmp_path, run_op):
+    """intended: a journal a crashed save left behind is finished under the lock (the pre-image
+    restored) BEFORE the new save journals and writes; no journal survives."""
+    svc = _svc(tmp_path)
+    assert svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}).ok
+    (tmp_path / FILE).write_text("# torn\n")
+    (tmp_path / "state/config-txn.json").write_text(json.dumps({"version": 1, "targets": [
+        {"kind": "stack", "rel": FILE, "pre": _text("434.500"), "existed": True,
+         "mode": 0o644}]}))
+    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.600"}))
+    assert run.fields == SAVED
+    assert run.phases == ["lock:config", "recheck:config-journal", "mutate:write:" + FILE,
+                          "mutate:write:state/config-txn.json", "mutate:write:" + FILE]
+    assert run.files == {"added": [], "removed": ["state/config-txn.json"], "changed": [FILE]}
+    assert (tmp_path / FILE).read_text() == _text("434.600")
+
+
+def test_unrecoverable_journal_refuses(tmp_path, run_op):
+    """intended: a malformed journal refuses the save (typed: data["reason"] ==
+    "recovery-required") under the lock; the journal is retained and nothing is written."""
+    svc = _svc(tmp_path)
+    (tmp_path / "state/config-txn.json").write_text("{ not json")
+    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.5"}))
+    assert run.fields == {**REFUSED, "data_keys": ["reason"]}
+    assert run.res.data == {"reason": "recovery-required"}
+    assert run.phases == ["lock:config", "recheck:config-journal"]
+    assert run.files == NOTHING
+
+
+def test_failed_write_rolls_back(tmp_path, run_op, monkeypatch):
+    """intended: a write failing mid-transaction (here the marker, the third write) restores
+    every pre-image — the per-stack file that did not exist is removed again — and drops the
+    journal; the refusal names the rollback."""
+    svc = _svc(tmp_path, running=True)
+    real, calls = cfgmod._atomic_write, []
+
+    def failing_third(paths, path, *a, **k):
+        calls.append(path)
+        if len(calls) == 3:
+            raise OSError(28, "No space left on device")
+        return real(paths, path, *a, **k)
+    monkeypatch.setattr(cfgmod, "_atomic_write", failing_third)
+    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.5"}))
+    assert run.fields == {**REFUSED, "data_keys": []}
+    assert run.res.details == [
+        "config transaction failed and was rolled back: [Errno 28] No space left on device"]
+    assert run.phases == WRITES
+    assert run.files == NOTHING
+
+
+def test_malformed_stack_file_is_preserved(tmp_path, run_op):
+    """intended: a present-but-malformed per-stack file is a typed refusal raised while rendering
+    under the lock; the rollback writes its pre-image back byte for byte."""
+    svc = _svc(tmp_path)
+    (tmp_path / "config/stacks").mkdir(parents=True, exist_ok=True)
+    (tmp_path / FILE).write_text("this is = = not toml\n")
+    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.5"}))
+    assert run.fields == {**REFUSED, "data_keys": []}
+    assert len(run.res.details) == 1 and re.fullmatch(
+        r"config transaction failed and was rolled back: /\S+/config/stacks/chat\.toml: "
+        r"Expected '=' after a key in a key/value pair \(at line 1, column 6\)",
+        run.res.details[0])
+    assert run.phases == WRITES
+    assert run.files == NOTHING
+    assert (tmp_path / FILE).read_text() == "this is = = not toml\n"
diff --git a/tests/golden/test_golden_start.py b/tests/golden/test_golden_start.py
new file mode 100644
index 0000000..c8e877a
--- /dev/null
+++ b/tests/golden/test_golden_start.py
@@ -0,0 +1,197 @@
+"""Golden: `ControllerService.start` — what a start does today, in order.
+
+The box (`kiss_box`): kiss installed and built over a daemon already serving 433; the TNC spawns a
+real process and its ready endpoint follows it. Phases: admission → the config-stability guard and
+the operation locks (sorted) → the authoritative identity recheck → the daemon feed-floor reset →
+the preflight (firewall gate, config ambiguity, band owners) → spawn + ownership record →
+endpoint/post-start verification → finalization (running band, known-working candidate,
+restart-required marker, stop intent).
+"""
+
+import pytest
+
+from lhpc.core.service_base import ActionResult
+
+pytestmark = pytest.mark.needs_session
+
+KISS_LOCKS = [
+    "admission", "lock:controller-task-admission", "config-stable", "lock:source-txn-index",
+    "lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.radio.433",
+    "lock:claim.tcp.port.8001", "lock:lifecycle.kiss", "lock:source.src/loraham-daemon",
+    "lock:source.src/loraham-kiss-tnc",
+]
+RECHECK = ["recheck:start", "recheck:identity"]
+KISS_RUN = ["mutate:feed-floor:433", "recheck:preflight", "mutate:spawn:loraham-kiss-tnc",
+            "verify:endpoints:loraham-kiss-tnc", "verify:post-start", "final:running-band:433",
+            "final:known-working:kiss", "final:clear-restart-marker:kiss",
+            "final:clear-stop-intent:kiss"]
+PLAN = {"ok": True, "summary": "Run plan for 'kiss': 2 component(s) in order.",
+        "data_keys": ["blockers", "changes", "commands"],
+        "next_commands": ["lhpc stack start kiss --yes"],
+        "heads": ["[daemon] start/ensure", "[start] loraham-kiss-tnc"], "outcomes": []}
+NOTHING = {"added": [], "removed": [], "changed": []}
+
+
+def test_plan_then_apply_happy_path(kiss_box, run_op):
+    """intended: the plan is read-only and lock-free; the apply takes admission and every lock
+    before the recheck, mutates, verifies the endpoint, then finalizes."""
+    box = kiss_box()
+    plan = run_op(box.root, lambda: box.svc.start("kiss"))
+    assert plan.fields == PLAN
+    assert plan.phases == ["recheck:start", "recheck:identity", "recheck:preflight"]
+    assert plan.files == NOTHING
+
+    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
+    assert run.fields == {
+        "ok": True, "summary": "Run applied for 'kiss'.", "data_keys": [],
+        "next_commands": ["lhpc status kiss", "lhpc logs kiss", "lhpc stack stop kiss"],
+        "heads": ["[ok] daemon", "[log] loraham-kiss-tnc", "[verified] loraham-kiss-tnc"],
+        "outcomes": [("loraham-daemon", "verified"), ("loraham-kiss-tnc", "verified")]}
+    assert run.phases == KISS_LOCKS + RECHECK + KISS_RUN
+    assert run.kinds == ["admission", "lock", "recheck", "mutate", "recheck", "mutate", "verify",
+                         "final"]
+    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
+                                   "state/daemon-feed-floor-433",
+                                   "state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json",
+                                   "state/running/kiss.band"],
+                         "removed": [], "changed": []}
+    assert box.owned() == ["loraham-kiss-tnc"]
+
+
+def test_refused_missing_callsign(kiss_box, run_op):
+    """intended: a licensed stack with no callsign is refused alike by the plan and, under every
+    lock, by the apply's recheck — before any mutation (typed: data["enforce_fields"])."""
+    box = kiss_box(callsign=False)
+    (box.root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
+    (box.root / "src" / "LoRaHAM_Daemon" / "loraham_chat").write_text("#bin")
+    refusal = {
+        "ok": False, "data_keys": ["enforce_fields"], "heads": [], "outcomes": [],
+        "summary": "Cannot start 'chat': a callsign is required to start 'chat' — set 'call' "
+                   "(or the global operator callsign)",
+        "next_commands": ["lhpc config chat call YOURCALL-10   # YOURCALL-10 = your callsign "
+                          "(+optional SSID); or once for every licensed stack: lhpc config "
+                          "operator --callsign YOURCALL"]}
+    plan = run_op(box.root, lambda: box.svc.start("chat"))
+    assert plan.fields == refusal and plan.files == NOTHING
+    run = run_op(box.root, lambda: box.svc.start("chat", apply=True))
+    assert run.fields == refusal
+    assert run.res.data["enforce_fields"]
+    assert run.phases == [
+        "admission", "lock:controller-task-admission", "config-stable", "lock:source-txn-index",
+        "lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.radio.433",
+        "lock:lifecycle.chat", "lock:source.src/loraham-daemon", "lock:source.src/LoRaHAM_Daemon",
+        "recheck:start", "recheck:identity"]
+    assert run.files == NOTHING
+
+
+def test_refused_by_admission(kiss_box, run_op, uninstall_guard):
+    """intended: admission is apply-only — the plan passes, the apply refuses at admission
+    (typed: data["admission_blocked"]) before any other lock, with nothing written."""
+    box = kiss_box()
+    uninstall_guard(box.root)
+    assert run_op(box.root, lambda: box.svc.start("kiss")).fields == PLAN
+    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "data_keys": ["admission_blocked"], "next_commands": [], "heads": [],
+        "outcomes": [],
+        "summary": "A controller uninstall is in progress (.lhpc-uninstalling) — refusing to "
+                   "start new work. Let it finish, or recover it."}
+    assert run.res.data["admission_blocked"] == "uninstalling"
+    assert run.phases == ["admission", "lock:controller-task-admission"]
+    assert run.files == NOTHING
+
+
+def test_refused_by_interrupted_install(kiss_box, run_op, interrupted_install):
+    """intended: an unresolved source-transaction journal is an apply-only refusal, decided under
+    the source-txn index lock before the operation locks; the plan does not check it."""
+    box = kiss_box()
+    interrupted_install(box.root)
+    assert run_op(box.root, lambda: box.svc.start("kiss")).fields == PLAN
+    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "data_keys": [], "next_commands": ["lhpc status kiss"], "heads": [],
+        "outcomes": [],
+        "summary": "Cannot start 'kiss': an unresolved source-transaction journal is present — "
+                   "resolve it before starting"}
+    assert run.phases == ["admission", "lock:controller-task-admission", "config-stable",
+                          "lock:source-txn-index"]
+    assert run.files == NOTHING
+
+
+def test_refused_by_band_owner(kiss_box, run_op):
+    """known defect T1-F1: a running stack owning the band is listed by the plan (ok, with
+    blockers) and refuses the apply without stop_owners — but only in the preflight, AFTER the
+    band's daemon feed floor was already reset (state/daemon-feed-floor-433 written by a start
+    that never ran)."""
+    box = kiss_box()
+    box.fake.cmdlines_data[300] = ["meshtasticd"]
+    plan = run_op(box.root, lambda: box.svc.start("kiss"))
+    assert plan.fields == {**PLAN, "heads": PLAN["heads"] + ["[conflict] loraham.radio.433",
+                                                             "[conflict] radio"]}
+    assert [b["holder_stack"] for b in plan.res.data["blockers"]] == ["meshtastic", "meshtastic"]
+    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "summary": "Cannot run 'kiss': meshtastic must be stopped first.",
+        "data_keys": [], "next_commands": ["lhpc stack stop meshtastic"], "heads": [],
+        "outcomes": []}
+    assert run.phases == KISS_LOCKS + RECHECK + ["mutate:feed-floor:433", "recheck:preflight"]
+    assert run.files == {"added": ["state/daemon-feed-floor-433"], "removed": [], "changed": []}
+
+
+def test_unverified_termination_is_cleaned_up(kiss_box, run_op):
+    """intended: a TNC whose ready endpoint never comes up is stopped again (identity-verified)
+    and reported UNVERIFIED; no ownership record and no running-band marker survive."""
+    box = kiss_box()
+    box.endpoint_never_up()
+    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "summary": "Run FAILED for 'kiss': loraham-kiss-tnc did not start/verify.",
+        "data_keys": [],
+        "next_commands": ["lhpc status kiss", "lhpc logs kiss", "lhpc stack stop kiss"],
+        "heads": ["[ok] daemon", "[log] loraham-kiss-tnc", "[unverified] loraham-kiss-tnc"],
+        "outcomes": [("loraham-daemon", "verified"), ("loraham-kiss-tnc", "unverified")]}
+    assert run.phases == KISS_LOCKS + RECHECK + [
+        "mutate:feed-floor:433", "recheck:preflight", "mutate:spawn:loraham-kiss-tnc",
+        "verify:endpoints:loraham-kiss-tnc", "mutate:signal:loraham-kiss-tnc"]
+    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
+                                   "state/daemon-feed-floor-433"], "removed": [], "changed": []}
+    assert box.owned() == []
+
+
+def test_entry_hook_runs_after_locks_and_rechecks(kiss_box, run_op, phases):
+    """intended: the hook the detached job runner and boot-restore pass (`_before_start_locked`)
+    runs after every lock and the recheck, before the first mutation; its refusal is returned
+    as is and nothing is written."""
+    box = kiss_box()
+    seen = []
+
+    def hook():
+        seen.append(list(phases))
+        return ActionResult(False, "superseded")
+    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True, _before_start_locked=hook))
+    assert run.fields == {"ok": False, "summary": "superseded", "data_keys": [],
+                          "next_commands": [], "heads": [], "outcomes": []}
+    assert seen == [KISS_LOCKS + RECHECK] and run.phases == KISS_LOCKS + RECHECK
+    assert run.files == NOTHING
+
+
+def test_interactive_launch_is_manual_required(kiss_box, run_op):
+    """intended: an interactive main component is never spawned — its config is generated, the
+    dashboard marker set, and the start reports MANUAL_REQUIRED with ok False (the CLI, the job
+    runner and boot-restore count that alone as success: see the cross-path set)."""
+    box = kiss_box()
+    (box.root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
+    (box.root / "src" / "LoRaHAM_Daemon" / "loraham_chat").write_text("#bin")
+    run = run_op(box.root, lambda: box.svc.start("chat", apply=True))
+    assert run.fields == {
+        "ok": False,
+        "summary": "Run for 'chat': manual start required for loraham-chat — see the dashboard.",
+        "data_keys": [],
+        "next_commands": ["lhpc status chat", "lhpc logs chat", "lhpc stack stop chat"],
+        "heads": ["[ok] daemon", "[manual_required] loraham-chat"],
+        "outcomes": [("loraham-daemon", "verified"), ("loraham-chat", "manual_required")]}
+    assert run.phases[-4:] == ["recheck:identity", "mutate:feed-floor:433", "recheck:preflight",
+                               "mutate:config-files:loraham-chat"]
+    assert run.files == {"added": ["config/files/lorachat.conf", "state/daemon-feed-floor-433",
+                                   "state/interactive/chat.show"], "removed": [], "changed": []}
+    assert box.owned() == []
diff --git a/tests/golden/test_golden_stop.py b/tests/golden/test_golden_stop.py
new file mode 100644
index 0000000..de51578
--- /dev/null
+++ b/tests/golden/test_golden_stop.py
@@ -0,0 +1,131 @@
+"""Golden: `ControllerService.stop` — what a stop does today, in order.
+
+Phases: the operation locks (stop takes no admission and no config-stability guard) →
+identity-verified signal per component → the client stop releases the daemon band it no longer
+needs (its own lock set: every daemon client) → feed-floor reset → finalization (restart-required
+marker cleared, operator stop intent written). A stop is verified only when the process ceased
+AND its ready endpoint is gone; only then are the ownership record and markers removed.
+"""
+
+import re
+
+import pytest
+
+from lhpc.core.lifecycle import Lifecycle
+
+pytestmark = pytest.mark.needs_session
+
+STOP_LOCKS = ["lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.radio.433",
+              "lock:claim.tcp.port.8001", "lock:lifecycle.kiss"]
+SIGNAL = ["mutate:signal:loraham-kiss-serial", "mutate:signal:loraham-kiss-tnc"]
+RELEASE_DAEMON = ["lock:claim.audio.default", "lock:claim.tcp.port.12323",
+                  "lock:claim.tcp.port.18083", "lock:claim.tcp.port.7000",
+                  "lock:claim.tcp.port.8080", "lock:lifecycle.chat", "lock:lifecycle.daemon",
+                  "lock:lifecycle.graywolf", "lock:lifecycle.meshcom", "lock:lifecycle.voice",
+                  "mutate:signal:loraham-daemon", "mutate:feed-floor:433"]
+FINAL = ["final:clear-restart-marker:kiss", "final:stop-intent:['kiss']"]
+APPLIED = {"ok": True, "summary": "Stop applied for 'kiss'.", "data_keys": [],
+           "next_commands": ["lhpc status kiss"]}
+
+
+def test_plan_is_read_only(kiss_box, run_op):
+    """intended: the stop plan lists the components, takes no lock and writes nothing."""
+    box = kiss_box()
+    plan = run_op(box.root, lambda: box.svc.stop("kiss"))
+    assert plan.fields == {
+        "ok": True, "summary": "Stop plan for 'kiss': 2 component(s).",
+        "data_keys": ["changes", "commands", "dependents", "other_bands"],
+        "next_commands": ["lhpc stack stop kiss --yes"],
+        "heads": ["[stop] loraham-kiss-serial", "[stop] loraham-kiss-tnc"], "outcomes": []}
+    assert plan.phases == [] and plan.files == {"added": [], "removed": [], "changed": []}
+
+
+def test_stop_a_running_stack(kiss_box, run_op):
+    """intended: locks → verified SIGTERM → daemon band released → finalization; the ownership
+    record and the running-band marker go, the operator's stop intent is written."""
+    box = kiss_box()
+    assert box.svc.start("kiss", apply=True).ok
+    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
+    assert run.fields == {**APPLIED,
+                          "heads": ["[already_stopped] loraham-kiss-serial",
+                                    "[stopped] loraham-kiss-tnc", "[stopped daemon] daemon"],
+                          "outcomes": [("loraham-kiss-serial", "already_stopped"),
+                                       ("loraham-kiss-tnc", "stopped"),
+                                       ("loraham-daemon", "stopped")]}
+    assert run.phases == STOP_LOCKS + SIGNAL + RELEASE_DAEMON + FINAL
+    assert run.kinds == ["lock", "mutate", "lock", "mutate", "final"]
+    assert run.files == {"added": ["state/stop-intent/kiss.json"],
+                         "removed": ["state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json",
+                                     "state/running/kiss.band"],
+                         "changed": []}
+    assert box.owned() == []
+
+
+def test_stop_when_nothing_runs(kiss_box, run_op):
+    """intended: nothing owned is ALREADY_STOPPED — a verified stop, so the stop intent is still
+    recorded (a later boot must not restore it), and the daemon band no client needs is
+    released (its feed floor reset) as after a real stop."""
+    box = kiss_box()
+    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
+    assert run.fields == {**APPLIED,
+                          "heads": ["[already_stopped] loraham-kiss-serial",
+                                    "[already_stopped] loraham-kiss-tnc",
+                                    "[stopped daemon] daemon"],
+                          "outcomes": [("loraham-kiss-serial", "already_stopped"),
+                                       ("loraham-kiss-tnc", "already_stopped"),
+                                       ("loraham-daemon", "stopped")]}
+    assert run.phases == STOP_LOCKS + SIGNAL + RELEASE_DAEMON + FINAL
+    assert run.files == {"added": ["state/daemon-feed-floor-433", "state/stop-intent/kiss.json"],
+                         "removed": [], "changed": []}
+
+
+def test_unowned_process_is_manual_required(kiss_box, run_op):
+    """intended: a matching process LHPC does not own is never signalled — MANUAL_REQUIRED, ok
+    False, no daemon release, no markers touched, no stop intent."""
+    box = kiss_box()
+    box.fake.cmdlines_data[4242] = ["loraham-kiss-tnc"]
+    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "summary": "Stop for 'kiss' is NOT fully verified — see details.",
+        "data_keys": [], "next_commands": ["lhpc status kiss"],
+        "heads": ["[already_stopped] loraham-kiss-serial", "[manual_required] loraham-kiss-tnc"],
+        "outcomes": [("loraham-kiss-serial", "already_stopped"),
+                     ("loraham-kiss-tnc", "manual_required")]}
+    assert run.phases == [
+        "lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.daemon-socket.868",
+        "lock:claim.loraham.radio.433", "lock:claim.loraham.radio.868",
+        "lock:claim.tcp.port.8001", "lock:lifecycle.kiss"] + SIGNAL
+    assert run.files == {"added": [], "removed": [], "changed": []}
+
+
+def test_process_that_does_not_cease(kiss_box, run_op, monkeypatch):
+    """intended: SIGTERM without verified cessation is STILL_RUNNING — no SIGKILL, the ownership
+    record and the running-band marker are retained, no stop intent, no daemon release."""
+    box = kiss_box()
+    assert box.svc.start("kiss", apply=True).ok
+    # Stub the collaborator, not the coordinator: the process "outlives" its SIGTERM.
+    monkeypatch.setattr(Lifecycle, "_wait_ceased", lambda self, rec, timeout=0.0: False)
+    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
+    assert run.fields == {
+        "ok": False, "summary": "Stop for 'kiss' is NOT fully verified — see details.",
+        "data_keys": [], "next_commands": ["lhpc status kiss"],
+        "heads": ["[already_stopped] loraham-kiss-serial", "[still_running] loraham-kiss-tnc"],
+        "outcomes": [("loraham-kiss-serial", "already_stopped"),
+                     ("loraham-kiss-tnc", "still_running")]}
+    assert run.phases == STOP_LOCKS + SIGNAL
+    assert run.files == {"added": [], "removed": [], "changed": []}
+    assert box.owned() == ["loraham-kiss-tnc"]
+
+
+def test_contended_lock_refuses(kiss_box, run_op, held_lock):
+    """intended: another process holding the stack's lifecycle lock refuses the stop at once,
+    naming the holder, with nothing signalled or written."""
+    box = kiss_box()
+    with held_lock(box.svc._paths, "lifecycle.kiss"):
+        run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
+    assert run.fields["ok"] is False
+    assert re.fullmatch(r"Cannot stop 'kiss': resource 'lifecycle\.kiss' is busy: golden on 'x' "
+                        r"\(pid \d+\)", run.fields["summary"])
+    assert run.fields["outcomes"] == [] and run.fields["heads"] == []
+    assert run.phases == STOP_LOCKS
+    assert run.files == {"added": [], "removed": [], "changed": []}
```
