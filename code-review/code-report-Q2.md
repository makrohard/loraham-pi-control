# Code report — Q2: `probe_exists()`, one fail-closed existence probe

## Correction 2 (gate 1 round 2 RED → round 3)

Commits after the correction (round-2 sha in brackets; Correction 1 below uses the round-2 shas, the
body below it the round-1 shas):

| round 3 | round 2 | patch-id (`git patch-id --stable`, first 12) | change |
|---|---|---|---|
| `2e934ea` | `97e5057` | `73302f83c118` → `af34aa7da31a` | plan: the behaviour change, why, every call site |
| `65540b0` | `d788bbe` | `eb805740a5f8` = `eb805740a5f8` | none |
| `66810ed` | `3f5b102` | `3e9ebcd6c2dd` → `87bcffc77f34` | code, 2 tests, CHANGELOG bullet, docs sentence, commit message |
| `a8db6d9` | `7bcccee` | `839c1f1a76f8` = `839c1f1a76f8` | none |
| report | `62083e7` | — | this section, the gate-1 file |

Made with `git commit --fixup 97e5057`, `--fixup 3f5b102`, `--fixup=reword:3f5b102` and
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash 042716f`; the tree before and after the rebase is
identical (`HEAD^{tree}` compared). The new CHANGELOG bullet and docs sentence sit outside `7bcccee`'s
context lines, so its patch-id is unchanged this time.

### The finding

The plan said "Behaviour change on healthy disks: none". Untrue: `probe_exists` is `os.lstat`, the old
`dirty_report`/`extra_files` check was `(dest / ".git").exists()`, which follows the link. A dangling
`.git` symlink was "not a checkout" before (`DirtyReport()` / `()`), and "present" on round 2, which
then handed the tree to git. No test pinned it.

### Reproduction on both trees (and the fix)

`Installer(...).dirty_report(d, ...)` / `.extra_files(d, ...)` on a tree with `added.txt` and a `.git`
symlink, run from a worktree of each commit with `PYTHONPATH` set to it (Python 3.11). The fake
runner answers either nothing (git fails, as with no enclosing repository) or `rc 0`, empty output for
`git status`/`ls-files` (what git prints for a CLEAN enclosing repository):

| tree | `.git` | git answer | `dirty_report` | `extra_files` | git calls |
|---|---|---|---|---|---|
| base 042716f | dangling | fails | `DirtyReport()` (clean) | `()` | 0 |
| base 042716f | dangling | clean enclosing | `DirtyReport()` (clean) | `()` | 0 |
| base 042716f | loop | either | `DirtyReport()` (clean) | `()` | 0 |
| round 2 62083e7 | dangling | fails | `tracked=('(git status failed — treating as dirty)',)` | `None` | 2 |
| round 2 62083e7 | dangling | clean enclosing | `DirtyReport()` (**clean**) | `()` | 2 |
| round 2 62083e7 | loop | same two rows as dangling | | | 2 |
| round 3 | dangling | either | `tracked=('(.git is a dangling symlink — treating as dirty)',)` | `None` | 0 |
| round 3 | loop | either | `tracked=("(.git is a symlink that cannot be followed ([Errno 40] Too many levels of symbolic links: '<d>/.git') — treating as dirty)",)` | `None` | 0 |

That the clean-enclosing row is real git behaviour, not just the fake (git 2.43.0, in `$SCRATCH`):
an `outer/` repository with `sub/.git` → a dangling link: `git -C sub status --porcelain` prints
`?? sub/a` with rc 0 (git walked up to `outer`); `git -C sub ls-files --others` prints `a`; the same
`.git` with no enclosing repository: `fatal: not a git repository`, rc 128. So round 2 was not only
"dirty instead of clean": its answer depended on whatever repository encloses the tree, and could
be clean.

`Path.exists()` for the old reading, measured on 3.11.15, 3.12.3 and 3.13.14: dangling → False,
loop → False, an injected EIO on `os.stat` → raises `OSError` (all three versions).

### Decision: fail closed, and never ask git

The inventory answers "may this tree be replaced or removed without losing the operator's work?". A
`.git` that is there but leads nowhere does not answer that, and the batch's rule is "not proven
absent → not absent". Keeping the old "absent" reading would make a tree with a broken `.git` read
as a clean non-checkout, so every caller below would replace or remove it unasked. Handing it to git
(round 2) makes the answer depend on the enclosing directory. So:

- `install._git_marker_anomaly(dest)` (install.py:164): `probe_stat(.git)` (lstat). Absent → `None`.
  `unknown` → `"cannot examine .git (why)"` (round 2's text, unchanged). A symlink → `probe_stat(...,
  follow=True)`: absent → `".git is a dangling symlink"`; `unknown` (ELOOP for a loop, EIO, EACCES on
  the target) → `".git is a symlink that cannot be followed (why)"`; present → `""`. Anything else
  present (a directory, a gitfile) → `""`.
- `dirty_report` (install.py:901): `None` → `DirtyReport()`; an anomaly →
  `DirtyReport(tracked=("(<anomaly> — treating as dirty)",))`; `""` → git, as before.
- `extra_files` (install.py:958): `None` → `()`; an anomaly → `None`; `""` → git, as before.

Behaviour change, exactly: a `.git` symlink that does not resolve (dangling, or a loop) is treated as
present and the tree is not reported clean; before, it was ignored (clean, `()`). A `.git` symlink
whose target cannot be stat'ed for another reason (EACCES, EIO) was an uncaught `OSError` from
`Path.exists()` before; now it is the named anomaly. A `.git` symlink that resolves, a `.git`
directory and a gitfile go to git as before. Plan, CHANGELOG (new bullet) and the docs sentence state
it; no "no behaviour change" sentence is left in the plan, the CHANGELOG or the docs (the gate file
quotes the old one only as the finding and as a removed diff line).

Call sites whose result changes for such a `.git` (HEAD line numbers):

| site | function | result now |
|---|---|---|
| install.py:475 | `_adopt_locked` (update) | `blocks_update()` → action failed, the anomaly listed under "modified" |
| install.py:607 | `_stage_and_activate` final dirty check | blocks the archive |
| install.py:622 | `_stage_and_activate` `_carry` | `None` → refuses ("could not be inventoried") |
| install.py:1514 / 1519 | `_prev_dirty_scan` | dirty → the archived prior is retained |
| install.py:1552 | `_prev_extras` | `None` |
| service_binary_ops.py:274 | `binary_install` (pinned checkout) | refused: local changes |
| service_binary_ops.py:539 | `_stale_clean_clones` | skipped (not a clean clone) |
| service_binary_ops.py:792 | `switch_source_plan` | refusal |
| service_maintenance.py:1961 / 1968 | `_remove_source_leaf`, uninstall (`allow_dirty=False`) | refused; Clean (`allow_dirty=True`) skips the check, unchanged |
| service_auto_install.py:1698 | `_reconcile_group` | `blocked` |

At the sites where `verify_identity` runs first (binary_ops 274/539/792, maintenance, auto-install)
that check may refuse before; the dirty result changes either way.

Other sites of the batch, without any I/O error (also in the plan's risk section): the boot-restore,
config and firewall sites used `lexists` (lstat) before, so a dangling symlink reads the same.
Retire's `still_there` moved from `os.path.exists` (follows) to lstat: a dangling symlink at a
receipt path now reads "still present", but the unlink loop right before it unlinks every symlink
(`islink`), so the result differs only if one remains after that loop (a failed unlink is already
INCOMPLETE). `_rel_files_under` follows like `os.path.isdir` did; a base it cannot stat for a reason
other than ENOENT/ENOTDIR (ELOOP included) now raises instead of listing `[]`. The owned-dir check
leaves a symlink alone as before.

### Tests (tests/core/test_existence_probe.py)

- `test_a_git_symlink_that_does_not_resolve_is_never_clean[dangling,loop]`: fake git answers like a
  clean enclosing repository; asserts `dirty_report == DirtyReport(tracked=("(.git is a dangling
  symlink — treating as dirty)",))` (loop: the exact ELOOP text with the path), `extra_files is None`,
  no git call, the link and `added.txt` still there.
- `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading`: no `.git` → `DirtyReport()` and
  `()` with no git call; a `.git` symlink to a directory → git consulted (`status`, `ls-files`) and its
  answer returned (`DirtyReport()`, `()`).

Red-before (the round-3 test file copied into a worktree of each commit, imports checked to come
from the worktree): base 042716f and round 2 62083e7 both `2 failed, 1 passed` — the two
`never_clean` cases fail on the `dirty_report` assertion (`DirtyReport(tracked=(), untracked=())` on
both: on the base the `.git` was ignored, on round 2 the fake enclosing repository answered clean);
the keep-their-reading test passes on both, as it must. Green on round 3: `32 passed` (whole file).

Healthy trees keep their results; existing tests that prove it, unchanged and green:
`tests/install/test_source.py::test_dirty_report_names_edits_and_additions_but_not_artifacts`,
`::test_dirty_carveout_is_exact_leaf_only`, `::test_dirty_report_ignores_the_shipped_patch_only`,
`::test_an_ignored_file_survives_an_update` (all with a real `.git` directory), and the round-2
`test_an_unexaminable_path_is_never_absent[dirty-report-*, extra-files-*]` (lstat `unknown`, same
text). No existing test pinned the no-`.git` reading; the new keep-their-reading test does (it
passes on the base too).

### Runs (foreground, HEAD, Python 3.11 venv with `.[dev]`)

- `ruff check lhpc testlab`: All checks passed.
- `tests/core tests/install tests/host/test_firewall.py tests/repo`: `103 failed, 3778 passed, 22 skipped`.
  The failure set (by test id) is identical to the same run on round 2's head `62083e7` in a
  worktree (`103 failed, 3775 passed, 22 skipped`): 92 `test_bootstrap_deps.py`,
  `test_firewall.py::test_receipt_reader_rejects_nonroot_symlink_and_unsafe`,
  `test_version_consistent.py::test_changelog_leads_with_the_current_version`, 3
  `test_binary_install.py`, 1 `test_binary_channel.py`, and five that were not in round 2's
  report list (2 `tests/core/test_disk_warning.py`, `test_binary_pin_override.py`,
  `test_dependency_overview.py`, `test_deps.py`) — they fail identically on `62083e7` in this
  container, so they are environmental here, not this change; not investigated further. +3 passed = the
  3 new test cases. Two earlier runs of the same HEAD tree reported `3773 passed, 27 skipped`
  (same 103 failures); split runs and the final full run gave 22 skips (all root-chmod, missing
  `zstd`, and the slow-target "no row A yet" skip). The 5-skip difference was not explained.

### Adversarial self-review of the correction

Found and fixed before the push:
- My first `extra_files` edit inverted the branches (`absent` went to git, a reachable `.git`
  returned `()`); `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading` caught it (a
  `ls-files` call with no `.git`). Fixed before the commit.
- The first CHANGELOG draft claimed "an update keeps an archived copy" and "does not take its file
  inventory"; the update is refused at `_adopt_locked` first, so the archive path is not reached in
  the plain case. Both clauses dropped; the bullet lists only the local-change checks.
- The first caller line list was taken before my edit shifted install.py by 20 lines; recomputed on
  HEAD.
- The commit message of `3f5b102` did not mention the behaviour change; reworded
  (`--fixup=reword:`) with a paragraph on it.
- The plan's first draft called the dangling-`.git` change "the ONE behaviour change without an I/O
  error", while its next bullet names a second (`_rel_files_under` on a symlink-loop base now raises
  instead of listing `[]`). "ONE" dropped; that bullet now says it is a second such change.

Noted, not changed:
- A `.git` DIRECTORY that is not a valid repository (empty, corrupt) is handed to git as before, and
  git walks up to an enclosing repository the same way (checked: an empty `emp/.git` inside `outer/`
  → `git -C emp status` prints `?? emp/`, rc 0). Pre-existing on the base, not changed by Q2; a fix
  needs a different mechanism (compare `git rev-parse --show-toplevel` with `dest`, or set
  `GIT_CEILING_DIRECTORIES`). Proposed as a follow-up, not widened into this batch.
- `_stage_and_activate`'s carry message says "(git failed)" also when the inventory is `None` for an
  unexaminable or dangling `.git`; reached only if the tree changed after the first dirty check, which
  already names the anomaly.

## Correction 1 (gate 1 round 1 RED → round 2)

Commits after the correction (the round-1 sha in brackets; the body below this section is the round-1
report and uses the round-1 shas):

| round 2 | round 1 | patch-id (`git patch-id --stable`, first 12) | change |
|---|---|---|---|
| `97e5057` | `6753747` | `83f2f16ea3f8` → `73302f83c118` | finding 1 (plan text) |
| `d788bbe` | `c6cdaac` | `eb805740a5f8` = `eb805740a5f8` | none |
| `3f5b102` | `075519e` | `2b4aa9c35721` → `3e9ebcd6c2dd` | finding 2 (code, test, docs sentence, message) |
| `7bcccee` | `f8badb9` | `97f09f822b50` → `839c1f1a76f8` | none in its own lines — see below |
| report | `38e2289` | — | this section, the gate-1 file |

`7bcccee`'s patch-id moved although none of its `+`/`-` lines did: `git patch-id` hashes context
lines, and its `docs/architecture.md` hunk has the sentence `3f5b102` rewords as context. Proof, per
file (`git show <sha> -- <file> | git patch-id --stable`): `CHANGELOG.md` `185d76a45cbb` both,
`lhpc/core/firewall_helper.py` `efd9ddc419e6` both, `tests/host/test_firewall.py` `54eb884c74c0`
both; `docs/architecture.md` `3ccec2cc40ee` → `287a42648b96`, and
`diff <(git show f8badb9 -- docs | grep '^[-+][^-+]') <(git show 7bcccee -- docs | grep '^[-+][^-+]')`
is empty. The docs claim cannot be narrowed without touching that context: every line of
`075519e`'s sentence is inside `f8badb9`'s hunk. The final tree before and after the autosquash
rebase is identical (`git rev-parse HEAD^{tree}` compared).

### Finding 1 — the plan described two edits that are not in the diff

`plans/PLAN-Q2.md` listed bootstrap `local.toml`/`secrets.toml` (install.py:263/268) under REPLACE,
named `apply_bootstrap` as a test seam, and claimed a dangling symlink there is now kept. The code
commits never changed them. Fixed: the two rows move to LEAVE with one sentence why (`Path.exists()`
raises on EIO/EACCES on every supported Python, so `apply_bootstrap` already fails the action and no
red test is possible); the `apply_bootstrap` seam and the bootstrap risk line are gone; the commit
list counts the 9 controller sites; the plan names `probe_stat` and the stat-only tests.

### Finding 2 — `os.path.isdir()` after `probe_exists()` read a stat error as "not a directory"

Two sites took the kind from a second, error-swallowing call after a `present` probe:

- `_rel_files_under` (service_binary_ops.py): lstat fine + stat failing → `isdir` False → `[]` →
  `_artifact_only_dir` → `all([])` → True: a tree it could not examine was judged the artifact's own
  leftover.
- owned-dir retirement (service_binary_ops.py): `isdir` False → the dir skipped → receipt dropped,
  the folder left unowned.

The round-1 injector failed `os.lstat` and `os.stat` together, so the probe already said `unknown`
and neither branch was reached.

Change (`3f5b102`):

- `runtime_fs.probe_stat(path, *, follow=False) -> (state, why, st)` (runtime_fs.py:752): the same
  ENOENT/ENOTDIR-only rule as `probe_exists`, plus the stat result it read (`st` None unless present);
  `follow=True` uses `os.stat`, so a dangling link is absent and an unreachable target is `unknown`.
  `probe_exists` itself is unchanged (`d788bbe` keeps its patch-id).
- `_rel_files_under` (service_binary_ops.py:618): one `probe_stat(base, follow=True)` — the question
  `os.walk(base)` answers. `unknown` raises `OSError` (its caller returns False → the switch
  refuses); is-dir comes from `stat.S_ISDIR(st.st_mode)`.
- owned-dir retirement (service_binary_ops.py:953/955): `probe_stat(d)` (lstat: a symlink is never
  `rmtree`d through, as before), then for a non-link `probe_stat(d, follow=True)` (is it a dir?);
  `unknown` from either raises into the existing INCOMPLETE result, receipt kept. A race that swaps
  in a symlink between the two probes reaches `shutil.rmtree`, which refuses a symlink → INCOMPLETE.
- Tests (tests/core/test_existence_probe.py), injector `_stat_failing` fails `os.stat` ONLY for one
  exact path (no `dir_fd` call affected):
  - `test_probe_stat_returns_the_kind_and_follows_on_request` (link/dir kinds, dangling → absent,
    stat-only EIO → `unknown` while lstat still `present`);
  - `test_a_tree_whose_stat_fails_is_not_listed_as_empty[EIO,EACCES]`: `_rel_files_under` raises
    `OSError` with the errno text; `switch_source_plan` refuses the path; the file stays;
  - `test_retire_keeps_an_owned_dir_whose_stat_fails[EIO,EACCES]`: `binary_retire` → not ok,
    summary "… stays owned", details exactly `could not remove build/tools/meshtastic-cli ([Errno N] …)`,
    receipt still there, venv file intact.

Red-before (the new test file against `075519e`'s `lhpc/`:
`git stash -- lhpc && pytest tests/core/test_existence_probe.py; git stash pop`): `5 failed, 24 passed`.

- `test_probe_stat_…`: `AttributeError: module 'lhpc.core.runtime_fs' has no attribute 'probe_stat'`.
- `test_a_tree_whose_stat_fails_…[EIO]`, `[EACCES]`: `Failed: DID NOT RAISE OSError` (the listing was `[]`).
- `test_retire_keeps_an_owned_dir_…[EIO]`, `[EACCES]`: `ActionResult(ok=True, summary="Retired the binary
  install of 'daemon' (1 file(s) removed).")` — the receipt was dropped and the folder left behind.

Green: `29 passed`.

### Every site in the diff and how it fails closed (HEAD line numbers)

The presence decision comes from the probe's own result at each of these; `unknown` refuses or keeps:

| site | probe | `unknown` → |
|---|---|---|
| lhpc/core/service_binary_ops.py:618 `_rel_files_under` | `probe_stat(follow=True)`, kind from `st` | raises `OSError`; `_artifact_only_dir` (:746) → False; the switch refuses (:785 →) |
| lhpc/core/service_binary_ops.py:857 `_receipt_leaf_present`, used by retire `still_there` (:935) | `probe_exists` | present → INCOMPLETE, receipt kept |
| lhpc/core/service_binary_ops.py:953/955 owned-dir retirement | `probe_stat` then `probe_stat(follow=True)`, kind from `st` | INCOMPLETE, receipt kept |
| lhpc/core/install.py:890 `dirty_report` | `probe_exists` | dirty ("cannot examine .git …") |
| lhpc/core/install.py:954 `extra_files` | `probe_exists` | `None` (caller refuses) |
| lhpc/core/service_boot_restore.py:75 running-band marker | `probe_exists` | `unsafe` |
| lhpc/core/service_boot_restore.py:89 last-start candidate | `probe_exists` | `unsafe` |
| lhpc/core/boot_restore.py:140 `load_journal` | `probe_exists` | `unsafe:unreadable (…)` |
| lhpc/core/config.py:1810 `recover_config_transaction` | `probe_exists` | `""` (recovery required) |
| lhpc/core/firewall_helper.py:1000 `_read_json_state` | inline `os.lstat` | `present-invalid` |

Two error-swallowing `os.path` calls remain next to these sites, outside the probe, and both err to
the safe side: service_binary_ops.py:630 `os.path.isdir(full)` inside the walk (an entry it cannot
stat is listed as a file → not owned → not artifact-only), and :928 the retire unlink guard
`os.path.isfile/islink` (an error → not unlinked → the :935 probe sees it present → receipt kept).
Two more `os.path.isdir` calls in the same file decide no deletion and no retirement: :670 in
`_sweep_binary_staging` (an error → the `rmtree` is skipped and the staging folder stays — the safe
side, not a safety decision), and :713 in `_binary_provision` (the build steps' working directory:
an error leaves it at the runtime root, so a step that needs the source fails and the provision is
rolled back — neither a delete nor a retire decision).

Text: the CHANGELOG bullet already names exactly these behaviours (retire, switch, `.git`
inventory, boot restore and config journal, firewall) and never says "uniformly"; it is unchanged.
The docs/architecture.md sentence claimed "an existence check that decides a delete, a retirement or
'nothing to recover' goes through `probe_exists`" — wider than the diff (bootstrap still uses
`Path.exists()`, the unlink guard `isfile`). It now names the sites above and `probe_stat`.

Guardrail: `lhpc/` vs the round-1 head +27 −6 (runtime_fs 976→992, service_binary_ops 995→1000);
vs the base +94 −27. One new plain function, no new module dependency (`service_binary_ops` imports
stdlib `stat`).

### Runs (foreground, HEAD, Python 3.11)

- `tests/core`: `2074 passed, 6 skipped`.
- `tests/install tests/host/test_firewall.py tests/repo`: `98 failed, 1701 passed, 21 skipped`. The
  failure set is identical, test for test, to the same run on the round-1 head `38e2289` (a
  worktree, imports checked to come from it): 92 `test_bootstrap_deps.py` (root container, no
  non-root operator), `test_firewall.py::test_receipt_reader_rejects_nonroot_symlink_and_unsafe`
  (root), `test_version_consistent.py::test_changelog_leads_with_the_current_version` (0.12.0
  heading vs `version.py` 0.11.11, as before), and four with no `zstd` binary in this container
  (`test_binary_install.py::test_extract_rejects_hostile_archive[3 ids]`,
  `test_binary_channel.py::test_doctor_is_quiet_for_a_healthy_binary_install`).
- `ruff check lhpc testlab`: All checks passed. `ruff check tests --select F,E9`: All checks passed.

### Adversarial self-review of the correction

Found and fixed before the push:
- The first draft of the plan's risk line said an unreadable-target symlink at an owned-dir path now
  keeps the receipt. The code checks link-ness first and leaves a symlink alone, as before. Reworded.
- The retirement test first asserted `receipt_state == "valid"`; after the files are unlinked the
  receipt reads `unsafe`, so it now checks the receipt leaf is still there, as the round-1 test does.
- The new test used `os.path.stat` (a private import of `posixpath`); it imports `stat`.
- The commit message of `075519e` said only `probe_exists`; reworded (`--fixup=reword:`) to name
  `probe_stat`.

Noted, not changed:
- A symlink at an owned-dir path is still skipped and the receipt dropped (pre-existing; nothing to
  `rmtree` through a link).
- `probe_stat` has no return annotation (`probe_exists` has one); the docstring states the tuple.

Base `integration/1.0` (042716f). Branch commits: `6753747` plan, `c6cdaac`, `075519e`, `f8badb9`
code, then this report. Interpreter: Python 3.11 (the new tests also pass on 3.13).

## Commits

| sha | subject | files | tests (red-before → green) |
|---|---|---|---|
| c6cdaac | Q2: probe_exists — the fail-closed existence probe | `lhpc/core/runtime_fs.py`, `lhpc/core/service_binary_ops.py` (`_receipt_leaf_present` on the helper), `tests/core/test_existence_probe.py` | helper tests, 6 cases. RED against 6753747's `lhpc/`: `6 failed` (no `probe_exists`). GREEN: `6 passed`. `_receipt_leaf_present` keeps its behaviour: `tests/install/test_binary_channel.py` 133 passed (incl. `test_retire_refuses_a_file_whose_lstat_fails`). |
| 075519e | Q2: the controller's safety paths never read an unexaminable path as absent | `install.py`, `service_binary_ops.py`, `service_boot_restore.py`, `boot_restore.py`, `config.py` (one probe), test, CHANGELOG, `docs/architecture.md` | `test_an_unexaminable_path_is_never_absent`, 9 sites × {EIO, EACCES}. RED against c6cdaac's `lhpc/`: `18 failed, 6 deselected`. GREEN: `18 passed`. |
| f8badb9 | Q2: the firewall helper reads an unexaminable journal as present | `lhpc/core/firewall_helper.py`, `tests/host/test_firewall.py`, CHANGELOG (upgrade note), `docs/architecture.md` | `test_a_journal_that_cannot_be_examined_is_not_absent[EIO,EACCES]`. RED against 075519e's `lhpc/`: `2 failed`. GREEN: `2 passed`. |

How red-before was run: `git checkout <parent> -- lhpc && python -m pytest -q -p no:cacheprovider <test> ; git checkout HEAD -- lhpc`.

Red-before, by site. Two of the nine controller sites were not silent before. `Path.exists()` on 3.11–3.13
*raises* on EIO/EACCES, so `dirty_report`/`extra_files` let an uncaught `OSError` escape. Now they return the
typed refusal: dirty, or `None`. The other seven read the error as absence: retire dropped the receipt, the
switch took over the tree, boot restore and the config journal reported "absent".

## Deviations from the plan

1. Bootstrap `local.toml`/`secrets.toml` (install.py:263/268) are NOT changed. On every supported Python,
   `Path.exists()` raises on EIO/EACCES, so `apply_bootstrap` already records `failed`. No red test is possible.
   The change would have added code with no effect.
2. The plan named `config.py:1877` as "leave" and kept it. The commit list has 3 code commits, as planned.

## Line counts and dependencies (guardrail)

`lhpc/`: +71 −25 (net +46). runtime_fs 958→976 (helper +16 with docstring), service_binary_ops 988→995
(`_receipt_leaf_present` 5 lines shorter), install 2323→2329, service_boot_restore 645→651, boot_restore 338→340,
config 2151→2152, firewall_helper 1479→1485. The code grows because each site now has an explicit `unknown`
branch. That is the smallest form that meets the completion criterion.

Dependencies: no new imports between modules. `install.py` imports `runtime_fs` locally, as it already does in
other places. `boot_restore.py` already imported `runtime_fs` and loses a local `import os`. `service_*` gain no
new `self.*` method. The firewall helper stays stdlib-only, with the rule inline.

Design: one plain function and a `Literal` with a message field. No class, no enum type, no registry. Why
`guard_state` is not reused: it needs the path inside the runtime root, and it reads a missing parent as
`unsafe`. A fresh box has no `state/running/`, so boot restore would turn unsafe. Changing `guard_state` would
change the uninstall guard.

## The 6-point block

1. **Contracts.**
   - `runtime_fs.probe_exists(path) -> (state, why)` (runtime_fs.py:735). `state` is one of
     `present|absent|unknown`; `why` is `""` unless the state is `unknown`. It never raises.
   - `Installer.dirty_report` (install.py:881): an unexaminable `.git` now gives a truthy report with
     `blocks_update()` true. The new tracked entry `"(cannot examine .git (…) — treating as dirty)"` follows
     the same pattern as the existing `"(git status failed — treating as dirty)"`.
   - `Installer.extra_files` (install.py:939): `None` means "could not take the inventory" (existing contract:
     the caller refuses).
   - `_rel_files_under` (service_binary_ops.py:609) may now raise `OSError`. Its only caller,
     `_artifact_only_dir` (:745), turns that into `False`.
   - Retire (service_binary_ops.py:934/:950) returns the existing INCOMPLETE `ActionResult` and keeps the
     receipt.
   - `_boot_marker_view` (service_boot_restore.py:70): `unsafe` uses the existing tri-state.
   - `boot_restore.load_journal` (boot_restore.py:134): `"unsafe:unreadable (<err>)"`, an existing state form.
   - `config.recover_config_transaction` (config.py:1792): `""` means recovery-required (existing contract).
   - `firewall_helper._read_json_state` (:991): `present-invalid` (existing).
   - No lock order, persisted format or exception type changed.
2. **Invariants + tests.**
   - *Path containment / fail closed*: `test_existence_probe.py` (helper and sites).
   - *Locally added files are never collateral*: `extra-files`, `dirty-report` and `switch-artifact-only` cases.
   - *Config as a transaction*: the `config-journal` case, plus the existing
     `test_a_journal_path_that_cannot_be_examined_refuses_the_writer`.
   - *Boot restore replays only saved configuration*: the `boot-*` cases.
   - *Binary receipt ownership*: the `retire-*` cases.
   - *Firewall fail-closed*: the new `test_firewall.py` case.
   - All of `tests/core` passes.
3. **Known failure classes.**
   - Fakes: the injector wraps the real `os.lstat`/`os.stat` with the same call signature. It raises only for
     one exact path string, and only when no `dir_fd` is passed.
   - Probes: EIO and EACCES at every site; ELOOP and NUL in the helper; ENOTDIR on a parent is absent.
   - KeyboardInterrupt: the probe catches only `OSError`/`ValueError`. The `os.walk` `onerror` re-raises the
     walk's `OSError`, never a `BaseException`.
   - Same decision across CLI / web / detached job / boot-restore: every site is in the core function that all
     of them call. The adapters are untouched.
   - Stacked conflicts: no file on Q1's list (jobs/hmac/autoinstall), Q4's list (service_system/pki) or Q5's
     list (web static). `config.py` changes only in the probe and its one comment line inside
     `recover_config_transaction`.
4. **Test rules.**
   - Every new test is red before and green after (table above).
   - Decision strings are compared exactly: `load_journal` against the full `unsafe:unreadable (…)` string,
     refusals by their path field.
   - No return value goes unchecked.
   - No network.
5. **Whole directories**, foreground, on HEAD f8badb9:
   - `tests/core`: `2069 passed, 6 skipped`.
   - `tests/install tests/repo tests/host`: `96 failed, 2183 passed, 9 skipped`. Every failure also fails on
     the base 042716f:
     - 92 in `tests/install/test_bootstrap_deps.py`: the container runs as root and the script refuses without
       a non-root operator.
     - `test_firewall.py::test_receipt_reader_rejects_nonroot_symlink_and_unsafe`,
       `test_network_controls.py::test_rule_helpers_and_single_install_site` and
       `test_power_controls.py::test_dependency_entry_probe_and_bootstrap_exclusion`: root environment.
     - `tests/repo/test_version_consistent.py::test_changelog_leads_with_the_current_version`: `version.py` says
       0.11.11 under a 0.11.12 heading already on the base. The release commit fixes it.
   - `ruff check lhpc testlab`: All checks passed. `ruff check tests --select F,E9`: All checks passed.
   - No function signature changed. A grep of tests/ and testlab/ for `_rel_files_under`, `_artifact_only_dir`,
     `_receipt_leaf_present` and `probe_exists` finds only the new test.
6. **Adversarial self-review.** Found and fixed:
   - A stale "`os.path.lexists` does not" comment in config.py, reworded.
   - The bootstrap edit had no effect, so it was reverted (deviation 1).
   - The test's `monkeypatch.undo()` also undid the case's `binary_target` patch. The injector now has its own
     `MonkeyPatch`.

   Noted, not changed:
   - Retire's `still_there` now treats a path `under()` cannot resolve as still present. Before, the uncaught
     `PathContainmentError` escaped, so this is the safer side.
   - ~~`_rel_files_under` keeps `os.path.isdir(base)` after a `present` probe.~~ Wrong — gate 1 round 1
     finding 2; fixed in Correction 1.
   - The firewall helper's bytes change, so boxes read *Update required* (upgrade note in the CHANGELOG). The
     change is its own commit, so it can be held back.

   The docs (one sentence in architecture.md) and this report describe exactly the diff.
