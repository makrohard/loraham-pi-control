"""Every stack an automated pin release may move: installed at the selected commit, built,
started and verified — then proved to BE that commit.

One ordered module: the lab has one fake radio pair, so a stack releases its band before the
next claims it. Each case names its own evidence; none of them is a log grep.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from lhpc_testlab.release import (
    gui_startable,
    install_build,
    pty_readiness,
    running,
    stop,
    wait_http,
    wait_tcp,
)
from lhpc_testlab.testing import run_lhpc

pytestmark = pytest.mark.slow

MESHCORE_COMPANION = 5000
REPEATER_DASHBOARD = 8000
MESHCOM_UI = 18083
MESHTASTIC_API = 4403
KISS_TCP = 8001
GRAYWOLF_UI = 8080


def _start(env, stack: str, timeout: float = 600.0):
    r = run_lhpc(env, "stack", "start", stack, "--yes", timeout=timeout)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-800:])
    return r


# --------------------------------------------------------------------------- 433 chain


def test_release_kiss(env):
    """The KISS TNC over the fake daemon: its own TCP listener is the evidence."""
    install_build(env, "kiss")
    _start(env, "kiss")
    assert wait_tcp(KISS_TCP, 120), "kiss is not serving KISS/TCP on 8001"
    assert running(env, "kiss")


def test_release_graywolf(env):
    """Graywolf on the running KISS chain: its own web UI answers."""
    install_build(env, "graywolf")
    _start(env, "graywolf", timeout=900)
    assert wait_http(f"http://127.0.0.1:{GRAYWOLF_UI}/", 120,
                     accept=(200, 401, 403)) in (200, 401, 403), \
        "graywolf web UI never answered on 8080"
    assert running(env, "graywolf")
    stop(env, "graywolf")


def test_release_chat(env, svc):
    """Chat is interactive: the controller presents a command instead of starting it, so the
    proof is that command running on a real terminal against the fake daemon."""
    stop(env, "kiss")
    install_build(env, "chat")
    _start(env, "daemon", timeout=300)
    comp = next(c for c in svc.stack("chat").components if c.id == "loraham-chat")
    pty_readiness(svc.manual_start_command(comp), env)


def test_release_voice(env, svc):
    """Voice ships two variants and LHPC picks ONE for this box: the GTK app where a toolkit
    and a display exist, otherwise the terminal fallback. Both are BUILT here; the one this box
    would actually run is the one started.

    The terminal variant's launcher is rendered by the start, not by the build, so the start
    comes first either way — running its command before that would only prove the file is
    missing.
    """
    install_build(env, "voice", timeout=2400)
    _start(env, "voice", timeout=300)
    if svc.gui_fallback_active(svc.stack("voice")):
        comp = next(c for c in svc.stack("voice").components if c.id == "loraham-voice-cli")
        pty_readiness(svc.manual_start_command(comp), env)
    else:
        assert gui_startable(svc, "voice", "loraham-voice")
        assert running(env, "voice"), "the GTK variant is startable here but is not running"
    stop(env, "voice")
    stop(env, "daemon")


# --------------------------------------------------------------------------- 868, MeshCore


def _meshcore_mode(env, mode: str):
    """A repeater mode refuses without the repeater's own node name — set it first, exactly as
    the typed refusal tells an operator to."""
    if mode != "chat":
        run_lhpc(env, "config", "meshcore", "repeater_name", "LABRPT", check=True, timeout=120)
    run_lhpc(env, "config", "meshcore", "mode", mode, check=True, timeout=120)


def test_release_meshcore_chat(env):
    """Companion node only: the companion TCP port is the node's own state.

    The Web UI is an OPTIONAL component — `lhpc stack start meshcore` deliberately does not
    bring it up — so it is not this case's evidence; the acceptance lane drives it.
    """
    install_build(env, "meshcore", timeout=2400)
    _meshcore_mode(env, "chat")
    _start(env, "meshcore", timeout=900)
    assert wait_tcp(MESHCORE_COMPANION, 180), "MeshCore companion never opened TCP 5000"
    assert running(env, "meshcore")
    stop(env, "meshcore")


def test_release_meshcore_chat_repeater(env):
    """The repeater hosting the companion: BOTH the dashboard and the companion answer."""
    _meshcore_mode(env, "chat+repeater")
    _start(env, "meshcore", timeout=900)
    assert wait_http(f"http://127.0.0.1:{REPEATER_DASHBOARD}/", 240,
                     accept=(200, 401, 403)) in (200, 401, 403), "repeater dashboard silent"
    assert wait_tcp(MESHCORE_COMPANION, 180), "hosted companion never opened TCP 5000"
    stop(env, "meshcore")


def test_release_meshcore_repeater(env):
    """Repeater only: the dashboard answers and no companion is hosted."""
    _meshcore_mode(env, "repeater")
    _start(env, "meshcore", timeout=900)
    assert wait_http(f"http://127.0.0.1:{REPEATER_DASHBOARD}/", 240,
                     accept=(200, 401, 403)) in (200, 401, 403), "repeater dashboard silent"
    stop(env, "meshcore")
    _meshcore_mode(env, "chat")


def test_release_meshcore_cli(env, svc):
    """The MeshCore CLI is an interactive component: proved on a terminal against the running
    companion, not by `--version`."""
    _start(env, "meshcore", timeout=900)
    assert wait_tcp(MESHCORE_COMPANION, 180)
    comp = next(c for c in svc.stack("meshcore").components if c.id == "meshcore-cli")
    try:
        pty_readiness(svc.manual_start_command(comp), env, ready_timeout=90)
    finally:
        stop(env, "meshcore")


# --------------------------------------------------------------------------- 868, Reticulum


def test_release_reticulum(env, svc):
    """Reticulum's own client tool must list the LoRa interface this stack installed."""
    install_build(env, "reticulum", timeout=3600)
    _start(env, "reticulum", timeout=900)
    assert running(env, "reticulum")
    root = Path(env["LHPC_RUNTIME_ROOT"])
    rnstatus = root / "src" / "reticulum" / ".venv" / "bin" / "rnstatus"
    assert rnstatus.exists(), f"reticulum did not install its own tools ({rnstatus})"
    out = ""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        r = subprocess.run([str(rnstatus), "--config", str(root / "state" / "reticulum")],
                           capture_output=True, text=True, timeout=60, check=False)
        out = r.stdout + r.stderr
        if "Interface" in out:
            break
        time.sleep(5)
    assert "Interface" in out, f"rnstatus listed no interface:\n{out[-1500:]}"
    assert "lora" in out.lower(), f"rnstatus lists no LoRa interface:\n{out[-1500:]}"


def test_release_reticulum_nomadnet(env, svc):
    """NomadNet is interactive: proved on a terminal against the running Reticulum."""
    comp = next(c for c in svc.stack("reticulum").components if c.id == "nomadnet")
    try:
        pty_readiness(svc.manual_start_command(comp), env, ready_timeout=90)
    finally:
        stop(env, "reticulum")


# --------------------------------------------------------------------------- binary stacks


def test_release_meshtastic(env):
    """The published artifact, started against the simulated radio; the node answers its own
    CLI. A binary install has no source tree, so it is never `lhpc build`."""
    install_build(env, "meshtastic", timeout=2400)
    _start(env, "meshtastic", timeout=900)
    assert wait_tcp(MESHTASTIC_API, 180), "meshtasticd never opened its API port 4403"
    info = run_lhpc(env, "meshtastic", "--info", timeout=300)
    assert info.returncode == 0, (info.stdout[-1500:], info.stderr[-800:])
    stop(env, "meshtastic")


def test_release_meshcom(env):
    """The emulated MeshCom node from the published artifact: its web UI answers once the
    firmware has booted (502 until then — that wait is the point)."""
    stop(env, "kiss", "graywolf")
    install_build(env, "meshcom", timeout=2400)
    _start(env, "meshcom", timeout=1200)
    assert wait_http(f"http://127.0.0.1:{MESHCOM_UI}/", 900, accept=(200,)) == 200, \
        "MeshCom web UI never reached 200 — the firmware did not finish booting"
    stop(env, "meshcom")


# --------------------------------------------------------------------------- identity


def test_release_identity_matches_candidate_manifest(env, svc):
    """THE release check: what is installed IS the candidate.

    Every managed source is re-proved NOW through the production verifier (record + live
    HEAD), then its HEAD is compared with the pin in the REAL packaged manifest — not the
    lab overlay, which retargets the daemon and RadioLib at local fixtures. Those two are
    named as the exception; their artifact is proved by the binary builder's own smoke and
    clean-runtime test, never here.
    """
    import tomllib

    from lhpc_testlab.manifest_overlay import RETARGETS

    from lhpc.core import binary_receipt, source_registry
    from lhpc.core.manifest import default_manifest_path

    candidate = tomllib.loads(default_manifest_path().read_text())
    pins, comp_of = {}, {}
    for st in candidate["stack"]:
        for c in st.get("component", []):
            src = c.get("source") or {}
            if src.get("pin_commit"):
                pins[c["id"]] = (src["path"], src["pin_commit"])
                comp_of[c["id"]] = st["id"]

    # A stack installed from an artifact has no managed checkout for its covered components —
    # the artifact IS the install, and its receipt is what proves it (below). A directory left
    # under src/ by the artifact's own layout is not an adopted source and must not be read as
    # one.
    from_binary = set()
    for stack in svc.stacks():
        spec = svc.binary_spec(stack.id)
        if spec and binary_receipt.receipt_state(svc._paths, stack.id)[0] == "valid":
            from_binary.update(spec.covers)

    config = svc.config()
    checked, excluded, absent = [], [], []
    for stack in svc.stacks():
        for comp in stack.components:
            spec = getattr(comp, "source", None)
            if spec is None or comp.id not in pins:
                continue
            path, pin = pins[comp.id]
            if comp.id in from_binary:
                excluded.append(f"{comp.id} (installed as part of the {stack.id} artifact)")
                continue
            if path in RETARGETS:
                excluded.append(f"{comp.id} ({path}: lab fixture)")
                continue
            dest = svc._paths.resolve_source(path)
            if not dest.exists():
                absent.append(f"{comp.id} ({path})")
                continue
            rec, why = source_registry.verify_identity(
                svc._paths, svc._system, config, comp, dest)
            assert rec is not None, f"{comp.id}: identity not provable — {why}"
            head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=60,
                                  check=True).stdout.strip()
            assert head == pin, (
                f"{comp.id}: installed {head[:9]} but the candidate manifest pins "
                f"{pin[:9]} — the release would ship an untested commit")
            checked.append(comp.id)

    for stack in svc.stacks():
        if not svc.binary_spec(stack.id):
            continue
        state, rec, why = binary_receipt.receipt_state(svc._paths, stack.id)
        if state == "absent":
            absent.append(f"{stack.id} (binary)")
            continue
        assert state == "valid", f"{stack.id}: binary receipt {state} — {why}"
        # `verify_files` is deliberately NOT used here. It is a pre-destructive guard against
        # deleting an operator's changed files, and a started MeshCom node writes to its own
        # emulated flash — so after this lane has run the stacks, a hash mismatch there is the
        # node having run, not a provenance defect. What identifies a binary install is what
        # LHPC's own acceptance gate reads: the receipt's components map against the pins,
        # with `receipt_state` having already proved every proof path is still in place.
        for cid, commit in rec.components.items():
            if cid in pins and pins[cid][0] not in RETARGETS:
                assert commit == pins[cid][1], (
                    f"{stack.id}: artifact carries {cid} {commit[:9]}, the candidate "
                    f"manifest pins {pins[cid][1][:9]}")
        checked.append(f"{stack.id} (binary)")

    # Readable evidence beside the machine check — never instead of it.
    versions = run_lhpc(env, "status", "--versions", timeout=180).stdout
    Path(env["LHPC_RUNTIME_ROOT"]).parent.joinpath("versions.txt").write_text(versions)
    assert checked, "nothing was identity-checked — the lane proved nothing"
    print(f"identity verified: {len(checked)} — {', '.join(sorted(checked))}")
    print(f"lab fixtures excluded: {', '.join(excluded) or 'none'}")
    print(f"not installed here: {', '.join(sorted(absent)) or 'none'}")


def test_release_gui_predicate_names_only_gui_components(svc):
    """The predicate the image's composition check reuses must answer for every stack without
    raising, and name only components that genuinely carry a GUI requirement — a Lite image is
    allowed to omit exactly these, and nothing else."""
    for stack in svc.stacks():
        unavailable = svc.gui_unavailable_components(stack)
        for cid in unavailable:
            comp = next(c for c in stack.components if c.id == cid)
            assert svc.needs_display(comp) or any(
                getattr(r, "gui", False) for r in (comp.requires or ())), \
                f"{cid} reported GUI-unavailable without a GUI requirement"
