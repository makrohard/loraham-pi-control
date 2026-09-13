"""Shared helpers of the meshcore_host suite: a plain module beside fake_loraham_daemon.py, so no
test module imports another (tests/README.md, rule 7). A radio on the fake daemon, an identity
file, a host config, a peer and its advert, and the poll that every asynchronous check uses."""

import asyncio
import os

from meshcore_host.config import HostConfig
from meshcore_host.loraham_radio import LoRaHAMRadio
from openhop_core.protocol.identity import LocalIdentity
from openhop_core.protocol.packet_builder import PacketBuilder


def make_radio(daemon, **overrides):
    kwargs = {
        "data_socket": str(daemon.data_socket),
        "config_socket": str(daemon.config_socket),
        "frequency": 869618000,
        "bandwidth": 62500,
        "spreading_factor": 8,
        "coding_rate": 8,
        "txpower": 14,
        "preamble": 16,
        "enable_tx": True,
        "connect_timeout": 1.0,
        "reconnect_delay": 0.2,
        "tx_result_margin": 0.5,
        "noise_poll_interval": 0.05,
        "resolve_sockets": False,
    }
    kwargs.update(overrides)
    return LoRaHAMRadio(**kwargs)


async def wait_for(predicate, timeout=5.0, interval=0.02):
    """Poll until `predicate()` is truthy: its value, or None once the deadline has passed."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


def write_identity(tmp_path):
    ident = LocalIdentity()
    seed = ident.signing_key.encode()
    key_file = tmp_path / "meshcore_identity.key"
    key_file.write_text(seed.hex() + "\n")
    os.chmod(key_file, 0o600)
    return key_file, ident


def host_config(tmp_path, daemon, key_file, **overrides):
    cfg = HostConfig(
        name="TESTNODE",
        bind="127.0.0.1",
        port=0,
        key_file=str(key_file),
        data_socket=str(daemon.data_socket),
        config_socket=str(daemon.config_socket),
        frequency=869618000,
        bandwidth=62500,
        spreading_factor=8,
        coding_rate=8,
        txpower=14,
        txmaxpower=14,
        preamble=16,
        enable_tx=True,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_peer():
    return LocalIdentity()


async def inject_advert(daemon, peer, name="PEER"):
    pkt = PacketBuilder.create_advert(peer, name, route_type="flood")
    await daemon.send_rx(pkt.write_to())
