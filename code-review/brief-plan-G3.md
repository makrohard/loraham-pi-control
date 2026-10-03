# CLOUD BRIEF · PLAN for fix group G3 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-G3.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
### CR1-2
- where: `lhpc/core/service_lifecycle_ops.py:2963` · severity kept S2: a routine restart leaves the stack down
- claim: Restart: "a valid target is never stopped only to be rejected by the later start" (2859-2861, 2939-2940).
- defect: Before the stop leg, `_restart_impl_inner` checks only identity, saved launch values and the dependency band (2941-2956). Three apply-only refusals of `_start_impl_inner` are not checked first: the firewall exposure gate (1029), config ambiguity (1038) and run blockers without `stop_owners` (1045). The running stack is stopped (2963), then the nested start refuses, and the stack is left down.
- how to see it: Firewall integration installed. A stack runs with a saved non-loopback listener. Firewall intent is changed but not applied (`config_ok` false), for example after editing the listener bind, which is exactly the change a restart is meant to apply. Run `lhpc stack restart <id> --yes`: the stop is verified, the start returns "Firewall changes pending — the listener was NOT started", and the stack is down. Red test: "restart with a pending firewall gate leaves the running stack up".
- verifier: CONFIRMED — `_restart_impl_inner` checks only identity, launch values and the dependency band (2941-2956), then stops at 2963. The checks at 1029/1038/1045 run only in the nested apply. Scratch: firewall gate stubbed to refuse; `restart("kiss", apply=True)` stopped kiss, then returned ok=False, with a summary that starts with a misleading "Restarted 'kiss'. Cannot start…".

### CR1-7
- where: `lhpc/core/service_lifecycle_ops.py:591` · severity kept S3: the plan says ok, the apply refuses; nothing is mutated before the refusal
- claim: `start()` docstring: "The plan (`apply=False`) and the apply take every decision alike … so a refused start is known before anything is queued or mutated."
- defect: The plan returns at 936-994. The firewall exposure gate (1029), the config-ambiguity refusal (1038) and the MeshCore position refusal (669, `start()` apply path only) run only on apply. The plan answers ok ("Run plan …"); the queued or applied start is then refused.
- how to see it: Same setup as CR1-2: `lhpc stack start <id>` without `--yes` shows a clean plan; `--yes` returns "Firewall changes pending". Red test: "start plan refuses what the apply's firewall gate refuses".
- verifier: CONFIRMED — The plan returns at 936-994, before the firewall gate (1029) and ambiguity (1038). The MeshCore position refusal runs only in the `start()` apply (655-669). Scratch: firewall gate stubbed to refuse; `start("kiss", apply=False)` returned ok "Run plan…". Caveat: `firewall_gate_stack_start` writes via `firewall_render()` (service_firewall.py:1018, 1026), so the plan needs a read-only variant.



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
