# CLOUD BRIEF · code review of loraham-pi-control, slice 3 of 10 (one Claude Code cloud session)

You review a fixed set of files of this repository for defects. READ-ONLY on the code: you change no file under
`lhpc/`, `testlab/`, `demo/`, `tests/` or `tools/`. Your only output is ONE commit on this routine's branch (the
environment names it; `git push` once at the end) that adds the file `code-review/findings-slice-3.md`. Never open a
pull request, never touch `main` or `dev` or any other branch, never tag. No Co-Authored-By or AI-attribution line.

## Base
`git fetch origin && git checkout -B <this routine's branch> origin/main`. Assert `git rev-parse HEAD` ==
`c60028f08a87a98373646f4364f7988e5cf6d58d` (v0.11.9); if not, stop and write only that into the findings file.

## The files of this slice (read every line of every one; nothing else is in scope for findings)
- `lhpc/core/install.py` (124 KB)
- `lhpc/core/service_auto_install.py` (112 KB)
- `lhpc/core/auto_install.py` (23 KB)
- `lhpc/core/deps.py` (109 KB)
- `lhpc/core/manifest.py` (50 KB)

You MAY read any other file of the repository to understand a call, a contract or a test — but a finding must
point at a line inside this slice. `docs/architecture.md` (the model and the safety invariants) and `docs/README.md`
are the frame; read them first. `tests/README.md` names the house rules of the tests.

## What counts as a finding
Defects only: wrong behaviour; a guarantee the code claims (docstring, comment, doc, CHANGELOG) but does not hold;
races between threads/processes/requests; unhandled errors on the paths an operator hits; resource leaks (fds,
processes, locks, temp files); secrets and file-permission handling; a safety invariant reachable without its check
(nothing transmits without the start gate: licence/call sign, band, power, clock; band arbitration between stacks);
boot restore and self-update that could leave a box unreachable; data loss on a crash mid-operation.
NOT findings: style, naming, wording, "could be simpler", missing type hints, long functions — unless they hide a
defect. Do not propose refactors.

## Method
1. Read docs/architecture.md, then the slice's files in the listed order, completely.
2. For every candidate finding: re-read the code once more and check the OTHER side (the caller, the test, the
   unit file, the doc) before you write it. Prefer one proven finding over five guesses. If a test already covers the
   case and is green, it is not a finding — say so in a "checked and fine" list at the end (short).
3. Run nothing that needs hardware or root. `python -m pytest -q -p no:cacheprovider <the slice's test files>` is
   allowed to confirm a claim; `ruff check lhpc` for information only.

## The findings file (`code-review/findings-slice-3.md`)
Header: slice number, the files with their line counts, the base commit, the date (UTC), the model.
Then ONE table, one row per finding:
`| id | file:line | severity | the claim (what the code/doc says it guarantees) | the defect (what actually happens) | how to see it (a reproduction in words, or the name of the test that would be red — do not write the test) | confidence (high/medium/low) |`
ids `CR3-1`, `CR3-2`, …; severity S1 = safety or data loss, S2 = wrong behaviour the operator meets, S3 =
robustness (a path that fails only under an unusual condition). Severity S1 findings first.
Then "Checked and fine" (≤ 15 lines) and "Not read / not understood" (be honest; an unread file is listed here).
No addresses, call signs, node ids, home paths or secrets anywhere. Keep it short: facts, file:line, no narrative.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').

## Review-packet header (mandatory wording)
Every review packet you write MUST start with: a request paragraph that names what to judge and the answer form — `| commit | verdict (OK / FINDING) | what |` and a final line GREEN / GREEN WITH NOTES / RED — and the sentence "This file is your whole input: you have no repository access; use no connector, tool or web lookup."
