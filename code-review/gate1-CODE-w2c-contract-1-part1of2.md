# Gate 1: w2c-contract-1 v2 (the CONTRACT release), part 1 of 2: plan, commits, report, product + data + docs diff

**Request.** v2 for a short delta round: judge what changed since v1 (round 13 GREEN WITH NOTES; the report's top lists it: the gate now also refuses an `incomplete` set containing a `compatible` unit, `update_refusal` takes the integration result, the call is wrapped, two report sentences). The full v2 diff is given; the rest is unchanged from v1 (same plan §2–§4, same bytes).

This packet is TWO files (part 1 and part 2, each under 90 KB); together they are your whole input: you have no repository access; use no connector, tool or web lookup. Answer once, after both parts.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied; the diffs are `git diff` output verbatim, split by path.

## The plan (v3), §2–§4 (verbatim)

## 2. The change (the simplest form: the same mechanism, pointing back instead of forward)
1. **The templates take the edits.** `_WEB`, `_NGINX`, `_BOOT_RESTORE` (`:85`, `:218`, `:335`) get the three edits
   applied literally, and `deploy/` gets the three new renders (`tests/host/test_updater_units.py` proves the copies
   are the renders).
2. **The edit table points back.**
   - `_NEXT_EDITS` becomes `_COMPATIBLE_EDITS` with each pair inverted (new, old), so it renders 0.12.1's units from
     the new templates.
   - `render_next`/`_is_next`/`_NEXT_TEMPLATES` become `render_compatible`/`_is_compatible`/`_COMPATIBLE_TEMPLATES`,
     and the CLI subcommand `render-next` becomes `render-compatible` (used only by the box runbooks and
     `tests/host/test_updater_units.py:519-521`; `git grep render-next` lists no doc).
   - The neutral names hold for the next migration too: an EXPAND release puts forward edits in the same table.
   - The verdict `compatible` and every consumer branch stay exactly as shipped. Only the wording changes, from "a newer
     release's" to "another release's" (an older or a newer one):
     - `COMPATIBLE_NOTE` (`:488`) and the comment `:678`;
     - the comments and texts at `service_selfupdate.py:486, 503, 518, 522, 1872-1874`;
     - `app.py:1352`, `_update.html:103`, `services.py:1350`.
   - Each text sits behind a typed field in its tests (verdict, status, `fixable`, a button's absence), so no test
     compares the old sentence; any that does is changed to the field (rule 2) and listed.
3. **The data.**
   - `unit-templates.sha256`: the three lines become 0.12.1's `unit-templates-next.sha256` digests.
   - `unit-templates-released.sha256`: three appended lines `<digest>  <unit>  <release version>` (the version the
     handler names at landing).
   - `unit-templates-next.sha256` becomes `unit-templates-compatible.sha256`, holding the three 0.12.0/0.12.1 digests
     (`67ee9e95…`, `101d941f…`, `ecb2dde6…`, the seed lines).
4. **The guard** (same tests, renamed data):
   - (a) every compatible digest is in the released history;
   - (b) none equals its current digest;
   - (c) the seed only grows;
   - (d) every current digest is in the history;
   - the compatible renders equal their pinned digests both ways.
   - NEW (e): the three 0.12.0/0.12.1 digests (`67ee9e95…`, `101d941f…`, `ecb2dde6…`, a constant in the test, like
     `SEED`) are always in `unit-templates-compatible.sha256`. A later migration therefore has to keep those renders
     beside its own predecessor's, so an extra edit set per unit is that release's work. Dropping them means editing
     the test, a reviewed decision (see the rule in §5). Test: the file without one of them fails (e), naming the unit.
   With the data of 3, (d) makes the three new history lines mandatory, and (a) holds because the compatible digests
   ARE seed lines. "Current renders == 0.12.1's next file" is proven once in the packet by a command
   (`git show 78c9fac7:tests/data/unit-templates-next.sha256` against the new `unit-templates.sha256` lines: three
   identical lines). It is a release-time fact, kept in the history as the version column.
5. **Instead of (b): an update gate in this release's code (round-8 F1).** (b) acts only when the running code is
   the new release, so it cannot help the 0.12.1 → CONTRACT hop. The real risk is an operator who never repairs: the
   next unit-changing release would read 0.12.1's bytes as `modified_ours`. A later release cannot stop the update
   that installs it (the old code runs that update), so the gate must sit in THIS release's update path:
   - one-click already refuses unless the units read `ok` (`self_update_trigger`, `service_selfupdate.py:985-991`),
     so a `compatible` set never updates that way;
   - NEW: the operator `lhpc self-update --apply` (`self_update_apply_operator`, `:601`) refuses while
     `updater_integration()["status"] == "compatible"`, before any fetch, stop or sync. It names
     `lhpc self-update --repair-integration` (typed `data={"integration": "compatible"}`, `next_commands`). The
     decision is one plain function in `updater_units` (`update_refusal(status) -> str | None`, rule 13); the call
     site is two lines. `service_selfupdate.py` is at its budget (1932 of 1932), and the call is paid for by the
     comments the rewording shortens (net ≤ 0, the ratchet line shows it).
   - Cost: a box on 0.12.1 bytes also cannot take a later patch with no unit change until it repairs. That is one
     command, named in the refusal, and it keeps the rule simple: no update from a `compatible` set.
   - The gate stays in every later release (maintenance.md *Changing a unit* makes it binding): an update never
     starts from units that are not this release's own.
   - The gate cannot cover one path: 0.12.1 → CONTRACT (unrepaired) → back to 0.12.1 (its bytes read `ok` there) →
     straight to a later release with 0.12.1's own updater, which has no gate. That path is covered by clause (e):
     the later release still reads 0.12.1's units as `compatible`, so boot restore runs and its gate asks for the repair.
   - Measured result of the 0.12.1 → CONTRACT hop (unchanged by the gate, which is CONTRACT's own code):
     - 0.12.1's refresh reads its own bytes `ok` and leaves them;
     - the new checkout's `verify-set` reads `compatible` (rc 1);
     - the update reports "units still not canonical … compatible";
     - boot restore runs; status, doctor and the console name the repair, which writes the new bytes.

## 3. What each hop does (no new mechanism; measured against the code paths above)
| hop | boot restore | after `--repair-integration` |
|---|---|---|
| 0.12.1 → CONTRACT (any route) | runs: 0.12.1's bytes are `compatible` (note in the log) | current, `ok` |
| CONTRACT → 0.12.1 | runs: 0.12.1 reads CONTRACT's bytes as its `compatible` (its shipped `_NEXT_EDITS`) | 0.12.1's own bytes |
| CONTRACT → 0.12.0 or older (also skipping 0.12.1) | refuses: `modified_ours` | that release's own repair restores — documented, not mechanised |
| an interrupted repair on CONTRACT | runs: each unit is current or previous (atomic per unit, `write_set` `:709-716`) | completes on rerun |
| 0.12.1 → CONTRACT → back to 0.12.1 → a later release (0.12.1's ungated updater) | runs: (e) keeps 0.12.1's units `compatible` in every later release | that release's gate asks for the repair before its next update |
| CONTRACT → any later release | — | the update starts only from `ok` units: `--apply` refuses on `compatible` (the gate), one-click already does; after the repair it proceeds |

## 4. Tests (red-before per behaviour; layers per tests/README.md)
- `tests/repo/test_unit_templates_frozen.py` (repo): the existing six tests on the renamed data. Red-before for the
  change itself: run the guard after the templates change and before the data change → "drifted" and "current
  digest not in the released history" (shows (d) bites on this very change).
- `tests/host/test_updater_units.py` (host): `verify` → `compatible` for each 0.12.1 render with the default root, a
  custom `--target` root and a root text that occurs elsewhere (the EXPAND cases, now pointing back); `ok` for the new
  render; one byte changed → `modified_ours`; a drop-in → `overridden`. `render-compatible` CLI. Red-before: on dev
  the 0.12.1 render reads `ok`, and the new render reads `compatible` (the roles swap).
- **Forward/mixed proof** (`tests/core/test_boot_restore.py`, core):
  - (i) the 2³ mixes of previous/current over the three units under the new code → boot restore runs; the note
    whenever a previous unit is present; none when all current;
  - (ii) the forward write: `write_set` over an all-previous set with a fault injected on the k-th `os.replace` among
    the three (k = 1, 2, 3; the injector fails exactly that call and the test asserts the earlier units ARE current) →
    boot restore runs on the partial set, and a second `write_set` ends all `ok`.
  - Red-before: (i) on dev the previous bytes read `ok` and the new ones `compatible`, so the note sits on the wrong
    mixes; (ii) is a new invariant (the injector proves the branch ran), shown red by inverting the expected note.
- Golden (D1, `tests/golden/conftest.py`): the all-compatible set is now the all-previous set.
- Uninstall (`tests/host/test_deploy_scripts.py`): a previous-bytes set and `*.uninstall-staged` are handled;
  `modified_ours` still aborts. Same cases, data reversed.
- Downgrade side: 0.12.1's shipped tests prove that it reads these exact bytes `compatible`; the packet cites them
  (`git show v0.12.1:tests/data/unit-templates-next.sha256`) instead of re-testing old code.
- **The gate** (`tests/install/test_selfupdate.py`, install): the operator apply with a `compatible` set → `ok is
  False`, `data["integration"] == "compatible"`, `next_commands` holds `lhpc self-update --repair-integration`, and
  no git/systemctl command ran (the fake runner's log is empty of them). Control: an `ok` set proceeds. `update_refusal`
  per status (host). Red-before: on dev the apply proceeds.


## Commits (full messages, `git log --reverse --format=%B`)
### e6e76863 w2c-contract: the web, nginx and boot-restore units ship their new bytes

```text
w2c-contract: the web, nginx and boot-restore units ship their new bytes

The second half of the staged unit migration (0.12.1 accepted these bytes
as `compatible`). The three templates take the planned edits: the web and
boot-restore units lose the dead `-%h/.meshcore_nm` ReadWritePaths entry
(and web its two comment lines), the nginx unit's boot-gate comment says
what the step does. Their renders are byte-identical to 0.12.1's
unit-templates-next.sha256; the other four units are unchanged.

The edit table is inverted and renamed (_NEXT_EDITS -> _COMPATIBLE_EDITS,
render_next -> render_compatible, CLI render-next -> render-compatible), so
0.12.1's units now read `compatible`: boot restore runs on them, status and
doctor name the repair, and the shell repair rewrites them. Every consumer
branch is unchanged; their texts say "another release's" instead of "a newer
release's".

Data: unit-templates.sha256 takes the three new digests;
unit-templates-released.sha256 appends them (0.13.0);
unit-templates-next.sha256 becomes unit-templates-compatible.sha256 with the
0.12.0/0.12.1 digests; deploy/ carries the three new renders.

Two binding rules for every later release:
- `lhpc self-update --apply` refuses while any unit reads `compatible`, also
  beside a missing or changed one (`incomplete`) (updater_units.update_refusal
  on the integration result; one-click already needs `ok` units);
- the 0.12.0/0.12.1 units stay compatible: guard clause (e) in
  test_unit_templates_frozen.py fails when one leaves the compatible file.

Red before:
- the gate: test_operator_apply_refuses_while_the_units_are_another_releases
  fails with the gate lines removed (the apply proceeds: ok is True), and its
  `incomplete` case fails against a status-only gate (data {'already': True});
- clause (e): with the clause removed, the dropped/unreleased case fails;
- the frozen-bytes guard on the template edit alone: "drifted" and "current
  digest not in the released history" until the data change (same commit).
New invariants: the 2^3 previous/current mixes restore (the note follows the
web unit) and an interrupted repair at unit k = 1, 2, 3 leaves a restorable
set that a rerun completes (the injector fails exactly the k-th replace).
Changed expectations: two hardening tests asserted the removed
ReadWritePaths entry; two refresh tests compare its sentence.
```

### d5e75ab3 w2c-contract: docs and CHANGELOG for the shipped units and the two binding rules

```text
w2c-contract: docs and CHANGELOG for the shipped units and the two binding rules
```

## The code report (verbatim)

### Code report: w2c-contract-1 (the CONTRACT release: three units ship their new bytes)

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

## The diff, part 1 (`git diff d77e34f4 d5e75ab3 -- lhpc deploy tests/data docs CHANGELOG.md`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ba902936..df55b2d0 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -2,6 +2,13 @@
 
 ## Unreleased
 
+Changed
+
+- The web, nginx and boot-restore systemd units change (a dead `.meshcore_nm` path removed, a corrected comment).
+  Run `lhpc self-update --repair-integration` once after this update: boot restore runs on the old units meanwhile,
+  but `lhpc self-update --apply` refuses until then: an update starts only from this release's units.
+- Going back to 0.12.0 or older afterwards restores at boot only after that release's repair.
+
 Fixed
 
 - The GPS sky view no longer alternates with "no satellites reported" when gpsd reports the satellites only
diff --git a/deploy/lhpc-boot-restore.service b/deploy/lhpc-boot-restore.service
index 66e68bc4..1b184a09 100644
--- a/deploy/lhpc-boot-restore.service
+++ b/deploy/lhpc-boot-restore.service
@@ -41,7 +41,7 @@ NoNewPrivileges=true
 ProtectSystem=strict
 ProtectHome=read-only
 # Same write set as lhpc-web.service: the restored stacks are the SAME workloads.
-ReadWritePaths=%h/loraham-pi-control -%h/.meshcore_nm /tmp
+ReadWritePaths=%h/loraham-pi-control /tmp
 Environment=PLATFORMIO_CORE_DIR=%h/loraham-pi-control/build/tool-cache/platformio
 Environment=IDF_TOOLS_PATH=%h/loraham-pi-control/build/tool-cache/espressif
 Environment=XDG_CACHE_HOME=%h/loraham-pi-control/build/tool-cache/cache
diff --git a/deploy/lhpc-nginx.service b/deploy/lhpc-nginx.service
index 53585089..7241114c 100644
--- a/deploy/lhpc-nginx.service
+++ b/deploy/lhpc-nginx.service
@@ -20,7 +20,8 @@ ExecStartPre=/usr/sbin/nginx -t -c %h/loraham-pi-control/config/nginx/lhpc.conf
 # remote listener, this refuses to bind that listener ahead of a verified current-boot firewall
 # receipt — regenerating the active config LOOPBACK-ONLY instead (recovery via SSH tunnel; the
 # desired remote intent in local.toml is untouched and never marked applied). No-op when the
-# firewall is absent or nothing remote is desired. Always exits 0 so nginx still starts.
+# firewall is absent or nothing remote is desired. Exits non-zero (nginx then does not
+# start) only when that loopback-only config cannot be established.
 ExecStartPre=%h/loraham-pi-control/venv/lhpc/bin/lhpc webserver --firewall-boot-gate
 ExecStart=/usr/sbin/nginx -c %h/loraham-pi-control/config/nginx/lhpc.conf
 ExecReload=/usr/sbin/nginx -s reload -c %h/loraham-pi-control/config/nginx/lhpc.conf
diff --git a/deploy/lhpc-web.service b/deploy/lhpc-web.service
index 60ebaff0..f0df491d 100644
--- a/deploy/lhpc-web.service
+++ b/deploy/lhpc-web.service
@@ -34,9 +34,8 @@ NotifyAccess=main
 NoNewPrivileges=true
 ProtectSystem=strict
 ProtectHome=read-only
-# runtime root + /tmp for lhpc itself; %h/.meshcore_nm because a stack GUI started under this
-# unit (meshcore-nodegui) keeps its sessions/settings/favourites there. %h expanded by systemd.
-ReadWritePaths=%h/loraham-pi-control -%h/.meshcore_nm /tmp
+# runtime root + /tmp for lhpc itself.
+ReadWritePaths=%h/loraham-pi-control /tmp
 Environment=PLATFORMIO_CORE_DIR=%h/loraham-pi-control/build/tool-cache/platformio
 Environment=IDF_TOOLS_PATH=%h/loraham-pi-control/build/tool-cache/espressif
 Environment=XDG_CACHE_HOME=%h/loraham-pi-control/build/tool-cache/cache
diff --git a/docs/architecture.md b/docs/architecture.md
index dbc40f22..bd32e6e0 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -353,7 +353,7 @@ the runtime root unless absolute.
 | firewall | remote exposure only with this boot's verified receipt, else loopback-only; a stale helper is named before the reboot ([firewall](firewall.md)) | `lhpc/core/service_firewall.py:609` `firewall_gate_activation`, `:781` `firewall_boot_gate`, `:705` `firewall_reapply_notice` | the caller's | the boot receipt above | `tests/host/test_firewall.py::test_gate_refuses_even_already_exposed_when_unverified`, `::test_boot_gate_falls_back_to_loopback_when_unverified`, `::test_boot_gate_fails_closed_when_fallback_cannot_stage`, `::test_reapply_notice_only_for_a_stale_helper` |
 | self-update | an unsafe controller identity blocks apply ([Controller identity](#controller-identity--self-update)) | `lhpc/core/services.py:496` `controller_identity_live` | none (read) | the cached verdict in the self-update envelope | `tests/core/test_controller.py::test_identity_ok`, `::test_missing_checkout_is_not_applicable_not_unsafe`, `::test_self_update_apply_blocked_by_unsafe_identity` |
 | self-update | a running console never has its source changed underneath it ([Controller identity](#controller-identity--self-update)) | `lhpc/core/selfupdate.py:100` `controller_runtime_lock`, `:65` `update_lock` | admission, controller-runtime (exclusive; the console holds it shared), self-update lock | `state/locks/` | `tests/core/test_controller.py::test_apply_refused_while_web_shared_lock_held`, `::test_web_shared_fails_closed_while_apply_exclusive_held` |
-| self-update | one-click update runs only on the canonical, unoverridden units; boot restore also on the next release's units (`compatible`, named with the repair) ([deployment](deployment.md#self-update)) | `lhpc/core/updater_units.py:623` `verify`, `:667` `integration`; `lhpc/core/service_boot_restore.py:44` `_web_integration_proven` | none (read) | the unit files under the user unit folder | `tests/host/test_updater_units.py::test_verify_ok_and_integration_ok`, `::test_verify_overridden_by_dropin`, `::test_verify_unsafe_symlinked_unit`, `::test_the_next_releases_units_read_compatible_for_any_root`; `tests/golden/test_golden_boot_restore.py::test_restores_on_the_next_releases_web_unit_and_says_so` |
+| self-update | one-click update runs only on the canonical, unoverridden units; boot restore also on another release's units (`compatible`, named with the repair) ([deployment](deployment.md#self-update)) | `lhpc/core/updater_units.py:623` `verify`, `:667` `integration`; `lhpc/core/service_boot_restore.py:44` `_web_integration_proven` | none (read) | the unit files under the user unit folder | `tests/host/test_updater_units.py::test_verify_ok_and_integration_ok`, `::test_verify_overridden_by_dropin`, `::test_verify_unsafe_symlinked_unit`, `::test_the_next_releases_units_read_compatible_for_any_root`; `tests/golden/test_golden_boot_restore.py::test_restores_on_the_next_releases_web_unit_and_says_so` |
 | self-update | the update request is claimed once; an in-flight record is cleared only when its helper is proven gone ([deployment](deployment.md#recovery)) | `lhpc/core/service_selfupdate.py:962` `self_update_trigger`, `:1089` `_self_update_run_service_locked`, `:1348` `self_update_recover_request` | admission | `state/selfupdate.request`, `state/selfupdate.inflight`; `lhpc self-update --recover-request` | `tests/install/test_selfupdate_service.py::test_trigger_writes_exclusive_request_no_systemctl`, `::test_recover_inflight_requires_dead_helper`, `::test_run_service_refuses_preexisting_inflight_preserving_both` |
 | self-update | config migration is recorded before the checkout moves; a damaged journal blocks with no change ([deployment](deployment.md#self-update)) | `lhpc/core/selfupdate.py:651` `classify_journal` | the self-update lock | `state/selfupdate-migrate.json` and its git anchor | `tests/install/test_selfupdate_migration.py::test_interrupted_migration_recovered_by_fresh_service`, `::test_journal_persist_failure_refuses_before_mutation`, `::test_malformed_journal_blocks_without_mutation_or_deletion` |
 | radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py:49` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
diff --git a/docs/backlog.md b/docs/backlog.md
index 6e508220..3c4683a6 100644
--- a/docs/backlog.md
+++ b/docs/backlog.md
@@ -15,24 +15,16 @@ Known gaps reviewed and left for a later change. Each entry says what holds the
 
 ## Two-stage unit-template migration
 
-**The first half exists:** the verifier accepts the next release's units as `compatible`
-(`updater_units._NEXT_EDITS`): the nginx unit with a true comment on its firewall boot gate, and the
-web and boot-restore units without the dead `-%h/.meshcore_nm` path. Boot restore runs on them, so
-a box that goes back from the release that ships them still restores
-([maintenance](maintenance.md), *Changing a unit*; `tests/repo/test_unit_templates_frozen.py`).
-
-**Open — the release that ships them:**
-
-* the edits become the templates, their digests join `tests/data/unit-templates-released.sha256`,
-  and the previous bytes are accepted the same way;
-* the update path still cannot write new units: `service_selfupdate._refresh_units_post_update()`
-  repairs **in process** with the pre-update templates, and the systemd helper cannot write units
-  at all (`ProtectHome=read-only`, writable paths limited to the runtime root and `/tmp`). The
-  operator path is to run the repair through the new checkout's CLI; a failure leaves the units as
-  they are, recorded *units-stale*. Until then an update that brings new bytes ends *units-stale*
-  and `lhpc self-update --repair-integration` finishes it;
-* that release proves the forward and mixed unit states on a box. A downgrade below the accepting
-  release does not restore at boot until that release's own repair runs.
+The procedure exists and ran once (0.12.1 accepted, 0.13.0 ships the web, nginx and boot-restore
+units; [maintenance](maintenance.md), *Changing a unit*; `tests/repo/test_unit_templates_frozen.py`).
+
+**Deferred:** the update path cannot write new units itself.
+`service_selfupdate._refresh_units_post_update()` repairs **in process** with the pre-update
+templates, and the systemd helper cannot write units at all (`ProtectHome=read-only`, writable paths
+limited to the runtime root and `/tmp`). So an update that brings new unit bytes ends with the units
+`compatible`, and the operator runs `lhpc self-update --repair-integration` once. What holds the line:
+boot restore runs on `compatible` units, status and doctor name the repair, and `lhpc self-update
+--apply` refuses to start the next update until it ran.
 
 **Workaround for new writable paths:** redirect the state into the runtime root with an
 environment variable instead of granting a HOME path (Sideband:
diff --git a/docs/deployment.md b/docs/deployment.md
index 8918eec2..ad314c25 100644
--- a/docs/deployment.md
+++ b/docs/deployment.md
@@ -61,7 +61,7 @@ Back up `config/`, `profiles/` and the app data under `state/` first
   `systemctl --user set-property` overrides and the `service.d/` / `lhpc-.service.d/` folders). To repair the integration, run
   `lhpc self-update --repair-integration` from a shell — it restores the exact canonical set on an
   existing or `--no-service` deployment (the console's *Repair & update* does the same while its
-  unit still has bus access). Units of the next release read `compatible`: boot restore runs on them
+  unit still has bus access). Units of another release read `compatible`: boot restore runs on them
   and `lhpc status` and `lhpc doctor` name them; only the shell repair rewrites them to this
   version's ([maintenance](maintenance.md), *Changing a unit*).
 - **Manual path.** `lhpc self-update --apply` from an operator shell (refused inside a managed
diff --git a/docs/maintenance.md b/docs/maintenance.md
index eac5a8e5..2e5dc2af 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -33,7 +33,8 @@ What CI enforces, branches and releases, the pin-bump recipe, and the gotchas on
     against the real signature (`tests/conftest.py`, `tests/repo/test_fake_signatures.py`);
   - the ten systemd unit templates render to the hashes in `tests/data/unit-templates.sha256`; every
     current digest is in the append-only history `tests/data/unit-templates-released.sha256`, and the
-    next release's units (`updater_units._NEXT_EDITS`) render to `tests/data/unit-templates-next.sha256`
+    other release's units (`updater_units._COMPATIBLE_EDITS`) render to
+    `tests/data/unit-templates-compatible.sha256`, which always holds the 0.12.0/0.12.1 units
     (`tests/repo/test_unit_templates_frozen.py`; the procedure: *Changing a unit* below).
 - `pin-validation`: every pinned source has a rule in the release bot's policy, and every pin is
   an ancestor of its live branch with its referenced scripts present
@@ -157,23 +158,27 @@ repositories.
     introduces the narrower range, so controller and daemon agree; a refusal change still needs a
     minor's justification. 0.12.1, by the maintainer's decision, shipped the first half of a unit
     change as a patch: the verifier accepts the next release's three units as `compatible`, while
-    no installed unit byte changes; going back below 0.12.1 after the release that ships those units
-    restores at boot only after the older release's `lhpc self-update --repair-integration`.
+    no installed unit byte changes; 0.13.0 ships them. Going back below 0.12.1 after 0.13.0 restores
+    at boot only after the older release's `lhpc self-update --repair-integration`.
     0.12.2, by the maintainer's decision, changed a refusal and added a selector as a patch: the
     start refusal for a band the daemon holds names the narrower, correct remedy, and
     `lhpc stack stop daemon` takes `--band` (that remedy; daemon only); no format or default
     changes.
   - **Changing a unit** takes two releases, so a box restores at boot after an update and after a
-    downgrade between them. First a release that only ACCEPTS the new bytes: `_NEXT_EDITS` in
-    `lhpc/core/updater_units.py`, its digests in `unit-templates-next.sha256`; `verify` reads an
-    installed unit equal to them as `compatible`, and boot restore runs on it. Then the release that
-    ships them: the edits become the templates, their digests are appended to the released history,
-    and the previous bytes are accepted the same way (that release runs the forward and mixed unit
-    proof and shows its current renders equal the earlier `unit-templates-next.sha256`). Accepted
-    digests are append-only within a minor line; a minor may prune them, with a CHANGELOG note naming
-    the releases whose units then need `lhpc self-update --repair-integration` after the update. A
-    downgrade to a release older than the accepting one (also skipping over it) does not restore at
-    boot until that older release's `lhpc self-update --repair-integration` rewrites its own units.
+    downgrade between them. First a release that only ACCEPTS the new bytes: edits in
+    `_COMPATIBLE_EDITS` (`lhpc/core/updater_units.py`), pinned in `unit-templates-compatible.sha256`;
+    `verify` reads an installed unit equal to their render as `compatible`, and boot restore runs on
+    it. Then the release that ships them: the edits become the templates, their digests are appended
+    to the released history, and the table is inverted so the previous bytes read `compatible`
+    (0.13.0 did this for 0.12.1's three units). Two rules bind every later release: the operator
+    `lhpc self-update --apply` refuses while any unit reads `compatible`, naming
+    `lhpc self-update --repair-integration` (one-click already needs `ok` units), and the
+    0.12.0/0.12.1 units stay `compatible` (the guard enforces it), so a box that went back to 0.12.1
+    and then straight to a later release still restores. Other accepted digests are append-only
+    within a minor line; a minor may prune them, with a CHANGELOG note naming the releases whose units
+    then need the repair after the update. A downgrade to a release older than the accepting one
+    (also skipping over it) does not restore at boot until that older release's repair rewrites its
+    own units.
   - **The proof a patch needs** is what its change calls for. A pin move: the binary builder's
     smoke and clean-runtime test plus the
     [release-verification lane](testlab.md#running-the-verification-lanes), no box. That lane
diff --git a/docs/operations.md b/docs/operations.md
index ff42b74c..2ce401fa 100644
--- a/docs/operations.md
+++ b/docs/operations.md
@@ -33,7 +33,7 @@ a `kill` hint instead ([safety model](architecture.md#safety-model)).
   **saved** configuration through the normal gated start path (hardware, band arbitration,
   callsign, firewall exposure, TX mode), as a web or CLI start does.
 - It refuses to act unless the web console unit is enabled and byte-exact canonical (this
-  version's, or the next release's: `compatible`, which it names with the repair), and honours
+  version's, or another release's: `compatible`, which it names with the repair), and honours
   `[boot] restore` in `local.toml` (strictly boolean; anything else disables restore).
 - An explicit `lhpc stack stop` is the last word: the stack stays down across reboots even when
   the stop could not verify the process gone; the next `stack start` makes it restorable again.
@@ -204,7 +204,7 @@ Three things are not regenerable; a default `uninstall.sh` keeps them (plus `bac
 `uninstall.sh` writes the `.lhpc-uninstalling` guard (no new tasks), refuses on active or
 unprovable jobs or any UNKNOWN component, stops clients before the shared daemon and verifies
 they stopped; if it cannot prove that, it removes the guard and deletes nothing. It stops and
-removes only canonical units of the same root (this version's or the next release's).
+removes only canonical units of the same root (this version's or another release's).
 
 **Full backup.** With every stack stopped (`lhpc stack stop <stack>`), as the LHPC user; `-p`
 keeps the `0600` modes, and missing app-data directories are skipped:
diff --git a/lhpc/adapters/web/app.py b/lhpc/adapters/web/app.py
index 35b2aad0..440d952f 100644
--- a/lhpc/adapters/web/app.py
+++ b/lhpc/adapters/web/app.py
@@ -1348,8 +1348,8 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
         # (normal|overwrite); a stale overwrite tick with a meanwhile-clean, fast-forwardable tree
         # drops to normal. repair_and_trigger delegates straight to the marker trigger when the units
         # are already canonical, and otherwise migrates a non-canonical same-root deployment (old/%h units,
-        # no .path) to the canonical set first — all in this one click — except compatible units (a
-        # newer release's), which only the shell repair rewrites.
+        # no .path) to the canonical set first — all in this one click — except compatible units
+        # (another release's), which only the shell repair rewrites.
         overwrite = (request.form.get("overwrite") == "yes"
                      and (service.self_update_local_dirty() or service.self_update_ff_blocked()))
         # Every gate runs NOW (a refusal stays on /stacks); the marker itself is written only
diff --git a/lhpc/adapters/web/templates/_update.html b/lhpc/adapters/web/templates/_update.html
index 83c9d66a..61441da1 100644
--- a/lhpc/adapters/web/templates/_update.html
+++ b/lhpc/adapters/web/templates/_update.html
@@ -100,7 +100,7 @@
           and not ((updater.per_unit or {}).values()
                    | select('in', ['foreign', 'ambiguous', 'overridden', 'unsafe', 'unreadable'])
                    | list) %}
-    <p class="depnote depnote-warn">The units are a newer release's (compatible): run
+    <p class="depnote depnote-warn">The units are another release's (compatible): run
       <code>lhpc self-update --repair-integration</code> from an operator shell, then update.</p>
   {% elif updater and updater.status != 'ok' and st.update_available and not updater.fixable %}
     <p class="depnote depnote-warn">One-click update needs attention ({{ updater.status }} web/updater
diff --git a/lhpc/core/service_boot_restore.py b/lhpc/core/service_boot_restore.py
index 57012160..68c724d0 100644
--- a/lhpc/core/service_boot_restore.py
+++ b/lhpc/core/service_boot_restore.py
@@ -45,7 +45,7 @@ class BootRestoreOpsMixin:
         """BOTH proofs, bus-free and descriptor-safe: the enablement symlink resolves to the
         expected managed unit path, AND lhpc-web.service verifies byte-exact/no-drop-in via the
         PER-UNIT canonical verifier (never the aggregate verdict, never unrelated units) — this
-        version's unit, or the next release's (`compatible`: proven, with a note as the reason)."""
+        version's unit, or another release's (`compatible`: proven, with a note as the reason)."""
         user_dir = self._user_unit_dir()
         root = Path(self._paths.runtime_root)
         _root, checkout, venv = updater_units.deployment_paths(str(root))
diff --git a/lhpc/core/service_firewall.py b/lhpc/core/service_firewall.py
index 0a47d527..bc0efbd7 100644
--- a/lhpc/core/service_firewall.py
+++ b/lhpc/core/service_firewall.py
@@ -881,7 +881,7 @@ class FirewallOpsMixin:
         integ = self.updater_integration()
         verdict = integ.get("per_unit", {}).get(updater_units.NGINX_UNIT)
         if verdict in (updater_units.OK, updater_units.COMPATIBLE):
-            return None                    # unit current (or the next release's) -> gate present
+            return None                    # unit current (or another release's) -> gate present
         owned_stale = verdict in (updater_units.MISSING, updater_units.MODIFIED_OURS)
         managed = bool(integ.get("managed"))
         # Foreign/ambiguous unit (cannot be safely replaced), OR a managed (bus-blocked) updater
diff --git a/lhpc/core/service_selfupdate.py b/lhpc/core/service_selfupdate.py
index 100c41d4..fea4ac7e 100644
--- a/lhpc/core/service_selfupdate.py
+++ b/lhpc/core/service_selfupdate.py
@@ -483,8 +483,7 @@ class SelfUpdateOpsMixin:
                 ours = all(v in (updater_units.OK, updater_units.MISSING,
                                  updater_units.MODIFIED_OURS) for v in integ["per_unit"].values())
             verdicts = (integ.get("per_unit") or {}).values()
-            # A newer release's units, alone or beside missing / changed units of this deployment:
-            # ours, but this code's render must not be written over the newer ones.
+            # Another release's units (alone or beside missing / changed ones): ours, never rewritten here.
             newer = (updater_units.COMPATIBLE in verdicts
                      and all(v in (updater_units.OK, updater_units.COMPATIBLE, updater_units.MISSING,
                                    updater_units.MODIFIED_OURS) for v in verdicts))
@@ -500,8 +499,7 @@ class SelfUpdateOpsMixin:
         if not _op_exists(py):
             py = _sys.executable
         try:
-            # A newer release's units are not rewritten with this code's render (it would put
-            # the older bytes back); the verify-set below still decides.
+            # Another release's units are not rewritten here; the verify-set below decides.
             rep = (None if helper or newer
                    else self.self_update_repair_integration(restart=False))
         except Exception as exc:
@@ -515,11 +513,11 @@ class SelfUpdateOpsMixin:
             return True, "units canonical"
         detail = (chk.stdout or chk.stderr or "").strip()[:140]
         if mixed:
-            return False, ("units of a newer release beside missing or changed ones — left as they "
+            return False, ("units of another release beside missing or changed ones — left as they "
                            "are; repair them from an operator shell with `lhpc self-update "
                            f"--repair-integration`: {detail}")[:240]
         if newer:
-            return False, f"units of a newer release — left as they are: {detail}"
+            return False, f"units of another release — left as they are: {detail}"
         if rep is None:
             return False, f"units not canonical: {detail}"
         return False, f"units still not canonical after repair ({rep.summary[:60]}): {detail}"
@@ -614,6 +612,9 @@ class SelfUpdateOpsMixin:
                                 "`lhpc self-update --apply` from an interactive operator shell",
                                 data={"reason": "managed-unit"},
                                 next_commands=["lhpc self-update --apply"])
+        if (why := updater_units.update_refusal(self.updater_integration())):
+            return ActionResult(False, why, data={"integration": "compatible"},
+                                next_commands=["lhpc self-update --repair-integration"])
         _S = 30.0
         act = self._system.runner.run(
             ["systemctl", "--user", "is-active", "--quiet", updater_units.WEB_UNIT], _S)
@@ -1869,9 +1870,8 @@ class SelfUpdateOpsMixin:
         per = integ.get("per_unit", {})
         bad = {k: v for k, v in per.items() if v not in fixable_set}
         if bad and all(v == updater_units.COMPATIBLE for v in bad.values()):
-            # A newer release's units: ours, but only the shell repair rewrites them (this
-            # console's service cannot write the user unit folder).
-            return ActionResult(False, "The units are a newer release's (compatible): run "
+            # Another release's units: ours, but only the shell repair rewrites them.
+            return ActionResult(False, "The units are another release's (compatible): run "
                                 "`lhpc self-update --repair-integration` from an operator shell, "
                                 "then update.", data={"integration": status, "compatible": True},
                                 next_commands=["lhpc self-update --repair-integration"])
diff --git a/lhpc/core/services.py b/lhpc/core/services.py
index b0a1c3b9..407cd3b5 100644
--- a/lhpc/core/services.py
+++ b/lhpc/core/services.py
@@ -1347,7 +1347,7 @@ class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMix
             details.append("  ! BOOT RESTORE WILL BE SKIPPED — " + why)
             details.append("    stacks will NOT come back after a reboot until this is repaired:")
             details.append("      lhpc self-update --repair-integration")
-        elif why:                               # proven on a newer release's units: say so
+        elif why:                               # proven on another release's units: say so
             details.append("")
             details.append(f"  ! {why}")
 
diff --git a/lhpc/core/updater_units.py b/lhpc/core/updater_units.py
index 799f7c00..28d64fc0 100644
--- a/lhpc/core/updater_units.py
+++ b/lhpc/core/updater_units.py
@@ -71,17 +71,15 @@ _MAX_UNIT_BYTES = 64 * 1024
 # cannot write at all (ProtectHome=read-only). So a template edit silently strands every box that
 # already has the old unit installed.
 #
-# A changed unit ships in two releases (docs/backlog.md, the staged unit migration): first the
-# release that ACCEPTS the new bytes (`_NEXT_EDITS` below: `verify` reads them `compatible`, and
-# boot restore runs on them), then the release that ships them. Until then these strings stay as
-# they are. If a stack needs a new writable path, redirect it into {root} with an environment
+# A changed unit ships in two releases (docs/maintenance.md, *Changing a unit*): first the release
+# that ACCEPTS the new bytes, then the release that ships them and accepts the previous ones
+# (`_COMPATIBLE_EDITS` below: `verify` reads them `compatible`, and boot restore runs on them).
+# Outside such a migration these strings stay as they are. If a stack needs a new writable path, redirect it into {root} with an environment
 # variable instead — that is why Sideband's Kivy state goes to KIVY_HOME={root}/state/sideband/kivy
 # rather than ~/.kivy.
 #
 # tests/repo/test_unit_templates_frozen.py enforces this.
 
-# The ReadWritePaths comment inside _WEB names a `meshcore-nodegui` component that does not exist;
-# no shipped component uses ~/.meshcore_nm. `_NEXT_EDITS` removes both in the next release's units.
 _WEB = """\
 # LoRaHAM Pi Control web console — CANONICAL managed unit (generated; do not hand-edit).
 # Rendered by lhpc.core.updater_units; the one-click updater proves this file BYTE-EXACT.
@@ -119,9 +117,8 @@ NotifyAccess=main
 NoNewPrivileges=true
 ProtectSystem=strict
 ProtectHome=read-only
-# runtime root + /tmp for lhpc itself; %h/.meshcore_nm because a stack GUI started under this
-# unit (meshcore-nodegui) keeps its sessions/settings/favourites there. %h expanded by systemd.
-ReadWritePaths={root} -%h/.meshcore_nm /tmp
+# runtime root + /tmp for lhpc itself.
+ReadWritePaths={root} /tmp
 Environment=PLATFORMIO_CORE_DIR={root}/build/tool-cache/platformio
 Environment=IDF_TOOLS_PATH={root}/build/tool-cache/espressif
 Environment=XDG_CACHE_HOME={root}/build/tool-cache/cache
@@ -238,7 +235,8 @@ ExecStartPre=/usr/sbin/nginx -t -c {root}/config/nginx/lhpc.conf
 # remote listener, this refuses to bind that listener ahead of a verified current-boot firewall
 # receipt — regenerating the active config LOOPBACK-ONLY instead (recovery via SSH tunnel; the
 # desired remote intent in local.toml is untouched and never marked applied). No-op when the
-# firewall is absent or nothing remote is desired. Always exits 0 so nginx still starts.
+# firewall is absent or nothing remote is desired. Exits non-zero (nginx then does not
+# start) only when that loopback-only config cannot be established.
 ExecStartPre={venv}/bin/lhpc webserver --firewall-boot-gate
 ExecStart=/usr/sbin/nginx -c {root}/config/nginx/lhpc.conf
 ExecReload=/usr/sbin/nginx -s reload -c {root}/config/nginx/lhpc.conf
@@ -376,7 +374,7 @@ NoNewPrivileges=true
 ProtectSystem=strict
 ProtectHome=read-only
 # Same write set as lhpc-web.service: the restored stacks are the SAME workloads.
-ReadWritePaths={root} -%h/.meshcore_nm /tmp
+ReadWritePaths={root} /tmp
 Environment=PLATFORMIO_CORE_DIR={root}/build/tool-cache/platformio
 Environment=IDF_TOOLS_PATH={root}/build/tool-cache/espressif
 Environment=XDG_CACHE_HOME={root}/build/tool-cache/cache
@@ -402,33 +400,34 @@ _TEMPLATES = {WEB_UNIT: _WEB, HELPER_UNIT: _HELPER, PATH_UNIT: _PATH, NGINX_UNIT
               RESTART_UNIT: _NGINX_RESTART, RESTART_PATH_UNIT: _NGINX_RESTART_PATH,
               BOOT_RESTORE_UNIT: _BOOT_RESTORE}
 
-# --------------------------------------------------------------------------- the next release's units
-# The units the next release will ship, as exact edits of the templates above (old, new). An
-# installed unit equal to their render for this deployment reads `compatible`: boot restore runs on
-# it, so a box that goes back from that release to this one still restores. No replacement text
-# contains a brace (the template is formatted after the edits), and each edit matches its template
-# exactly once; an edit that does not is left out of `_NEXT_TEMPLATES` here, never raised, and
-# tests/repo/test_unit_templates_frozen.py fails on it.
-_NEXT_EDITS = {
+# --------------------------------------------------------------------------- another release's units
+# The units of the release on the other side of a unit migration, as exact edits of the templates
+# above (old, new): here the previous release's (0.12.0/0.12.1). An installed unit equal to their
+# render for this deployment reads `compatible`: boot restore runs on it, so a box updated from that
+# release restores before `lhpc self-update --repair-integration` rewrites this version's. No
+# replacement text contains a brace (the template is formatted after the edits), and each edit
+# matches its template exactly once; an edit that does not is left out of `_COMPATIBLE_TEMPLATES`
+# here, never raised, and tests/repo/test_unit_templates_frozen.py fails on it.
+_COMPATIBLE_EDITS = {
     WEB_UNIT: (
-        ("# runtime root + /tmp for lhpc itself; %h/.meshcore_nm because a stack GUI started under "
+        ("# runtime root + /tmp for lhpc itself.\n",
+         "# runtime root + /tmp for lhpc itself; %h/.meshcore_nm because a stack GUI started under "
          "this\n# unit (meshcore-nodegui) keeps its sessions/settings/favourites there. %h expanded "
-         "by systemd.\n",
-         "# runtime root + /tmp for lhpc itself.\n"),
-        (" -%h/.meshcore_nm /tmp\n", " /tmp\n")),
+         "by systemd.\n"),
+        (" /tmp\n", " -%h/.meshcore_nm /tmp\n")),
     NGINX_UNIT: (
-        ("# firewall is absent or nothing remote is desired. Always exits 0 so nginx still starts.\n",
-         "# firewall is absent or nothing remote is desired. Exits non-zero (nginx then does not\n"
-         "# start) only when that loopback-only config cannot be established.\n"),),
-    BOOT_RESTORE_UNIT: ((" -%h/.meshcore_nm /tmp\n", " /tmp\n"),),
+        ("# firewall is absent or nothing remote is desired. Exits non-zero (nginx then does not\n"
+         "# start) only when that loopback-only config cannot be established.\n",
+         "# firewall is absent or nothing remote is desired. Always exits 0 so nginx still starts.\n"),),
+    BOOT_RESTORE_UNIT: ((" /tmp\n", " -%h/.meshcore_nm /tmp\n"),),
 }
 
 
-def _next_template(kind: str):
-    """The next release's template for `kind`, or None (no edits, or an edit that does not match
+def _compatible_template(kind: str):
+    """The other release's template for `kind`, or None (no edits, or an edit that does not match
     its template exactly once, or a replacement with a brace)."""
     text = _TEMPLATES.get(kind)
-    edits = _NEXT_EDITS.get(kind)
+    edits = _COMPATIBLE_EDITS.get(kind)
     if text is None or not edits:
         return None
     for old, new in edits:
@@ -438,12 +437,12 @@ def _next_template(kind: str):
     return text
 
 
-_NEXT_TEMPLATES = {k: t for k in _NEXT_EDITS if (t := _next_template(k)) is not None}
+_COMPATIBLE_TEMPLATES = {k: t for k in _COMPATIBLE_EDITS if (t := _compatible_template(k)) is not None}
 
 
-def render_next(kind: str, root: str, checkout: str, venv: str):
-    """The next release's unit text for `kind` (as `render`), or None when it ships none."""
-    tmpl = _NEXT_TEMPLATES.get(kind)
+def render_compatible(kind: str, root: str, checkout: str, venv: str):
+    """The other release's unit text for `kind` (as `render`), or None when there is none."""
+    tmpl = _COMPATIBLE_TEMPLATES.get(kind)
     if tmpl is None:
         return None
     return tmpl.format(root=root, checkout=checkout, venv=venv,
@@ -451,11 +450,11 @@ def render_next(kind: str, root: str, checkout: str, venv: str):
                        guard=UNINSTALL_GUARD)
 
 
-def _is_next(kind: str, text: str, root: str, checkout: str, venv: str) -> bool:
-    """`text` is the next release's unit for this deployment. Never raises: any problem with the
-    next render reads as no match (the caller then classifies as before)."""
+def _is_compatible(kind: str, text: str, root: str, checkout: str, venv: str) -> bool:
+    """`text` is the other release's unit for this deployment. Never raises: any problem with
+    that render reads as no match (the caller then classifies as before)."""
     try:
-        nxt = render_next(kind, root, checkout, venv)
+        nxt = render_compatible(kind, root, checkout, venv)
     except Exception:
         return False
     return nxt is not None and text == nxt
@@ -483,9 +482,9 @@ def deployment_paths(root: str) -> tuple[str, str, str]:
 OK = "ok"                 # byte-exact canonical, no drop-in, no symlinked dir
 MISSING = "missing"
 MODIFIED_OURS = "modified_ours"   # not byte-exact but carries THIS root's provenance
-COMPATIBLE = "compatible"  # byte-exact the NEXT release's unit for this deployment (_NEXT_EDITS)
+COMPATIBLE = "compatible"  # byte-exact another release's unit for this deployment (_COMPATIBLE_EDITS)
 # The one line every surface prints for a `compatible` set (boot restore, doctor, status).
-COMPATIBLE_NOTE = ("units-compatible: the managed units are a newer release's — boot restore runs "
+COMPATIBLE_NOTE = ("units-compatible: the managed units are another release's — boot restore runs "
                    "on them; `lhpc self-update --repair-integration` rewrites this version's")
 FOREIGN = "foreign"       # provenance names another runtime root
 AMBIGUOUS = "ambiguous"   # neither clearly ours nor clearly foreign
@@ -640,7 +639,7 @@ def verify(user_dir: Path, kind: str, root: str, checkout: str, venv: str) -> st
         return OVERRIDDEN
     if text == render(kind, root, checkout, venv):
         return OK
-    if _is_next(kind, text, root, checkout, venv):
+    if _is_compatible(kind, text, root, checkout, venv):
         return COMPATIBLE
     return _classify_mismatch(kind, text, root)
 
@@ -659,7 +658,7 @@ def verify_file(path: Path, kind: str, root: str) -> str:
         return UNSAFE if getattr(exc, "errno", None) == _errno.ELOOP else UNREADABLE
     if text == render(kind, root, checkout, venv):
         return OK
-    if _is_next(kind, text, root, checkout, venv):
+    if _is_compatible(kind, text, root, checkout, venv):
         return COMPATIBLE
     return _classify_mismatch(kind, text, root)
 
@@ -675,12 +674,23 @@ def integration(user_dir: Path, root: str) -> dict:
     elif all(v == OK for v in per.values()):
         status = "ok"
     elif all(v in (OK, COMPATIBLE) for v in per.values()):
-        status = "compatible"             # a newer release's units: boot restore runs on them
+        status = "compatible"             # another release's units: boot restore runs on them
     else:
         status = "incomplete"
     return {"status": status, "per_unit": per}
 
 
+def update_refusal(integ: dict):
+    """Why an operator update may not start from this `integration()` result, or None. Units of
+    another release (`compatible`, alone or beside missing / changed ones) are repaired first: a
+    later release that changes a unit accepts only this release's units and the 0.12.0/0.12.1 ones
+    (docs/maintenance.md, *Changing a unit*), and every release keeps this rule."""
+    if integ.get("status") == COMPATIBLE or COMPATIBLE in (integ.get("per_unit") or {}).values():
+        return ("the managed units are another release's (compatible) — run `lhpc self-update "
+                "--repair-integration` first, then update")
+    return None
+
+
 def write_set(user_dir: Path, root: str) -> list[tuple[str, str]]:
     """Write/refresh the canonical unit set (repair op). Overwrites ONLY `missing`/`modified_ours`
     units; REFUSES the WHOLE set if ANY unit is `foreign`/`ambiguous`/`overridden`/`unsafe`/
@@ -744,10 +754,10 @@ def _main(argv: list[str]) -> int:
         import sys
         sys.stdout.write(render(argv[2], argv[3], argv[4], argv[5]))
         return 0
-    # `... render-next <kind> <root> <checkout> <venv>` — the next release's unit (exit 1: none).
-    if len(argv) == 6 and argv[1] == "render-next":
+    # `... render-compatible <kind> <root> <checkout> <venv>` — the other release's unit (exit 1: none).
+    if len(argv) == 6 and argv[1] == "render-compatible":
         import sys
-        text = render_next(argv[2], argv[3], argv[4], argv[5])
+        text = render_compatible(argv[2], argv[3], argv[4], argv[5])
         if text is None:
             return 1
         sys.stdout.write(text)
@@ -778,7 +788,7 @@ def _main(argv: list[str]) -> int:
         return 0
     import sys
     sys.stderr.write("usage: python -m lhpc.core.updater_units "
-                     "render <kind> <root> <checkout> <venv> | render-next <kind> <root> "
+                     "render <kind> <root> <checkout> <venv> | render-compatible <kind> <root> "
                      "<checkout> <venv> | verify-file <kind> <file> <root> | verify-set <root>\n")
     return 2
 
diff --git a/tests/data/unit-templates-compatible.sha256 b/tests/data/unit-templates-compatible.sha256
new file mode 100644
index 00000000..759352f1
--- /dev/null
+++ b/tests/data/unit-templates-compatible.sha256
@@ -0,0 +1,3 @@
+67ee9e95fa5f14b3f1e0c7c2ab8a76671f3b5521963438f5980c08110eab267e  lhpc-web.service
+101d941f78e321c87340ed5dba74c93a910f642a662dc5de709241a8367fcf61  lhpc-nginx.service
+ecb2dde6c6f48476acc0d6f216fb30d3d9ef8087a530607b1e876448382c3652  lhpc-boot-restore.service
diff --git a/tests/data/unit-templates-next.sha256 b/tests/data/unit-templates-next.sha256
deleted file mode 100644
index 7d554d16..00000000
--- a/tests/data/unit-templates-next.sha256
+++ /dev/null
@@ -1,3 +0,0 @@
-00e6e592fe51dd2588771cc79cd5bbbd9b30c325b9809e09de805986bc2cb844  lhpc-web.service
-f4e25da2b90bd029d0fbb3618b1a7646d7a1b6fd781fa746a6d11c8f246734de  lhpc-nginx.service
-4c02be609b12fe82715b863b817004b2dadbc32dedbac4d5735bbb690124b539  lhpc-boot-restore.service
diff --git a/tests/data/unit-templates-released.sha256 b/tests/data/unit-templates-released.sha256
index e51c0c3f..b57825f3 100644
--- a/tests/data/unit-templates-released.sha256
+++ b/tests/data/unit-templates-released.sha256
@@ -8,3 +8,6 @@ ecb2dde6c6f48476acc0d6f216fb30d3d9ef8087a530607b1e876448382c3652  lhpc-boot-rest
 c319e238350b56f5dc5ce23ef14e7d6e2f599b9d4c2e87edf3e2c1a4633ee769  lhpc-firewall.service  0.12.0
 9b0928ac6b1debbf792a140dd5886201d663f80cc6ba8114843ab4bf3dee9d0b  lhpc-firewall-check.service  0.12.0
 c4d477cb51b813e18a025612cc469e340320afb9ba5c7536701c5abb565ee818  lhpc-firewall-check.timer  0.12.0
+00e6e592fe51dd2588771cc79cd5bbbd9b30c325b9809e09de805986bc2cb844  lhpc-web.service  0.13.0
+f4e25da2b90bd029d0fbb3618b1a7646d7a1b6fd781fa746a6d11c8f246734de  lhpc-nginx.service  0.13.0
+4c02be609b12fe82715b863b817004b2dadbc32dedbac4d5735bbb690124b539  lhpc-boot-restore.service  0.13.0
diff --git a/tests/data/unit-templates.sha256 b/tests/data/unit-templates.sha256
index 1ab684a4..8f5a0ff2 100644
--- a/tests/data/unit-templates.sha256
+++ b/tests/data/unit-templates.sha256
@@ -1,10 +1,10 @@
-67ee9e95fa5f14b3f1e0c7c2ab8a76671f3b5521963438f5980c08110eab267e  lhpc-web.service
+00e6e592fe51dd2588771cc79cd5bbbd9b30c325b9809e09de805986bc2cb844  lhpc-web.service
 c10ab4d516da4b38824c9af955ba2cd012d0de4d6d776fcd297bade33108813e  lhpc-selfupdate.service
 b0adcfda127b1c2dd37b1b85b3a020ba862db105f02ec22479177c0de8770b2d  lhpc-selfupdate.path
-101d941f78e321c87340ed5dba74c93a910f642a662dc5de709241a8367fcf61  lhpc-nginx.service
+f4e25da2b90bd029d0fbb3618b1a7646d7a1b6fd781fa746a6d11c8f246734de  lhpc-nginx.service
 047a1056eb4994ea7f23219a69792c955fdc9c62d377a13e419a0df392394fc6  lhpc-nginx-restart.service
 23deeea7fec3725a8beb72c6da1105ad2c0cae48de8be6fbcfb088785aa218b2  lhpc-nginx-restart.path
-ecb2dde6c6f48476acc0d6f216fb30d3d9ef8087a530607b1e876448382c3652  lhpc-boot-restore.service
+4c02be609b12fe82715b863b817004b2dadbc32dedbac4d5735bbb690124b539  lhpc-boot-restore.service
 c319e238350b56f5dc5ce23ef14e7d6e2f599b9d4c2e87edf3e2c1a4633ee769  lhpc-firewall.service
 9b0928ac6b1debbf792a140dd5886201d663f80cc6ba8114843ab4bf3dee9d0b  lhpc-firewall-check.service
 c4d477cb51b813e18a025612cc469e340320afb9ba5c7536701c5abb565ee818  lhpc-firewall-check.timer
```
