# CLOUD BRIEF · PLAN for fix group G4 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-G4.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** DECIDED: REFUSE a daemon-param apply (and the TX test) on a band that serves another running stack, with a typed message naming the stack; never clamp silently. U-2 (TX-test fallback) was already fixed in 0.11.10 — check and reuse its band logic.

### CR1-1
- where: `lhpc/core/service_lifecycle_ops.py:2153` · severity corrected S1→S2: values are validated per band and POWER is live-gated (service_params.py:
- claim: Band arbitration: only one app stack per band. `_ensure_daemon` (1975-1984) says a band that already serves ANOTHER running stack "must NOT be reconfigured". `daemon_params_view` (2078) clamps the band to the bands the stack can use.
- defect: `apply_daemon_params` takes `b = _effective_daemon_band(target, band)`, which returns any *active* band the caller asks for. It does not check the target's own bands or the band it runs on. The only other check is `stack_running(sid)`. So the target's whole profile (TXMODE, MODE, FREQ/SF/BW/CR, CAD*, POWER) is pushed live onto a daemon band that may belong to a different stack: TXMODE=DIRECT from voice can land under a MANAGED client, and another stack's frequency and spreading factor can retune a band someone else is using.
- how to see it: Reproduced in a scratch test: `apply_daemon_params("meshcom", "868")` with meshcom (fixed 433) stubbed as running. `daemon_params_view` clamps the band to 433, yet 17 SETs went to `/tmp/loraconf868.sock` and `data.band == "868"`. Reachable by `lhpc config meshcom --band 868 --apply-daemon` and by web POST `/stacks/<id>/daemon-params/apply` with `band=868`. The same applies to a band-switchable stack (kiss, voice) asking for the band it is not on.
- verifier: CONFIRMED — `apply_daemon_params` (service_lifecycle_ops.py:2153) takes `_effective_daemon_band` (service_params.py:217-227), which returns any active band, and has no own-band check unlike `_ensure_daemon` 1975-1984. Scratch: meshcom running, 433+868 active; `daemon_params_view` clamps to 433, yet `apply_daemon_params("meshcom","868")` sent 15 SETs to the 868 socket with `data.band=="868"`.



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
