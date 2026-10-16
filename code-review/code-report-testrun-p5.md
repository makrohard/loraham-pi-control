# Code report: testrun-p5 (the service_params.py budget follows the file down)

Branch `cons/testrun-p5-r1` on `origin/dev` = `771d1a37`. Plan: PLAN-testrun v3 §3 P5 (gate 1 GREEN WITH NOTES):
"after the release landings, each budget lowered to its measured value where the file shrank". Dev is held at
771d1a37 for its CI; a later landing that touches the family is measured again at the rebase. Tests only.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| bf8ab1aa | tests/repo/test_module_budgets.py | repo layer: `BUDGETS["service_params.py"]` 3380 → 3379 | none: lowering a budget is free (rule 13); negative check instead |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 27 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        0 new tests; layers tests/repo=repo; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The family on 771d1a37 (after this commit; `testrun-scan-v3.py`'s table: lines/budget, public/budget)

```text
service_auto_install.py       1875/1875   16/16  slack 0
service_base.py                166/166     0/0   slack 0
service_binary_channel.py      297/297    19/19  slack 0
service_binary_ops.py         1077/1077    5/5   slack 0
service_boot_restore.py        678/678     2/2   slack 0
service_firewall.py           1533/1533   19/19  slack 0
service_hmac.py               1016/1016   17/17  slack 0
service_lifecycle_ops.py      6315/6315   73/73  slack 0
service_maintenance.py        2891/2891   30/30  slack 0
service_network.py            1183/1183    9/9   slack 0
service_params.py             3379/3379   54/54  slack 0
service_secrets.py             399/399     2/2   slack 0
service_selfupdate.py         1932/1932   22/22  slack 0
service_system.py              759/759     2/2   slack 0
service_webserver.py          2388/2388   42/42  slack 0
services.py                   2959/2959   28/28  slack 0
```

Before this commit `service_params.py` read `3379/3380` (slack 1); every other file and every public-method count
was at its budget (slack 0). The line went in c699bd16 (field-fixes-p2, the repeater login secret: `git show --stat c699bd16 --
lhpc/core/service_params.py` → `2 insertions(+), 3 deletions(-)`, net −1).

## Negative check

A copy of the test module with the budget at 3378 (one under the file): `test_service_family_within_budget[service_params.py]`
FAILED, the other 15 family cases passed. The copy was deleted (`git status --short` after: the one edited file).

## The 6-point block

1. **Contracts:** none. 2. **Invariants:** the ratchet itself (rule 13), unchanged.
3. **Failure classes:** a later landing that adds a line to `service_params.py` now fails the budget test, as rule
   13 intends (a raise needs `# <item id>: <reason>` on its line).
4. **Test rules:** rule 13.
5. **Directories:** `python -m pytest -q -p no:cacheprovider -n 8 tests/repo` → `591 passed, 1 skipped`; both ruff
   runs → `All checks passed!`.
6. **Adversarial self-review:** the family was measured on 771d1a37 only; landings after it are measured again when
   this branch is rebased (the table is re-run then).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | 771d1a37 |
| service_params.py lines | `wc -l < lhpc/core/service_params.py` | 3379 |
| slack before / after | the scan's family table | 1 / 0 (all 16 files) |
