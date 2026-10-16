# Code report: docsrun (P1 docs + P2 CHANGELOG)

Branch `cons/docsrun-r1` on `origin/dev` = `d77e34f4`. Plan: PLAN-docsrun v2 (gate 1 GREEN WITH NOTES; its note,
"for each code landing record the doc sentences it can affect, or none with evidence", is applied below).
Docs and CHANGELOG only: no code, no test.

## Commits

| commit | files | tests | red-before |
|---|---|---|---|
| 3de37b99 | docs/architecture.md, docs/maintenance.md, docs/testlab.md | none (docs only); the architecture-table `file:line` scan: 94 ok / 3 stale → 97 ok / 0 stale | n/a (no behaviour) |
| 6a80255b | CHANGELOG.md (`## Unreleased` only) | none | n/a |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 12 lines   OK
docs         3 files, +8 lines; fact "service_maintenance.py:1781" -> docs/architecture.md; fact "pytest-xdist" -> docs/maintenance.md; fact "PYTHONFAULTHANDLER=1" -> docs/testlab.md   OK
tests        0 new tests; layers -; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## What changed

| id | file:line (after) | change | evidence |
|---|---|---|---|
| D1 | docs/architecture.md:338, :361, :362 | `service_maintenance.py:2389` `uninstall` → 2397; `:1773` `update` → 1781; `service_params.py:2813` `enforce_identity` → 2812 | `grep -nE '    def (uninstall|update)\(' lhpc/core/service_maintenance.py` → 1781, 2397; `grep -n 'def enforce_identity' lhpc/core/service_params.py` → 2812 |
| D2 | docs/maintenance.md:96-97 | `pytest-xdist` added to the unpinned dev tools | `grep -n pytest-xdist pyproject.toml` → 44 (dev list) |
| D3 | docs/architecture.md:233 | "(before 0.12.0 it was ignored)" removed: history | — |
| D5 | docs/testlab.md:148 | ", because its first start has hung on a loaded runner for a cause not yet measured" removed: a reason with history, not a checkable fact | found by the manual review below |
| D4 | CHANGELOG.md Unreleased | the four tooling bullets removed (slow-target Graywolf download; CI parallel processes; the release check's readiness timeout; its interactive start); the four operator bullets stay | the handler's line on D4 |

D5 is new against the plan's D1–D4: the plan's P1 covers "any file the review below corrects".

## Per code landing: the doc sentences it can affect (the gate-1 note)

`git log --reverse eb9673a4..d77e34f4`, commits touching more than `code-review/`:

| commit | what it changed | doc sentences it can affect | state |
|---|---|---|---|
| b0c4750a | slow-build times the manifest's Graywolf | docs/test-matrix.md:110 `deb-fetch` row | updated in the commit; reviewed S9 |
| b379911f | Graywolf check keeps curl's rc + message (+8 lines in service_maintenance.py) | the architecture rows citing service_maintenance.py lines after 886; any doc quoting the old message | D1 (two rows); `grep -rn 'releases API\|network/API' docs` → 0 |
| c8f9c631 | the budget test, tests/README rule 13 | tests/README.md rule 13 | reviewed S12 |
| cf24d05a, a8e0e5ce | the checker | docs/maintenance.md "Docs and CHANGELOG" | reviewed S4; a8e0e5ce changed the checker's word list only: docs/architecture.md:225 uses the POSIX term "session leader", unchanged and still true (`grep -rn 'session leader' docs` → that one line) |
| add7076c | xdist, the cancel rule, slow-build after testlab | docs/maintenance.md:26, :48-50, the dev-tool list | S2, S3 reviewed; D2 |
| 46ffa57f | the sky-view memo | docs/gps.md:52-57 | reviewed S1 |
| 2ea5b5b0, aa5410dd | release-check readiness, interactive timeout | docs/testlab.md:139-149 | S10, S11 reviewed; D5 |
| 642224c4 | repeater presets installed | docs/stacks/meshcore.md:51, :82-84 | S6, S8 reviewed |
| c699bd16 | repeater JWT secret kept (−1 line in service_params.py before `enforce_identity`) | docs/stacks/meshcore.md:15, :68-70; the architecture row citing `enforce_identity` | S5, S7 reviewed; D1 |
| 819f6159, 06a6fac2 | tests only (tests/install, tests/stacks) | none: no doc names the changed cases (`grep -rn 'fresh interpreter\|os.fork' docs` → 0) | — |

## Manual review: every sentence holding a line this run added to the docs

`git diff -U0 eb9673a4 d77e34f4 -- docs README.md tests/README.md` (44 added lines, 6 files). Each sentence was
checked against the code or a command; for truth, history, explanation, and a second place for its fact.

| id | where (on d77e34f4) | sentence (short) | checked against | verdict |
|---|---|---|---|---|
| S1 | docs/gps.md:52-57 | the console keeps each device's last satellite list ≤ 20 s; one `lhpc gps --monitor` run shows only its own read | `lhpc/core/gps.py:556` `NMEA_STALE_S = 20.0`, `:1024` `_SKY_MEMO` (process memory), `:1171` the age check | OK |
| S2 | docs/maintenance.md:26-27 | CI runs `python -m pytest -q -rs -n auto --dist loadfile --cov=lhpc --cov-branch` | `.github/workflows/ci.yml:91` | OK |
| S3 | docs/maintenance.md:48-50 | slow-build only after `testlab`; a newer `cons/*` push cancels that branch's superseded runs of both workflows; nothing else is cancelled | `testlab.yml:221` `needs: testlab`; `ci.yml:19`, `testlab.yml:30` `startsWith(github.ref, 'refs/heads/cons/')`; `pages.yml:27` `cancel-in-progress: false` | OK |
| S4 | docs/maintenance.md:104-112 | the checker prints the block (CHANGELOG, docs, tests, patterns, markers, attribution, module budgets); HARD fails it; it checks form, not truth | `tools/check_houserules.py` rows `changelog`…`modules`, `--correction`, `--fact` (its `--help`) | OK |
| S5 | docs/stacks/meshcore.md:15 | repeater secrets: `openhop_repeater_identity.key`, `_admin.txt`, `_jwt.txt` | `lhpc/core/meshcore_identity.py:35-40` | OK |
| S6 | docs/stacks/meshcore.md:51 | `presets_file` among the controller-set config keys | `meshcore_host/config.py:103,250`; manifest `presets_file` param (hidden) | OK |
| S7 | docs/stacks/meshcore.md:68-70 | key, dashboard password and login-signing secret minted once and reused; a restart signs no one out | `meshcore_identity.py:33-39` ("minted once"), `:253` `_MINTED_LENGTH` | OK |
| S8 | docs/stacks/meshcore.md:82-84 | presets from the pinned checkout, copied into `state/openhop/` at each start; a picked preset cannot be saved there | `meshcore_host/repeater.py` `install_presets` (called at start; skips an identical file); manifest default `{runtime}/src/openhop-repeater/radio-presets.json`; `proxy_deny_paths` holds `/api/update_radio_config` | OK |
| S9 | docs/test-matrix.md:110 | `graywolf-fetch.sh /tmp/gw <the manifest's graywolf version> --from-upstream` | the script's usage line `<dest-dir> <version> [--from-upstream]`; its caller `testlab/tests/slowbuild/test_slow_build.py:216` `version = stt._fetch_version(comp)` (the manifest component's fetch step) passed as argument 2 at `:220-221` (`graywolf-fetch.sh <tmp>/deb <version> --from-upstream`) | OK |
| S10 | docs/testlab.md:139-142 | a readiness timeout is marked only when LHPC reports the component stopped or failed, else unmarked with the run-log tail | `testlab/lhpc_testlab/release.py:704` `readiness_failed` | OK |
| S11 | docs/testlab.md:145-149 | a timeout is never retried; SIGABRT + faulthandler; NomadNet records sockets and every Reticulum stack; the lane runs with `PYTHONFAULTHANDLER=1` | `release.py:464`, `:500` `reticulum_peers_snapshot`, `:541`; `testlab/tests/release/conftest.py:93` | OK after D5 |
| S12 | tests/README.md:110-115 | rule 13: caps lines and public methods of the family; never lowers a budget by itself | `tests/repo/test_module_budgets.py` (fixed `BUDGETS`, `<=` comparison) | OK |

Not reviewed: doc sentences this run did not touch (plan §5).

## The 6-point block

1. **Contracts:** none changed (docs and CHANGELOG only).
2. **Invariants:** the architecture table's owners resolve at their cited lines (scan (a): 97 ok, 0 stale).
3. **Failure classes:** n/a; a line number moves with any later edit above it, so P3 re-runs scan (a) before
   the 0.12.10 release commit.
4. **Test rules:** no test changed.
5. **Directories:** `pytest -q -p no:cacheprovider -n 8 tests/repo` → `578 passed, 1 skipped`; `ruff check lhpc
   testlab` and `ruff check tests --select F,E9` → `All checks passed!`.
6. **Adversarial self-review:**
   - (a) The first subject read "the docs … are true again", a claim the round-2 verdict ruled out; it was reworded
     to what the commit does before the push.
   - (b) D3's reflow kept every word of the remaining sentence (`git diff --word-diff` shows only the
     parenthesis removed).
   - (c) D5 removes text another item landed tonight; it is a reason clause, and its fact (the snapshot runs) stays.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | d77e34f4 |
| architecture refs before/after | `python3 -I docsrun-scan.py` (run dir) on d77e34f4 / on 6a80255b | 94 ok, 3 stale / 97 ok, 0 stale |
| history candidates before/after | same, part (b) | 9 / 8 |
| added doc lines reviewed | `git diff -U0 eb9673a4 d77e34f4 -- docs README.md tests/README.md \| grep -c '^+[^+]'` | 44 |
| Unreleased bullets before/after | `awk '/^## Unreleased/{f=1;next}/^## /{f=0}f' CHANGELOG.md \| grep -c '^- '` | 8 / 4 |
| repo tests | the pytest command above | 578 passed, 1 skipped |
