# Code report: houserules-2 (packet P2 of PLAN-houserules-kept v2)

**Branch:** `cons/houserules-2-r1` on `origin/dev` = 254bbae3 (0.12.9 + testlab-det-1). One code commit, c8f9c631.

## Commit
| commit | files | tests | red-before |
|---|---|---|---|
| c8f9c631 | tests/repo/test_module_budgets.py (new), tests/README.md (rule 13) | `test_service_family_within_budget` (16 cases), `test_the_table_covers_the_whole_family`, `test_measure_counts_lines_and_only_public_methods_of_classes` | none claimed: a new invariant. Negative tests (run once, reverted; in the commit message): a lines budget 1 below the actual and a public-methods budget 1 below the actual each fail the named case with `AssertionError: service_params.py: 3380 lines / 54 public methods, budget 3379 / 54` (resp. `3380 / 53`) |

## The 6-point block
1. **Contracts:**
   - `BUDGETS[file] = (max_lines, max_public_methods)`;
   - lines = `len(source.splitlines())`;
   - public methods = `ast.FunctionDef`/`AsyncFunctionDef` directly in a module-level `ClassDef` body whose name does not start with `_`;
   - the table's key set = the relative `from .service_* import` modules of `services.py` plus `services.py` and `service_base.py`.
2. **Invariants:** no product behaviour changes (tests and README only). The new repository invariant is tested by the three functions above.
3. **Failure classes:**
   - A parallel item that grows a family file fails this test after its landing order. **Known now:** testlab-det-1 (S1) adds +8 lines to `service_maintenance.py`. Whichever lands second re-measures that one value and carries `# <item>: <reason>` on that line.
   - A file renamed or added to `services.py`'s `service_*` imports fails `test_the_table_covers_the_whole_family` until the table follows.
4. **Test rules:**
   - Rule 1 is broken on purpose: the module reads production source to measure size, which rule 13 sanctions and the module docstring states.
   - Assertions compare numbers, not prose.
   - No network, no sleeps.
5. **Whole directory:** `python -m pytest -q -p no:cacheprovider -n 8 tests/repo` → `546 passed, 1 skipped in 3.54s`. `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`. No signature changed.
6. **Self-review (found and fixed):** the first draft compared `(lines, public) <= (max, max)` as tuples (lexicographic, so the public budget could pass wrongly); it is now two separate comparisons. Nothing else found. The docs (README rule 13) state exactly what the test enforces. The raise-needs-a-reason rule is the checker's (P1), not this test's, and the paragraph does not claim the test enforces it.

**Second read (applied before gate 1):** the budget-values row now carries its command; the family is named as `services.py`'s relative `service_*` imports everywhere (docstring, rule 13, commit message), which is what the test checks; rule 13 and the docstring mark "raise with a reason" and "remove at least what it adds" as review rules this test does not check.

**Gate 1 round 5 notes (applied, no new round):** the completeness case walks the whole tree (`ast.walk`, `level == 1`, `service_*`), so a relative import inside a function counts too; rule 13 now says "imports relatively", as the test and this report do.

**Work round W1b notes (applied before landing):**
- family membership: the completeness case now runs through `family(services_source)`, and a negative fixture shows a new unbudgeted mixin and a removed entry both break the equality;
- "static ceilings": rule 13 now says the test enforces fixed caps and never lowers one; lowering is free and a review rule;
- the item-B points on the checker (CHANGELOG lines, `--fact` at `<head>`, public-method growth) belong to houserules-1.

**CHANGELOG:** none (tests and contributor docs; nothing an operator sees).

**Guardrails:** one table, one helper function, three tests; stdlib `ast`; no new module outside `tests/repo`.

## Numbers, measured
| figure | command | output |
|---|---|---|
| diff | `git diff --stat origin/dev..c8f9c631` | 2 files changed, 94 insertions(+) |
| test file | `wc -l tests/repo/test_module_budgets.py` | 88 |
| budget values | `python3 -c 'import ast,sys; t=open(sys.argv[1]).read(); print(len(t.splitlines()), sum(1 for c in ast.parse(t).body if isinstance(c, ast.ClassDef) for n in c.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and not n.name.startswith("_")))' lhpc/core/<f>.py` on eb9673a4, per file; service_maintenance.py re-measured on 254bbae3 | as in the table (e.g. service_lifecycle_ops 6315 73; service_maintenance 2891 30) |
| new module alone | `pytest -q tests/repo/test_module_budgets.py` | 20 passed in 0.20s |
| directory after the amend | `pytest -q -n 8 tests/repo` | 546 passed, 1 skipped in 3.54s |
| negative runs | the two edits above | 1 failed, 17 passed (each) |
