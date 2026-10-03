# Code report — T1: golden / characterization tests of the six coordinating operations

Base `origin/integration/0.12.0` (f257831). Test-only batch: no production change.
`git diff --stat f257831..HEAD -- lhpc testlab` → empty (checked before this commit and again
before the push).

## Correction 2 (gate 1 round 2: RED — findings F and G; round-1 C, D, E confirmed fixed)

Every fix is amended into the commit it belongs to (`git commit --fixup` + `--autosquash`):
plan 00fd913 → 95abb69, harness 99d3cc3 → 3386a3a, stop/restart a4f7b4c → ae68018,
boot-restore f3dc59a → f1efd85, and this report. The two commits with no change keep their
patch-ids (`git show <sha> | git patch-id --stable`): save_config_bundle `a45c962362a9`
(11e38be → 3863140), build `3a05b9bf473d` (dc81e41 → c9cddbc); their SHAs changed only because
their parents did.

| finding | verified against the repo | commit | what changed |
|---|---|---|---|
| F — plan and report claimed "No collaborator result is stubbed", but `prior_boot` stubbed `_web_integration_proven()` and `boot_restore_enabled()` | Confirmed. The round-2 `prior_boot` set both with `monkeypatch.setattr` (and `service_boot_restore.current_boot_id` as well), and `test_disabled_retires_the_evidence` stubbed `boot_restore_enabled` a second time. | 3386a3a, f1efd85, 95abb69, this report | All three stubs are removed. The real values are arranged in the test's lab runtime. This boot's id comes from a file named by `LHPC_BOOT_ID_FILE`, the production override every boot-id reader honours (`lifecycle.py:169`). Restore is enabled by the default `[boot] restore = true` (`config.py:203`), because no `[boot]` table is written. The disabled case saves the switch off through the production setter `set_boot_restore(False)`. The web integration is proven by the canonical `lhpc-web.service` (`updater_units.render`) and its `default.target.wants` link, both written into the test's isolated HOME for the box's root. `_web_integration_proven` then runs unpatched (`service_boot_restore.py:43`). `test_nothing_to_restore` gets the same host through `prior_boot(root, evidence=False)`. The claim is now scoped to exactly what holds: "The TNC's readiness and cessation collaborators are not stubbed". Every remaining substitution is listed by name in the plan (risks), in this report (6-point 3) and in the module docstring of `tests/golden/conftest.py`. |
| G — `test_entry_hook_refusal_stops_nothing`'s tag said the hook runs after the preflight, but it asserts `seen == [LOCKS]` / `phases == LOCKS` with no `recheck:preflight` | Confirmed from the code: the tag was wrong and the ordering is right. `restart()` calls the hook inside its lifecycle guard at `service_lifecycle_ops.py:2769`–`2772`, before `self._restart_impl(...)` at `:2773`. `_restart_impl` → `_restart_impl_inner` (`:3001`) runs `_start_preflight_refusal` at `:3056`, after the hook. The normal restart records `recheck:preflight` after LOCKS for that reason: it runs the hook-free path into `_restart_impl`. | ae68018 | The tag now reads: the hook runs "after every lock and the identity recheck, and BEFORE the start preflight (`_start_preflight_refusal`, which the restart runs only afterwards, in `_restart_impl`) and the stop; its refusal leaves the running stack up and the preflight never runs". The module docstring's phase list was fixed the same way (locks → hook → start preflight → stop → start). The expected ordering is unchanged, because it is what the coordinator does. The plan's restart row names `:2769` and `:3056`. |

Red-before for G (the mismatch): the expected ordering was set to what the old tag claimed,
`seen == [LOCKS + ["recheck:preflight"]]` and `run.phases == LOCKS + ["recheck:preflight"]`. Then
`python -m pytest -q -p no:cacheprovider tests/golden/test_golden_restart.py -k entry_hook` →
`1 failed, 5 deselected` (`At index 0 diff: [..., 'lock:source.src/loraham-kiss-tnc'] != [...,
'recheck:preflight']`). The edit was reverted and never committed. Red-before for F: every
case that uses `prior_boot` now reaches the real gates. The first draft of this correction
wrote the unit only next to planted evidence. `test_nothing_to_restore`, which plants none, then
failed: `test_golden_boot_restore.py` → `1 failed, 6 passed`, with the summary "Boot restore
disabled (web console integration not proven: lhpc-web.service is not enabled (no wants
symlink)) …". So the unpatched `_web_integration_proven` is the one consulted.

Recorded, not fixed (production docstring, `lhpc/` untouched): **T1-N2.** The `restart`
docstring (`service_lifecycle_ops.py:2692`–`2696`) says the hook runs with "preflight and the GPS
position done". That holds for the identity and saved-launch rechecks and the position. It does
not hold for the start preflight (`_start_preflight_refusal`: dependency band, firewall gate,
ambiguity, band owners), which runs after the hook. The 6-point contract line below is corrected
accordingly.

### Numbers, measured (at the T1 tip f1efd85 unless stated)

| figure | command | output |
|---|---|---|
| T1 test lines added | `git diff --stat f257831..f1efd85 -- tests` | `8 files changed, 1405 insertions(+)` |
| per file | same command | README 7, conftest 438, boot_restore 161, build 177, restart 143, save_config_bundle 141, start 204, stop 134 |
| production lines changed | `git diff --stat f257831..HEAD -- lhpc testlab \| wc -l` (at the branch tip) | `0` |
| cases per module | `python -m pytest --collect-only -q -p no:cacheprovider tests/golden/test_golden_<op>.py` | start 8, stop 6, restart 6, save_config_bundle 7, build 7, boot_restore 7 (41) |
| tags | `grep -c '^    """intended:' tests/golden/test_golden_*.py`; `grep -h '^    """known defect' tests/golden/test_golden_*.py \| wc -l` | 40 intended, 1 known defect |
| golden + repo | `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` | `461 passed, 5 skipped` |
| golden set, three runs | `python -m pytest -q -p no:cacheprovider tests/golden` ×3 | `41 passed` ×3 |
| restart golden | `python -m pytest -q -p no:cacheprovider tests/golden/test_golden_restart.py` | `6 passed` |
| boot-restore golden | `python -m pytest -q -p no:cacheprovider tests/golden/test_golden_boot_restore.py` | `7 passed` |
| lint | `ruff check tests --select F,E9` | `All checks passed!` |
| unchanged commits | `git show <sha> \| git patch-id --stable` | 3863140 `a45c962362a9` (was 11e38be, same), c9cddbc `3a05b9bf473d` (was dc81e41, same) |

## Correction 1 (gate 1 round 1: RED, 4 findings; adcae90 and 80afd93 OK)

Every fix is amended into the commit it belongs to (`git commit --fixup` + `--autosquash`).
The two commits judged OK keep their patch-ids (`git show <sha> | git patch-id --stable`):
save_config_bundle `a45c962362a9` (adcae90 → 11e38be), boot-restore `f69ab41be6a6`
(80afd93 → f3dc59a); their SHAs changed only because their parents did.

| finding | commit | what changed |
|---|---|---|
| C — the goldens stubbed `Lifecycle._wait_ceased → False` and tagged the case `intended:` | 99d3cc3 (harness), a4f7b4c (stop, restart), 00fd913 (plan) | The stub is gone. `KissBox.survives_sigterm()` makes the box spawn a TNC that really ignores SIGTERM (`signal.signal(SIGTERM, SIG_IGN)` in the child, its own session). `test_process_that_does_not_cease` and `test_unverified_stop_aborts_before_any_start` start it, stop it, and observe the real `_wait_ceased` outcome: `still_running`, the record kept, and `box.tnc_alive()` (from `/proc/<pid>/stat`) still true. Only the bounded wait is shortened (`Lifecycle.STOP_WAIT_S` 1 s; production 5 s). Every spawned process is killed (`killpg`, SIGKILL) and reaped in the fixture's teardown. |
| D — `_TncEndpoint` synthesized the listener from the TNC's liveness | 99d3cc3, 00fd913 | The box's TNC is a real process that opens a real 127.0.0.1 listener on a free port (or, after `endpoint_never_up()`, none). `_TncEndpoint` reads the kernel's `/proc/net/tcp` with the production parser `parse_proc_net_tcp` and keeps only that port's LISTEN socket, reported as the manifest's 8001 (the one translation). Readiness is observed: "alive but never opened" is a process that never binds. The start's real bounded wait now runs (`ENDPOINT_VERIFY_TIMEOUT_S` 3 s in the box; the suite default 0 had made it a single snapshot). The start goldens pin the real verification line: `[verified] … (127.0.0.1:8001: present (family=ipv4))` and `[unverified] … never came up (127.0.0.1:8001: absent (family=ipv4)); cleanup: stopped`. |
| E — the marker-order case filtered heads with `startswith("[succeeded]")` | dc81e41 | The case compares the complete `run.fields` with `==` (ok, summary, data_keys, next_commands, outcomes, all 14 heads in order) and the complete `run.files` (added, removed, changed). |
| precision | 00fd913, this report | Base named `origin/integration/0.12.0` (the plan and report said a base name that does not exist). The plan's harness and risk text, and this report's fakes, no-network, dependency, line-count and README sentences, now describe the amended diff. |

Red-before of the assertions made real. Each mutation was applied to `lhpc/` in two trees — the
original S1 branch (old tests) and this branch (new tests) — run, and reverted
(`git checkout -- <file>`); none was committed:

- C: `Lifecycle._wait_ceased` returns `True` at once (cessation claimed without proof) — stop +
  restart modules: old `12 passed`, new `2 failed, 10 passed` (the two outlives-SIGTERM cases).
- D: `_ready_endpoints_present` never waits (`budget = 0.0`) — start module: old `8 passed`,
  new `1 failed, 7 passed` (the happy path: the real listener is not up at the first look).
- D, harness: the child listens even after `endpoint_never_up()` — start module: `1 failed,
  7 passed` (the never-up case fails because the listener is observed).
- E: one `[log]` line per component instead of per step (`seen.add(comp_id)`) — build module:
  old `7 passed`, new `1 failed, 6 passed`.

Evidence: `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` at the T1 tip →
`461 passed, 5 skipped`; the golden set at the T1 tip, three runs in a row: `41 passed` each.
`ruff check tests --select F,E9` → `All checks passed!`.

## Commits

| sha | subject | files | tests | red-before |
|---|---|---|---|---|
| 95abb69 | T1: plan — golden tests of the six coordinating operations | `plans/PLAN-T1.md` | — | — |
| 3386a3a | T1: golden harness and the start golden module | `tests/golden/conftest.py`, `tests/golden/test_golden_start.py`, `tests/README.md` | 8 | yes, mutation "start" |
| ae68018 | T1: stop and restart golden modules | `tests/golden/test_golden_stop.py`, `tests/golden/test_golden_restart.py` | 6 + 6 | yes, mutations "stop", "restart" |
| 3863140 | T1: save_config_bundle golden module | `tests/golden/test_golden_save_config_bundle.py` | 7 | yes, mutation "save_config_bundle" |
| c9cddbc | T1: build golden module | `tests/golden/test_golden_build.py` | 7 | yes, mutation "build" |
| f1efd85 | T1: boot-restore golden module | `tests/golden/test_golden_boot_restore.py` | 7 | yes, mutation "boot-restore" |

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

The tree was clean after the runs (`git status --short` showed only the new test files). The
six mutations were re-run on the corrected modules (Correction 1) with the same counts.

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
  others are `KissBox` (the box handle, which also spawns the box's real TNC process) and
  `_TncEndpoint` (an iterable the FakeSystem reads as its listener table: the kernel's
  `/proc/net/tcp`, filtered to the TNC's port). No framework, no registry, no result type.
- Extractions: none. Line counts: production before/after are unchanged (0 lines touched).
  Tests: +1398 lines in `tests/golden/` (conftest 438, six modules 134–204 each) and +7 in
  `tests/README.md` (measured: table in Correction 2).
- Dependencies of the new tests: `lhpc.core.{services, lifecycle, config, boot_restore,
  reslock, restart_required, paths, probes.backends, service_base, updater_units,
  service_boot_restore}`, the production parser `probes.backends.parse_proc_net_tcp`, and the
  existing root fixture `set_call`.
- Typed outcomes: none added.

## 6-point block

1. **CONTRACTS.** None changed. Contracts the goldens now pin, each with its owner:
   - `ActionResult` fields (`lhpc/core/service_base.py:110`).
   - `Outcome` values (`lhpc/core/outcomes.py:16`).
   - start/stop/restart signatures and the `_before_start_locked`/`_before_restart_locked` hook
     contract: for a start it runs after every lock and the recheck, before the first
     mutation; for a restart it runs after every lock and the identity recheck, before the start
     preflight and the stop (`:2769` vs `:3056`; T1-N2). An `ActionResult` return cancels
     (`service_lifecycle_ops.py:641/2373/2680`).
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
   - Fakes: the real `FakeSystem` and the real `Lifecycle`, whose spawn launches a real detached
     TNC process, so the ownership record carries a real `/proc` identity, the TNC's readiness is
     the listener that process really opens (read from `/proc/net/tcp`), and its cessation is the
     real one (a TNC that ignores SIGTERM really outlives the stop). The TNC's readiness and
     cessation collaborators are not stubbed. Substituted, every one by name (the module
     docstring of `tests/golden/conftest.py` lists the same):
     (1) `ControllerService._lifecycle`: the real `Lifecycle` with the box's spawn callable,
     which launches the TNC process instead of the component's binary;
     (2) the `System`: a `FakeSystem` — the daemon CONF socket answering READY, the listener
     table `_TncEndpoint` (the kernel's, filtered to the TNC's port, reported as 8001), and the
     host data a scenario puts into it: `cmdlines_data` entries (a band owner, an unowned TNC, a
     running chat) and the build steps' `commands` results;
     (3) two bounded waits: `ControllerService.ENDPOINT_VERIFY_TIMEOUT_S` 3 s and
     `Lifecycle.STOP_WAIT_S` 1 s in the two outlives-SIGTERM cases;
     (4) `LHPC_BOOT_ID_FILE` in `prior_boot` (the production boot-id override; the two
     boot-restore host gates are arranged, not stubbed — Correction 2, F);
     (5) single-case fault injection and probes: `config._atomic_write` failing its third write
     (save_config_bundle) and a delegating `runtime_fs.atomic_write` recording the build's two
     stamps (build);
     (6) the `ORDER_SEAMS` wrappers, which take `*args, **kwargs` and delegate;
     (7) the suite's autouse isolation (`tests/conftest.py`): `_fw_integration_state`,
     `_fw_units_enabled`, `firewall.RECEIPT_PATH`, `updater_units._SYSTEM_ROOTS`, HOME and the
     XDG roots, `config.HW_DEFAULT`, `display_available`, the three `RealProcFs` reads,
     `gps.local_gpsd_listening`, `read_kernel_time_state`, the binary-download refusal,
     `DAEMON_VERIFY_TIMEOUT_S` and `Lifecycle.OBSERVE_TIMEOUT_S`.
   - Probes on EIO/EACCES/ENOTDIR: not in scope (no probe touched); the ENOSPC write failure is
     covered (`test_failed_write_rolls_back`).
   - KeyboardInterrupt through cleanup: not in scope (no cleanup code touched).
   - Same decision across entry paths: batch T3.
   - Stacked-only conflicts: the new files are only under `tests/golden/`, plus one
     7-line bullet in `tests/README.md` (the `golden/` bullet). No `lhpc/` or `testlab/`
     file. CHANGELOG not touched (see deviations).
4. **TEST RULES.**
   - Red-before: per module, by mutation (table above).
   - Decision-bearing strings are compared with `==` or `re.fullmatch`; no golden module uses
     `startswith` (checked with `grep -n startswith tests/golden/test_golden_*.py` → no hit).
   - No unchecked error return: every seeding call asserts `.ok`.
   - No network beyond loopback: the `held_lock` helper spawns a local interpreter and the TNC
     is a local interpreter listening on a free 127.0.0.1 port.
5. **WHOLE TEST DIRECTORIES.**
   - `python -m pytest -q -p no:cacheprovider tests/golden tests/repo` (at the T1 tip, after
     Correction 2) → `461 passed, 5 skipped`. The golden set alone was run three times in a
     row: `41 passed` each.
   - `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` →
     `All checks passed!`.
   - No signature changed, so there is nothing to grep for.
   - `tests/README.md` lives in the `tests/` root, but running the whole `tests/` directory would
     be the forbidden full-suite run. The README's one consumer, the repo hygiene set
     (`tests/repo`), was run.
6. **ADVERSARIAL SELF-REVIEW.** Found and fixed before this report:
   - (a) A static listener made every stop read `endpoint_still_present` and a second start
     spawn a duplicate. (Correction 1 replaced the liveness-following listener that fixed it
     with the kernel-observed one.)
   - (b) The first `held_lock` helper passed a `str` root and failed. Fixed to `Path`.
   - (c) `startswith` on the busy summary was replaced by `re.fullmatch`.
   - (d) The build-marker order was asserted only by the final files. It now records the two
     stamps in the phase log, so the mutation can fail it.
   - (e) Two unused-variable and import-order nits.
   - (f) Correction 2: the canonical web unit was first written only next to planted evidence,
     so `test_nothing_to_restore` read "integration not proven". `prior_boot(root,
     evidence=False)` now arranges the host without evidence.
   - (g) Correction 2: removing the `boot_restore_enabled` stub left an unused
     `ControllerService` import, an unused `monkeypatch` parameter and a double blank line in
     the import block. All three were removed.
   - (h) Correction 2: `grep -n "stub" plans/PLAN-T1.md code-review/code-report-T1.md
     tests/golden/*.py` was checked by hand. Every remaining "stub" sentence says "not stubbed"
     of exactly the TNC collaborators or of the boot-restore gates, and each is true of the diff.
   The docs and this report describe exactly the diff.

## Deviations

- **No CHANGELOG line.** The batch is test-only, an operator sees no change, and FILES limits
  T1 to `tests/`. The batch's FILES list wins over the general rule; the handler adds a line at
  the release commit if wanted.
- **One harness file instead of a helper module.** Rule 7 forbids sibling-test imports, so the
  shared code is fixtures in `tests/golden/conftest.py`; the tests import nothing from it.
- **Private names.** The golden set names private coordinator steps by design. They are
  confined to `ORDER_SEAMS`, and `tests/README.md` states the exception.
