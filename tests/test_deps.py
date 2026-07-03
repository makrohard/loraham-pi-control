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
        source_registry.RegistryRecord(f"src/{rel}", "", "legacy", "", time.time(), "",
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
