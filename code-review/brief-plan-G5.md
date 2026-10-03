# CLOUD BRIEF · PLAN for fix group G5 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-G5.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
### CR1-3
- where: `lhpc/core/daemon_control.py:512` · severity kept S2: a daemon refusal of an unconfirmable key is shown as sent/applied
- claim: `apply_set` docstring (495-503): "rejected … -> (False, False, …)". The comment at 38-42 says the daemon answers every line with `OK`/`ERR`. `apply_daemon_params` (2139-2140): "ok=True only when every attempted set is applied". Safety model: "Truthful outcomes".
- defect: The SET goes out through `system.unix.send`, which is fire-and-forget (closes without reading), so the daemon's `ERR` is thrown away. For every key that cannot be read back (POWER, FREQ, SF, BW, CR, PREAMBLE, SYNC, CRC, LDRO), a daemon refusal comes back as `(True, False, "SENT")`. `apply_daemon_params` counts it in `applied` (2164-2166) and can report "applied n/n". The start path logs `[ok] … sent` (2054-2056). `daemon_set` reports "SENT (unconfirmed)". `live_power_error`'s docstring (428-429) already describes this exact case: the SX127x refuses `POWER=0` "while the unconfirmable SET is reported 'sent'".
- how to see it: A daemon stub that replies `ERR RANGE` to `SET SF=12`: `apply_set(..., "SF", "12")` returns ok=True. Red test: "a daemon ERR ack makes apply_set fail".
- verifier: CONFIRMED — `apply_set` sends via `system.unix.send` (daemon_control.py:512); `RealUnixClient.send` reads no reply (probes/backends.py:754-760). This is the live path (callers service_params.py:77, 112, 145, 3466; see U-4). Scratch: a fake answering `ERR RANGE` still gave `(True, False, "SF=12 SENT but UNCONFIRMED…")`.



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

## Review-packet header (mandatory wording)
Every review packet you write MUST start with: a request paragraph that names what to judge and the answer form — `| commit | verdict (OK / FINDING) | what |` and a final line GREEN / GREEN WITH NOTES / RED — and the sentence "This file is your whole input: you have no repository access; use no connector, tool or web lookup."
