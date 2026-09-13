"""What the manifest declares for the two stacks that BUILD a server from a pinned upstream
checkout instead of installing a package: Meshtastic (meshtasticd, server-only, never the X11
`native-tft` environment, run from the runtime root, rootless throughout) and MeshCom's emulator
(qemu-system-xtensa built headless behind the shared link gate). The strict completion marker
that makes a replaced checkout read "not built" is proven here too."""
from __future__ import annotations

import re

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _stamp_inputs(path, text):
    """Write the recorded inputs where a real build puts them — beside the built artifact, whose
    directory a real build has already created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- managed server-only Meshtastic ---------------------------------------------------------------
# meshtasticd is BUILT from a pinned upstream checkout with upstream's `native` environment, instead
# of installed from the OBS package (built `native-tft`, so it links X11/libinput/xkbcommon and its
# Depends drag SDL2 -> PulseAudio/Wayland/Mesa/LLVM onto a headless rig).

def _mesh(svc):
    # By ID, not by position: the stack also carries an optional GPS feed component, and
    # "the meshtasticd component" is what every caller here means.
    return next(c for c in svc.stack("meshtastic").components if c.id == "meshtastic")


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
    # The web client is LHPC's own pin: the step names a release version and the sha256 of its
    # build.tar (verified on every install), and no longer depends on the firmware checkout.
    web = next(s for s in steps if "meshtastic-web-assets.sh" in " ".join(s.get("argv", [])))
    assert re.fullmatch(r"\d+\.\d+\.\d+", web["argv"][-2]) and re.fullmatch(r"[0-9a-f]{64}", web["argv"][-1])
    assert "{source}" not in " ".join(web["argv"])


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
    # What a real build writes: the marker's own content, and the recorded inputs BESIDE it.
    marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(c))
    _stamp_inputs(svc.build_inputs_path(c), svc.build_inputs_text(c))
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


# --- meshcom-qemu builds the emulator from source (headless) --------------------------------------
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
