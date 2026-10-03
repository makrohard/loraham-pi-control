# Gate 1 — code review request, batch T5

Please judge the plan and each code commit of batch T5 below: does each commit do what the
plan says, are its tests able to fail for the right reason (red-before), are the recorded known
defects real and precisely described, and does anything in the diff change production behaviour
(it must not: tests only)? Answer per commit in this form, then one final line:

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Final line: GREEN / GREEN WITH NOTES / RED

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

Note: IP literals of test data are shown as `<ip-…>` placeholders in this file only.

## The plan

# PLAN T5 — CI guards and the named missing contract tests (tests/CI only)

Base `integration/0.12.0` + the T2 series on the same branch. Files: `tests/` (incl. `tests/repo`,
`tests/data`), `.github/workflows/ci.yml` (one new job), `docs/maintenance.md` (one sentence per
guard). `lhpc/` and `testlab/` untouched. No mutation testing (the maintainer's rule; the words
"and the mutation job" in the completion criterion are stale — handler's clarification).

## 1. Fakes keep the real signature

Today: 1,725 `monkeypatch.setattr` calls (`ControllerService` 511, `type(svc)` 210, `svc` 158,
module aliases, 32 dotted strings, 10 with a computed name). 760 replace with a lambda, many of
them `lambda *a, **k`. No `autospec` anywhere. A static AST pass resolves about 1,420; the
`svc`/`type(svc)` targets need type guessing, and same-named local defs mis-resolve. It already
finds a stale fake: `tests/core/test_boot_restore.py:581` fakes `start(…, params, daemon_overrides,
file_overrides, …)`, parameters the real `start` (service_lifecycle_ops.py) no longer has. The
risk the brief names (B6) is a permissive fake swallowing a call the real function would refuse.

**Simpler form (guardrail: no framework):** check the CALL, not the fake's text. A ~40-line
wrapper in `tests/conftest.py` replaces `pytest.MonkeyPatch.setattr` for the session. When a lambda
or def is put over an `lhpc.*` function or method (module function; method on its class, bound
with `self`; method on one instance, without), it installs a thin wrapper. The wrapper binds each
call to the REAL `inspect.signature` first, then calls the fake. That is what `create_autospec`
checks, applied to every site at once with no rewrite, whatever the target expression. Values,
classes, callable objects and non-LHPC targets are installed unchanged.

Test: `tests/repo/test_fake_signatures.py`, one case per target form plus the pass-through.
Red-before: without the guard, 4 of 6 fail.

Risk: a test that depends on the extra frame (one does: `test_secrets_backup.py` reads
`inspect.stack()[1]`), or on `getattr(...) is fake`. Ruled out by running every test directory
with the guard. The one frame-reading test is adjusted to look past the wrapper.

## 2. Unit templates frozen

Today: `tests/host/test_updater_units.py::test_unit_bytes_are_the_frozen_render` pins the seven
user units (inline `_UNIT_BYTES_0_1_6`, rendered with a fixed home root). The three firewall units
(`firewall.py:411/439/458`, installed by the sudo apply script) are not pinned at all. No CI step
mentions frozen units.

Change (extend, not duplicate): `tests/repo/test_unit_templates_frozen.py` checks all ten renders
against the checked-in `tests/data/unit-templates.sha256`, with the exact failure message from the
brief. The user units are hashed as the shipped `%h` render, so the file equals
`sha256sum deploy/<unit>`. The inline dict and the old test are removed from tests/host. The
backlog's "Holding the line" sentence names the new test.

Red-before: one comment byte changed in `_WEB`, and one directive in the firewall timer → red.

## 3. The five contract tests (backlog "Contract gaps" 1, 3, 4, 5, 7)

One module each, at the widest seam:

| contract | module | seam → observed effect | red-before mutation |
|---|---|---|---|
| firewall route effects | tests/web/test_firewall_configure_route.py | `POST /firewall/configure` → candidate in `firewall-apply.sh` → helper ruleset (accept/drop per route; recommended preset; a refused submission changes nothing) | route ignores `allow_*` |
| binary-channel switch | tests/install/test_channel_switch_route.py | `POST /action op=install source=…` → the job argv → that CLI command in-process → receipt, MeshCom auth (open on binary, password on source), rollback of a failed switch | failed switch skips `binary_recover` |
| uninstall/clean refusal | tests/web/test_uninstall_clean_running_route.py | confirmed `POST /action` for a running, owned kiss → refused, source + record kept; stopped → removed | the four running checks off |
| hardware setup | tests/web/test_hardware_setup_route.py | `POST /hardware` → the daemon argv the next start spawns (`--radio`/`--hw` per band); unknown setup or no CSRF → no write | the save skipped |
| TX opt-in + test + callsign | tests/web/test_tx_test_route.py | `POST /action op=test-tx`: unconfirmed → nothing sent; confirmed without a valid callsign → nothing sent; valid → one identified frame per READY band | callsign check off; opt-in skipped |

The backlog's gap list loses those five entries.

## 4. CI

`ci.yml`: a new `guards` job (Python 3.13, the same pinned actions and install step as `test`)
runs the two guard modules as their own named check. Both also run inside `test`.
`docs/maintenance.md`: one sentence per guard. Checks run: `tests/repo/test_workflow_shell.py`
and `actionlint` 1.7.7 on ci.yml.

## Open questions (with recommendation)

- Should `guards` become a required check in the GitHub ruleset? That is a repository setting,
  not in the tree. Recommendation: yes, beside `test (3.x)`.
- The static scan's stale-but-harmless fakes (e.g. the `start` fake above) never receive a call
  the real function refuses, so the guard does not flag them. Recommendation: leave them; the
  guard fails the first time one would hide a refused call.
- `lhpc/core/updater_units.py`'s banner names `tests/test_updater_units.py`. That was already
  stale, and it is lhpc/, out of this batch's files. Recommendation: fix it with the next lhpc
  change to that file.


## The commits

```
becbf7b T5: plan — the fake-signature and frozen-unit guards, five contract tests
a464d58 T5: a fake of an LHPC function takes only the calls the real one takes
a2f599a T5: the unit templates are frozen — all ten pinned in tests/data, checked from tests/repo
e1d0818 T5: contract — the firewall the console configures is the ruleset the apply loads
2b0fa09 T5: contract — the console's binary/source channel switch: receipt, auth, rollback
aa7e0a0 T5: contract — uninstall and clean from the console refuse while the stack runs
9cf4c7f T5: contract — the board chosen on the console is the board the daemon launches for
5edeaab T5: contract — a console TX test needs the opt-in and a valid callsign
e065484 T5: CI names the two guards in their own job
```

## The code report

# Code report T5 — CI guards and the named missing contract tests

Branch `cons/S2`, on top of the T2 series. Tests/CI only: `git diff
f257831..HEAD -- lhpc testlab | wc -l` → `0`. No mutation testing (handler's
clarification: the completion criterion's "and the mutation job" is stale).

## Commits

| sha | subject | files | red-before |
|---|---|---|---|
| becbf7b | T5: plan | plans/PLAN-T5.md | — |
| a464d58 | fakes take only the real function's calls | tests/conftest.py, tests/repo/test_fake_signatures.py, tests/core/test_secrets_backup.py | yes: guard off → 5 failed, 2 passed |
| a2f599a | unit templates frozen | tests/repo/test_unit_templates_frozen.py, tests/data/unit-templates.sha256, tests/host/test_updater_units.py, docs/backlog.md, CHANGELOG.md | yes: one comment byte in `_WEB` → 1 failed; `OnUnitActiveSec=61` in the firewall timer → 1 failed |
| e1d0818 | contract: firewall configure route | tests/web/test_firewall_configure_route.py | yes: route drops `allow_*` → 3 failed, 2 passed |
| 2b0fa09 | contract: channel switch route | tests/install/test_channel_switch_route.py | yes: failed switch skips `binary_recover` → 1 failed, 1 passed |
| aa7e0a0 | contract: uninstall/clean refused while running | tests/web/test_uninstall_clean_running_route.py | yes: the four running checks off → 2 failed, 2 passed |
| 9cf4c7f | contract: hardware setup route | tests/web/test_hardware_setup_route.py | yes: save skipped → 5 failed |
| 5edeaab | contract: TX opt-in + callsign | tests/web/test_tx_test_route.py, docs/backlog.md | yes: callsign check off → 4 failed, 2 passed; opt-in skipped → 1 failed, 5 passed |
| e065484 | CI `guards` job | .github/workflows/ci.yml, docs/maintenance.md | n/a (CI wiring); `actionlint` 1.7.7 clean, `tests/repo/test_workflow_shell.py` 10 passed |

Red-before method. One throwaway edit per behaviour, then `python -m pytest -q -p no:cacheprovider
<module>`, then `git checkout -- <file>` and a purge of `lhpc/**/__pycache__`.

The purge matters. A same-size, same-second mutation (`60`→`61`) once left a stale `.pyc` that made
the restored source render the mutated unit. That cost one confusing host/repo run, which was
re-run clean.

For the guard, the line installing it was removed from a copy of `tests/conftest.py`, and the copy
was restored afterwards. Green: the T5 modules plus the workflow lint → `40 passed`.

## Numbers, measured

| figure | command | output |
|---|---|---|
| per module | `python -m pytest -q -p no:cacheprovider <module>` (PYTHONPATH = the tree) | fake signatures 7; frozen units 1; firewall route 5; channel switch 2; uninstall/clean 4; hardware 5; TX 6 (all passed) |
| T5 modules + workflow lint | the same over the seven modules and `tests/repo/test_workflow_shell.py` | `40 passed` |
| module lines | `wc -l` | fake signatures 85, frozen units 58; contract modules 73, 110, 71, 71, 78 |
| the guard | `git diff --numstat <T5 plan> HEAD -- tests/conftest.py` | `86 0` |
| unit freeze | `git show --numstat --format= <the unit-freeze commit>` | `tests/host/test_updater_units.py` +1 −51 (net −50); `tests/repo/test_unit_templates_frozen.py` +58; `tests/data/unit-templates.sha256` +10 |
| pinned units | `grep -c . tests/data/unit-templates.sha256` | `10` |
| `monkeypatch.setattr` sites | `git grep -h 'monkeypatch.setattr' f257831 -- tests \| wc -l` (lines, at the base) | `1740`; at HEAD `1775`. The plan's "1,725" and its per-target split were not reproduced by a recorded command |
| production untouched | `git diff f257831..HEAD -- lhpc testlab \| wc -l` | `0` |

## The 6-point block

1. **CONTRACTS** (read, none changed).
   - `pytest.MonkeyPatch.setattr(target, name, value=…, raising=True)` and the dotted-string form.
     The wrapper keeps both, and `raising`.
   - The guard wraps only a plain function or lambda put over an `lhpc.*` function: a module
     function, or a method put on its class (bound with self) or on one instance (without self).
     Static/class methods, properties, classes and callable objects are installed unchanged.
   - The routes and their form fields: `/firewall/configure` (`mode`, `allow_<route id>`,
     `ssh_ports`, `ap_*`, `recommended`; app.py:2255); `/action` (`op`, `target`, `source`,
     `confirmed`, `confirm_text`; app.py:1560); `/hardware` (`hardware`; app.py:1933).
   - The candidate heredoc `LHPC_EOF_CANDIDATE` in the apply script (firewall.py:475/480).
   - `Lifecycle.spawn_job(name, argv, cwd, env=None)` (lifecycle.py:1456),
     `Lifecycle._real_spawn(argv, log_path, cwd=None, env=None)` (:214) and
     `Lifecycle.run_daemon_tx_test(band, payload)` (:1538): every fake carries that real signature.
   - The TX payload `LHPC TX TEST DE <call>` (service_lifecycle_ops.py:4275).
   - `updater_units.render(kind, root, checkout, venv)` (:400) and the three firewall renderers
     (firewall.py:411/439/458).
2. **INVARIANTS + TESTS.**
   - P0.5 uninstall protection → the uninstall/clean route module.
   - RF-TX-opt-in (no transmit without the confirmation, a READY band and a valid identity) → the
     TX route module.
   - Firewall fail-closed (only allowed routes accepted, refused input changes nothing) → the
     firewall route module.
   - The binary channel (receipt, auth by channel, restore from disk with no network) → the
     channel switch module.
   - Boot restore needs canonical unit bytes → the frozen-unit test.
   - No production change, so nothing else moves. Every test directory was re-run with the
     guard: no fake anywhere is called in a way its real function refuses.
3. **KNOWN FAILURE CLASSES.**
   - Fakes with the real signature: this batch makes that suite-wide (the guard), and every fake
     these modules add has the real signature, so it passes the guard.
   - EIO/EACCES probes: not in scope here (T2).
   - KeyboardInterrupt through cleanup: T2.
   - Same decision across CLI / web / job: the channel switch runs the exact CLI command the web
     job carries; the TX and uninstall cases use the same service decisions the CLI calls.
   - Stacked conflicts: T5 touches `tests/conftest.py`, which every other batch's tests load. The
     guard can fail another batch's fake only if that fake is called in a way the real function
     refuses, which is the point. One shared file: CHANGELOG.md (separate line).
4. **TEST RULES.**
   - Red-before for every module (table).
   - Decision-bearing values compared whole: rules by exact line, launched `(radio, hw)` tuples,
     payloads by equality, flash categories by list equality. The only substring checks are a
     refusal's flash containing "running", next to the exact category and the kept source and
     record, and the guard's error naming the function.
   - Every return checked: status codes, `cli main()`'s exit code, `.ok`.
   - No network: stubbed index/download/clone, FakeSystem.
5. **WHOLE TEST DIRECTORIES** (HEAD, foreground, purged caches; `python -m pytest -q
   -p no:cacheprovider <dirs>`):
   - `tests/core` → `2092 passed, 6 skipped`.
   - `tests/web tests/cli` → `1136 passed`.
   - `tests/stacks tests/repo` → `1691 passed, 5 skipped`.
   - `tests/install tests/host` → `99 failed, 1996 passed, 16 skipped, 30 xfailed`.

   The 99 are exactly the root-container failures that also fail on the base `origin/integration/0.12.0` (f257831):
   92 in `test_bootstrap_deps.py`; `test_binary_install.py::test_extract_rejects_hostile_archive`
   ×3; `test_binary_channel.py::test_doctor_is_quiet_for_a_healthy_binary_install`; and three in
   host (`test_receipt_reader_rejects_nonroot_symlink_and_unsafe`,
   `test_rule_helpers_and_single_install_site`,
   `test_dependency_entry_probe_and_bootstrap_exclusion`). The bootstrap script refuses root, and
   the receipt check asserts non-root ownership.

   `ruff check lhpc testlab` → All checks passed; `ruff check tests --select F,E9` → All checks
   passed. Workflow lint: `actionlint` 1.7.7 clean, `tests/repo/test_workflow_shell.py` 10 passed.
   No signature changed.
6. **ADVERSARIAL SELF-REVIEW.** Found and fixed:
   - the guard first resolved an instance target through the class (bound `self` twice) → it
     uses the bound method;
   - it imported `_pytest` internals → its own dotted-path resolver;
   - a guard `TypeError` could be swallowed by production's broad `except Exception` → violations
     are also recorded and fail the test at teardown (a case proves it);
   - a frame-reading test broke on the wrapper frame → it skips the wrapper;
   - the hardware fake first raised, which aborted the second band → it launches the suite's
     harmless stand-in process;
   - the firewall rules carry the route id as an nft comment → pinned whole.

   The docs (maintenance: one sentence per guard; backlog: the holding-line sentence and the
   closed gaps) and this report describe exactly the diff.

## Simplicity guardrails

- No production code.
- The guard is two plain functions and two fixtures in `tests/conftest.py` (+86 lines). It
  replaces the brief's static scan, which would have needed import-alias resolution, `svc`-type
  guessing and an allow-list for about 300 unresolvable sites; that is a framework the guardrail
  forbids. The call-time check covers every `monkeypatch.setattr` site with no rewrite (1,740 lines
  at f257831; the plan's "1,725" does not reproduce, see the table).
- The unit freeze moves the existing pin into one data file and one test (net −50 lines in
  tests/host, +58 in tests/repo, +10 data) and adds the three unpinned firewall units.
- Contract modules: 71–110 lines each, no new helper module.

## Deviations (the reviewer should judge)

- Fake signatures are checked at the call, not by parsing the fake's parameter list (see the
  plan). A stale fake parameter that no caller uses is not flagged. Example:
  `tests/core/test_boot_restore.py:581`'s `start(… params, daemon_overrides …)`.
- The unit freeze hashes the `%h` render (= `sha256sum deploy/<unit>`) instead of the old
  fixed-root render. It is the same template bytes, so the old inline hashes are replaced, not
  carried.
- The required failure message says "see docs/architecture.md". That file names the frozen bytes
  only in its package map; the migration itself is in `docs/backlog.md`, which the test docstring
  links.
- `docs/backlog.md` was edited outside T5's listed files: two sentences had become untrue (the
  holding-line test name and the five closed contract gaps).
- `lhpc/core/updater_units.py`'s banner still names `tests/test_updater_units.py`. It was stale
  before this batch, and it is lhpc/; recorded as a fix item.
- The hardware and TX modules observe the spawn and transmit seams (`Lifecycle._real_spawn`,
  `run_daemon_tx_test`). The hardware start runs at service level after the route POST, because a
  web start is a detached job.
- The CI `guards` job duplicates two modules that `test` already runs, so they show as a named
  check. Making it a required check is a repository setting (plan, open question).

## The full diff of the code commits

```diff
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 91aa153..bd53417 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -125,6 +125,35 @@ jobs:
         if: ${{ !cancelled() }}
         run: pip-audit . --strict --progress-spinner=off
 
+  # The two suite guards as their own named check, so a red one reads as what it is: a fake of a
+  # production function called in a way the real one refuses (tests/conftest.py checks every
+  # monkeypatch.setattr), or a changed systemd unit template (frozen until the staged unit
+  # migration exists). Both also run inside `test`; this job only names them.
+  guards:
+    runs-on: ubuntu-latest
+    timeout-minutes: 15
+    steps:
+      - name: Checkout
+        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
+        with:
+          persist-credentials: false
+
+      - name: Set up Python
+        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
+        with:
+          python-version: "3.13"
+          cache: pip
+          cache-dependency-path: pyproject.toml
+
+      - name: Install package and test tooling
+        run: |
+          . "$GITHUB_WORKSPACE/.github/scripts/retry.sh"
+          retry 3 python -m pip install --upgrade pip
+          retry 3 pip install .[dev]
+
+      - name: Fake-signature guard and frozen unit templates
+        run: python -m pytest -q -p no:cacheprovider tests/repo/test_fake_signatures.py tests/repo/test_unit_templates_frozen.py
+
   # Authoritative CROSS-REPOSITORY pin gate. Catches BOTH orphaned-pin classes: a pin that predates a
   # referenced script, AND a once-valid SHA orphaned by force-pushing/amending the source branch (no
   # longer an ancestor of the remote branch tip). Read-only, immutable action SHAs, no persisted creds.
diff --git a/CHANGELOG.md b/CHANGELOG.md
index a2a5aa3..6b454d1 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -2,6 +2,9 @@
 
 ## 0.12.0
 
+- The systemd units LHPC installs are now pinned in the test suite: no release can change one by
+  accident, because a changed unit would make boot restore refuse on every box that already has the old one.
+
 - Every step that writes a journal, receipt or marker the next run recovers from (settings saves, boot restore,
   binary installs, source installs and updates, self-update, the firewall apply) is now tested against a full
   disk, an I/O error and Ctrl-C at that exact step: recovery must leave a clean state and the same command must
diff --git a/docs/backlog.md b/docs/backlog.md
index 621c4d5..0b787e4 100644
--- a/docs/backlog.md
+++ b/docs/backlog.md
@@ -29,7 +29,7 @@ power cycle with nothing running. The update path cannot repair this:
 Only detection exists: verification runs out of process against the new checkout, and a failure
 makes the update visibly partial.
 
-**Holding the line:** `tests/host/test_updater_units.py::test_unit_bytes_are_the_frozen_render`.
+**Holding the line:** `tests/repo/test_unit_templates_frozen.py` (hashes in `tests/data/unit-templates.sha256`).
 
 **Workaround for new writable paths:** redirect the state into the runtime root with an
 environment variable instead of granting a HOME path (Sideband:
@@ -81,16 +81,10 @@ tests named next to each guarantee are the only evidence.
 Promises whose widest-seam case is missing — the next test-quality pass (`tests/README.md` has
 the tiers):
 
-1. no `POST /firewall/configure` route test observing an applied effect (apply/fail-closed is
-   proven only at the `ActionResult` seam);
-2. `tests/core/test_boot_restore.py` is `needs_session` at module scope — no isolation-safe case
+1. `tests/core/test_boot_restore.py` is `needs_session` at module scope — no isolation-safe case
    (split out the pure route-toggle tests);
-3. no route-level binary-channel SWITCH test (only the install confirmation's channel selection);
-4. no `/action` POST test for `op=uninstall`/`op=clean` refuse-while-running;
-5. no direct `/hardware` setup POST test (only `/hardware/probe`);
-6. no route-table gate in lhpc's own suite; the coverage matrix is in [testlab](testlab.md) and
-   runs in that package's CI lane;
-7. no single composite "TX opt-in + tests + callsign" gate test (covered by several separate ones).
+2. no route-table gate in lhpc's own suite; the coverage matrix is in [testlab](testlab.md) and
+   runs in that package's CI lane.
 
 ## Safety invariant IDs
 
diff --git a/docs/maintenance.md b/docs/maintenance.md
index fd6b52a..b058f25 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -27,6 +27,12 @@ What CI enforces, branches and releases, the pin-bump recipe, and the gotchas on
     coverage in the log, a `coverage.xml` artifact per Python version and the total in the job
     summary
   - `bandit -q -r lhpc -lll` (high severity) and `pip-audit . --strict`, also after a test failure
+- `guards`, on Python 3.13, names the two suite guards that also run inside `test`:
+  - a fake that `monkeypatch.setattr` puts over an LHPC function or method is checked at every call
+    against the real signature (`tests/conftest.py`, `tests/repo/test_fake_signatures.py`);
+  - the ten systemd unit templates render to the hashes in `tests/data/unit-templates.sha256`, frozen
+    until the [staged unit migration](backlog.md#two-stage-unit-template-migration) exists
+    (`tests/repo/test_unit_templates_frozen.py`).
 - `pin-validation`: every pinned source has a rule in the release bot's policy, and every pin is
   an ancestor of its live branch with its referenced scripts present
 - `meshcore-host`: LHPC's own tests for `lhpc/data/meshcore_host` (not collected by `pytest -q`)
diff --git a/tests/conftest.py b/tests/conftest.py
index ee3d9cc..7002a49 100644
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -46,6 +46,92 @@ from lhpc.core.services import ControllerService
 from lhpc.core.lifecycle import Lifecycle
 
 
+def _keeping_the_real_signature(target, name, value):
+    """A fake of an LHPC function or method — a lambda or def put over it with `monkeypatch.setattr`
+    — accepts only the calls the REAL one accepts: each call is bound to the real signature first,
+    so a call the production function would refuse (a renamed, removed or added parameter) fails
+    the test loudly instead of being swallowed by a permissive fake. Anything else (a class, a
+    callable object, a value, a non-LHPC target) is installed unchanged."""
+    import functools
+    import inspect
+    if not inspect.isfunction(value):
+        return value
+    if inspect.ismodule(target) or isinstance(target, type):
+        real = inspect.getattr_static(target, name, None)   # a module function, or a method on
+        if not inspect.isfunction(real):                    # its class (bound with `self`)
+            return value
+    else:
+        real = getattr(target, name, None)          # a method put on one instance: no `self`
+        if not inspect.ismethod(real):
+            return value
+    if not (getattr(real, "__module__", "") or "").startswith("lhpc."):
+        return value
+    signature = inspect.signature(real)
+    where = f"{real.__module__}.{real.__qualname__}"
+
+    @functools.wraps(value)
+    def fake(*args, **kwargs):
+        try:
+            signature.bind(*args, **kwargs)
+        except TypeError as exc:
+            why = f"the fake of {where}{signature} was called in a way the real function refuses: {exc}"
+            _SIGNATURE_VIOLATIONS.append(why)       # failed at teardown even if production swallows it
+            raise TypeError(why) from None
+        return value(*args, **kwargs)
+    return fake
+
+
+_UNSET = object()
+
+
+def _owner_of(dotted):
+    """`"pkg.mod.Cls.attr"` -> (the object holding `attr`, "attr"), as monkeypatch resolves it."""
+    import importlib
+    path, attr = dotted.rsplit(".", 1)
+    parts = path.split(".")
+    for i in range(len(parts), 0, -1):
+        try:
+            owner = importlib.import_module(".".join(parts[:i]))
+        except ImportError:
+            continue
+        for part in parts[i:]:
+            owner = getattr(owner, part)
+        return owner, attr
+    raise ImportError(dotted)
+
+
+def _setattr(self, target, name, value=_UNSET, raising=True):
+    if value is _UNSET:                             # the string form: setattr("pkg.mod.fn", value)
+        owner, attr = _owner_of(target)
+        return _real_setattr(self, owner, attr, _keeping_the_real_signature(owner, attr, name),
+                             raising=raising)
+    if isinstance(name, str):
+        value = _keeping_the_real_signature(target, name, value)
+    return _real_setattr(self, target, name, value, raising=raising)
+
+
+# Every `monkeypatch.setattr` in this suite goes through the check (tests/repo/test_fake_signatures.py).
+_real_setattr = pytest.MonkeyPatch.setattr
+pytest.MonkeyPatch.setattr = _setattr
+_SIGNATURE_VIOLATIONS: list[str] = []
+
+
+@pytest.fixture(autouse=True)
+def _fakes_called_as_the_real_function():
+    """A refused call fails the test even when the production code under test catches the
+    `TypeError` (a broad `except Exception` turning it into an ordinary failed result)."""
+    _SIGNATURE_VIOLATIONS.clear()
+    yield
+    assert not _SIGNATURE_VIOLATIONS, _SIGNATURE_VIOLATIONS[0]
+
+
+@pytest.fixture
+def signature_violations():
+    """The refused calls recorded so far, for the test that provokes one on purpose (it clears
+    them once it has checked the refusal)."""
+    return _SIGNATURE_VIOLATIONS
+
+
 @pytest.fixture
 def short_tmp_path():
     """A temporary directory with a SHORT path, removed after the test, for a test that binds an
diff --git a/tests/core/test_secrets_backup.py b/tests/core/test_secrets_backup.py
index 13a90f9..ff9b86b 100644
--- a/tests/core/test_secrets_backup.py
+++ b/tests/core/test_secrets_backup.py
@@ -895,7 +895,9 @@ def test_a_symlink_swapped_in_after_the_last_check_cannot_redirect_the_restore(t
 
     def swap_after_the_check(root, target):
         real(root, target)
-        if inspect.stack()[1].function == "apply" and not outside.exists():
+        # the caller, past the signature check that wraps every fake (tests/conftest.py)
+        caller = next(f.function for f in inspect.stack()[1:] if f.function != "fake")
+        if caller == "apply" and not outside.exists():
             (b / "config/tls").rename(outside)               # the real folder goes outside…
             (b / "config/tls").symlink_to(outside)           # …and a link takes its place
     monkeypatch.setattr(sb, "check_ancestors", swap_after_the_check)
diff --git a/tests/data/unit-templates.sha256 b/tests/data/unit-templates.sha256
new file mode 100644
index 0000000..1ab684a
--- /dev/null
+++ b/tests/data/unit-templates.sha256
@@ -0,0 +1,10 @@
+67ee9e95fa5f14b3f1e0c7c2ab8a76671f3b5521963438f5980c08110eab267e  lhpc-web.service
+c10ab4d516da4b38824c9af955ba2cd012d0de4d6d776fcd297bade33108813e  lhpc-selfupdate.service
+b0adcfda127b1c2dd37b1b85b3a020ba862db105f02ec22479177c0de8770b2d  lhpc-selfupdate.path
+101d941f78e321c87340ed5dba74c93a910f642a662dc5de709241a8367fcf61  lhpc-nginx.service
+047a1056eb4994ea7f23219a69792c955fdc9c62d377a13e419a0df392394fc6  lhpc-nginx-restart.service
+23deeea7fec3725a8beb72c6da1105ad2c0cae48de8be6fbcfb088785aa218b2  lhpc-nginx-restart.path
+ecb2dde6c6f48476acc0d6f216fb30d3d9ef8087a530607b1e876448382c3652  lhpc-boot-restore.service
+c319e238350b56f5dc5ce23ef14e7d6e2f599b9d4c2e87edf3e2c1a4633ee769  lhpc-firewall.service
+9b0928ac6b1debbf792a140dd5886201d663f80cc6ba8114843ab4bf3dee9d0b  lhpc-firewall-check.service
+c4d477cb51b813e18a025612cc469e340320afb9ba5c7536701c5abb565ee818  lhpc-firewall-check.timer
diff --git a/tests/host/test_updater_units.py b/tests/host/test_updater_units.py
index 344a9a2..4834877 100644
--- a/tests/host/test_updater_units.py
+++ b/tests/host/test_updater_units.py
@@ -98,7 +98,7 @@ def test_web_and_helper_carry_the_bus_block_and_sandbox():
     # Sideband is NOT granted %h/.kivy: KIVY_HOME redirects its state into
     # {root}/state/sideband/kivy, which is already writable. Granting it here would
     # change the unit bytes and strand every installed box — see
-    # test_unit_bytes_are_the_frozen_render.
+    # tests/repo/test_unit_templates_frozen.py.
     assert f"ReadWritePaths={ROOT} -%h/.meshcore_nm /tmp" in web
     assert f"ReadWritePaths={ROOT} /tmp" in helper
     assert "%h/.meshcore_nm" not in helper and "%h/.kivy" not in helper
@@ -454,53 +454,3 @@ def test_verify_and_integration_cover_the_restart_units(tmp_path):
     (dropin / "evil.conf").write_text("[Service]\nExecStart=\n")
     assert U.verify(ud, U.RESTART_UNIT, ROOT, CO, VENV) == U.OVERRIDDEN
     assert U.integration(ud, ROOT)["status"] == "overridden"
-
-
-# The frozen rendered unit bytes, pinned. See the frozen-template banner in updater_units.py.
-_UNIT_BYTES_0_1_6 = {
-    "lhpc-web.service":
-        "1dcfc443666fd2e9780e8c32822aec3a1b3b924088c19d4c056c6e1bff5b7963",
-    "lhpc-selfupdate.service":
-        "aff7551b15392d7e665882d2a0a1d3a1268164d99918111e02d40dfb634f1bd2",
-    "lhpc-selfupdate.path":
-        "3ea1a78764b95349cc5ff9b2005feb30278c91d7b61798d0bc0696bae85e891f",
-    "lhpc-nginx.service":
-        "2033890f7a3b062f15577b714eea83f51b679c94b83e36c84e9007ca9445949a",
-    "lhpc-nginx-restart.service":
-        "d11077656397af64409854cb7a9928d91d2a72e64d7520025100ab139d98aac1",
-    "lhpc-nginx-restart.path":
-        "1f7b523f2e863e05cd1dda74dd59b1ce9812d7ebfdf77584216eec310302a39a",
-    "lhpc-boot-restore.service":
-        "5c667fb424138a61513cb1df3f6cabd7137a33da8d1e14b6b6009ae249de6780",
-}
-
-
-def test_unit_bytes_are_the_frozen_render():
-    """The managed units must render EXACTLY the frozen bytes.
-
-    `verify()` compares byte-for-byte, so any change — including to a COMMENT inside a
-    template — makes every already-installed unit non-canonical. Boot restore then
-    refuses and the box comes back from a power cycle with nothing running. No update
-    path repairs this: the in-process repair renders pre-update templates, and the
-    systemd-helper route cannot write units at all (ProtectHome=read-only).
-
-    Caught exactly that during an audit fix: reverting an added
-    `-%h/.kivy` write path restored the DIRECTIVE, but the reworded comment beside it
-    still changed lhpc-web.service's bytes.
-
-    If you are here because this failed: do NOT just re-pin the hash. Either keep the
-    bytes identical (redirect state into {root} with an env var, as KIVY_HOME does), or
-    build the two-stage migration in docs/backlog.md first.
-    """
-    import hashlib
-
-    assert set(_UNIT_BYTES_0_1_6) == set(U.ALL_UNITS), \
-        "a managed unit was added or removed — that alone changes the installed set"
-    drifted = {}
-    for kind in U.ALL_UNITS:
-        got = hashlib.sha256(U.render(kind, ROOT, CO, VENV).encode()).hexdigest()
-        if got != _UNIT_BYTES_0_1_6[kind]:
-            drifted[kind] = got
-    assert not drifted, (
-        "unit template bytes changed -> boot restore will refuse on every "
-        f"installed box: {sorted(drifted)}")
diff --git a/tests/install/test_channel_switch_route.py b/tests/install/test_channel_switch_route.py
new file mode 100644
index 0000000..5161e35
--- /dev/null
+++ b/tests/install/test_channel_switch_route.py
@@ -0,0 +1,110 @@
+"""Switching MeshCom between the binary and the source channel from the console.
+
+The widest seam: `POST /action op=install source=binary|pinned` (confirmed) starts a detached
+install job; the job is the `lhpc install … --source <channel>` command the route put on its
+argv. Each case captures that argv at the job spawn and runs exactly that command in-process
+(network collaborators stubbed: the index, the download, the clone), then observes what the switch
+promises: on the binary channel a valid receipt and open auth (the published firmware has no mesh
+password); switching back to source retires the receipt and puts the mesh password back; a switch
+whose adoption fails restores the binary install from disk — receipt, files and open auth — and
+leaves no transaction open.
+"""
+from __future__ import annotations
+
+import os
+
+import pytest
+
+from lhpc.adapters.cli import main as cli_main
+from lhpc.core import binary_install as bi
+from lhpc.core.install import Installer
+from lhpc.core.lifecycle import Lifecycle
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+
+pytestmark = pytest.mark.contract
+
+
+@pytest.fixture
+def box(tmp_path, monkeypatch, web, stub_pipeline):
+    """MeshCom with its mesh password set, the console over it, and the binary pipeline local:
+    returns (client, svc)."""
+    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    assert svc.hmac_set_secret("meshcom", "enable").ok
+    spec = svc.binary_spec("meshcom")
+    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})
+    stub_pipeline(svc, download=lambda entry, path: None)
+
+    def stage(tar, stage_dir, roots):
+        for rel in files:
+            os.makedirs(os.path.dirname(os.path.join(stage_dir, rel)), exist_ok=True)
+            with open(os.path.join(stage_dir, rel), "w") as fh:
+                fh.write("artifact")
+        return files
+    monkeypatch.setattr(bi, "validate_and_extract", stage)
+    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
+    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])
+    monkeypatch.setattr(ControllerService, "install_dep_gate",
+                        lambda self, target: {"block": [], "warn": []})
+    monkeypatch.setattr(cli_main, "_print_install_dep_gate", lambda svc, stack, check=False: False)
+    monkeypatch.setattr(cli_main, "ControllerService", lambda: svc)
+    return web(service_factory=lambda: svc), svc
+
+
+def _switch(client, csrf, monkeypatch, channel):
+    """POST the confirmed install on `channel`; run the job command the route spawned."""
+    jobs = []
+
+    def spawn_job(self, name, argv, cwd, env=None):
+        jobs.append(list(argv))
+        return None, None                           # nothing detached: run in-process below
+    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
+    r = client.post("/action", data={"_csrf": csrf(client), "op": "install", "target": "meshcom",
+                                     "source": channel, "confirmed": "yes"})
+    assert r.status_code in (302, 303)
+    (argv,) = jobs
+    command = argv[argv.index("install"):]
+    command = command[:command.index("--web-result")] if "--web-result" in command else command
+    assert command[:2] == ["install", "meshcom"] and \
+        command[command.index("--source") + 1] == channel
+    return cli_main.main(command)
+
+
+def _state(svc):
+    """(receipt state, the bridge's mesh password setting, its listener's auth)."""
+    svc.invalidate_snapshot()
+    comp = svc._hmac_component("meshcom")
+    st = svc.stack("meshcom")
+    auth = next(svc._fw_resolve_scope(st, c, ep)["auth"] for c in st.components
+                for ep in c.endpoints if ep.kind == "tcp" and ep.role == "listener" and ep.firewall)
+    return (svc.binary_receipt_state("meshcom")[0],
+            bool(svc._resolved_param_value("meshcom", "run", comp.id, "password_file")), auth)
+
+
+def test_binary_then_back_to_source(box, csrf, monkeypatch, stub_adopt):
+    client, svc = box
+    monkeypatch.setattr(Installer, "adopt_source",
+                        lambda self, comp, **k: type("A", (), {"status": "done", "detail": ""})())
+    assert _switch(client, csrf, monkeypatch, "binary") == 0
+    assert _state(svc) == ("valid", False, "none")
+    stub_adopt(svc)
+    assert _switch(client, csrf, monkeypatch, "pinned") == 0
+    assert _state(svc) == ("absent", True, "password")
+    assert bi.read_journal(svc._paths)[1] == "absent"
+
+
+def test_a_failed_switch_to_source_restores_the_binary_install(box, csrf, monkeypatch, stub_adopt,
+                                                                tmp_path):
+    client, svc = box
+    monkeypatch.setattr(Installer, "adopt_source",
+                        lambda self, comp, **k: type("A", (), {"status": "done", "detail": ""})())
+    assert _switch(client, csrf, monkeypatch, "binary") == 0
+    proof = tmp_path / svc.binary_spec("meshcom").proof_paths[0]
+    stub_adopt(svc, fail_paths=("src/MeshCom-Firmware",))
+    assert _switch(client, csrf, monkeypatch, "pinned") != 0
+    assert _state(svc) == ("valid", False, "none")
+    assert proof.read_text() == "artifact"
+    assert bi.read_journal(svc._paths)[1] == "absent"
+    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []
diff --git a/tests/repo/test_fake_signatures.py b/tests/repo/test_fake_signatures.py
new file mode 100644
index 0000000..c3aef01
--- /dev/null
+++ b/tests/repo/test_fake_signatures.py
@@ -0,0 +1,85 @@
+"""A fake of a production function keeps the real signature.
+
+Every `monkeypatch.setattr` of this suite that puts a lambda or def over an LHPC function or method
+is checked at each CALL against the real signature (`tests/conftest.py`). So when production
+changes a signature — a parameter renamed, removed or added — a caller that still uses the old
+shape fails the test that fakes it, instead of a permissive fake (`lambda *a, **k: …`) swallowing
+the call that the real function would refuse. Checking the call, not the fake's own parameter
+list, covers every fake whatever its target expression (`svc`, `type(svc)`, a module alias, a
+dotted string) without rewriting them. A refused call is also recorded and fails the test at
+teardown, so production code that catches the `TypeError` cannot hide it.
+"""
+from __future__ import annotations
+
+import pytest
+
+from lhpc.core import binary_install as bi
+from lhpc.core import selfupdate
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+
+
+def test_a_permissive_fake_of_a_module_function_refuses_a_call_the_real_one_refuses(
+        monkeypatch, signature_violations):
+    monkeypatch.setattr(selfupdate, "check_upstream", lambda *a, **k: {"ok": True})
+    assert selfupdate.check_upstream(None, "main") == {"ok": True}        # the real shape passes
+    with pytest.raises(TypeError, match=r"lhpc\.core\.selfupdate\.check_upstream.*refuses"):
+        selfupdate.check_upstream(None, branch="main", remote="origin")  # `remote` does not exist
+    assert len(signature_violations) == 1
+    signature_violations.clear()
+
+
+def test_a_refused_call_that_production_swallows_still_fails_the_test(monkeypatch,
+                                                                      signature_violations):
+    monkeypatch.setattr(selfupdate, "check_upstream", lambda *a, **k: {"ok": True})
+    try:
+        selfupdate.check_upstream(None, remote="origin")
+    except Exception:                                           # what a broad handler does
+        pass
+    assert signature_violations and "check_upstream" in signature_violations[0]
+    signature_violations.clear()
+
+
+def test_a_fake_method_on_the_class_is_bound_with_self(monkeypatch, tmp_path, signature_violations):
+    monkeypatch.setattr(ControllerService, "running_band", lambda self, *a, **k: "433")
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    assert svc.running_band("kiss", default="") == "433"
+    with pytest.raises(TypeError, match="running_band"):
+        svc.running_band("kiss", band="868")                    # the real one takes `default`
+    signature_violations.clear()
+
+
+def test_a_fake_method_on_one_instance_has_no_self(monkeypatch, tmp_path, signature_violations):
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    monkeypatch.setattr(svc, "running_band", lambda *a, **k: "868")
+    assert svc.running_band("kiss") == "868"
+    with pytest.raises(TypeError, match="running_band"):
+        svc.running_band()                                      # `stack_id` is required
+    signature_violations.clear()
+
+
+def test_the_dotted_string_form_is_checked_too(monkeypatch, signature_violations):
+    monkeypatch.setattr("lhpc.core.binary_install.require_zstd", lambda *a, **k: None)
+    assert bi.require_zstd() is None
+    with pytest.raises(TypeError, match="require_zstd"):
+        bi.require_zstd("zstd")                                 # the real one takes nothing
+    signature_violations.clear()
+
+
+def test_a_fake_that_lacks_a_real_parameter_fails_on_the_real_call(monkeypatch, tmp_path):
+    """The other direction: a fake written before a parameter was added cannot take the call."""
+    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid: "433")
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    with pytest.raises(TypeError):
+        svc.running_band("kiss", "")
+
+
+def test_a_value_or_a_non_lhpc_target_is_installed_unchanged(monkeypatch):
+    import os
+    sentinel = object()
+    monkeypatch.setattr(selfupdate, "_LOCAL_TIMEOUT", sentinel)
+    assert selfupdate._LOCAL_TIMEOUT is sentinel
+    fake = lambda *a, **k: 0                                    # noqa: E731
+    monkeypatch.setattr(os, "getpid", fake)
+    assert os.getpid is fake
diff --git a/tests/repo/test_unit_templates_frozen.py b/tests/repo/test_unit_templates_frozen.py
new file mode 100644
index 0000000..918bf77
--- /dev/null
+++ b/tests/repo/test_unit_templates_frozen.py
@@ -0,0 +1,58 @@
+"""The systemd unit templates LHPC installs are frozen: their rendered bytes match the checked-in
+hashes in `tests/data/unit-templates.sha256`.
+
+The seven user units (`updater_units.ALL_UNITS`) are verified byte for byte on the box: a unit
+whose installed bytes differ from the render is not canonical, so boot restore refuses and the box
+comes back from a power cycle with nothing running — and no update path repairs it (the in-process
+repair renders the PRE-update templates; the systemd-helper route cannot write units at all,
+`ProtectHome=read-only`). A changed COMMENT is enough: an audit fix once reverted a directive but
+kept a reworded comment beside it, and `lhpc-web.service`'s bytes still changed. The three firewall
+units are installed by the operator's sudo apply script, which nothing re-runs on an update. So no
+release may change any of them silently: keep the bytes (redirect new state into `{root}` with an
+environment variable, as `KIVY_HOME` does), or change them together with the staged unit
+migration ([backlog](../../docs/backlog.md#two-stage-unit-template-migration)) and update the
+hashes in the same change.
+
+The user units are hashed as rendered for the shipped `deploy/` copies (`%h` root), so the file is
+also `sha256sum deploy/<unit>`; `tests/host/test_updater_units.py` proves those copies are the
+renders.
+"""
+from __future__ import annotations
+
+import hashlib
+
+import repo_paths
+from lhpc.core import firewall as fw
+from lhpc.core import updater_units as U
+
+FROZEN = repo_paths.TESTS / "data" / "unit-templates.sha256"
+MESSAGE = ("unit templates are frozen until the staged unit migration exists (see "
+           "docs/architecture.md); update the hash only together with that migration")
+
+
+def _renders() -> dict[str, str]:
+    root = "%h/loraham-pi-control"
+    units = {kind: U.render(kind, root, f"{root}/src/loraham-pi-control", f"{root}/venv/lhpc")
+             for kind in U.ALL_UNITS}
+    units.update({fw.LOADER_UNIT: fw.render_loader_unit(),
+                  fw.CHECKER_UNIT: fw.render_checker_unit(),
+                  fw.CHECKER_TIMER: fw.render_checker_timer()})
+    return units
+
+
+def _frozen() -> dict[str, str]:
+    pinned = {}
+    for line in FROZEN.read_text().splitlines():
+        digest, name = line.split("  ", 1)
+        pinned[name] = digest
+    return pinned
+
+
+def test_every_unit_template_renders_its_frozen_bytes():
+    frozen = _frozen()
+    rendered = {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
+                for name, text in _renders().items()}
+    assert set(rendered) == set(frozen), f"{MESSAGE} — a unit was added or removed: " \
+        f"{sorted(set(rendered) ^ set(frozen))}"
+    drifted = sorted(name for name in rendered if rendered[name] != frozen[name])
+    assert not drifted, f"{MESSAGE} — changed: {drifted}"
diff --git a/tests/web/test_firewall_configure_route.py b/tests/web/test_firewall_configure_route.py
new file mode 100644
index 0000000..203f885
--- /dev/null
+++ b/tests/web/test_firewall_configure_route.py
@@ -0,0 +1,73 @@
+"""The firewall the console configures is the ruleset the operator's apply loads.
+
+`POST /firewall/configure` never runs a privileged command: its effect is the candidate it embeds
+in `config/files/firewall/firewall-apply.sh`, which the operator runs with sudo and which hands
+that candidate to the root helper. So the applied effect is the helper's ruleset for the candidate
+in the script: a route the form allows is accepted, every other declared route is dropped, the
+recommended preset drops them all, and a refused submission leaves the script as it was.
+"""
+from __future__ import annotations
+
+import json
+import re
+
+import pytest
+
+from lhpc.core import firewall_helper as fh
+
+pytestmark = [pytest.mark.contract, pytest.mark.safety("firewall-fail-closed")]
+
+SCRIPT = "config/files/firewall/firewall-apply.sh"
+SSH = [{"proto": "tcp", "family": "dual", "addr": "*", "port": 22}]
+ACCEPT = {port: f"tcp dport {port} accept" for port in (4403, 9443)}
+DROP = {port: f'tcp dport {port} drop comment "lhpc-deny:meshtastic.tcp-{port}"'
+        for port in (4403, 9443)}
+
+
+def _configure(client, csrf, **form):
+    r = client.post("/firewall/configure", data={"_csrf": csrf(client), **form})
+    assert r.status_code in (302, 303)
+
+
+def _applied(tmp_path) -> list[str]:
+    """The rules the root helper loads for the candidate in the apply script, for the two
+    meshtastic routes (TCP 4403 and 9443) the default manifest declares."""
+    text = (tmp_path / SCRIPT).read_text()
+    candidate = json.loads(re.search(r"<<'LHPC_EOF_CANDIDATE'\n(.*?)\nLHPC_EOF_CANDIDATE\n",
+                                     text, re.S).group(1))
+    assert fh.validate_candidate(candidate) == []
+    nft = fh.render_nft_text(fh.resolve_model(candidate, ownership_id="x", ssh_scopes=SSH))
+    return sorted((line.strip() for line in nft.splitlines()
+                   if re.search(r"dport (4403|9443) ", line)), key=lambda r: r.split()[2])
+
+
+@pytest.mark.parametrize("allowed,rules", [
+    ([], [DROP[4403], DROP[9443]]),
+    (["meshtastic.tcp-4403"], [ACCEPT[4403], DROP[9443]]),
+    (["meshtastic.tcp-4403", "meshtastic.tcp-9443"], [ACCEPT[4403], ACCEPT[9443]]),
+])
+def test_an_allowed_route_is_accepted_and_every_other_is_dropped(tmp_path, web, csrf,
+                                                                allowed, rules):
+    client = web()
+    _configure(client, csrf, mode="secure-default", ssh_ports="",
+               **{f"allow_{route}": "on" for route in allowed})
+    assert _applied(tmp_path) == rules
+
+
+def test_the_recommended_preset_drops_every_route(tmp_path, web, csrf):
+    client = web()
+    _configure(client, csrf, mode="secure-default", ssh_ports="",
+               **{"allow_meshtastic.tcp-4403": "on"})
+    _configure(client, csrf, recommended="yes")
+    assert _applied(tmp_path) == [DROP[4403], DROP[9443]]
+
+
+def test_a_refused_submission_leaves_the_applied_ruleset_as_it_was(tmp_path, web, csrf):
+    client = web()
+    _configure(client, csrf, mode="secure-default", ssh_ports="",
+               **{"allow_meshtastic.tcp-4403": "on"})
+    before = (tmp_path / SCRIPT).read_bytes()
+    _configure(client, csrf, mode="secure-default", ssh_ports="", ap_enabled="on",
+               **{"allow_meshtastic.tcp-9443": "on"})        # an AP without interface/CIDR
+    assert (tmp_path / SCRIPT).read_bytes() == before
+    assert _applied(tmp_path) == [ACCEPT[4403], DROP[9443]]
diff --git a/tests/web/test_hardware_setup_route.py b/tests/web/test_hardware_setup_route.py
new file mode 100644
index 0000000..56ecd1a
--- /dev/null
+++ b/tests/web/test_hardware_setup_route.py
@@ -0,0 +1,71 @@
+"""The board chosen on the console is the board the daemon launches for.
+
+`POST /hardware` saves `[radio].hardware`; its effect is what the next daemon start spawns: one
+`loraham_daemon` per band the setup serves, each with that band's `--hw` preset (the SPI board
+wiring), and nothing for a band the setup does not serve. An unknown setup, or a POST without the
+CSRF token, changes nothing.
+"""
+from __future__ import annotations
+
+import pytest
+
+from lhpc.core.lifecycle import Lifecycle
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+from seams import seed_built
+
+pytestmark = [pytest.mark.contract, pytest.mark.needs_session]
+
+
+@pytest.fixture
+def box(tmp_path, web, monkeypatch, real_spawn):
+    """The console over a box whose daemon is built; returns (client, launched) — `launched`
+    lists the (radio, hw) of every daemon a start spawns. The spawn is the seam: it records the
+    argv and launches the suite's harmless stand-in process instead (reaped at session end)."""
+    seed_built(tmp_path, "loraham-daemon/loraham_daemon/loraham_daemon")
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    launched = []
+
+    def spawn(self, argv, log_path, cwd=None, env=None):
+        if "--radio" in argv:
+            launched.append((argv[argv.index("--radio") + 1], argv[argv.index("--hw") + 1]))
+        return real_spawn(argv, log_path, cwd, env)
+    monkeypatch.setattr(Lifecycle, "_real_spawn", spawn)
+    return web(service_factory=lambda: svc), svc, launched
+
+
+def _choose(client, csrf, setup, *, token=True):
+    form = {"hardware": setup}
+    if token:
+        form["_csrf"] = csrf(client)
+    return client.post("/hardware", data=form)
+
+
+def _start(svc, launched):
+    launched.clear()
+    svc.start("daemon", apply=True)     # the stand-in never opens a CONF socket: not verified
+    return sorted(launched)
+
+
+@pytest.mark.parametrize("setup,daemons", [
+    ("waveshare-433", [("433", "waveshare-sx1262")]),
+    ("uputronics-868", [("868", "uputronics-ce1")]),
+    ("uputronics", [("433", "uputronics-ce0"), ("868", "uputronics-ce1")]),
+])
+def test_the_chosen_setup_is_what_the_daemon_launches(box, csrf, setup, daemons):
+    client, svc, launched = box
+    assert _choose(client, csrf, setup).status_code in (302, 303)
+    assert svc.hardware_setup() == setup
+    assert _start(svc, launched) == daemons
+
+
+@pytest.mark.parametrize("token", [True, False], ids=["unknown-setup", "no-csrf"])
+def test_a_refused_choice_changes_nothing(box, csrf, tmp_path, token):
+    client, svc, launched = box
+    assert _choose(client, csrf, "waveshare-433").status_code in (302, 303)
+    before = (tmp_path / "config" / "local.toml").read_bytes()
+    r = _choose(client, csrf, "nosuchboard" if token else "uputronics", token=token)
+    assert r.status_code == (302 if token else 400)
+    assert (tmp_path / "config" / "local.toml").read_bytes() == before
+    assert _start(svc, launched) == [("433", "waveshare-sx1262")]
diff --git a/tests/web/test_tx_test_route.py b/tests/web/test_tx_test_route.py
new file mode 100644
index 0000000..f84e1ba
--- /dev/null
+++ b/tests/web/test_tx_test_route.py
@@ -0,0 +1,78 @@
+"""The console's TX test transmits only after the operator's explicit opt-in, and only under a
+valid station callsign.
+
+The one gate across the three conditions, at the widest seam (`POST /action op=test-tx`): the first
+POST only renders the TX confirmation (the opt-in) and transmits nothing; the confirmed POST is
+refused, with nothing transmitted, while the operator callsign is missing or a placeholder; with a
+valid callsign it sends exactly one frame per READY band, identified with that callsign. The
+transmit itself is the seam (`Lifecycle.run_daemon_tx_test`); nothing reaches a radio.
+"""
+from __future__ import annotations
+
+import pytest
+
+from lhpc.core import validators
+from lhpc.core.config import save_operator_config
+from lhpc.core.lifecycle import Lifecycle, TxTestResult
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+from seams import seed_built
+
+pytestmark = [pytest.mark.contract, pytest.mark.safety("RF-TX-opt-in")]
+
+READY = b"STATUS RADIO=READY TXMODE=MANAGED\n"
+
+
+@pytest.fixture
+def box(tmp_path, web, monkeypatch):
+    """The console over a box whose daemon serves both bands, both READY; returns (client, svc,
+    sent) — `sent` lists every (band, payload) the TX test hands to the radio."""
+    seed_built(tmp_path, "loraham-daemon/loraham_daemon/loraham_daemon")
+    sys_ = FakeSystem(unix_replies={"/tmp/loraconf433.sock": READY,
+                                    "/tmp/loraconf868.sock": READY}).system
+    svc = ControllerService(system=sys_, paths=Paths(runtime_root=tmp_path))
+    sent = []
+
+    def transmit(self, band, payload):
+        sent.append((band, payload))
+        return TxTestResult(ok=True, band=band, txok_before=0, txok_after=1, detail="stub")
+    monkeypatch.setattr(Lifecycle, "run_daemon_tx_test", transmit)
+    return web(service_factory=lambda: svc), svc, sent
+
+
+def _post(client, csrf, *, confirmed):
+    form = {"_csrf": csrf(client), "op": "test-tx", "target": "daemon"}
+    if confirmed:
+        form["confirmed"] = "yes"
+    return client.post("/action", data=form)
+
+
+def test_without_the_confirmation_nothing_is_transmitted(box, csrf, set_call):
+    client, svc, sent = box
+    set_call(svc)
+    r = _post(client, csrf, confirmed=False)
+    assert r.status_code == 200                   # the confirmation page — the opt-in
+    assert sent == []
+
+
+@pytest.mark.parametrize("callsign", ["", *validators._PLACEHOLDER_BASES])
+def test_a_confirmed_test_without_a_valid_callsign_transmits_nothing(box, csrf, callsign):
+    client, svc, sent = box
+    if callsign:
+        save_operator_config(svc._paths, callsign)
+        svc._invalidate_config()
+    r = _post(client, csrf, confirmed=True)
+    assert r.status_code in (302, 303)
+    assert sent == []
+    with client.session_transaction() as sess:
+        assert [cat for cat, _msg in sess.get("_flashes", [])] == ["warn"]
+
+
+def test_a_confirmed_test_with_a_callsign_sends_one_identified_frame_per_band(box, csrf, set_call):
+    client, svc, sent = box
+    set_call(svc)
+    call = svc.config().operator.callsign
+    r = _post(client, csrf, confirmed=True)
+    assert r.status_code in (302, 303)
+    assert sent == [("433", f"LHPC TX TEST DE {call}"), ("868", f"LHPC TX TEST DE {call}")]
diff --git a/tests/web/test_uninstall_clean_running_route.py b/tests/web/test_uninstall_clean_running_route.py
new file mode 100644
index 0000000..bfb2d67
--- /dev/null
+++ b/tests/web/test_uninstall_clean_running_route.py
@@ -0,0 +1,71 @@
+"""Uninstall and clean from the console refuse while the stack runs, and remove nothing.
+
+The widest seam of the P0.5 promise: a confirmed `POST /action` (`op=uninstall`, and `op=clean`
+with the typed stack id) for a running kiss stack whose source is a registered, identity-proven
+LHPC adoption — so the ONLY thing that keeps the checkout is the running refusal. The same POST
+with the stack stopped removes it (the control case), so a refusal that held for another reason
+could not pass here.
+"""
+from __future__ import annotations
+
+import os
+import time
+
+import pytest
+
+from lhpc.core import source_registry
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import CommandResult, FakeSystem
+from lhpc.core.services import ControllerService
+
+pytestmark = [pytest.mark.contract, pytest.mark.safety("P0.5")]
+
+SRC = "src/loraham-kiss-tnc"
+REMOTE = "https://github.com/makrohard/loraham-kiss-tnc.git"
+RUNNING = {555: ["loraham-kiss-tnc"]}
+
+
+def _client(tmp_path, web, cmdlines):
+    """kiss's source checked out, recorded as LHPC's adoption, and answering the identity queries
+    with its canonical remote; `cmdlines` is the host's process table."""
+    (tmp_path / SRC).mkdir(parents=True)
+    assert source_registry.write_record(
+        Paths(runtime_root=tmp_path),
+        source_registry.RegistryRecord(SRC, "", "pinned", "", time.time(), "",
+                                       ("loraham-kiss-tnc", "loraham-kiss-serial")))
+    svc = ControllerService(system=FakeSystem(cmdlines_data=cmdlines).system,
+                            paths=Paths(runtime_root=tmp_path))
+    real_run, dest = svc._system.runner.run, os.path.realpath(tmp_path / SRC)
+
+    def run(argv, timeout, *a, **k):
+        if (list(argv[:2]) == ["git", "-C"] and os.path.realpath(argv[2]) == dest
+                and list(argv[3:]) == ["config", "--get", "remote.origin.url"]):
+            return CommandResult(0, REMOTE + "\n", "")
+        return real_run(argv, timeout, *a, **k)
+    svc._system.runner.run = run
+    return web(service_factory=lambda: svc)
+
+
+def _post(client, csrf, op):
+    form = {"_csrf": csrf(client), "op": op, "target": "kiss", "confirmed": "yes"}
+    if op == "clean":
+        form["confirm_text"] = "kiss"
+    r = client.post("/action", data=form)
+    assert r.status_code in (302, 303)
+    with client.session_transaction() as sess:
+        return sess.get("_flashes", [])
+
+
+@pytest.mark.parametrize("op", ["uninstall", "clean"])
+def test_a_running_stack_is_refused_and_keeps_its_source(tmp_path, web, csrf, op):
+    flashes = _post(_client(tmp_path, web, RUNNING), csrf, op)
+    assert (tmp_path / SRC).is_dir()
+    assert source_registry.read_record(Paths(runtime_root=tmp_path), SRC) is not None
+    assert [cat for cat, _msg in flashes] == ["warn"]
+    assert "running" in flashes[0][1].lower()
+
+
+@pytest.mark.parametrize("op", ["uninstall", "clean"])
+def test_the_same_post_for_a_stopped_stack_removes_the_source(tmp_path, web, csrf, op):
+    _post(_client(tmp_path, web, {}), csrf, op)
+    assert not (tmp_path / SRC).exists()
```
