"""Grouped dependency diagnosis (system / build / runtime) + the RadioLib build_requires
edge: reported truthfully with operator commands, enforced in update inclusion and
uninstall refcounting. LHPC never installs system packages itself."""

import time

from lhpc.core import source_registry
from lhpc.core.manifest import ManifestError, parse_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

import pytest


def _svc(tmp_path, cmdlines=None):
    return ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                             paths=Paths(runtime_root=tmp_path))


def _own(tmp_path, rel, comps):
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord(f"src/{rel}", "", "backfilled", "", time.time(), "",
                                       "", tuple(comps)))


def test_daemon_reports_radiolib_build_dep(tmp_path):
    svc = _svc(tmp_path)
    g = svc.deps_report("daemon")
    build = [d for d in g["build"] if "radiolib" in d.label]
    assert build and not build[0].satisfied            # not installed in the empty runtime
    assert "install" in build[0].install_cmd           # operator command offered
    (tmp_path / "src" / "RadioLib").mkdir(parents=True)
    g2 = _svc(tmp_path).deps_report("daemon")
    assert all(d.satisfied for d in g2["build"])       # present once the checkout exists


def test_system_deps_carry_operator_commands(tmp_path):
    g = _svc(tmp_path).deps_report("voice")
    assert g["system"] and all(not d.satisfied for d in g["system"])
    assert all(d.install_cmd.startswith("sudo apt install") for d in g["system"])
    assert all("not executed by LHPC" in d.note for d in g["system"])


def test_runtime_ordering_listed(tmp_path):
    g = _svc(tmp_path).deps_report("chat")
    assert any("loraham-daemon" in d.label for d in g["runtime"])


def test_doctor_itemizes_unmet_dependencies(tmp_path):
    res = _svc(tmp_path).doctor()
    assert res.ok
    blob = "\n".join(res.details)
    assert "[build] radiolib source checkout" in blob
    assert "not executed by LHPC" in blob


def test_doctor_ends_with_a_copyable_install_block(tmp_path):
    # A genuinely-missing dep (spi/gpio not granted) surfaces its grant command in a consolidated,
    # copyable "Install the missing dependencies:" block at the VERY END — after the per-dep lines.
    fake = FakeSystem(effective_group_names=frozenset(), configured_group_names=frozenset())
    details = ControllerService(system=fake.system,
                                paths=Paths(runtime_root=tmp_path)).doctor().details
    assert "Install the missing dependencies:" in details
    hi = details.index("Install the missing dependencies:")
    # placed after the components/conflicts tally (i.e. at the very end)
    assert hi > next(i for i, ln in enumerate(details) if "observed resource conflicts" in ln)
    block = details[hi + 1:]
    assert any("sudo usermod -aG spi,gpio $USER" in ln for ln in block)   # the grant command, copyable
    assert not any(ln.strip().startswith(("lhpc install", "lhpc build")) for ln in block)  # no actions


def test_every_controller_dep_has_a_copyable_install_command(tmp_path):
    # Coverage invariant: no controller dependency is ever shown "missing" as a dead end — it must carry
    # EITHER a copyable install command OR an explanatory note (for genuinely un-installable-by-command
    # deps like systemd, where `apt install systemd` would be nonsense advice).
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for grp in svc.controller_system_deps():
        for d in grp["deps"]:
            assert d["install"] or d.get("note"), f"{d['what']} has neither install command nor note"
    # venv deps target the running interpreter, not a bare `pip`
    import sys
    flat = [d for grp in svc.controller_system_deps() for d in grp["deps"]]
    flask = next(d for d in flat if d["what"] == "flask")
    assert flask["install"] == f"{sys.executable} -m pip install 'flask>=3,<4'"


def test_build_requires_manifest_validation():
    base = {
        "stack": [{
            "id": "s", "name": "s", "main": "a",
            "component": [
                {"id": "a", "name": "a", "kind": "service", "run": "true",
                 "readiness": "process", "build_requires": ["b"],
                 "source": {"path": "src/a"}},
                {"id": "b", "name": "b", "kind": "library",
                 "source": {"path": "src/b"}},
            ],
        }],
    }
    assert parse_manifest(base)
    bad = {**base}
    bad["stack"][0]["component"][0]["build_requires"] = ["nope"]
    with pytest.raises(ManifestError, match="build_requires unknown"):
        parse_manifest(bad)
    bad["stack"][0]["component"][0]["build_requires"] = ["a"]
    with pytest.raises(ManifestError, match="build_requires itself"):
        parse_manifest(bad)


def test_uninstall_radiolib_refused_while_daemon_installed(tmp_path):
    # daemon source present -> the build edge holds -> radiolib is a SHARED reference.
    (tmp_path / "src" / "loraham-daemon").mkdir(parents=True)
    (tmp_path / "src" / "RadioLib").mkdir(parents=True)
    _own(tmp_path, "RadioLib", ("radiolib",))
    svc = _svc(tmp_path)
    res = svc.uninstall("radiolib", apply=True)
    assert res.ok                                        # plan succeeds…
    assert (tmp_path / "src" / "RadioLib").exists()      # …but the checkout is KEPT (shared)
    assert any("kept" in d and "loraham-daemon" in d for d in res.details)


def test_uninstall_whole_daemon_stack_removes_radiolib(tmp_path):
    from lhpc.core.probes.backends import CommandResult
    (tmp_path / "src" / "loraham-daemon").mkdir(parents=True)
    (tmp_path / "src" / "RadioLib").mkdir(parents=True)
    _own(tmp_path, "loraham-daemon", ("loraham-daemon",))
    _own(tmp_path, "RadioLib", ("radiolib", "loraham-daemon"))
    import os as _os
    remotes = {_os.path.realpath(str(tmp_path / "src" / rel)): remote
               for rel, remote in (
                   ("loraham-daemon", "https://github.com/makrohard/LoRaHAM_Daemon.git"),
                   ("RadioLib", "https://github.com/jgromes/RadioLib"))}
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    real_run = svc._system.runner.run
    def run(argv, timeout, *a, **k):
        argv = list(argv)
        if (len(argv) >= 4 and argv[:2] == ["git", "-C"]
                and argv[3:] == ["config", "--get", "remote.origin.url"]
                and _os.path.realpath(argv[2]) in remotes):
            return CommandResult(0, remotes[_os.path.realpath(argv[2])] + "\n", "")
        return real_run(argv, timeout, *a, **k)
    svc._system.runner.run = run
    res = svc.uninstall("daemon", apply=True)
    assert res.ok, res.details
    assert not (tmp_path / "src" / "RadioLib").exists()
    assert not (tmp_path / "src" / "loraham-daemon").exists()


def test_stack_update_includes_radiolib_despite_optional(tmp_path):
    svc = _svc(tmp_path)
    plan = svc.update("daemon", apply=False)
    blob = "\n".join(plan.details)
    assert "radiolib" in blob                            # build_requires target included


def test_radiolib_built_state_is_honest(tmp_path):
    # RadioLib compiles via build_steps and declares its .a as `bin`, so is_built must reflect whether
    # build/libRadioLib.a actually exists (it used to be a permanent false-positive True, hiding the
    # need to build it and letting the daemon build fail with "RADIOLIB_DIR not usable").
    svc = _svc(tmp_path)
    radiolib = svc.stack("daemon").component("radiolib")
    (tmp_path / "src" / "RadioLib").mkdir(parents=True)          # checkout present, not built
    assert not svc.is_built(radiolib)
    assert "radiolib" in svc.unbuilt_components("daemon")        # honest "Build needed: RadioLib"
    art = tmp_path / "src" / "RadioLib" / "build" / "libRadioLib.a"
    art.parent.mkdir(parents=True)
    art.write_bytes(b"")
    assert svc.is_built(radiolib)                               # artifact present -> built
    assert "radiolib" not in svc.unbuilt_components("daemon")


def test_venv_component_built_state_uses_venv_bin_not_exec_name(tmp_path):
    # REGRESSION: meshcore-pi compiles an in-tree venv via build_steps; its exec_name is "python"
    # (a process-match NAME). _build_artifact must key on `bin` (.venv/bin/python), NOT exec_name —
    # else is_built checked a non-existent <src>/python and the stack read "not built" forever, so
    # `lhpc stack start meshcore` was blocked even right after a successful build.
    svc = _svc(tmp_path)
    mc = svc.stack("meshcore").component("meshcore-pi")
    src = tmp_path / "src" / "meshcore-pi"
    src.mkdir(parents=True)                                      # checkout present, venv not built
    assert not svc.is_built(mc)                                  # honest: no venv yet
    assert "meshcore-pi" in svc.unbuilt_components("meshcore")
    (src / "python").write_bytes(b"")                           # a bogus <src>/python must NOT count
    assert not svc.is_built(mc)                                  # exec_name is ignored (the fix)
    venv_py = src / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"")
    assert svc.is_built(mc)                                      # venv interpreter present -> built
    assert "meshcore-pi" not in svc.unbuilt_components("meshcore")


def test_unbuilt_build_deps_flags_radiolib_before_daemon(tmp_path):
    svc = _svc(tmp_path)
    (tmp_path / "src" / "RadioLib").mkdir(parents=True)          # provider checkout present, not built
    (tmp_path / "src" / "loraham-daemon").mkdir(parents=True)
    assert svc.unbuilt_build_deps("daemon") == ["radiolib"]
    art = tmp_path / "src" / "RadioLib" / "build" / "libRadioLib.a"
    art.parent.mkdir(parents=True)
    art.write_bytes(b"")
    assert svc.unbuilt_build_deps("daemon") == []               # built -> no longer a blocker


def test_build_dependency_banner_warns_radiolib_first(tmp_path):
    # After an update (fresh RadioLib checkout, no .a) the stack body shows an explicit "build the
    # dependency first" warning that links to the build section — not the generic build-needed note.
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    (tmp_path / "src" / "RadioLib").mkdir(parents=True)
    (tmp_path / "src" / "loraham-daemon").mkdir(parents=True)
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    body = app.test_client().get("/stacks?open=daemon").get_data(as_text=True)
    assert "not built — build this dependency first" in body and "radiolib" in body
    assert "comp=radiolib" in body and "#comp-radiolib" in body  # link opens RadioLib's own dep card


def test_library_shows_build_dependency_pill_not_optional(tmp_path):
    # A kind=library (RadioLib) is a BUILD dependency, not a skippable "optional" component — the
    # stack body must present it as such.
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    body = app.test_client().get("/stacks?open=daemon").get_data(as_text=True)
    assert "build dependency" in body
