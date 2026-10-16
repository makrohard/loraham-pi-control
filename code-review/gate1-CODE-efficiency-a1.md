# Gate 1: efficiency-a1 (the console reuses a source's git status for up to 30 s), v5

**v5 in one line.** v4 (`358ace8e`, auditor 7b round 1 RED, one finding on the hit path) plus the decided fix: a read returns a cached entry only if no clear came between its fetch and its return; one deterministic test. Delta tables on top of the report: v5, v4, v3, v2. Base `origin/dev` = `a3733021`.

**Request.** Judge the code commit below against the plan's A1 (quoted; the form the maintainer decided). That means:
correctness; whether a stale answer can be served beyond the stated window (30 s from the probe, or an unchanged
key); whether a recheck under the operation locks can ever use the cache; whether every mutation in the console
clears it; whether each red-before test can fail for the defect named; the docs and CHANGELOG lines; and the
report's claims and stated deviations against the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's A1 (plan v4 §3, verbatim)

## 3. A1 — a source-probe cache across requests (decided form)
**What.** The web process keeps, per (checkout path, pin), the last git probe of that checkout with a KEY and the
monotonic time it was taken. A page read reuses it while the key is unchanged and it is at most 30 s old; else it
probes as today and stores the new result.
**The key** (stat of each, no-follow; any missing or unreadable item → no key → no cache for that checkout):
`.git/HEAD` bytes; `(st_ino, st_size, st_mtime_ns)` of `.git/index`, `.git/packed-refs` and the ref file HEAD names;
and `st_mtime_ns` of every directory that holds a tracked file, listed by `git ls-files` (with
`GIT_OPTIONAL_LOCKS=0`) when the entry is stored. A commit, checkout, reset, stage, update, tag or adopt changes the
first part; a file created, deleted or renamed by hand changes its directory's mtime. Storing an entry costs one
more git run (`ls-files`); a hit costs only the stats.
**Where it applies.** Only `build_snapshot()` without `fresh` in the long-lived console process: the cache lives on
the `ControllerService` instance (one per web app, shared by its threads under a lock). The CLI is one process per
command and starts empty, so `lhpc status` is unchanged. `fresh=True` (every recheck under the operation locks)
never reads or fills it. Only MATCH / DIRTY / DIFFERS results are stored, never UNKNOWN (a timeout or a git error is
retried next time).
**Invalidation.** Every public mutating service entry is already `@invalidates_snapshot` (`snapshot_memo.py`, enforced
by `test_snapshot_memo.py`, which fails on an undecorated writer); the decorator also clears this cache on entry and
exit, so every LHPC write path run INSIDE the console process (update, clean, uninstall, binary install, adopt,
the source transactions) is seen at once; no entry on the neutral lists writes a source (checked by hand against
`SNAPSHOT_NEUTRAL` / `SNAPSHOT_NEUTRAL_WRITERS`). A detached web job (install, build, test) runs in a CHILD process:
its POST clears the cache when it starts the job, and what the job later changes in a checkout is seen when the key
changes, else within 30 s; the same holds for the CLI. A commit, checkout, reset, stage or update changes the key and
is seen at once from any process. `invalidate_snapshot()` at each request start does NOT clear it (that is what
makes it cross-request).
**Residual window (the docs row, `docs/architecture.md`, verbatim):** "The console reuses a source's git status for
up to 30 s while the checkout's git metadata and the directories of its tracked files are unchanged: a hand edit of
an existing file inside an unchanged directory, or a build (from the console or the CLI) that patches files in
place, a change inside a submodule, or any other change made outside LHPC that leaves the git metadata and those
directories unchanged, shows within 30 s. The 30 s count from when the probe ran, not from its last reuse."
**Code.** A plain module `lhpc/core/source_cache.py` (key, a small locked dict, max age; no service growth beyond
one line to hold the instance and the decorator's clear call in `snapshot_memo.py`); `StatusProber(cache=…)` consults
it for the git part only, after the filesystem checks (missing / not a repo / symlink / binary never cached).
**Tests** (`tests/core/`, real git in tmp for the key, FakeSystem for counts): (a) two page reads within 30 s with an
unchanged checkout run git once (red: twice); (b) each key part changed → the next read probes again (commit, stage,
checkout, packed-refs rewrite, a new file in a tracked directory, a deleted file, a renamed file); (c) older than 30 s
→ probes again (injected clock); (d) a decorated mutation clears it; (e) `fresh=True` neither reads nor fills;
(f) UNKNOWN is not stored; (g) a symlinked `.git` or a missing ref file → no key, probes every time; (h) the CLI path
(a new service) starts empty. **Gain** (estimate from the box-window-1 profile, `probe_source` 0.844 s for 16
sources): up to ≈ 0.8 s per page on a cache hit; the real figure after QW1/QW2 comes from box window 2.

## The commit message (`git log a3733021..62f06a6b`, full)

### 62f06a6b efficiency: the console reuses a source's git status for up to 30 s

efficiency: the console reuses a source's git status for up to 30 s

Every status page ran git status and git describe for each installed
source (the bulk of a page's time on a Pi Zero 2 W). The console now
keeps the last answer per (checkout, pin) with a key: .git/HEAD's
bytes; inode, size and mtime of .git/index, .git/packed-refs and the
ref file HEAD names; and the mtime of every directory holding a tracked
file (git ls-files when the answer is stored). It reuses the answer
while the key is unchanged and the answer is at most 30 s old, counted
from the probe (the maintainer's decision, 2026-10-16). A hand edit of an
existing file inside an unchanged directory, a build that patches files
in place, a change inside a submodule, or another change outside LHPC
that leaves the key unchanged shows within 30 s (docs/architecture.md).

Never cached: a checkout whose .git is not a plain directory, and any
answer but MATCH / DIRTY / DIFFERS. A recheck under the operation locks
(build_snapshot(fresh=True)) neither reads nor fills it; every
@invalidates_snapshot entry clears it on entry and exit, and a probe that
was already running when it was cleared does not store its answer (a
generation counter: clear() bumps it, a miss records it, put() stores
only while it is unchanged), and a read that found an entry returns it
only if no clear came between its fetch and its return (the same
generation, the same entry). One per process
(source_cache.SHARED): the console keeps it across requests and its
watchdog's services; a CLI command starts with an empty one. Every
mutating service call in the console clears it; a change from another
process shows through the key or within 30 s.

services.py grows by one line (budget 2959 -> 2960): the prober call in
build_snapshot takes the cache argument on a line of its own rather than
a packed one, so the growth is visible in the ratchet table.

Red before: test_two_page_reads_of_an_unchanged_checkout_run_git_once
(through the service, a real checkout: git status ran twice for two
requests, now once). The rest of tests/install/test_source_cache.py
(structural on the parent: the module is new): a second read within 30 s runs no git; after 30 s, and after each key
change (commit, stage, checkout, packed-refs, a new / deleted / renamed
file, a new top-level file), it probes again; the hand-edit window; a
failed probe is not kept; no cache without a plain .git; the decorator
clears it; fresh=True gets no cache, a page read gets SHARED; a probe in
flight during a mutation is not kept; a hit read across a mutation is
not served.


## The code report (verbatim; committed as `code-review/code-report-efficiency-a1.md`)

# Code report: efficiency-a1 (the console reuses a source's git status for up to 30 s)

Branch `cons/efficiency-a1-r1` on `origin/dev` = `a3733021` (the efficiency stack P1–A6 and QW8 have landed). Plan:
`PLAN-efficiency` v4 §3 (gate 1 GREEN, round 4; the form decided by the maintainer on 2026-10-16). One commit.

## v5: the delta from v4 (gate 1, auditor 7b round 1: one finding)

| point | v4 | v5 |
|---|---|---|
| F1 (the hit path) | `get()` copied the entry under the lock, ran the age and directory checks without it and returned the entry without looking at the generation again, so a clear plus an in-place edit between the fetch and the return still served the old MATCH | `get()` takes the generation together with the entry; after the checks it re-takes the lock and returns the hit only if the generation and the entry are unchanged, else None (`source_cache.py:104-114`). Test: `test_a_hit_read_across_a_mutation_is_not_served` (the clear and the invisible edit run inside the directory check, between the fetch and the return: the read runs git and reads DIRTY); red on v4: MATCH served |

Code commit `ee3768dc` → `62f06a6b` (this change only, and rebased on `a3733021`); `source_cache.py` 143 → 148 lines.

## v4: the delta from v3 (found by the author when rebasing onto dev 402bfc46)

| point | v3 | v4 |
|---|---|---|
| docs pointers | N2's own line in `services.py` (v2) moved every later line by one; six `docs/architecture.md` pointers kept the old numbers (`_lifecycle_lock_keys` 2523, `_admit` 2588, `_admission_guard` 2638, `_acquire_key` 2651, `_lifecycle_guard` 2715, `invalidate_snapshot` 882) | each + 1 (2524, 2589, 2639, 2652, 2716, 883); all 97 `file:line` + name pointers in `docs/` resolve (checked by a script that reads each named line) |

Code commit `f4871ddd` → `ee3768dc` (that docs row only, and rebased on `402bfc46`, where QW8 has landed).

## v3: the delta from v2 (gate 1 round 10, the fix decided by the handler)

| point | v2 | v3 |
|---|---|---|
| F1 (in-flight probe) | a page probe that started before a console mutation could `put()` its answer after the mutation's clear on entry and exit; with an in-place edit the cheap key is unchanged, so its pre-mutation MATCH was served for up to 30 s after the console action, against "every mutating service call in the console clears it" | `SourceCache` keeps a generation: `clear()` bumps it (`source_cache.py:95`), a miss reads it before the probe (`:133`), and `put()` stores only while it is unchanged (`:111`). The docs sentence holds as written. Test: `test_a_probe_in_flight_during_a_mutation_is_not_kept` (the probe runs, then a clear and an in-place edit, then it returns: nothing stored, the next read runs git and reads DIRTY); red on v2 (`20902afa`): `len(cache) == 1` |

Code commit `20902afa` → `f4871ddd` (this change only); `source_cache.py` 131 → 143 lines. Red-before and green
below are on v3.

## v2: the delta from v1 (second read; decisions by the handler)

| point | v1 | v2 |
|---|---|---|
| P1 (docs sentence) | "Every LHPC mutation clears it, and a recheck under the operation locks never uses it" — stronger than the code: a mutation in ANOTHER process (the CLI, a detached job) does not clear the console's cache | "Every mutating service call in the console clears it; a change from another process shows through the key or within 30 s; a recheck under the operation locks never uses it" |
| N1 (several snapshots in one CLI run) | not stated | Checked: `grep -n "while True\|for _ in range" lhpc/adapters/cli/main.py` finds one loop, `lhpc rflog --follow` (`main.py:507`), which re-reads log lines and builds no snapshot; no other CLI command loops over status. Inside a decorated operation, a later snapshot in the same operation can reuse a probe taken earlier in it; a source the operation changes moves its HEAD or index, so the key differs and git runs. Nothing to fix. Found on the way, NOT changed here: `rflog_running` (`service_lifecycle_ops.py:5893`), the RF-log page's running badge, still builds a full snapshot with git per poll, unlike `log_running` after QW7 — a candidate for a later quick win. |
| N2 (the packed line) | the cache argument packed onto the existing ~120-column prober line to keep `services.py` at 2959 | its own line; `services.py` 2959 → 2960 and its budget raised to 2960 in `tests/repo/test_module_budgets.py` with `# efficiency-a1: …` on that line; reason in the commit message |
| N3 (gain) | estimate | accepted: measured in box window 2 |

Code commit `8551e961` → `20902afa` (the three changes above + rebased on `771d1a37`); red-before and green below are
on v2.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| 62f06a6b | efficiency: the console reuses a source's git status for up to 30 s | core/source_cache.py (new), core/status.py, core/services.py, core/snapshot_memo.py, tests/install/test_source_cache.py (new), tests/conftest.py, tests/repo/test_module_budgets.py, docs/architecture.md, CHANGELOG.md | install, real git in tmp, git runs counted at the runner, the cache's clock injected: `test_two_page_reads_of_an_unchanged_checkout_run_git_once` (through `ControllerService`: two requests → `git status` runs == 1); `test_a_second_read_within_30_s_runs_no_git` (29 s later: same state, head, version; 1 run); `test_after_30_s_it_probes_again` (30.5 s: 2 runs); `test_a_change_to_the_key_probes_again[commit, stage, checkout, packed-refs, new file in a tracked dir, deleted file, renamed file, new file at the top]` (2 runs); `test_a_hand_edit_inside_an_unchanged_directory_shows_within_30_s` (MATCH at 10 s, DIRTY at 31 s); `test_clear_drops_every_entry`; `test_a_failed_probe_is_not_kept` (UNKNOWN, then MATCH from git); `test_a_checkout_without_a_plain_git_directory_is_never_cached[symlinked .git, git file, no checkout]` (`meta_key` None, nothing stored, git on every read or none); `test_a_mutating_service_entry_clears_the_cache_on_entry_and_exit`; `test_a_recheck_never_uses_the_cache_and_a_page_read_does` (`fresh=True` → every probe gets `cache=None`; a page read → `SHARED`); `test_a_probe_in_flight_during_a_mutation_is_not_kept` (not stored; the next read runs git: DIRTY); `test_a_hit_read_across_a_mutation_is_not_served` (a clear between fetch and return: git runs, DIRTY) | yes, on 771d1a37: the page-reads test `assert 2 == 1` (behavioural); the rest of the file structurally (`cannot import name 'source_cache'`: the module is new) |

**Green** (on 673ad4ea, v5's code before the rebase onto a3733021, a detached worktree): `python -m pytest -q -p
no:cacheprovider -n 12 --dist loadfile tests/core tests/install tests/repo tests/web tests/stacks tests/host tests/cli
tests/golden` → `7782 passed, 1 skipped in 108.69s`; on 62f06a6b (after the rebase) `tests/install tests/repo tests/core`
→ `4549 passed, 1 skipped`. `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` →
`All checks passed!`.

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  origin/dev..62f06a6b
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 29 lines   OK
docs         1 files, +12 lines; fact "mutating service call in the console clears it" -> docs/architecture.md   OK
tests        12 new tests; layers tests/install=install tests/repo=repo; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      services.py 2959->2960 lines (budget 2960), 28->28 public (budget 28); family net +1 lines, +0 public   WARN
VERDICT      PASS  (hard 0, warn 1)
```

The one WARN is the planned growth (N2): `services.py` +1 line, its budget raised on the same line with the item id.

## Simplicity guardrails

- One plain module (`source_cache.py`, 148 lines with its docstring): a key function, a locked dict with a
  generation counter, one wrapper
  (`probe_cached`) around the unchanged `probe_source`, one process-wide instance. No thread, no timer, no file.
- Service family: `services.py` 2959 → 2960 (the cache argument on its own line; one name on an existing
  import), its budget raised to 2960 with the item id (N2); `snapshot_memo.py` (not in the family) gains the two `clear()` calls and its import; `status.py` passes the
  cache through.
- Lines (`wc -l`, 771d1a37 → f4871ddd): status.py 490 → 492; services.py 2959 → 2960; snapshot_memo.py 301 → 306;
  tests/conftest.py 670 → 680 (one autouse fixture: every test starts with an empty cache, as a CLI command does).
- `docs/architecture.md`: the residual-window sentence of plan v4 §3, verbatim, under "Probes and status" (which
  said status is reconstructed on each call), plus where the cache lives and what never uses it; the three
  `snapshot_memo.py` line pointers in the invariant table follow the five added lines (24 → 26, 43 → 48, 286 → 291).

## Deviations from the plan

- **One cache per process, not per `ControllerService` instance.** The plan put the cache on the service instance.
  A module instance needs no service attribute, and the console's watchdog builds a new service per pass anyway; a module
  instance (`source_cache.SHARED`) serves all services of the process, and every decorated mutation of any of them
  clears it. The CLI is one process per command, so it starts empty either way. The `fresh=True` rule and the
  invalidation are as planned.
- **`git ls-files` runs before the probe** (the plan: "when the entry is stored"): the directory list and its mtimes
  are taken before `git status`, like the metadata key, so a change while the probe runs makes the next read probe
  again rather than being masked.
- **Missing `packed-refs` or ref file** is part of the key (recorded as absent), not "no key": a fresh clone has no
  `packed-refs`; only a `.git` that is not a plain directory, or an unreadable HEAD, means no cache.
- **`probe_cached(..., probe=probe_source)`** takes the probe function from `status.py`, so the existing seam
  `status.probe_source` (patched by `tests/stacks/test_gps_nmea_monitor.py`) keeps working unchanged.

## The 6-point block

1. **Contracts.**
   - `meta_key(abs_path)` (`source_cache.py:44`): `(HEAD bytes, stamp(index), stamp(packed-refs), stamp(HEAD's ref
     file))`, a stamp being `(ino, size, mtime_ns)` from `lstat` or None when absent; None when `.git` is not a plain
     directory or HEAD cannot be read (`O_NOFOLLOW`, ≤ 4 KiB).
   - `_tracked_dirs` (`:65`): `git -C <path> ls-files -z` with `GIT_OPTIONAL_LOCKS=0`; the directories of the listed
     files plus the root; None on a failure (then nothing is stored).
   - `SourceCache.get` (`:104`) returns the stored probe only when the metadata key is equal, the entry is at most
     `MAX_AGE_S` (30 s, `:30`) old by its own clock, every listed directory's mtime is equal, and — re-checked under
     the lock after those checks — neither the generation nor the entry changed since it was fetched; `clear`
     (`:95`) empties it and bumps the generation; `put` (`:116`) stores only when the generation it is given is still
     the current one.
   - `probe_cached(cache, system, spec, abs_path, git=True, probe=probe_source)` (`:124`): no cache or `git=False`
     → `probe(...)` as before; no key → `probe(...)`; a hit → the stored probe; else generation, key and directories
     first (`:138`), then `probe(...)`, stored only for MATCH / DIRTY / DIFFERS and only if no clear came between.
   - `build_snapshot` (`services.py:637-638`) passes `cache=None if fresh else source_cache.SHARED`;
     `invalidates_snapshot` (`snapshot_memo.py:26`) clears `SHARED` on entry and exit (`:32`, `:37`).
2. **Invariants and their tests.** Reuse within 30 s and an unchanged key: the second-read and page-reads tests.
   Expiry from the probe, never from a reuse: the 30.5 s test and the hand-edit test (two reuses, then DIRTY at
   31 s). Every key part works: the 8 change cases. Failures are never kept: the failed-probe test. No cache without
   a plain `.git`: the three shapes. A mutation clears it: the decorator test; a probe in flight across it is not kept: the
   in-flight test; a hit read across it is not served: the hit-read test. A recheck never uses it: the `fresh`
   test. The stated residual window: the hand-edit test.
3. **Failure classes.**
   - Fakes with the real signature: `RealSystem` with a counting wrapper around its runner; the decorator test uses a
     plain object with `invalidate_snapshot`.
   - EIO / EACCES: an unreadable `.git`/HEAD → `meta_key` None → git as before; a vanished directory → its mtime
     reads None, so the key changes and git runs.
   - Ctrl-C: no new wait or lock beyond a dict lock held for a lookup.
   - CLI / web / job / boot agree: the same `build_snapshot`; the CLI starts with an empty cache; detached jobs run
     in child processes whose changes show through the key or within 30 s (the docs row says so).
   - Stacked conflicts: the cache stores per (path, pin), like the snapshot's own per-probe memo; a stored probe is
     read only by scalar field (`status.py:169-171`), never mutated.
4. **Test rules.** Real git in temporary directories; an injected clock instead of sleeping; behaviour through
   `probe_cached` and through the service; no source reading.
5. **Directories and ruff.** As under Green.
6. **Adversarial self-review.** Found and fixed: (a) the `services.py` budget (+1 line) — in v1 the argument was packed onto the
   existing line; v2 gives it its own line and raises the budget openly (N2); (b) `tests/stacks/test_gps_nmea_monitor.py` patches `status.probe_source`, which the first version
   no longer called — the probe function is now passed from `status.py`; (c) the first red was structural only — the
   page-reads test adds a behavioural red; (d) the docs pointers into `snapshot_memo.py` moved with the new lines.
   Checked: a cached probe is shared between requests but never mutated by its readers.

## Numbers, measured

| figure | command | output |
|---|---|---|
| git status runs for two page reads before | the page-reads test on 771d1a37 | `assert 2 == 1` |
| an in-flight probe kept across a clear, v2 | the in-flight test on 20902afa | `len(cache) == 1` |
| a hit served across a clear, v4 | the hit-read test on ee3768dc's code | MATCH served (`assert MATCH is DIRTY`) |
| family line counts | `tools/check_houserules.py` | the modules line |
| the Zero figure (`probe_source` 0.844 s of a page, 16 sources) | reported by `fieldtest-e293/50-profile-endpoints.txt` (box window 1); the after-figure comes from box window 2 | — |

## The diff (`git diff a3733021 62f06a6b`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ad64476d..4b833b1d 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -8,6 +8,8 @@ Changed
   pages fetch new lines every 5 s (was 2 s).
 - The Apps page loads faster: a closed stack's settings are prepared only when the stack is opened; the page itself
   is unchanged.
+- The console reuses a source's git status for up to 30 s while its git metadata and the directories of its
+  tracked files are unchanged, so pages load faster; a hand edit of an existing file can take up to 30 s to show.
 
 Fixed
 
diff --git a/docs/architecture.md b/docs/architecture.md
index 5f734c88..4f0ea5cd 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -140,6 +140,13 @@ Status is reconstructed on each call, never from a stale PID file: process ident
 (`/proc/<pid>/cmdline`), TCP listeners, Unix sockets + a bounded daemon `GET STATUS`, systemd unit
 state, and local git source/pin state. Every probe is bounded and turns errors into evidence. A
 missing runtime root reports `not-installed`, not an error.
+The console reuses a source's git status for up to 30 s while the checkout's git metadata and the
+directories of its tracked files are unchanged: a hand edit of an existing file inside an unchanged
+directory, or a build (from the console or the CLI) that patches files in place, a change inside a
+submodule, or any other change made outside LHPC that leaves the git metadata and those directories
+unchanged, shows within 30 s. The 30 s count from when the probe ran, not from its last reuse. Every
+mutating service call in the console clears it; a change from another process shows through the key
+or within 30 s; a recheck under the operation locks never uses it (`core/source_cache.py`).
 `RunState` ∈ {running, degraded, stopped, failed, unknown, not-applicable, not-installed}.
 
 ## Radios, bands and resource claims
@@ -327,15 +334,15 @@ the runtime root unless absolute.
 
 | area | invariant | owner | locks | durable state · recovery | proving tests |
 |---|---|---|---|---|---|
-| admission / locks | task admission: no new task while an uninstall, a self-update or a reboot/shutdown is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py:2588` `_admit`; `lhpc/core/service_selfupdate.py:1280` `_task_admission_blocked` | `controller-task-admission` | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
-| admission / locks | named non-blocking locks; a contended operation refuses naming the holder, after waiting up to 5 s for a holder in the same controller process ([Locking](#safety-model)) | `lhpc/core/reslock.py:106` `operation_lock`; `lhpc/core/services.py:2651` `_acquire_key` | one flock per key under `state/locks/` | owner record beside the lock; the kernel drops a dead holder's flock | `tests/core/test_reslock.py::test_second_acquire_is_blocked_and_names_holder`, `::test_dead_holder_lock_is_free`, `::test_concurrent_lifecycle_op_is_blocked` |
-| admission / locks | one lock order for every operation ([Locking](#safety-model)) | `lhpc/core/services.py:2638` `_admission_guard`, `:219` `_config_stable`, `:361` `_source_operation_guard`, `:2715` `_lifecycle_guard` | the order is stated once, in [Locking](#safety-model) | — | `tests/core/test_task_admission.py::test_admission_acquired_before_config_stable`, `::test_second_thread_start_and_config_do_not_invert`; `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` |
+| admission / locks | task admission: no new task while an uninstall, a self-update or a reboot/shutdown is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py:2589` `_admit`; `lhpc/core/service_selfupdate.py:1280` `_task_admission_blocked` | `controller-task-admission` | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
+| admission / locks | named non-blocking locks; a contended operation refuses naming the holder, after waiting up to 5 s for a holder in the same controller process ([Locking](#safety-model)) | `lhpc/core/reslock.py:106` `operation_lock`; `lhpc/core/services.py:2652` `_acquire_key` | one flock per key under `state/locks/` | owner record beside the lock; the kernel drops a dead holder's flock | `tests/core/test_reslock.py::test_second_acquire_is_blocked_and_names_holder`, `::test_dead_holder_lock_is_free`, `::test_concurrent_lifecycle_op_is_blocked` |
+| admission / locks | one lock order for every operation ([Locking](#safety-model)) | `lhpc/core/services.py:2639` `_admission_guard`, `:219` `_config_stable`, `:361` `_source_operation_guard`, `:2716` `_lifecycle_guard` | the order is stated once, in [Locking](#safety-model) | — | `tests/core/test_task_admission.py::test_admission_acquired_before_config_stable`, `::test_second_thread_start_and_config_do_not_invert`; `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` |
 | admission / locks | a build holds every source it consumes, transitively, for its whole run; the receipt's revisions are read under those locks ([Locking](#safety-model)) | `lhpc/core/build_plan.py:36` `lock_sources`, `:96` `consumed_lines`; `lhpc/core/service_lifecycle_ops.py:3383` `build`, `:3730` `spawn_web_job`; `lhpc/core/build_launcher_runtime.py:206` `run` | the CLI: admission, index, every source exclusive; a detached build: index, its own source exclusive, each dependency's shared | the completion marker (the receipt) and its build-inputs sidecar, written only after the last step | `tests/core/test_build_plan.py::test_a_provider_source_cannot_move_during_a_dependent_build`, `::test_the_receipt_records_the_revision_read_under_the_locks`, `::test_both_paths_lock_and_record_the_same`, `::test_parallel_jobs_share_a_dependency` |
 | config transactions | a save validates everything first, is journalled and rolls back; a crashed save is finished before any writer runs ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1905` `apply_config_transaction`, `:1858` `_finish_pending_journal`, `:1885` `recover_config_journal_at_startup` | config lock `config/.lock` (`lhpc/core/config.py:77` `config_lock`) | `state/config-txn.json`; `lhpc/core/config.py:1800` `recover_config_transaction` rolls back, or blocks and keeps the journal | `tests/core/test_config.py::test_pending_journal_is_recovered_before_next_save`, `::test_rollback_failure_retains_journal_and_blocks_later`, `::test_a_non_transactional_save_finishes_a_pending_journal_before_it_writes`; `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` |
 | config transactions | a malformed `local.toml` is kept; only an absent per-stack file means defaults ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1320` `_write_local_tables`, `:2023` `load_stack_config` | config lock (writes only) | `config/local.toml`, `config/stacks/<id>[@band].toml` | `tests/core/test_config.py::test_local_unsupported_structures_block_and_preserve`, `::test_malformed_stack_config_raises_and_is_preserved`, `::test_absent_stack_config_is_defaults`, `::test_web_returns_409_on_malformed_config`; gap: a write over a `local.toml` that is not valid TOML |
 | install / recovery | source transactions: candidate recorded before the clone, prior archived to `.prev`, no-clobber activation, ownership record, unresolved journal blocks all source mutation ([Source transactions](#safety-model)) | `lhpc/core/install.py:532` `_stage_and_activate`, `:1454` `_staged_clone_record`, `:2280` `_activate_held`, `:1381` `_pending_journals` | admission, `source-txn-index`, source paths | `state/source-txn/<name>-<sha256>.json`, its `.staging` record, `src/.<name>.prev`; `lhpc/core/install.py:1404` `_recover_scan` finishes or rolls back, else blocks | `tests/install/test_source.py::test_recovery_removes_a_clone_killed_before_its_journal`, `::test_recovery_finishes_an_activation_interrupted_right_after_the_archive_rename`, `::test_retained_journal_blocks_every_source_op`, `::test_an_empty_directory_that_is_not_the_recorded_inode_is_kept_and_named`, `::test_an_unrecorded_candidate_is_retained_and_named`; `tests/install/test_staged_update.py::test_failed_clone_leaves_active_source_intact` |
 | install / recovery | locally added files are carried, never collateral ([Locally added files](#safety-model)) | `lhpc/core/source_fs.py:553` `carry_extras`, `:703` `extras_preserved`; `lhpc/core/install.py:2280` `_activate_held` | as source transactions | `.prev` and its journal are kept while a carry is unproven; the journal says `carrying` while the carry writes, and what it wrote is fsynced before the activation; a copy a stop caught inside the carry no longer matches its full recorded identity and is kept and named, never removed on dev+ino; a prior another process can still write into is kept (`prior-in-use`) until the next recovery | `tests/install/test_source.py::test_an_added_file_colliding_with_the_new_upstream_refuses`, `::test_a_carry_failure_restores_the_prior_and_refuses`, `::test_an_addition_made_after_the_carry_retains_the_archived_prior`, `::test_a_write_through_a_descriptor_opened_before_the_update_is_never_lost`, `::test_a_write_by_a_process_inside_the_prior_without_a_descriptor_is_never_lost`, `::test_a_writer_whose_proc_entries_cannot_be_read_is_still_a_holder`, `::test_the_carry_makes_what_it_wrote_durable`; `tests/install/test_source_interruption.py::test_a_power_loss_after_the_carry_wrote_is_recovered_and_retried`, `::test_a_directory_on_a_recycled_inode_in_carrying_is_never_removed` |
-| install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py:1267` `stop`, `:1129` `verify_owned`; `lhpc/core/procident.py:96` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py:2523` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
+| install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py:1267` `stop`, `:1129` `verify_owned`; `lhpc/core/procident.py:96` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py:2524` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
 | install / recovery | path containment ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:74` `_walk_parent`, `:156` `atomic_write` | none | — | `tests/core/test_runtime_fs.py::test_symlinked_parent_refuses_every_runtime_op`, `::test_atomic_write_rejects_symlink_leaf` |
 | install / recovery | a path that cannot be examined is never absent: only ENOENT/ENOTDIR read as absent, any other error refuses or keeps; a `.git` symlink that does not resolve is present ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:740` `probe_exists`, `:756` `probe_stat`; `lhpc/core/firewall_helper.py:1066` `_read_json_state` (the stdlib-only helper's inline rule) | none | the receipt, journal or record is kept while its path cannot be examined | `tests/core/test_existence_probe.py::test_any_other_error_is_unknown_with_the_error`, `::test_an_unexaminable_path_is_never_absent`, `::test_a_git_symlink_that_does_not_resolve_is_never_clean`; `tests/host/test_firewall.py::test_a_journal_that_cannot_be_examined_is_not_absent` |
 | install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2397` `uninstall` | admission, config stability (shared), every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
@@ -346,7 +353,7 @@ the runtime root unless absolute.
 | binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:916` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
 | PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py:2519` `run_server`, `:326` `_trusted_host`, `:259` `_csrf_ok`, `:310` `_set_headers`; `lhpc/core/config.py:1999` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
 | PKI / web | no GET route runs a network or git-remote command ([Web](#safety-model)) | no single owner: every GET renders cached state (`lhpc/core/selfupdate.py:741` `status_view` for self-update) | none | — | `tests/web/test_web.py::test_get_routes_make_no_network_calls`, `::test_page_load_is_read_only`; `tests/core/test_controller.py::test_controller_status_makes_no_live_calls` |
-| PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py:24` `invalidates_snapshot`; `lhpc/core/services.py:882` `invalidate_snapshot`, `:597` `build_snapshot`; the tables `lhpc/core/snapshot_memo.py:43` `SNAPSHOT_NEUTRAL`, `:286` `SNAPSHOT_NEUTRAL_WRITERS` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
+| PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py:26` `invalidates_snapshot`; `lhpc/core/services.py:883` `invalidate_snapshot`, `:597` `build_snapshot`; the tables `lhpc/core/snapshot_memo.py:48` `SNAPSHOT_NEUTRAL`, `:291` `SNAPSHOT_NEUTRAL_WRITERS` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
 | PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:3730` `spawn_web_job`, `:4238` `active_jobs`, `:4078` `prune_logs`; `lhpc/core/webjob_gate.py:64` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
 | PKI / web | no shell: a validated value is always one argv token ([No shell](#safety-model)) | `lhpc/core/commands.py:75` `expand_argv` | none | — | `tests/core/test_structured_exec.py::test_hostile_value_stays_one_token`, `::test_started_process_argv_is_not_a_shell` |
 | PKI / web | typed validation; an `@file:` secret that is missing, empty, a symlink, not a regular file or over 64 KiB blocks the launch; a `pkg-config` failure aborts a build ([Typed validation](#safety-model)) | `lhpc/core/validators.py:434` `validate_param`; `lhpc/core/commands.py:130` `build_env`, `:241` `build_step_argv`; `lhpc/core/runtime_fs.py:515` `read_secret_text` | none | — | `tests/core/test_validators.py::test_start_rejects_malicious_runparam`; `tests/core/test_structured_exec.py::test_at_file_secret_missing_blocks`, `::test_at_file_secret_empty_blocks`, `::test_a_secret_that_is_not_a_bounded_regular_file_is_a_command_error` (a symlink; a 1 MiB file), `::test_a_fifo_secret_is_refused_without_blocking` (not a regular file); `tests/stacks/test_daemon_bounds.py::test_build_step_argv_pkgconfig_failure_fails_closed`; gap: a secret the service user cannot read (no read permission); the 64 KiB bound itself (no test reads a file just over it) |
diff --git a/lhpc/core/services.py b/lhpc/core/services.py
index 0b16805b..fdf57da2 100644
--- a/lhpc/core/services.py
+++ b/lhpc/core/services.py
@@ -613,7 +613,7 @@ class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMix
         cached = getattr(self._snapshot_state, "cache", None)
         if cached is not None and not fresh:
             return cached
-        from . import known_working
+        from . import known_working, source_cache
         confirmed: dict = {}
         for s in self.stacks():
             comps = known_working.load(self._paths, s.id)
@@ -635,6 +635,7 @@ class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMix
                     binary_cover[cid] = rec
         snap = StatusProber(self._system, self._paths, confirmed,
                             binary_cover=binary_cover, sources=sources,
+                            cache=None if fresh else source_cache.SHARED,
                             meshcore_mode=self.meshcore_running_mode()).assess_stacks(self.stacks())
         self._overlay_runtime_bands(snap)
         self._overlay_gui_unavailable(snap)
diff --git a/lhpc/core/snapshot_memo.py b/lhpc/core/snapshot_memo.py
index 8dcb2374..9bf280df 100644
--- a/lhpc/core/snapshot_memo.py
+++ b/lhpc/core/snapshot_memo.py
@@ -20,16 +20,21 @@ from __future__ import annotations
 
 import functools
 
+from . import source_cache
+
 
 def invalidates_snapshot(fn):
-    """Decorate a public mutating service entry: drop the memoized snapshot on entry and exit."""
+    """Decorate a public mutating service entry: drop the memoized snapshot, and the process's cached
+    source probes (a mutation may change a checkout), on entry and exit."""
     @functools.wraps(fn)
     def _wrap(self, *args, **kwargs):
         self.invalidate_snapshot()
+        source_cache.SHARED.clear()
         try:
             return fn(self, *args, **kwargs)
         finally:
             self.invalidate_snapshot()
+            source_cache.SHARED.clear()
     _wrap.invalidates_snapshot = True        # the marker tests check (`__wrapped__` is any wraps)
     return _wrap
 
diff --git a/lhpc/core/source_cache.py b/lhpc/core/source_cache.py
new file mode 100644
index 00000000..9b682f6f
--- /dev/null
+++ b/lhpc/core/source_cache.py
@@ -0,0 +1,148 @@
+"""The console's source-probe cache: a checkout's git answer, reused across page reads.
+
+A status page runs `git status` and `git describe` for every installed source (the bulk of a page's time on
+a small box). This keeps the last answer per (checkout, pin) with a KEY and the time it was probed, and gives it
+back while the key is unchanged and the answer is at most `MAX_AGE_S` old; otherwise it probes as before.
+
+The key: `.git/HEAD`'s bytes; inode, size and mtime of `.git/index`, `.git/packed-refs` and the ref file HEAD
+names; and the mtime of every directory holding a tracked file (listed by `git ls-files` when the answer is
+stored). A commit, checkout, reset, stage, tag or pack changes the first part; a file created, deleted or renamed
+changes its directory. A hand edit of an existing file inside an unchanged directory changes neither: it shows
+when the answer is `MAX_AGE_S` old, counted from the probe, never from a reuse.
+
+Never cached: a checkout whose `.git` is not a plain directory (a symlink, a gitdir file, absent), and any
+answer other than MATCH / DIRTY / DIFFERS (a timeout or a git error is retried at the next read). The service
+gives the cache only to snapshots without `fresh` (never to a recheck under the operation locks), and every
+mutating service entry clears it (`snapshot_memo.invalidates_snapshot`); a probe already running when it is cleared
+does not store its answer (a generation counter). One per process (`SHARED`): the
+console keeps it across requests; a CLI command starts with an empty one.
+"""
+from __future__ import annotations
+
+import os
+import stat
+import threading
+import time
+
+from .model import SourceState
+from .probes.source import _READ_ONLY, _TIMEOUT_S, probe_source
+
+MAX_AGE_S = 30.0
+_KEPT = (SourceState.MATCH, SourceState.DIRTY, SourceState.DIFFERS)
+_HEAD_MAX = 4096
+
+
+def _stamp(path: str):
+    """(inode, size, mtime_ns) of `path` without following a symlink; None when absent."""
+    try:
+        st = os.lstat(path)
+    except FileNotFoundError:
+        return None
+    return (st.st_ino, st.st_size, st.st_mtime_ns)
+
+
+def meta_key(abs_path: str):
+    """The git-metadata part of the key, or None when `abs_path/.git` is not a plain directory or HEAD is
+    unreadable (then nothing is cached for this checkout)."""
+    gitdir = os.path.join(abs_path, ".git")
+    try:
+        if not stat.S_ISDIR(os.lstat(gitdir).st_mode):
+            return None
+        fd = os.open(os.path.join(gitdir, "HEAD"), os.O_RDONLY | os.O_NOFOLLOW)
+        try:
+            head = os.read(fd, _HEAD_MAX)
+        finally:
+            os.close(fd)
+        ref = head.decode("utf-8", "replace").strip()
+        ref_file = (os.path.join(gitdir, *ref[len("ref: "):].split("/"))
+                    if ref.startswith("ref: refs/") and ".." not in ref else None)
+        return (head, _stamp(os.path.join(gitdir, "index")), _stamp(os.path.join(gitdir, "packed-refs")),
+                _stamp(ref_file) if ref_file else None)
+    except OSError:
+        return None
+
+
+def _tracked_dirs(system, abs_path: str):
+    """The checkout's directories that hold a tracked file (the root included), or None."""
+    r = system.runner.run(["git", "-C", abs_path, "ls-files", "-z"], timeout=_TIMEOUT_S, env=_READ_ONLY)
+    if r.returncode != 0 or r.timed_out:
+        return None
+    rel = {os.path.dirname(p) for p in r.stdout.split("\0") if p}
+    return tuple(sorted(os.path.join(abs_path, d) if d else abs_path for d in rel | {""}))
+
+
+def _dir_key(dirs):
+    out = []
+    for d in dirs:
+        try:
+            out.append(os.lstat(d).st_mtime_ns)
+        except OSError:
+            out.append(None)
+    return tuple(out)
+
+
+class SourceCache:
+    def __init__(self) -> None:
+        self._lock = threading.Lock()
+        self._entries: dict = {}
+        self._generation = 0            # bumped by every clear(): a probe begun before one is not stored
+        self.clock = time.monotonic
+
+    def __len__(self) -> int:
+        with self._lock:
+            return len(self._entries)
+
+    def clear(self) -> None:
+        with self._lock:
+            self._entries.clear()
+            self._generation += 1
+
+    def generation(self) -> int:
+        with self._lock:
+            return self._generation
+
+    def get(self, abs_path: str, pin: str, meta):
+        """The stored probe, or None. The key checks run outside the lock; the hit is returned only if no
+        clear() came in between (the same generation, the same entry), so a mutation during a read is seen."""
+        with self._lock:
+            e, gen = self._entries.get((abs_path, pin)), self._generation
+        if e is None or e[0] != meta or self.clock() - e[3] > MAX_AGE_S or _dir_key(e[1]) != e[2]:
+            return None
+        with self._lock:
+            if self._generation != gen or self._entries.get((abs_path, pin)) is not e:
+                return None
+        return e[4]
+
+    def put(self, abs_path: str, pin: str, meta, dirs, dkey, at: float, probe, generation: int) -> None:
+        """Store `probe` unless the cache was cleared since `generation` was read (a mutation ran while the
+        probe was in flight: its answer may predate the mutation)."""
+        with self._lock:
+            if generation == self._generation:
+                self._entries[(abs_path, pin)] = (meta, dirs, dkey, at, probe)
+
+
+def probe_cached(cache, system, spec, abs_path: str, git: bool = True, probe=probe_source):
+    """`probe(system, spec, abs_path, git=…)` (the source probe), through `cache` when one is given (see the
+    module docstring)."""
+    if cache is None or not git:
+        return probe(system, spec, abs_path, git=git)
+    meta = meta_key(abs_path)
+    if meta is None:
+        return probe(system, spec, abs_path)
+    pin = spec.pin_commit or ""
+    hit = cache.get(abs_path, pin, meta)
+    if hit is not None:
+        return hit
+    # The key and the generation are taken BEFORE the probe, so a change while it runs makes the next read
+    # probe again, and a clear while it runs keeps its answer out of the cache.
+    gen = cache.generation()
+    at = cache.clock()
+    dirs = _tracked_dirs(system, abs_path)
+    dkey = _dir_key(dirs) if dirs is not None else None
+    got = probe(system, spec, abs_path)
+    if dirs is not None and got.state in _KEPT:
+        cache.put(abs_path, pin, meta, dirs, dkey, at, got, gen)
+    return got
+
+
+SHARED = SourceCache()
diff --git a/lhpc/core/status.py b/lhpc/core/status.py
index 130ff7bd..d30506d0 100644
--- a/lhpc/core/status.py
+++ b/lhpc/core/status.py
@@ -12,7 +12,7 @@ from __future__ import annotations
 
 from dataclasses import dataclass, field
 
-from . import gps, procident
+from . import gps, procident, source_cache
 from .model import (
     Component,
     ComponentKind,
@@ -64,9 +64,11 @@ class StatusProber:
 
     def __init__(self, system: System, paths: Paths, profiles: dict | None = None,
                  binary_cover: dict | None = None,
-                 meshcore_mode: str = _meshcore_mode.DEFAULT_MODE, sources: bool = True) -> None:
+                 meshcore_mode: str = _meshcore_mode.DEFAULT_MODE, sources: bool = True,
+                 cache=None) -> None:
         self._system = system
         self._sources = sources                  # False: no git per source (`build_snapshot`)
+        self._cache = cache                      # a `source_cache.SourceCache` for the git part, or None
         self._paths = paths
         self._profiles = profiles or {}
         # The MeshCore stack's effective mode (services.meshcore_mode()): decides which of the
@@ -248,8 +250,8 @@ class StatusProber:
         key = (abs_path, comp.source.pin_commit or "")
         probe = self._source_probes.get(key)
         if probe is None:
-            probe = self._source_probes[key] = probe_source(self._system, comp.source, abs_path,
-                                                            git=self._sources)
+            probe = self._source_probes[key] = source_cache.probe_cached(
+                self._cache, self._system, comp.source, abs_path, git=self._sources, probe=probe_source)
         return probe
 
     def _profile_state(self, comp: Component, src) -> ProfileState:
diff --git a/tests/conftest.py b/tests/conftest.py
index ddb330bf..32f86b38 100644
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -165,6 +165,16 @@ def short_tmp_path():
         shutil.rmtree(d, ignore_errors=True)
 
 
+@pytest.fixture(autouse=True)
+def _empty_source_cache():
+    """ISOLATION: the process keeps cached source probes across service instances
+    (`lhpc/core/source_cache.py`); each test starts with none, as a CLI command does."""
+    from lhpc.core import source_cache
+    source_cache.SHARED.clear()
+    yield
+    source_cache.SHARED.clear()
+
+
 @pytest.fixture(autouse=True)
 def _isolated_runtime_root(monkeypatch, tmp_path_factory):
     """HERMETIC: never let a test resolve the operator's REAL deployment root. Tests that call the
diff --git a/tests/install/test_source_cache.py b/tests/install/test_source_cache.py
new file mode 100644
index 00000000..d223f278
--- /dev/null
+++ b/tests/install/test_source_cache.py
@@ -0,0 +1,252 @@
+"""The console's source-probe cache (`lhpc/core/source_cache.py`): a checkout's git answer is reused for at
+most 30 s while its git metadata and the directories of its tracked files are unchanged, and never by a
+recheck under the operation locks.
+
+Real git in temporary directories (the `git` / `make_repo` fixtures); git runs are counted at the runner,
+and the clock is the cache's own, injected.
+"""
+from __future__ import annotations
+
+import os
+
+import pytest
+
+from lhpc.core import source_cache
+from lhpc.core.model import SourceSpec, SourceState
+from lhpc.core.probes.backends import RealSystem
+
+
+class _Counting:
+    """A RealSystem whose runner counts the git commands it runs."""
+
+    def __init__(self):
+        self.system = RealSystem()
+        self.git = []
+        real = self.system.runner.run
+
+        def run(argv, timeout, cwd=None, env=None):
+            if argv[:1] == ["git"]:
+                self.git.append(argv[3] if len(argv) > 3 else "")
+            return real(argv, timeout, cwd=cwd, env=env)
+        self.system.runner.run = run
+
+
+@pytest.fixture
+def world(tmp_path, make_repo, git):
+    repo = tmp_path / "src" / "app"
+    head = make_repo(repo, {"f": "a\n", "d/h": "c\n"})
+    cache = source_cache.SourceCache()
+    now = [100.0]
+    cache.clock = lambda: now[0]
+    counted = _Counting()
+    spec = SourceSpec(path="src/app", pin_commit=head)
+
+    def probe():
+        return source_cache.probe_cached(cache, counted.system, spec, str(repo))
+    return repo, cache, now, counted, probe, git
+
+
+def _status_runs(counted):
+    return counted.git.count("status")
+
+
+def test_a_second_read_within_30_s_runs_no_git(world):
+    repo, cache, now, counted, probe, _git = world
+    first = probe()
+    now[0] += 29.0
+    second = probe()
+    assert (first.state, second.state) == (SourceState.MATCH, SourceState.MATCH)
+    assert second.head == first.head and second.version == first.version
+    assert _status_runs(counted) == 1
+
+
+def test_after_30_s_it_probes_again(world):
+    repo, cache, now, counted, probe, _git = world
+    probe()
+    now[0] += 30.5
+    probe()
+    assert _status_runs(counted) == 2
+
+
+@pytest.mark.parametrize("change", ["commit", "stage", "checkout", "packed-refs", "new file in a tracked dir",
+                                    "deleted file", "renamed file", "new file at the top"])
+def test_a_change_to_the_key_probes_again(world, change):
+    repo, cache, now, counted, probe, git = world
+    probe()
+    if change == "commit":
+        (repo / "f").write_text("b\n")
+        git(repo, "commit", "-qam", "two")
+    elif change == "stage":
+        (repo / "f").write_text("b\n")
+        git(repo, "add", "f")
+    elif change == "checkout":
+        git(repo, "checkout", "-q", "-b", "other")
+    elif change == "packed-refs":
+        git(repo, "tag", "v1")
+        git(repo, "pack-refs", "--all")
+    elif change == "new file in a tracked dir":
+        (repo / "d" / "new").write_text("n\n")
+    elif change == "deleted file":
+        (repo / "d" / "h").unlink()
+    elif change == "renamed file":
+        os.rename(repo / "d" / "h", repo / "d" / "h2")
+    else:
+        (repo / "top").write_text("t\n")
+    now[0] += 1.0
+    probe()
+    assert _status_runs(counted) == 2
+
+
+def test_a_hand_edit_inside_an_unchanged_directory_shows_within_30_s(world):
+    # The stated residual window: an in-place edit of an existing file changes no key part.
+    repo, cache, now, counted, probe, _git = world
+    assert probe().state is SourceState.MATCH
+    st = (repo / "d").stat()
+    (repo / "d" / "h").write_text("edited\n")
+    os.utime(repo / "d", ns=(st.st_atime_ns, st.st_mtime_ns))     # the directory is left unchanged
+    now[0] += 10.0
+    assert probe().state is SourceState.MATCH                      # still the cached answer
+    now[0] += 21.0
+    assert probe().state is SourceState.DIRTY                      # within 30 s of the first probe
+
+
+def test_clear_drops_every_entry(world):
+    repo, cache, now, counted, probe, _git = world
+    probe()
+    cache.clear()
+    probe()
+    assert _status_runs(counted) == 2
+
+
+def test_a_failed_probe_is_not_kept(world, monkeypatch):
+    repo, cache, now, counted, probe, _git = world
+    real = counted.system.runner.run
+
+    def failing_status(argv, timeout, cwd=None, env=None):
+        if argv[3:4] == ["status"] and not counted.git.count("status"):
+            counted.git.append("status")
+            from lhpc.core.probes.backends import CommandResult
+            return CommandResult(returncode=124, stdout="", stderr="", timed_out=True)
+        return real(argv, timeout, cwd=cwd, env=env)
+    counted.system.runner.run = failing_status
+    assert probe().state is SourceState.UNKNOWN
+    assert probe().state is SourceState.MATCH                      # retried, not served from the cache
+
+
+@pytest.mark.parametrize("shape", ["symlinked .git", "git file", "no checkout"])
+def test_a_checkout_without_a_plain_git_directory_is_never_cached(world, tmp_path, shape):
+    repo, cache, now, counted, probe, _git = world
+    target = tmp_path / "elsewhere.git"
+    if shape == "symlinked .git":
+        os.rename(repo / ".git", target)
+        os.symlink(target, repo / ".git")
+    elif shape == "git file":
+        os.rename(repo / ".git", target)
+        (repo / ".git").write_text(f"gitdir: {target}\n")
+    else:
+        import shutil
+        shutil.rmtree(repo)
+    probe()
+    probe()
+    assert source_cache.meta_key(str(repo)) is None
+    assert len(cache) == 0
+    assert _status_runs(counted) == (0 if shape == "no checkout" else 2)   # git on every read, or none
+
+
+def test_a_mutating_service_entry_clears_the_cache_on_entry_and_exit():
+    from lhpc.core.snapshot_memo import invalidates_snapshot
+    seen = []
+
+    class Svc:
+        def invalidate_snapshot(self):
+            pass
+
+        @invalidates_snapshot
+        def change(self):
+            seen.append(len(source_cache.SHARED))
+            source_cache.SHARED.put("/x", "", (b"",), (), (), 0.0, object(), source_cache.SHARED.generation())
+    source_cache.SHARED.put("/y", "", (b"",), (), (), 0.0, object(), source_cache.SHARED.generation())
+    Svc().change()
+    assert seen == [0] and len(source_cache.SHARED) == 0
+
+
+def test_a_recheck_never_uses_the_cache_and_a_page_read_does(tmp_path, monkeypatch):
+    # `fresh=True` is the authoritative recheck under the operation locks: it neither reads nor fills
+    # the process cache; an ordinary snapshot passes the process cache to every source probe.
+    from lhpc.core.paths import Paths
+    from lhpc.core.probes.backends import CommandResult, FakeSystem
+    from lhpc.core.services import ControllerService
+    src = str(tmp_path / "src" / "meshcore-webui")
+    a = "a" * 40
+    fake = FakeSystem(paths={src, src + "/.git"}, commands={
+        ("git", "-C", src, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
+            CommandResult(0, f"# branch.oid {a}\n", ""),
+        ("git", "-C", src, "describe", "--tags", "--always"): CommandResult(0, "v1\n", "")})
+    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
+    caches = []
+    real = source_cache.probe_cached
+    monkeypatch.setattr(source_cache, "probe_cached",
+                        lambda cache, *a, **k: (caches.append(cache), real(cache, *a, **k))[1])
+    svc.build_snapshot(fresh=True)
+    assert caches and all(c is None for c in caches)
+    caches.clear()
+    svc.invalidate_snapshot()
+    svc.build_snapshot()
+    assert caches and all(c is source_cache.SHARED for c in caches)
+
+
+def test_two_page_reads_of_an_unchanged_checkout_run_git_once(tmp_path, make_repo):
+    # Through the service, as the console reads: one snapshot per request, the request memo dropped
+    # between them. Before the cache every request ran git for every installed checkout.
+    from lhpc.core.paths import Paths
+    from lhpc.core.services import ControllerService
+    repo = tmp_path / "src" / "meshcore-webui"
+    make_repo(repo, {"app/main.py": "x = 1\n"})
+    counted = _Counting()
+    svc = ControllerService(system=counted.system, paths=Paths(runtime_root=tmp_path))
+    svc.build_snapshot()
+    svc.invalidate_snapshot()                                     # the next request starts
+    svc.build_snapshot()
+    assert _status_runs(counted) == 1
+
+
+def test_a_probe_in_flight_during_a_mutation_is_not_kept(world):
+    # A page probe that started before a console mutation must not store its answer after the
+    # mutation cleared the cache: with an in-place edit the key is unchanged, so the stale MATCH would
+    # be served for up to 30 s. The probe finishing late is played in order here: it runs, then the
+    # mutation clears the cache and edits a file in place, then the probe returns.
+    repo, cache, now, counted, probe, _git = world
+    from lhpc.core.probes.source import probe_source
+
+    def probe_then_mutation(system, spec, abs_path, git=True):
+        got = probe_source(system, spec, abs_path, git=git)        # the old answer: MATCH
+        cache.clear()                                               # a mutating service call
+        st = (repo / "d").stat()
+        (repo / "d" / "h").write_text("edited\n")
+        os.utime(repo / "d", ns=(st.st_atime_ns, st.st_mtime_ns))  # the cheap key cannot see it
+        return got
+    spec = SourceSpec(path="src/app", pin_commit=_git(repo, "rev-parse", "HEAD"))
+    old = source_cache.probe_cached(cache, counted.system, spec, str(repo), probe=probe_then_mutation)
+    assert old.state is SourceState.MATCH and len(cache) == 0     # not kept
+    assert probe().state is SourceState.DIRTY                      # the next read runs git
+
+
+def test_a_hit_read_across_a_mutation_is_not_served(world, monkeypatch):
+    # A page read that fetched a cached entry must not return it when a console mutation cleared the cache
+    # (and edited a file in place) between the fetch and the return. Played in order: the read fetches the
+    # entry, the mutation runs while the read checks the directories, then the read decides.
+    repo, cache, now, counted, probe, _git = world
+    assert probe().state is SourceState.MATCH                      # stored
+    real_dir_key = source_cache._dir_key
+
+    def mutation_during_the_check(dirs):
+        cache.clear()                                               # a mutating service call
+        st = (repo / "d").stat()
+        (repo / "d" / "h").write_text("edited\n")
+        os.utime(repo / "d", ns=(st.st_atime_ns, st.st_mtime_ns))  # the cheap key cannot see it
+        monkeypatch.setattr(source_cache, "_dir_key", real_dir_key)
+        return real_dir_key(dirs)
+    monkeypatch.setattr(source_cache, "_dir_key", mutation_during_the_check)
+    now[0] += 1.0
+    assert probe().state is SourceState.DIRTY                      # not the old MATCH: git ran
+    assert _status_runs(counted) == 2
diff --git a/tests/repo/test_module_budgets.py b/tests/repo/test_module_budgets.py
index cc950735..c0abb299 100644
--- a/tests/repo/test_module_budgets.py
+++ b/tests/repo/test_module_budgets.py
@@ -19,7 +19,7 @@ CORE = repo_paths.REPO / "lhpc" / "core"
 
 # file: (max lines, max public methods) — measured on dev at 0.12.9, raised where a line says why
 BUDGETS = {
-    "services.py": (2959, 28),
+    "services.py": (2960, 28),  # efficiency-a1: the source-probe cache argument of build_snapshot
     "service_base.py": (166, 0),
     "service_auto_install.py": (1875, 16),
     "service_binary_channel.py": (297, 19),
```
