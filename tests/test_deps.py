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


def test_meshtastic_and_spi_copyboxes_are_executable(tmp_path):
    # meshtasticd is BUILT from the managed source now (server-only, upstream env `native`), so no
    # dependency surface may add a third-party apt repo or install the OBS package — its Depends are
    # what dragged a desktop stack onto a headless image. What IS declared: the build/runtime
    # libraries, as one runnable apt command. The SPI box must be a command, not prose.
    svc = _svc(tmp_path)
    deps = svc.system_deps("meshtastic")
    for d in deps:
        assert "opensuse" not in (d["install"] or "")
        assert "install -y meshtasticd" not in (d["install"] or "")
    libs = next(d for d in deps if "libyaml-cpp-dev" in (d["install"] or ""))
    assert libs["install"].startswith("sudo apt install -y")
    for forbidden in ("libsdl", "libx11", "xkbcommon", "libinput", "libpulse"):
        assert forbidden not in libs["install"]
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
    # into the PACKAGE LIST. The generator itself installs with --no-install-recommends (Recommends
    # are what dragged a desktop stack onto a headless image), so the flag appears on the install
    # line by design — but never as a token declared by a require.
    assert script.count("apt-get install -y --no-install-recommends") == 1
    body = script.split("apt-get install -y --no-install-recommends", 1)[1]
    assert "cmake" in body and body.count("cmake") == 1
    assert "socat" in body and "libssl-dev" in body
    pkg_block = body.split("\n\n", 1)[0]
    assert "--no-install-recommends" not in pkg_block
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
    # NO third-party apt repository: meshtasticd is built from the managed source now, so nothing
    # adds a repo or installs the OBS package (whose Depends dragged in a desktop stack).
    assert "sources.list.d" not in script and "opensuse" not in script
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
    # and the source-built (headless, link-gated) qemu — and hand the managed pio to the build scripts
    # by absolute path.
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() for c in s.components if c.id == "meshcom-qemu")
    argvs = [" ".join(str(t) for t in st.get("argv", [])) for st in comp.build_steps]
    assert any("venv" in a and "build/tools/platformio" in a for a in argvs)                    # managed pio venv
    assert any("build-qemu.sh" in a and "build/tool-cache/qemu-xtensa" in a for a in argvs)     # managed qemu (source build)
    envs = [st.get("env", {}) for st in comp.build_steps]
    assert any(e.get("PIO", "").endswith("platformio/.venv/bin/pio") for e in envs)              # PIO by abs path


# --- Item K: GUI-only dependency taxonomy ----------------------------------------------------------
# This box HAS GTK and tkinter, so absence is SIMULATED (FakeSystem / monkeypatched find_spec) —
# never inferred from the host, which would make these tests pass for the wrong reason.

def _scopes(tmp_path):
    return _svc(tmp_path)._declared_dep_scopes()


def test_gui_scope_is_exactly_what_the_manifest_declares(tmp_path):
    core, gui = _scopes(tmp_path)
    # EXACT invariants, not a denylist: a denylist rots and proves nothing about new deps.
    # The opt-in scope is GUI toolkits PLUS the Voice-only audio libraries (Voice is skipped by
    # default on a headless rig, so its private deps have no business in every bootstrap).
    assert sorted(gui) == ["sudo apt install -y libasound2-dev",
                           "sudo apt install -y libcodec2-dev",
                           "sudo apt install -y libgtk-3-dev",
                           "sudo apt install -y python3-tk"]
    assert not set(core) & set(gui)                 # a command lives in exactly one scope
    assert not any("gtk" in c or "python3-tk" in c or "asound" in c or "codec2" in c for c in core)
    assert any("ncurses" in c for c in core)        # shared with chat -> core wins


def test_default_script_body_is_exactly_the_core_scope(tmp_path):
    svc = _svc(tmp_path)
    core, gui = svc._declared_dep_scopes()
    script = svc.deps_script()
    # Drop the --dry-run guard first: it NAMES GUI packages in its denylist regex (to refuse them),
    # which must not be confused with installing them.
    body = script.split("# --- --dry-run", 1)[0] + script.split("exit 0\nfi", 1)[-1]
    head, _, tail = body.partition("--- GUI-only dependencies")
    for cmd in gui:                                  # GUI packages ONLY below the guard
        pkg = cmd.split()[-1]
        assert pkg not in head
        assert pkg in tail
    assert 'if [ -n "$WITH_GUI" ]' in tail


def test_moving_a_dep_between_scopes_changes_the_revision(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    before = svc.deps_script()
    core, gui = svc._declared_dep_scopes()
    # Same command SET, different scope: a revision that hashed commands alone would not move.
    monkeypatch.setattr(type(svc), "_declared_dep_scopes",
                        lambda self: (sorted(core + gui), []))
    after = svc.deps_script()
    def _rev(s):
        return next(ln for ln in s.splitlines() if "dependency revision" in ln)
    assert _rev(before) != _rev(after)


def test_core_declaration_wins_over_a_gui_declaration(tmp_path, monkeypatch):
    """AND-merge: one non-GUI declaration keeps the command in the DEFAULT bootstrap, once."""
    svc = _svc(tmp_path)
    stacks = svc.stacks()
    dup = None
    for s in stacks:
        for c in s.components:
            for r in c.requires:
                if getattr(r, "gui", False):
                    dup = r
                    break
    assert dup is not None
    monkeypatch.setattr(type(dup), "gui", property(lambda self: False), raising=False)
    core, gui = svc._declared_dep_scopes()
    assert dup.install in core
    assert dup.install not in gui
    assert core.count(dup.install) == 1


def test_module_probe_uses_find_spec_and_is_honest(tmp_path, monkeypatch):
    import importlib.util

    from lhpc.core import lifecycle
    assert lifecycle.module_present("tkinter") is True
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert lifecycle.module_present("tkinter") is False
    def _boom(name):
        raise ImportError("no parent")
    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    assert lifecycle.module_present("tkinter") is False


def test_dotted_module_names_are_rejected_at_parse():
    # find_spec("a.b") IMPORTS a — that would break the no-side-effect GET guarantee.
    from lhpc.core.manifest import _require_module
    for bad in ("a.b", "os.path", "", " x-y "):
        if bad.strip():
            with pytest.raises(ManifestError):
                _require_module(bad, "c")
    assert _require_module("tkinter", "c") == "tkinter"


def test_gui_deps_are_warn_not_block_in_the_install_gate(tmp_path):
    svc = _svc(tmp_path)                             # FakeSystem: no gtk headers
    gate = svc.install_dep_gate("voice")
    labels = lambda ds: [d["what"] for d in ds]
    assert any("GTK" in w for w in labels(gate["warn"]))
    assert not any("GTK" in w for w in labels(gate["block"]))
    # still VISIBLE — a GUI dep is opt-in, not hidden
    assert any("GTK" in d["what"] for d in svc.missing_system_deps("voice"))


# --- Item K: component-scoped skip (GUI absent) ----------------------------------------------------

def _no_tkinter(monkeypatch):
    """Simulate a headless box with no python3-tk (this box HAS it)."""
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: None if name == "tkinter" else real(name))


def test_meshcore_keeps_working_headless_only_nodegui_is_skipped(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _no_tkinter(monkeypatch)
    st = svc.stack("meshcore")
    assert svc.gui_unavailable_components(st) == ("meshcore-nodegui",)
    # OPTIONAL GUI component -> the STACK is not skipped: the CLI still works.
    assert svc.gui_skipped_stack(st) is False
    work = next(w for s, w in svc._auto_install_scope() if s.id == "meshcore")
    assert work.skipped == ("meshcore-nodegui",)
    ids = {c.id for c in work.source} | {c.id for c in work.build} | {c.id for c in work.test}
    assert "meshcore-nodegui" not in ids
    assert "meshcore-cli" in ids or "meshcore-pi" in ids       # headless components survive


def test_voice_stack_is_skipped_whole_when_gtk_is_absent(tmp_path):
    svc = _svc(tmp_path)                              # FakeSystem: no gtk headers
    st = svc.stack("voice")
    assert svc.gui_skipped_stack(st) is True
    scope = svc._auto_install_scope()
    assert len(scope) == len(svc.stacks())            # every stack still gets a ROW
    work = next(w for s, w in scope if s.id == "voice")
    assert "loraham-voice" in work.skipped


def _tree(root):
    """Every path under `root` with its mtime+size — a real zero-mutation fingerprint."""
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_direct_build_voice_refuses_before_running_a_step(tmp_path):
    svc = _svc(tmp_path)
    before = _tree(tmp_path)
    r = svc.build("voice", apply=True)
    assert r.ok is False
    assert "GUI toolkit" in r.summary
    assert any("headless-safe default" in d for d in r.details)
    # Refused during PREFLIGHT: not one byte of state may have been written — no build marker, no
    # log, no lock file. (The previous `... or True` assertion could never fail.)
    assert _tree(tmp_path) == before


def test_direct_build_meshcore_skips_nodegui_and_continues(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _no_tkinter(monkeypatch)
    r = svc.build("meshcore", apply=False)
    assert r.ok is True
    assert not any("[build] meshcore-nodegui" in d for d in r.details)
    assert any("[skip] meshcore-nodegui" in d for d in r.details)


def test_skipped_is_a_valid_marker_status_and_not_a_failure(tmp_path):
    from lhpc.core import auto_install as ai
    assert "skipped" in ai.STACK_STATUSES


def test_direct_build_of_a_skipped_component_is_typed_no_work_not_success(tmp_path, monkeypatch):
    """`lhpc build meshcore-nodegui` on a headless box: every requested component is skipped, so the
    result must be an explicit no-work/skipped outcome — never "succeeded"/"built" — and it must not
    execute a build step, mutate a marker or take a source lock."""
    svc = _svc(tmp_path)
    _no_tkinter(monkeypatch)
    before = _tree(tmp_path)
    r = svc.build("meshcore-nodegui", apply=True)
    assert "succeed" not in r.summary.lower() and "built" not in r.summary.lower()
    assert "Nothing to build" in r.summary
    assert r.data.get("skipped") == ["meshcore-nodegui"] and r.data.get("built") == 0
    assert _tree(tmp_path) == before                      # zero mutation


def test_dry_run_build_of_a_skipped_component_reports_no_work(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _no_tkinter(monkeypatch)
    r = svc.build("meshcore-nodegui", apply=False)
    assert "Nothing to build" in r.summary
    assert any("[skip] meshcore-nodegui" in d for d in r.details)


def test_partial_build_reports_succeeded_with_gui_skips(tmp_path, monkeypatch):
    """Headless work remains -> it is built AND the skip is preserved in the result."""
    svc = _svc(tmp_path)
    _no_tkinter(monkeypatch)
    calls = []
    life = svc._lifecycle()
    class L:
        def build(self, comp, **kw):
            calls.append(comp.id)
            class R:
                ok, state, returncode, log_path, tail = True, type("S", (), {"value": "ok"})(), 0, "l", []
                cancelled = unsafe = False
                unsafe_scope = session_ident = ""
            return R()
        def __getattr__(self, n):
            return getattr(life, n)
    monkeypatch.setattr(svc, "_lifecycle", lambda: L())
    r = svc.build("meshcore", apply=True)
    assert r.ok is True
    assert "succeeded with GUI skips" in r.summary
    assert "meshcore-nodegui" in r.summary
    assert "meshcore-nodegui" not in calls                 # never built
    assert r.data.get("skipped") == ["meshcore-nodegui"]


# --- order-independent dedup + disjoint counters ---------------------------------------------------

def _synth(tmp_path, decls):
    """A one-stack manifest whose components declare the SAME install command with the given
    (gui, optional) flags, in the given order."""
    comps = []
    for i, (gui, optional) in enumerate(decls):
        comps.append(f'''
[[stack.component]]
id = "c{i}"
name = "C{i}"
kind = "service"
purpose = "p"
optional = {str(optional).lower()}
  [[stack.component.require]]
  gui = {str(gui).lower()}
  check_file = "/nonexistent/shared.h"
  install = "sudo apt install -y sharedpkg"
  note = "Shared header"
''')
    import tomllib
    text = ('[[stack]]\nid = "syn"\nname = "Syn"\nsummary = "s"\nmain = "c0"\n' + "".join(comps))
    return parse_manifest(tomllib.loads(text))


@pytest.mark.parametrize("order", [
    [(True, False), (False, False)],       # GUI declared FIRST
    [(False, False), (True, False)],       # core declared FIRST
])
def test_core_declaration_wins_regardless_of_order(tmp_path, order):
    svc = _svc(tmp_path)
    stacks = _synth(tmp_path, order)
    monkey = {s.id: s for s in stacks}
    svc.stack = lambda t, _m=monkey: _m.get(t)                        # type: ignore[assignment]
    deps = svc.system_deps("syn")
    assert len(deps) == 1                                             # present ONCE
    assert deps[0]["gui"] is False                                    # core wins
    assert deps[0]["mandatory"] is True                               # normal semantics restored


def test_all_gui_declarations_stay_gui_only(tmp_path):
    svc = _svc(tmp_path)
    stacks = _synth(tmp_path, [(True, False), (True, True)])
    monkey = {s.id: s for s in stacks}
    svc.stack = lambda t, _m=monkey: _m.get(t)                        # type: ignore[assignment]
    deps = svc.system_deps("syn")
    assert len(deps) == 1
    assert deps[0]["gui"] is True
    assert deps[0]["mandatory"] is False                              # GUI-only is never mandatory


def test_optional_and_gui_counters_are_disjoint(tmp_path):
    svc = _svc(tmp_path)
    ov = svc.dependency_overview()
    for sec in ov["sections"]:
        for d in sec["deps"]:
            if d.get("gui"):
                assert d["mandatory"] is False
    gui = [d for sec in ov["sections"] for d in sec["deps"]
           if not d["satisfied"] and d.get("gui")]
    opt = [d for sec in ov["sections"] for d in sec["deps"]
           if not d["satisfied"] and not d["mandatory"] and not d.get("gui")
           and not d.get("restart_pending")]
    assert ov["gui_missing"] == len(gui)
    assert ov["optional_missing"] == len(opt)
    assert not [d for d in gui if d in opt]                           # no dep counted twice


# --- managed server-only Meshtastic ---------------------------------------------------------------
# meshtasticd is BUILT from a pinned upstream checkout with upstream's `native` environment, instead
# of installed from the OBS package (built `native-tft`, so it links X11/libinput/xkbcommon and its
# Depends drag SDL2 -> PulseAudio/Wayland/Mesa/LLVM onto a headless rig).

def _mesh(svc):
    return svc.stack("meshtastic").components[0]


def test_meshtastic_is_a_normal_managed_source_with_the_usual_selectors(tmp_path):
    svc = _svc(tmp_path)
    c = _mesh(svc)
    assert c.source is not None and c.source.path == "src/meshtastic-firmware"
    assert len(c.source.pin_commit) == 40                       # pinned by FULL sha
    assert c.source.remote.endswith("meshtastic/firmware.git") and c.source.branch
    # No bespoke update path: the ordinary selectors plan normally.
    for sel in ("pinned", "dev"):
        r = svc.install("meshtastic", apply=False, source=sel)
        assert r.ok and any("meshtastic-firmware" in d for d in r.details), sel


def test_meshtastic_builds_the_server_only_env_and_never_native_tft(tmp_path):
    c = _mesh(_svc(tmp_path))
    steps = c.build_steps
    argvs = [" ".join(s.get("argv", [])) for s in steps]
    blob = "\n".join(argvs)
    assert "--environment native" in blob
    assert "native-tft" not in blob                             # the X11/TFT build, never built here
    # The link gate is a BUILD STEP: a binary that links a display stack must not be publishable.
    assert any("meshtastic-link-gate.sh" in a for a in argvs)
    assert any("meshtastic-web-assets.sh" in a for a in argvs)
    # Serialised compile: a parallel native build is what OOMs a 512 MB Zero 2W.
    run_step = next(s for s in steps if "run" in s.get("argv", []) and "--environment" in s["argv"])
    env = dict(run_step.get("env") or {})
    assert env.get("PLATFORMIO_RUN_JOBS") == "1"
    assert env.get("PLATFORMIO_CORE_DIR") == "{runtime}/build/tools/platformio/core"
    # The web-asset step carries the pin COMMIT as well as the hash, and that commit must be the
    # source pin — a bumped pin with a stale argument would silently drop pinned verification.
    web = next(s for s in steps if "meshtastic-web-assets.sh" in " ".join(s.get("argv", [])))
    assert web["argv"][-2] == c.source.pin_commit


def test_meshtastic_runs_the_runtime_owned_binary_and_web_root(tmp_path):
    svc = _svc(tmp_path)
    c = _mesh(svc)
    assert c.run_argv[0] == "{runtime}/build/tools/meshtasticd/meshtasticd"
    assert "/usr/bin/meshtasticd" not in " ".join(c.run_argv)
    assert c.bin == "build/tools/meshtasticd/meshtasticd"       # the server IS the artifact
    root = next(p for p in c.config_file.params if p.key == "RootPath")
    assert root.default == "{runtime}/build/tools/meshtasticd/web"   # never /usr/share


def test_meshtastic_declares_no_graphical_or_audio_dependency(tmp_path):
    joined = " ".join((r.install or "") + " " + (r.check_file or "")
                      for r in _mesh(_svc(tmp_path)).requires)
    for forbidden in ("libsdl", "libx11", "libwayland", "mesa", "libllvm",
                      "libpulse", "libinput", "libxkbcommon", "libgtk"):
        assert forbidden not in joined.lower(), forbidden


def test_source_update_leaves_the_stack_needing_a_rebuild(tmp_path):
    # The completion marker is written only after every build step; the artifact lives under the
    # runtime root, so a replaced checkout cannot read as built until it is rebuilt.
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT
    svc = _svc(tmp_path)
    c = _mesh(svc)
    assert c.build_marker                                       # strict completion marker declared
    assert svc.is_built(c) is False                             # nothing built yet
    src = tmp_path / "src" / "meshtastic-firmware"
    src.mkdir(parents=True)
    marker = src / c.build_marker
    marker.write_text(BUILD_MARKER_TEXT)
    assert svc.is_built(_mesh(_svc(tmp_path))) is True
    # An update REPLACES the checkout, taking the source-local marker with it.
    marker.unlink()
    assert svc.is_built(_mesh(_svc(tmp_path))) is False         # -> "Build required" again


def test_partial_build_does_not_read_as_built(tmp_path):
    # The binary alone is NOT the completion signal: a run that installed meshtasticd but died
    # before the web assets were provisioned would otherwise start and serve a missing UI.
    svc = _svc(tmp_path)
    art = tmp_path / "build" / "tools" / "meshtasticd" / "meshtasticd"
    art.parent.mkdir(parents=True)
    art.write_text("#!/bin/true\n")
    (tmp_path / "src" / "meshtastic-firmware").mkdir(parents=True)
    assert svc.is_built(_mesh(_svc(tmp_path))) is False


# --- shipped build helpers: web assets follow the source; the link gate is fail-closed ------------
# Driven offline via LHPC_MESHTASTIC_WEB_TARBALL / fake readelf+ldd, so no network or real binary.

def _scripts():
    from lhpc.core.assets import asset_path
    return asset_path("scripts")


def _fake_tar(tmp_path, name="index.html", body="<html>ok</html>"):
    import subprocess
    import tarfile
    stage = tmp_path / "stage"; stage.mkdir()
    (stage / name).write_text(body)
    subprocess.run(["gzip", str(stage / name)], check=True)      # release ships gzipped members
    tar = tmp_path / "build.tar"
    with tarfile.open(tar, "w") as tf:
        tf.add(stage / f"{name}.gz", arcname=f"{name}.gz")
    import hashlib
    return tar, hashlib.sha256(tar.read_bytes()).hexdigest()


def _checkout(tmp_path, version="2.6.7"):
    src = tmp_path / "src"; (src / "bin").mkdir(parents=True)
    (src / "bin" / "web.version").write_text(version + "\n")
    return src


def _git_init(src):
    """Make `src` a real checkout and return its HEAD sha (the helper reads it to pick its mode)."""
    import subprocess
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@e", "PATH": "/usr/bin:/bin", "HOME": str(src)}
    run = lambda *a: subprocess.run(["git", "-C", str(src), *a], env=env, check=True,
                                    capture_output=True)     # noqa: E731
    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "t")
    out = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _run_web(tmp_path, src, dest, tar, pin_commit="", pinned_sha=""):
    import os
    import subprocess
    return subprocess.run(
        ["bash", str(_scripts() / "meshtastic-web-assets.sh"), str(src), str(dest),
         pin_commit, pinned_sha],
        env={**os.environ, "LHPC_MESHTASTIC_WEB_TARBALL": str(tar)},
        capture_output=True, text=True, timeout=60)


def test_web_assets_enforce_the_pinned_hash_when_head_is_the_pin(tmp_path):
    # PINNED HEAD: the declared digest describes exactly this revision, so it is asserted.
    src, dest = _checkout(tmp_path), tmp_path / "web"
    head = _git_init(src)
    tar, digest = _fake_tar(tmp_path)
    r = _run_web(tmp_path, src, dest, tar, pin_commit=head, pinned_sha=digest)
    assert r.returncode == 0, r.stderr
    assert "enforcing the declared web asset hash" in r.stdout
    assert (dest / "index.html").read_text() == "<html>ok</html>"      # unpacked AND gunzipped
    prov = (dest.parent / "web.provenance").read_text()
    assert "web_version=2.6.7" in prov and f"web_sha256={digest}" in prov and "pinned=yes" in prov
    assert f"firmware_rev={head}" in prov


def test_web_assets_pinned_mismatch_fails_and_keeps_the_old_ui(tmp_path):
    src, dest = _checkout(tmp_path), tmp_path / "web"
    head = _git_init(src)
    dest.mkdir(); (dest / "index.html").write_text("PREVIOUS")
    tar, _digest = _fake_tar(tmp_path)
    r = _run_web(tmp_path, src, dest, tar, pin_commit=head, pinned_sha="0" * 64)
    assert r.returncode == 3
    assert "checksum mismatch" in r.stderr
    assert (dest / "index.html").read_text() == "PREVIOUS"            # never half-swapped


def test_web_assets_do_not_assert_the_pinned_hash_on_a_non_pinned_head(tmp_path):
    # DEV/STABLE: HEAD is NOT the pin, so the pinned digest describes a DIFFERENT revision and must
    # not be enforced — the observed hash is recorded instead. (Passing it unconditionally would
    # fail every dev build.)
    src, dest = _checkout(tmp_path, version="2.7.1"), tmp_path / "web"
    head = _git_init(src)
    tar, digest = _fake_tar(tmp_path)
    r = _run_web(tmp_path, src, dest, tar, pin_commit="b" * 40, pinned_sha="0" * 64)
    assert r.returncode == 0, r.stderr                                # the stale pin is NOT applied
    assert "NOT the pinned revision" in r.stdout
    prov = (dest.parent / "web.provenance").read_text()
    assert "web_version=2.7.1" in prov and f"web_sha256={digest}" in prov and "pinned=no" in prov
    assert f"firmware_rev={head}" in prov


def test_web_assets_follow_the_checkout_not_a_hardcoded_version(tmp_path):
    # The version comes from THIS checkout; a checkout without it cannot silently reuse anything.
    src, dest = tmp_path / "src", tmp_path / "web"
    (src / "bin").mkdir(parents=True)
    tar, digest = _fake_tar(tmp_path)
    r = _run_web(tmp_path, src, dest, tar, pin_commit="a" * 40, pinned_sha=digest)
    assert r.returncode == 2 and "web.version" in r.stderr
    assert not dest.exists()


def _run_gate(tmp_path, needed, ldd_out=None):
    """Run the link gate against a fake readelf/ldd reporting `needed`."""
    import os
    import subprocess
    fb = tmp_path / "fb"; fb.mkdir(exist_ok=True)
    lines = "".join(f"Shared library: [{n}]\n" for n in needed)
    (fb / "readelf").write_text(f"#!/usr/bin/env bash\ncat <<'E'\n{lines}E\n")
    (fb / "ldd").write_text("#!/usr/bin/env bash\ncat <<'E'\n"
                            + "".join(f"{n} => /lib/{n} (0x0)\n" for n in (ldd_out or needed))
                            + "E\n")
    for f in ("readelf", "ldd"):
        (fb / f).chmod(0o755)
    binary = tmp_path / "meshtasticd"; binary.write_text("x")
    return subprocess.run(["bash", str(_scripts() / "meshtastic-link-gate.sh"), str(binary)],
                          env={**os.environ, "PATH": f"{fb}:/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=60)


def test_link_gate_passes_a_server_only_binary(tmp_path):
    r = _run_gate(tmp_path, ["libyaml-cpp.so.0.8", "libuv.so.1", "libgpiod.so.3",
                             "libulfius.so.2.7", "libc.so.6"])
    assert r.returncode == 0, r.stderr
    assert "link gate OK" in r.stdout


@pytest.mark.parametrize("lib", ["libX11.so.6", "libSDL2-2.0.so.0", "libinput.so.10",
                                 "libxkbcommon.so.0", "libpulse.so.0", "libwayland-client.so.0"])
def test_link_gate_refuses_a_display_or_audio_dependency(tmp_path, lib):
    r = _run_gate(tmp_path, ["libc.so.6", lib])
    assert r.returncode == 3
    assert "forbidden libraries" in r.stderr and lib in r.stderr


def test_link_gate_catches_a_transitively_pulled_library(tmp_path):
    # Clean NEEDED, dirty closure: still fatal — the process would load it either way.
    r = _run_gate(tmp_path, ["libc.so.6"], ldd_out=["libc.so.6", "libpulse.so.0"])
    assert r.returncode == 3
    assert "transitive closure" in r.stderr


# --- generalized link gate (now also gates the source-built qemu-system-xtensa) --------------------
def _run_gate_ex(tmp_path, needed, ldd_out=None, label=None, readelf_fail=False, notfound=()):
    """Flexible gate driver: optional label arg, a forced readelf failure, `=> not found` closure lines."""
    import os
    import subprocess
    fb = tmp_path / "fbx"; fb.mkdir(exist_ok=True)
    if readelf_fail:
        (fb / "readelf").write_text("#!/usr/bin/env bash\necho 'readelf: Error: Not an ELF file' >&2\nexit 1\n")
    else:
        lines = "".join(f"Shared library: [{n}]\n" for n in needed)
        (fb / "readelf").write_text(f"#!/usr/bin/env bash\ncat <<'E'\n{lines}E\n")
    ldd_lines = "".join(f"{n} => /lib/{n} (0x0)\n" for n in (ldd_out or needed))
    ldd_lines += "".join(f"{n} => not found\n" for n in notfound)
    (fb / "ldd").write_text("#!/usr/bin/env bash\ncat <<'E'\n" + ldd_lines + "E\n")
    for f in ("readelf", "ldd"):
        (fb / f).chmod(0o755)
    binary = tmp_path / "artifact"; binary.write_text("x")
    argv = ["bash", str(_scripts() / "meshtastic-link-gate.sh"), str(binary)]
    if label is not None:
        argv.append(label)
    return subprocess.run(argv, env={**os.environ, "PATH": f"{fb}:/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=60)


def test_link_gate_labeled_failure_names_the_artifact_not_meshtasticd(tmp_path):
    # With a label the generic message is used — never the meshtasticd-specific remediation.
    r = _run_gate_ex(tmp_path, ["libc.so.6", "libGL.so.1"], label="qemu-system-xtensa (headless)")
    assert r.returncode == 3
    assert "qemu-system-xtensa (headless)" in r.stderr
    assert "meshtasticd" not in r.stderr


def test_link_gate_fails_closed_on_unresolved_library(tmp_path):
    # A `=> not found` in the closure is fatal — an unproven closure is not a pass.
    r = _run_gate_ex(tmp_path, ["libc.so.6"], notfound=["libfoo.so.1"])
    assert r.returncode == 2
    assert "unresolved shared libraries" in r.stderr and "libfoo.so.1" in r.stderr


def test_link_gate_fails_closed_when_readelf_cannot_inspect(tmp_path):
    # readelf that cannot inspect the binary is a failure, not a silent pass.
    r = _run_gate_ex(tmp_path, ["libc.so.6"], readelf_fail=True)
    assert r.returncode == 2
    assert "readelf could not inspect" in r.stderr


# --- meshcom-qemu now BUILDS the emulator from source (headless) ------------------------------------
def _meshcom_qemu(svc):
    return svc.stack("meshcom").component("meshcom-qemu")


def test_meshcom_qemu_builds_the_emulator_from_source_with_the_link_gate(tmp_path):
    c = _meshcom_qemu(_svc(tmp_path))
    argvs = [list(s.get("argv", [])) for s in c.build_steps]
    build = [a for a in argvs if any("build-qemu.sh" in t for t in a)]
    assert build, "meshcom-qemu must provision qemu via build-qemu.sh"
    b = build[0]
    assert "--link-gate" in b
    gate = b[b.index("--link-gate") + 1]
    assert gate.endswith("meshtastic-link-gate.sh") and "{asset}" in gate
    # The managed build must NOT fetch the prebuilt (libSDL2) tarball.
    assert not any("fetch-qemu.sh" in t for a in argvs for t in a)


def test_meshcom_qemu_step_budget_covers_a_from_source_build(tmp_path):
    # The from-source QEMU compile is the heaviest step; the per-step budget must clear the cold Zero
    # firmware build (~1560 s) AND leave room for a multi-hour QEMU build.
    c = _meshcom_qemu(_svc(tmp_path))
    assert c.build_timeout >= 3600.0 and c.build_timeout >= 7200.0


def test_meshcom_qemu_declares_the_source_build_toolchain_deps(tmp_path):
    # The generated bootstrap installs the headless QEMU build toolchain and drops the tarball's
    # wget/xz-utils; the runtime libslirp0 stays.
    import re
    script = _svc(tmp_path).deps_script()
    m = re.search(r'DRY_PKGS="([^"]+)"', script)
    assert m, "generated bootstrap must carry a DRY_PKGS dry-run set"
    pkgs = set(m.group(1).split())
    for pkg in ("meson", "ninja-build", "libglib2.0-dev", "libpixman-1-dev", "libslirp-dev",
                "zlib1g-dev", "git"):
        assert pkg in pkgs, f"{pkg} missing from generated bootstrap"
    assert "wget" not in pkgs and "xz-utils" not in pkgs
    assert "libslirp0" in pkgs


def test_meshtastic_never_needs_root_to_build_start_or_configure(tmp_path):
    """lhpc runs meshtasticd ROOTLESS. The managed build replaced an apt package, so this checks the
    replacement did not smuggle privilege in: no build, run or post-start command may invoke sudo or
    otherwise assume uid 0. Privileged setup stays where it belongs — the operator-run bootstrap."""
    c = _mesh(_svc(tmp_path))
    argvs = [list(s.get("argv", [])) for s in c.build_steps]
    argvs += [list(s.get("argv", [])) for s in c.post_steps if s.get("kind") == "exec"]
    argvs += [list(c.run_argv)]
    for argv in argvs:
        assert argv, "empty argv"
        joined = " ".join(argv)
        for priv in ("sudo", "pkexec", "doas", "su "):
            assert priv not in joined, f"{priv!r} in {joined!r}"
        assert not argv[0].startswith("/usr/sbin/")          # not a root-only binary path
    # Every artifact it writes lives under the runtime root, which the operator owns.
    assert c.bin.startswith("build/") and not c.bin.startswith("/")
    for s in c.build_steps:
        for tok in s.get("argv", []):
            assert not tok.startswith(("/etc/", "/usr/", "/var/", "/opt/")), tok
