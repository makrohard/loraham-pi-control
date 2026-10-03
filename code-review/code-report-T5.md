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
