# Code report: trackF-c1 (the binary channel without nagging, packet C1: the measured MeshCom case)

Branch `cons/trackF-c1-r1` on `origin/dev` = `f0216560`. Plan: `PLAN-trackF` v3.2 (gate 1 GREEN WITH NOTES, round
3; gate 2 W2a: the plan RED on C2/B1/the web client, the C1-only split for 0.12.10 judged sound), §F1 and §F2.1. Scope ruled for 0.12.10: C1 only; `--accept-pin-mismatch`
(D1) and the newest-compatible rule stay packet C2, landing with lhpc-binaries' B1. One code commit.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| 8af06e3f | trackF-c1: a binary install moves a clean run-script checkout to its pin | lhpc/core/service_binary_ops.py, lhpc/core/service_auto_install.py, tests/install/test_binary_install.py, tests/install/test_auto_install.py, CHANGELOG.md | install: `test_inside_an_auto_install_run_a_clean_clone_at_an_older_pin_is_moved_to_the_pin` (with `locked=True`: the adoptions are exactly `[("meshcom-qemu", "pinned", force=True, locked=True, <manifest pin>)]`, no `update()` call, the install reaches the download); `test_a_dirty_clone_at_an_older_pin_is_still_refused_not_moved[locked=False/True]` (no update, no adoption, the old refusal); `test_outside_an_auto_install_run_a_clean_clone_left_at_an_older_pin_is_refused_as_before` (CLI path: the pre-lock update leaves it stale → the old refusal, no adoption); `test_a_refused_binary_row_names_the_refusals_own_command_first` (the row names `run \`<the refusal's next_commands[0]>\`, or ` before the source command) | yes, before the code on 771d1a37: the auto-install case `[] == [('meshcom-qemu', 'pinned', True, True, 'c2335d12f…')]` (no adoption: the refusal); the row case (the row began with "install from source with"). The dirty case passes before and after (the unchanged refusal, now also under `locked=True`). The CLI guard is red on this commit's first version (ff18d159, which re-adopted there too: the install reached the download) and green now. On hardware: see "Live row" |

## The change

- `binary_install`'s `clone_required` loop (`service_binary_ops.py:283-305`): for a present checkout proven ours
  (`verify_identity`), `stale` = HEAD ≠ manifest pin; `dirty_report` is read once (one `git status` earlier than
  before on the stale+dirty path; harmless); stale AND (dirty OR not `locked`) → the existing "is at X, the pin is Y"
  refusal (unchanged); dirty → the existing local-changes refusal (unchanged); clean at the pin → reuse
  (unchanged); clean and stale INSIDE an auto-install run (`locked`) → falls through to the adoption the loop
  already runs for a MISSING checkout, now with `force=dest.is_dir()` (`:321`).
- `adopt_source(force=True)` on an existing directory, `install.py` on this base (verbatim excerpts):
  ```
  447:            if kind == "dir" and force:
  458:                rec, why = source_registry.verify_identity(
  459:                    self.paths, self.system, self.config, comp, dest,
  460:                    components=self._path_consumers(spec.path), handle=prior)
  475:                dirty = self.dirty_report(Path(prior.pinned_path()), spec.path)
  476:                if dirty.blocks_update():
  502:             return self._stage_and_activate(comp, source, action, dest, spec, local,
  ```
  i.e. capture the leaf, prove identity against the captured inode (unproven → failed, nothing moved), refuse
  local modifications, then the journalled staged activation every source update uses (`_stage_and_activate`,
  `:532`). The loop's own `dirty_report` (any tracked change or untracked file) runs before and is stricter. `update()` is not called: no known-working clearing, no build follow-up, no dependency scope.
  `locked=_guard_held` is the existing rule (an outer auto-install boundary or this call's own guard).
- The CLI path (`binary_install` without `locked`) is unchanged: its pre-lock `_stale_clean_clones` + `update()`
  move (3aed16fe) still runs first; if that leaves the checkout stale, the loop refuses as before (gated on
  `locked`, new guard test).
- Auto-install's blocked row (`service_auto_install.py:1271-1275`): `own = br.next_commands[0]` (else the source
  command); a pin mismatch keeps "update LHPC first (lhpc self-update --apply), or "; any other own command reads
  "run \`<own>\`, or "; the source command alone reads as before.

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 31 lines   OK
docs         0 files, +0 lines; fact "run-script checkout to its pin" -> none   OK
tests        4 new tests; layers tests/install=install; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      service_auto_install.py 1875->1875 lines (budget 1875), 16->16 public (budget 16); service_binary_ops.py 1077->1076 lines (budget 1077), 5->5 public (budget 5); family net -1 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The 6-point block
1. **Contracts.** `binary_install(stack_id, apply, *, locked, accept_pin_mismatch)` unchanged signature; for a
   `clone_required` component: present + proven + clean + stale → re-adopted at `pin_commit` (`pinned_expected`),
   result as a fresh adoption ("adopted pinned source for <cid> (run scripts)" note) — inside an auto-install run
   only; the auto-install boundary already holds `src/meshcom-qemu-raspi` (the shipped missing-clone adoption
   under `locked=True` is the same call; this rests on code outside the diff). Auto-install row text as above.
2. **Invariants + tests.** A run-script checkout that the artifact executes is always the manifest pin, clean and
   ours before the overlay: unproven/dirty refused (existing + new `locked` case), stale clean moved (new).
   Nothing installs over a dirty or foreign tree.
3. **Failure classes.** Fakes with the real signature (`adopt_source(comp, *, force, source, pinned_expected,
   locked)`); a failed adoption returns the loop's existing refusal (`:325-331`, source command); Ctrl-C: the
   adoption's own journal recovery (unchanged code); CLI/web/job: CLI and the console's install job go through the
   unchanged pre-lock move, auto-install (CLI and console) through the loop.
4. **Test rules.** Typed fields (the adoption tuple, the row's command token), no sentence compared exactly; no
   sleep, no network.
5. **Directories + ruff.** `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/install tests/repo` → `2287 passed, 1 skipped` (on 8af06e3f); `ruff check lhpc testlab`, `ruff check tests
   --select F,E9` → All checks passed!
6. **Adversarial self-review.** Found and fixed (second read): the first version also force-re-adopted a clean
   stale checkout on the CLI path when its pre-lock move had not reached the pin; now gated on `locked`, with a
   guard test; the CHANGELOG says "a clean … checkout". Earlier: the module budgets (both files at budget) — comments shortened,
   net −1 / 0; a `startswith` on a row sentence replaced by token order. Checked: the auto-install boundary already
   covers `src/meshcom-qemu-raspi` (the missing-clone adoption under `locked=True` is the same call, shipped).

## Live row (Pi 5, lhpc-0ae1, aarch64 Desktop image)
Start state 0.12.9 eb9673a4, MeshCom binary-installed, `src/meshcom-qemu-raspi` at c2335d12f.
Red-before (0.12.9, the box's own code): checkout re-adopted at 3c30c07e4 through `adopt_source(force=True)`
(clean, recorded); a meshcom-only binary auto-install → `[blocked] meshcom: Binary install of 'meshcom' blocked:
src/meshcom-qemu-raspi is at 3c30c07e4, the pin is c2335d12f — … — install from source with: lhpc install meshcom
--source pinned --yes` (runs 194ecdc3…, 39a604c7…), identical to the e293 field log.
C1 half: run on this version's code (8af06e3f) after the second read; its lines are in the DELTA file. (The first version, ff18d159, passed the same row at 04:01Z: `[ok]`, checkout at c2335d12f; recorded there too.)
Box changes so far: `src/meshcom-qemu-raspi` re-adopted at 3c30c07e4 (+ its ownership record), two auto-install run markers. No keys, no files, no RF.

## Numbers, measured
| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | f0216560 |
| diff | `git diff --stat origin/dev 8af06e3f \| tail -1` | 5 files changed, 84 insertions(+), 24 deletions(-) |
| budgets | checker modules line | service_binary_ops.py 1077→1076, service_auto_install.py 1875→1875 |
| e293 field log | `grep blocked fieldtest-e293/runlogs/auto-install-11c3b495.log` | the same refusal line |
