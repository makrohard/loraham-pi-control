# CLOUD BRIEF · CODE fix group G2 (transactional "Reset to defaults": CR2-2, CR2-3, CR2-4) (one Claude Code cloud session)

You implement an AUDITED PLAN exactly; you do not redesign. Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6
(v0.11.10). Read, in this order: `code-review/PLAN-G2.md` and `code-review/PLAN-G2-DELTA.md` (the DELTA replaces the CR2-3 test design and adds the notes to honour), both on the
branch `code-review/brief` (`git show origin/code-review/brief:code-review/<file>`), then `docs/architecture.md`,
`tests/README.md`, `docs/maintenance.md`.

## Output
The commits the plan's §5 names (one per finding, in the plan's order CR2-4 → CR2-3 → CR2-2, subjects `<id>: <what>`), each green on its own
(`python -m pytest -q -p no:cacheprovider` on the touched test modules + `tests/repo`; `ruff check lhpc testlab` and
`ruff check tests --select F,E9`), then ONE report commit adding `code-review/code-report-G2.md`. `git push`
once at the end. Never touch `main`, `dev` or `code-review/brief`; no pull request; no Co-Authored-By or
AI-attribution line in any commit (the author is re-set downstream; still write none).

## Rules (the maintainer's)
The simplest change that implements the plan; the plan's file:line are at e5187f70; match the surrounding code's
idiom and comment density; no behaviour beyond the plan; every regression test RED before its commit (prove it by
running the new test against the parent commit once) and GREEN after; preservation tests marked as such in the
report; docs changed exactly as the plan says (one place per fact); the CHANGELOG lines in the operator's words,
under "## Unreleased" is NOT used in this repo — put them where the plan says. If the plan cannot be implemented as
written (a line moved, a claim wrong), STOP that commit, write what you found into the report under "Deviations",
and continue with the next; never improvise a different design.

## The report (`code-review/code-report-G2.md`) — with a SELF-AUDIT PROOF section
1. Per commit: sha, files, the tests (module::name) with red-before yes/no/preservation and the exact command + result.
2. SELF-AUDIT PROOF, per change: (a) the guarantee the plan states, (b) the code line(s) that now enforce it, (c) what
   else the change could break (callers, the test lane, image builds, the lifecycle guards' lock order, the web config routes, boot restore) and the check that
   rules it out (a test name or a grep result), (d) one thing you re-read after writing it and what you found.
3. Deviations from the plan (or "none").
4. The full pytest/ruff summary lines.

## Commit identity (the maintainer's rule — a direct violation otherwise)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits in this repository: git log -1 --format=%ae --author=makrohard origin/main>`, and commit with that identity; no Co-Authored-By, Claude-Session or any AI-attribution line in any message. After each commit check `git log -1 --format='%an %cn%n%B'` shows makrohard twice and no such line; fix it with `git commit --amend --reset-author --no-edit` before you push.

## The gate-1 review packet (you build it too)
As the LAST file of your report commit add `code-review/gate1-CODE-G2.md`: a header for an independent reviewer with NO repository access ("This file is your whole input"; one paragraph: what the defects were and what each commit changes, in product terms — no 'threat/bypass/forge' wording), the list of your commits (`<sha> <subject>`), your report (the self-audit section included), then the FULL diff of your fix commits (`git diff <base>..<last fix commit>`) in a ```diff block; replace any home path by `$HOME`; no e-mail addresses, IPs, call signs. The reviewer answers per commit OK / FINDING and GREEN / GREEN WITH NOTES / RED — write that request into the header.
