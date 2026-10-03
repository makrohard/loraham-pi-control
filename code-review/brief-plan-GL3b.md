# CLOUD BRIEF · PLAN for fix group GL3b of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-GL3b.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** Install-journal design: the ctime-bound ident goes stale across a rename made before the journal refresh — the plan decides between refreshing the journal before each rename and relaxing the ident for one state/leaf pair; candidate cleanup on recovery; power-loss ordering on ext4 reasoned, not assumed.

### CR3-1
- where: `lhpc/core/install.py:1772 (journal refreshed only at 1796-1797; recovery refuses at 1583-1586 and 1627-1629)` · severity kept S3: narrow crash window, but the managed source stays missing and all source mutation
- claim: `_recover_scan` docstring (1221): "Finish or roll back each INTERRUPTED source activation so the active source is never left missing". architecture.md, Source transactions: "journalled at every step".
- defect: The `planned` journal records the prior's v5 ident `[dev, ino, ctime]` while the prior is still at `dest`. The rename `dest -> .prev` at 1772 changes the prior's ctime (the comment at 1793 says so). The journal only gets the new ctime at 1797, after `txn.fsync()`. If the process dies or power is lost between 1772 and 1797, recovery finds `dest` absent and `.prev` failing `ident_matches`. It returns "archived prior could not be proven … (everything retained)". The managed source stays missing, so the stack cannot start. The retained journal also blocks every source mutation on the box (`_pending_journals`) until an operator renames `.prev` back by hand.
- how to see it: Write a v5 `planned` journal whose idents are taken while the prior is still at `dest`, then `os.rename(dest, prev)` and call `recover_source_activations()`. Result: `dest` absent, `.prev` and the journal retained. A test that expects the prior restored would fail.
- verifier: CONFIRMED — The `planned` journal holds the prior's ident taken at `dest` (install.py:1741). The rename is at 1772, the refresh only at 1796-1797, and recovery checks the v5 ctime at 1582-1586. Scratch: planned journal, `os.rename(dest, prev)`, then `recover_source_activations()`: "archived prior could not be proven … (everything retained)", with `dest` absent. A refreshed `prior-archived` journal recovers (control).

### CR3-2
- where: `lhpc/core/install.py:1839 (journal refreshed only at 1853-1854; recovery refuses at 1559-1564)` · severity kept S3: the new tree is active, no loss. `.prev` and the journal stay, and source mutatio
- claim: Same claim as CR3-1: recovery completes an interrupted activation, and the journal is cleared once the prior is proven removed.
- defect: Same pattern at promotion. `staging -> dest` (1839) changes the candidate's ctime. The `activated` journal with the refreshed ident is written only at 1854. A crash in between leaves the new tree active and the record writable. But `_prev_cleanup_ok(..., active=(dest, stale candidate ident))` fails at 1427, so recovery returns "archived prior could not be removed or was substituted". The journal and `.prev` stay forever, and all source mutation box-wide is blocked until manual cleanup. No data loss.
- how to see it: Use a `prior-archived` journal with idents taken before promotion, `os.rename(staging, dest)`, then `recover_source_activations()`. Result: journal and `.prev` remain, with recovery-required.
- verifier: CONFIRMED — The candidate ident goes stale with the promotion at install.py:1839; the refresh is only at 1853-1854. `_prev_cleanup_ok` fails at 1426-1427, so recovery returns at 1562-1564. Scratch: `prior-archived` journal, `os.rename(staging, dest)`, then recovery: "archived prior could not be removed or was substituted (journal + prior retained)".

### CR3-3
- where: `lhpc/core/install.py:526 / 724 (candidate created), 1565 (recovery clears the journal)` · severity kept S3: no data loss, but each interrupted update can leave a full orphan clone
- claim: Module docstring: a failed transaction leaves the active source untouched. `_cleanup_owned_staging` is "THE authoritative handle-safe staging cleanup". architecture.md: "clones a candidate beside the destination … journalled at every step".
- defect: A full clone is staged as `.<name>.candidate-<pid>-<ns>` long before the journal exists: the clone can take up to `_CLONE_TIMEOUT_S` = 900 s. A kill, reboot or power loss during the clone leaves the candidate with no journal. A crash after journal creation but before the archive (state `planned`, `dest` intact) is cleared by recovery as "active source intact" (1565), and the candidate is left behind. No code path ever removes such a candidate: nothing outside `install.py` handles `candidate-` leaves, and the names are unique, so later updates never collide with it. Each interrupted update can permanently waste a whole clone (RadioLib is about 114 MB) on the SD card.
- how to see it: Write a v5 `planned` journal next to an intact `dest` plus a staged candidate, then `recover_source_activations()`. The journal is removed, the candidate is still on disk, and it is still there after a later successful `adopt_source(force=True)`.
- verifier: CONFIRMED — The candidate is created at install.py:526/724, before the journal (1742). A `planned` journal with `dest` intact is cleared at 1565. `candidate-` appears nowhere in lhpc/ outside install.py (only a regex at 1087). Scratch: recovery returned "active source intact" and removed the journal; the candidate was still on disk after a later successful `adopt_source(force=True)`.



## What the plan must contain (≤ 200 lines, tables where possible)
1. **Analysis per finding**: the code path today (file:line), the defect, callers affected, what a test sees today.
2. **The change per finding**: exact function(s), the new behaviour in one sentence, the expected diff size, the
   risk (what working path could break and how the plan rules it out). If two findings share a fix, say so.
3. **Tests**: per finding the test module::name, what it asserts, why it is red before.
4. **Docs/CHANGELOG**: the sentences to change (file:line) and the CHANGELOG line.
5. **Order and commits**: one commit per finding, subject `<id>: <what>`; the order if one depends on another.
6. **Live proof**: whether a row on the Pi 5 is needed (an operator-visible path) and what it would show.
7. **Open questions** for the maintainer, each with your recommendation (≤ 5).
8. **Self-check**: re-read every claim against the code once more; list what you could not verify.

## Commit identity (the maintainer's rule — a direct violation otherwise)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits in this repository: git log -1 --format=%ae --author=makrohard origin/main>`, and commit with that identity; no Co-Authored-By, Claude-Session or any AI-attribution line in any message. After each commit check `git log -1 --format='%an %cn%n%B'` shows makrohard twice and no such line; fix it with `git commit --amend --reset-author --no-edit` before you push.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').
