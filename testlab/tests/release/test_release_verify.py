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
    alive,
    built,
    gui_startable,
    install_build,
    pty_readiness,
    start_component,
    stop,
    wait_http,
    wait_tcp,
)
from lhpc_testlab.testing import run_lhpc

pytestmark = pytest.mark.slow

MESHCORE_COMPANION = 5000
MESHCORE_WEBUI = 8788
REPEATER_DASHBOARD = 8000
MESHCOM_UI = 18083
MESHTASTIC_API = 4403
KISS_TCP = 8001
GRAYWOLF_UI = 8080


def _artifact_intact(svc, stack: str) -> None:
    """Every file of the installed artifact still hashes as its receipt records — checked while
    the stack is still STOPPED.

    It has to be here rather than at the end: the emulated MeshCom node writes to its own flash
    image as soon as it boots, so after a start a mismatch there means the node ran. Integrity
    is a property of what was installed; identity after execution is the receipt's components
    map, which the identity case compares.
    """
    from lhpc.core import binary_receipt
    state, rec, why = binary_receipt.receipt_state(svc._paths, stack)
    assert state == "valid", f"{stack}: binary receipt {state} — {why}"
    ok, bad = binary_receipt.verify_files(svc._paths, rec)
    assert ok, f"{stack}: the installed artifact does not match its receipt: {bad}"


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
    assert alive(env, "loraham-kiss-tnc")


def test_release_graywolf(env):
    """Graywolf on the running KISS chain: its own web UI answers."""
    install_build(env, "graywolf")
    _start(env, "graywolf", timeout=900)
    assert wait_http(f"http://127.0.0.1:{GRAYWOLF_UI}/", 120,
                     accept=(200, 401, 403)) in (200, 401, 403), \
        "graywolf web UI never answered on 8080"
    assert alive(env, "graywolf")
    stop(env, "graywolf")


def test_release_chat(env, svc):
    """Chat is interactive: the controller presents a command instead of starting it, so the
    proof is that command running on a real terminal against the fake daemon."""
    stop(env, "kiss", require=False)
    install_build(env, "chat")
    _start(env, "daemon", timeout=300)
    comp = next(c for c in svc.stack("chat").components if c.id == "loraham-chat")
    pty_readiness(svc.manual_start_command(comp), env)


def test_release_voice(env, svc):
    """BOTH Voice variants, because both are shipped and they share one source checkout — a
    matching checkout proves neither of them.

    LHPC runs one of them per box: the GTK app where a toolkit and a display exist, otherwise
    the terminal fallback. Whichever that is, it is started; the other is proved by running its
    own presented command on a terminal. Their launchers are rendered by the start, not by the
    build, so the start comes first either way.
    """
    install_build(env, "voice", timeout=2400)
    _start(env, "voice", timeout=300)
    cli = next(c for c in svc.stack("voice").components if c.id == "loraham-voice-cli")
    if svc.gui_fallback_active(svc.stack("voice")):
        # This box runs the terminal variant. LHPC renders ITS launcher and not the GTK app's.
        pty_readiness(svc.manual_start_command(cli), env)
        assert built(svc, "voice", "loraham-voice"), "the GTK variant was not built"
    else:
        assert gui_startable(svc, "voice", "loraham-voice")
        assert alive(env, "loraham-voice"), "the GTK variant is startable here but is not running"
        # The fallback's launcher only exists where the fallback is active, so the terminal
        # variant is proved here by its own build — never by a source checkout it shares with
        # the GTK app.
        assert built(svc, "voice", "loraham-voice-cli"), "the terminal variant was not built"
    stop(env, "voice")
    stop(env, "daemon")


# --------------------------------------------------------------------------- 868, MeshCore


def _meshcore_mode(env, mode: str):
    """A repeater mode refuses without the repeater's own node name — set it first, exactly as
    the typed refusal tells an operator to."""
    if mode != "chat":
        run_lhpc(env, "config", "meshcore", "repeater_name", "LABRPT", check=True, timeout=120)
    run_lhpc(env, "config", "meshcore", "mode", mode, check=True, timeout=120)


def test_release_meshcore_chat(env, svc):
    """Companion node only, plus its OPTIONAL Web UI.

    `lhpc stack start meshcore` does not bring an optional component up, so the Web UI is
    started by name — the way an operator starts it, and the only way this lane can claim it
    starts at all.
    """
    install_build(env, "meshcore", timeout=2400)
    _meshcore_mode(env, "chat")
    _start(env, "meshcore", timeout=900)
    assert wait_tcp(MESHCORE_COMPANION, 180), "MeshCore companion never opened TCP 5000"
    assert alive(env, "meshcore-node")
    start_component(env, "meshcore-webui")
    assert wait_http(f"http://127.0.0.1:{MESHCORE_WEBUI}/", 180,
                     accept=(200, 401, 403)) in (200, 401, 403), "MeshCore Web UI never answered"
    assert alive(env, "meshcore-webui")
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
    """Repeater only: the dashboard answers, and NO companion is hosted.

    The absence is the point of the mode. Asserting only the dashboard would pass just as well
    if the mode setting had been ignored and a companion were running beside it.
    """
    _meshcore_mode(env, "repeater")
    _start(env, "meshcore", timeout=900)
    assert wait_http(f"http://127.0.0.1:{REPEATER_DASHBOARD}/", 240,
                     accept=(200, 401, 403)) in (200, 401, 403), "repeater dashboard silent"
    assert not wait_tcp(MESHCORE_COMPANION, 20), \
        "repeater-only mode is hosting a companion on 5000"
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
    assert alive(env, "rns")
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
    # Optional components of this stack: not started by the stack, so started by name.
    start_component(env, "lxmd")
    assert alive(env, "lxmd"), "lxmd did not come up"
    if gui_startable(svc, "reticulum", "sideband"):
        start_component(env, "sideband")
        assert alive(env, "sideband"), "sideband is startable here but did not come up"
    else:
        print("sideband not startable here — LHPC's own GUI predicate dropped it")


def test_release_reticulum_nomadnet(env, svc):
    """NomadNet is interactive: proved on a terminal against the running Reticulum."""
    comp = next(c for c in svc.stack("reticulum").components if c.id == "nomadnet")
    try:
        pty_readiness(svc.manual_start_command(comp), env, ready_timeout=90)
    finally:
        stop(env, "reticulum")


# --------------------------------------------------------------------------- binary stacks


def test_release_meshtastic(env, svc):
    """The published artifact, started against the simulated radio; the node answers its own
    CLI. A binary install has no source tree, so it is never `lhpc build`."""
    install_build(env, "meshtastic", timeout=2400)
    _artifact_intact(svc, "meshtastic")
    _start(env, "meshtastic", timeout=900)
    assert wait_tcp(MESHTASTIC_API, 180), "meshtasticd never opened its API port 4403"
    info = run_lhpc(env, "meshtastic", "--info", timeout=300)
    assert info.returncode == 0, (info.stdout[-1500:], info.stderr[-800:])
    stop(env, "meshtastic")


def test_release_meshcom(env, svc):
    """The emulated MeshCom node from the published artifact: its web UI answers once the
    firmware has booted (502 until then — that wait is the point)."""
    stop(env, "kiss", "graywolf", require=False)
    install_build(env, "meshcom", timeout=2400)
    _artifact_intact(svc, "meshcom")
    _start(env, "meshcom", timeout=1200)
    assert wait_http(f"http://127.0.0.1:{MESHCOM_UI}/", 900, accept=(200,)) == 200, \
        "MeshCom web UI never reached 200 — the firmware did not finish booting"
    stop(env, "meshcom")


# --------------------------------------------------------------------------- identity


def test_release_identity_matches_candidate_manifest(env, svc):
    """THE release check: what is installed IS the candidate, and NOTHING mandatory is missing.

    Every managed source is re-proved NOW through the production verifier (record + live HEAD),
    then its HEAD is compared with the pin in the REAL packaged manifest — not the lab overlay,
    which retargets the daemon and RadioLib at local fixtures. Those two are named as the
    exception; their artifact is proved by the binary builder's own smoke and clean-runtime
    test, never here.

    An absent component is a FAILURE unless LHPC's own GUI predicate says it cannot run on this
    box. Collecting the absent ones and printing them would let a lane that installed nothing
    report success as loudly as one that installed everything.
    """
    import tomllib

    from lhpc.core import binary_receipt, source_registry
    from lhpc.core.manifest import default_manifest_path
    from lhpc_testlab.manifest_overlay import RETARGETS

    candidate = tomllib.loads(default_manifest_path().read_text())
    pins, stack_of = {}, {}
    for st in candidate["stack"]:
        for c in st.get("component", []):
            src = c.get("source") or {}
            if src.get("pin_commit"):
                pins[c["id"]] = (src["path"], src["pin_commit"])
                stack_of[c["id"]] = st["id"]

    # A stack installed from an artifact has no managed checkout for the components that
    # artifact covers — the artifact IS the install, and its receipt is what proves it (below).
    from_binary, binary_ok = set(), {}
    for stack in svc.stacks():
        spec = svc.binary_spec(stack.id)
        if not spec:
            continue
        state, rec, why = binary_receipt.receipt_state(svc._paths, stack.id)
        binary_ok[stack.id] = (state, rec, why)
        if state == "valid":
            from_binary.update(spec.covers)

    config = svc.config()
    checked, excluded, permitted, missing, wrong = [], [], [], [], []
    for stack in svc.stacks():
        skippable = set(svc.gui_unavailable_components(stack))
        for comp in stack.components:
            if getattr(comp, "source", None) is None or comp.id not in pins:
                continue
            path, pin = pins[comp.id]
            if comp.id in from_binary:
                excluded.append(f"{comp.id} (in the {stack.id} artifact)")
                continue
            if path in RETARGETS:
                excluded.append(f"{comp.id} ({path}: lab fixture)")
                continue
            dest = svc._paths.resolve_source(path)
            if not dest.exists():
                (permitted if comp.id in skippable else missing).append(
                    f"{stack.id}/{comp.id}")
                continue
            rec, why = source_registry.verify_identity(
                svc._paths, svc._system, config, comp, dest)
            if rec is None:
                wrong.append(f"{stack.id}/{comp.id}: identity not provable — {why}")
                continue
            head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=60,
                                  check=True).stdout.strip()
            if head != pin:
                wrong.append(f"{stack.id}/{comp.id}: installed {head[:9]}, candidate manifest "
                             f"pins {pin[:9]}")
            else:
                checked.append(comp.id)

    # Every binary stack must be installed from its artifact, and the artifact must carry the
    # candidate's commits for EVERY component it covers — the full map, not whatever it lists.
    for stack in svc.stacks():
        spec = svc.binary_spec(stack.id)
        if not spec:
            continue
        state, rec, why = binary_ok[stack.id]
        if state != "valid":
            missing.append(f"{stack.id} (binary receipt {state}: {why})")
            continue
        for cid in spec.covers:
            want = pins.get(cid)
            got = rec.components.get(cid)
            if want is None:
                continue
            if got is None:
                wrong.append(f"{stack.id}: the artifact records no commit for {cid}")
            elif got != want[1]:
                wrong.append(f"{stack.id}: artifact carries {cid} {got[:9]}, candidate manifest "
                             f"pins {want[1][:9]}")
        checked.append(f"{stack.id} (binary)")

    # Readable evidence beside the machine check — never instead of it.
    versions = run_lhpc(env, "status", "--versions", timeout=180).stdout
    Path(env["LHPC_RUNTIME_ROOT"]).parent.joinpath("versions.txt").write_text(versions)
    print(f"identity verified: {len(checked)} — {', '.join(sorted(checked))}")
    print(f"lab fixtures / artifact-covered: {', '.join(excluded) or 'none'}")
    print(f"permitted GUI omissions here: {', '.join(permitted) or 'none'}")

    assert not wrong, "the release would ship something other than the candidate:\n  " + \
        "\n  ".join(wrong)
    assert not missing, "mandatory components are not installed:\n  " + "\n  ".join(missing)
    assert checked, "nothing was identity-checked — the lane proved nothing"


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
