# Code report: testrun-p3 (P3-lite: two HTML reads via htmlq; the sentence-assert walk as a finding)

Branch `cons/testrun-p3-r1` on `origin/dev` = `b8a6a5fc`. Plan: PLAN-testrun v3 §3 P3, reduced on the handler's line
(option B, "P3-lite"): the planned conversions of sentence asserts are not made; the walk below is the finding.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| 0aed8b78 | tests/stacks/test_hardware.py, tests/core/test_boot_restore.py | stacks and core layers: `test_dashboard_narrows_in_single_radio_setup` asks htmlq whether `<body>` carries `dash-narrow` (both directions); `test_web_toggle_round_trip` asserts the `<input name="restore">` field with htmlq | none: a test-suite change; negative run instead (below) |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 16 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        0 new tests; layers tests/core=core tests/stacks=stacks; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The two changes (rule 5, structural HTML queries)

| site (on 1803e92c) | before | after |
|---|---|---|
| stacks/test_hardware.py:310 `_body_class` (used at :398, :400) | `html.split("<body", 1)[1].split(">", 1)[0]`, then `"dash-narrow" in …` | `_body_has_class(html, name)`: `htmlq.parse(html).find("body")[0].has_class(name)` |
| core/test_boot_restore.py:993 `test_web_toggle_round_trip` | `"Autostart stacks on boot:" in body and 'name="restore"' in body` | `htmlq.parse(body).find("input", name="restore")`: the form field the test then posts (dashboard.html:483, `<input type="hidden" name="restore" …>`) |

The label sentence is dropped: the field is what the test drives next (`c.post("/boot-restore", …)`).

## Negative run

| change in a temporary copy | result |
|---|---|
| `find("input", name="restore-x")` | `test_web_toggle_round_trip` FAILED (`assert []`) |
| `has_class(name + "-x")` | `test_dashboard_narrows_in_single_radio_setup` FAILED |

## The walk (the finding instead of the planned conversions)

On 1803e92c the scan finds 212 sentence-assert sites in P3's files (the 253 of the plan minus those in files other
packets own). 15 are in tests/cli/: the CLI's only output is text, so there is no typed field at that seam.
The 32 mechanical candidates (a sentence asserted `in`, or `startswith` on, a result's `.summary` / `.details`)
were all read:

| site | the typed field asserted beside it |
|---|---|
| core/test_clock_gate.py:510 | `res.ok`; `pki.provisional_pending(p)`, `pki_status(p)["provisional"] is True` |
| core/test_process_ownership.py:847, :911, :912, :948, :949 | `outcome == blocked` / `Outcome.BLOCKED` per component |
| core/test_restart_optional.py:64, :114 | none: the test's fake `start` returns an `ActionResult` without `results`, so the restart carries no typed outcome for the lost component |
| core/test_run_order.py:1180, :1236 | the recorded `stopped` list |
| core/test_secrets_backup.py:459 | `not r.ok`; `_tree(b) == before` |
| host/test_cert_cap_and_expiry.py:258 | `d.ok is False` |
| install/test_binary_pin_override.py:67, :108, :194, :230, :317 | `data["pin_mismatch"]`, `data["override_command"]`, `data["override_refused"]`, `next_commands`; :317 the outcome `blocked` |
| install/test_selfupdate.py:467, :468, :896, :897, :935 | `r.ok`, `"state" not in r.data`, `next_commands == cmds` / `[]` |
| install/test_selfupdate_service.py:841, :843, :891 | `next_commands == []`; `data.get("cleanup_failed")` |
| install/test_source.py:98, :116 | `not res.ok`; the per-component status; `data["counts"]`, `data["checked"]` |
| web/test_webserver.py:2568, :2569, :2620, :2621, :2676 | `data["revocation_recorded"]`, `data["reload_ok"]`; the `crl-reload-pending` marker; `data["checks"]` |

30 of 32: the typed field that decides the verdict is already asserted beside the sentence. There the sentence
checks that the operator's message is true to that state; converting it would delete that check. 2 of 32
(test_restart_optional.py): no typed field exists at that seam. No sentence assert was deleted.

**Proposed rule clarification for tests/README.md** (rule 2, for the X3 final report; not in this commit):
"A sentence assert beside a typed assert is allowed: it checks the message, not the decision."

## The 6-point block

1. **Contracts:** none changed.
2. **Invariants:** none new.
3. **Failure classes:** a template that renames the field or the class fails the test, which is the intent; a
   reworded label no longer fails the toggle test.
4. **Test rules:** rule 5 (both sites); rule 2 (the walk; no change).
5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/stacks
   tests/repo` on b8a6a5fc → `4090 passed, 1 skipped in 74.34s`; both ruff runs → `All checks passed!`.
6. **Adversarial self-review:** the walk read the 32 mechanical candidates only; the other 165 non-CLI sites were not
   read one by one, so no claim about them beyond the scan's count. The branch was rebased from 1803e92c onto b8a6a5fc (the
   docs-run landing) before the directories ran.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | b8a6a5fc |
| diff | `git diff --stat origin/dev 0aed8b78` | 2 files changed, 7 insertions(+), 5 deletions(-) |
| sites in P3's files | the P3 site listing (the scan's two sentence classes over P3's file set) on 1803e92c | 212 (cli 15) |
| `.split(` on a page, all of tests/ | `python3 -I testrun-scan-v3.py` | 2 on b8a6a5fc: `install/test_deps.py:339` (a shell script) and `core/test_identity.py:215` (fixed in P2, not yet landed) |
