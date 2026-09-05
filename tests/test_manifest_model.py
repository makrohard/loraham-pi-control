"""Tests for the corrected manifest model.

Covers per-band daemons, provider/consumer socket roles, the MeshCom 433 DIRECT
default, and the MeshCore daemon-backed vs direct-SPI distinction.
"""

from __future__ import annotations

import contextlib

from lhpc.core.manifest import load_manifest
from lhpc.core.model import ResourceMode


def _index(stacks):
    return {c.id: c for s in stacks for c in s.components}


def test_single_daemon_with_radio_run_param():
    comps = _index(load_manifest())
    d = comps["loraham-daemon"]
    radio = next(p for p in d.run_params if p.name == "radio")
    # `--radio both` was removed: lhpc runs one process per band, so the daemon offers only 433/868.
    assert radio.choices == ("433", "868") and radio.default == "433"
    # 0.2.9: the daemon's `debug` flag had no setter left (its only one was the retired start
    # page) — a knob the capability model advertises must be settable, so it is gone.
    assert not any(p.name == "debug" for p in d.run_params)
    # Provides both band sockets/radios.
    provided = {r.key for r in d.resources if r.mode is ResourceMode.PROVIDER}
    assert {"loraham.daemon-socket.433", "loraham.daemon-socket.868"} <= provided


def test_daemon_spi_is_cooperative():
    spi = next(r for r in _index(load_manifest())["loraham-daemon"].resources
               if r.key == "spi.bus.0")
    assert spi.mode is ResourceMode.COOPERATIVE


def test_every_stack_has_a_main_component():
    stacks = {s.id: s for s in load_manifest()}
    assert set(stacks) == {"daemon", "chat", "igate", "voice", "kiss", "graywolf",
                           "meshtastic", "meshcom", "meshcore", "reticulum"}
    for sid, s in stacks.items():
        assert s.main and s.main_component is not None, sid
    assert stacks["daemon"].main == "loraham-daemon"
    assert stacks["meshcom"].main == "meshcom-qemu"
    assert stacks["graywolf"].main == "graywolf"


def test_graywolf_is_package_managed_not_operator_installed():
    """graywolf's delivery contract, pinned.

    It is a PREBUILT upstream release unpacked into the runtime root: no git source, no system
    package, and therefore no `require` an operator has to satisfy by hand. The pieces have to
    agree or the stack is either uninstallable or silently stale:
      * a build step that runs the fetch script, carrying the pinned version;
      * `bin` under build/tools so `is_built` has an artifact to check;
      * a VERSION-BEARING build_marker, so bumping the pin reads as not-built and re-fetches;
      * process identity narrowed by an argv token, so a leftover system graywolf is not claimed.
    """
    comps = _index(load_manifest())
    gw = comps["graywolf"]

    assert gw.source is None                       # nothing is cloned
    assert gw.requires == ()                       # nothing for the operator to install
    assert gw.build_steps, "the artifact must be produced by a build step"

    argv = list(gw.build_steps[0]["argv"])
    assert any("graywolf-fetch.sh" in tok for tok in argv)
    version = argv[-1]
    assert version.count(".") == 2, f"expected a pinned version as the last argv token, got {version!r}"

    assert gw.bin == "build/tools/graywolf/usr/bin/graywolf"
    # A bump must invalidate "built": the marker name carries the same version the step fetches.
    assert gw.build_marker and version in gw.build_marker
    # ... and it must NOT be the fetch script's own stamp, which LHPC would delete before each
    # build and so defeat the script's offline short-circuit.
    assert not gw.build_marker.endswith(".lhpc-graywolf-version")

    assert gw.process is not None and gw.process.exec_name == "graywolf"
    assert gw.process.all_args, "exec_name alone would claim a leftover system graywolf"


def test_provider_consumer_socket_roles():
    comps = _index(load_manifest())
    # daemon provides 433 socket; bridge consumes it.
    bridge = comps["meshcom-bridge"]
    consumed = [r for r in bridge.resources if r.key == "loraham.daemon-socket.433"]
    assert consumed and consumed[0].mode is ResourceMode.CONSUMER


def test_meshcom_default_is_433_managed():
    # The bridge delegates channel access to the daemon and forces SET TXMODE=MANAGED on
    # connect (the QEMU firmware has no real radio to run its own CAD), so it REQUIRES the
    # daemon in MANAGED — both the field and the daemon-profile resource say so.
    comps = _index(load_manifest())
    bridge = comps["meshcom-bridge"]
    assert bridge.band == "433"
    assert "loraham-daemon" in bridge.depends_on and bridge.requires_daemon_tx == "MANAGED"
    profile = next(r for r in bridge.resources if r.key == "loraham.profile.433")
    assert profile.mode is ResourceMode.REQUIREMENT and profile.requirement == "MANAGED"
    # No 868 socket in the default MeshCom path.
    assert all("868" not in r.key for r in bridge.resources)


def test_meshtastic_is_rootless_multiband():
    comps = _index(load_manifest())
    m = comps["meshtastic"]
    assert m.bands == ("433", "868")            # band-switchable
    assert not m.units                          # no systemd — lhpc runs it directly
    assert "meshtasticd -c" in m.run_cmd and "-d" in m.run_cmd   # rootless user process
    keys = {r.key for r in m.resources}
    assert {"loraham.radio.433", "loraham.radio.868"} <= keys    # conflicts with daemon
    # per-band Lora pins come from band_defaults on the config-file params
    fp = {p.name: p for p in m.config_file.params}
    assert dict(fp["cs"].band_defaults) == {"433": "8", "868": "7"}
    # binary + SPI declared as system requirements (apt / SPI overlay)
    assert any("meshtasticd" in r.install for r in m.requires)


def test_stack_dependencies_apps_depend_on_daemon():
    from lhpc.core.status import stack_dependencies
    deps = stack_dependencies(load_manifest())
    assert deps["daemon"] == []                 # foundation has no stack deps
    for sid in ("chat", "igate", "kiss", "meshcom", "meshcore"):
        assert "daemon" in deps[sid], sid       # app stacks depend on the daemon stack
    assert deps["meshtastic"] == []             # meshtastic is direct, no daemon


def test_native_chat_igate_are_daemon_backed():
    comps = _index(load_manifest())
    for cid in ("loraham-chat", "loraham-igate"):
        assert "loraham-daemon" in comps[cid].depends_on


def test_optional_serial_kiss_requires_socat():
    comps = _index(load_manifest())
    serial = comps["loraham-kiss-serial"]
    assert "loraham-kiss-tnc" in serial.depends_on
    assert any(r.cmd == "socat" and "apt" in r.install for r in serial.requires)
    # exposes a PTY path endpoint, not a TCP port
    assert any(e.kind == "path" for e in serial.endpoints)


def test_meshcore_daemon_backed_does_not_claim_direct_spi():
    comps = _index(load_manifest())
    node = comps["meshcore-node"]
    assert node.band == "868"
    assert "loraham-daemon" in node.depends_on       # auto-starts the daemon on 868
    # Daemon-backed: consumes the 868 socket, requires a profile, claims NO SPI.
    keys = {r.key: r.mode for r in node.resources}
    assert keys["loraham.daemon-socket.868"] is ResourceMode.CONSUMER
    assert "spi.bus.0" not in keys


def test_gui_stacks_keep_their_state_out_of_a_read_only_home():
    # lhpc-web.service runs ProtectHome=read-only. Both desktop GUIs create a state
    # directory at import/startup, so each MUST be redirected into the runtime root or it
    # dies with EROFS before its window exists. The unit's `-%h/.meshcore_nm` grant is
    # OPTIONAL (leading `-`), so on a fresh box that path is never made writable — the
    # env var is what actually fixes it, and the units are byte-frozen anyway.
    comps = _index(load_manifest())
    for cid, var in (("sideband", "KIVY_HOME"),):
        env = dict(comps[cid].run_env or ())      # run_env is a tuple of pairs, not a dict
        assert var in env, f"{cid}: {var} missing — a read-only HOME would break startup"
        assert env[var].startswith("{runtime}/"), \
            f"{cid}: {var}={env[var]!r} must live under the runtime root, not HOME"


def test_every_tcp_listener_endpoint_carries_its_port_claim():
    """A TCP listener endpoint without a matching `tcp.port.<n>` claim is invisible to the
    conflict machinery: another stack wanting that port would be admitted with nothing shown.
    meshtastic's 4403/9443 were the only two such endpoints — this pins the rule for every
    future one. External endpoints are exempt (observe-only, not ours to claim)."""
    import re

    from lhpc.core.manifest import load_manifest
    for s in load_manifest():
        for c in s.components:
            claimed = {r.key for r in c.resources}
            for e in c.endpoints:
                if e.kind != "tcp" or getattr(e, "external", False) \
                        or getattr(e, "role", "") != "listener":
                    continue
                m = re.search(r":(\d+)$", str(e.address))
                assert m, (c.id, e.address)
                assert f"tcp.port.{m.group(1)}" in claimed, (
                    f"{s.id}/{c.id}: endpoint {e.address} has no tcp.port.{m.group(1)} claim")


def test_meshcore_webui_and_cli_force_both_dashboard_links():
    # The dashboard shows a config link when a component has tunables and a log link when lhpc
    # captures a start log. meshcore-webui has no tunables (config withheld) and meshcore-cli is
    # operator-run/interactive (log withheld), so each explicitly forces the missing link on to
    # match its sibling rows (meshcore-node has both).
    comps = _index(load_manifest())
    webui = comps["meshcore-webui"]
    cli = comps["meshcore-cli"]
    assert webui.show_config_link is True and webui.show_log_link is True
    assert cli.show_config_link is True and cli.show_log_link is True


def test_meshcore_cli_and_webui_are_exclusive_companion_client_conflict():
    # The openHop node serves ONE Companion client at a time, so meshcore-cli and meshcore-webui
    # both EXCLUSIVE-claim the same companion-client slot. Running both must register as an
    # observed conflict (Apps-page banner + component card); one alone is declared-only.
    from lhpc.core.resources import interpret_conflicts

    comps = _index(load_manifest())
    webui, cli = comps["meshcore-webui"], comps["meshcore-cli"]
    key = "meshcore.companion-client"
    for comp in (webui, cli):
        claim = next(r for r in comp.resources if r.key == key)
        assert claim.mode is ResourceMode.EXCLUSIVE

    both = interpret_conflicts([webui, cli], {"meshcore-webui", "meshcore-cli"})
    active = [c for c in both if c.resource_key == key and c.observed]
    assert len(active) == 1
    assert set(active[0].holders) == {"meshcore-webui", "meshcore-cli"}

    one = interpret_conflicts([webui, cli], {"meshcore-webui"})
    assert all(not c.observed for c in one if c.resource_key == key)


def test_companion_client_conflict_is_advisory_not_start_blocking():
    # The claim is ADVISORY: it must SHOW the conflict (banner/card via observed_conflicts) but
    # never block a start — the WebUI yields the slot to a running CLI at runtime (lock handoff),
    # so admission must let the CLI start. observed_conflicts keeps it; _running_conflicts drops it.
    from lhpc.core.resources import interpret_conflicts

    comps = _index(load_manifest())
    key = "meshcore.companion-client"
    for cid in ("meshcore-webui", "meshcore-cli"):
        claim = next(r for r in comps[cid].resources if r.key == key)
        assert claim.advisory is True

    both = interpret_conflicts(
        [comps["meshcore-webui"], comps["meshcore-cli"]],
        {"meshcore-webui", "meshcore-cli"},
    )
    adv = next(c for c in both if c.resource_key == key)
    assert adv.observed is True and adv.advisory is True  # shown, but flagged non-blocking


def test_meshcore_webui_patch_guards_gps_policy_and_cli_slot():
    # The shipped WebUI patch must carry BOTH LHPC ownership guards, so a pinned-SHA bump that
    # silently drops them fails review (and the patch also fails to apply if the code moved).
    from pathlib import Path

    patch = (
        Path(__file__).parents[1] / "lhpc/data/patches/meshcore-webui-lhpc-guards.patch"
    ).read_text()
    # GPS-policy guard: reject adv_loc_policy and DROP the set_advert_loc_policy call.
    assert "adv_loc_policy is not None" in patch
    assert "managed by LHPC" in patch
    assert "-        await _call(client.set_advert_loc_policy(" in patch
    # CLI-slot yield: the supervisor ACQUIRES and HOLDS the flock lease across its connect (atomic
    # check+connect, so a CLI can't slip in and be evicted), never a probe-then-release nor mere
    # file existence.
    assert "MESHCORE_CLI_LOCK" in patch
    assert "_acquire_slot_lease" in patch and "_release_slot_lease" in patch
    assert "fcntl.flock" in patch
    assert "os.path.exists(cli_lock)" not in patch and "_cli_owns_slot" not in patch


def test_meshcore_cli_wrapped_for_slot_handoff():
    # The three pieces of the handoff: the CLI runs through the lock wrapper, the wrapper ships
    # and removes the lock on ANY exit, and the WebUI launcher exports the SAME lock path.
    from pathlib import Path

    root = Path(__file__).parents[1]
    cli = _index(load_manifest())["meshcore-cli"]
    assert "scripts/meshcli-run.sh" in cli.run_cmd
    assert "state/meshcore-cli.lock" in cli.run_cmd

    wrapper = root / "lhpc/data/scripts/meshcli-run.sh"
    body = wrapper.read_text()
    # Ownership is a kernel flock LEASE (crash/reboot-safe), never file existence + a fragile trap.
    # BLOCKING (with a timeout), so the CLI waits for the WebUI's transient connect lease rather
    # than racing in with `-n` and getting evicted.
    assert 'exec 9>"$LOCK"' in body and "flock -w 30 9" in body

    webui_run = (root / "lhpc/data/scripts/meshcore-webui-run.sh").read_text()
    assert "MESHCORE_CLI_LOCK" in webui_run and "state/meshcore-cli.lock" in webui_run


def test_meshcli_wrapper_flock_lease_is_crash_safe(tmp_path):
    # Exercises the REAL shipped wrapper: it holds a KERNEL flock lease while the CLI runs, and the
    # kernel drops that lease when the process tree dies — even by SIGKILL (a stand-in for a
    # power-loss/reboot). A leftover lock PATHNAME with no live owner must therefore read as free,
    # so the WebUI never yields forever after a CLI crash. This is the exact probe the supervisor
    # uses (fcntl.flock, non-blocking).
    import fcntl
    import os
    import signal
    import subprocess
    import time
    from pathlib import Path

    wrapper = Path(__file__).parents[1] / "lhpc/data/scripts/meshcli-run.sh"
    lock = str(tmp_path / "state" / "meshcore-cli.lock")

    def lease_free() -> bool:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    # Own process group so we can SIGKILL the whole tree (wrapper + child), like a reboot.
    proc = subprocess.Popen(
        ["bash", str(wrapper), lock, "sleep", "30"], start_new_session=True
    )
    try:
        held = False
        for _ in range(100):
            if os.path.exists(lock) and not lease_free():
                held = True
                break
            time.sleep(0.05)
        assert held, "wrapper must HOLD the flock lease while the CLI runs"

        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # no trap can run — kernel must release
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)

    freed = False
    for _ in range(100):
        if lease_free():
            freed = True
            break
        time.sleep(0.05)
    assert freed, "a leftover lock pathname with no live owner must NOT read as ownership"
    assert os.path.exists(lock), "the lock file survives — only the kernel lease is gone"


def test_meshcli_wrapper_waits_for_a_transient_lease_holder(tmp_path):
    # Deterministic race barrier: while the WebUI holds the transition lease across its own connect,
    # the CLI wrapper must WAIT (blocking flock), not race in and get evicted. It runs its command
    # only after the lease is released — reproducing "WebUI decided FREE, CLI acquires, WebUI
    # connects" and proving the CLI is never evicted by that stale decision.
    import fcntl
    import os
    import subprocess
    import time
    from pathlib import Path

    wrapper = Path(__file__).parents[1] / "lhpc/data/scripts/meshcli-run.sh"
    lock = str(tmp_path / "state" / "meshcore-cli.lock")
    marker = tmp_path / "cli-ran"
    os.makedirs(os.path.dirname(lock), exist_ok=True)

    # Simulate the WebUI holding the lease through its connect (the barrier).
    holder = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(holder, fcntl.LOCK_EX)
    proc = subprocess.Popen(["bash", str(wrapper), lock, "touch", str(marker)])
    try:
        time.sleep(1.5)
        assert not marker.exists(), "CLI must WAIT while the transition lease is held (no race-in)"
        fcntl.flock(holder, fcntl.LOCK_UN)  # WebUI finished connecting -> release
        proc.wait(timeout=15)
        assert marker.exists(), "CLI must run once the transition lease is released"
    finally:
        with contextlib.suppress(Exception):
            os.close(holder)
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Voice on a headless/Lite box: terminal variant beside the GTK app
# ---------------------------------------------------------------------------

def test_voice_terminal_variant_shape():
    """The voice stack carries a -DNO_GTK ncurses variant so a Lite box gets Voice
    without any graphical environment: interactive like chat/nomadnet (manual
    readiness -> never autostarted, dashboard shows the copy-paste command), GTK
    strictly absent from its build, and both components share one pinned source
    and the exclusive audio device."""
    comps = _index(load_manifest())
    gtk, cli = comps["loraham-voice"], comps["loraham-voice-cli"]

    # Lite run model: operator-started in a real TTY, never a background service.
    assert cli.interactive is True and cli.readiness == "manual"
    # Desktop unchanged: the GTK app stays a normal lhpc-started service...
    assert gtk.interactive is False and gtk.readiness == "process"
    # ...but is GUI-OPTIONAL, so the GUI preflight drops it headless instead of
    # skipping the whole stack — while a default desktop `stack start` still seeds it
    # (plain `optional` would not).
    assert gtk.gui_optional is True and gtk.optional is False

    # The CLI build must never touch the GTK toolchain; the GTK build keeps it.
    cli_argv = [tok for step in cli.build_steps for tok in step.get("argv", ())]
    assert "-DNO_GTK" in cli_argv and not any("gtk" in t for t in cli_argv)
    gtk_argv = [tok for step in gtk.build_steps for tok in step.get("argv", ())]
    assert any("gtk" in t for t in gtk_argv)

    # None of the CLI variant's requires are gui-scoped (buildable on Lite).
    assert not any(getattr(r, "gui", False) for r in cli.requires)

    # One source, one pin — two builds of the same checkout.
    assert gtk.source.path == cli.source.path
    assert gtk.source.pin_commit == cli.source.pin_commit

    # Both grab the same exclusive audio device, so they can never run at once.
    assert {r.key for r in cli.resources} == {"audio.default"}


def test_the_meshtastic_cli_is_an_on_demand_component_not_a_start_hint():
    """0.2.9: the managed CLI is listed on the Dashboard as an interactive on-demand component
    (like the MeshCore CLI) with its copyable launch line; the start-time hint is gone."""
    from lhpc.core.model import ComponentKind
    comps = _index(load_manifest())
    cli, node = comps["meshtastic-cli"], comps["meshtastic"]
    assert cli.kind is ComponentKind.ONESHOT and cli.optional and cli.interactive
    assert cli.readiness == "manual" and cli.depends_on == ("meshtastic",)
    assert list(cli.run_argv) == ["lhpc", "meshtastic", "--help"] and cli.process is None
    assert cli.tx_capable                                          # it transmits through the node
    assert not node.start_note                                     # the hint is retired
