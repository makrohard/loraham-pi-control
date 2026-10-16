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
