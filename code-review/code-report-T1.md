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
