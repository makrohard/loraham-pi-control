# CLOUD BRIEF · PLAN for F38: the source-selector model (one Claude Code cloud session, read-only on the code)

You write the PLAN; you change no code. Output = ONE commit on this routine's branch adding `plans/PLAN-F38.md`. Never touch
`main`, `dev` or `code-review/brief`; no pull request; no attribution lines. Base: origin/main = e5187f70 (v0.11.10). Read
first: `git show origin/code-review/brief:code-review/F38-auto-install-default.md` (the analysis: nothing flipped; the
selector is never stored; `default_channel()` = binary where published else `pinned`, since 0.3.6; the console labels
`pinned` "Known working"), then `docs/architecture.md`, `docs/provenance.md`, `docs/operations.md`, `docs/cli.md`,
`tests/README.md`, `docs/maintenance.md`.

## The maintainer's decision (2026-10-03), verbatim: "We do want to use binaries where able, but we want latest dev to be
default (known-working is fallback when dev fails and latest stable is for the ones that do not like to update too often)."

## What the plan must settle (with file:line at e5187f70)
1. **The model.** Selectors today: `pinned` (the manifest pin; labelled "Known working"), `dev` (branch tip), `stable`
   (latest tag), `binary` (published build). New default rule: `binary` where a binary is published for the stack on this
   platform, else `dev`. `pinned` stays as the explicit fallback selector; `stable` stays for operators who update rarely.
2. **"Known-working as fallback when dev fails."** Establish what exists: the known-working compositions
   (`profiles/known-working/*`, `_pinned_expected`, `known_working.py`), the auto-install plan/marker, build failure
   outcomes. Propose the SIMPLEST mechanism that, when a `dev` install or build fails, offers/uses the known-working pin:
   prefer an operator-visible fallback (the result names the fallback and the command, or the auto-install driver retries
   once with `pinned` and records that it did) over a silent switch; never silently change what the operator selected.
   If an automatic retry is a new mechanism, say so and size it; the maintainer decides between "offer" and "auto-retry".
3. **Where the default is produced and consumed:** `service_binary_channel.default_channel()` and every caller the
   analysis lists (auto-install rows and driver, the console's Update, the CLI defaults and help texts, `auto_install()`'s
   signature default `source="pinned"`), the image builder and the testlab release lane (they must pass `--source pinned`
   explicitly if they rely on the old default — find the exact places: the `063a475` message, testlab `release` lane).
4. **Labels and docs:** the console label for `pinned` ("Known working" → the truthful name), the "pinned =
   production-safe default" wording (services.py SOURCE_CHOICES comment, docs/cli.md, docs/provenance.md, README if any),
   the CHANGELOG line in the operator's words; one place per fact.
5. **Tests:** default_channel for a non-binary stack is `dev`, for a binary-capable stack on a platform with a published
   build is `binary`; the auto-install row preselection; the console POST fallback; the CLI help/default; the builder and
   lane pass `pinned` explicitly (a test that would be red if they fell to the new default); the fallback mechanism's test;
   upgrade path: a box with `profiles/known-working/*.json` present still renders `dev` as default. Each red before.
6. **Risk:** boxes that today install `pinned` by default will build `dev` after this change (compile time on a Pi; an
   upstream break reaches them sooner) — state it and the mitigation (the fallback, the stable selector); the image build
   must stay reproducible (`pinned`). Live proof on the Pi 5: the auto-install page shows "latest dev" as the default
   for a source stack and "binary" for a binary-capable one; a forced dev failure shows the fallback.
7. Commits (one per concern), order, open questions with recommendations (≤ 5), self-check. ≤ 220 lines.

## Commit identity (the maintainer's rule — a direct violation otherwise)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits in this repository: git log -1 --format=%ae --author=makrohard origin/main>`, and commit with that identity; no Co-Authored-By, Claude-Session or any AI-attribution line in any message. After each commit check `git log -1 --format='%an %cn%n%B'` shows makrohard twice and no such line; fix it with `git commit --amend --reset-author --no-edit` before you push.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').

## Review-packet header (mandatory wording)
Every review packet you write MUST start with: a request paragraph that names what to judge and the answer form — `| commit | verdict (OK / FINDING) | what |` and a final line GREEN / GREEN WITH NOTES / RED — and the sentence "This file is your whole input: you have no repository access; use no connector, tool or web lookup."
