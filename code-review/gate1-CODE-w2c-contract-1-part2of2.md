# Gate 1: w2c-contract-1 v2 (the CONTRACT release), part 2 of 2: the test diff

**Request.** v2 for a short delta round: judge what changed since v1 (round 13 GREEN WITH NOTES; the report's top lists it: the gate now also refuses an `incomplete` set containing a `compatible` unit, `update_refusal` takes the integration result, the call is wrapped, two report sentences). The full v2 diff is given; the rest is unchanged from v1 (same plan §2–§4, same bytes).

This packet is TWO files (part 1 and part 2, each under 90 KB); together they are your whole input: you have no repository access; use no connector, tool or web lookup. Answer once, after both parts.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied; the diffs are `git diff` output verbatim, split by path.

## The diff, part 2 (`git diff d77e34f4 d5e75ab3 -- tests ':!tests/data'`, verbatim)

```diff
diff --git a/tests/core/test_boot_restore.py b/tests/core/test_boot_restore.py
index 12de878a..ee5aa406 100644
--- a/tests/core/test_boot_restore.py
+++ b/tests/core/test_boot_restore.py
@@ -1313,8 +1313,8 @@ def test_web_integration_proven_real_function(tmp_path, monkeypatch):
     assert ok is False and "wants symlink points at" in why
 
 
-def test_the_next_releases_web_unit_proves_the_console_with_a_note(tmp_path, monkeypatch):
-    # The REAL gate: the next release's web unit (updater_units.render_next) is `compatible` —
+def test_the_other_releases_web_unit_proves_the_console_with_a_note(tmp_path, monkeypatch):
+    # The REAL gate: the other release's web unit (updater_units.render_compatible) is `compatible` —
     # proven, the reason being the units-compatible note naming the shell repair.
     from lhpc.core import updater_units as U
     home = tmp_path / "home"
@@ -1322,13 +1322,39 @@ def test_the_next_releases_web_unit_proves_the_console_with_a_note(tmp_path, mon
     (ud / "default.target.wants").mkdir(parents=True)
     monkeypatch.setenv("HOME", str(home))
     _r, checkout, venv = U.deployment_paths(str(tmp_path))
-    (ud / U.WEB_UNIT).write_text(U.render_next(U.WEB_UNIT, str(tmp_path), checkout, venv))
+    (ud / U.WEB_UNIT).write_text(U.render_compatible(U.WEB_UNIT, str(tmp_path), checkout, venv))
     (ud / "default.target.wants" / U.WEB_UNIT).symlink_to(ud / U.WEB_UNIT)
     ok, why = _svc(tmp_path)._web_integration_proven()
     assert ok is True
     assert "units-compatible:" in why and "lhpc self-update --repair-integration" in why
 
 
+_CHANGED = ("lhpc-web.service", "lhpc-nginx.service", "lhpc-boot-restore.service")
+
+
+@pytest.mark.parametrize("previous", [tuple(u for i, u in enumerate(_CHANGED) if m >> i & 1)
+                                      for m in range(8)], ids=lambda p: "+".join(p) or "none")
+def test_every_mix_of_the_previous_and_current_units_restores(tmp_path, monkeypatch, previous):
+    # Each changed unit is either this release's or the previous release's (an interrupted repair,
+    # an update not yet repaired): boot restore is proven on every mix; its note follows the web
+    # unit (the per-unit gate), and the set reads `compatible` until all three are current.
+    from lhpc.core import updater_units as U
+    home = tmp_path / "home"
+    ud = home / ".config" / "systemd" / "user"
+    (ud / "default.target.wants").mkdir(parents=True)
+    monkeypatch.setenv("HOME", str(home))
+    root = str(tmp_path)
+    _r, checkout, venv = U.deployment_paths(root)
+    for k in U.ALL_UNITS:
+        render = U.render_compatible if k in previous else U.render
+        (ud / k).write_text(render(k, root, checkout, venv))
+    (ud / "default.target.wants" / U.WEB_UNIT).symlink_to(ud / U.WEB_UNIT)
+    ok, why = _svc(tmp_path)._web_integration_proven()
+    assert ok is True
+    assert why == (U.COMPATIBLE_NOTE if U.WEB_UNIT in previous else "")
+    assert U.integration(ud, root)["status"] == ("compatible" if previous else "ok")
+
+
 def test_driver_defect_is_clean_integrity_failure(tmp_path, monkeypatch):
     # Any unexpected exception inside the driver -> clean nonzero failure, never a traceback
     # escaping to systemd as the unit's only diagnostic.
diff --git a/tests/golden/conftest.py b/tests/golden/conftest.py
index 2df02380..90a84ffa 100644
--- a/tests/golden/conftest.py
+++ b/tests/golden/conftest.py
@@ -448,19 +448,19 @@ def prior_boot(monkeypatch, tmp_path_factory):
     `LHPC_BOOT_ID_FILE` (the production override every reader honours), restore enabled by the
     default `[boot] restore = true` (no `[boot]` table is written), and the web integration proven
     by the canonical `lhpc-web.service` (`updater_units.render`) with its `default.target.wants`
-    link in the test's isolated HOME, rendered for `root` (`next_unit=True`: the next release's
-    unit, `updater_units.render_next`, which this version verifies as `compatible`)."""
+    link in the test's isolated HOME, rendered for `root` (`compatible=True`: the other release's
+    unit, `updater_units.render_compatible`, which this version verifies as `compatible`)."""
     from lhpc.core import updater_units
     boot = tmp_path_factory.mktemp("boot") / "boot_id"
     boot.write_text("CURBOOT\n")
     monkeypatch.setenv("LHPC_BOOT_ID_FILE", str(boot))
 
     def _plant(root, stack="kiss", comp="loraham-kiss-tnc", band="433", pid=999999,
-               evidence=True, next_unit=False):
+               evidence=True, compatible=False):
         units = Path(os.environ["HOME"]) / ".config" / "systemd" / "user"
         (units / "default.target.wants").mkdir(parents=True, exist_ok=True)
         _r, checkout, venv = updater_units.deployment_paths(str(root))
-        render = updater_units.render_next if next_unit else updater_units.render
+        render = updater_units.render_compatible if compatible else updater_units.render
         (units / updater_units.WEB_UNIT).write_text(
             render(updater_units.WEB_UNIT, str(root), checkout, venv))
         link = units / "default.target.wants" / updater_units.WEB_UNIT
diff --git a/tests/golden/test_golden_boot_restore.py b/tests/golden/test_golden_boot_restore.py
index ea8399bb..809ebe11 100644
--- a/tests/golden/test_golden_boot_restore.py
+++ b/tests/golden/test_golden_boot_restore.py
@@ -76,12 +76,12 @@ def test_nothing_to_restore(kiss_box, prior_boot, run_op):
     assert run.files == {"added": ["state/boot-restore.json"], "removed": [], "changed": []}
 
 
-def test_restores_on_the_next_releases_web_unit_and_says_so(kiss_box, prior_boot, run_op):
-    """intended: a web unit of the next release (`compatible`) proves the console as the current
+def test_restores_on_the_other_releases_web_unit_and_says_so(kiss_box, prior_boot, run_op):
+    """intended: a web unit of the other release (`compatible`) proves the console as the current
     one does; the restore runs exactly as on a current unit, and its result carries the
     units-compatible note."""
     box = kiss_box()
-    prior_boot(box.root, next_unit=True)
+    prior_boot(box.root, compatible=True)
     run = run_op(box.root, box.svc.boot_restore_run)
     assert run.fields == _fields("Boot restore: 1 restored.")
     assert _noted(run.res.details)
@@ -90,10 +90,10 @@ def test_restores_on_the_next_releases_web_unit_and_says_so(kiss_box, prior_boot
     assert box.owned() == ["loraham-kiss-tnc"]
 
 
-def test_nothing_to_restore_on_the_next_releases_web_unit_says_so(kiss_box, prior_boot, run_op):
+def test_nothing_to_restore_on_the_other_releases_web_unit_says_so(kiss_box, prior_boot, run_op):
     """intended: the note is carried on the no-plan exit too."""
     box = kiss_box()
-    prior_boot(box.root, evidence=False, next_unit=True)
+    prior_boot(box.root, evidence=False, compatible=True)
     run = run_op(box.root, box.svc.boot_restore_run)
     assert run.fields == _fields("Boot restore: nothing to restore.")
     assert _noted(run.res.details)
diff --git a/tests/host/test_deploy_scripts.py b/tests/host/test_deploy_scripts.py
index a0f489de..90da119f 100644
--- a/tests/host/test_deploy_scripts.py
+++ b/tests/host/test_deploy_scripts.py
@@ -139,10 +139,10 @@ def _run(script: Path, args, home: Path, fakebin: Path, *, real_first=False):
 
 def _deployment(root: Path, *, unit_home: Path, unit_target: Path | None = None,
                 link_target: Path | None = None, marker: bool = True, canonical_units: bool = True,
-                next_units: bool = False):
+                compatible: bool = False):
     """A fake installed layout with a WORKING venv/python (symlinked to this interpreter, which
     has lhpc importable) so uninstall's byte-exact unit render works, plus CANONICAL web+updater
-    units and a root marker (`next_units`: the next release's units where it changes one, read
+    units and a root marker (`compatible`: the other release's units where it changes one, read
     `compatible`). `unit_target`/`link_target` default to this root; point them elsewhere to model
     another deployment's integration that must be left untouched."""
     import sys as _sys
@@ -179,7 +179,7 @@ def _deployment(root: Path, *, unit_home: Path, unit_target: Path | None = None,
     if canonical_units:
         r, co, venv = _U.deployment_paths(str(ut))
         for k in _U.ALL_UNITS:
-            nxt = _U.render_next(k, r, co, venv) if next_units else None
+            nxt = _U.render_compatible(k, r, co, venv) if compatible else None
             (unit_dir / k).write_text(nxt or _U.render(k, r, co, venv))
     else:
         (unit_dir / "lhpc-web.service").write_text(
@@ -817,10 +817,10 @@ def test_uninstall_retry_recovers_leftover_staged_units_then_completes(tmp_path)
 
 
 def test_uninstall_removes_a_newer_releases_unit_set(tmp_path):
-    # units of the next release (`compatible`) are this deployment's: removed, not "CUSTOMIZED"
+    # units of the other release (`compatible`) are this deployment's: removed, not "CUSTOMIZED"
     home = tmp_path / "home"; home.mkdir()
     root = home / "loraham-pi-control"
-    _deployment(root, unit_home=home, next_units=True)
+    _deployment(root, unit_home=home, compatible=True)
     ud = _unit_dir(home)
     r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, _fake_bin(tmp_path, systemctl="ok"))
     assert r.returncode == 0, r.stdout + r.stderr
@@ -830,10 +830,10 @@ def test_uninstall_removes_a_newer_releases_unit_set(tmp_path):
 
 def test_uninstall_retry_recovers_a_newer_releases_staged_units(tmp_path):
     # the staged-recovery path reads the same verdict: a leftover `*.uninstall-staged` unit of the
-    # next release is restored and the retry completes
+    # other release is restored and the retry completes
     home = tmp_path / "home"; home.mkdir()
     root = home / "loraham-pi-control"
-    _deployment(root, unit_home=home, next_units=True)
+    _deployment(root, unit_home=home, compatible=True)
     ud = _unit_dir(home)
     r1 = _run(UNINSTALL, ["--target", str(root), "--yes"], home,
               _fake_bin(tmp_path, systemctl="reloadfail", break_restore=True))
diff --git a/tests/host/test_deployment.py b/tests/host/test_deployment.py
index bd5d4872..4e7f2979 100644
--- a/tests/host/test_deployment.py
+++ b/tests/host/test_deployment.py
@@ -45,11 +45,10 @@ def test_unit_has_least_privilege_hardening():
                       "ProtectKernelModules=true", "RestrictNamespaces=true", "PrivateTmp=false"):
         assert directive in active, directive
     assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK AF_BLUETOOTH" in active
-    # Writable areas: the runtime root, /tmp, and the optional -%h/.meshcore_nm entry ONLY
-    # (no shipped component uses it) — never broad $HOME or /var.
+    # Writable areas: the runtime root and /tmp ONLY — never broad $HOME or /var.
     rw = [ln for ln in active if ln.startswith("ReadWritePaths=")]
     assert len(rw) == 1 and "%h/loraham-pi-control" in rw[0] and "/tmp" in rw[0]
-    assert "%h/.meshcore_nm" in rw[0]
+    assert "%h/.meshcore_nm" not in rw[0]
     assert "/var" not in rw[0] and "%h " not in rw[0] and not rw[0].rstrip().endswith("%h")
 
 
diff --git a/tests/host/test_firewall.py b/tests/host/test_firewall.py
index b2eae726..9b2ab0b4 100644
--- a/tests/host/test_firewall.py
+++ b/tests/host/test_firewall.py
@@ -1684,8 +1684,8 @@ def test_fw_update_preflight_gates_nginx_unit(tmp_path, monkeypatch):
     assert svc.firewall_update_nginx_preflight() is None
 
 
-def test_fw_a_newer_releases_nginx_unit_carries_the_gate(tmp_path, monkeypatch):
-    # The next release's nginx unit (`compatible`) differs from this one only in a comment: the
+def test_fw_another_releases_nginx_unit_carries_the_gate(tmp_path, monkeypatch):
+    # The other release's nginx unit (`compatible`) differs from this one only in a comment: the
     # boot gate is in it, so neither the preflight nor the after-advance step touches it.
     from lhpc.core import updater_units as uu
     svc = _svc(tmp_path)
diff --git a/tests/host/test_updater_units.py b/tests/host/test_updater_units.py
index a241f638..54935b71 100644
--- a/tests/host/test_updater_units.py
+++ b/tests/host/test_updater_units.py
@@ -90,18 +90,15 @@ def test_web_and_helper_carry_the_bus_block_and_sandbox():
     for t in (web, helper):
         assert "InaccessiblePaths=%t/bus %t/systemd/private" in t
         assert "ProtectHome=read-only" in t and "ProtectSystem=strict" in t
-    # web also grants an OPTIONAL -%h/.meshcore_nm entry (no shipped component uses it); the helper
-    # (no stack GUIs) stays minimal. The `-` prefix makes the path OPTIONAL — an absent
-    # non-prefixed ReadWritePaths entry fails namespace setup (226/NAMESPACE) and would
-    # hard-fail the web console on a box where that app has never run.
+    # Both grant only the runtime root and /tmp (the dead optional -%h/.meshcore_nm entry is gone).
     #
     # Sideband is NOT granted %h/.kivy: KIVY_HOME redirects its state into
     # {root}/state/sideband/kivy, which is already writable. Granting it here would
     # change the unit bytes and strand every installed box — see
     # tests/repo/test_unit_templates_frozen.py.
-    assert f"ReadWritePaths={ROOT} -%h/.meshcore_nm /tmp" in web
+    assert f"ReadWritePaths={ROOT} /tmp" in web
     assert f"ReadWritePaths={ROOT} /tmp" in helper
-    assert "%h/.meshcore_nm" not in helper and "%h/.kivy" not in helper
+    assert "%h/.meshcore_nm" not in web + helper and "%h/.kivy" not in helper
     assert "%h/.kivy" not in web
     # helper: no QEMU -> W^X; no systemctl; declarative restart; refuse manual start
     assert "MemoryDenyWriteExecute=true" in helper
@@ -456,11 +453,11 @@ def test_verify_and_integration_cover_the_restart_units(tmp_path):
     assert U.integration(ud, ROOT)["status"] == "overridden"
 
 
-# --- the next release's units (the staged migration's expand step) ---------------------------
+# --- the other release's units (the staged migration: `compatible`) ---------------------------
 
 def _next_set(root, co, venv):
-    """The unit set as the next release installs it: its three changed units, the rest current."""
-    return {k: U.render_next(k, root, co, venv) or U.render(k, root, co, venv) for k in U.ALL_UNITS}
+    """The unit set as the other release installs it: its three changed units, the rest current."""
+    return {k: U.render_compatible(k, root, co, venv) or U.render(k, root, co, venv) for k in U.ALL_UNITS}
 
 
 @pytest.mark.parametrize("root", [
@@ -468,7 +465,7 @@ def _next_set(root, co, venv):
     "/srv/lhpc",                             # a custom --target outside $HOME
     "/tmp",                                  # a root that is also other text in the unit
 ])
-def test_the_next_releases_units_read_compatible_for_any_root(tmp_path, root):
+def test_the_other_releases_units_read_compatible_for_any_root(tmp_path, root):
     co, venv = f"{root}/src/loraham-pi-control", f"{root}/venv/lhpc"
     ud = _install(tmp_path, **_next_set(root, co, venv))
     changed = {U.WEB_UNIT, U.NGINX_UNIT, U.BOOT_RESTORE_UNIT}
@@ -477,19 +474,19 @@ def test_the_next_releases_units_read_compatible_for_any_root(tmp_path, root):
     assert U.integration(ud, root)["status"] == "compatible"
 
 
-def test_a_changed_next_unit_is_modified_ours_and_a_dropin_still_overrides(tmp_path):
-    ud = _install(tmp_path, **{U.WEB_UNIT: U.render_next(U.WEB_UNIT, ROOT, CO, VENV) + "# x\n"})
+def test_a_changed_compatible_unit_is_modified_ours_and_a_dropin_still_overrides(tmp_path):
+    ud = _install(tmp_path, **{U.WEB_UNIT: U.render_compatible(U.WEB_UNIT, ROOT, CO, VENV) + "# x\n"})
     assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.MODIFIED_OURS
-    (ud / U.WEB_UNIT).write_text(U.render_next(U.WEB_UNIT, ROOT, CO, VENV))
+    (ud / U.WEB_UNIT).write_text(U.render_compatible(U.WEB_UNIT, ROOT, CO, VENV))
     d = ud / f"{U.WEB_UNIT}.d"; d.mkdir()
     (d / "override.conf").write_text("[Service]\nExecStart=\nExecStart=/usr/bin/evil\n")
     assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.OVERRIDDEN
 
 
 def test_a_broken_next_render_never_raises_in_verify(tmp_path, monkeypatch):
-    ud = _install(tmp_path, **{U.WEB_UNIT: U.render_next(U.WEB_UNIT, ROOT, CO, VENV)})
+    ud = _install(tmp_path, **{U.WEB_UNIT: U.render_compatible(U.WEB_UNIT, ROOT, CO, VENV)})
     # Stubs the collaborator, the next template table: its web entry cannot be formatted.
-    monkeypatch.setitem(U._NEXT_TEMPLATES, U.WEB_UNIT, "{no_such_field}")
+    monkeypatch.setitem(U._COMPATIBLE_TEMPLATES, U.WEB_UNIT, "{no_such_field}")
     assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.MODIFIED_OURS
 
 
@@ -501,9 +498,47 @@ def test_write_set_rewrites_a_compatible_set_to_this_versions_units(tmp_path):
     assert U.integration(ud, ROOT)["status"] == "ok"
 
 
+@pytest.mark.parametrize("k", [1, 2, 3])
+def test_a_repair_interrupted_after_any_unit_leaves_a_set_that_restores_and_completes(
+        tmp_path, monkeypatch, k):
+    # The forward write: the repair replaces the units one at a time (atomic per unit). A failure on
+    # the k-th replacement leaves the earlier units current and the rest the previous release's:
+    # every unit still reads ok or compatible (boot restore runs), and a rerun completes the set.
+    ud = _install(tmp_path, **_next_set(ROOT, CO, VENV))
+    real, calls = U.os.replace, []
+
+    def replace(src, dst):            # fails exactly the k-th unit replacement
+        calls.append(dst)
+        if len(calls) == k:
+            raise OSError("injected")
+        return real(src, dst)
+    monkeypatch.setattr(U.os, "replace", replace)
+    with pytest.raises(OSError, match="injected"):
+        U.write_set(ud, ROOT)
+    monkeypatch.setattr(U.os, "replace", real)
+    changed = [u for u in U.ALL_UNITS if u in (U.WEB_UNIT, U.NGINX_UNIT, U.BOOT_RESTORE_UNIT)]
+    assert [Path(d).name for d in calls] == changed[:k]          # the branch under test ran
+    verdicts = {u: U.verify(ud, u, ROOT, CO, VENV) for u in changed}
+    assert verdicts == {u: (U.OK if i < k - 1 else U.COMPATIBLE) for i, u in enumerate(changed)}
+    assert U.integration(ud, ROOT)["status"] == "compatible"
+    U.write_set(ud, ROOT)
+    assert U.integration(ud, ROOT)["status"] == "ok"
+
+
+@pytest.mark.parametrize("status, units, refused", [
+    ("compatible", (U.COMPATIBLE, U.OK), True),
+    ("incomplete", (U.COMPATIBLE, U.MISSING), True),       # beside a missing or changed unit too
+    ("incomplete", (U.COMPATIBLE, U.MODIFIED_OURS), True),
+    ("ok", (U.OK, U.OK), False), ("incomplete", (U.OK, U.MISSING), False), (None, (), False)])
+def test_an_update_refuses_to_start_from_another_releases_units(status, units, refused):
+    why = U.update_refusal({"status": status, "per_unit": dict(zip(("a", "b"), units))})
+    assert (why is not None) is refused
+    assert not refused or "lhpc self-update --repair-integration" in why
+
+
 def test_verify_file_reads_one_file_as_verify_does(tmp_path):
     f = tmp_path / "lhpc-web.service.uninstall-staged"
-    f.write_text(U.render_next(U.WEB_UNIT, ROOT, CO, VENV))
+    f.write_text(U.render_compatible(U.WEB_UNIT, ROOT, CO, VENV))
     assert U.verify_file(f, U.WEB_UNIT, ROOT) == U.COMPATIBLE
     f.write_text(U.render(U.WEB_UNIT, ROOT, CO, VENV))
     assert U.verify_file(f, U.WEB_UNIT, ROOT) == U.OK
@@ -515,12 +550,12 @@ def test_verify_file_reads_one_file_as_verify_does(tmp_path):
     assert U.verify_file(tmp_path / "absent", U.WEB_UNIT, ROOT) == U.MISSING
 
 
-def test_the_module_cli_renders_the_next_unit_and_verifies_a_file(tmp_path, capsys):
-    assert U._main(["x", "render-next", U.NGINX_UNIT, ROOT, CO, VENV]) == 0
-    assert capsys.readouterr().out == U.render_next(U.NGINX_UNIT, ROOT, CO, VENV)
-    assert U._main(["x", "render-next", U.HELPER_UNIT, ROOT, CO, VENV]) == 1   # unchanged unit
+def test_the_module_cli_renders_the_compatible_unit_and_verifies_a_file(tmp_path, capsys):
+    assert U._main(["x", "render-compatible", U.NGINX_UNIT, ROOT, CO, VENV]) == 0
+    assert capsys.readouterr().out == U.render_compatible(U.NGINX_UNIT, ROOT, CO, VENV)
+    assert U._main(["x", "render-compatible", U.HELPER_UNIT, ROOT, CO, VENV]) == 1   # unchanged unit
     f = tmp_path / "u"
-    f.write_text(U.render_next(U.NGINX_UNIT, ROOT, CO, VENV))
+    f.write_text(U.render_compatible(U.NGINX_UNIT, ROOT, CO, VENV))
     capsys.readouterr()
     assert U._main(["x", "verify-file", U.NGINX_UNIT, str(f), ROOT]) == 0
     assert capsys.readouterr().out == "compatible\n"
diff --git a/tests/install/test_selfupdate.py b/tests/install/test_selfupdate.py
index 7dfd2708..65997fcf 100644
--- a/tests/install/test_selfupdate.py
+++ b/tests/install/test_selfupdate.py
@@ -305,6 +305,31 @@ def _op_inactive(op_svc, monkeypatch, apply_result, *, pip=None):
     return svc, fake, root
 
 
+def test_operator_apply_refuses_while_the_units_are_another_releases(op_svc, monkeypatch):
+    # The update gate (docs/maintenance.md, *Changing a unit*): another release's units are repaired
+    # before any update, so a later unit change meets only units it accepts. Nothing is fetched,
+    # stopped or synced; with this release's units the same apply proceeds (the control).
+    from lhpc.core import updater_units as U
+    from lhpc.core.services import ActionResult, ControllerService
+    applied = []
+    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False: (
+        applied.append(force), ActionResult(True, "already", data={"already": True}))[1])
+    svc, fake = op_svc(dict(_WEB_INACTIVE), units=True, invocation=False)
+    ud, root = svc._user_unit_dir(), str(svc._paths.runtime_root)
+    _r, co, venv = U.deployment_paths(root)
+    (ud / U.WEB_UNIT).write_text(U.render_compatible(U.WEB_UNIT, root, co, venv))
+    r = svc.self_update_apply_operator()
+    assert r.ok is False and r.data == {"integration": "compatible"}
+    assert r.next_commands == ["lhpc self-update --repair-integration"]
+    assert applied == [] and not any(c[0] in ("systemctl", "git") for c in fake.calls)
+    (ud / U.HELPER_UNIT).unlink()                           # beside a missing unit: `incomplete`
+    assert svc.updater_integration()["status"] == "incomplete"
+    assert svc.self_update_apply_operator().data == {"integration": "compatible"} and applied == []
+    (ud / U.HELPER_UNIT).write_text(U.render(U.HELPER_UNIT, root, co, venv))
+    (ud / U.WEB_UNIT).write_text(U.render(U.WEB_UNIT, root, co, venv))
+    assert svc.self_update_apply_operator().ok and applied == [False]
+
+
 def test_operator_inactive_real_advance_syncs_venv(op_svc, monkeypatch):
     # web inactive + a REAL advance still synchronizes the editable venv (no service control).
     from lhpc.core.services import ActionResult
@@ -941,6 +966,6 @@ def test_status_names_a_newer_releases_units_and_the_shell_repair(op_svc, tmp_pa
     svc, _fake = op_svc(units=True)
     _r, co, venv = U.deployment_paths(str(tmp_path))
     assert not any("units-compatible:" in d for d in svc.status().details)          # control
-    (svc._user_unit_dir() / U.WEB_UNIT).write_text(U.render_next(U.WEB_UNIT, str(tmp_path), co, venv))
+    (svc._user_unit_dir() / U.WEB_UNIT).write_text(U.render_compatible(U.WEB_UNIT, str(tmp_path), co, venv))
     assert any("units-compatible:" in d and "lhpc self-update --repair-integration" in d
                for d in svc.status().details), svc.status().details
diff --git a/tests/install/test_selfupdate_service.py b/tests/install/test_selfupdate_service.py
index d74de3f6..1f10327b 100644
--- a/tests/install/test_selfupdate_service.py
+++ b/tests/install/test_selfupdate_service.py
@@ -529,7 +529,7 @@ def test_repair_and_trigger_names_the_shell_repair_for_a_newer_releases_units(tm
     _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
     ud, root = svc._user_unit_dir(), str(tmp_path)
     _r, co, venv = U.deployment_paths(root)
-    (ud / U.WEB_UNIT).write_text(U.render_next(U.WEB_UNIT, root, co, venv))
+    (ud / U.WEB_UNIT).write_text(U.render_compatible(U.WEB_UNIT, root, co, venv))
     res = svc.self_update_repair_and_trigger()
     assert not res.ok and res.data.get("compatible") is True
     assert res.next_commands == ["lhpc self-update --repair-integration"]
@@ -675,7 +675,7 @@ def test_the_unit_refresh_runs_out_of_process(op_svc, monkeypatch):
 
 
 def test_the_refresh_leaves_a_newer_releases_units_as_they_are(op_svc, monkeypatch):
-    """A `compatible` set is the next release's units: the running code must not write its own
+    """A `compatible` set is the other release's units: the running code must not write its own
     (older) render over them; the out-of-process verifier still runs and decides."""
     from lhpc.core.probes.backends import CommandResult as CR
     from lhpc.core.services import ControllerService
@@ -694,12 +694,12 @@ def test_the_refresh_leaves_a_newer_releases_units_as_they_are(op_svc, monkeypat
     ok, why = svc._refresh_units_post_update()  # the private IS the seam under test
     assert repaired == []                                       # no in-process write
     assert any("verify-set" in str(a) for a in seen[-1])
-    assert not ok and why.startswith("units of a newer release — left as they are")
+    assert not ok and why.startswith("units of another release — left as they are")
 
 
 @pytest.mark.parametrize("other", ["missing", "modified_ours"])
 def test_the_refresh_keeps_a_mixed_set_with_newer_units_and_verifies_it(op_svc, monkeypatch, other):
-    """A newer release's unit beside a missing or changed one of this deployment is still ours:
+    """Another release's unit beside a missing or changed one of this deployment is still ours:
     nothing is written in process, the verifier runs, and the result names the shell repair."""
     from lhpc.core.probes.backends import CommandResult as CR
     from lhpc.core.services import ControllerService
@@ -718,7 +718,7 @@ def test_the_refresh_keeps_a_mixed_set_with_newer_units_and_verifies_it(op_svc,
     ok, why = svc._refresh_units_post_update()  # the private IS the seam under test
     assert repaired == []                                       # no in-process write
     assert seen and any("verify-set" in str(a) for a in seen[-1])
-    assert not ok and "newer release" in why and "lhpc self-update --repair-integration" in why
+    assert not ok and "another release" in why and "lhpc self-update --repair-integration" in why
 
 
 def test_the_one_click_helper_verifies_the_units_it_refreshed(tmp_path, op_svc, monkeypatch):
diff --git a/tests/repo/test_unit_templates_frozen.py b/tests/repo/test_unit_templates_frozen.py
index ab65b8fd..a5f6bff7 100644
--- a/tests/repo/test_unit_templates_frozen.py
+++ b/tests/repo/test_unit_templates_frozen.py
@@ -19,8 +19,10 @@ renders.
 
 The staged migration's data is guarded here too: `unit-templates-released.sha256` is the
 append-only history of every digest a release shipped (it only grows, and every current digest is
-in it), and `unit-templates-next.sha256` pins the next release's units (`updater_units._NEXT_EDITS`)
-both ways, none equal to its current unit.
+in it), and `unit-templates-compatible.sha256` pins the other release's units
+(`updater_units._COMPATIBLE_EDITS`) both ways, none equal to its current unit and every one released.
+The 0.12.0/0.12.1 units stay in it in every later release (docs/maintenance.md, *Changing a unit*): a
+box that went back to 0.12.1 and then straight to a later release still restores at boot.
 """
 from __future__ import annotations
 
@@ -32,10 +34,15 @@ from lhpc.core import updater_units as U
 
 FROZEN = repo_paths.TESTS / "data" / "unit-templates.sha256"
 RELEASED = repo_paths.TESTS / "data" / "unit-templates-released.sha256"
-NEXT = repo_paths.TESTS / "data" / "unit-templates-next.sha256"
-MESSAGE = ("a unit's bytes change only through the staged unit migration (docs/backlog.md): first "
-           "a release that accepts the new bytes (updater_units._NEXT_EDITS), then the one that "
+COMPATIBLE = repo_paths.TESTS / "data" / "unit-templates-compatible.sha256"
+MESSAGE = ("a unit's bytes change only through the staged unit migration (docs/maintenance.md): first "
+           "a release that accepts the new bytes (updater_units._COMPATIBLE_EDITS), then the one that "
            "ships them, its digests added to the released history")
+# The 0.12.0/0.12.1 units: every later release keeps them `compatible` (docs/maintenance.md).
+PRE_CONTRACT = {"lhpc-web.service": "67ee9e95fa5f14b3f1e0c7c2ab8a76671f3b5521963438f5980c08110eab267e",
+                "lhpc-nginx.service": "101d941f78e321c87340ed5dba74c93a910f642a662dc5de709241a8367fcf61",
+                "lhpc-boot-restore.service":
+                    "ecb2dde6c6f48476acc0d6f216fb30d3d9ef8087a530607b1e876448382c3652"}
 # The released history as 0.12.0 left it: the history only grows, so every one of these stays.
 SEED = """67ee9e95fa5f14b3f1e0c7c2ab8a76671f3b5521963438f5980c08110eab267e  lhpc-web.service  0.5.0-0.12.0
 c10ab4d516da4b38824c9af955ba2cd012d0de4d6d776fcd297bade33108813e  lhpc-selfupdate.service  0.5.0-0.12.0
@@ -87,14 +94,14 @@ def _history(text: str) -> list[tuple[str, str]]:
     return [tuple(line.split("  ")[:2]) for line in text.splitlines() if line]
 
 
-def _next_renders() -> dict[str, str]:
+def _compatible_renders() -> dict[str, str]:
     root = "%h/loraham-pi-control"
-    return {kind: hashlib.sha256(U.render_next(kind, root, f"{root}/src/loraham-pi-control",
-                                               f"{root}/venv/lhpc").encode("utf-8")).hexdigest()
-            for kind in U.ALL_UNITS if U.render_next(kind, root, root, root) is not None}
+    return {kind: hashlib.sha256(U.render_compatible(kind, root, f"{root}/src/loraham-pi-control",
+                                                     f"{root}/venv/lhpc").encode("utf-8")).hexdigest()
+            for kind in U.ALL_UNITS if U.render_compatible(kind, root, root, root) is not None}
 
 
-def _guard(current: dict, history: list, next_pinned: dict, next_rendered: dict) -> list[str]:
+def _guard(current: dict, history: list, pinned: dict, rendered: dict) -> list[str]:
     """The migration data's rules, as named errors."""
     errors = []
     held = {}
@@ -106,54 +113,70 @@ def _guard(current: dict, history: list, next_pinned: dict, next_rendered: dict)
                for name, digest in current.items() if digest not in held.get(name, set())]
     errors += [f"firewall unit's history is not its current digest alone: {name}"
                for name in current if name not in U.ALL_UNITS and held.get(name) != {current[name]}]
-    if next_pinned != next_rendered:
-        errors.append(f"next units differ from their pinned digests: "
-                      f"{sorted(set(next_pinned.items()) ^ set(next_rendered.items()))}")
-    errors += [f"next unit equals its current unit: {name}"
-               for name, digest in next_rendered.items() if current.get(name) == digest]
+    if pinned != rendered:
+        errors.append(f"compatible units differ from their pinned digests: "
+                      f"{sorted(set(pinned.items()) ^ set(rendered.items()))}")
+    errors += [f"compatible unit equals its current unit: {name}"
+               for name, digest in rendered.items() if current.get(name) == digest]
+    errors += [f"compatible digest not in the released history: {name}"
+               for name, digest in pinned.items() if digest not in held.get(name, set())]
+    errors += [f"0.12.0/0.12.1 unit no longer compatible: {name}"
+               for name, digest in PRE_CONTRACT.items() if pinned.get(name) != digest]
     return errors
 
 
-def test_the_released_history_and_the_next_units_agree_with_the_templates():
+def test_the_released_history_and_the_compatible_units_agree_with_the_templates():
     current = {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in _renders().items()}
-    assert _guard(current, _history(RELEASED.read_text()), _pairs(NEXT), _next_renders()) == []
+    assert _guard(current, _history(RELEASED.read_text()), _pairs(COMPATIBLE), _compatible_renders()) == []
 
 
 def test_a_byte_change_without_its_history_line_fails():
     current = _pairs(FROZEN)
     history = _history(RELEASED.read_text())
     current[U.WEB_UNIT] = "0" * 64                              # a changed template's new digest
-    assert _guard(current, history, _pairs(NEXT), _pairs(NEXT)) == [
+    assert _guard(current, history, _pairs(COMPATIBLE), _pairs(COMPATIBLE)) == [
         f"current digest not in the released history: {U.WEB_UNIT}"]
-    assert _guard(current, history + [("0" * 64, U.WEB_UNIT)], _pairs(NEXT), _pairs(NEXT)) == []
+    assert _guard(current, history + [("0" * 64, U.WEB_UNIT)], _pairs(COMPATIBLE), _pairs(COMPATIBLE)) == []
 
 
 def test_a_released_line_removed_from_the_history_fails():
     history = [(d, n) for d, n in _history(RELEASED.read_text()) if n != U.HELPER_UNIT]
-    errors = _guard(_pairs(FROZEN), history, _pairs(NEXT), _pairs(NEXT))
+    errors = _guard(_pairs(FROZEN), history, _pairs(COMPATIBLE), _pairs(COMPATIBLE))
     assert f"history lost a released line: {U.HELPER_UNIT}" in errors
 
 
-def test_a_next_unit_without_its_pinned_digest_or_equal_to_current_fails():
+def test_a_compatible_unit_without_its_pinned_digest_or_equal_to_current_fails():
     current, history = _pairs(FROZEN), _history(RELEASED.read_text())
-    rendered = dict(_pairs(NEXT), **{U.HELPER_UNIT: "1" * 64})  # an edit with no pinned line
-    assert any(e.startswith("next units differ") for e in _guard(current, history, _pairs(NEXT),
-                                                                 rendered))
-    same = dict(_pairs(NEXT), **{U.WEB_UNIT: current[U.WEB_UNIT]})
-    assert f"next unit equals its current unit: {U.WEB_UNIT}" in _guard(current, history, same, same)
+    rendered = dict(_pairs(COMPATIBLE), **{U.HELPER_UNIT: "1" * 64})  # an edit with no pinned line
+    assert any(e.startswith("compatible units differ") for e in _guard(current, history,
+                                                                       _pairs(COMPATIBLE), rendered))
+    same = dict(_pairs(COMPATIBLE), **{U.WEB_UNIT: current[U.WEB_UNIT]})
+    assert f"compatible unit equals its current unit: {U.WEB_UNIT}" in _guard(current, history, same,
+                                                                               same)
 
 
-def test_every_next_edit_matches_its_template_once_and_has_no_brace():
-    assert set(U._NEXT_TEMPLATES) == set(U._NEXT_EDITS)
-    for kind, edits in U._NEXT_EDITS.items():
+def test_a_compatible_unit_never_released_or_the_pre_contract_units_dropped_fails():
+    current, history = _pairs(FROZEN), _history(RELEASED.read_text())
+    unreleased = dict(_pairs(COMPATIBLE), **{U.NGINX_UNIT: "2" * 64})
+    errors = _guard(current, history, unreleased, unreleased)
+    assert f"compatible digest not in the released history: {U.NGINX_UNIT}" in errors
+    assert f"0.12.0/0.12.1 unit no longer compatible: {U.NGINX_UNIT}" in errors
+    dropped = {k: v for k, v in _pairs(COMPATIBLE).items() if k != U.BOOT_RESTORE_UNIT}
+    assert f"0.12.0/0.12.1 unit no longer compatible: {U.BOOT_RESTORE_UNIT}" in _guard(
+        current, history, dropped, dropped)
+
+
+def test_every_compatible_edit_matches_its_template_once_and_has_no_brace():
+    assert set(U._COMPATIBLE_TEMPLATES) == set(U._COMPATIBLE_EDITS)
+    for kind, edits in U._COMPATIBLE_EDITS.items():
         for old, new in edits:
             assert U._TEMPLATES[kind].count(old) == 1, (kind, old)
             assert "{" not in new and "}" not in new, (kind, new)
 
 
 def test_an_edit_that_does_not_match_is_left_out_never_raised(monkeypatch):
-    monkeypatch.setitem(U._NEXT_EDITS, U.WEB_UNIT, (("no such text\n", "x\n"),))
-    assert U._next_template(U.WEB_UNIT) is None
-    monkeypatch.setitem(U._NEXT_EDITS, U.WEB_UNIT, ((" -%h/.meshcore_nm /tmp\n", " {root}\n"),))
-    assert U._next_template(U.WEB_UNIT) is None
+    monkeypatch.setitem(U._COMPATIBLE_EDITS, U.WEB_UNIT, (("no such text\n", "x\n"),))
+    assert U._compatible_template(U.WEB_UNIT) is None
+    monkeypatch.setitem(U._COMPATIBLE_EDITS, U.WEB_UNIT, ((" /tmp\n", " {root}\n"),))
+    assert U._compatible_template(U.WEB_UNIT) is None
```
