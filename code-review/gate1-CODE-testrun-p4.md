# Gate 1: testrun-p4 (the firewall helper's stdlib-only rule gets its behavioural twin)

**Request.** Judge the one commit below against PLAN-testrun v3 §3 P4 (excerpt below; the plan passed gate 1 GREEN WITH NOTES). Judge: does the twin prove the helper runs with the standard library only (copied alone, `-I -S`, no lhpc or third-party package reachable) and can it fail (the negative run); is the guard right; are the two unguarded loops really over literal lists; is any claim stronger than the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## The plan, §3 P4 (PLAN-testrun v3)

> | P4 firewall file | `host/test_firewall.py` | the twin: the helper copied alone to tmp, `python3 -I <copy>` as non-root (`needs_nonroot`) reaches its root refusal and prints no `ModuleNotFoundError`; the docstring names the rule-1 exception; + the P2 guard on `:170`, `:185`, `:3012` | the twin fails with `import lhpc` injected into the copy; each guard fails with its iterable empty |

## Commit (full message, `git log -1 --format=%B`)

### 65ec6e6a testrun-p4: the firewall helper's stdlib-only rule gets its behavioural twin

```text
testrun-p4: the firewall helper's stdlib-only rule gets its behavioural twin

test_helper_imports_stdlib_only reads the helper's source to check its
imports (a whole-module negative invariant, the rule-1 exception of
tests/README.md), but had no behavioural twin. The new
test_the_helper_runs_alone_with_the_standard_library_only copies the helper
alone into an empty directory and runs it with `python -I -S` (no
site-packages: no lhpc, no third-party package); it must reach its root
refusal (EXIT_INTERNAL, empty stderr), which main() makes only after every
module-level import resolved. The source-reading test now says it is the
rule-1 exception, names its twin, and asserts it found imports before it
checks them (rule 10).

The plan's other two firewall loops (the validator corpus, the containment
cases) iterate literal lists built in the test: no guard.

Test-suite change, no behaviour: no parent-red. Negative run instead: the
twin fails with `import lhpc` or `import flask` added to the copy
(ModuleNotFoundError); the guard fails with the import list emptied.

```

## The report

> # Code report: testrun-p4 (the firewall helper's stdlib-only rule gets its behavioural twin)
>
> Branch `cons/testrun-p4-r1` on `origin/dev` = `d77e34f4`. Plan: PLAN-testrun v3 §3 P4 (gate 1 GREEN WITH NOTES).
> Tests only; no production file. Slow-marker decisions for this file moved to P6 on the handler's line.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 65ec6e6a | tests/host/test_firewall.py | host layer: new `test_the_helper_runs_alone_with_the_standard_library_only` asserts `(returncode, stderr) == (EXIT_INTERNAL, "")` for the helper run alone with `python -I -S`; `test_helper_imports_stdlib_only` asserts its import list is non-empty before checking it | none: a test-suite change (the twin of an existing invariant); negative run instead (below) |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
> docs         0 files, +0 lines; no fact given   OK
> tests        1 new tests; layers tests/host=host; red-before 0/1   WARN
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 1)
> ```
>
> The WARN, answered: `red-before 0/1` — the new test is the behavioural twin of an existing invariant (no behaviour
> changed, so no parent can be red); the commit says so and shows the negative run instead.
>
> ## What changed
>
> - **The twin** (`test_firewall.py`, after `test_helper_imports_stdlib_only`): the helper is copied alone to
>   `tmp_path/firewall-helper.py` and run as `[sys.executable, "-I", "-S", <copy>]` with `cwd=tmp_path` and
>   `env={"PATH": "/usr/bin:/bin"}`. `-I` drops the script's directory, PYTHON* variables and the user site; `-S`
>   drops site-packages, so no lhpc and no third-party package can be imported. As non-root (`needs_nonroot`) the
>   helper's `main()` returns `EXIT_INTERNAL` (70) at its first statement, the root check, which runs only after
>   the module and every module-level import loaded. Asserted: exit 70 and an empty stderr (no traceback).
> - **The source-reading test:** its docstring names the rule-1 exception and the twin; the read line carries the
>   checker's `# rule-1 exception` marker; it collects the import nodes first and asserts the list is non-empty
>   (rule 10), then checks each as before.
> - **Not changed:** `test_helper_validator_agrees_with_controller_validator` (loops over `corpus` and `bad`, lists
>   built in the test) and `test_firewall_containment_verdicts` (loops over the literal `cases` list): no guard.
>
> ## Negative run
>
> | change in a temporary copy of the test file | result |
> |---|---|
> | the twin, with `import lhpc` inserted after the helper copy's `from __future__` line | 1 failed: `ModuleNotFoundError: No module named 'lhpc'` in stderr |
> | the twin, with `import flask` inserted there | 1 failed: `ModuleNotFoundError: No module named 'flask'` |
> | the source test, with its guard `assert imports` → `assert []` | 1 failed: `assert []` |
>
> A first injection placed the import above `from __future__ import annotations`, which fails as a SyntaxError,
> not as the import; it was redone after that line (the table).
>
> ## The 6-point block
>
> 1. **Contracts:** none changed.
> 2. **Invariants:** the helper loads and runs with the standard library only (the twin, now behavioural).
> 3. **Failure classes:** a root CI runner would skip the twin (`needs_nonroot`), as the marker states; a helper whose
>    first statement changes from the root check fails the twin's exit code, which is then updated with it.
> 4. **Test rules:** rule 1 (the exception named, twin added), rule 10 (the guard).
> 5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/host tests/repo` →
>    `1282 passed, 1 skipped in 63.97s`; `ruff check lhpc testlab` and `ruff check tests --select F,E9` →
>    `All checks passed!`.
> 6. **Adversarial self-review:** the twin proves the imports of the module body only; an import inside a function
>    that `main()` never reaches as non-root is still covered only by the source test (stated in its docstring as its
>    reason to exist).
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | d77e34f4 |
> | diff | `git diff --stat origin/dev 65ec6e6a` | 1 file changed, 24 insertions(+), 3 deletions(-) |
> | `EXIT_INTERNAL` | `grep -n 'EXIT_INTERNAL =' lhpc/core/firewall_helper.py` | 1042: 70 |
> | directories | the command in 5 | 1282 passed, 1 skipped |

## The full diff (`git diff d77e34f4c775d85ba0822b373ec4243651be5cc9 65ec6e6a30047bde1d0a670f7346dd974336073f`)

```diff
diff --git a/tests/host/test_firewall.py b/tests/host/test_firewall.py
index b2eae726..ff1e9aa1 100644
--- a/tests/host/test_firewall.py
+++ b/tests/host/test_firewall.py
@@ -169,12 +169,16 @@ def test_intent_hash_refuses_invalid_candidates():
 
 def test_helper_imports_stdlib_only():
     """TRUST BOUNDARY: the installed helper must never import lhpc checkout code. Assert the
-    module source imports only an allowlisted stdlib set."""
+    module source imports only an allowlisted stdlib set. A whole-module negative invariant
+    (tests/README.md rule 1 exception: no driven path proves an import that is never made); its
+    behavioural twin is `test_the_helper_runs_alone_with_the_standard_library_only`."""
     import ast
-    src = (repo_paths.REPO / "lhpc" / "core" / "firewall_helper.py").read_text()
+    src = (repo_paths.REPO / "lhpc" / "core" / "firewall_helper.py").read_text()  # rule-1 exception
     allowed = {"hashlib", "ipaddress", "json", "re", "os", "sys", "fcntl", "subprocess",
                "time", "tempfile", "ctypes", "errno", "stat", "__future__"}
-    for node in ast.walk(ast.parse(src)):
+    imports = [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))]
+    assert imports                                                    # something is checked
+    for node in imports:
         if isinstance(node, ast.Import):
             for a in node.names:
                 assert a.name.split(".")[0] in allowed, a.name
@@ -182,6 +186,23 @@ def test_helper_imports_stdlib_only():
             assert (node.module or "").split(".")[0] in allowed, node.module
 
 
+@pytest.mark.needs_nonroot
+def test_the_helper_runs_alone_with_the_standard_library_only(tmp_path):
+    """The behavioural twin of `test_helper_imports_stdlib_only`: the helper copied alone into an
+    empty directory and run by an interpreter without site-packages (`-I -S`: no lhpc, no
+    third-party package) reaches its own first decision, the root refusal (`EXIT_INTERNAL`), which
+    `main()` makes only after every module-level import resolved."""
+    import shutil
+    import subprocess
+    import sys
+    from lhpc.core import firewall_helper as fh
+    copy = tmp_path / "firewall-helper.py"
+    shutil.copyfile(repo_paths.REPO / "lhpc" / "core" / "firewall_helper.py", copy)
+    r = subprocess.run([sys.executable, "-I", "-S", str(copy)], cwd=tmp_path, capture_output=True,
+                       text=True, timeout=30, env={"PATH": "/usr/bin:/bin"}, check=False)
+    assert (r.returncode, r.stderr) == (fh.EXIT_INTERNAL, ""), r.stderr
+
+
 def test_helper_validator_agrees_with_controller_validator():
     """The schema duplication across the trust boundary is deliberate — this drift test keeps
     both validators in behavioral lockstep over a shared corpus."""
```
