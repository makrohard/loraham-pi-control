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
    # (a process-match NAME), so is_built must NOT key on a bogus <src>/python. It now gates on the
    # `build_marker` (written ONLY after the LAST build step succeeds), NOT the venv interpreter —
    # because the interpreter exists after step 1 (python -m venv), long before the pip installs
    # finish, so a build killed mid-pip would otherwise read "built" and die at start.
    svc = _svc(tmp_path)
    mc = svc.stack("meshcore").component("meshcore-pi")
    src = tmp_path / "src" / "meshcore-pi"
    src.mkdir(parents=True)                                      # checkout present, venv not built
    assert not svc.is_built(mc)                                  # honest: no venv yet
    assert "meshcore-pi" in svc.unbuilt_components("meshcore")
    (src / "python").write_bytes(b"")                           # a bogus <src>/python must NOT count
    assert not svc.is_built(mc)                                  # exec_name is ignored
    venv_py = src / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"")
    assert not svc.is_built(mc)                                  # interpreter alone is NOT enough (marker gates)
    assert "meshcore-pi" in svc.unbuilt_components("meshcore")
    (src / mc.build_marker).write_text("lhpc build complete\n")                   # completion marker -> fully built
    assert svc.is_built(mc)
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


def test_meshtasticd_and_spi_copyboxes_are_executable(tmp_path):
    # Field-verified (Trixie lite): meshtasticd is in NO distro repo, so the copybox must add the
    # Meshtastic OBS apt repo + signing key before installing (the bare `apt install -y meshtasticd`
    # fails); the SPI box must be a runnable command, not prose. Requires absent under the bare FakeSystem.
    svc = _svc(tmp_path)
    deps = svc.system_deps("meshtastic")
    mesh = next(d for d in deps if "meshtasticd" in d["what"])
    assert "download.opensuse.org" in mesh["install"]          # adds the OBS repo…
    assert mesh["install"].rstrip().endswith("apt install -y meshtasticd")   # …then installs
    spi = next(d for d in deps if d["what"].startswith("SPI device"))
    assert spi["install"].startswith("printf")                 # a command, not prose
    assert "/boot/firmware/config.txt" in spi["install"] and "Enable SPI" not in spi["install"]


def test_no_dependency_surface_ever_advises_apt_install_systemd(tmp_path):
    # systemd is not installable by package: `apt install systemd` is nonsense on a non-systemd host.
    # No dependency surface may emit it, and the systemd controller-dep must name the real fallback.
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    sysd = next(d for grp in svc.controller_system_deps() for d in grp["deps"]
                if "systemd" in d["what"].lower())
    assert sysd["install"] == ""                               # never a copybox
    assert "lhpc web" in sysd["note"] and "self-update --repair-integration" in sysd["note"]
    # Every copybox INSTALL command across all dep surfaces — none may install systemd (a NOTE may
    # still mention "apt install systemd is not the fix"; that is guidance, not a command).
    installs = [d["install"] for grp in svc.controller_system_deps() for d in grp["deps"]]
    installs += [d.get("install", "") for sec in svc.dependency_overview()["sections"] for d in sec["deps"]]
    for s in svc.stacks():
        installs += [d["install"] for d in svc.system_deps(s.id)]
        installs += [d["install"] for lst in svc.install_dep_gate(s.id).values() for d in lst]
    offenders = [c for c in installs if "systemd" in (c or "")]
    assert not offenders, offenders


# --- Item 5: `lhpc deps --script` bootstrap generator -------------------------------------------

def test_render_bootstrap_merges_and_dedups_apt(tmp_path):
    from lhpc.core import deps
    script = deps.render_bootstrap_script([
        "sudo apt install -y cmake",
        "sudo apt install socat",                 # no -y -> still merged, non-interactively
        "sudo apt-get install -y cmake",          # dup pkg -> deduped
        "sudo apt install -y --no-install-recommends libssl-dev",   # flag dropped
    ], revision="deadbeef")
    # exactly one merged, non-interactive apt install line; packages sorted + unique; no flags leaked
    assert script.count("apt-get install -y") == 1
    body = script.split("apt-get install -y", 1)[1]
    assert "cmake" in body and body.count("cmake") == 1
    assert "socat" in body and "libssl-dev" in body
    assert "--no-install-recommends" not in script
    assert "deadbeef" in script                   # revision in header
    import subprocess
    p = subprocess.run(["bash", "-n", "-c", script], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_render_bootstrap_preserves_multiline_blocks_verbatim(tmp_path):
    from lhpc.core import deps
    obs = ("echo deb ... | sudo tee /etc/apt/sources.list.d/x.list\n"
           "sudo apt update\nsudo apt install -y meshtasticd")
    script = deps.render_bootstrap_script(["sudo apt install -y git", obs, obs], revision="r")
    # a multi-line block is emitted verbatim (never merged out of order) and deduplicated
    assert script.count("sources.list.d/x.list") == 1
    assert "sudo apt install -y meshtasticd" in script       # kept AFTER the repo add, not merged up


def test_deps_script_service_has_every_category_and_no_venv_pip(tmp_path):
    svc = _svc(tmp_path)
    script = svc.deps_script()
    # every declared category is present
    assert "apt-get install -y" in script                    # merged apt packages
    assert "sources.list.d" in script or "opensuse" in script  # OBS repo block
    assert "dtparam=spi=on" in script                        # SPI / config.txt
    assert "usermod -aG" in script                           # group grants
    assert "systemctl disable --now meshtasticd" in script   # system-meshtasticd disable
    # NOT a venv-level pip install, and no leaked absolute dev path
    assert "-m pip install" not in script
    assert "/home/" not in script
    import subprocess
    assert subprocess.run(["bash", "-n", "-c", script], capture_output=True).returncode == 0


def test_shipped_bootstrap_snapshot_is_up_to_date(tmp_path):
    # The committed bootstrap-deps.sh must equal what `lhpc deps --script` renders now (regenerate it
    # when the declared dependencies change).
    import pathlib
    svc = _svc(tmp_path)
    shipped = pathlib.Path("bootstrap-deps.sh")
    assert shipped.exists(), "bootstrap-deps.sh snapshot missing — run `lhpc deps --script > bootstrap-deps.sh`"
    assert shipped.read_text() == svc.deps_script(), \
        "bootstrap-deps.sh is stale — regenerate with `lhpc deps --script > bootstrap-deps.sh`"


def test_bootstrap_script_never_advises_apt_install_systemd(tmp_path):
    # Same guardrail as the other dep surfaces: never advise removing/installing systemd via apt.
    assert "install systemd" not in _svc(tmp_path).deps_script()


def test_qemu_and_pio_are_managed_not_copyboxes(tmp_path):
    # Item 2/4: qemu + PlatformIO are provisioned INTO the runtime root by the managed setup step, so the
    # pre-clone bootstrap no longer carries the $HOME qemu download or the pipx copybox. libslirp0 (a
    # genuine apt dependency) stays.
    script = _svc(tmp_path).deps_script()
    assert "espressif/qemu/releases" not in script and "qemu-xtensa" not in script
    assert "pipx" not in script
    assert "libslirp0" in script


def test_managed_tool_requires_verify_in_root_artifacts(tmp_path):
    # Item 2: the qemu + pio requires verify the IN-ROOT artifact via a {runtime}-substituted check_file
    # (or the PATH `cmd` override); neither carries an install copybox anymore.
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() for c in s.components if c.id == "meshcom-qemu")
    reqs = {r.cmd: r for r in comp.requires if r.cmd in ("qemu-system-xtensa", "pio")}
    assert "{runtime}/build/tool-cache/qemu-xtensa" in reqs["qemu-system-xtensa"].check_file
    assert "{runtime}/build/tools/platformio" in reqs["pio"].check_file
    assert reqs["qemu-system-xtensa"].install == "" and reqs["pio"].install == ""   # managed, no copybox
    # the {runtime} token resolves to the runtime root (so the pre-check reads the real in-root path)
    life = svc._lifecycle()
    resolved = life._resolve_req_path(reqs["qemu-system-xtensa"].check_file)
    assert str(tmp_path) in resolved and "{runtime}" not in resolved


def test_qemu_param_default_points_at_the_in_root_binary(tmp_path):
    # Item 2a: the managed run passes --qemu <in-root binary> — the param default carries the
    # {runtime}-templated tool-cache path (expand_argv substitutes {runtime} at launch).
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() for c in s.components if c.id == "meshcom-qemu")
    qp = next(p for p in comp.run_params if p.name == "qemu")
    assert qp.arg == "--qemu"
    assert "{runtime}/build/tool-cache/qemu-xtensa" in qp.default and qp.default.endswith("qemu-system-xtensa")


def test_build_steps_provision_managed_tools_in_root(tmp_path):
    # Item 2: the build's setup steps provision BOTH tools INSIDE the runtime root — a PlatformIO venv
    # and the sha256-verified qemu — and hand the managed pio to the build scripts by absolute path.
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() for c in s.components if c.id == "meshcom-qemu")
    argvs = [" ".join(str(t) for t in st.get("argv", [])) for st in comp.build_steps]
    assert any("venv" in a and "build/tools/platformio" in a for a in argvs)                    # managed pio venv
    assert any("fetch-qemu.sh" in a and "build/tool-cache/qemu-xtensa" in a for a in argvs)      # managed qemu
    envs = [st.get("env", {}) for st in comp.build_steps]
    assert any(e.get("PIO", "").endswith("platformio/.venv/bin/pio") for e in envs)              # PIO by abs path
