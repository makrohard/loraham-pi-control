# Gate 1: houserules-2 (packet P2 of PLAN-houserules-kept v2)

**Request.** Judge the one code commit against the plan's §3 (the ratchet) and the README paragraph. That means:
- whether the test measures what it claims (lines; public methods = functions directly in a class body not starting with `_`);
- whether the table equals the composed family;
- whether the negative tests show it can fail;
- whether rule 13 states exactly what is enforced;
- the report's claims against the diff.

The plan passed gate 1 (round 4, GREEN WITH NOTES); v2.1 applies its two notes, including the order P2 (this) before P1 (checker).

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied; the diff is `git diff` output verbatim and contains no home path, LAN address, host name or call sign.

## Commits
```
8f41c078 houserules-2: code report
c8f9c631 houserules-2: the service family does not grow (budget test, tests/README rule 13)
```

## Commit message of c8f9c631
```
houserules-2: the service family does not grow (budget test, tests/README rule 13)

tests/repo/test_module_budgets.py caps lines and public methods of services.py
and every service_* module it imports relatively (service_base.py and the 14
mixins), at the values measured on dev (0.12.9); service_maintenance.py is
raised to 2891 for testlab-det-1 (S1 keeps curl rc and message, +8 lines),
with that reason on its line; a second case keeps the table
equal to that import set. tests/README.md gains rule 13: the ceilings are
tested; "raise only with a reason on the budget line" and "an item removes at
least what it adds" are review rules this test does not check.

New invariant: no parent-red exists, the table is new. Negative tests, run once
and reverted: service_params.py lines 3380 -> budget 3379 gives
"AssertionError: service_params.py: 3380 lines / 54 public methods, budget 3379 / 54";
public 54 -> budget 53 gives "... budget 3380 / 53". The counter has its own
fixture case (9 lines, 2 public methods).

```

## The report

> # Code report: houserules-2 (packet P2 of PLAN-houserules-kept v2)
> 
> **Branch:** `cons/houserules-2-r1` on `origin/dev` = 254bbae3 (0.12.9 + testlab-det-1). One code commit, c8f9c631.
> 
> ## Commit
> | commit | files | tests | red-before |
> |---|---|---|---|
> | c8f9c631 | tests/repo/test_module_budgets.py (new), tests/README.md (rule 13) | `test_service_family_within_budget` (16 cases), `test_the_table_covers_the_whole_family`, `test_measure_counts_lines_and_only_public_methods_of_classes` | none claimed: a new invariant. Negative tests (run once, reverted; in the commit message): a lines budget 1 below the actual and a public-methods budget 1 below the actual each fail the named case with `AssertionError: service_params.py: 3380 lines / 54 public methods, budget 3379 / 54` (resp. `3380 / 53`) |
> 
> ## The 6-point block
> 1. **Contracts:**
>    - `BUDGETS[file] = (max_lines, max_public_methods)`;
>    - lines = `len(source.splitlines())`;
>    - public methods = `ast.FunctionDef`/`AsyncFunctionDef` directly in a module-level `ClassDef` body whose name does not start with `_`;
>    - the table's key set = the relative `from .service_* import` modules of `services.py` plus `services.py` and `service_base.py`.
> 2. **Invariants:** no product behaviour changes (tests and README only). The new repository invariant is tested by the three functions above.
> 3. **Failure classes:**
>    - A parallel item that grows a family file fails this test after its landing order. **Known now:** testlab-det-1 (S1) adds +8 lines to `service_maintenance.py`. Whichever lands second re-measures that one value and carries `# <item>: <reason>` on that line.
>    - A file renamed or added to `services.py`'s `service_*` imports fails `test_the_table_covers_the_whole_family` until the table follows.
> 4. **Test rules:**
>    - Rule 1 is broken on purpose: the module reads production source to measure size, which rule 13 sanctions and the module docstring states.
>    - Assertions compare numbers, not prose.
>    - No network, no sleeps.
> 5. **Whole directory:** `python -m pytest -q -p no:cacheprovider -n 8 tests/repo` → `546 passed, 1 skipped in 3.54s`. `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`. No signature changed.
> 6. **Self-review (found and fixed):** the first draft compared `(lines, public) <= (max, max)` as tuples (lexicographic, so the public budget could pass wrongly); it is now two separate comparisons. Nothing else found. The docs (README rule 13) state exactly what the test enforces. The raise-needs-a-reason rule is the checker's (P1), not this test's, and the paragraph does not claim the test enforces it.
> 
> **Second read (applied before gate 1):** the budget-values row now carries its command; the family is named as `services.py`'s relative `service_*` imports everywhere (docstring, rule 13, commit message), which is what the test checks; rule 13 and the docstring mark "raise with a reason" and "remove at least what it adds" as review rules this test does not check.
> 
> **Gate 1 round 5 notes (applied, no new round):** the completeness case walks the whole tree (`ast.walk`, `level == 1`, `service_*`), so a relative import inside a function counts too; rule 13 now says "imports relatively", as the test and this report do.
> 
> **Work round W1b notes (applied before landing):**
> - family membership: the completeness case now runs through `family(services_source)`, and a negative fixture shows a new unbudgeted mixin and a removed entry both break the equality;
> - "static ceilings": rule 13 now says the test enforces fixed caps and never lowers one; lowering is free and a review rule;
> - the item-B points on the checker (CHANGELOG lines, `--fact` at `<head>`, public-method growth) belong to houserules-1.
> 
> **CHANGELOG:** none (tests and contributor docs; nothing an operator sees).
> 
> **Guardrails:** one table, one helper function, three tests; stdlib `ast`; no new module outside `tests/repo`.
> 
> ## Numbers, measured
> | figure | command | output |
> |---|---|---|
> | diff | `git diff --stat origin/dev..c8f9c631` | 2 files changed, 94 insertions(+) |
> | test file | `wc -l tests/repo/test_module_budgets.py` | 88 |
> | budget values | `python3 -c 'import ast,sys; t=open(sys.argv[1]).read(); print(len(t.splitlines()), sum(1 for c in ast.parse(t).body if isinstance(c, ast.ClassDef) for n in c.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and not n.name.startswith("_")))' lhpc/core/<f>.py` on eb9673a4, per file; service_maintenance.py re-measured on 254bbae3 | as in the table (e.g. service_lifecycle_ops 6315 73; service_maintenance 2891 30) |
> | new module alone | `pytest -q tests/repo/test_module_budgets.py` | 20 passed in 0.20s |
> | directory after the amend | `pytest -q -n 8 tests/repo` | 546 passed, 1 skipped in 3.54s |
> | negative runs | the two edits above | 1 failed, 17 passed (each) |

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

## The full diff (`git diff origin/dev c8f9c631`)

```diff
diff --git a/tests/README.md b/tests/README.md
index e475cce9..e1c17294 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -107,6 +107,12 @@ through `interrupts.py`: its module pins the operation's durable writes and fail
     call the real one would refuse (`repo/test_fake_signatures.py`).
 12. **A regression test is red before its fix.** Run it once against the parent of the fix and say
     so in the commit; a case that passes on both sides is a control and is labelled as one.
+13. **The service family does not grow.** `repo/test_module_budgets.py` caps the lines and public
+    methods of `services.py` and every `service_*` module it imports relatively (a repository
+    invariant, so it may read their source). Review rules the test does not check: new behaviour
+    goes into a plain function module, not a mixin; an item that touches the family removes at
+    least what it adds; a budget is raised only with `# <item id>: <reason>` on its line. Lowering a
+    budget is free; the test enforces fixed caps and never lowers one by itself.
 
 **Coverage is diagnostic, not the target:** a new test should normally accompany a behaviour or a
 defect; no test added or kept only because it covers lines.
diff --git a/tests/repo/test_module_budgets.py b/tests/repo/test_module_budgets.py
new file mode 100644
index 00000000..cc950735
--- /dev/null
+++ b/tests/repo/test_module_budgets.py
@@ -0,0 +1,88 @@
+"""The service family does not grow (tests/README.md, rule 13).
+
+The family is `lhpc/core/services.py` and every `service_*` module it imports relatively
+(`service_base.py` and the mixins). Each file has a ceiling for its lines and for its public methods
+(functions defined directly in a class body whose name does not start with `_`). This test enforces
+the ceilings only. A raise with `# <item id>: <one sentence>` on its line, and an item removing at
+least what it adds, are review rules this test does not check; lowering a value is free.
+
+A repository invariant, not behaviour: it reads the production files to measure their size, which
+rule 1 otherwise forbids; nothing it asserts is about what the code does.
+"""
+import ast
+
+import pytest
+
+import repo_paths
+
+CORE = repo_paths.REPO / "lhpc" / "core"
+
+# file: (max lines, max public methods) — measured on dev at 0.12.9, raised where a line says why
+BUDGETS = {
+    "services.py": (2959, 28),
+    "service_base.py": (166, 0),
+    "service_auto_install.py": (1875, 16),
+    "service_binary_channel.py": (297, 19),
+    "service_binary_ops.py": (1077, 5),
+    "service_boot_restore.py": (678, 2),
+    "service_firewall.py": (1533, 19),
+    "service_hmac.py": (1016, 17),
+    "service_lifecycle_ops.py": (6315, 73),
+    "service_maintenance.py": (2891, 30),  # testlab-det-1: S1 keeps curl rc and message
+    "service_network.py": (1183, 9),
+    "service_params.py": (3380, 54),
+    "service_secrets.py": (399, 2),
+    "service_selfupdate.py": (1932, 22),
+    "service_system.py": (759, 2),
+    "service_webserver.py": (2388, 42),
+}
+
+
+def measure(source: str) -> tuple[int, int]:
+    """(lines, public methods) of one module's source."""
+    tree = ast.parse(source)
+    public = sum(1 for cls in tree.body if isinstance(cls, ast.ClassDef)
+                 for node in cls.body
+                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
+                 and not node.name.startswith("_"))
+    return len(source.splitlines()), public
+
+
+def family(services_source: str) -> set:
+    """`services.py` and every `service_*` module it imports relatively."""
+    return {"services.py"} | {f"{node.module}.py" for node in ast.walk(ast.parse(services_source))
+                              if isinstance(node, ast.ImportFrom) and node.level == 1
+                              and (node.module or "").startswith("service_")}
+
+
+def test_the_table_covers_the_whole_family():
+    # Every module of the family has a budget, and the table names no other file: a new mixin
+    # without an entry fails here, and so does an entry removed while its module stays.
+    assert family((CORE / "services.py").read_text()) == set(BUDGETS)
+
+
+@pytest.mark.parametrize("source, missing, extra", [
+    ("from .service_base import X\nfrom .service_new import Y\n", {"service_new.py"}, set()),
+    ("from .service_base import X\n", set(), set()),
+])
+def test_an_unbudgeted_mixin_or_a_removed_entry_breaks_the_equality(source, missing, extra):
+    # Negative fixture for the completeness rule (a new invariant: no parent-red exists).
+    table = {"services.py", "service_base.py", "service_gone.py"}
+    found = family(source)
+    assert (found - table, table - found) == (missing, {"service_gone.py"} | extra)
+
+
+@pytest.mark.parametrize("name", sorted(BUDGETS))
+def test_service_family_within_budget(name):
+    lines, public = measure((CORE / name).read_text())
+    max_lines, max_public = BUDGETS[name]
+    assert lines <= max_lines and public <= max_public, (
+        f"{name}: {lines} lines / {public} public methods, budget {max_lines} / {max_public}")
+
+
+def test_measure_counts_lines_and_only_public_methods_of_classes():
+    # Negative/positive fixture for the counter itself (a new invariant: no parent-red exists).
+    source = ("def top():\n    pass\n"
+              "class A:\n    def run(self):\n        pass\n    def _helper(self):\n        pass\n"
+              "    async def fetch(self):\n        pass\n")
+    assert measure(source) == (9, 2)
```
