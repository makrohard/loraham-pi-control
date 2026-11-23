"""Every stack an automated pin release may move: installed at the selected commit, built,
started and verified — then proved to BE that commit.

One ordered module: the lab has one fake radio pair, so a stack releases its band before the
next claims it. Each case names its own evidence; none of them is a log grep.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from lhpc_testlab.release import (
    GRAYWOLF_UI,
    KISS_TCP,
    MESHCHAT_TITLE,
    MESHCHAT_UI,
    MESHCOM_UI,
    MESHCORE_COMPANION,
    MESHCORE_WEBUI,
    MESHCORE_WEBUI_TITLE,
    MESHTASTIC_API,
    MESHTASTIC_WEB,
    REPEATER_DASHBOARD,
    REPEATER_DASHBOARD_TITLE,
    alive,
    assert_graywolf_web_ui,
    assert_meshcom_web_ui,
    assert_plugin_manager_running,
    assert_web_ui,
    binaries_at_the_pins,
    binaries_installed,
    busy_case_wrong,
    fetch_argv,
    file_mark,
    gui_startable,
    inherits,
    install_build,
    meshchat_attached,
    meshcore_webui_healthy,
    openhop_set_up,
    plugin_manager_markers,
    plugin_manager_pids,
    pty_readiness,
    read_since,
    readiness_failed,
    require_all_passed,
    require_prerequisite,
    required_release_cases,
    reticulum_peers_snapshot,
    rnstatus_lists_interface,
    set_lab_flags,
    stack_regression,
    start_component,
    start_log_tails,
    stop,
    wait_for,
    wait_tcp,
)
from lhpc_testlab.testing import named, port_of, run_lhpc

from lhpc.core import instances
from lhpc.core.manifest import default_manifest_path, load_manifest

pytestmark = pytest.mark.slow

# What each interactive component DRAWS when it is working, read from the pinned source of the
# program itself: chat's title window, NomadNet's menu bar, meshcli's interactive banner, the
# Voice terminal status line. `pty_readiness` requires one of these on the screen, because
# "wrote something and stayed up" is also true of a program that printed an error and slept.
CHAT_DRAWS = r"LoRaHAM_Pi Chat"
NOMADNET_DRAWS = r"Conversations"
MESHCLI_DRAWS = r"Interactive mode"
# The terminal variant opens by ENUMERATING the box's audio devices and asking which to record
# from — it reaches its titled screen only after that answer. Observed in run 34410321870, where
# it drew "Verfuegbare Audiogeraete:" and a numbered ALSA list, and waiting for the title timed
# out at 60 s. The prompt is the honest readiness signal: only a program that started, linked
# against ALSA and queried the host draws it. Matched loosely because the build is localised.
VOICE_CLI_DRAWS = r"Aufnahme \[|Recording \[|LoRaHAM Voice"


def test_release_lane_reports_exactly_the_required_cases():
    """The names in `required-release-cases.json` ARE this module's cases — none renamed, none
    dropped, none added without being declared.

    The automated release reads this lane's JUnit and requires those names to have passed. It
    used to require only that FOURTEEN cases called `test_release_*` passed, which fourteen
    renamed or replaced cases satisfy just as well — a stack could stop being proved without
    anything going red. This case is what makes the list binding, and it runs FIRST on purpose:
    a drift must fail before the lane spends an hour installing stacks.
    """
    present = {n for n, obj in globals().items()
               if n.startswith("test_release_") and callable(obj)}
    required = set(required_release_cases())
    assert present == required, (
        f"missing from the lane: {sorted(required - present)}; "
        f"in the lane but not declared required: {sorted(present - required)} — "
        f"lhpc_testlab/data/required-release-cases.json and this module move together")



def _binary_stacks(tmp_path):
    """(service, target, [(stack, spec, entries)]) for every stack with a binary channel in the REAL
    packaged manifest, its entries read from the index the manifest names."""
    from lhpc.core import binary_install as bi
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService

    svc = ControllerService(manifest_path=default_manifest_path(),
                            paths=Paths(runtime_root=tmp_path))
    target = svc.binary_target()
    assert target, "the release lane runs on an aarch64 box with an os-release codename"
    out = []
    for stack in svc.stacks():
        spec = svc.binary_spec(stack.id)
        if spec is not None:
            out.append((stack, spec, bi.index_entries(bi.fetch_index(spec.index_url), stack.id)))
    assert out, "the packaged manifest names no binary stack — the check proved nothing"
    return svc, target, out


def test_release_every_binary_stack_has_a_binary_compatible_with_the_candidate(tmp_path):
    """Every binary stack has a published entry BUILT AT THE CANDIDATE'S PINS: this target, its commit for
    every covered component equal to the candidate's manifest pin and its build-input digests equal to the
    ones the candidate computes (`lhpc_commit` is not compared, so a bootstrap entry of the same composition
    passes). The release bot publishes one per release; none is red even though a box would install an
    older one (the check below): the release did not prove its own binary. Runs before the installs, so a
    gap fails in seconds."""
    svc, target, stacks = _binary_stacks(tmp_path)
    chosen, refused = binaries_at_the_pins(svc, target, stacks)
    print("binaries built at the candidate's pins: " + (", ".join(chosen) or "none"))
    assert not refused, "no published binary at the candidate's pins:\n  " + "\n  ".join(refused)


def test_release_every_binary_stack_has_a_binary_the_candidate_installs(tmp_path):
    """Every binary stack has an entry the candidate's install takes: `newest_entry` (U9: the newest entry
    for this target that covers the candidate's binary spec, at any commits). None (`none_compatible`) is
    red: the release would leave its boxes without a binary."""
    _svc, target, stacks = _binary_stacks(tmp_path)
    chosen, refused = binaries_installed(target, stacks)
    print("binaries the candidate installs: " + (", ".join(chosen) or "none"))
    assert not refused, "no published binary the candidate installs:\n  " + "\n  ".join(refused)


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


def _start(env, stack: str, timeout: float = 600.0, *, attribute: bool = True):
    """Start the stack this case is about.

    `attribute=False` is for a start that is NOT this case's own stack — the lab's fake daemon
    — so its failure carries no `stack_regression` marker and can never freeze the wrong thing.
    """
    mark = f"{stack_regression(stack, 'start')}\n" if attribute else ""
    since = time.time()                      # wallclock: a timestamp for the start-log mtime filter, no wait
    r = run_lhpc(env, "stack", "start", named(env, stack), "--yes", timeout=timeout)
    assert r.returncode == 0, (f"{mark}starting {stack} failed (rc {r.returncode}): "
                               f"{r.stdout[-2000:]}\n{r.stderr[-800:]}{start_log_tails(env, since)}")
    return r


# --------------------------------------------------------------------------- 433 chain


def test_release_kiss(env, svc):
    """The KISS TNC over the fake daemon: its own TCP listener is the evidence."""
    install_build(env, svc, "kiss")
    _start(env, "kiss")
    assert wait_tcp(port_of(env, "kiss", KISS_TCP), 120), readiness_failed(
        env, "kiss", "loraham-kiss-tnc", "kiss is not serving KISS/TCP on 8001")
    assert alive(env, "loraham-kiss-tnc"), readiness_failed(
        env, "kiss", "loraham-kiss-tnc", "LHPC does not report loraham-kiss-tnc running")


def test_release_graywolf(env, svc):
    """Graywolf on the running KISS chain: its own web UI answers, and its public version API
    reports the version the manifest fetched."""
    require_prerequisite(env, "kiss", left_by="test_release_kiss")
    install_build(env, svc, "graywolf")
    _start(env, "graywolf", timeout=900)
    _owner, argv = fetch_argv("graywolf-fetch.sh")
    assert_graywolf_web_ui(env, f"http://127.0.0.1:{port_of(env, 'graywolf', GRAYWOLF_UI)}/", argv[3], timeout=120)
    assert alive(env, "graywolf"), readiness_failed(
        env, "graywolf", "graywolf", "LHPC does not report graywolf running")
    stop(env, "graywolf")


def test_release_chat(env, svc):
    """Chat is interactive: the controller presents a command instead of starting it, so the
    proof is that command running on a real terminal against the fake daemon."""
    stop(env, "kiss", require=False)
    install_build(env, svc, "chat")
    # The lab's fake daemon: a prerequisite of a DIFFERENT stack, so its start is unattributed
    # — a broken fixture must never freeze chat.
    _start(env, "daemon", timeout=300, attribute=False)
    # The product flow: the start presents chat's instance (writes its config into its own config/files/<rid>/); the operator then
    # runs the presented command. K8: an instance's run directory is its own, written by that start.
    _start(env, "chat", timeout=300)
    comp = next(c for c in svc.stack("chat").components if c.id == "loraham-chat")
    inst = instances.copy_of(svc, "chat", named(env, "chat").partition("@")[2])[0]
    pty_readiness(svc.manual_start_command(comp, inst), env, CHAT_DRAWS, stack="chat")


def test_release_voice_gtk(env, svc):
    """The GTK app on a box WITH a graphical session: `lhpc stack start voice` starts it, and
    LHPC reporting the component running is its own readiness.

    Voice ships two variants that share one source checkout, one `loraham_voice.conf` and one
    exclusive ALSA device, and LHPC runs exactly ONE of them per box — the GTK app where a
    toolkit and a display exist, the terminal fallback otherwise. Both are release deliverables,
    so both must START, each proved by its own readiness. They cannot both run at once, so they
    are proved in two cases, in the two contexts LHPC's own predicate distinguishes: this one
    with the lane's display up, `test_release_voice_terminal` with it down.
    """
    require_prerequisite(env, "daemon", left_by="test_release_chat")
    install_build(env, svc, "voice", timeout=2400)
    # Unmarked: this predicate answers for THIS box's display and toolkit as well as for the
    # stack, so a lab that lost its display would freeze an innocent Voice.
    assert gui_startable(svc, "voice", "loraham-voice"), (
        "LHPC's GUI predicate says the Voice GTK app cannot run here, but this lane owns a "
        "display: the capability it requires is missing, which is a release defect")
    _start(env, "voice", timeout=300)
    assert alive(env, "loraham-voice"), readiness_failed(
        env, "voice", "loraham-voice", "the GTK variant is startable here but is not running")
    stop(env, "voice")


def test_release_voice_terminal(env, svc, headless_box):
    """The terminal variant on a box with NO graphical session — the box LHPC ships it for.

    Nothing here is faked into place. With the display gone, LHPC's own start typed-SKIPS the
    GTK app, writes the shared `loraham_voice.conf` it owns, runs the terminal variant's
    pre-steps (the launcher symlink, which a desktop start deliberately never materialises) and
    presents its command — the one an operator pastes. That command is then run on a real
    terminal and must draw the variant's own screen, stay up and exit cleanly.

    `lhpc stack start loraham-voice-cli` is NOT a route to this: LHPC refuses a direct start of
    the fallback, because the shared configuration is only generated by a stack start.
    """
    inherits("test_release_voice_gtk")         # its install is the one voice_gtk built
    # Unmarked, and a precondition rather than evidence: it states that the context this case
    # needs actually exists. Without it a lab that kept a compositor would run the desktop
    # branch and silently prove the GTK app twice.
    assert svc.gui_fallback_active(svc.stack("voice")), (
        "no graphical session is running, yet LHPC does not report Voice's terminal fallback "
        "as the variant for this box — the fallback would never be offered on any headless box")
    # The case's own fake daemon, unattributed like chat's: the GTK case's stop of Voice released
    # the radio's daemon (a client's stop also stops a daemon no other client uses).
    _start(env, "daemon", timeout=300, attribute=False)
    _start(env, "voice", timeout=300)
    cli = next(c for c in svc.stack("voice").components if c.id == "loraham-voice-cli")
    inst = instances.copy_of(svc, "voice", named(env, "voice").partition("@")[2])[0]      # the instance it started
    try:
        pty_readiness(svc.manual_start_command(cli, inst), env, VOICE_CLI_DRAWS, stack="voice")
    finally:
        stop(env, "voice")
        stop(env, "daemon")


# --------------------------------------------------------------------------- 868, MeshCore


def _meshcore_mode(env, mode: str):
    """A repeater mode refuses without the repeater's own node name — set it first, exactly as
    the typed refusal tells an operator to."""
    if mode != "chat":
        run_lhpc(env, "config", "meshcore", "repeater_name", "LABRPT", check=True, timeout=120)
    run_lhpc(env, "config", "meshcore", "mode", mode, check=True, timeout=120)


def _assert_plugin_manager_running(env) -> None:
    assert_plugin_manager_running(env["LHPC_RUNTIME_ROOT"])


def _assert_plugin_manager_gone(env) -> None:
    """A graceful stack stop takes the manager down and the host clears the marker: the ONE
    acceptance criterion of the plugin-manager lifecycle (docs/stacks/meshcore.md)."""
    root = env["LHPC_RUNTIME_ROOT"]
    wait_for(lambda: not (plugin_manager_pids(root) or plugin_manager_markers(root)),
             40, every=0.5)                                   # the node's stop_timeout budget
    assert plugin_manager_pids(root) == [], "plugin manager still running after the stack stop"
    assert not plugin_manager_markers(root), \
        "plugin-manager marker not cleared: the manager did not stop cleanly"


def test_release_meshcore_chat(env, svc):
    """Companion node only, plus its OPTIONAL Web UI.

    `lhpc stack start meshcore` does not bring an optional component up, so the Web UI is
    started by name — the way an operator starts it, and the only way this lane can claim it
    starts at all.
    """
    install_build(env, svc, "meshcore", timeout=2400)
    _meshcore_mode(env, "chat")
    _start(env, "meshcore", timeout=900)
    assert wait_tcp(port_of(env, "meshcore", MESHCORE_COMPANION), 180), readiness_failed(
        env, "meshcore", "meshcore-node", "MeshCore companion never opened TCP 5000")
    assert alive(env, "meshcore-node"), readiness_failed(
        env, "meshcore", "meshcore-node", "LHPC does not report meshcore-node running")
    _assert_meshcore_webui(env)
    stop(env, "meshcore")


def _assert_meshcore_webui(env) -> None:
    """The optional Web UI, started by name, serves its bundle and reaches the companion radio."""
    start_component(env, "meshcore-webui", stack="meshcore")
    assert_web_ui(env, "meshcore", "meshcore-webui", f"http://127.0.0.1:{port_of(env, 'meshcore', MESHCORE_WEBUI)}/",
                  timeout=180, page=MESHCORE_WEBUI_TITLE, api=("/api/health/deep", meshcore_webui_healthy))
    assert alive(env, "meshcore-webui"), readiness_failed(
        env, "meshcore", "meshcore-webui", "LHPC does not report meshcore-webui running")


def _assert_repeater_dashboard(env) -> None:
    """openHop's dashboard serves its own page, and LHPC's provisioning closed first-run setup."""
    assert_web_ui(env, "meshcore", "meshcore-node",
                  f"http://127.0.0.1:{port_of(env, 'meshcore', REPEATER_DASHBOARD)}/", timeout=240,
                  page=REPEATER_DASHBOARD_TITLE, api=("/api/needs_setup", openhop_set_up))


def test_release_meshcore_chat_repeater(env):
    """The repeater hosting the companion: the dashboard, the companion and the chat Web UI on
    that hosted companion all answer."""
    _meshcore_mode(env, "chat+repeater")
    _start(env, "meshcore", timeout=900)
    _assert_repeater_dashboard(env)
    assert wait_tcp(port_of(env, "meshcore", MESHCORE_COMPANION), 180), readiness_failed(
        env, "meshcore", "meshcore-node", "hosted companion never opened TCP 5000")
    _assert_meshcore_webui(env)
    _assert_plugin_manager_running(env)
    stop(env, "meshcore")
    _assert_plugin_manager_gone(env)


def test_release_meshcore_repeater(env):
    """Repeater only: the dashboard answers, and NO companion is hosted.

    The absence is the point of the mode. Asserting only the dashboard would pass just as well
    if the mode setting had been ignored and a companion were running beside it.
    """
    _meshcore_mode(env, "repeater")
    _start(env, "meshcore", timeout=900)
    _assert_repeater_dashboard(env)
    # Unmarked: mode wiring between LHPC's config and the upstream repeater, not one of the
    # four phases — a freeze would be the wrong remedy.
    assert not wait_tcp(port_of(env, "meshcore", MESHCORE_COMPANION), 20), \
        "repeater-only mode is hosting a companion on 5000"
    _assert_plugin_manager_running(env)
    stop(env, "meshcore")
    _assert_plugin_manager_gone(env)
    _meshcore_mode(env, "chat")


def test_release_meshcore_cli(env, svc):
    """The MeshCore CLI is an interactive component: proved on a terminal against the running
    companion, not by `--version`."""
    _meshcore_mode(env, "chat")                     # its own mode, never the previous case's last line
    _start(env, "meshcore", timeout=900)
    assert wait_tcp(port_of(env, "meshcore", MESHCORE_COMPANION), 180), readiness_failed(
        env, "meshcore", "meshcore-node", "MeshCore companion never opened TCP 5000")
    comp = next(c for c in svc.stack("meshcore").components if c.id == "meshcore-cli")
    inst = instances.copy_of(svc, "meshcore", named(env, "meshcore").partition("@")[2])[0]   # its companion's port
    try:
        pty_readiness(svc.manual_start_command(comp, inst), env, MESHCLI_DRAWS, ready_timeout=90,
                      stack="meshcore")
    finally:
        stop(env, "meshcore")


# --------------------------------------------------------------------------- 868, Reticulum


def test_release_reticulum(env, svc):
    """Reticulum's own client tool must list the LoRa interface this stack installed."""
    install_build(env, svc, "reticulum", timeout=3600)
    _start(env, "reticulum", timeout=900)
    assert alive(env, "rns"), readiness_failed(
        env, "reticulum", "rns", "LHPC does not report rns running")
    root = Path(env["LHPC_RUNTIME_ROOT"])
    # The venv lives in the node's own checkout (its `bin` and `build_marker` are declared
    # relative to it), so the tool's path follows the manifest's source path.
    rns = next(c for c in svc.stack("reticulum").components if c.id == "rns")
    rnstatus = svc._paths.resolve_source(rns.source.path) / ".venv" / "bin" / "rnstatus"
    assert rnstatus.exists(), (f"{stack_regression('reticulum', 'install')}\n"
                               f"reticulum did not install its own tools ({rnstatus})")
    out = ""

    def _rnstatus_lists_an_interface():
        nonlocal out
        r = subprocess.run([str(rnstatus), "--config", str(root / "state" / "reticulum")],
                           capture_output=True, text=True, timeout=60, check=False)
        out = r.stdout + r.stderr
        return "Interface" in out

    assert wait_for(_rnstatus_lists_an_interface, 120), readiness_failed(
        env, "reticulum", "rns", f"rnstatus listed no interface:\n{out[-1500:]}")
    iface = f"LoRa {named(env, 'reticulum').split('@', 1)[1]}"     # the section LHPC renders for this instance
    assert rnstatus_lists_interface(out, iface), (
        f"{stack_regression('reticulum', 'readiness')}\n"
        f"rnstatus does not list this instance's interface [{iface}]:\n{out[-1500:]}")
    # A busy channel, before lxmd / MeshChat / Sideband start (their announces would add TX lines). One path
    # request for an unknown destination first goes out answered OK, so the busy tries follow a successful send
    # on this connection (each result carries the next sequence, refused ones too). Then the lab's fake daemon
    # refuses the next framed 868 TX twice and flags the OK as a send after a CAD timeout; the next path request
    # must be logged as the interface logs it. Reticulum runs on 868 radios only. Logs are read from marks taken
    # after the first request, not by their mtime.
    rnpath = [str(rnstatus.with_name("rnpath")), "--config", str(root / "state" / "reticulum"), "-w", "8"]
    subprocess.run([*rnpath, os.urandom(16).hex()], capture_output=True, text=True, timeout=60, check=False)
    rflog = root / "logs" / iface.split(" ", 1)[1] / "rf-reticulum.log"
    txlog = root / "state" / "testlab" / "tx.jsonl"
    marks = {p: file_mark(p) for p in (rflog, txlog, *(root / "logs").glob("start-*.log"))}
    wrong = "the busy-channel check did not run"

    def _busy_try_logged():
        nonlocal wrong
        tx = [json.loads(line) for line in read_since(txlog, marks[txlog]).splitlines() if line.strip()]
        node = "".join(read_since(p, marks.get(p, 0)) for p in sorted((root / "logs").glob("start-*.log")))
        wrong = busy_case_wrong(tx, read_since(rflog, marks[rflog]), node, refusals=2)
        return not wrong
    set_lab_flags(env, txbusy_868=2, txcadtimeout_868=True)
    try:
        subprocess.run([*rnpath, os.urandom(16).hex()], capture_output=True, text=True, timeout=60, check=False)
        wait_for(_busy_try_logged, 30, every=1.0)
    finally:
        set_lab_flags(env, txbusy_868=0, txcadtimeout_868=False)      # re-read before lxmd starts
    assert not wrong, f"{stack_regression('reticulum', 'readiness')}\nbusy channel: {wrong}"
    # Optional components of this stack: not started by the stack, so started by name.
    start_component(env, "lxmd", stack="reticulum")
    assert alive(env, "lxmd"), readiness_failed(env, "reticulum", "lxmd", "lxmd did not come up")
    # MeshChat is a release deliverable too, and a stack start deliberately leaves it stopped —
    # so without starting it by name the lane shipped a browser client it never once ran. It
    # launches through the rns-client guard, which refuses unless the node above is really up,
    # and it serves its prebuilt bundle: the HTTP answer proves both the guard passed and the
    # bundle is where `get_file_path()` looks.
    start_component(env, "meshchat", stack="reticulum")
    # 200, not "any answer": the bundled UI requires no authentication of its own — the proxy is
    # where auth lives — so a 401/403 here would mean something other than MeshChat replied.
    assert_web_ui(env, "reticulum", "meshchat", f"http://127.0.0.1:{MESHCHAT_UI}/", timeout=180,
                  page=MESHCHAT_TITLE, api=("/api/v1/app/info", meshchat_attached))
    assert alive(env, "meshchat"), readiness_failed(
        env, "reticulum", "meshchat", "LHPC does not report meshchat running")
    # Sideband is a release deliverable of this stack, and the lane runs against a real X
    # display (the workflow starts Xvfb before it). So LHPC's GUI predicate dropping it here
    # means the GUI capability it needs is MISSING, not that this box is legitimately headless
    # — and printing that while passing let the whole component go unproved.
    # Unmarked: the predicate reads this box's display and toolkit as well as the stack.
    assert gui_startable(svc, "reticulum", "sideband"), (
        "LHPC's GUI predicate says Sideband cannot run here, but this lane has a display: the "
        "capability it requires is missing, which is a release defect")
    start_component(env, "sideband", stack="reticulum")
    assert alive(env, "sideband"), readiness_failed(
        env, "reticulum", "sideband", "sideband is startable here but did not come up")


def test_release_reticulum_nomadnet(env, svc):
    """NomadNet is interactive: proved on a terminal against the running Reticulum.

    Its first start has hung on a loaded runner (nothing drawn, the process alive), never
    reproduced elsewhere. A timeout is not retried: it fails unmarked, and before anything is
    stopped `reticulum_peers_snapshot` records who holds the shared instance's sockets and every
    Reticulum process's stack, so the next occurrence names its cause."""
    require_prerequisite(env, "reticulum", left_by="test_release_reticulum")
    comp = next(c for c in svc.stack("reticulum").components if c.id == "nomadnet")
    aborted = []

    def _peers():
        aborted.append(True)
        return reticulum_peers_snapshot(env)
    try:
        root = Path(env["LHPC_RUNTIME_ROOT"])
        # NomadNet's own log, and the start logs (those that exist) of the Reticulum node it
        # talks to and of the other Reticulum components.
        logs = (root / "state" / "nomadnet" / "logfile",
                *sorted(p for name in ("rns", "lxmd", "meshchat", "sideband")
                        for p in (root / "logs").glob(f"start-{name}-*.log")))
        pty_readiness(svc.manual_start_command(comp), env, NOMADNET_DRAWS, ready_timeout=90,
                      stack="reticulum", logs=logs, on_timeout=_peers)
    finally:
        # After a snapshot its processes were aborted on purpose: the stop then only tidies,
        # and must not replace the case's own failure with its own.
        stop(env, "reticulum", require=not aborted)


# --------------------------------------------------------------------------- binary stacks


def test_release_meshtastic(env, svc):
    """The published artifact, started against the simulated radio; the node answers its own
    CLI. A binary install has no source tree, so it is never `lhpc build`."""
    install_build(env, svc, "meshtastic", timeout=2400)
    _artifact_intact(svc, "meshtastic")
    _start(env, "meshtastic", timeout=900)
    assert wait_tcp(port_of(env, "meshtastic", MESHTASTIC_API), 180), readiness_failed(
        env, "meshtastic", "meshtastic", "meshtasticd never opened its API port 4403")
    info = run_lhpc(env, "meshtastic", "--info", timeout=300)
    assert info.returncode == 0, (f"{stack_regression('meshtastic', 'readiness')}\n"
                                  f"the node did not answer its own CLI (rc {info.returncode}): "
                                  f"{info.stdout[-1500:]}\n{info.stderr[-800:]}")
    web = _web_client_is_the_pinned_one(env)
    # meshtasticd serves that installed client itself, over its own self-signed TLS, and the
    # client's own poll path answers (PiWebServer.cpp at the firmware pin: no login on either).
    assert_web_ui(env, "meshtastic", "meshtastic", f"https://127.0.0.1:{port_of(env, 'meshtastic', MESHTASTIC_WEB)}/",
                  timeout=180, page=(web / "index.html").read_bytes(), api=("/api/v1/fromradio?all=false", None),
                  tls=True)
    _artifact_records_the_manifest_inputs(svc, "meshtastic")
    stop(env, "meshtastic")


def _web_client_is_the_pinned_one(env) -> Path:
    """The browser UI is LHPC's OWN pin, moved independently of the firmware: the binary install
    of meshtastic fetches it as the `meshtastic-web` component (selector `binary`). So the version
    that ends up installed must be the version the manifest names — otherwise a moved web pin
    could leave the box serving the old client while the manifest claimed the new one, and
    nothing else in this lane would notice.

    The fetch script writes its own provenance beside the assets; that file is the artifact's
    statement of what it carries.

    Version, digest and destination are read from the fetch step the packaged manifest declares
    (`meshtastic-web-assets.sh <dest> <version> <sha256>` is the script's own usage line), and
    the step belongs to the `meshtastic-web` component (its own component since Track F C3,
    with a static marker: the provenance file is the one statement of the installed version).
    """
    owner, argv = fetch_argv("meshtastic-web-assets.sh")
    assert len(argv) == 5, f"the web-client fetch step's argv changed shape: {argv}"
    dest, want_version, want_sha = argv[2], argv[3], argv[4]
    assert owner == "meshtastic-web", f"the web-client fetch step moved to {owner}"

    prov = Path(dest.replace("{runtime}", env["LHPC_RUNTIME_ROOT"])).parent / "web.provenance"
    assert prov.exists(), f"no web-client provenance at {prov}"
    got = dict(line.split("=", 1) for line in prov.read_text().splitlines() if "=" in line)
    assert got.get("web_version") == want_version, (
        f"the installed web client is {got.get('web_version')}, the manifest pins {want_version}")
    assert got.get("web_sha256") == want_sha, (
        f"the installed web client hashes {got.get('web_sha256')}, the manifest pins {want_sha}")
    return Path(dest.replace("{runtime}", env["LHPC_RUNTIME_ROOT"]))


def test_release_meshcom(env, svc):
    """The emulated MeshCom node from the published artifact: its web UI answers once the
    firmware has booted (502 until then — that wait is the point), titled with the node call
    LHPC set (the firmware writes `<node call> - Meshcom`, web_functions.cpp at its pin), showing
    the node information without a login (no web password by default; the login page has the same
    title) and loading its gzip-encoded stylesheet and script."""
    stop(env, "kiss", "graywolf", require=False)
    install_build(env, svc, "meshcom", timeout=2400)
    _artifact_intact(svc, "meshcom")
    _start(env, "meshcom", timeout=1200)
    inst = instances.resolve(svc, named(env, "meshcom"))[1]
    call = svc._resolved_param_value("meshcom", "run", "meshcom-qemu", "mc_callsign", inst=inst)
    url = f"http://127.0.0.1:{port_of(env, 'meshcom', MESHCOM_UI)}/"
    assert_web_ui(env, "meshcom", "meshcom-qemu", url, timeout=900, page=f"<title>{call} - Meshcom</title>")
    assert_meshcom_web_ui(env, url)
    stop(env, "meshcom")


# --------------------------------------------------------------------------- identity


def _candidate_pins() -> dict:
    """`{component id: (source path, pin commit, artifact?)}` for every pinned component of the
    REAL packaged manifest — not the lab overlay, which retargets the daemon and RadioLib at
    local fixtures."""
    return {c.id: (c.source.path, c.source.pin_commit, c.source.artifact)
            for st in load_manifest(default_manifest_path()) for c in st.components
            if c.source is not None and c.source.pin_commit}


def _binary_receipts(svc) -> dict:
    """`{stack id: (state, receipt, why)}` for every stack that has a binary channel."""
    from lhpc.core import binary_receipt
    out = {}
    for stack in svc.stacks():
        if svc.binary_spec(stack.id):
            out[stack.id] = binary_receipt.receipt_state(svc._paths, stack.id)
    return out


def _source_identity(svc, pins: dict, from_binary: set):
    """Every managed source checkout against its candidate pin.

    Returns `(checked, excluded, permitted, missing, wrong)`: what was proved, what is
    legitimately not a checkout here (a lab fixture or covered by an artifact), what LHPC's own
    GUI predicate allows to be absent, what is absent without that permission, and what is
    installed but is NOT the candidate."""
    from lhpc_testlab.manifest_overlay import RETARGETS

    from lhpc.core import source_registry

    config = svc.config()
    checked, excluded, permitted, missing, wrong = [], [], [], [], []
    for stack in svc.stacks():
        skippable = set(svc.gui_unavailable_components(stack))
        for comp in stack.components:
            if getattr(comp, "source", None) is None or comp.id not in pins:
                continue
            path, pin, is_artifact = pins[comp.id]
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
            if is_artifact:
                # `artifact = true` means every selector resolves to the SAME thing: the
                # declared artifact at the upstream default branch. The manifest's pin is not
                # honoured for such a source, so HEAD is expected to move when upstream does
                # and comparing the two would fail whenever it did. The verifier above is the
                # identity that IS meaningful here: this leaf is the one LHPC adopted and
                # still owns.
                checked.append(f"{comp.id} (artifact-head)")
                continue
            head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=60,
                                  check=True).stdout.strip()
            if head != pin:
                wrong.append(f"{stack.id}/{comp.id}: installed {head[:9]}, candidate manifest "
                             f"pins {pin[:9]}")
            else:
                checked.append(comp.id)
    return checked, excluded, permitted, missing, wrong


def _artifact_identity(svc, pins: dict, receipts: dict):
    """Every binary stack installed from its artifact, and the artifact carrying the candidate's
    commits for EVERY component it covers — the full map, not whatever it lists.

    Returns `(checked, missing, wrong)`."""
    checked, missing, wrong = [], [], []
    for stack_id, (state, rec, why) in receipts.items():
        if state != "valid":
            missing.append(f"{stack_id} (binary receipt {state}: {why})")
            continue
        for cid in svc.binary_spec(stack_id).covers:
            want = pins.get(cid)
            got = rec.components.get(cid)
            if want is None:
                continue
            if got is None:
                wrong.append(f"{stack_id}: the artifact records no commit for {cid}")
            elif got != want[1]:
                wrong.append(f"{stack_id}: artifact carries {cid} {got[:9]}, candidate manifest "
                             f"pins {want[1][:9]}")
        checked.append(f"{stack_id} (binary)")
    return checked, missing, wrong


def test_release_identity_matches_candidate_manifest(env, svc):
    """THE release check: what is installed IS the candidate, and NOTHING mandatory is missing.

    Every managed source is re-proved NOW through the production verifier (record + live HEAD).
    A PINNED source is then compared with the pin in the REAL packaged manifest — not the lab
    overlay, which retargets the daemon and RadioLib at local fixtures. An `artifact = true`
    source has no such comparison to make: every selector resolves to the upstream default
    branch, so the verifier above is its whole identity. Those two are named as the
    exception; their artifact is proved by the binary builder's own smoke and clean-runtime
    test, never here.

    An absent component is a FAILURE unless LHPC's own GUI predicate says it cannot run on this
    box. Collecting the absent ones and printing them would let a lane that installed nothing
    report success as loudly as one that installed everything.
    """
    require_all_passed()
    pins = _candidate_pins()
    receipts = _binary_receipts(svc)
    # A stack installed from an artifact has no managed checkout for the components that
    # artifact covers — the artifact IS the install, and its receipt is what proves it.
    from_binary = {cid for sid, (state, _rec, _why) in receipts.items() if state == "valid"
                   for cid in svc.binary_spec(sid).covers}

    checked, excluded, permitted, missing, wrong = _source_identity(svc, pins, from_binary)
    b_checked, b_missing, b_wrong = _artifact_identity(svc, pins, receipts)
    checked += b_checked
    missing += b_missing
    wrong += b_wrong

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


def _artifact_records_the_manifest_inputs(svc, stack_id: str) -> None:
    """The artifact's completion marker must agree with the manifest it was built for.

    The marker records the build inputs that are not commits (the web client, the CLI), and
    `is_built` recomputes them from the manifest. On the binary channel that comparison is the
    only thing standing between a moved web/CLI pin and a box that keeps the old one while
    reporting itself built. If a pin here moved without the artifact being republished, this is
    where the release stops.
    """
    comp = next(c for c in svc.stack(stack_id).components
                if c.build_marker and c.build_inputs)
    assert svc.is_built(comp), (
        f"the installed {stack_id} artifact does not record the manifest's build inputs "
        f"({dict(comp.build_inputs)}) — it was built for a different web-client/CLI pin and "
        f"must be republished before this release")
