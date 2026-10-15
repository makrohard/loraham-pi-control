# Code report: field-fixes-p4 (plan item P4, the nits: Ctrl-C)

Branch `cons/field-fixes-p4-r1` on `origin/dev` = `d77e34f4` (rebased from 78c9fac7). Plan: `plans/PLAN-field-fixes-0.12.11.md` v2 (gate 1
GREEN WITH NOTES), item P4.

**Scope against the plan.** P4 named two nits. Only O4 is implemented. The second, 146's `-W error` edge in
`testlab/lhpc_testlab/release.py` (the retry note's `warnings.warn`/`record` before the `try`), no longer exists on
dev. `aa5410dd` ("testlab: an interactive start that times out is diagnosed, never retried", 2026-10-15) removed
the retry, and `_pty_launch` is now followed directly by the `try` whose `finally` calls `_pty_stop`
(release.py:465-466). Nothing is left to move.

## Commits

| commit | subject | files | test (layer; rule-10 assertion) | red-before |
|---|---|---|---|---|
| eb244049 | field-fixes-p4: Ctrl-C ends an lhpc command with one line, not a traceback | lhpc/adapters/cli/main.py, tests/cli/test_cli.py, CHANGELOG.md | `tests/cli`: `test_ctrl_c_ends_a_command_with_one_line_and_exit_130`: `ControllerService.start` stubbed to raise `KeyboardInterrupt` (a collaborator stub, as the module's other refusal tests do) → `main(["stack", "start", "daemon", "--yes"]) == 130`, the command token `lhpc status` on stderr, no `Traceback` | yes, on 78c9fac7 with the committed test file: the interrupt escapes `main` and aborts the pytest run (`!!! KeyboardInterrupt !!!`, test_cli.py:117) |

**Green** (on eb244049): `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/cli tests/repo` →
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
| diff | `git diff --stat origin/dev eb244049 \| tail -1` | 3 files changed, 22 insertions(+), 1 deletion(-) |
| main.py | `wc -l` before/after | 1850 → 1854 |
| the 146 edge gone | `git log --oneline -S'warnings.warn(note' -- testlab/lhpc_testlab/release.py` | aa5410dd (removal), 6d8875d3 (introduction) |
| green | `pytest -n 12 --dist loadfile tests/cli tests/repo` | 695 passed, 1 skipped |
