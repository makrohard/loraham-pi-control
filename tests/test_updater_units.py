"""Canonical updater units + the byte-exact integrity proof (lhpc.core.updater_units)."""

from pathlib import Path

import pytest

from lhpc.core import updater_units as U

REPO = Path(__file__).resolve().parents[1]
ROOT = "/home/op/loraham-pi-control"
CO, VENV = f"{ROOT}/src/loraham-pi-control", f"{ROOT}/venv/lhpc"


def _install(tmp_path, **units) -> Path:
    ud = tmp_path / ".config" / "systemd" / "user"
    ud.mkdir(parents=True)
    for name, text in units.items():
        (ud / name).write_text(text)
    return ud


# --- render / template parity ----------------------------------------------------------------

@pytest.mark.parametrize("kind,fname", [
    (U.WEB_UNIT, "lhpc-web.service"),
    (U.HELPER_UNIT, "lhpc-selfupdate.service"),
    (U.PATH_UNIT, "lhpc-selfupdate.path"),
])
def test_deploy_templates_are_exact_renders(kind, fname):
    r = "%h/loraham-pi-control"
    expected = U.render(kind, r, f"{r}/src/loraham-pi-control", f"{r}/venv/lhpc")
    assert (REPO / "deploy" / fname).read_text() == expected


def test_overwrite_variant_is_gone():
    assert not (REPO / "deploy" / "lhpc-selfupdate-overwrite.service").exists()


def test_render_unknown_kind_raises():
    with pytest.raises(ValueError):
        U.render("bogus.service", ROOT, CO, VENV)


def test_web_and_helper_carry_the_bus_block_and_sandbox():
    web = U.render(U.WEB_UNIT, ROOT, CO, VENV)
    helper = U.render(U.HELPER_UNIT, ROOT, CO, VENV)
    for t in (web, helper):
        assert "InaccessiblePaths=%t/bus %t/systemd/private" in t
        assert "ProtectHome=read-only" in t and "ProtectSystem=strict" in t
    # web also grants the stack GUI (meshcore-nodegui) its %h/.meshcore_nm data dir; the
    # helper (no stack GUIs) stays minimal.
    assert f"ReadWritePaths={ROOT} %h/.meshcore_nm /tmp" in web
    assert f"ReadWritePaths={ROOT} /tmp" in helper
    assert "%h/.meshcore_nm" not in helper
    # helper: no QEMU -> W^X; no systemctl; declarative restart; refuse manual start
    assert "MemoryDenyWriteExecute=true" in helper
    assert "RefuseManualStart=yes" in helper
    for d in ("Conflicts=lhpc-web.service", "After=lhpc-web.service",
              "OnSuccess=lhpc-web.service", "OnFailure=lhpc-web.service"):
        assert d in helper
    for t in (web, helper):        # no systemctl in ANY active directive (comments may mention it)
        assert not any("systemctl" in ln for ln in t.splitlines() if not ln.lstrip().startswith("#"))
    # web: pulls the watcher up + refuses restart mid-uninstall; keeps QEMU W+X (no MDWE)
    assert "Wants=network-online.target lhpc-selfupdate.path" in web
    assert f"ConditionPathExists=!{ROOT}/.lhpc-uninstalling" in web
    assert not any(ln.startswith("MemoryDenyWriteExecute") for ln in web.splitlines())


# --- verify() class taxonomy -----------------------------------------------------------------

def _canon(tmp_path):
    return _install(tmp_path,
                    **{U.WEB_UNIT: U.render(U.WEB_UNIT, ROOT, CO, VENV),
                       U.HELPER_UNIT: U.render(U.HELPER_UNIT, ROOT, CO, VENV),
                       U.PATH_UNIT: U.render(U.PATH_UNIT, ROOT, CO, VENV)})


def test_verify_ok_and_integration_ok(tmp_path):
    ud = _canon(tmp_path)
    for k in U.ALL_UNITS:
        assert U.verify(ud, k, ROOT, CO, VENV) == U.OK
    assert U.integration(ud, ROOT)["status"] == "ok"


def test_verify_missing(tmp_path):
    ud = tmp_path / ".config" / "systemd" / "user"; ud.mkdir(parents=True)
    assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.MISSING
    assert U.integration(ud, ROOT)["status"] == "incomplete"


def test_verify_modified_ours_service_and_path(tmp_path):
    web = U.render(U.WEB_UNIT, ROOT, CO, VENV) + "\n# operator note\n"
    path = U.render(U.PATH_UNIT, ROOT, CO, VENV) + "\n# note\n"
    ud = _install(tmp_path, **{U.WEB_UNIT: web, U.PATH_UNIT: path,
                               U.HELPER_UNIT: U.render(U.HELPER_UNIT, ROOT, CO, VENV)})
    assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.MODIFIED_OURS
    assert U.verify(ud, U.PATH_UNIT, ROOT, CO, VENV) == U.MODIFIED_OURS
    assert U.integration(ud, ROOT)["status"] == "incomplete"


def test_verify_foreign_other_root(tmp_path):
    other = "/home/op/other-root"
    web = U.render(U.WEB_UNIT, other, f"{other}/src/loraham-pi-control", f"{other}/venv/lhpc")
    path = U.render(U.PATH_UNIT, other, f"{other}/src/loraham-pi-control", f"{other}/venv/lhpc")
    ud = _install(tmp_path, **{U.WEB_UNIT: web, U.PATH_UNIT: path})
    assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.FOREIGN
    assert U.verify(ud, U.PATH_UNIT, ROOT, CO, VENV) == U.FOREIGN
    assert U.integration(ud, ROOT)["status"] == "foreign"


def test_verify_ambiguous(tmp_path):
    ud = _install(tmp_path, **{U.WEB_UNIT: "[Service]\nExecStart=/usr/bin/other\n"})
    assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.AMBIGUOUS


def test_verify_overridden_by_dropin(tmp_path):
    ud = _canon(tmp_path)
    d = ud / f"{U.WEB_UNIT}.d"; d.mkdir()
    (d / "override.conf").write_text("[Service]\nExecStart=\nExecStart=/usr/bin/evil\n")
    assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.OVERRIDDEN
    assert U.integration(ud, ROOT)["status"] == "overridden"


def test_verify_unsafe_symlinked_unit(tmp_path):
    ud = tmp_path / ".config" / "systemd" / "user"; ud.mkdir(parents=True)
    (ud / U.WEB_UNIT).symlink_to("/dev/null")            # a mask
    assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.UNSAFE


def test_verify_unsafe_symlinked_dir(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    ud = tmp_path / ".config" / "systemd" / "user"
    ud.parent.mkdir(parents=True)
    ud.symlink_to(real)
    assert U.verify(ud, U.WEB_UNIT, ROOT, CO, VENV) == U.UNSAFE


# --- write_set() -----------------------------------------------------------------------------

def test_write_set_writes_missing_and_restores_modified(tmp_path):
    ud = _install(tmp_path, **{U.WEB_UNIT: U.render(U.WEB_UNIT, ROOT, CO, VENV) + "\n# edited\n"})
    actions = dict(U.write_set(ud, ROOT))
    assert actions[U.WEB_UNIT] == "restored" and actions[U.HELPER_UNIT] == "written"
    for k in U.ALL_UNITS:
        assert U.verify(ud, k, ROOT, CO, VENV) == U.OK


def test_write_set_idempotent_on_ok(tmp_path):
    ud = _canon(tmp_path)
    assert all(a == "unchanged" for _, a in U.write_set(ud, ROOT))


@pytest.mark.parametrize("bad", ["foreign", "ambiguous", "overridden"])
def test_write_set_refuses_non_ours(tmp_path, bad):
    ud = tmp_path / ".config" / "systemd" / "user"; ud.mkdir(parents=True)
    if bad == "foreign":
        other = "/home/op/other-root"
        (ud / U.WEB_UNIT).write_text(
            U.render(U.WEB_UNIT, other, f"{other}/src/loraham-pi-control", f"{other}/venv/lhpc"))
    elif bad == "ambiguous":
        (ud / U.WEB_UNIT).write_text("[Service]\nExecStart=/usr/bin/other\n")
    else:  # overridden
        for k in U.ALL_UNITS:
            (ud / k).write_text(U.render(k, ROOT, CO, VENV))
        d = ud / f"{U.HELPER_UNIT}.d"; d.mkdir()
        (d / "x.conf").write_text("[Service]\nExecStart=/bin/evil\n")
    with pytest.raises(ValueError, match="not provably this deployment"):
        U.write_set(ud, ROOT)


# --- %h (systemd home specifier) same-root recognition -----------------------------------------

def _pct_units(tail="loraham-pi-control"):
    web = (f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT=%h/{tail}\n"
           f"ExecStart=%h/{tail}/venv/lhpc/bin/lhpc web --host 127.0.0.1 --port 8770\n")
    helper = (f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT=%h/{tail}\n"
              f"ExecStart=%h/{tail}/venv/lhpc/bin/lhpc self-update --run-service\n")
    path = f"[Path]\nPathExists=%h/{tail}/state/selfupdate.request\nUnit=lhpc-selfupdate.service\n"
    return {U.WEB_UNIT: web, U.HELPER_UNIT: helper, U.PATH_UNIT: path}


def test_verify_pct_h_same_root_is_modified_ours(tmp_path):
    import os
    home = os.path.expanduser("~"); root = f"{home}/loraham-pi-control"
    _, co, venv = U.deployment_paths(root)
    ud = _install(tmp_path, **_pct_units())
    for k in U.ALL_UNITS:
        assert U.verify(ud, k, root, co, venv) == U.MODIFIED_OURS, k


def test_verify_pct_h_different_root_is_foreign(tmp_path):
    import os
    home = os.path.expanduser("~"); root = f"{home}/loraham-pi-control"
    _, co, venv = U.deployment_paths(root)
    # %h/other-root expands to a DIFFERENT dir under the same home -> foreign
    ud = _install(tmp_path, **{U.WEB_UNIT: _pct_units("other-root")[U.WEB_UNIT],
                               U.PATH_UNIT: _pct_units("other-root")[U.PATH_UNIT]})
    assert U.verify(ud, U.WEB_UNIT, root, co, venv) == U.FOREIGN
    assert U.verify(ud, U.PATH_UNIT, root, co, venv) == U.FOREIGN


def test_integration_legacy_pct_h_no_path_is_incomplete_and_fixable(tmp_path):
    import os
    home = os.path.expanduser("~"); root = f"{home}/loraham-pi-control"
    u = _pct_units()
    ud = _install(tmp_path, **{U.WEB_UNIT: u[U.WEB_UNIT], U.HELPER_UNIT: u[U.HELPER_UNIT]})  # no .path
    integ = U.integration(ud, root)
    assert integ["status"] == "incomplete"
    assert all(v in (U.OK, U.MISSING, U.MODIFIED_OURS) for v in integ["per_unit"].values())


def test_write_set_overwrites_pct_h_modified_ours(tmp_path):
    import os
    home = os.path.expanduser("~"); root = f"{home}/loraham-pi-control"
    _, co, venv = U.deployment_paths(root)
    ud = _install(tmp_path, **{U.WEB_UNIT: _pct_units()[U.WEB_UNIT]})
    actions = dict(U.write_set(ud, root))
    assert actions[U.WEB_UNIT] == "restored"                 # %h modified_ours -> overwritten
    assert U.verify(ud, U.WEB_UNIT, root, co, venv) == U.OK  # now canonical literal
