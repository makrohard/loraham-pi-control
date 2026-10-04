"""What the manifest declares for the two stacks that BUILD a server from a pinned upstream
checkout instead of installing a package: Meshtastic (meshtasticd, server-only, never the X11
`native-tft` environment, run from the runtime root, rootless throughout) and MeshCom's emulator
(qemu-system-xtensa built headless behind the shared link gate). The strict completion marker
that makes a replaced checkout read "not built" is proven here too."""
from __future__ import annotations

import re

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from seams import readable_heads, write_own_rev


def _svc(tmp_path):
    return ControllerService(system=FakeSystem(commands=readable_heads(tmp_path)).system,
                             paths=Paths(runtime_root=tmp_path))


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
    # What a real build writes: the marker's own content, its own revision and the recorded
    # inputs BESIDE it.
    marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(c))
    write_own_rev(svc, c)
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
    # The from-source QEMU compile is the heaviest step; it is never ended for being slow (F42): the
    # manifest declares no value of its own, so only a stall or the 24 h runaway guard ends a step.
    from lhpc.core import progress
    c = _meshcom_qemu(_svc(tmp_path))
    assert c.build_timeout == 0.0
    assert progress.build_limits(c.build_timeout, {})[1] >= 7200.0


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
    assert c.bin == "build/tools/meshtasticd/meshtasticd"
    for s in c.build_steps:
        for tok in s.get("argv", []):
            assert not tok.startswith(("/etc/", "/usr/", "/var/", "/opt/")), tok


# --- a source build of an artifact-capable component proves its own revision BESIDE the marker -----
# The marker stays the static text (a binary artifact ships it); the side file `<marker>.rev`
# records the checkout's own revision and is compared only off the binary channel.


def _built_from_source(tmp_path):
    """meshtastic built from its checkout, as `svc.build` leaves it: the static marker, the
    own-revision side file and the recorded inputs. Returns (service, component, marker)."""
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT
    svc = _svc(tmp_path)
    c = _mesh(svc)
    src = tmp_path / "src" / "meshtastic-firmware"
    src.mkdir(parents=True, exist_ok=True)
    marker = src / c.build_marker
    marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(c))
    write_own_rev(svc, c)
    _stamp_inputs(svc.build_inputs_path(c), svc.build_inputs_text(c))
    return svc, c, marker


def test_a_source_build_whose_checkout_moved_in_place_reads_not_built(tmp_path, monkeypatch):
    svc, c, marker = _built_from_source(tmp_path)
    assert svc.is_built(c) is True
    assert marker.read_text() == "lhpc build complete\n"            # the marker stays static
    # Stubs the collaborator, git: the checkout's HEAD moved (a hand-made change, no update).
    monkeypatch.setattr(svc, "_git_out", lambda argv: (0, "f" * 40 + "\n"))
    svc.invalidate_snapshot()
    assert svc.is_built(c) is False


def test_a_source_build_from_before_the_side_file_reads_not_built_once(tmp_path):
    svc, c, marker = _built_from_source(tmp_path)
    (marker.parent / (c.build_marker + ".rev")).unlink()              # built by an older lhpc
    svc.invalidate_snapshot()
    assert svc.is_built(c) is False


@pytest.mark.parametrize("side_file", ["absent", "stale"])
def test_on_the_binary_channel_the_side_file_is_never_compared(tmp_path, monkeypatch,
                                                               binary_receipt, side_file):
    # Stubs the collaborator, the host: the binary channel is published for this target.
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc, c, marker = _built_from_source(tmp_path)
    rev = marker.parent / (c.build_marker + ".rev")
    if side_file == "absent":
        rev.unlink()
    else:
        rev.write_text(f"consumed {c.id} {'0' * 40}\n")              # a stale one inside an artifact
    binary_receipt(svc, "meshtastic")
    assert svc.on_binary_channel("meshtastic") is True
    assert svc.is_built(c) is True


class _StepsSucceedRevisionUnreadable(dict):
    """A runner table answering every build step with rc 0 and `git rev-parse` with rc 1: the
    checkout's revision cannot be read."""

    def get(self, argv, default=None):
        from lhpc.core.probes.backends import CommandResult
        return CommandResult(1 if "rev-parse" in argv else 0, "ok\n", "")


class _StepsSucceedRevisionReadable(dict):
    """A runner table answering every build step with rc 0 and `git rev-parse` with one revision."""

    def get(self, argv, default=None):
        from lhpc.core.probes.backends import CommandResult
        return CommandResult(0, "c" * 40 + "\n" if "rev-parse" in argv else "ok\n", "")


def test_a_successful_cli_source_build_writes_its_own_revision_and_reads_built(tmp_path):
    svc = ControllerService(system=FakeSystem(commands=_StepsSucceedRevisionReadable()).system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.bootstrap(apply=True).ok
    (tmp_path / "src" / "meshtastic-firmware").mkdir(parents=True)
    res = svc.build("meshtastic", apply=True)
    assert res.ok, res.summary
    c = _mesh(svc)
    side = tmp_path / "src" / "meshtastic-firmware" / (c.build_marker + ".rev")
    assert side.read_text() == f"consumed meshtastic {'c' * 40}\n"
    svc.invalidate_snapshot()
    assert svc.is_built(c) is True


def test_a_successful_console_source_build_writes_its_own_revision_and_reads_built(tmp_path):
    """The console's detached build: the launcher reads the checkout's revision under its locks
    and writes the side file before the marker."""
    import subprocess

    from lhpc.core import build_launcher_runtime
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT
    src = tmp_path / "src" / "meshtastic-firmware"
    src.mkdir(parents=True)
    git = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    svc = ControllerService(system=FakeSystem(commands=readable_heads(tmp_path, sha)).system,
                            paths=Paths(runtime_root=tmp_path))
    c = _mesh(svc)
    assert svc._consumed_source_lines(c) == ""          # premise: meshtastic consumes no other source
    rev_path, rev_consumed = svc._own_rev_source(c)     # what the console hands the launcher
    build_launcher_runtime.run({
        "steps": [{"argv": ["true"]}], "cwd": str(src), "runtime_root": str(tmp_path),
        "lock_names": [], "index_lock_name": "",
        "marker_path": str(src / c.build_marker), "marker_text": BUILD_MARKER_TEXT,
        "inputs_path": str(svc.build_inputs_path(c)), "inputs_text": svc.build_inputs_text(c),
        "rev_path": str(rev_path), "rev_consumed": [list(p) for p in rev_consumed]})
    assert (src / (c.build_marker + ".rev")).read_text() == f"consumed meshtastic {sha}\n"
    svc.invalidate_snapshot()
    assert svc.is_built(c) is True


def test_a_source_build_whose_own_revision_cannot_be_read_says_so_and_never_reads_built(tmp_path):
    svc = ControllerService(system=FakeSystem(commands=_StepsSucceedRevisionUnreadable()).system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.bootstrap(apply=True).ok
    (tmp_path / "src" / "meshtastic-firmware").mkdir(parents=True)
    res = svc.build("meshtastic", apply=True)
    assert res.ok, res.summary                                        # every step ran
    unverified = [d for d in res.details if d.split()[:1] == ["[unverified]"]]
    assert [d.split()[:2] for d in unverified] == [["[unverified]", "meshtastic:"]]
    assert "`lhpc build meshtastic --yes`" in unverified[0]                # the remedy, not a wait
    assert svc.is_built(_mesh(svc)) is False

