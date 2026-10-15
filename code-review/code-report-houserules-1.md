# Code report: houserules-1 (packet P1 of PLAN-houserules-kept v2.1)

**Branch:** `cons/houserules-1-r1`, stacked on P2 (`cons/houserules-2-r1` @ 6775285e, which sits on `origin/dev` = 254bbae3). Landing order is P2, then P1. One code commit, cf24d05a.

## HOUSE RULES KEPT (the checker on its own range)
```
$ python tools/check_houserules.py cons/houserules-2-r1..cf24d05a --fact "One place per fact" --fact "checks form, not truth"
HOUSE RULES KEPT  cons/houserules-2-r1..cf24d05a
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 9 lines   OK
docs         1 files, +9 lines; fact "One place per fact" -> docs/maintenance.md; fact "checks form, not truth" -> docs/maintenance.md   OK
tests        9 new tests; layers tests/repo=repo; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```
PASS with no WARN: the commit states the red-before lines of the three second-read fixes.

## Commit
| commit | files | tests | red-before |
|---|---|---|---|
| cf24d05a | tools/check_houserules.py (new), tests/repo/test_houserules_checker.py (new), docs/maintenance.md (Policy paragraph) | 25 cases: 10 HARD fixtures + a foreign author (exit 1, line HARD, VERDICT FAIL); 9 WARN fixtures (exit 0, line WARN, VERDICT PASS). Each HARD/WARN fixture violates exactly one condition and asserts every other line OK. Product tokens stay OK; a non-contiguous indented line is not a continuation; an indented test method is not a new test; one clean range (every line OK); no budget table (modules OK, N/A) | the tool: none claimed (a new invariant; the fixtures are negative and positive invariant tests). The three second-read fixes: red against the first version, cb7f97ed; the two round-6 controls: red against f2de62ed (lines in the commit message) |

## The 6-point block
1. **Contracts:**
   - `check_houserules.py <base>..<head> [--fact LITERAL]...`: exit 0 with no HARD line, 1 with a HARD line, 2 on a usage or git error.
   - The block is `HOUSE RULES KEPT <range>`, seven `<name> <text> <OK|WARN|HARD>` lines, then `VERDICT PASS|FAIL (hard h, warn w)`.
   - Each condition is as in plan §2, with two stated clarifications:
     - net family growth is WARN instead of HARD only for the growth of files whose budget the range raises with a reason; growth elsewhere stays HARD;
     - `--fact` is a literal `git grep -lF` over `docs` and `README.md` at `<head>`.
2. **Invariants:** no product code changes. The tool only reads git (`git diff`, `git log`, `git show`, `git grep`) and prints.
3. **Failure classes:**
   - A range with no `CHANGELOG.md` or budget table degrades to "top section 0 lines" and "modules N/A".
   - A git error is exit 2 with git's message.
   - Strings and comments that contain a pattern token match. That is documented, and the test file builds its own pattern fixtures from fragments so it does not trip the checker.
4. **Test rules:**
   - ordinary layer (`tests/repo`), with git in a temporary directory and no network;
   - assertions read the exit code and verdict tokens, never prose;
   - no sleeps; no private patching.
5. **Whole directory:** `python -m pytest -q -p no:cacheprovider -n 8 tests/repo` → `576 passed, 1 skipped`. `ruff check tests --select F,E9` and `ruff check lhpc testlab tools/check_houserules.py` → `All checks passed!`.
6. **Self-review:**
   - Found and fixed:
     - the git-log record split left a leading newline on each sha (exit 2 on the first run);
     - a raise compared tuples lexicographically (now: any component larger);
     - "fact in two files" was counted by commas in the text (now: by the number of files);
     - the test file's own pattern fixtures matched the checker (now built from fragments).

**Second read (notes applied before gate 1):**
- the word check now covers both house-rule words, with the exact product tokens exempt (`user-`/`user_` header names, pytest's `scope="…"` fixture value and the `needs_…` marker);
- a reasoned raise excuses only that file's growth;
- the docs paragraph says the block checks form, not truth. tests/README.md does not mention the checker, so it needs no such sentence.

**Gate 1 round 6 notes (applied, no new round):**
- a CHANGELOG continuation is contiguous with its bullet;
- a new test is a top-level `def test_` (no leading whitespace), and the markers check uses the same definition;
- fixtures are neutral on every other condition (the trailer case changes `notes.txt`; over-budget offsets the line growth; raise-without-reason grows nothing) and assert it;
- the self-check below is the real command and its output;
- the stale "Accepted" bullet is removed.

**Work round W1b (item B), applied before landing:**
- CHANGELOG: a bullet over three lines is now HARD (the rule is "one or two entries, each at most three lines", stated so in docs/maintenance.md); `--correction` lets an item that only corrects a released section pass with WARN;
- family public-method growth over the range is HARD unless the grown file's budget was raised with a reason (the line count already was);
- `--fact`: already read at `<head>` (`git grep -lF -- <fact> <head> -- docs README.md`); a dirty-checkout fixture now proves it (a control: green on the previous version too);
- the tests line's WARN (stale finding): present since v2.1 and asserted by the `test-without-red-before-wording` fixture;
- fixed caps and family completeness: houserules-2 (rule 13 and its tests).

**CHANGELOG:** none (contributor tooling; nothing an operator sees).

**Guardrails:** one stdlib script (`argparse`, `ast`, `re`, `subprocess`), no class, no registry; one test module; one docs paragraph.

## Numbers, measured
| figure | command | output |
|---|---|---|
| diff | `git diff --stat cons/houserules-2-r1..cf24d05a` | 3 files changed, 476 insertions(+) |
| script / test lines | `wc -l tools/check_houserules.py tests/repo/test_houserules_checker.py` | 265 / 202 |
| test cases | `pytest --collect-only -q tests/repo/test_houserules_checker.py \| grep -c '::'` | 28 |
| directory | the pytest command above | 576 passed, 1 skipped |
