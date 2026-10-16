# Code report: doc-pointers (owners named by file and symbol, checked by a repo test) — v3

Branch `cons/doc-pointers-r1` on the 0.12.10 release commit `f6b7940e` (itself on `origin/dev` = `0a7fb61d`; it lands
after the release, 0.12.11 content). Decision: the maintainer's choice on the doc-pointer note: docs/architecture.md
keeps `path` + symbol name and drops the line numbers; a small tests/repo test asserts each named symbol exists in its
file and fails on a rename or a removal. After gate 1 round 4 (rule 1), the maintainer chose to add a second, narrow
exception to tests/README.md rule 1 for this kind of test. Docs, the rule and one test, no CHANGELOG line (tooling).

**v3 against v2 (packet sha256 d3236118…, commit 91a62ac1):** gate 1 round 4 was RED on one policy point: rule 1
forbids a test that reads production `.py` files, and the docstring's "repository invariant, not behaviour" is not
its exception. tests/README.md rule 1 gets a second exception, verbatim: "Second exception: a
documentation-integrity test may assert that a symbol named by `docs/` is defined in its file; it asserts existence,
never behaviour or source text." The docstring points to it (`git diff 91a62ac1 25c7d657 --stat` →
`2 files changed, 5 insertions(+), 3 deletions(-)`); the commit message names the rule change. The test's code is unchanged.

**v2 against v1 (packet sha256 50fedac5…, commit 73d80fb6):** the second read found that the definition pattern's
assignment branch matched indented keyword-argument lines, so renaming `def build` in
`lhpc/core/service_lifecycle_ops.py` still passed (`build=False),` at line 6264). The pattern is now anchored: `def`/
`class` (optionally `async`) at line start after indentation, assignments at column 0. One line of the test changed
(`git diff 73d80fb6 91a62ac1 --stat` → `1 file changed, 1 insertion(+), 1 deletion(-)`); the two new red cases, the
6-point items 3 and 6 and the test description below are updated. Nothing else changed.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| 25c7d657 | docs/architecture.md (38 lines), tests/README.md (rule 1, +3/−1), tests/repo/test_doc_owner_pointers.py (new, 45 lines) | repo layer: `test_every_named_owner_is_defined_in_its_file`, `test_owner_pointers_carry_no_line_numbers` | both fail on the parent's doc (below) |

## HOUSE RULES KEPT (tools/check_houserules.py f6b7940e..HEAD --fact "the owning function's file and name")

```
HOUSE RULES KEPT  f6b7940e..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 31 lines   OK
docs         1 files, +38 lines; fact "the owning function's file and name" -> docs/architecture.md   OK
tests        2 new tests; layers tests/repo=repo; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

The checker's docs row reads `docs/` and `README.md` only; the tests/README.md rule-1 line is shown in the diff.

## The rewrite is mechanical

The edit was made by a script: `` `(lhpc|testlab)/….py:N` ` `` → `` `(lhpc|testlab)/….py` ` `` (62 replacements) and
`` , `:N` ` `` → `` , ` `` (38 replacements), plus one sentence of the table's intro. Proof: the same two substitutions
applied to `git show f6b7940e:docs/architecture.md` give a file of 403 lines that differs from the commit's
`docs/architecture.md` (403 lines) in exactly one line, the intro:

```text
- the row says where it is enforced (`file:line` of the owning function), which locks it takes, what
+ the row says where it is enforced (the owning function's file and name), which locks it takes, what
```

All 38 continuations followed ", " before the edit (`grep -oE '.{2}`:[0-9]+`'` → 38 × `, `). No other doc carries a
`path:N` pointer: `grep -rlE '\.py:[0-9]+`' --include=*.md .` lists only frozen review records under `code-review/`.
The second read checked that every one of the 100 old `path:N` pointers hit the definition line of its named symbol
at f6b7940e (100/100), so each name kept is the right symbol.

## The test

`_POINTER` reads `` `path` `name` `` followed by any `` , `name` `` in each table row; each name must match
`^(\s*(async\s+)?(def|class)\s+{name}\b|{name}\s*[:=])` (multiline) in its file: a `def`/`class` at line start after
indentation, or an assignment at column 0 (both assignment owners, `SNAPSHOT_NEUTRAL` and `SNAPSHOT_NEUTRAL_WRITERS`,
are at column 0). The test reads 100 pointers on 25c7d657 (`list(_pointers())` → 100), and each matches exactly one
line of its file (the per-pointer count of matching lines → `{1: 100}`). The second test fails on any table row with
`` .py:N` `` or `` `:N` ``: such a pointer would not match `_POINTER` and would go unchecked (the re-added case below
shows it: the first test still passes). The docstring points to rule 1's second exception,
which it names.

## Red cases (on a clean detached checkout of 25c7d657; each edit reverted, `git status --short` empty after)

| case | edit | result on 25c7d657 | on 73d80fb6 (v1 pattern) |
|---|---|---|---|
| green | none | `2 passed` | `2 passed` |
| doc rename | `operation_lock` → `operation_lockX` in the doc | `names owners not defined there: lhpc/core/reslock.py `operation_lockX`` — 1 failed, 1 passed | same |
| missing file | `testlab/lhpc_testlab/ops.py` → `opsX.py` in the doc | `… testlab/lhpc_testlab/opsX.py `power`` — 1 failed, 1 passed | same |
| source rename | `def _retire_body(` → `def _retire_bodyX(` in `lhpc/core/service_binary_ops.py` | `… lhpc/core/service_binary_ops.py `_retire_body`` — 1 failed, 1 passed | same |
| source rename, name also a kwarg | `    def build(` → `    def build_x(` in `lhpc/core/service_lifecycle_ops.py` | `… lhpc/core/service_lifecycle_ops.py `build`` — 1 failed, 1 passed | `2 passed` (the v1 gap) |
| source rename, old name in a comment | `def operation_lock(` → `# replaces def operation_lock` + `def operation_lock_x(` in `lhpc/core/reslock.py` | `… lhpc/core/reslock.py `operation_lock`` — 1 failed, 1 passed | `2 passed` (the v1 gap) |
| re-added `:N` | `` `lhpc/core/assets.py` `` → `` `lhpc/core/assets.py:20` `` | `a `path:N` pointer drifts and escapes the check above: ['| install / recovery | a wheel installed into a fresh venv r']` — 1 failed, 1 passed | same |
| parent doc | `git show f6b7940e:docs/architecture.md` | `assert []` and the numbered-rows message — 2 failed | same |

A public name (`asset_path`) could not serve as the source-rename case: the rename breaks `tests/conftest.py`'s import
before collection. The private `_retire_body` is only called inside its module.

## The 6-point block

1. **Contracts:** none (docs and a repo test). 2. **Invariants:** every owner the invariant table names is defined in
   the file it names.
3. **Failure classes:** a rename, a removal or a move to another file of a named owner now fails tests/repo, also
   when the old name survives as a keyword argument, a comment or any other indented non-definition line (each of
   the 100 names matches exactly one line today); an edit above an owner no longer touches the doc.
4. **Test rules:** rule 1, with its new second exception (this test is its only user); rule 13 not touched.
5. **Directories:** `python -m pytest tests/repo -q -n 12 -p no:cacheprovider` → `620 passed, 1 skipped` (616 before:
   the 2 new tests and 2 `test_suite_hygiene.py` parametrizations over the new file, by a `--co` diff against
   f6b7940e); `ruff check tests/repo/test_doc_owner_pointers.py` → clean.
6. **Adversarial self-review:**
   - v1's pattern (`\b(def|class)\s+{name}\b|^\s*{name}\s*[:=]`) let a rename pass when the old name stayed on an
     indented `name=` line or after `def` in a comment; true for `build` today. Found by the second read, fixed in v2.
   - The existence check is file-wide: an owner moved to another class in the same file still passes, and so does
     a column-0 assignment of the same name. That the named function still enforces the row's invariant stays a
     reading check, as before.
   - Only the grammar `` `path` `name`(, `name`)* `` is read. A continuation written another way (`` and `b` ``) is
     not seen; today all 38 use ", " (measured above).
   - The numbered check guards `` `path:N` `` and `` `:N` `` in table rows only; `path:N-M`, a `.js:N` pointer and a
     pointer in prose would escape it (the second read measured each at `2 passed`). None exists today.
   - The numbered-rows message names a row by its first 60 characters, not the pointer.
   - Slip: after the first red run, a `git checkout docs/architecture.md` meant to undo the red edit also reverted
     the uncommitted rewrite. It was reapplied by the same script (62/38 again) before the commit; the mechanical
     proof above was run on the committed file.
   - v2's docstring claimed "a repository invariant, not behaviour", the wording of `test_module_budgets.py`, but
     rule 1 had no such exception (gate 1 round 4). v3 names the exception the rule now has. `test_module_budgets.py`
     keeps its own sentence; it is backed by rule 13, not by rule 1.
   - Amends: the handler first got `eb660169`; `f3078165` added the rule-1 sentence to the docstring; `73d80fb6` added
     the red-before paragraph to the commit message (v1 packet); `91a62ac1` is the anchored pattern (v2);
     `25c7d657` adds the rule-1 exception (this v3).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h HEAD~1` | f6b7940e |
| pointers before | the doc-pointer resolver on f6b7940e | 100 ok, 0 stale |
| pointers read by the test | `list(_pointers())` on 25c7d657 | 100 |
| matching lines per pointer | the test's pattern, `re.findall` per pointer | `{1: 100}` |
| replacements | the rewrite script | 62 + 38 |
| numbered rows before / after | the second test's pattern on the parent / commit | 37 / 0 |
| tests/repo | `pytest tests/repo -q -n 12` | 620 passed, 1 skipped |
