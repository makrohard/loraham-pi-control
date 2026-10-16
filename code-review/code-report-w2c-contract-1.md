# Code report: w2c-contract-1 (the CONTRACT release: three units ship their new bytes)

Branch `cons/w2c-contract-1-r1` on `origin/release/0.13.0-w2c` = `d77e34f4`. Plan: `PLAN-W2c-CONTRACT-v3.md`
(gate 1 GREEN, round 11), §2 and §4–§5: one code commit, one docs commit. Release class 0.13.0 (the maintainer).

**v2** (round 13 GREEN WITH NOTES + the second reader): the gate also refuses an `incomplete` set that contains a
`compatible` unit (a 0.12.1 unit beside a missing or changed one); `update_refusal` now takes the `integration()`
result (round 13's F1 on the `status: str` annotation falls away with it); the gate's return is wrapped, paid by one
comment line (service_selfupdate.py stays 1932); the injector sentence is corrected; the commit list row uses
`--reverse` (F2). New cases: `update_refusal` ×6 (three refused) and the install test's `incomplete` step, red
against v1's status-only gate (`{'already': True} == {'integration': 'compatible'}`: the apply proceeded).

## Commits

| commit | files | tests (layer; rule-10 assertion) | red-before |
|---|---|---|---|
| e6e76863 | `lhpc/core/updater_units.py` (templates, table, `update_refusal`), `service_selfupdate.py` (gate call + texts), `services.py`, `service_boot_restore.py`, `service_firewall.py`, `web/app.py`, `_update.html` (texts), `deploy/` (3 renders), `tests/data/` (current, released +3, next → compatible), tests | repo: guard clause (e) `test_a_compatible_unit_never_released_or_the_pre_contract_units_dropped_fails` (named errors). install: `test_operator_apply_refuses_while_the_units_are_another_releases` (`ok is False`, `data == {"integration": "compatible"}`, `next_commands`, the apply stub not called, no systemctl/git; control: canonical web → proceeds). host: `test_a_repair_interrupted_after_any_unit_leaves_a_set_that_restores_and_completes` ×3 (verdict map per unit, the injector's call list), `test_an_update_refuses_to_start_from_another_releases_units` ×6. core: `test_every_mix_of_the_previous_and_current_units_restores` ×8 (proof, note, set status). 10 existing tests renamed next → other/compatible | the gate: with its lines removed the test fails (`assert (True is False)`, the apply proceeds), and its `incomplete` step fails against v1's status-only gate; clause (e): with it removed the dropped/unreleased case fails; the guard on the template edit alone (data reverted to base, a scratch worktree): 3 failed ("changed: [boot-restore, nginx, web]", "current digest not in the released history"). The mixes and the interrupted repair are new invariants: the injector proves its branch ran (`calls == changed[:k]`) |
| d5e75ab3 | `docs/maintenance.md`, `docs/backlog.md`, `docs/architecture.md`, `docs/deployment.md`, `docs/operations.md`, `CHANGELOG.md` | — | — |

**Byte-exactness, proven before the tests:** with root `/R`, every new current render equals 0.12.1's
`render_next`, every `render_compatible` equals 0.12.1's current render, and the four other units render unchanged
(a script over `updater_units`, before and after). In data terms: the three new lines of `unit-templates.sha256` are
identical to `git show d77e34f4:tests/data/unit-templates-next.sha256` (`diff` → no output), and
`unit-templates-compatible.sha256` holds the three 0.12.0/0.12.1 seed digests.

**Green** (on d5e75ab3): `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/repo tests/host
tests/core tests/install tests/golden tests/web tests/cli tests/stacks` → `7691 passed, 1 skipped in 130.35s`.
`ruff check lhpc testlab`, `ruff check tests --select F,E9` → `All checks passed!`. Lanes: none per packet (the
maintainer's rule until Sat 10:00).

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  origin/release/0.13.0-w2c..HEAD
changelog    +2 bullets (limit 2, each ≤ 3 lines; longest 3), top section 27 lines   OK
docs         5 files, +32 lines; fact "unit-templates-compatible.sha256" -> docs/maintenance.md   OK
tests        15 new tests; layers tests/core=core tests/golden=golden tests/host=host tests/install=install tests/repo=repo; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 2 network 0   WARN
markers      3 without slow   WARN
attribution  authors makrohard; trailers 0; words 0   OK
modules      service_boot_restore.py 678->678 lines (budget 678), 2->2 public (budget 2); service_firewall.py 1533->1533 lines (budget 1533), 19->19 public (budget 19); service_selfupdate.py 1932->1932 lines (budget 1932), 22->22 public (budget 22); services.py 2959->2959 lines (budget 2959), 28->28 public (budget 28); family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 2)
```

WARNs, answered:
- `startswith 2`: one is a pre-existing assert on `_refresh_units_post_update`'s detail sentence. Its words changed
  ("a newer release" → "another release"), and the function returns `(ok, detail)` with no typed field, so the
  literal is updated. The other is the guard test matching its own helper's error prefix (`"compatible units
  differ"`), a test-internal name, not a product sentence.
- `markers 3 without slow`: the checker's text heuristic (`venv` in a test body: `deployment_paths` returns a venv
  path). These tests write unit files and run in milliseconds; none builds a venv.

## Simplicity guardrails

- No new mechanism: the shipped EXPAND table, inverted and renamed; one plain function (`update_refusal`, 9 lines)
  and a two-line call. Lines (`wc -l`): updater_units.py 788 → 798; service_selfupdate.py 1932 → 1932 (budget
  1932: the call is paid by shortened comments); services.py 2959 → 2959; service_boot_restore.py and
  service_firewall.py text only (the checker's modules line: family net +0).
- Every consumer branch is unchanged (`git diff` of the five service/adapter files shows only comment/string lines
  and the gate call).

## Deviations

- Plan §4 (i) said "the note whenever a previous unit is present". The boot-restore gate is per web unit (since
  EXPAND, `service_boot_restore.py:64-70`), so the note follows the web unit; the set-level `compatible` status
  (status, doctor) follows any previous unit. The test asserts exactly that.
- Plan §2.2 listed the texts; `service_boot_restore.py:48` and `service_firewall.py:884` (comments) carried "the next
  release's" too and are reworded. The four golden/core/host test names with "next_releases" become
  "other_releases".
- The fault injector patches `os.replace` on the global `os` module (reached as `updater_units.os`); the test
  puts the original back before the rerun, and monkeypatch restores it at the end.

## The 6-point block

1. **Contracts.**
   - `render_compatible(kind, root, checkout, venv) -> str | None` (was `render_next`). It is the inverse of 0.12.1's
     edits.
   - `verify`/`verify_file` → `compatible` for a byte-exact 0.12.1 unit of this deployment, checked after `ok` (order
     unchanged).
   - `update_refusal(integ) -> str | None`: a text when the set reads `compatible` or any unit does (also inside an
     `incomplete` set); None otherwise.
   - `self_update_apply_operator()` returns `ActionResult(False, <text>, data={"integration": "compatible"},
     next_commands=["lhpc self-update --repair-integration"])` before any service call, fetch or sync. It reads the
     status with `.get`, so an integration dict without a status proceeds as before.
   - CLI `python -m lhpc.core.updater_units render-compatible …` (was `render-next`; no doc named it).
2. **Invariants + tests.**
   - Boot restore on every previous/current mix: core ×8.
   - Atomic per-unit repair converges after a failure at any unit: host ×3.
   - The frozen bytes with their history, both ways: repo guard.
   - The 0.12.0/0.12.1 units stay compatible: clause (e).
   - Updates start only from `ok` units: the install test plus one-click's existing `status != "ok"` refusal
     (`service_selfupdate.py:985-991`, unchanged).
3. **Failure classes.**
   - Real signatures: the real `write_set`, `verify`, `_web_integration_proven` and `self_update_apply_operator` on
     files and fakes.
   - EIO in the repair: the injected `OSError` on `os.replace`. The unit's temp file is removed by `write_set`'s own
     `except BaseException`.
   - Ctrl-C during the repair: the same per-unit atomicity.
   - CLI/web: one-click already refuses `compatible`; the web panel shows the shell remedy; the operator CLI now
     refuses.
   - Stacked: the release branch is off dev d77e34f4; dev's later testrun packets also touch
     `tests/host/test_updater_units.py` (different hunks).
4. **Test rules.**
   - Typed fields and verdicts; the two sentence asserts are named above.
   - No sleep, no network.
   - The injector fails exactly the k-th replace and the test proves which units it reached.
5. **Directories + ruff:** as in Green. Signature changes grepped: `render_next`, `_NEXT_TEMPLATES`, `_NEXT_EDITS`,
   `_next_template`, `_is_next`, `render-next` → no hit in `lhpc/`, `tests/`, `testlab/`, `docs/` except the
   history sentence `docs/maintenance.md:160`, which describes 0.12.1.
6. **Adversarial self-review.** Found and fixed:
   - The CHANGELOG first draft said "a later unit change accepts only these units", which rule (e) makes false (the
     0.12.0/0.12.1 units stay accepted). It now says "an update starts only from this release's units".
   - A comment in `app.py` lost a word ("(a release's)") in the rewording; fixed.
   - The gate first read `integration()["status"]` and raised `KeyError` on a test fake; it now uses `.get`, so a
     status-less dict proceeds as the code did before.
   Weighed and kept: the gate also blocks a later patch with no unit change until the repair ran (plan §2.5's cost).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/release/0.13.0-w2c` | d77e34f4 |
| commits | `git log --reverse --format=%H -2` | e6e768631573904eeb2f1afcad9c13548207c9c9 (code), d5e75ab3ef9f80f5253f0003448bfc87c407bd14 (docs) |
| diff | `git diff --stat d77e34f4 d5e75ab3 \| tail -1` | 30 files changed, 317 insertions(+), 192 deletions(-) |
| code + data / tests / docs | `git diff --stat` per path set | 14 files +86 −73 / 10 files +192 −84 / 6 files +39 −35 |
| current == 0.12.1 next | `diff <(git show d77e34f4:tests/data/unit-templates-next.sha256) <(grep -E 'web\|nginx\|boot-restore' unit-templates.sha256)` | no output (3 identical lines) |
| green | the command above | 7691 passed, 1 skipped |
