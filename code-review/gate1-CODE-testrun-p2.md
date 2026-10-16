# Gate 1: testrun-p2 v2 (a check over a computed list first asserts the list is not empty)

**Request.** For the record (no new round: your round 11 GREEN WITH NOTES note is answered in the packet, and the second reader's two guards are tightened); change table at the top of the report. If you read it: does each changed guard now assert the set its loop really checks (the loop's skips applied as filters first); is the `len(U.ALL_UNITS) == 7` contract shown; is any claim stronger than the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## The plan, §3 P2 and §4 (PLAN-testrun v3)

> | P2 vacuous loops | the loop list minus `host/test_firewall.py` (22 tests, 20 files) + the `test_identity.py` htmlq fix | one guard per test before the loop: `assert <iterable>` or `len(...) == N` where N is the contract | each guard fails with its iterable patched empty (one per file shown) |
> ## 4. Per packet (brief v2.1)
> Test-suite changes, not fixes of behaviour: no parent-red; each states "test-suite change" and shows its negative
> run. Before the push: `tools/check_houserules.py origin/dev..HEAD`, the touched test directories + `tests/repo`,
> `ruff check lhpc testlab`, `ruff check tests --select F,E9`. Report + gate-1 packet per packet with the scan
> before/after; lands on dev only on the handler's line.

## The contract line for `len(U.ALL_UNITS) == 7` (`docs/architecture.md:59`)

```text
    updater_units.py     # the seven canonical systemd units (frozen bytes) + verification
```

## Commit (full message, `git log -1 --format=%B`)

### c28185b6 testrun-p2: a check over a computed list first asserts the list is not empty

```text
testrun-p2: a check over a computed list first asserts the list is not empty

The test-run plan's scan lists 25 tests whose every assert sits in a loop
over a computed iterable (tests/README.md rule 10: a test must be able to
fail): if that iterable came back empty, the test passed without checking
anything. Three of them belong to the firewall packet. Of the other 22, 14
loop over something computed at run time (the stacks, the manifest, the
RF-log registry, the shipped asset trees, the workflow files, the bootstrap
script, the calibration baseline, the daemon's verify table). Each of
those now asserts first that the set its loop really checks is not empty
(`len(...) == 7` where the seven canonical units are the contract). Where
the loop skips entries (the TCP listener endpoints, the stacks without a
secret, the non-empty daemon defaults), that set is built first with the
same filter and the loop runs over it. The other 8 loop over a
literal written in the test or the module (a tuple of verbs, stacks, bad
inputs, foreign argv, D-Bus actions, the two HMAC results, built blobs) or
a fixed clock loop, and are left as they are.

Also rule 5: test_identity.py's global-callsign card is read with htmlq
(`by_id("operator-callsign")`, then `find` for the row and the marker)
instead of splitting the page text.

Test-suite change, no behaviour: no parent-red. Negative run instead: with
each guard's iterable replaced by an empty one, each of the 14 guarded tests
fails at its guard.

```

## The report

> # Code report: testrun-p2 v2 (a check over a computed list first asserts the list is not empty)
>
> ## Changes v1 → v2 (second read: 2 points + 1 note; auditor round 11: GREEN WITH NOTES, 1 note; all applied)
>
> | finding | change |
> |---|---|
> | `test_manifest_model.py` guard `any(c.endpoints …)` weaker than the loop's effective set (TCP listeners, not external) | the set is built once with the loop's filter (`listeners`), guarded, and the loop runs over it (14 today) |
> | `test_reticulum_stack.py` guard misses the loop's `secret_ref` skip | the stacks without a secret (`plain`) and their config writes (`writes`) are built first; the guard is on `writes`, the loop runs over it |
> | note: `test_daemon_params.py` inner `continue` | exact as well: the non-empty defaults (`defaults`) are built first, guarded, looped |
> | auditor note: `len(U.ALL_UNITS) == 7` needs the contract shown | kept, with the source line: `docs/architecture.md:59` `updater_units.py     # the seven canonical systemd units (frozen bytes) + verification` |
> | "each guard asserts the computed iterable a test loops over" | now "the set the loop really checks", true after the three changes |
>
> Branch `cons/testrun-p2-r1` on `origin/dev` = `d77e34f4`. Plan: PLAN-testrun v3 §3 P2 (gate 1 GREEN WITH NOTES).
> Tests only; no production file. Slow-marker decisions for these files are not here: they moved to P6 on the
> handler's line (plan v3 said per packet).
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | c28185b6 | 13 test files (below) | core, host, install, repo and stacks layers: 14 guards (each asserts that the set its loop really checks is non-empty, or `len(U.ALL_UNITS) == 7`); test_identity.py's card read via htmlq | none: a test-suite change, no behaviour. Negative run instead (below) |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
> docs         0 files, +0 lines; no fact given   OK
> tests        0 new tests; layers tests/core=core tests/host=host tests/install=install tests/repo=repo tests/stacks=stacks; red-before 0/0   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> ## The 22 tests (the scan's computed-loop list on d77e34f4, minus host/test_firewall.py's three, which are P4's)
>
> | test | iterable | decision |
> |---|---|---|
> | core/test_clean_purge_wipe.py::test_no_other_stack_uses_a_state_root_or_secret_a_purge_removes | `svc.stacks()`, the declared roots/secrets | guard: `svc.stacks() and any(s or r for s, r in decl.values())` |
> | core/test_rflog_decode.py::test_the_decoder_scripts_ship_and_take_no_key_on_argv | `rflog.REGISTRY` entries with a decoder | guard: `any(e.decoder for e in rflog.REGISTRY)` |
> | host/test_updater_units.py::test_verify_pct_h_same_root_is_modified_ours | `U.ALL_UNITS` | guard: `len(U.ALL_UNITS) == 7`; the contract: `docs/architecture.md:59` `updater_units.py     # the seven canonical systemd units (frozen bytes) + verification` |
> | install/test_binary_channel.py::test_default_channel_is_never_the_branch_tip | `svc.stacks()`, `svc.auto_install_rows()` | guard: both non-empty |
> | install/test_deps.py::test_bootstrap_script_runs_no_sudo | the script's lines | guard: `lines` |
> | install/test_slow_target_budget.py::test_every_calibration_entry_is_well_formed | `BASELINE["calibration"]` (4 entries today) | guard: `BASELINE.get("calibration")` |
> | repo/test_packaging.py::test_every_shipped_asset_tree_is_in_the_package_data_allow_list | the `-dist` trees; the `*constraints.txt` files | two guards, one per loop |
> | repo/test_packaging.py::test_every_file_in_a_shipped_asset_tree_is_tracked_by_git | the `-dist` trees | guard |
> | repo/test_workflow_shell.py::test_every_workflow_shell_block_parses (3 cases) | the workflow's `run:` blocks (ci 8, pages 3, testlab 10 today) | guard: `blocks` |
> | repo/test_workflow_shell.py::test_the_helper_does_not_wrap_a_verdict | `WORKFLOWS` | guard |
> | stacks/test_daemon_control.py::test_no_verify_entry_uses_the_scanning_channel_read | `dc._VERIFY` | guard |
> | stacks/test_daemon_params.py::test_all_default_values_are_daemon_valid | the non-empty defaults of `dp.CLIENT_STACKS` × both bands × the radio and LBT params | built as `defaults` (the loop's `v == ""` skip as a filter), guarded, looped |
> | stacks/test_manifest_model.py::test_every_tcp_listener_endpoint_carries_its_port_claim | the manifest's TCP listener endpoints that are not external (14 today) | built as `listeners` (the loop's skip as a filter), guarded, looped |
> | stacks/test_reticulum_stack.py::test_a_stack_without_secrets_is_unaffected | the config writes of the stacks other than reticulum that declare no `secret_ref` | built as `plain` → `writes` (both skips as filters), guarded on `writes`, looped |
> | core/test_controller.py::test_every_generic_verb_refuses_controller | `_GENERIC_VERBS`, a module literal (8 verbs) | none: a literal |
> | core/test_identity.py::test_licensed_with_neither_value_is_refused | `LICENSED`, a module literal (4 stacks) | none: a literal |
> | core/test_post_start.py::test_schedule_rejects_malformed_and_ambiguous | `bads`, a literal tuple in the test | none |
> | core/test_probes.py::test_an_unrelated_socat_is_not_our_kiss_bridge | `foreign`, a literal list in the test | none |
> | core/test_progress.py::test_tiny_steady_progress_never_stalls | `while clock.t < 10 * 600` on a fake clock from 0 | none: the loop always runs |
> | host/test_network_controls.py::test_shared_ap_way_home_actions_are_required_and_granted | `share` (literal), then `svc._NET_ACTIONS` | none: the first loop asserts both share actions are in `_NET_ACTIONS`, so the second is never empty |
> | stacks/test_meshcore.py::test_lhpc_never_emits_the_key | `blobs`, built in the test (two unconditional `append`s: the status summary and the config view) | none |
> | web/test_hmac.py::test_secret_value_never_appears_in_action_results | `results`, a two-element list in the test | none |
>
> The scan's class counts module-level names as computed; the 8 "none" rows are literals by reading (manual).
> Rule 5 in the same packet: `core/test_identity.py` (`test_global_card_renders_and_never_marked_required`) read the
> card with `html.split(...)`; it now uses `htmlq.parse`, `by_id("operator-callsign")` and `within(...).find("tr",
> class_="field-bad")` / `find("span", class_="req")`, the tags the settings template puts those classes on
> (`_stack_settings.html:211-212`).
>
> ## Negative run (each guard can fail)
>
> A temporary copy of each file with only the guard's iterable replaced by an empty one, the one test run, the copy
> deleted (`git status --short` after: clean):
>
> | test | result | first assertion line |
> |---|---|---|
> | tests/core/test_clean_purge_wipe.py::test_no_other_stack_uses_a_state_root_or_secret_a_purge_removes | 1 failed in 0.08s | `E       assert ([])` |
> | tests/core/test_rflog_decode.py::test_the_decoder_scripts_ship_and_take_no_key_on_argv | 1 failed in 0.05s | `E       assert False` |
> | tests/host/test_updater_units.py::test_verify_pct_h_same_root_is_modified_ours | 1 failed in 0.08s | `E       assert 0 == 7` |
> | tests/install/test_binary_channel.py::test_default_channel_is_never_the_branch_tip | 1 failed in 0.22s | `E       AssertionError: assert ((Stack(id='daemon', name='LoRaHAM daem` |
> | tests/install/test_deps.py::test_bootstrap_script_runs_no_sudo | 1 failed in 0.11s | `E       assert []` |
> | tests/install/test_slow_target_budget.py::test_every_calibration_entry_is_well_formed | 1 failed in 0.08s | `E       AssertionError: assert None` |
> | tests/repo/test_packaging.py::test_every_shipped_asset_tree_is_in_the_package_data_allow_list | 1 failed in 0.03s | `E       AssertionError: assert False` |
> | tests/repo/test_packaging.py::test_every_file_in_a_shipped_asset_tree_is_tracked_by_git | 1 failed in 0.03s | `E       assert False` |
> | tests/repo/test_workflow_shell.py::test_every_workflow_shell_block_parses | 3 failed in 0.04s | `E       assert []` |
> | tests/repo/test_workflow_shell.py::test_the_helper_does_not_wrap_a_verdict | 1 failed in 0.03s | `E       assert ()` |
> | tests/stacks/test_daemon_control.py::test_no_verify_entry_uses_the_scanning_channel_read | 1 failed in 0.06s | `E       assert {}` |
> | tests/stacks/test_daemon_params.py::test_all_default_values_are_daemon_valid | 1 failed in 0.03s | `E       assert []` (v2: the new set emptied) |
> | tests/stacks/test_manifest_model.py::test_every_tcp_listener_endpoint_carries_its_port_claim | 1 failed in 0.06s | `E       assert []` (v2: the new set emptied) |
> | tests/stacks/test_reticulum_stack.py::test_a_stack_without_secrets_is_unaffected | 1 failed in 0.13s | `E       assert []` (v2: the new set emptied) |
>
> `test_packaging.py`'s first test has two guards; the run empties the constraints one (the `-dist` guard is the
> same expression as the second test's, emptied there).
>
> ## The 6-point block
>
> 1. **Contracts:** none; every existing assertion is unchanged.
> 2. **Invariants:** each of the 14 tests now fails when its iterable is empty (the negative run).
> 3. **Failure classes:** a manifest, registry or workflow change that empties an iterable now fails the test instead
>    of passing it vacuously; `len(U.ALL_UNITS) == 7` also fails when a unit is added, which is the contract change
>    docs/architecture.md would need anyway.
> 4. **Test rules:** rule 10 (shown); rule 5 for one HTML read.
> 5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/host
>    tests/install tests/repo tests/stacks` → `6427 passed, 1 skipped in 110.37s`; `ruff check lhpc testlab` and
>    `ruff check tests --select F,E9` → `All checks passed!`.
> 6. **Adversarial self-review:**
>    - (a) The plan said "one guard per test" over 22; reading showed 8 literal iterables, where a guard would assert
>      a constant; those are left and listed with the reason.
>    - (b) The scan class falls from 25 to 11 (3 firewall + the 8 literals): it cannot tell a literal from a call.
>    - (c) The identity card's checks still name two classes, through htmlq's class-token match, the suite's tool for it.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | d77e34f4 |
> | diff | `git diff --stat origin/dev c28185b6` | 13 files changed, 44 insertions(+), 33 deletions(-) |
> | computed-loop class before/after | `python3 -I testrun-scan-v3.py` | 25 / 11 |
> | `.split(` on a page before/after | same | 3 / 2 (the shell-script one and `stacks/test_hardware.py:311`, P3's) |
> | negative run | the table above | 14 of 14 failed at the guard |
> | directories | the command in 5 | 6427 passed, 1 skipped |

## The full diff (`git diff d77e34f4c775d85ba0822b373ec4243651be5cc9 c28185b6ca5a087efb2497c9162f9191d418fe4e`)

```diff
diff --git a/tests/core/test_clean_purge_wipe.py b/tests/core/test_clean_purge_wipe.py
index 083589e0..f412fb6a 100644
--- a/tests/core/test_clean_purge_wipe.py
+++ b/tests/core/test_clean_purge_wipe.py
@@ -247,6 +247,7 @@ def test_no_other_stack_uses_a_state_root_or_secret_a_purge_removes(tmp_path):
     a declared root or secret file belongs to the stack that declares it."""
     svc = _svc(tmp_path)
     decl = _declared(svc)
+    assert svc.stacks() and any(s or r for s, r in decl.values())     # something is checked
     for st in svc.stacks():
         for c in st.components:
             tokens = _path_tokens(c)
diff --git a/tests/core/test_identity.py b/tests/core/test_identity.py
index b783ba2d..0d2ef8e4 100644
--- a/tests/core/test_identity.py
+++ b/tests/core/test_identity.py
@@ -212,8 +212,10 @@ def test_global_card_renders_and_never_marked_required(tmp_path):
     html = app.test_client().get("/stacks").get_data(as_text=True)
     assert "Global operator callsign" in html
     assert "Licensed stacks (chat," in html
-    card = html.split("Global operator callsign", 1)[1].split("</details>", 1)[0]
-    assert "field-bad" not in card and 'class="req"' not in card
+    import htmlq
+    doc = htmlq.parse(html)
+    card = doc.within(doc.by_id("operator-callsign"))
+    assert not card.find("tr", class_="field-bad") and not card.find("span", class_="req")
 
 
 # ===== audit-found launch/materialization regressions =====
diff --git a/tests/core/test_rflog_decode.py b/tests/core/test_rflog_decode.py
index eab41bbc..6c3d7bc8 100644
--- a/tests/core/test_rflog_decode.py
+++ b/tests/core/test_rflog_decode.py
@@ -334,6 +334,7 @@ def test_the_decoder_scripts_ship_and_take_no_key_on_argv():
     `test_stdin_carries_key_and_raw_only_and_stdout_is_capped_per_field` (what actually crosses
     the process boundary) plus the argv tests above (paths only)."""
     d = repo_paths.REPO / "lhpc" / "data" / "rfdecode"
+    assert any(e.decoder for e in rflog.REGISTRY)                     # something is checked
     for e in rflog.REGISTRY:
         if e.decoder:
             src = (d / f"decode_{e.decoder}.py").read_text()
diff --git a/tests/host/test_updater_units.py b/tests/host/test_updater_units.py
index a241f638..fb3dda39 100644
--- a/tests/host/test_updater_units.py
+++ b/tests/host/test_updater_units.py
@@ -350,6 +350,7 @@ def test_verify_pct_h_same_root_is_modified_ours(tmp_path):
     home = os.path.expanduser("~"); root = f"{home}/loraham-pi-control"
     _, co, venv = U.deployment_paths(root)
     ud = _install(tmp_path, **_pct_units())
+    assert len(U.ALL_UNITS) == 7                                      # the seven canonical units
     for k in U.ALL_UNITS:
         assert U.verify(ud, k, root, co, venv) == U.MODIFIED_OURS, k
 
diff --git a/tests/install/test_binary_channel.py b/tests/install/test_binary_channel.py
index 48817339..e9a80444 100644
--- a/tests/install/test_binary_channel.py
+++ b/tests/install/test_binary_channel.py
@@ -55,6 +55,7 @@ def test_default_channel_is_never_the_branch_tip(tmp_path, monkeypatch):
     only by asking for it, so upstream pushing to a branch can never change what a fresh box
     installs."""
     svc = _svc(tmp_path, monkeypatch=monkeypatch)
+    assert svc.stacks() and svc.auto_install_rows()                  # something is checked
     for st in svc.stacks():
         assert svc.default_channel(st.id) in (svc.BINARY_CHANNEL, "pinned")
     # every stack the auto-install (and so the image builder) plans for
diff --git a/tests/install/test_deps.py b/tests/install/test_deps.py
index bdcab0a8..c839abcf 100644
--- a/tests/install/test_deps.py
+++ b/tests/install/test_deps.py
@@ -376,7 +376,9 @@ def test_deps_script_service_has_every_category_and_no_venv_pip(tmp_path):
 def test_bootstrap_script_runs_no_sudo(tmp_path):
     """The script is root: `sudo` may survive only in comments and inside quoted (echoed) text."""
     import re
-    for ln in _svc(tmp_path).deps_script().splitlines():
+    lines = _svc(tmp_path).deps_script().splitlines()
+    assert lines                                                      # something is checked
+    for ln in lines:
         code = "" if ln.lstrip().startswith("#") else ln.split("#", 1)[0]
         assert "sudo" not in re.sub(r'"[^"]*"|\'[^\']*\'', "", code), ln
 
diff --git a/tests/install/test_slow_target_budget.py b/tests/install/test_slow_target_budget.py
index 256e1409..531a3d51 100644
--- a/tests/install/test_slow_target_budget.py
+++ b/tests/install/test_slow_target_budget.py
@@ -51,6 +51,7 @@ def test_every_baseline_entry_is_well_formed():
 
 
 def test_every_calibration_entry_is_well_formed():
+    assert BASELINE.get("calibration")                                # something is checked
     for c in BASELINE.get("calibration", []):
         assert re.fullmatch(r"sha256:[0-9a-f]{64}", str(c.get("workload", ""))), c
         assert c.get("source") in stt.SOURCES, c
diff --git a/tests/repo/test_packaging.py b/tests/repo/test_packaging.py
index 7c22419a..1a3216ff 100644
--- a/tests/repo/test_packaging.py
+++ b/tests/repo/test_packaging.py
@@ -47,6 +47,8 @@ def test_every_shipped_asset_tree_is_in_the_package_data_allow_list():
     patterns = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"][
         "package-data"]["lhpc"]
     data = root / "lhpc" / "data"
+    assert any(p.is_dir() and p.name.endswith("-dist") for p in data.iterdir())
+    assert any(data.glob("*constraints.txt"))                         # something is checked
 
     for tree in sorted(p for p in data.iterdir() if p.is_dir() and p.name.endswith("-dist")):
         assert f"data/{tree.name}/**/*" in patterns, (
@@ -77,6 +79,7 @@ def test_every_file_in_a_shipped_asset_tree_is_tracked_by_git():
     and the wheel built from the working tree still contained them — so only a box installing
     from a clone would have found the UI incomplete. Compare tracked files against the disk."""
     root = repo_paths.REPO
+    assert any(p.is_dir() and p.name.endswith("-dist") for p in (root / "lhpc" / "data").iterdir())
     for tree in sorted(p for p in (root / "lhpc" / "data").iterdir()
                        if p.is_dir() and p.name.endswith("-dist")):
         rel = tree.relative_to(root)
diff --git a/tests/repo/test_workflow_shell.py b/tests/repo/test_workflow_shell.py
index 5e28c619..18c628d9 100644
--- a/tests/repo/test_workflow_shell.py
+++ b/tests/repo/test_workflow_shell.py
@@ -47,7 +47,9 @@ def test_the_scan_finds_the_blocks_it_is_meant_to_check():
 
 @pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda w: w.name)
 def test_every_workflow_shell_block_parses(workflow):
-    for line_no, script in _run_blocks(workflow.read_text()):
+    blocks = list(_run_blocks(workflow.read_text()))
+    assert blocks                                                     # something is checked
+    for line_no, script in blocks:
         r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True,
                            check=False)
         assert r.returncode == 0, (
@@ -122,6 +124,7 @@ def test_the_helper_does_not_wrap_a_verdict():
     pip-audit` installs the auditor and is fine, while `retry 3 pip-audit .` would run it and is
     not. A substring match cannot tell those apart.
     """
+    assert WORKFLOWS                                                  # something is checked
     for wf in WORKFLOWS:
         for lineno, line in enumerate(wf.read_text().splitlines(), 1):
             stripped = line.strip()
diff --git a/tests/stacks/test_daemon_control.py b/tests/stacks/test_daemon_control.py
index 6a528d8d..ca849065 100644
--- a/tests/stacks/test_daemon_control.py
+++ b/tests/stacks/test_daemon_control.py
@@ -272,6 +272,7 @@ def test_no_verify_entry_uses_the_scanning_channel_read():
 
     Guards the whole table, not just MODE: a future confirmable key that needs a CHANNEL field
     must use NOSCAN, or it silently reintroduces this defect on every SET of that key."""
+    assert dc._VERIFY                                                 # something is checked
     for key, (command, _field) in dc._VERIFY.items():
         assert command != b"GET CHANNEL\n", f"{key} read-back would run a CAD scan"
 
diff --git a/tests/stacks/test_daemon_params.py b/tests/stacks/test_daemon_params.py
index a708d1ff..5599abb9 100644
--- a/tests/stacks/test_daemon_params.py
+++ b/tests/stacks/test_daemon_params.py
@@ -42,13 +42,12 @@ def test_meshcom_and_meshcore_868_defaults():
 
 def test_all_default_values_are_daemon_valid():
     # Every displayed default must pass the daemon's own validator (no mismatch/typo).
-    for s in dp.CLIENT_STACKS:
-        for band in ("433", "868"):
-            for name in dp.RADIO_PARAMS + dp.LBT_PARAMS:
-                v = dp.default_value(s, band, name)
-                if v == "":
-                    continue
-                assert daemon_control.validate_set(name, v) is None, f"{s}/{band}/{name}={v}"
+    defaults = [(s, band, name, dp.default_value(s, band, name)) for s in dp.CLIENT_STACKS
+                for band in ("433", "868") for name in dp.RADIO_PARAMS + dp.LBT_PARAMS]
+    defaults = [d for d in defaults if d[3] != ""]
+    assert defaults                                                   # something is checked
+    for s, band, name, v in defaults:
+        assert daemon_control.validate_set(name, v) is None, f"{s}/{band}/{name}={v}"
 
 
 def test_radio_params_greyed_lbt_not():
diff --git a/tests/stacks/test_manifest_model.py b/tests/stacks/test_manifest_model.py
index 8752ce4e..fe7320ac 100644
--- a/tests/stacks/test_manifest_model.py
+++ b/tests/stacks/test_manifest_model.py
@@ -177,17 +177,16 @@ def test_every_tcp_listener_endpoint_carries_its_port_claim():
     import re
 
     from lhpc.core.manifest import load_manifest
-    for s in load_manifest():
-        for c in s.components:
-            claimed = {r.key for r in c.resources}
-            for e in c.endpoints:
-                if e.kind != "tcp" or getattr(e, "external", False) \
-                        or getattr(e, "role", "") != "listener":
-                    continue
-                m = re.search(r":(\d+)$", str(e.address))
-                assert m, (c.id, e.address)
-                assert f"tcp.port.{m.group(1)}" in claimed, (
-                    f"{s.id}/{c.id}: endpoint {e.address} has no tcp.port.{m.group(1)} claim")
+    listeners = [(s, c, e) for s in load_manifest() for c in s.components for e in c.endpoints
+                 if e.kind == "tcp" and not getattr(e, "external", False)
+                 and getattr(e, "role", "") == "listener"]
+    assert listeners                                                  # something is checked
+    for s, c, e in listeners:
+        claimed = {r.key for r in c.resources}
+        m = re.search(r":(\d+)$", str(e.address))
+        assert m, (c.id, e.address)
+        assert f"tcp.port.{m.group(1)}" in claimed, (
+            f"{s.id}/{c.id}: endpoint {e.address} has no tcp.port.{m.group(1)} claim")
 
 
 def test_meshcore_webui_and_cli_force_both_dashboard_links():
diff --git a/tests/stacks/test_reticulum_stack.py b/tests/stacks/test_reticulum_stack.py
index d8d710c6..f9e21479 100644
--- a/tests/stacks/test_reticulum_stack.py
+++ b/tests/stacks/test_reticulum_stack.py
@@ -489,17 +489,14 @@ def test_a_stack_without_secrets_is_unaffected(monkeypatch):
 
     monkeypatch.setattr(service_params, "load_secrets", refuse)
     svc = ControllerService()
-    for stack in load_manifest():
-        if stack.id == "reticulum":
-            continue
-        has_secret = any(getattr(p, "secret_ref", "")
-                         for c in stack.components if c.config_file
-                         for p in c.config_file.params)
-        if has_secret:
-            continue
-        for w in svc.write_config_files(stack.id):
-            assert not (w.status == "failed" and "refused" in (w.detail or "")), \
-                f"{stack.id} broke on a secrets file it does not use"
+    plain = [stack for stack in load_manifest() if stack.id != "reticulum"
+             and not any(getattr(p, "secret_ref", "") for c in stack.components if c.config_file
+                         for p in c.config_file.params)]
+    writes = [(stack.id, w) for stack in plain for w in svc.write_config_files(stack.id)]
+    assert writes                                                     # something is checked
+    for sid, w in writes:
+        assert not (w.status == "failed" and "refused" in (w.detail or "")), \
+            f"{sid} broke on a secrets file it does not use"
 
 
 # ---- headless GUI skip ----------------------------------------------------
```
