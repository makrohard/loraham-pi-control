# PLAN-Q2 — `probe_exists()`: one fail-closed existence probe for every safety path

Base: `integration/1.0` (042716f). Files: `lhpc/core/runtime_fs.py` (helper), `install.py`,
`service_binary_ops.py`, `service_boot_restore.py`, `boot_restore.py`, `config.py` (two probe
lines only — Q1 owns the recovery result type), `firewall_helper.py`, their tests, CHANGELOG,
`docs/architecture.md` (one sentence).

## The helper (runtime_fs.py, beside the other no-follow leaf helpers)

`probe_exists(path) -> (state, why)`, `state` a `Literal["present", "absent", "unknown"]`, `why`
the error text ("" unless unknown). `os.lstat` (no-follow: a dangling or escaping symlink is
present); `absent` ONLY on `FileNotFoundError`/`NotADirectoryError`; any other `OSError` (EIO,
EACCES, ELOOP, ENAMETOOLONG) or a `ValueError` (NUL byte — `os.path.lexists` reads that as
absent too) is `unknown`. Plain function, one Literal + one message field; no class, no enum
type. Guardrail check: it REMOVES duplication — `_receipt_leaf_present` (service_binary_ops.py:842)
becomes a one-line use of it.

`probe_stat(path, *, follow=False) -> (state, why, st)` (added in commit 3, beside it): the same
rule plus the stat result it read (`st` None unless present); `follow=True` uses `os.stat` (a
dangling link is absent). A decision that follows the probe takes the kind (directory, symlink)
from `st`, never from a second `os.path.isdir`/`islink`, which reads an error as "no".

## Inventory — `grep -nE 'lexists\(|\.exists\(|isfile\(|isdir\('` over the FILES

REPLACE (safety: an OSError read as absence deletes, overwrites or proceeds):

| site today | decision on a false "absent" | with `unknown` |
|---|---|---|
| install.py:889 `dirty_report` `.git` exists | reports CLEAN → uninstall/clean/update proceed | dirty ("cannot examine …"); a `.git` symlink that does not resolve: dirty (named, see below) |
| install.py:949 `extra_files` `.git` exists | `()` → update archives the prior with no carry | `None` (caller refuses); same for an unresolvable `.git` symlink |
| service_binary_ops.py:616 `_rel_files_under` isdir (+ `os.walk` swallowing errors) | `[]` → `_artifact_only_dir` True → foreign dir treated as the artifact's leftover | `probe_stat(follow=True)`: unknown raises → `_artifact_only_dir` False; is-dir from its `st` |
| service_binary_ops.py:930 retire `still_there` exists | receipt dropped while files remain (unowned) | still present → INCOMPLETE, receipt kept |
| service_binary_ops.py:946 owned dir isdir + islink | dir skipped, receipt dropped (unowned venv) | `probe_stat` (lstat: link?) then `probe_stat(follow=True)` (dir?): unknown in either → INCOMPLETE, receipt kept |
| service_binary_ops.py:842 `_receipt_leaf_present` (already lstat) | — | same behaviour, uses the helper |
| service_boot_restore.py:75 running-band marker lexists | `absent` evidence | `unsafe` |
| service_boot_restore.py:86 last-start candidate lexists | `absent` evidence | `unsafe` |
| boot_restore.py:141 `load_journal` lexists | `absent` → restore runs | `unsafe:unreadable (why)` |
| config.py:1810 `recover_config_transaction` lexists | `None` = "no journal" | `""` = recovery required |
| firewall_helper.py:999 `_read_json_state` lexists | journal `absent` → `recover()` True → apply proceeds | `present-invalid` (inline: stdlib-only module) |

LEAVE (display/convenience, or an error already refuses/keeps):
install.py:263/268 bootstrap `local.toml`/`secrets.toml` `not dest.exists()` — dropped after
drafting because `Path.exists()` RAISES on EIO/EACCES on every supported Python (3.11–3.13), so
`apply_bootstrap` already fails the action and no red test is possible; install.py:228/232
plan status text; :291 doclink (`os.symlink` itself never clobbers); :2237-2260
post-clone sanity (`False` = clone failed). service_binary_ops.py:622 (an unstat-able entry is
listed as a file → not owned → not artifact-only: fails closed); :662 staging rmtree (error =
keep); :705 build cwd; :719 CLI missing → error; :922 unlink guard (error = keep, then :930
reports it). config.py:1001 web-session key persistence (error = ephemeral key, nothing written);
config.py:1877 eager startup recovery (absent and unknown both leave it to `config_lock`, which
already refuses on EIO — no observable difference, no red test possible). selfupdate.py:136
(error = no repo root = self-update unavailable); service_selfupdate.py:40 log tail; :96/:257
dependency rows; :1096 helper exe text; :1295 `.git` missing → refuses. firewall_helper.py:1390
transition unlink (error = keep). service_firewall.py:344 log link, :412 units-enabled row.
Out of the grep but scanned (`is_dir`/`is_file`/`is_symlink` in FILES): none decides a delete or
an overwrite on absence (binary_ops:247/532 hand absence to the locked adoption, which proves it
with `leaf_kind`).

## Commits

1. `Q2: plan`. 2. `Q2: probe_exists — the fail-closed existence probe` (helper, its unit test,
`_receipt_leaf_present` on it). 3. `Q2: the controller's safety paths never read an
unexaminable path as absent` (the REPLACE rows except `_receipt_leaf_present` and the firewall
helper — 9 sites — plus `probe_stat`, one parametrised test, and one stat-only test per kind
decision). 4. `Q2: the firewall helper reads an unexaminable journal as present` (inline
lstat; CHANGELOG upgrade note). 5. report.

## Tests

`tests/core/test_existence_probe.py`: the helper (present / absent / dangling symlink present /
ENOTDIR absent / EIO+EACCES unknown with errno text), and ONE parametrised test over the
replaced sites × {EIO, EACCES}: an injector makes `os.lstat` AND `os.stat` raise for exactly the
site's path (so the `exists`/`isfile` sites are red before too), drives the site through its
public seam (`dirty_report`, `extra_files`, `binary_retire(force=True)`,
`switch_source_plan`, `load_journal`, `recover_config_transaction`; `_boot_marker_view` as
test_boot_restore.py already does for private views) and asserts the refusal/keep state AND that
the file is still there. The two kind decisions (`_rel_files_under`, the owned-dir retirement)
get one more test each with an injector that fails `os.stat` ONLY (lstat answers), so the
`isdir` after a present probe is caught too. The dangling-`.git` change: `test_a_git_symlink_that_does_not_resolve_is_never_clean`
(dangling, loop; exact `DirtyReport` and `None` asserted, no git call, against a fake git that
answers like a clean enclosing repository) and `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading`
(no `.git` → `DirtyReport()` / `()` with no git call; a resolving `.git` symlink → git consulted). Firewall helper: one case in
`tests/host/test_firewall.py` (`recover()` is False and the journal stays). Red-before: run the
new tests against commit 2's tree.

## Risks and how they are ruled out

- Behaviour change without any I/O error, deliberate: a dangling `.git` symlink is treated
  as present and the tree is not reported clean; before, it was ignored (`Path.exists()` follows
  the link, so the old code read "not a checkout": `DirtyReport()` / `()`). Reproduced on the
  base and on round 2's head (report, Correction 2). Why fail-closed: the inventory's question
  is "may this tree be replaced or removed without losing the operator's work?", and a `.git`
  that is there but leads nowhere does not answer it; it is the batch's own rule (not proven
  absent → not absent). It is also not handed to git: given a `.git` it cannot use, git walks up
  to the first enclosing checkout and reports on THAT one (round 2's head did exactly this:
  dirty when no enclosing repository exists, but CLEAN / `()` under a clean enclosing one). So
  `install._git_marker_anomaly` reads `.git` with `probe_stat` (lstat); a symlink is followed
  (`probe_stat(follow=True)`): absent → ".git is a dangling symlink", unknown (ELOOP for a loop,
  EIO, EACCES) → ".git is a symlink that cannot be followed (why)"; both make `dirty_report`
  return `DirtyReport(tracked=("(<anomaly> — treating as dirty)",))` and `extra_files` `None`,
  with no git call. Same for a `.git` symlink loop (old: `Path.exists()` reads ELOOP as False).
  A `.git` symlink that resolves goes to git exactly as before. Call sites whose result changes
  for such a `.git` (all now see "changed" / "no inventory"): install.py `_adopt_locked` (update
  refused), `_stage_and_activate` (final dirty check blocks the archive; the carry refuses),
  `_prev_dirty_scan` (archived prior retained), `_prev_extras` (`None`);
  service_binary_ops.py `binary_install` (refused: local changes), `_stale_clean_clones` (not
  listed as a clean clone), `switch_source_plan` (refusal); service_maintenance.py
  `_remove_source_leaf` (uninstall refuses, both checks; Clean skips the check, unchanged);
  service_auto_install.py `_reconcile_group` (blocked). Where an identity check
  (`verify_identity`) runs first, it may already refuse; the dirty result changes either way.
- Other sites without an I/O error: ENOENT/ENOTDIR keep "absent"; the boot-restore, config and
  firewall sites used `lexists` (lstat) before, so a dangling symlink reads the same; retire's
  `still_there` moved from `os.path.exists` (follows) to lstat — a dangling symlink at a receipt
  path is "still present" now, but the unlink loop just before it unlinks any symlink
  (`islink`), so the result differs only if one remains after that loop (an unlink that failed
  is already INCOMPLETE). A symlink at an owned-dir path is still left alone (never `rmtree`d
  through), as before. `_rel_files_under` follows like `os.path.isdir` did; a base it cannot
  stat other than ENOENT/ENOTDIR (ELOOP included) now raises instead of listing `[]`, so a
  symlink loop at a switch target is no longer judged the artifact's own leftover (a second change without an
  I/O error, covered by the CHANGELOG's "does not take over a folder it cannot fully read").
- Firewall helper bytes change → boxes with the firewall read *Update required* until the
  operator re-applies (F36 makes the product say so). CHANGELOG carries the upgrade note.
- Injector over-reach: it raises only for one exact path string, no `dir_fd` call is affected.
- Q1 conflict: config.py touched on exactly one line in `recover_config_transaction`.

## Open questions (with recommendation)

1. Firewall helper change forces an operator re-apply on upgrade. Recommendation: ship it (the
   journal is the one firewall state whose false "absent" lets an apply run over an interrupted
   one); it is its own commit, so it can be held back to a release that changes the helper anyway.
2. `os.walk` error swallowing in `_rel_files_under` is the same class one line below the isdir;
   fixed in the same site (onerror raises). Recommendation: keep.
