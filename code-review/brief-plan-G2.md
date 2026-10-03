# CLOUD BRIEF · PLAN for fix group G2 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-G2.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
### CR2-2
- where: `lhpc/core/service_params.py:3361` · severity kept S2: a running stack whose saved settings changed shows no RESTART REQUIRED line
- claim: docs/architecture.md "Config as a transaction" and `save_config_bundle` (l.1407-1412): a restart/build-mode change saved while the stack runs writes the restart-required marker atomically, "a running stack can never hold changed restart-mode settings without the warning".
- defect: `reset_config` clears run/file/autostart/`use_gps`/`rf_log` keys through `update_stack_config` and never computes or writes a restart-required marker. Resetting a RUNNING stack leaves its processes on the old values with no RESTART REQUIRED line in status/dashboard.
- how to see it: Start any stack with a non-default restart-mode param saved, then `lhpc config <stack> --reset --yes`; `restart_required(<stack>)` stays None. A test "reset of a running stack writes the restart marker" would be red.
- verifier: CONFIRMED — `reset_config` (service_params.py:3332-3372) never computes or writes a marker, unlike `_render_marker` in `save_config_bundle` (l.1407ff). Scratch: kiss marked running with non-default `rf_log`/`rx_only` saved. After the reset, `restart_required("kiss")` stayed None. The equivalent `save_config_bundle` did write a marker.

### CR2-3
- where: `lhpc/core/service_params.py:3352` · severity kept S3: the race window is narrow
- claim: `save_config_bundle` `_gps_recheck` (l.1333-1362): a `use_gps` change is rechecked against a FORCED-FRESH snapshot UNDER the exclusive config lock, because a start can come up between the pre-check and the write.
- defect: `reset_config` only runs the liveness check before `update_stack_config` takes the config lock; there is no in-lock recheck. A start that completes between the check and the write runs with feed/claims/rendered config from the old `use_gps` value while the saved value flips. Also, only the flat `use_gps` key is gated: a scoped `__r__<comp>__use_gps` key is cleared without any check.
- how to see it: Race: block in `gps_liveness_blockers` returning [], start the stack, then let the reset write; the stack runs with `use_gps` reset underneath it.
- verifier: PARTLY — Holds: the liveness check (l.3352-3360) and the `stored` read (l.3345) run before `update_stack_config` takes `config_lock` (config.py:2026). Nothing rechecks under the lock the way `_gps_recheck` does (l.1333-1362). Does not hold: no GPS reader uses the scoped `__r__<comp>__use_gps` key (l.1264-1267), so clearing it unchecked changes nothing.

### CR2-4
- where: `lhpc/core/service_params.py:3344` · severity kept S3: it needs a malformed or unsafe band-less file
- claim: The refusal text (l.3366): "unsafe/malformed config (refused, not modified)".
- defect: For a band-switchable stack, `files = [cfg_band, ""]` is written as two separate locked writes. If the band-less file raises (`ConfigError`/`OSError`/`PathContainmentError`), the banded file is already cleared, and the result still says "not modified". This is a partial reset that is reported as none.
- how to see it: Make `config/stacks/<stack>.toml` malformed (or a symlink) with valid `<stack>@433.toml` overrides; `reset_config(<stack>, "433")` → failure message, yet the 433 overrides are gone.
- verifier: CONFIRMED — Scratch: kiss@433 had overrides and kiss.toml held invalid TOML. `reset_config("kiss","433")` returned "refused, not modified" with the 433 overrides already gone. The same partial write happens when the use_gps gate returns at l.3356 on the band-less pass.



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
