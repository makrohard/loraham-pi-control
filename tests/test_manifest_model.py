"""Tests for the corrected manifest model.

Covers per-band daemons, provider/consumer socket roles, the MeshCom 433 DIRECT
default, and the MeshCore daemon-backed vs direct-SPI distinction.
"""

from __future__ import annotations

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
    assert any(p.name == "debug" and p.kind == "flag" for p in d.run_params)
    # Provides both band sockets/radios.
    provided = {r.key for r in d.resources if r.mode is ResourceMode.PROVIDER}
    assert {"loraham.daemon-socket.433", "loraham.daemon-socket.868"} <= provided


def test_daemon_spi_is_cooperative():
    spi = next(r for r in _index(load_manifest())["loraham-daemon"].resources
               if r.key == "spi.bus.0")
    assert spi.mode is ResourceMode.COOPERATIVE


def test_eight_stacks_each_with_a_main_component():
    stacks = {s.id: s for s in load_manifest()}
    assert set(stacks) == {"daemon", "chat", "igate", "voice", "kiss",
                           "meshtastic", "meshcom", "meshcore"}
    for sid, s in stacks.items():
        assert s.main and s.main_component is not None, sid
    assert stacks["daemon"].main == "loraham-daemon"
    assert stacks["meshcom"].main == "meshcom-qemu"


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
    node = comps["meshcore-pi"]
    assert node.band == "868"
    assert "loraham-daemon" in node.depends_on       # auto-starts the daemon on 868
    # Daemon-backed: consumes the 868 socket, requires a profile, claims NO SPI.
    keys = {r.key: r.mode for r in node.resources}
    assert keys["loraham.daemon-socket.868"] is ResourceMode.CONSUMER
    assert "spi.bus.0" not in keys
