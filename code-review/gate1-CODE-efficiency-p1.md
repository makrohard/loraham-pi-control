# Gate 1: efficiency-p1 (quick wins QW1–QW3: the runner's wait, the read-only source probe, one process table), v2

**Request.** Judge the code commits below against the plan's QW1–QW3 (quoted). That means: correctness; whether each
red-before test can fail for the defect named; whether behaviour changes only where the plan says (QW3 is an approved coherence change, everything else must be output-identical); the
docs and CHANGELOG lines; and the report's claims against the diff. The plan is GREEN (gate 1, round 4); only its QW1–QW3 are implemented here.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's QW1–QW3 (plan v2 §3 P1, verbatim; v4 keeps them and adds §1 and §2 below)

**P1 — plain modules.**
- **QW1 (F1), commit 6fce7c02.** `RealCommandRunner.run` calls `_wait_exit(proc, timeout)` (`probes/backends.py:317`)
  instead of `proc.wait(timeout=timeout)`. The helper, verbatim:
  ```
  deadline = time.monotonic() + timeout
  try: fd = os.pidfd_open(proc.pid)
  except (AttributeError, OSError): proc.wait(timeout=timeout); return
  try: poller = select.poll(); poller.register(fd, select.POLLIN); ready = poller.poll(max(timeout, 0) * 1000)
  except OSError: ready = None
  finally: os.close(fd)
  if ready is None: proc.wait(timeout=max(deadline - time.monotonic(), 0))
  elif not ready: raise subprocess.TimeoutExpired(proc.args, timeout)
  else: proc.wait()
  ```
  Policy: EINTR is resumed by Python itself with the time left (PEP 475); any other poll error falls back to today's
  timed wait for what is left of the SAME deadline; no pidfd → today's timed wait; Ctrl-C propagates as from today's
  `proc.wait` (the fd is closed in `finally`). TimeoutExpired takes today's terminate path, unchanged. Return code is
  `proc.returncode` as before (a signaled child: negative). Tests (`tests/core/test_bounded_runner.py`): a short
  command is reaped with `time.sleep` forbidden (red before); `pidfd_open` raising → same rc, timeout still at 0.4 s;
  `sh -c 'kill -TERM $$'` → rc −15, not timed out; the exit-wait poll raising EIO (the pipe drains keep the real poll)
  → the fallback ran (asserted) and rc/stdout are `0, "ok\n"`. The existing timeout/tree-kill tests run unchanged.
- **QW2 (F2), commit 7fb2af6d.** The probe's five git commands run with `GIT_OPTIONAL_LOCKS=0` (git's documented
  switch for background readers; env, so argv and every argv-keyed fake stay as they are). `git describe --dirty`
  refreshes the index under its lock even then (measured: with `GIT_OPTIONAL_LOCKS=0`, `status` left `.git/index`
  unchanged, `describe --dirty` rewrote it), so describe runs without `--dirty` and the `-dirty` suffix comes from the
  status answer that already decides DIRTY. Only divergence: a staged change whose worktree equals HEAD is DIRTY
  today with a version WITHOUT the suffix; after the change the version carries it, i.e. state and version agree.
  The version is display-only (`status.py:167`, `install.py:2711`, CLI table). Test (`tests/install/test_source.py`,
  real git): a touched-only file → MATCH, a changed file → DIRTY, version `<sha7>` / `<sha7>-dirty`, and the whole
  `.git` tree (every dir mtime, every file mtime + bytes) identical before and after. Red on dev's `source.py`
  (2 failed), green on the branch. Four argv-keyed test fakes lose `--dirty`.
- **QW3 (F3), commit e5a5208a.** `assess_stacks` reads `cmdlines()` once beside the TCP listeners (`status.py:126`);
  `probe_process(system, spec, table=None)` matches against it, a standalone call still reads fresh. Coherence
  change, not identity: one instant for all components instead of 22 successive ones; a process that starts or stops
  during the ~1 s snapshot is either in or out for every component. Red before: 22 reads (test_snapshot_memo.py).

## Plan v4 §1 (verbatim)

## 1. QW2 — the version is what `describe --dirty` prints (measured, git 2.55, this PC)
Method per case: build a repo with tag `v1` and tracked `f`, `g`, `d/h`; apply the change; wait 1.1 s and touch the
tracked files (a stale index stat cache, the box's case); copy the checkout and run `git describe --tags --always
--dirty` on the COPY (today's text); on the original run the branch's two commands with `GIT_OPTIONAL_LOCKS=0`
(`status --porcelain=v2 --branch --untracked-files=no`, then `describe --tags --always`; suffix `-dirty` when status
lists any entry); `.git` = an md5 of `find .git -printf '%p %T@ %s'` before and after the two commands.
| case | today | branch | `.git` |
|---|---|---|---|
| clean · untracked file · assume-unchanged + edit · skip-worktree + edit | v1 | v1 | untouched |
| modified · staged-only (worktree = HEAD) · staged + modified · staged new file · deleted · deleted from index · mode change · edit in a subdirectory · intent-to-add | v1-dirty | v1-dirty | untouched |
| submodule: clean · untracked file inside | v1 | v1 | untouched |
| submodule: modified worktree · new submodule HEAD | v1-dirty | v1-dirty | untouched |
17 of 17 equal, 17 of 17 untouched (scripts and output: `efficiency-qw2-cases.txt` in the run dir). The test `test_the_probed_version_is_what_git_describe_dirty_says` (real git, 10
cases: clean, modified, staged-only, staged-and-modified, staged-new, deleted, deleted-from-index, mode, untracked,
intent-to-add) compares the probe's version with `describe --dirty` run on a copy; with the suffix removed from the
code, 8 of the 10 fail (checked). The 4 submodule cases stay a by-hand row.

## Plan v4 §2 (verbatim)

## 2. The coherence changes (one consistent moment instead of a mix; the maintainer's YES, 2026-10-16)
| item | before | after |
|---|---|---|
| QW3 | 22 reads of `/proc` per snapshot, each component at its own instant | one table per snapshot |
| QW4 | each `Lifecycle` in a request re-read the generated MeshCore config | read once per request; LHPC's own writers (`write_config_files`, `clean`, both `@invalidates_snapshot`) drop it, so the next read sees their write; a write by another process during a request is seen from the next request on |
| QW6 | the Wi-Fi row and the wired row each asked NetworkManager | one answer for both rows |
Each commit message states its change in these words; `docs/architecture.md` ("Evidence once per request") states
QW3 and QW6. QW4 carries the same approved within-request trade-off: a change another process makes to the generated
MeshCore config during a page build is reflected from the next request on.

## The commit messages (`git log --reverse origin/dev..b06803d9`, full)

### 8062c4db efficiency: wait for a command's exit without the sleep loop

efficiency: wait for a command's exit without the sleep loop

A timed Popen.wait polls in sleeps of 1 to 50 ms, so every short probe
(git, systemctl) was noticed late: 141 sleeps in one dashboard poll on a
Pi Zero 2 W. The runner now blocks on a pidfd until the child exits or
the deadline passes, then reaps. One deadline is taken at the start: the
poll waits for what is left of it, an interrupted poll is resumed by
Python with the time left, and without pidfd, or when the poll itself
fails, the timed wait runs for what is left. The timeout path is
unchanged.

Red before: test_a_short_command_is_reaped_without_a_sleep_loop failed
(the runner slept). Guards: no pidfd -> same rc and timeout; a child
killed by SIGTERM -> rc -15; the exit-wait poll failing -> the fallback
ran and the result is the same; on an injected clock the poll gets 9 s
of a 10 s timeout after 1 s went by, and the fallback the 5 s left after
a poll that failed 4 s later.

### 65da4f02 efficiency: a source probe writes nothing into the checkout

efficiency: a source probe writes nothing into the checkout

git status and git describe --dirty took .git/index.lock to refresh the
index, so every status read wrote into each source checkout (all 16
.git directories changed during a read-only window on the Zero). The
probe now runs git with GIT_OPTIONAL_LOCKS=0, added to the runner's fixed
environment (which it never replaces with the controller's own), and
describe runs without --dirty, which refreshes under the lock regardless;
the -dirty suffix comes from the status answer that already decides
DIRTY. The version is unchanged: for every kind of tracked change it is
what describe --dirty prints (10 real-git cases in the test; by hand
also 4 submodule cases).

Red before: test_a_source_probe_writes_nothing_into_the_checkout
(touched and changed file) failed on the rewritten .git tree. Equal
output: test_the_probed_version_is_what_git_describe_dirty_says;
test_a_command_gets_the_runners_environment_plus_its_own_variables
(the child's environment is the fixed one plus GIT_OPTIONAL_LOCKS).

### b06803d9 efficiency: one process table per status snapshot

efficiency: one process table per status snapshot

probe_process listed /proc and read every cmdline once per component,
22 full scans per page on the Zero. assess_stacks now reads the table
once, as it already does for the TCP listeners, and each component is
matched against it; a standalone assess_component still reads fresh.
A coherence change, decided by the maintainer (2026-10-16): a process
that starts or stops while the snapshot is built is in or out for every
component, one moment instead of a mix of 22.

Red before: test_a_snapshot_reads_the_process_table_once (22 reads).


## The code report (verbatim; committed as `code-review/code-report-efficiency-p1.md`)

# Code report: efficiency-p1 (quick wins QW1–QW3 of the efficiency plan)

Branch `cons/efficiency-p1-r1` on `origin/dev` = `d77e34f4`. Plan: `PLAN-efficiency` v4 (quick wins QW1–QW3 unchanged
since v3; gate 1 round 3 OK on QW1, QW2 and QW3). Three commits, one per quick win; packet 2 (QW4–QW7) stacks on this
branch.

**v2 for the record.** Gate 1 round 1 (v1) and the second read gave three points, all folded into the commits: (1) the second reader: the `docs/architecture.md` sentence of QW3 was stronger than the diff ("so it shows one moment, not a mix": systemd units, sockets and git are still read per component); it now reads "A snapshot reads the process table once, so every component is judged against the same table." (2) round 1 F1: the exit-wait poll used the full timeout, not what was left of the deadline; it now uses `deadline - now`, with an injected-clock test. (3) round 1 F2: the report did not show that `env` adds to, and never replaces, the child's environment; the runner excerpt is under the guardrails and a test pins it. Hashes v1 → v2: 398f5702 → 8062c4db, 2fda6024 → 65da4f02, 975c08db → b06803d9. Red-before and green below are on the v2 hashes.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| 8062c4db | efficiency: wait for a command's exit without the sleep loop | probes/backends.py, tests/core/test_bounded_runner.py, docs/architecture.md (one line pointer 767 → 797) | core: `test_a_short_command_is_reaped_without_a_sleep_loop` (`time.sleep` forbidden; `(rc, stdout, timed_out) == (0, "ok\n", False)`); `test_without_pidfd_the_runner_falls_back_to_the_timed_wait` (rc 3; timeout still `timed_out`, rc 124, < 5 s); `test_a_child_killed_by_a_signal_reports_the_signal` (rc −15); `test_a_failing_poll_falls_back_to_the_timed_wait_on_the_same_deadline` (the injected EIO ran, `failed == [1]`; same result); `test_the_exit_wait_and_its_fallback_share_one_deadline` (injected clock: 1 s passes in `pidfd_open`, the poll gets `9000.0` ms; it fails after 4 s more, the fallback `wait` gets `5.0` s) | yes, on d77e34f4: 3 failed, 5 passed — the sleep-loop test (the runner slept), the failing-poll and the one-deadline tests (no exit-wait poll exists to fail or to time; structural); the pidfd and signal tests pass on the parent (equal-output guards) |
| 65da4f02 | efficiency: a source probe writes nothing into the checkout | probes/source.py, tests/install/test_source.py, three argv-keyed fakes (tests/core/test_snapshot_memo.py, tests/web/test_stacks_page_source_state.py, tests/web/test_update_component_link.py), CHANGELOG.md | install, real git: `test_a_source_probe_writes_nothing_into_the_checkout[touch-match / change-dirty]` (state, head, version `<sha7>` / `<sha7>-dirty`, and every path under `.git` — dir mtimes, file mtimes and bytes — equal before and after); `test_the_probed_version_is_what_git_describe_dirty_says` (10 kinds of change: the version equals `git describe --tags --always --dirty` run on a copy); core: `test_a_command_gets_the_runners_environment_plus_its_own_variables` (`env` output == the runner's `_FIXED_ENV` plus `GIT_OPTIONAL_LOCKS=0`) | yes, on 8062c4db: 2 failed (the two write tests, `.git` rewritten), 11 passed (the version test and the environment test pass on the parent: equal-output guards; with the suffix removed from the new code 8 of the 10 version cases fail, checked by hand) |
| b06803d9 | efficiency: one process table per status snapshot | status.py, probes/process.py, tests/core/test_snapshot_memo.py, docs/architecture.md | core: `test_a_snapshot_reads_the_process_table_once` (the fake `cmdlines` called once per `assess_stacks`; the matched component's `pids == [4242]`, every other component's `[]`) | yes, on 65da4f02: 1 failed, `assert 22 == 1` |

**Red-before command** (`redbefore.sh`, kept outside the repository): for each commit, `git archive <commit>^` into an
empty scratch directory, the commit's changed test files written over it with `git show <commit>:<file>`, then
`python -m pytest -q -p no:cacheprovider <test files> -k "<the new tests>"` with the controller venv; the directory
is removed afterwards. Results are the table's last column.

**Green** (on b06803d9, a detached worktree): `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile
tests/core tests/install tests/repo tests/web tests/stacks tests/host tests/cli tests/golden` → `7691 passed, 1 skipped in 155.05s`.
`ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` → `All checks passed!`.
Lanes: none per packet (the handler runs them on dev per batch).

**Observed once, not this packet's:** in an earlier run of the same command on an archive copy of 975c08db,
`tests/golden/test_golden_boot_restore.py::test_failed_after_the_hook_consumes_the_evidence` failed once (`ok` True
instead of False). It did not reproduce: 5 solo runs each at d77e34f4, 975c08db and the packet-2 tip, and 3 runs
each of `pytest -n 12 tests/golden` at d77e34f4 and 975c08db, all passed. The fixture's `_free_port()`
(`tests/golden/conftest.py:325-327`) releases the port before the test's TNC is told to leave it closed, and
`_TncEndpoint` counts any loopback listener on that port as the TNC, so another process on a busy host can take the
port in between. Reported to the run's list; not changed here.

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  origin/dev..b06803d9
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 22 lines   OK
docs         1 files, +3 lines; fact "judged against the same table" -> docs/architecture.md   OK
tests        9 new tests; layers tests/core=core tests/install=install tests/web=web; red-before 3/3   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

- QW1: one plain function (`_wait_exit`, 29 lines with its docstring) and one call; no new state. A pidfd is
  opened and closed per wait.
- QW2: one module constant (`_READ_ONLY`), passed as `env` to the five git calls; `--dirty` dropped from one argv.
  The runner builds every child's environment as `{**_FIXED_ENV, **env}` (`probes/backends.py:292`; also :404,
  :460): a fixed PATH/LANG/LC_ALL plus a short list of forwarded variables, never the controller's whole
  environment, so `GIT_OPTIONAL_LOCKS` is added to exactly what git got before.
- QW3: one attribute set beside the existing `_listeners`, one optional argument on `probe_process`.
- Lines (`wc -l`, d77e34f4 → b06803d9): probes/backends.py 960 → 990; probes/source.py 137 → 142; status.py 483 → 488;
  probes/process.py 65 → 66. No service-family file is touched (the checker's modules line).

## Deviations from the plan

- v1/v2 planned `--no-optional-locks` in argv; the code passes `GIT_OPTIONAL_LOCKS=0` as environment (git documents
  the two as the same switch), so argv and every argv-keyed fake stay as they are.
- v1 planned to keep `describe --dirty`; it rewrites the index even with optional locks off (measured), so the
  suffix comes from the status answer. The version is unchanged in all 17 measured cases (plan v3 §1; the 10-case
  test above).

## The 6-point block

1. **Contracts.**
   - `_wait_exit(proc, timeout) -> None` (`probes/backends.py:243`): returns once the child has exited and is reaped,
     or raises `subprocess.TimeoutExpired(proc.args, timeout)` when the timeout passes first. Called at
     `probes/backends.py:317`, in place of `proc.wait(timeout=timeout)`; the `except TimeoutExpired` path below it
     (terminate the child's process group, wait 2 s) is unchanged. One deadline is taken at entry
     (`time.monotonic() + timeout`); the poll waits for what is left of it when the poll starts; EINTR is resumed by
     Python with the time left (PEP 475). `pidfd_open` missing or failing: today's `proc.wait(timeout=timeout)`. The
     poll raising `OSError`: `proc.wait(timeout=<what is left of the same deadline>)`. KeyboardInterrupt propagates as from today's wait; the
     pidfd is closed in `finally`.
   - `probe_source` (`probes/source.py`): every git call passes `env=_READ_ONLY` (`:19`; calls at `:68, :72, :74,
     :100, :119`); describe runs as `describe --tags --always` (`:118`) and the version is `<describe> + "-dirty"` when
     the status lists an entry (`:121`); a failed describe still yields `head[:12]`, without suffix, as before; the
     patched-only path still strips the suffix.
   - `StatusProber.assess_stacks` (`status.py:126`) reads `procfs.cmdlines()` once into `self._cmdlines`
     (an exception → `None` → each component reads fresh, as before) and clears it with `_listeners` in `finally`;
     `probe_process(system, spec, table=None)` (`probes/process.py:53`) matches against `table` when given
     (`status.py:188`). The other two callers (`lifecycle.py:1298`, `service_params.py:2031`) pass no table and read
     fresh as before.
2. **Invariants and their tests.**
   - A command's result (rc, stdout, stderr, timed_out, termination) is what it was: the four QW1 tests plus the
     existing 22 of `test_bounded_runner.py` (timeouts, tree kill, TERM-ignoring child, escaped descendant).
   - A status read changes nothing under a checkout's `.git`: `test_a_source_probe_writes_nothing_into_the_checkout`.
   - The shown version is today's: `test_the_probed_version_is_what_git_describe_dirty_says`.
   - One process table per snapshot (new invariant, the maintainer's coherence decision of 2026-10-16):
     `test_a_snapshot_reads_the_process_table_once`; the negative half (a standalone `assess_component` reads fresh)
     is the existing per-component tests in `test_probes.py`, unchanged and green.
3. **Failure classes.**
   - Fakes with the real signature: the poll fake wraps the real `select.poll` and fails only the exit wait (mask
     `POLLIN` alone), so the pipe drains keep working; `FakeSystem.cmdlines` keeps its signature.
   - EIO / missing syscall: the failing-poll and no-pidfd tests. EACCES: not reachable (a pidfd for the runner's own
     child).
   - Ctrl-C: `KeyboardInterrupt` leaves `_wait_exit` like today's wait; `finally` closes the pidfd; the caller's
     existing paths are unchanged.
   - CLI / web / job / boot agree: all go through `RealCommandRunner.run` and `StatusProber`; nothing is per-adapter.
   - Stacked conflicts: none; the three commits touch disjoint code.
4. **Test rules.** Behaviour through the public seam (`RealCommandRunner.run`, `probe_source`, `assess_stacks`); real
   git in a temp directory for QW2; no sleep, no source read, no network; the `-k` names say what each proves.
5. **Directories and ruff.** core, install, repo, web, stacks, host, cli, golden: `7691 passed, 1 skipped`; `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests
   --select F,E9` → `All checks passed!`.
6. **Adversarial self-review.** Found and fixed: (a) `describe --dirty` still wrote the index with optional locks
   off — measured, then removed; (b) the first poll-failure fake patched `select.poll` for the drains too and lost
   the output — narrowed to the exit wait; (c) an edit placed the "fallback ran" assert in the sleep-loop test, where
   it raised `NameError` — moved, all 26 runner tests rerun; (d) QW1 moved `_authenticate_tmp_peer` by 30 lines; the
   pointer in `docs/architecture.md` follows (checked: all 97 `file:line` + name pointers in the docs; the two other
   stale ones, `service_maintenance.py:1773 update` and `service_params.py:2813 enforce_identity`, are stale on
   d77e34f4 already and not in this packet's files); (e) the bench in plan v1 was contaminated and is withdrawn
   (plan v2 §7); (f) gate 1 round 1: the exit-wait poll took the full timeout rather than what was
   left of the deadline (fixed, injected-clock test), and the environment merge was not shown (excerpt + test).

## Numbers, measured

| figure | command | output |
|---|---|---|
| 22 process-table reads per snapshot before | the QW3 test on 65da4f02 | `assert 22 == 1` |
| 17 of 17 version cases equal, `.git` untouched | `efficiency-qw2-cases.txt` (scripts + output, run dir) | 17 lines `SAME  .git untouched` |
| 8 of 10 version cases fail without the suffix | the version test with `+ ("-dirty" if dirty else "")` removed | `8 failed, 2 passed` |
| per-call runner time on this PC | `python3 -I efficiency-bench-runner.py {base,head} <worktree> 100` (run dir) | status 8.74 → 7.08 ms, describe 3.67 → 3.39 ms, true 1.56 → 1.16 ms; second pair 3.78/3.56/1.61 → 3.30/2.69/0.64 ms |
| file sizes | `git show <ref>:<file> \| wc -l` | the guardrails list |
| the Zero figures (1.63 s dash poll, 141 sleeps, 22 `/proc` scans, 16 `.git` dirs written) | reported by `fieldtest-e293/50-profile-endpoints.txt` and `99-state-after.txt` (box window 1), not re-measured here | — |

## The diff (`git diff origin/dev b06803d9`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ba902936..fc20e551 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -20,6 +20,8 @@ Fixed
   readiness timeout names the stack only when LHPC reports the component stopped or failed.
 - The release check no longer starts an interactive program a second time after a timeout: a hung start fails
   (unmarked), and NomadNet's records the stacks of every Reticulum process and the shared instance's sockets.
+- Reading a stack's status (console pages, `lhpc status`) no longer takes git's index lock or rewrites the git
+  index in the stack's source checkout.
 
 ## 0.12.9
 
diff --git a/docs/architecture.md b/docs/architecture.md
index dbc40f22..2a917ef8 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -287,7 +287,8 @@ leaves on disk and which test proves it: the [invariant table](#invariant-table)
 - **Evidence once per request.** A page render reads each piece of evidence once (status
   snapshot, per-(stack, band) config, consumed-source SHAs, firewall status, listeners, git state
   per distinct checkout) through a thread-local request memo dropped at every request start and
-  around every mutation; rechecks under operation locks use `build_snapshot(fresh=True)`. Every
+  around every mutation; rechecks under operation locks use `build_snapshot(fresh=True)`. A
+  snapshot reads the process table once, so every component is judged against the same table. Every
   public `ControllerService` entry that writes anything is `@invalidates_snapshot`; the rest are
   listed with a reason in `snapshot_memo.SNAPSHOT_NEUTRAL`. The
   neutral writers whose writes no snapshot input observes (lock files, the native RF-log roll,
@@ -358,7 +359,7 @@ the runtime root unless absolute.
 | self-update | config migration is recorded before the checkout moves; a damaged journal blocks with no change ([deployment](deployment.md#self-update)) | `lhpc/core/selfupdate.py:651` `classify_journal` | the self-update lock | `state/selfupdate-migrate.json` and its git anchor | `tests/install/test_selfupdate_migration.py::test_interrupted_migration_recovered_by_fresh_service`, `::test_journal_persist_failure_refuses_before_mutation`, `::test_malformed_journal_blocks_without_mutation_or_deletion` |
 | radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py:49` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
 | radio / band ownership | the SPI bus is shared through the daemon's `spi0.lock` by the daemon and the Reticulum node; meshtasticd takes no part and is admitted beside the daemon only on the opposite band ([Radios](#radios-bands-and-resource-claims)) | the daemon, not LHPC; LHPC models it as the `spi.bus.0` / `spi.bus.0.unlocked` claims | the daemon's flock `state/loraham/spi0.lock` | — | `tests/core/test_resources_conflicts.py::test_cooperative_peers_do_not_conflict`, `::test_meshtastic_conflicts_with_daemon_on_868_radio_not_spi`; gap: the lock itself (daemon side) |
-| radio / band ownership | bounded CONF replies, TX-mode read-back, `/tmp` socket peer check ([Daemon sockets](#safety-model)) | `lhpc/core/daemon_control.py:179` `parse_conf_reply`, `:519` `apply_set`; `lhpc/core/probes/backends.py:767` `_authenticate_tmp_peer` | none | — | `tests/stacks/test_daemon_bounds.py::test_oversized_response_rejected`, `::test_too_many_tokens_rejected`; `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent`; `tests/core/test_post_start.py::test_tx_mode_fails_when_readback_mismatches`; `tests/web/test_socket_peercred.py::test_foreign_uid_tmp_peer_is_refused` |
+| radio / band ownership | bounded CONF replies, TX-mode read-back, `/tmp` socket peer check ([Daemon sockets](#safety-model)) | `lhpc/core/daemon_control.py:179` `parse_conf_reply`, `:519` `apply_set`; `lhpc/core/probes/backends.py:797` `_authenticate_tmp_peer` | none | — | `tests/stacks/test_daemon_bounds.py::test_oversized_response_rejected`, `::test_too_many_tokens_rejected`; `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent`; `tests/core/test_post_start.py::test_tx_mode_fails_when_readback_mismatches`; `tests/web/test_socket_peercred.py::test_foreign_uid_tmp_peer_is_refused` |
 | radio / band ownership | truthful outcomes: a start fails unless every required component is verified ready, and a failed start stops again what it launched or names what stayed up; a stop counts only when the process ceased and its endpoints are gone; an update with a failed part reports failure; the CLI exit status follows the result ([Truthful outcomes](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:900` `_start_coordinator`, `:1690` `_roll_back_start`, `:2621` `_stop_impl`; `lhpc/core/start_plan.py:77` `verify_start`; `lhpc/core/outcomes.py:71` `applied_ok`; `lhpc/core/service_maintenance.py:1773` `update`; `lhpc/adapters/cli/main.py:254` `_render` | the start, stop and update locks | owned records and markers cleared only on verified stop | `tests/core/test_outcomes.py::test_applied_ok_requires_all_verified`; `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified`, `::test_dependent_not_started_when_daemon_unready`; `tests/core/test_process_ownership.py::test_a_failed_start_leaves_nothing_of_the_stack_running`, `::test_a_failed_start_stops_the_daemon_only_if_it_launched_it`, `::test_a_failed_start_names_what_it_could_not_stop`; `tests/core/test_stop_propagation.py::test_stop_unverified_keeps_markers`; `tests/core/test_known_working.py::test_candidate_clear_failure_is_truthful_incomplete`; `tests/cli/test_cli.py::test_status_unknown_stack_exits_one`; gap: a daemon start's check of each band's CONF socket separately (only a daemon that is not ready at all is driven); the web flash agreeing with the CLI exit status (no test drives one result through both) |
 | radio / band ownership | no start without a resolvable identity ([Identity](#identity-and-callsigns)) | `lhpc/core/service_params.py:2813` `enforce_identity` | the start locks (re-judged under them) | — | `tests/core/test_identity.py::test_start_refuses_identity_before_boot_hook_and_feed_clear`, `::test_licensed_with_neither_value_is_refused`; `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` |
 | lab | a simulated reboot kills only owned groups and runs the real boot restore ([testlab](testlab.md)) | `testlab/lhpc_testlab/ops.py:283` `power` | admission (waited for) | the lab root's boot identity | `testlab/tests/unit/test_testlab.py::test_simulated_reboot_kills_owned_groups_and_runs_boot_restore`; `testlab/tests/acceptance/test_power_network.py::test_simulated_reboot_advances_boot_identity_and_recovers` |
diff --git a/lhpc/core/probes/backends.py b/lhpc/core/probes/backends.py
index b126d1c8..2aa1c9c4 100644
--- a/lhpc/core/probes/backends.py
+++ b/lhpc/core/probes/backends.py
@@ -240,6 +240,36 @@ def _drain_fd(stream, emit, stop) -> bool:
     return False
 
 
+def _wait_exit(proc, timeout: float) -> None:
+    """`proc.wait(timeout=timeout)` without its sleep loop: CPython polls a timed wait in sleeps of
+    1 to 50 ms, so every short probe was noticed late. Block on a pidfd until the child exits or the
+    timeout passes (TimeoutExpired, as before), then reap. An interrupted poll is resumed by Python
+    with the time left (PEP 475); without pidfd, or when the poll itself fails, the timed wait runs
+    for what is left of the same deadline."""
+    import select
+    import time
+    deadline = time.monotonic() + timeout
+    try:
+        fd = os.pidfd_open(proc.pid)
+    except (AttributeError, OSError):
+        proc.wait(timeout=timeout)
+        return
+    try:
+        poller = select.poll()
+        poller.register(fd, select.POLLIN)
+        ready = poller.poll(max(deadline - time.monotonic(), 0) * 1000)
+    except OSError:
+        ready = None
+    finally:
+        os.close(fd)
+    if ready is None:
+        proc.wait(timeout=max(deadline - time.monotonic(), 0))
+    elif not ready:
+        raise subprocess.TimeoutExpired(proc.args, timeout)
+    else:
+        proc.wait()
+
+
 def _bounded_drain(stream, sink: bytearray, stop) -> None:
     """Read a subprocess pipe to EOF (or until `stop`), retaining only the last
     _MAX_CAPTURE_BYTES in `sink`."""
@@ -284,7 +314,7 @@ class RealCommandRunner:
         timed_out = False
         termination = ""
         try:
-            proc.wait(timeout=timeout)
+            _wait_exit(proc, timeout)
         except subprocess.TimeoutExpired:
             timed_out = True
             # Terminate the WHOLE owned session (a TERM-ignoring child can outlive its
diff --git a/lhpc/core/probes/process.py b/lhpc/core/probes/process.py
index d17d73d2..9d605315 100644
--- a/lhpc/core/probes/process.py
+++ b/lhpc/core/probes/process.py
@@ -50,10 +50,11 @@ def matches(spec: ProcessSpec, argv: list[str]) -> bool:
     return not (spec.any_args and not any(_token_contains(argv, p) for p in spec.any_args))
 
 
-def probe_process(system: System, spec: ProcessSpec) -> ProcessMatch:
+def probe_process(system: System, spec: ProcessSpec, table=None) -> ProcessMatch:
+    """Match `spec` against `table` ({pid: argv}, one snapshot's read of /proc), else a fresh read."""
     pids: list[int] = []
     sample = ""
-    for pid, argv in sorted(system.procfs.cmdlines().items()):
+    for pid, argv in sorted((system.procfs.cmdlines() if table is None else table).items()):
         if matches(spec, argv):
             pids.append(pid)
             if not sample:
diff --git a/lhpc/core/probes/source.py b/lhpc/core/probes/source.py
index 7e361c69..861b1372 100644
--- a/lhpc/core/probes/source.py
+++ b/lhpc/core/probes/source.py
@@ -14,6 +14,9 @@ from ..model import SourceSpec, SourceState
 from .backends import System
 
 _TIMEOUT_S = 3.0
+# A read never takes `.git/index.lock` to refresh the index: same answers, no write into the checkout,
+# and no lock a concurrent update's own git could fail on (git's switch for background readers).
+_READ_ONLY = {"GIT_OPTIONAL_LOCKS": "0"}
 
 
 @dataclass
@@ -62,13 +65,13 @@ def lhpc_patched_only(system: System, abs_path: str, patches) -> bool:
     if not files:
         return False
     check = system.runner.run(["git", "-C", abs_path, "apply", "--reverse", "--check", *files],
-                              timeout=_TIMEOUT_S)
+                              timeout=_TIMEOUT_S, env=_READ_ONLY)
     if check.returncode != 0:
         return False
     want = system.runner.run(["git", "-C", abs_path, "apply", "--numstat", *files],
-                             timeout=_TIMEOUT_S)
+                             timeout=_TIMEOUT_S, env=_READ_ONLY)
     have = system.runner.run(["git", "-C", abs_path, "diff", "HEAD", "--numstat"],
-                             timeout=_TIMEOUT_S)
+                             timeout=_TIMEOUT_S, env=_READ_ONLY)
     if want.returncode != 0 or have.returncode != 0:
         return False
     return _numstat(want.stdout) == _numstat(have.stdout)
@@ -94,7 +97,7 @@ def probe_source(system: System, spec: SourceSpec, abs_path: str) -> SourceProbe
     # their own repo are NOT source edits). Two subprocesses per source instead of three.
     status_res = system.runner.run(
         ["git", "-C", abs_path, "status", "--porcelain=v2", "--branch", "--untracked-files=no"],
-        timeout=_TIMEOUT_S,
+        timeout=_TIMEOUT_S, env=_READ_ONLY,
     )
     if status_res.timed_out:
         ev["error"] = "git status timed out"
@@ -109,11 +112,13 @@ def probe_source(system: System, spec: SourceSpec, abs_path: str) -> SourceProbe
         return SourceProbe(SourceState.UNKNOWN, evidence=ev)
     ev["head"] = head
 
+    # `--dirty` is not used: describe refreshes the index under its lock even with optional locks
+    # off. The suffix comes from the status answer above, the same verdict that makes it DIRTY.
     desc = system.runner.run(
-        ["git", "-C", abs_path, "describe", "--tags", "--always", "--dirty"],
-        timeout=_TIMEOUT_S,
+        ["git", "-C", abs_path, "describe", "--tags", "--always"],
+        timeout=_TIMEOUT_S, env=_READ_ONLY,
     )
-    version = desc.stdout.strip() if desc.returncode == 0 else head[:12]
+    version = (desc.stdout.strip() + ("-dirty" if dirty else "")) if desc.returncode == 0 else head[:12]
     if dirty and spec.patches and lhpc_patched_only(system, abs_path, spec.patches):
         # The only modifications are LHPC's own build-time patch: not an operator change.
         dirty, version = False, version.removesuffix("-dirty")
diff --git a/lhpc/core/status.py b/lhpc/core/status.py
index a3ee96f1..4c3ed675 100644
--- a/lhpc/core/status.py
+++ b/lhpc/core/status.py
@@ -75,9 +75,10 @@ class StatusProber:
         # artifact. Passed in by the service (which owns the receipt read) so status stays a
         # pure, bounded assessor.
         self._binary_cover = binary_cover or {}
-        # ONE /proc/net/tcp read per whole-snapshot assessment (set by `assess_stacks`); None
-        # outside it, so a standalone `assess_component` still reads fresh.
+        # ONE /proc/net/tcp read and ONE /proc cmdline table per whole-snapshot assessment (set by
+        # `assess_stacks`); None outside it, so a standalone `assess_component` still reads fresh.
         self._listeners = None
+        self._cmdlines = None
         # ONE git probe per identical (checkout, pin) for the life of this prober — several
         # components share a checkout (loraham-kiss-tnc and loraham-kiss-serial both come from src/loraham-kiss-tnc), and the
         # probe's answer depends on nothing but the path and the pin. A prober lives for one
@@ -121,6 +122,10 @@ class StatusProber:
             self._listeners = self._system.procfs.tcp_listeners()   # once for every TCP endpoint
         except Exception:
             self._listeners = None
+        try:
+            self._cmdlines = self._system.procfs.cmdlines()         # once for every process match
+        except Exception:
+            self._cmdlines = None
         try:
             for stack in stacks:
                 ss = StackStatus(stack=stack)
@@ -131,7 +136,7 @@ class StatusProber:
                     all_components.append(comp)
                 snap.stacks.append(ss)
         finally:
-            self._listeners = None
+            self._listeners = self._cmdlines = None
 
         # Attach runtime dependency observations now that all are computed.
         for stack in stacks:
@@ -180,7 +185,7 @@ class StatusProber:
 
         proc_matched = False
         if comp.process is not None:
-            pm = probe_process(self._system, comp.process)
+            pm = probe_process(self._system, comp.process, self._cmdlines)
             proc_matched = pm.matched
             status.pids = pm.pids
             status.evidence.update({f"process.{k}": v for k, v in pm.evidence.items()})
diff --git a/tests/core/test_bounded_runner.py b/tests/core/test_bounded_runner.py
index e6271430..9a4af35a 100644
--- a/tests/core/test_bounded_runner.py
+++ b/tests/core/test_bounded_runner.py
@@ -328,3 +328,113 @@ def test_streaming_run_returns_and_flags_unverified_output(tmp_path):
         assert log.read_text().split()[1:] == ["hi"]
     finally:
         _kill(log.read_text())
+
+
+def test_a_short_command_is_reaped_without_a_sleep_loop(monkeypatch):
+    # A timed `Popen.wait` polls in sleeps of 1 to 50 ms, so every git/systemctl probe was noticed
+    # late (on a Zero, 141 sleeps per status page). The runner blocks on the child's exit instead.
+    import subprocess
+
+    def no_sleep(_s):
+        raise AssertionError("the runner slept while waiting for the child")
+    monkeypatch.setattr(subprocess.time, "sleep", no_sleep)
+    r = RealCommandRunner().run(["python3", "-c", "print('ok')"], timeout=10)
+    assert (r.returncode, r.stdout, r.timed_out) == (0, "ok\n", False)
+
+
+def test_without_pidfd_the_runner_falls_back_to_the_timed_wait(monkeypatch):
+    import os
+
+    def unsupported(_pid, *_a):
+        raise OSError(38, "Function not implemented")
+    monkeypatch.setattr(os, "pidfd_open", unsupported)
+    r = RealCommandRunner().run(["python3", "-c", "import sys; sys.exit(3)"], timeout=10)
+    assert (r.returncode, r.timed_out) == (3, False)
+    t0 = time.time()
+    r = RealCommandRunner().run(["sleep", "30"], timeout=0.4)
+    assert r.timed_out and r.returncode == 124 and time.time() - t0 < 5.0
+
+
+def test_a_child_killed_by_a_signal_reports_the_signal():
+    r = RealCommandRunner().run(["sh", "-c", "kill -TERM $$"], timeout=10)
+    assert (r.returncode, r.timed_out) == (-15, False)            # as Popen reports it
+
+
+def test_a_failing_poll_falls_back_to_the_timed_wait_on_the_same_deadline(monkeypatch):
+    import select
+
+    real, failed = select.poll, []
+
+    class ExitPollFails:
+        # Only the exit wait registers POLLIN alone; the pipe drains (POLLIN|POLLHUP|POLLERR) run
+        # on the real poll, so the output is still captured.
+        def __init__(self):
+            self._p, self._exit_wait = real(), False
+
+        def register(self, fd, mask):
+            self._exit_wait = mask == select.POLLIN
+            self._p.register(fd, mask)
+
+        def poll(self, *a):
+            if self._exit_wait:
+                failed.append(1)
+                raise OSError(5, "Input/output error")
+            return self._p.poll(*a)
+    monkeypatch.setattr(select, "poll", ExitPollFails)
+    r = RealCommandRunner().run(["python3", "-c", "print('ok')"], timeout=10)
+    assert failed == [1]                                          # the fallback branch ran
+    assert (r.returncode, r.stdout, r.timed_out) == (0, "ok\n", False)
+
+
+def test_the_exit_wait_and_its_fallback_share_one_deadline(monkeypatch):
+    # Whatever the exit wait has used of the timeout is not given again: the poll waits for what is
+    # left of the deadline when it starts, and when it fails part-way the timed wait gets only the rest.
+    import select
+    import subprocess
+    import time as _time
+    now = [1000.0]
+    monkeypatch.setattr(_time, "monotonic", lambda: now[0])
+    real_poll, polls, waits = select.poll, [], []
+
+    class ExitPollFailsLate:
+        def __init__(self):
+            self._p, self._exit_wait = real_poll(), False
+
+        def register(self, fd, mask):
+            self._exit_wait = mask == select.POLLIN
+            self._p.register(fd, mask)
+
+        def poll(self, *a):
+            if self._exit_wait:
+                polls.append(a[0])
+                now[0] += 4.0                                     # 4 s of the budget go by
+                raise OSError(5, "Input/output error")
+            return self._p.poll(*a)
+    probe = subprocess.Popen(["true"])                            # the real class (the suite may wrap
+    probe.wait()                                                  # the `Popen` name in a guard)
+    popen, real_wait = type(probe), type(probe).wait
+
+    def wait(self, timeout=None):
+        waits.append(timeout)
+        return real_wait(self, timeout)
+    import os
+    real_open = os.pidfd_open
+
+    def slow_open(pid, *a):
+        now[0] += 1.0                                             # 1 s goes by before the poll
+        return real_open(pid, *a)
+    monkeypatch.setattr(os, "pidfd_open", slow_open)
+    monkeypatch.setattr(select, "poll", ExitPollFailsLate)
+    monkeypatch.setattr(popen, "wait", wait)
+    r = RealCommandRunner().run(["python3", "-c", "print('ok')"], timeout=10)
+    assert (r.returncode, r.stdout) == (0, "ok\n")
+    assert polls == [9000.0]                                      # what is left when the poll starts
+    assert waits[0] == 5.0                                        # the fallback: what is left after it
+
+
+def test_a_command_gets_the_runners_environment_plus_its_own_variables():
+    # `env` adds to the runner's fixed environment; it never brings in the controller's own.
+    from lhpc.core.probes.backends import _FIXED_ENV
+    r = RealCommandRunner().run(["env"], timeout=10, env={"GIT_OPTIONAL_LOCKS": "0"})
+    got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
+    assert got == {**_FIXED_ENV, "GIT_OPTIONAL_LOCKS": "0"}
diff --git a/tests/core/test_snapshot_memo.py b/tests/core/test_snapshot_memo.py
index 0c6e9b7a..edf14391 100644
--- a/tests/core/test_snapshot_memo.py
+++ b/tests/core/test_snapshot_memo.py
@@ -205,7 +205,7 @@ def _git_src(src, sha):
     return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
             ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                 CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
-            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}
+            ("git", "-C", a, "describe", "--tags", "--always"): CR(0, "v111a\n", "")}
 
 
 def test_components_sharing_a_checkout_and_pin_are_probed_once_per_snapshot(tmp_path):
@@ -882,3 +882,31 @@ def test_the_write_trace_catches_a_mutating_tty_ioctl(tmp_path, receiver, monkey
             os.close(fd)
     writes, err = _trace_writes(set_mode, root, receiver, fake, monkeypatch)
     assert err is None and writes == expected
+
+
+def test_a_snapshot_reads_the_process_table_once(tmp_path):
+    # Every process-matched component read ALL of /proc again (22 full scans per page on a Zero); a
+    # snapshot now takes one table, as it already does for the TCP listeners, and matches each
+    # component against it.
+    from lhpc.core.status import StatusProber
+    fake = FakeSystem()
+    paths = Paths(runtime_root=tmp_path)
+    svc = ControllerService(system=fake.system, paths=paths)
+    matched = [c for s in svc.stacks() for c in s.components if c.process is not None]
+    assert len(matched) >= 2                                   # precondition: several scans before
+    spec = matched[0].process
+    fake.cmdlines_data = {4242: [f"/usr/bin/{spec.exec_name}", *spec.all_args, *spec.any_args[:1]]}
+    reads = []
+    orig = fake.cmdlines
+    fake.cmdlines = lambda: (reads.append(1), orig())[1]
+    snap = StatusProber(fake.system, paths).assess_stacks(svc.stacks())
+    assert len(reads) == 1
+    pids = {c.id: st.pids for ss in snap.stacks for c in ss.stack.components
+            for st in [ss.components[c.id]] if c.process is not None}
+    assert pids[matched[0].id] == [4242]
+    assert all(p == [] for cid, p in pids.items()
+               if cid != matched[0].id and not _same_spec(svc, cid, spec))
+
+
+def _same_spec(svc, cid, spec):
+    return next(c for s in svc.stacks() for c in s.components if c.id == cid).process == spec
diff --git a/tests/install/test_source.py b/tests/install/test_source.py
index 48202422..4eee26bc 100644
--- a/tests/install/test_source.py
+++ b/tests/install/test_source.py
@@ -84,7 +84,7 @@ def _git_src(src, sha):
     return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),     # the source check's own read
             ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                 CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
-            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}
+            ("git", "-C", a, "describe", "--tags", "--always"): CR(0, "v111a\n", "")}
 
 
 def test_uninstalled_source_is_unknown_and_makes_no_network_call(tmp_path):
@@ -5361,3 +5361,69 @@ def test_leftover_prev_refusal_names_the_folder_and_the_command(tmp_path):
     assert action.status == "failed"
     assert "src/.repo.prev" in action.detail and "lhpc update c --yes" in action.detail
     assert (rt / "src" / ".repo.prev").is_dir()            # the refusal itself removes nothing
+
+
+@pytest.mark.parametrize("edit, state", [("touch", SourceState.MATCH), ("change", SourceState.DIRTY)])
+def test_a_source_probe_writes_nothing_into_the_checkout(tmp_path, make_repo, edit, state):
+    """A status read is read-only: probing a checkout whose index stat cache is stale changes
+    nothing under `.git` (no `index.lock`, no index rewrite), and it still tells a touched file
+    from a changed one."""
+    from lhpc.core.probes.source import probe_source
+    repo = tmp_path / "src" / "app"
+    head = make_repo(repo)
+    f = repo / "file.txt"
+    if edit == "change":
+        f.write_text("changed\n")
+    st = f.stat()
+    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
+    def tree():                     # every path under .git: kind, size, mtime, bytes
+        out = {}
+        for d, _dirs, files in os.walk(repo / ".git"):
+            out[d] = os.stat(d).st_mtime_ns
+            for n in files:
+                p = os.path.join(d, n)
+                out[p] = (os.stat(p).st_mtime_ns, Path(p).read_bytes())
+        return out
+    before = tree()
+    probe = probe_source(RealSystem(), SourceSpec(path="src/app", pin_commit=head), str(repo))
+    assert (probe.state, probe.head) == (state, head)
+    assert probe.version == head[:7] + ("-dirty" if state is SourceState.DIRTY else "")
+    assert tree() == before
+
+
+@pytest.mark.parametrize("edit", [
+    "clean", "modified", "staged-only", "staged-and-modified", "staged-new", "deleted",
+    "deleted-from-index", "mode", "untracked", "intent-to-add"])
+def test_the_probed_version_is_what_git_describe_dirty_says(tmp_path, make_repo, git, edit):
+    """The probe no longer runs `describe --dirty` (it rewrites the index even with optional locks
+    off); its `-dirty` suffix comes from the status answer. Equal output: for every kind of tracked
+    change the version is exactly what `git describe --tags --always --dirty` prints for a copy of
+    the same checkout."""
+    from lhpc.core.probes.source import probe_source
+    repo = tmp_path / "src" / "app"
+    head = make_repo(repo, {"f": "a\n", "g": "b\n"})
+    git(repo, "tag", "v1")
+    f = repo / "f"
+    if edit in ("modified", "staged-only", "staged-and-modified"):
+        f.write_text("x\n")
+    if edit in ("staged-only", "staged-and-modified"):
+        git(repo, "add", "f")
+    if edit == "staged-only":
+        f.write_text("a\n")                       # the worktree equals HEAD again
+    if edit in ("staged-new", "untracked", "intent-to-add"):
+        (repo / "n").write_text("n\n")
+    if edit == "staged-new":
+        git(repo, "add", "n")
+    if edit == "intent-to-add":
+        git(repo, "add", "-N", "n")
+    if edit == "deleted":
+        f.unlink()
+    if edit == "deleted-from-index":
+        git(repo, "rm", "-q", "--cached", "f")
+    if edit == "mode":
+        f.chmod(0o755)
+    ref = tmp_path / "ref"
+    shutil.copytree(repo, ref, symlinks=True)
+    want = git(ref, "describe", "--tags", "--always", "--dirty")
+    probe = probe_source(RealSystem(), SourceSpec(path="src/app", pin_commit=head), str(repo))
+    assert probe.version == want
diff --git a/tests/web/test_stacks_page_source_state.py b/tests/web/test_stacks_page_source_state.py
index 29c7cd82..1c93cd65 100644
--- a/tests/web/test_stacks_page_source_state.py
+++ b/tests/web/test_stacks_page_source_state.py
@@ -27,7 +27,7 @@ def _git_src(src, sha):
     return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
             ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                 CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
-            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}
+            ("git", "-C", a, "describe", "--tags", "--always"): CR(0, "v111a\n", "")}
 
 
 def _repo(tmp_path, rel):
diff --git a/tests/web/test_update_component_link.py b/tests/web/test_update_component_link.py
index a957c730..9c75d39e 100644
--- a/tests/web/test_update_component_link.py
+++ b/tests/web/test_update_component_link.py
@@ -88,7 +88,7 @@ def _git_src(src, sha):
     return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
             ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                 CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
-            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}
+            ("git", "-C", a, "describe", "--tags", "--always"): CR(0, "v111a\n", "")}
 
 
 def _repo(tmp_path, rel):
```
