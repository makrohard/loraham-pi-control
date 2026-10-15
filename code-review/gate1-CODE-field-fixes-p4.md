# Gate 1: field-fixes-p4 (plan item P4, the nits: Ctrl-C)

**Request.** Judge the one code commit below against the plan's item P4 (quoted). That means: correctness; whether
each red-before test can fail for the defect named; whether behaviour changes only where the plan says; the docs and
CHANGELOG lines; and the report's claims against the diff. The plan is in its own gate-1 rounds; only its P4 section (its O4 nit; see the report on the other)
is implemented here.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's item P4 (plan v2, verbatim)

## P4 — nits (target 0.12.11; upstream read at openhop_repeater 9c958e31)
- **O4 Ctrl-C.** `cli/main.py:main` (`:1022-1035`) catches only `ConfigError`: SIGINT in a start prints a raw
  traceback (rc 130; state converges). Change: `except KeyboardInterrupt` → one stderr line naming `lhpc status`, rc
  130 (every `finally` has run). Test (`tests/cli/`): a verb stubbed to raise it → rc 130, `lhpc status` on stderr,
  no "Traceback" (red today).
- **146's `-W error` edge.** `testlab/lhpc_testlab/release.py:473-476`: the retry note's `warnings.warn`/`record`
  run before the `try` whose `finally` calls `_pty_stop`; move them inside. Test (`testlab/tests/unit/
  test_release_attribution.py`): `record` raising on attempt 2 still stops the process (red today).
- **CHANGELOG.** O4: "An `lhpc` command stopped with Ctrl-C prints one line naming `lhpc status` instead of a Python
  traceback (exit status 130, as before)." The testlab edge: none (test tooling, per the brief).
- Out: MeshChat own-pins wording (no observation; maintainer's list); F136 (in v0.12.2, 95782542; live check window 2).

## Commits (full messages, `git log --reverse --format=%B`)
### 92370c72 field-fixes-p4: Ctrl-C ends an lhpc command with one line, not a traceback

```text
field-fixes-p4: Ctrl-C ends an lhpc command with one line, not a traceback

SIGINT during a command (seen during a start in the 0.12.2 matrix, O4)
printed a raw KeyboardInterrupt traceback; the state converged. `main` now
catches KeyboardInterrupt after every `finally` below it has run, prints one
line naming `lhpc status`, and returns 130, the status the interrupt gave
before.

Red before: test_ctrl_c_ends_a_command_with_one_line_and_exit_130 (a start
whose service raises KeyboardInterrupt) aborts the pytest run on the parent:
the interrupt escapes `main`.
```

## The code report (verbatim)

### Code report: field-fixes-p4 (plan item P4, the nits: Ctrl-C)

Branch `cons/field-fixes-p4-r1` on `origin/dev` = `78c9fac7`. Plan: `plans/PLAN-field-fixes-0.12.11.md` v2 (gate 1
GREEN WITH NOTES), item P4.

**Scope against the plan.** P4 named two nits. Only O4 is implemented. The second, 146's `-W error` edge in
`testlab/lhpc_testlab/release.py` (the retry note's `warnings.warn`/`record` before the `try`), no longer exists on
dev. `aa5410dd` ("testlab: an interactive start that times out is diagnosed, never retried", 2026-10-15) removed
the retry, and `_pty_launch` is now followed directly by the `try` whose `finally` calls `_pty_stop`
(release.py:465-466). Nothing is left to move.

## Commits

| commit | subject | files | test (layer; rule-10 assertion) | red-before |
|---|---|---|---|---|
| 92370c72 | field-fixes-p4: Ctrl-C ends an lhpc command with one line, not a traceback | lhpc/adapters/cli/main.py, tests/cli/test_cli.py, CHANGELOG.md | `tests/cli`: `test_ctrl_c_ends_a_command_with_one_line_and_exit_130`: `ControllerService.start` stubbed to raise `KeyboardInterrupt` (a collaborator stub, as the module's other refusal tests do) → `main(["stack", "start", "daemon", "--yes"]) == 130`, the command token `lhpc status` on stderr, no `Traceback` | yes, on 78c9fac7 with the committed test file: the interrupt escapes `main` and aborts the pytest run (`!!! KeyboardInterrupt !!!`, test_cli.py:117) |

**Green** (on 92370c72): `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/cli tests/repo` →
`695 passed, 1 skipped in 10.20s`. `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks
passed!`. Lanes: none per packet (the maintainer's rule until Sat 10:00).

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 18 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        1 new tests; layers tests/cli=cli; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

One `except` clause in the existing boundary of `main` (4 lines) and one docstring sentence. main.py 1850 → 1854.
No new import (`sys` was already used there).

## Deviations

- The 146 edge is not implemented: it no longer exists (above).
- The plan's O4 line said "interrupted — `lhpc status` shows where it stopped"; that is the text used.

## The 6-point block

1. **Contracts.** `lhpc.adapters.cli.main:main(argv) -> int` (main.py:1022):
   - a `KeyboardInterrupt` that reaches it now returns 130 after writing one line to stderr;
   - every other path is unchanged.
   130 is the status the console script gave before: the shell's 128 + SIGINT, which O4 recorded. The handler sits
   after `_run` returns or raises, so every `finally` and context manager below it has already run. The two
   cooperative-abort handlers (auto-install, HMAC apply: `signal.signal(SIGINT, …)`, main.py:1228, :1407) do not
   raise `KeyboardInterrupt` and are unaffected. `lhpc rflog --follow`'s own `except KeyboardInterrupt: return 0`
   (main.py:501) catches first, so it is unchanged.
2. **Invariants.** "CLI exit status and web flash agree" (architecture.md, Truthful outcomes): the web never sees a
   terminal SIGINT; no web path changed. Identity-verified stopping, locks and journals act in the layers below,
   whose cleanup already ran before this handler.
3. **Failure classes.**
   - Ctrl-C through cleanup is exactly this change: the handler runs after the unwinding, so it cannot skip one.
   - A second Ctrl-C during the unwinding still raises from inside that cleanup, before the handler; unchanged
     behaviour, stated.
   - `BaseException`s other than `KeyboardInterrupt` (`SystemExit`, `GeneratorExit`) are not caught.
4. **Test rules.** Red-before as above. Typed status (130) and the command token, no sentence comparison. The stub
   replaces a collaborator method; no private is patched; no sleep, no network.
5. **Directories + ruff.** As in Green; `main`'s signature is unchanged.
6. **Adversarial self-review.** Found:
   - "never a traceback" in the docstring would be false for the second-Ctrl-C case, so the docstring says only
     "Ctrl-C ends with one line and rc 130".
   - The CHANGELOG says "(exit status 130, as before)", true for the console-script path O4 measured.
   Nothing else.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | 78c9fac7 |
| diff | `git diff --stat origin/dev 92370c72 \| tail -1` | 3 files changed, 22 insertions(+), 1 deletion(-) |
| main.py | `wc -l` before/after | 1850 → 1854 |
| the 146 edge gone | `git log --oneline -S'warnings.warn(note' -- testlab/lhpc_testlab/release.py` | aa5410dd (removal), 6d8875d3 (introduction) |
| green | `pytest -n 12 --dist loadfile tests/cli tests/repo` | 695 passed, 1 skipped |

## The diff (`git diff 78c9fac7 92370c72`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 4b54a493..80eb5737 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -4,6 +4,8 @@
 
 Fixed
 
+- An `lhpc` command stopped with Ctrl-C prints one line naming `lhpc status` instead of a Python traceback (exit
+  status 130, as before).
 - The GPS sky view no longer alternates with "no satellites reported" when gpsd reports the satellites only
   every few seconds: the console keeps each receiver's last satellite list for up to 20 s.
 - The slow-target release check times the download of the Graywolf release the manifest pins and no longer asks
diff --git a/lhpc/adapters/cli/main.py b/lhpc/adapters/cli/main.py
index 2d0e232a..5b88affa 100644
--- a/lhpc/adapters/cli/main.py
+++ b/lhpc/adapters/cli/main.py
@@ -1022,7 +1022,7 @@ def build_parser() -> argparse.ArgumentParser:
 def main(argv: list[str] | None = None) -> int:
     """CLI entry. Fail-closed boundary: a malformed/unreadable stored configuration surfaces as a
     clean typed failure (rc 1) with the offending file left untouched — never a traceback, and never
-    a silent fall-through to defaults."""
+    a silent fall-through to defaults. Ctrl-C ends with one line and rc 130."""
     from lhpc.core.config import ConfigError
     from lhpc.core.source_fs import arm_lease_break_handler
     arm_lease_break_handler()
@@ -1033,6 +1033,10 @@ def main(argv: list[str] | None = None) -> int:
             f"error: a stored configuration file is unreadable or malformed — {exc}\n"
             "It was left untouched for inspection; repair or remove it, then retry.\n")
         return 1
+    except KeyboardInterrupt:
+        # Every `finally` below has run by now; the operator needs where to look, not a traceback.
+        sys.stderr.write("interrupted — `lhpc status` shows where it stopped\n")
+        return 130
 
 
 def _cmd_meshtastic(passthrough: list[str]) -> int:
diff --git a/tests/cli/test_cli.py b/tests/cli/test_cli.py
index 7e3e1d42..370bc612 100644
--- a/tests/cli/test_cli.py
+++ b/tests/cli/test_cli.py
@@ -106,6 +106,21 @@ def test_start_plan_is_dry_run_without_yes(tmp_path, monkeypatch, capsys):
     assert "Run plan" in capsys.readouterr().out
 
 
+def test_ctrl_c_ends_a_command_with_one_line_and_exit_130(tmp_path, monkeypatch, capsys):
+    # Ctrl-C while a verb runs: every `finally` below has already run; the operator gets one line
+    # naming where to look, never a Python traceback.
+    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
+    main(["bootstrap", "--yes"])
+    capsys.readouterr()
+
+    def _interrupted(self, *a, **k):                        # noqa: ANN001, ANN002, ANN003
+        raise KeyboardInterrupt
+    monkeypatch.setattr(ControllerService, "start", _interrupted)
+    assert main(["stack", "start", "daemon", "--yes"]) == 130
+    err = capsys.readouterr().err
+    assert "lhpc status" in err and "Traceback" not in err
+
+
 def test_help_topic(capsys):
     assert main(["help", "safety"]) == 0
     assert "never auto-enables TX" in capsys.readouterr().out
```
