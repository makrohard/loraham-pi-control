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
