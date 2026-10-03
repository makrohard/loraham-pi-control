# Code report — Q2: `probe_exists()`, one fail-closed existence probe

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
