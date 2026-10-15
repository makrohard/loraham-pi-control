# Gate 1: houserules-1 (packet P1 of PLAN-houserules-kept v2.1)

**Request.** Judge the one code commit against the plan's §2 (the checker), §5 (the docs paragraph) and §6 (tests). That means:
- whether each line's condition is implemented as stated, and HARD/WARN as planned;
- whether the fixture tests can fail for each condition;
- the two clarifications the report names;
- the docs paragraph;
- the report's claims against the diff.

The base is P2 (`cons/houserules-2-r1`, the budget table), which lands first.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied; the diff is `git diff` output verbatim and contains no home path, LAN address, host name or call sign.

## Commits
```
f42c7b6c houserules-1: code report
cf24d05a houserules-1: tools/check_houserules.py prints the HOUSE RULES KEPT block
```

## Commit message of cf24d05a
```
houserules-1: tools/check_houserules.py prints the HOUSE RULES KEPT block

One stdlib script reads git over <base>..<head> and prints one line each for
changelog, docs, tests, patterns, markers, attribution and modules (the budget
table of tests/repo/test_module_budgets.py), each OK/WARN/HARD, then VERDICT;
exit 1 on a HARD line, 2 on a usage or git error. It writes nothing. The
attribution names are assembled from fragments, so no committed file spells
them. docs/maintenance.md, Policy: the docs and CHANGELOG rule, the call, and
that the block checks form, not truth.

New invariant: no parent-red exists for a new tool. tests/repo/
test_houserules_checker.py builds a throwaway git repository per case: 10 HARD
fixtures + a foreign author exit 1 with that line HARD; 9 WARN fixtures exit 0
with that line WARN; product tokens (an HTTP header, pytest's fixture scope)
stay OK; a clean range passes; without a budget table the modules line is N/A.

Second-read fixes, red-before against the first version of this script
(cb7f97ed), each case run once there: raise-excuses-only-its-own-file gives
"assert (0, 'WARN', 'PASS') == (1, 'HARD', 'FAIL')"; word-in-a-message gives
"assert (0, 'OK', 'PASS') == (0, 'WARN', 'PASS')"; product tokens give
"assert (0, 'WARN') == (0, 'OK')".

Gate-1 notes (round 6), red-before against f2de62ed, each run once there:
an indented line after an unindented one no longer lengthens a CHANGELOG bullet
("assert (0, 'WARN') == (0, 'OK')"); an indented test method is not a new test
("assert (0, 'WARN') == (0, 'OK')"). Every fixture now violates exactly one
condition and asserts all other lines OK.

Work round W1b (item B), red-before against the previous version of this
script (bc1d6abd), each case run once there: a bullet over three lines is
now HARD ("assert (0, 'WARN', 'PASS') == (1, 'HARD', 'FAIL')"); family
public-method growth within every cap is HARD ("assert (0, 'OK', 'PASS') ==
(1, 'HARD', 'FAIL')"); `--correction` downgrades a bullet below the top
section to WARN (the flag did not exist). Control: `--fact` already read
<head>, not the working copy (the dirty-checkout case passes on both).

```

## The report

> # Code report: houserules-1 (packet P1 of PLAN-houserules-kept v2.1)
> 
> **Branch:** `cons/houserules-1-r1`, stacked on P2 (`cons/houserules-2-r1` @ 6775285e, which sits on `origin/dev` = 254bbae3). Landing order is P2, then P1. One code commit, cf24d05a.
> 
> ## HOUSE RULES KEPT (the checker on its own range)
> ```
> $ python tools/check_houserules.py cons/houserules-2-r1..cf24d05a --fact "One place per fact" --fact "checks form, not truth"
> HOUSE RULES KEPT  cons/houserules-2-r1..cf24d05a
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 9 lines   OK
> docs         1 files, +9 lines; fact "One place per fact" -> docs/maintenance.md; fact "checks form, not truth" -> docs/maintenance.md   OK
> tests        9 new tests; layers tests/repo=repo; red-before 1/1   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
> PASS with no WARN: the commit states the red-before lines of the three second-read fixes.
> 
> ## Commit
> | commit | files | tests | red-before |
> |---|---|---|---|
> | cf24d05a | tools/check_houserules.py (new), tests/repo/test_houserules_checker.py (new), docs/maintenance.md (Policy paragraph) | 25 cases: 10 HARD fixtures + a foreign author (exit 1, line HARD, VERDICT FAIL); 9 WARN fixtures (exit 0, line WARN, VERDICT PASS). Each HARD/WARN fixture violates exactly one condition and asserts every other line OK. Product tokens stay OK; a non-contiguous indented line is not a continuation; an indented test method is not a new test; one clean range (every line OK); no budget table (modules OK, N/A) | the tool: none claimed (a new invariant; the fixtures are negative and positive invariant tests). The three second-read fixes: red against the first version, cb7f97ed; the two round-6 controls: red against f2de62ed (lines in the commit message) |
> 
> ## The 6-point block
> 1. **Contracts:**
>    - `check_houserules.py <base>..<head> [--fact LITERAL]...`: exit 0 with no HARD line, 1 with a HARD line, 2 on a usage or git error.
>    - The block is `HOUSE RULES KEPT <range>`, seven `<name> <text> <OK|WARN|HARD>` lines, then `VERDICT PASS|FAIL (hard h, warn w)`.
>    - Each condition is as in plan §2, with two stated clarifications:
>      - net family growth is WARN instead of HARD only for the growth of files whose budget the range raises with a reason; growth elsewhere stays HARD;
>      - `--fact` is a literal `git grep -lF` over `docs` and `README.md` at `<head>`.
> 2. **Invariants:** no product code changes. The tool only reads git (`git diff`, `git log`, `git show`, `git grep`) and prints.
> 3. **Failure classes:**
>    - A range with no `CHANGELOG.md` or budget table degrades to "top section 0 lines" and "modules N/A".
>    - A git error is exit 2 with git's message.
>    - Strings and comments that contain a pattern token match. That is documented, and the test file builds its own pattern fixtures from fragments so it does not trip the checker.
> 4. **Test rules:**
>    - ordinary layer (`tests/repo`), with git in a temporary directory and no network;
>    - assertions read the exit code and verdict tokens, never prose;
>    - no sleeps; no private patching.
> 5. **Whole directory:** `python -m pytest -q -p no:cacheprovider -n 8 tests/repo` → `576 passed, 1 skipped`. `ruff check tests --select F,E9` and `ruff check lhpc testlab tools/check_houserules.py` → `All checks passed!`.
> 6. **Self-review:**
>    - Found and fixed:
>      - the git-log record split left a leading newline on each sha (exit 2 on the first run);
>      - a raise compared tuples lexicographically (now: any component larger);
>      - "fact in two files" was counted by commas in the text (now: by the number of files);
>      - the test file's own pattern fixtures matched the checker (now built from fragments).
> 
> **Second read (notes applied before gate 1):**
> - the word check now covers both house-rule words, with the exact product tokens exempt (`user-`/`user_` header names, pytest's `scope="…"` fixture value and the `needs_…` marker);
> - a reasoned raise excuses only that file's growth;
> - the docs paragraph says the block checks form, not truth. tests/README.md does not mention the checker, so it needs no such sentence.
> 
> **Gate 1 round 6 notes (applied, no new round):**
> - a CHANGELOG continuation is contiguous with its bullet;
> - a new test is a top-level `def test_` (no leading whitespace), and the markers check uses the same definition;
> - fixtures are neutral on every other condition (the trailer case changes `notes.txt`; over-budget offsets the line growth; raise-without-reason grows nothing) and assert it;
> - the self-check below is the real command and its output;
> - the stale "Accepted" bullet is removed.
> 
> **Work round W1b (item B), applied before landing:**
> - CHANGELOG: a bullet over three lines is now HARD (the rule is "one or two entries, each at most three lines", stated so in docs/maintenance.md); `--correction` lets an item that only corrects a released section pass with WARN;
> - family public-method growth over the range is HARD unless the grown file's budget was raised with a reason (the line count already was);
> - `--fact`: already read at `<head>` (`git grep -lF -- <fact> <head> -- docs README.md`); a dirty-checkout fixture now proves it (a control: green on the previous version too);
> - the tests line's WARN (stale finding): present since v2.1 and asserted by the `test-without-red-before-wording` fixture;
> - fixed caps and family completeness: houserules-2 (rule 13 and its tests).
> 
> **CHANGELOG:** none (contributor tooling; nothing an operator sees).
> 
> **Guardrails:** one stdlib script (`argparse`, `ast`, `re`, `subprocess`), no class, no registry; one test module; one docs paragraph.
> 
> ## Numbers, measured
> | figure | command | output |
> |---|---|---|
> | diff | `git diff --stat cons/houserules-2-r1..cf24d05a` | 3 files changed, 476 insertions(+) |
> | script / test lines | `wc -l tools/check_houserules.py tests/repo/test_houserules_checker.py` | 265 / 202 |
> | test cases | `pytest --collect-only -q tests/repo/test_houserules_checker.py \| grep -c '::'` | 28 |
> | directory | the pytest command above | 576 passed, 1 skipped |

## The plan (v2.1, for reference)

````markdown
# PLAN houserules-kept (v2.1): the house rules, kept at the first try

## Changes v1 → v2 (round 1 verdict, RED, F1–F5; all accepted)
| finding | change |
|---|---|
| F1 counts without commands; classes by reading | §1: the CHANGELOG/docs commands are stated; the finding classes are now a stated grep over the verdict files (overlapping keyword matches, not a partition) |
| F2 checks under-specified, false positives too clean | §2: each check is a stated syntactic condition; the false-positive column is expanded; `--fact`, the marker heuristic, the commit-message `red-before` and the attribution word match are WARN, not HARD |
| F3 "script does not exist" is no red-before | §6: the checker is a NEW invariant; its tests are named negative/positive fixture tests, and no parent-red is claimed |
| F4 ratchet red-before synthetic | §6: named a negative test (budget − 1 fails); no parent-red claimed for the new invariant |
| F5 path → layer not literal | §2: the literal table the checker uses |
| round 2 notes F1/F2 (v2.1) | the block's tests line reads `OK\|WARN\|HARD`; the order is P2 → P1 → P3, and P1 prints `modules N/A` without the table |

**Base:** `dev` = eb9673a4 (0.12.9). **Goal:**
- every item reports a measured HOUSE RULES KEPT block (tests, docs, CHANGELOG, attribution, module size);
- the service family stops growing until the architecture item.

Plain script, one table, one test, two paragraphs; no framework.

## 1. Measured
**Service family** = `services.py` + `service_base.py` + the 14 mixins it composes (`services.py:119-132`). Lines per tag (`git show <tag>:lhpc/core/<f>.py | wc -l`), classes and public methods at v0.12.9 (`grep -cE '^class '`, `grep -cE '^    (async )?def [a-z]'`):

| file | v0.9.0 | v0.10.0 | v0.11.0 | v0.12.0 | v0.12.9 | classes | public defs |
|---|---|---|---|---|---|---|---|
| service_lifecycle_ops | 5649 | 5784 | 5796 | 6243 | 6315 | 1 | 73 |
| service_params | 3335 | 3476 | 3476 | 3380 | 3380 | 1 | 54 |
| services | 2709 | 2737 | 2788 | 2950 | 2959 | 1 | 28 |
| service_maintenance | 2635 | 2649 | 2730 | 2874 | 2883 | 1 | 30 |
| service_webserver | 2070 | 2074 | 2112 | 2388 | 2388 | 2 | 42 |
| service_selfupdate | 1402 | 1429 | 1459 | 1915 | 1932 | 1 | 22 |
| service_auto_install | 1861 | 1861 | 1864 | 1875 | 1875 | 2 | 16 |
| service_firewall | 1454 | 1454 | 1454 | 1533 | 1533 | 1 | 19 |
| service_network | 1096 | 1118 | 1118 | 1183 | 1183 | 1 | 9 |
| service_binary_ops | 787 | 834 | 847 | 1077 | 1077 | 1 | 5 |
| service_hmac | 1013 | 1013 | 1013 | 1016 | 1016 | 2 | 17 |
| service_system | 622 | 752 | 752 | 759 | 759 | 2 | 2 |
| service_boot_restore | 569 | 578 | 578 | 651 | 678 | 1 | 2 |
| service_secrets | 0 | 0 | 0 | 399 | 399 | 1 | 2 |
| service_binary_channel | 235 | 235 | 235 | 297 | 297 | 1 | 19 |
| service_base | 126 | 126 | 126 | 166 | 166 | 6 | 0 |
| **total** | **25563** | **26120** | **26348** | **28706** | **28840** | | |

- **Family growth:** +3,277 lines (+12.8 %) from v0.9.0 to v0.12.9, of which the 0.12.0 consolidation added +2,358.
- **CHANGELOG body lines per release** (`git show v0.12.9:CHANGELOG.md | awk '/^## /{if(h)print h, n; h=$2; n=0; next} {n++} END{print h, n}'`: lines from one `## ` heading to the next, blank lines included):
  - 0.9.x: 22/17/20; 0.10.0: 31; 0.11.0: 52; 0.11.3: 34; 0.11.10: 45; 0.11.11: 41; 0.12.0: 58; 0.12.1: 20; 0.12.2: 28; 0.12.3–0.12.9: 5–12.
  - The file (`git show <tag>:CHANGELOG.md | wc -l`) was 652 lines at v0.9.0, 1,319 at v0.12.0, 1,153 at v0.12.9.
- **docs/*.md total lines** (`git ls-tree -r --name-only <tag> docs | grep '\.md$' | while read f; do git show <tag>:$f; done | wc -l`): 5,684 (v0.9.0) → 5,838 (v0.11.0) → 5,920 (v0.12.0) → 6,004 (v0.12.9).
- **Rule-class findings this run.** 50 findings in 10 verdict files (`audits/VERDICT-*.md` without houserules; per file the `**Fn ` / `Fn — ` lines, else its `| … | FINDING |` table rows). Keyword matches with `grep -ciE` (a finding can match several):
  - precision `stronger|exceed|outrun|unlabel|not reproducible|unproved|not establish|does not account|not yet justified|incomplete|unsupported|absolute|too strong|not fully accounted` → 16;
  - test `red-before|proof|smoke|test design|lane` → 14;
  - docs `comment|docs|README|become false` → 2;
  - `CHANGELOG` → 1; none of these → 19.
- The standing brief already carries findings 61, 68, 73, 79 and 94 of the last run (all precision-class). **The rules are known; they are not kept at the first try.**

## 2. Checker: `tools/check_houserules.py <base>..<head>` (stdlib Python, ~180 lines est.)
Prints one fixed block; exit 1 on any HARD line. Input = `git diff --unified=0 <base>..<head>` (added lines = `^\+[^+]`), `git log --format=%H%x00%an%x00%B <base>..<head>`, and the files at `<head>`.

| line | exact condition | HARD | WARN | false positives (honest) |
|---|---|---|---|---|
| changelog | in CHANGELOG.md's diff: added lines matching `^\+- ` = bullets; a bullet's lines = it plus following added lines starting with two spaces; the top section = lines between the first and second `^## ` of the head file | > 2 bullets in the range; an added bullet below the second `^## ` | a bullet > 3 lines | a deliberate correction of an old section's bullet (shown, HARD stays: such a change needs its own item) |
| docs | added lines in `docs/**/*.md`, `README.md`; for each `--fact "<literal>"`: `grep -rlF -- <literal> docs README.md` | — | a docs file changed and no `--fact` given; a literal found in > 1 file | it detects a LITERAL repeated, not a fact stated twice in other words, and a link text matches too |
| tests | path of each added/changed `test_*.py` against the layer table below (first matching prefix); new test functions = added lines `^\+(async )?def test_`; red-before = the commit's message matches `(?i)red[- ]before` | a new `test_*.py` under no listed prefix | a commit that adds a test function and has no `red-before` wording (wording only, not proof) | helper modules (not `test_*.py`) are not checked |
| patterns | added lines in test files: `inspect\.getsource\(`; `^\+\s*assert True\b`; `(read_text\|open)\(.*["']lhpc/[^"']+\.(py\|js\|css)["']`; `time\.sleep\(`; `assert .*\.startswith\(`; `\b(requests\.\|urllib\.request\|socket\.create_connection)` | `inspect.getsource(`; `assert True` | the other four | strings/comments containing the token match; rule 1's whole-module exception carries `# rule-1 exception` on its line and is shown, not counted |
| markers | per added test function: its added body contains `venv` or `pip install`, or both `while` and `monotonic`, and no `@pytest.mark.slow` line above it in the hunk | — | each such function | text in strings or comments; a helper doing the slow work elsewhere is missed |
| attribution | authors (`%an`) of the range; message lines and added lines against the attribution list, assembled from fragments in the script (case-insensitive substrings) | an author ≠ `makrohard`; a co-author trailer line `^Co-Authored-By:` | a list name in an added line or a message (shown with file:line); `\bagent\b` in prose | quoted third-party text, vendored files and fixtures that must contain such a word; pytest's `scope="session"` and `needs_session` are excluded by exact token |
| modules | for each family file touched: `len(text.splitlines())` and the count of `ast.FunctionDef`/`AsyncFunctionDef` directly in a `ClassDef` body whose name does not start with `_`, at base and head; BUDGETS parsed from `tests/repo/test_module_budgets.py` with `ast.literal_eval` at base and head | head > budget; family net lines > 0 over the range; a budget value larger at head than at base without `# <id>: <text>` on its line | — | none known: it reads the same table as the test |

**Layer table** (literal data in the script; tests/README.md "Where a new test goes" + "What each layer proves"):

| path prefix | layer |
|---|---|
| `tests/core/` | ordinary: controller service |
| `tests/stacks/` | ordinary: radio stacks, manifest, GPS |
| `tests/web/` | ordinary: Flask console |
| `tests/cli/` | ordinary: `lhpc` output and exit |
| `tests/install/` | ordinary: source, pins, binary channel, auto-install, self-update |
| `tests/host/` | ordinary: firewall, network, power, PKI, units, deploy scripts |
| `tests/repo/` | ordinary: repository invariants |
| `tests/golden/` | golden: the six coordinating operations only |
| `testlab/tests/{unit,acceptance,browser,release,slowbuild}/` | testlab lanes |
| `lhpc/data/meshcore_host/tests/`, `lhpc/data/rfdecode/tests/` | in-package suites |

`tests/data/`, `tests/fixtures/` and the top-level helper modules of `tests/` hold no `test_*.py` and are not layers.

Output, verbatim into the code report and the gate-1 packet:
```
HOUSE RULES KEPT  <base>..<head>
changelog    +<n> bullets (limit 2), top section <l> lines             OK|WARN|HARD
docs         <k> files, +<s> lines; fact "<p>" -> <files>              OK|WARN|HARD
tests        <t> new tests; layers <dir>=<layer>…; red-before <r>/<c>  OK|WARN|HARD
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK|WARN|HARD
markers      <m> without slow                                          OK|WARN
attribution  authors makrohard; trailers 0; words <w>                   OK|WARN|HARD
modules      <file> <a>-><b> lines (budget <B>), <d>-><e> public (budget <D>); family net <±n>   OK|HARD
VERDICT      PASS|FAIL  (hard <h>, warn <w>)
```

## 3. Ratchet: `tests/repo/test_module_budgets.py` (~40 lines est.)
- **Table:** `BUDGETS = {"lhpc/core/<f>.py": (max_lines, max_public_methods), …}` for the 16 family files, filled from dev's measured values at implementation (the columns above, the public count re-measured with `ast`).
- **`test_service_family_within_budget`** (parametrized per file): actual ≤ budget, else it names the file, the actual and the budget.
- **Docstring:** a repository invariant, not behaviour (README rule 1's whole-module exception, stated); a raise needs `# <item id>: <one sentence>` on that line (the checker enforces it); a lowering is free.
- **tests/README.md, one paragraph** (under "The rules", as rule 13; rules 11 and 12 exist on dev): "The service family does not grow. `tests/repo/test_module_budgets.py` caps the lines and public methods of `services.py`, `service_base.py` and the 14 mixins. New behaviour goes into a plain function module, not a mixin; an item that touches the family removes at least what it adds. A budget is raised only with `# <item>: <reason>` on its line; lowering is free."

## 4. Brief slimming (the run's standing brief `_common.txt`, outside the repository)
- **Before:** 35 lines / 1,347 words (`wc -l -w`). **After:** ≤ 40 short lines, est. ≤ 450 words.
- **Kept:** the three rules (tests per tests/README.md; docs per §5; CHANGELOG ≤ 2 lines under `## Unreleased`, operator's words); the checker call `python tools/check_houserules.py origin/dev..HEAD [--fact …]` with its block pasted verbatim; the report template (6-point block + HOUSE RULES KEPT block); attribution, identity, branch name; the precision items no checker tests (absolute words proven or weakened, `--color-moved` for moved code, the injector reaches the branch, packet diff = git's text, every number with its command).
- **Dropped:** the old finding numbers, the stale wave-0 base text, the duplicated simplicity paragraph.

## 5. Docs rule (docs/maintenance.md, "Policy", one paragraph)
"**Docs and CHANGELOG.** One place per fact; elsewhere a link. Short and structured: tables and lists over prose. No history and no explanation of why it was once otherwise. Every sentence is checkable against the code or a command. A change that makes a sentence untrue fixes that sentence in the same commit. CHANGELOG: one or two lines per item, in the operator's words, under `## Unreleased`."

## 6. Red-before, packets, guardrails
- **Checker tests** (`tests/repo/test_houserules_checker.py`, ordinary layer, no network). The checker is a NEW invariant: no parent tree "fails" a tool that does not exist, so **no red-before is claimed**. The tests are:
  - **negative fixtures**, one per HARD condition, in a temporary git repository built in `tmp_path`: 3 CHANGELOG bullets; a bullet below the second heading; a new `tests/misc/test_x.py`; `assert True`; `inspect.getsource(`; a commit by another author; a `Co-Authored-By:` trailer; a family file over budget; family net growth; a raised budget without comment. Each gives exit 1 and that line's verdict `HARD`.
  - **WARN fixtures** (each WARN condition: exit 0, `WARN` on its line) and one **positive fixture** (a clean range: `VERDICT PASS`, exit 0). Rule-10 assertion: the exit code and the verdict token of the named line, never the prose.
- **Ratchet test:** also a new invariant, so no parent-red is claimed. A **negative test** sets one budget to actual − 1 and expects the parametrized case to fail; the real table is green at the measured values. The commit message says "new invariant; negative test", not "red-before".
- **Packets** (file-disjoint): P1 = `tools/check_houserules.py`, `tests/repo/test_houserules_checker.py`, `docs/maintenance.md`; P2 = `tests/repo/test_module_budgets.py`, `tests/README.md`; P3 = the brief (outside the repository). Order: P2 lands first, then P1 (it reads the budget table; without the table it prints `modules N/A (budget table absent)`), then P3.
- **CHANGELOG:** none (developer tooling, nothing an operator sees). **Guardrails:** no new class, registry or framework; stdlib only; one table, one test, one script; the checker reads git and the files, writes nothing.

## 7. Open questions (recommendation first)
Python over bash: yes (`ast`, robust parsing; ~180 lines est.). WARN blocks nothing; the reviewer judges it. CI does not run the checker yet; the packet carries its block.
````

## The full diff (`git diff cons/houserules-2-r1 cf24d05a`)

```diff
diff --git a/docs/maintenance.md b/docs/maintenance.md
index 1bae3db3..20217611 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -99,6 +99,15 @@ repositories.
   [tests/README.md](../tests/README.md#markers).
 - **Version bump** = `pyproject.toml` **and** `lhpc/version.py` (a test pins them equal and
   requires the matching `CHANGELOG.md` heading) + tag.
+- **Docs and CHANGELOG.** One place per fact; elsewhere a link. Short and structured: tables and
+  lists over prose. No history and no explanation of why it was once otherwise. Every sentence is
+  checkable against the code or a command. A change that makes a sentence untrue fixes that
+  sentence in the same commit. CHANGELOG: one or two entries per item, each at most three lines, in
+  the operator's words, under `## Unreleased`; an item that only corrects a released section passes
+  `--correction`. `python tools/check_houserules.py <base>..<head> [--fact <literal>]` prints the
+  item's HOUSE RULES KEPT block (CHANGELOG, docs, tests, patterns, markers, attribution, module
+  budgets); a HARD line fails it. It checks form, not truth: a PASS says nothing about whether a
+  stated red-before ran, a CHANGELOG line is precise or a claim in a report holds.
 
 ## Branches and releases
 
diff --git a/tests/repo/test_houserules_checker.py b/tests/repo/test_houserules_checker.py
new file mode 100644
index 00000000..d7e8454d
--- /dev/null
+++ b/tests/repo/test_houserules_checker.py
@@ -0,0 +1,202 @@
+"""tools/check_houserules.py against a throwaway git repository: each HARD condition fails the
+range with that line HARD, each WARN condition passes it with that line WARN, a clean range passes.
+
+The checker is a new invariant, so these are negative and positive fixture tests; no parent tree
+existed for a red-before. Assertions read the exit code and each line's verdict token, never prose.
+"""
+import subprocess
+import sys
+
+import pytest
+
+import repo_paths
+
+CHECKER = repo_paths.REPO / "tools" / "check_houserules.py"
+CHANGELOG = "# Changelog\n\n## Unreleased\n\n## 0.1.0\n\n- old entry\n"
+BUDGET = 'BUDGETS = {{\n    "services.py": (10, 3),\n    "service_x.py": {x},\n}}\n'
+BASE = {
+    "CHANGELOG.md": CHANGELOG,
+    "docs/a.md": "Alpha fact.\n",
+    "docs/b.md": "Beta.\n",
+    "lhpc/core/services.py": "from .service_x import X\n\nclass S(X):\n    def run(self):\n        pass\n",
+    "lhpc/core/service_x.py": "class X:\n    def a(self):\n        pass\n",
+    "tests/repo/test_module_budgets.py": BUDGET.format(x="(3, 1)"),
+    "tests/core/test_ok.py": "def test_one():\n    assert 1\n",
+}
+RED = "item\n\nred-before: AssertionError on the parent\n"
+
+
+def _git(repo, *args, author="makrohard"):
+    subprocess.run(["git", "-c", f"user.name={author}", "-c", "user.email=dev@example.invalid",
+                    "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True)
+
+
+def _write(repo, files):
+    for path, text in files.items():
+        p = repo / path
+        if text is None:
+            p.unlink()
+            continue
+        p.parent.mkdir(parents=True, exist_ok=True)
+        p.write_text(text)
+
+
+@pytest.fixture
+def repo(tmp_path):
+    r = tmp_path / "r"
+    r.mkdir()
+    _write(r, BASE)
+    _git(r, "init", "-q", "-b", "main")
+    _git(r, "add", "-A")
+    _git(r, "commit", "-q", "-m", "base")
+    _git(r, "tag", "base")
+    return r
+
+
+def _commit(repo, files, msg=RED, author="makrohard"):
+    _write(repo, files)
+    _git(repo, "add", "-A")
+    _git(repo, "commit", "-q", "-m", msg, author=author)
+
+
+def _run(repo, *extra):
+    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD", *extra], cwd=repo,
+                       capture_output=True, text=True, check=False)
+    rows = {line.split()[0]: line.split()[-1] for line in p.stdout.splitlines()[1:] if line.strip()}
+    rows["VERDICT"] = p.stdout.split("VERDICT", 1)[1].split()[0] if "VERDICT" in p.stdout else None
+    return p.returncode, rows
+
+
+LINES = ("changelog", "docs", "tests", "patterns", "markers", "attribution", "modules")
+
+
+def _others(line):
+    # Every fixture violates exactly one condition: all other lines stay OK.
+    return [n for n in LINES if n != line]
+
+
+def _test(body):
+    return {"tests/core/test_ok.py": BASE["tests/core/test_ok.py"] + body}
+
+
+HARD = {
+    "changelog-three-bullets": ({"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n",
+                                 "## Unreleased\n\n- a\n- b\n- c\n")}, RED, "changelog"),
+    "changelog-below-top": ({"CHANGELOG.md": CHANGELOG + "- late\n"}, RED, "changelog"),
+    "changelog-bullet-over-three-lines": ({"CHANGELOG.md": CHANGELOG.replace(
+        "## Unreleased\n", "## Unreleased\n\n- a\n  b\n  c\n  d\n")}, RED, "changelog"),
+    # One line fewer, one public method more, within every cap: only the method growth is wrong.
+    "family-public-growth": ({"lhpc/core/services.py": "from .service_x import X\nclass S(X):\n"
+                              "    def run(self): pass\n    def more(self): pass\n"},
+                             RED, "modules"),
+    "test-outside-layers": ({"tests/misc/test_x.py": "def test_x():\n    assert 1\n"}, RED, "tests"),
+    "assert-true": (_test("\ndef test_two():\n    assert True\n"), RED, "patterns"),
+    # The patterns are assembled so that this file does not itself match them.
+    "getsource": (_test("\ndef test_two():\n    import inspect\n    assert inspect." + "getsource(len)\n"),
+                  RED, "patterns"),
+    "co-author-trailer": ({"notes.txt": "neutral\n"},
+                          "item\n\n" + "Co-" + "Authored-By: someone <x@example.invalid>\n", "attribution"),
+    "over-budget": ({"lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n",
+                     "lhpc/core/services.py": BASE["lhpc/core/services.py"].replace("\n\n", "\n", 1)},
+                    RED, "modules"),
+    "family-net-growth": ({"lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"}, RED, "modules"),
+    "raise-excuses-only-its-own-file": ({"tests/repo/test_module_budgets.py":
+                                         BUDGET.format(x="(4, 1),  # item-9: one line"),
+                                         "lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n",
+                                         "lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"},
+                                        RED, "modules"),
+    "raised-without-reason": ({"tests/repo/test_module_budgets.py": BUDGET.format(x="(4, 1)")},
+                              RED, "modules"),
+}
+
+
+@pytest.mark.parametrize("case", sorted(HARD))
+def test_each_hard_condition_fails_the_range(repo, case):
+    files, msg, line = HARD[case]
+    _commit(repo, files, msg)
+    rc, rows = _run(repo)
+    assert (rc, rows[line], rows["VERDICT"]) == (1, "HARD", "FAIL")
+    assert {k: v for k, v in rows.items() if k not in (line, "VERDICT")} == dict.fromkeys(_others(line), "OK")
+
+
+def test_a_foreign_author_fails_the_range(repo):
+    _commit(repo, {"docs/a.md": "Alpha fact.\nMore.\n"}, author="someone")
+    rc, rows = _run(repo, "--fact", "More.")
+    assert (rc, rows["attribution"]) == (1, "HARD")
+
+
+WARN = {
+    "correction-of-a-released-section": ({"CHANGELOG.md": CHANGELOG + "- late\n"}, RED,
+                                         ("--correction",), "changelog"),
+    "docs-without-fact": ({"docs/a.md": "Alpha fact.\nMore.\n"}, RED, (), "docs"),
+    "fact-in-two-files": ({"docs/b.md": "Beta.\nAlpha fact.\n"}, RED, ("--fact", "Alpha fact."), "docs"),
+    "test-without-red-before-wording": (_test("\ndef test_two():\n    assert 2\n"), "item\n", (), "tests"),
+    "sleep": (_test("\ndef test_two():\n    import time\n    time." + "sleep(0)\n    assert 2\n"),
+              RED, (), "patterns"),
+    "timed-loop-without-slow": (_test("\ndef test_two():\n    import time\n"
+                                      "    while time.monotonic() < 0:\n        pass\n    assert 2\n"),
+                                RED, (), "markers"),
+    "attribution-word": ({"docs/a.md": "Alpha fact.\n" + "Clau" + "de wrote this.\n"}, RED,
+                         ("--fact", "wrote this"), "attribution"),
+    "word-in-a-message": ({"docs/a.md": "Alpha fact.\nMore.\n"}, "item\n\nwritten in one " + "ses" + "sion\n",
+                          ("--fact", "More."), "attribution"),
+    "raised-with-reason": ({"tests/repo/test_module_budgets.py": BUDGET.format(x="(4, 1),  # item-9: one line"),
+                            "lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n"},
+                           RED, (), "modules"),
+}
+
+
+@pytest.mark.parametrize("case", sorted(WARN))
+def test_each_warn_condition_passes_with_a_warning(repo, case):
+    files, msg, extra, line = WARN[case]
+    _commit(repo, files, msg)
+    rc, rows = _run(repo, *extra)
+    assert (rc, rows[line], rows["VERDICT"]) == (0, "WARN", "PASS")
+    assert {k: v for k, v in rows.items() if k not in (line, "VERDICT")} == dict.fromkeys(_others(line), "OK")
+
+
+def test_a_clean_range_passes_with_every_line_ok(repo):
+    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- one line\n"),
+                   "docs/a.md": "Alpha fact.\nGamma.\n",
+                   "tests/core/test_ok.py": BASE["tests/core/test_ok.py"] + "\ndef test_two():\n    assert 2\n"})
+    rc, rows = _run(repo, "--fact", "Gamma.")
+    assert rc == 0 and set(rows.values()) == {"OK", "PASS"}
+
+
+def test_product_tokens_are_not_attribution_words(repo):
+    # An HTTP header and pytest's own fixture scope / marker contain the words; they are exempt.
+    _commit(repo, _test('\nimport pytest\n\n\n@pytest.fixture(scope="' + "ses" + 'sion")\ndef f():\n'
+                        '    return {"User-' + "Ag" + 'ent": "x"}\n'))
+    rc, rows = _run(repo)
+    assert (rc, rows["attribution"]) == (0, "OK")
+
+
+def test_an_indented_line_after_a_gap_does_not_lengthen_a_bullet(repo):
+    # A continuation is contiguous: three indented lines after an unindented one are not the bullet's.
+    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace(
+        "## Unreleased\n", "## Unreleased\n\n- a\n\nNote\n  x\n  y\n  z\n")})
+    rc, rows = _run(repo)
+    assert (rc, rows["changelog"]) == (0, "OK")
+
+
+def test_an_indented_test_method_is_not_a_new_test(repo):
+    # Only a top-level `def test_` counts; a method needs no red-before wording of its own here.
+    _commit(repo, {"tests/core/test_ok.py": BASE["tests/core/test_ok.py"]
+                   + "\n\nclass TestX:\n    def test_m(self):\n        assert 1\n"}, msg="item\n")
+    rc, rows = _run(repo)
+    assert (rc, rows["tests"]) == (0, "OK")
+
+
+def test_without_a_budget_table_the_modules_line_is_not_applicable(repo):
+    _commit(repo, {"tests/repo/test_module_budgets.py": None,
+                   "lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"})
+    rc, rows = _run(repo)
+    assert (rc, rows["modules"]) == (0, "OK")
+
+
+def test_a_fact_is_looked_up_at_head_not_in_the_working_copy(repo):
+    # The working copy states the fact a second time, uncommitted: <head> does not, so OK.
+    _commit(repo, {"docs/a.md": "Alpha fact.\nGamma.\n"})
+    (repo / "docs" / "b.md").write_text("Beta.\nGamma.\n")
+    rc, rows = _run(repo, "--fact", "Gamma.")
+    assert (rc, rows["docs"]) == (0, "OK")
diff --git a/tools/check_houserules.py b/tools/check_houserules.py
new file mode 100644
index 00000000..f754ce96
--- /dev/null
+++ b/tools/check_houserules.py
@@ -0,0 +1,265 @@
+#!/usr/bin/env python3
+"""Print the HOUSE RULES KEPT block for one item's commit range. Reads git, writes nothing.
+
+    check_houserules.py <base>..<head> [--fact LITERAL]...
+
+One line per rule (changelog, docs, tests, patterns, markers, attribution, modules), each ending in
+OK, WARN or HARD, then `VERDICT PASS|FAIL`. Exit 1 when a line is HARD, 2 on a usage or git error.
+The exact conditions: tests/README.md rule 13 and the plan of this tool. WARN lines are judged by
+the reviewer; HARD lines block.
+"""
+from __future__ import annotations
+
+import argparse
+import ast
+import re
+import subprocess
+import sys
+
+LAYERS = (
+    ("tests/core/", "core"), ("tests/stacks/", "stacks"), ("tests/web/", "web"),
+    ("tests/cli/", "cli"), ("tests/install/", "install"), ("tests/host/", "host"),
+    ("tests/repo/", "repo"), ("tests/golden/", "golden"),
+    ("testlab/tests/unit/", "testlab"), ("testlab/tests/acceptance/", "testlab"),
+    ("testlab/tests/browser/", "testlab"), ("testlab/tests/release/", "testlab"),
+    ("testlab/tests/slowbuild/", "testlab"),
+    ("lhpc/data/meshcore_host/tests/", "in-package"), ("lhpc/data/rfdecode/tests/", "in-package"),
+)
+BUDGET_FILE = "tests/repo/test_module_budgets.py"
+FAMILY_DIR = "lhpc/core/"
+# Assembled from fragments so that no committed file spells the names it looks for.
+ATTRIBUTION = ("Clau" + "de", "Chat" + "GPT", "Anthro" + "pic", "Generated " + "with")
+TRAILER = re.compile("^" + "Co-" + "Authored-By:", re.IGNORECASE | re.MULTILINE)
+WORDS = re.compile(r"\b(" + "ag" + "ent" + "|" + "ses" + "sion" + r")\b", re.IGNORECASE)
+# Product tokens that contain those words and are not attribution: an HTTP header, pytest's fixture
+# scope and the suite's marker.
+WORD_EXEMPT = re.compile(r"user[-_]" + "ag" + "ent" + r'|scope="' + "ses" + 'sion"|needs_' + "ses" + "sion",
+                         re.IGNORECASE)
+HARD_PATTERNS = {"getsource": re.compile(r"inspect\.getsource\("),
+                 "assert-True": re.compile(r"^\s*assert True\b")}
+WARN_PATTERNS = {"source-read": re.compile(r"""(read_text|open)\(.*["']lhpc/[^"']+\.(py|js|css)["']"""),
+                 "sleep": re.compile(r"time\.sleep\("),
+                 "startswith": re.compile(r"assert .*\.startswith\("),
+                 "network": re.compile(r"\b(requests\.|urllib\.request|socket\.create_connection)")}
+EXEMPT = "# rule-1 exception"
+RED_BEFORE = re.compile(r"red[- ]before", re.IGNORECASE)
+RAISE_REASON = re.compile(r"#\s*\S+:\s*\S")
+
+
+def git(*args: str) -> str:
+    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout
+
+
+def show(rev: str, path: str) -> str | None:
+    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True, check=False)
+    return r.stdout if r.returncode == 0 else None
+
+
+def added_lines(diff: str) -> list[tuple[str, int, str]]:
+    """(path, new line number, text) of every added line in a `--unified=0` diff."""
+    out, path, n = [], None, 0
+    for line in diff.splitlines():
+        if line.startswith("+++ "):
+            path = line[6:] if line.startswith("+++ b/") else None
+        elif line.startswith("@@"):
+            n = int(re.search(r"\+(\d+)", line).group(1))
+        elif line.startswith("+") and path:
+            out.append((path, n, line[1:]))
+            n += 1
+    return out
+
+
+def is_test_file(path: str) -> bool:
+    return path.rsplit("/", 1)[-1].startswith("test_") and path.endswith(".py")
+
+
+def layer_of(path: str) -> str | None:
+    return next((layer for prefix, layer in LAYERS if path.startswith(prefix)), None)
+
+
+def measure(source: str) -> tuple[int, int]:
+    tree = ast.parse(source)
+    public = sum(1 for cls in tree.body if isinstance(cls, ast.ClassDef) for node in cls.body
+                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
+                 and not node.name.startswith("_"))
+    return len(source.splitlines()), public
+
+
+def budgets(source: str | None) -> dict | None:
+    if source is None:
+        return None
+    for node in ast.parse(source).body:
+        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BUDGETS" for t in node.targets):
+            return ast.literal_eval(node.value)
+    return None
+
+
+def check(base: str, head: str, facts: list[str], correction: bool = False) -> list[tuple[str, str, str]]:
+    rng = f"{base}..{head}"
+    added = added_lines(git("diff", "--unified=0", rng))
+    status = [line.split("\t") for line in git("diff", "--name-status", rng).splitlines()]
+    new_files = {p[-1] for p in status if p[0].startswith("A")}
+    changed = {p[-1] for p in status}
+    commits = [c.strip("\n").split("\x00", 2)
+               for c in git("log", "--format=%H%x00%an%x00%B%x1e", rng).split("\x1e") if c.strip()]
+    rows = []
+
+    # changelog
+    head_cl = (show(head, "CHANGELOG.md") or "").splitlines()
+    heads = [i + 1 for i, line in enumerate(head_cl) if line.startswith("## ")]
+    second = heads[1] if len(heads) > 1 else len(head_cl) + 1
+    top = (second - heads[0] - 1) if heads else 0
+    bullets, below, active, last = [], False, False, None
+    for path, n, text in added:
+        if path != "CHANGELOG.md":
+            continue
+        if text.startswith("- "):
+            bullets.append(1)
+            below = below or n >= second
+            active = True
+        elif text.startswith("  ") and active and n == last + 1:
+            bullets[-1] += 1                 # a continuation: directly below its bullet, indented
+        else:
+            active = False
+        last = n
+    long_bullet = any(b > 3 for b in bullets)
+    # `--correction`: an item that only corrects a released section says so, and its bullets
+    # below the top section are shown (WARN), not refused.
+    v = ("HARD" if len(bullets) > 2 or long_bullet or (below and not correction)
+         else "WARN" if below else "OK")
+    rows.append(("changelog", f"+{len(bullets)} bullets (limit 2, each ≤ 3 lines; longest "
+                              f"{max(bullets, default=0)}), top section {top} lines", v))
+
+    # docs
+    docs = sorted(p for p in changed if (p.startswith("docs/") and p.endswith(".md")) or p == "README.md")
+    doc_lines = sum(1 for p, _, _ in added if p in docs)
+    found, repeated = [], False
+    for fact in facts:
+        r = subprocess.run(["git", "grep", "-lF", "--", fact, head, "--", "docs", "README.md"],
+                           capture_output=True, text=True, check=False)
+        files = sorted({line.split(":", 1)[1] for line in r.stdout.splitlines()})
+        found.append(f'fact "{fact}" -> {",".join(files) or "none"}')
+        repeated = repeated or len(files) > 1
+    v = "WARN" if (docs and not facts) or repeated else "OK"
+    rows.append(("docs", f"{len(docs)} files, +{doc_lines} lines; " + ("; ".join(found) or "no fact given"), v))
+
+    # tests
+    new_tests = sorted(p for p in new_files if is_test_file(p))
+    unlayered = [p for p in new_tests if layer_of(p) is None]
+    layers = sorted({f"{p.rsplit('/', 1)[0]}={layer_of(p) or 'NONE'}" for p in changed if is_test_file(p)})
+    with_tests = stated = 0
+    for sha, _, msg in commits:
+        own = added_lines(git("show", "--unified=0", "--format=", sha))
+        if any(is_test_file(p) and re.match(r"(async )?def test_", t) for p, _, t in own):
+            with_tests += 1
+            stated += bool(RED_BEFORE.search(msg))
+    n_new = sum(1 for p, _, t in added if is_test_file(p) and re.match(r"(async )?def test_", t))
+    v = "HARD" if unlayered else "WARN" if stated < with_tests else "OK"
+    rows.append(("tests", f"{n_new} new tests; layers {' '.join(layers) or '-'}; "
+                          f"red-before {stated}/{with_tests}" + (f"; unlayered {' '.join(unlayered)}" if unlayered else ""), v))
+
+    # patterns
+    in_tests = [(p, n, t) for p, n, t in added if is_test_file(p) or layer_of(p)]
+    counts = dict.fromkeys((*HARD_PATTERNS, *WARN_PATTERNS), 0)
+    for _, _, text in in_tests:
+        if EXEMPT in text:
+            continue
+        for k, rx in (*HARD_PATTERNS.items(), *WARN_PATTERNS.items()):
+            counts[k] += bool(rx.search(text))
+    v = ("HARD" if any(counts[k] for k in HARD_PATTERNS)
+         else "WARN" if any(counts[k] for k in WARN_PATTERNS) else "OK")
+    rows.append(("patterns", " ".join(f"{k} {c}" for k, c in counts.items()), v))
+
+    # markers
+    unmarked = 0
+    by_file: dict[str, list[tuple[int, str]]] = {}
+    for p, n, t in in_tests:
+        by_file.setdefault(p, []).append((n, t))
+    for lines in by_file.values():
+        for i, (_, t) in enumerate(lines):
+            if not re.match(r"(async )?def test_", t):
+                continue
+            body = []
+            for _, b in lines[i + 1:]:
+                if b.strip() and not b[0].isspace():     # the next top-level statement ends it
+                    break
+                body.append(b)
+            text = "\n".join(body)
+            slow_work = "venv" in text or "pip install" in text or ("while" in text and "monotonic" in text)
+            above = []
+            for _, a in reversed(lines[:i]):
+                if not a.strip().startswith("@"):
+                    break
+                above.append(a)
+            unmarked += slow_work and not any("mark.slow" in a for a in above)
+    rows.append(("markers", f"{unmarked} without slow", "WARN" if unmarked else "OK"))
+
+    # attribution
+    authors = sorted({a for _, a, _ in commits})
+    trailers = sum(len(TRAILER.findall(msg)) for _, _, msg in commits)
+    texts = [msg for _, _, msg in commits] + [t for _, _, t in added]
+    words = sum(1 for t in texts for name in ATTRIBUTION if name.lower() in t.lower())
+    words += sum(len(WORDS.findall(WORD_EXEMPT.sub("", t))) for t in texts)
+    v = "HARD" if trailers or any(a != "makrohard" for a in authors) else "WARN" if words else "OK"
+    rows.append(("attribution", f"authors {','.join(authors) or '-'}; trailers {trailers}; words {words}", v))
+
+    # modules
+    table, before = budgets(show(head, BUDGET_FILE)), budgets(show(base, BUDGET_FILE)) or {}
+    if table is None:
+        rows.append(("modules", "N/A (budget table absent)", "OK"))
+        return rows
+    parts, over, net, excused, raised_bad, raised_ok = [], False, 0, 0, [], set()
+    net_public, excused_public = 0, 0
+    head_budget_lines = (show(head, BUDGET_FILE) or "").splitlines()
+    for name, value in table.items():
+        if name in before and any(v > b for v, b in zip(value, before[name], strict=True)):
+            line = next((ln for ln in head_budget_lines if f'"{name}"' in ln), "")
+            (raised_ok.add(name) if RAISE_REASON.search(line) else raised_bad.append(name))
+    for name in sorted(table):
+        path = FAMILY_DIR + name
+        if path not in changed:
+            continue
+        a = measure(show(base, path) or "")
+        b = measure(show(head, path) or "")
+        net += b[0] - a[0]
+        excused += max(0, b[0] - a[0]) if name in raised_ok else 0
+        net_public += b[1] - a[1]
+        excused_public += max(0, b[1] - a[1]) if name in raised_ok else 0
+        over = over or b[0] > table[name][0] or b[1] > table[name][1]
+        parts.append(f"{name} {a[0]}->{b[0]} lines (budget {table[name][0]}), "
+                     f"{a[1]}->{b[1]} public (budget {table[name][1]})")
+    # Only a raised file's own growth is excused, for lines and for public methods alike.
+    grown_unexcused = net - excused > 0 or net_public - excused_public > 0
+    v = ("HARD" if over or grown_unexcused or raised_bad
+         else "WARN" if net > 0 or net_public > 0 else "OK")
+    rows.append(("modules", "; ".join(parts or ["no family file touched"])
+                 + f"; family net {net:+d} lines, {net_public:+d} public" + (f"; raised without reason {','.join(raised_bad)}" if raised_bad else ""), v))
+    return rows
+
+
+def main(argv: list[str]) -> int:
+    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
+    ap.add_argument("range", help="<base>..<head>")
+    ap.add_argument("--fact", action="append", default=[], help="a literal to look for in docs/")
+    ap.add_argument("--correction", action="store_true",
+                    help="this item only corrects a released CHANGELOG section")
+    args = ap.parse_args(argv)
+    if ".." not in args.range:
+        ap.error("the range must be <base>..<head>")
+    base, head = args.range.split("..", 1)
+    try:
+        rows = check(base, head, args.fact, args.correction)
+    except subprocess.CalledProcessError as exc:
+        print(f"git failed: {' '.join(exc.cmd)}: {exc.stderr.strip()}", file=sys.stderr)
+        return 2
+    hard = sum(v == "HARD" for _, _, v in rows)
+    warn = sum(v == "WARN" for _, _, v in rows)
+    print(f"HOUSE RULES KEPT  {args.range}")
+    for name, text, verdict in rows:
+        print(f"{name:<12} {text}   {verdict}")
+    print(f"{'VERDICT':<12} {'FAIL' if hard else 'PASS'}  (hard {hard}, warn {warn})")
+    return 1 if hard else 0
+
+
+if __name__ == "__main__":
+    sys.exit(main(sys.argv[1:]))
```
