"""End-to-end Companion integration: real meshcore_py client over TCP, real openHop
node underneath, RF exercised through the fake LoRaHAM daemon.

Proves the Phase-3 gate: app start, device query, self info, contacts, channels,
message send path, incoming message/push path, reconnect, idle survival, and
malformed-framing containment — against the CURRENT official client library.
"""

import asyncio
import contextlib
import os

import pytest
from meshcore import MeshCore
from openhop_core.companion.models import Contact
from openhop_core.protocol.constants import PAYLOAD_TYPE_TXT_MSG
from openhop_core.protocol.identity import LocalIdentity
from openhop_core.protocol.packet import Packet
from openhop_core.protocol.packet_builder import PacketBuilder

from fake_loraham_daemon import FakeLoRaHAMDaemon
from meshcore_host.app import HostApp
from meshcore_host.config import HostConfig


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


async def wait_for(predicate, timeout=5.0, interval=0.02):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


@pytest.fixture
async def daemon(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True)
    await d.start()
    yield d
    await d.close()


@pytest.fixture
async def app(tmp_path, daemon):
    key_file, ident = write_identity(tmp_path)
    cfg = host_config(tmp_path, daemon, key_file,
                      db=str(tmp_path / "state" / "companion.db"))
    application = HostApp(cfg)
    await application.start()
    # The adapter needs its daemon handshake before RF flows.
    assert await wait_for(lambda: application.radio.tx_ready)
    application.tcp_port = application.server._server.sockets[0].getsockname()[1]
    application.test_identity = ident
    yield application
    await application.stop()


@pytest.fixture
async def client(app):
    mc = await MeshCore.create_tcp("127.0.0.1", app.tcp_port, default_timeout=5)
    assert mc is not None, "meshcore_py failed to connect/appstart"
    yield mc
    with contextlib.suppress(Exception):
        await mc.disconnect()


def make_peer():
    return LocalIdentity()


async def inject_advert(daemon, peer, name="PEER"):
    pkt = PacketBuilder.create_advert(peer, name, route_type="flood")
    await daemon.send_rx(pkt.write_to())


# ---------------------------------------------------------------------------


async def test_appstart_self_info(app, client):
    info = client.self_info
    assert info["name"] == "TESTNODE"
    assert info["public_key"] == app.public_key_hex


async def test_device_query(app, client):
    res = await client.commands.send_device_query()
    assert res.type.name != "ERROR"
    payload = res.payload
    assert payload.get("model", "").startswith("LHPC")


async def test_contacts_initially_empty(app, client):
    res = await client.commands.get_contacts()
    assert res.type.name == "CONTACTS"
    assert res.payload == {}


async def test_advert_learns_contact_and_pushes(app, client, daemon):
    peer = make_peer()
    await inject_advert(daemon, peer, "ALICE")
    found = await wait_for(
        lambda: peer.get_public_key().hex()
        in app.companion.contacts.get_contact_dict()
        if hasattr(app.companion.contacts, "get_contact_dict")
        else app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    assert found, "advert did not create a contact"
    res = await client.commands.get_contacts()
    assert peer.get_public_key().hex() in res.payload
    assert res.payload[peer.get_public_key().hex()]["adv_name"] == "ALICE"


async def test_send_message_reaches_rf(app, client, daemon):
    peer = make_peer()
    await inject_advert(daemon, peer, "BOB")
    assert await wait_for(
        lambda: app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    before = len(daemon.tx_packets)
    res = await client.commands.send_msg(peer.get_public_key(), "hello over rf")
    assert res.type.name != "ERROR"
    assert await wait_for(lambda: len(daemon.tx_packets) > before)
    pkt = Packet()
    assert pkt.read_from(daemon.tx_packets[before])
    assert pkt.get_payload_type() == PAYLOAD_TYPE_TXT_MSG


async def test_incoming_message_push_path(app, client, daemon):
    peer = make_peer()
    await inject_advert(daemon, peer, "CAROL")
    assert await wait_for(
        lambda: app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    # Peer -> us: the peer only needs our public key to encrypt. PacketBuilder
    # takes the contact public key as hex.
    us = Contact(public_key=app.public_key_hex, name="TESTNODE")
    pkt, _crc = PacketBuilder.create_text_message(
        us, peer, "ping from rf", message_type="flood"
    )
    await daemon.send_rx(pkt.write_to())
    res = await wait_for_msg(client)
    assert res is not None
    assert res["text"] == "ping from rf"
    assert res["pubkey_prefix"] == peer.get_public_key().hex()[:12]


async def wait_for_msg(client, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        res = await client.commands.get_msg()
        if res.type.name == "CONTACT_MSG_RECV":
            return res.payload
        await asyncio.sleep(0.1)
    return None


async def test_channels_roundtrip(app, client):
    res = await client.commands.get_channel(0)
    assert res.type.name != "ERROR"
    set_res = await client.commands.set_channel(1, "testchan", b"\x11" * 16)
    assert set_res.type.name == "OK"
    back = await client.commands.get_channel(1)
    assert back.payload["channel_name"] == "testchan"
    assert back.payload["channel_secret"] == b"\x11" * 16


async def test_reconnect(app, client):
    await client.disconnect()
    mc2 = await MeshCore.create_tcp("127.0.0.1", app.tcp_port, default_timeout=5)
    assert mc2 is not None
    res = await mc2.commands.get_contacts()
    assert res.type.name == "CONTACTS"
    await mc2.disconnect()


async def test_idle_connection_survives(app, client):
    # No artificial idle timeout is configured at all.
    assert app.server._client_idle_timeout_sec is None
    await asyncio.sleep(2.0)  # idle
    res = await client.commands.send_device_query()
    assert res.type.name != "ERROR"


async def test_malformed_framing_contained(app):
    # Garbage on the TCP port must not wedge the server.
    reader, writer = await asyncio.open_connection("127.0.0.1", app.tcp_port)
    writer.write(b"\xde\xad\xbe\xef" * 64)
    await writer.drain()
    await asyncio.sleep(0.2)
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    mc = await MeshCore.create_tcp("127.0.0.1", app.tcp_port, default_timeout=5)
    assert mc is not None
    res = await mc.commands.send_device_query()
    assert res.type.name != "ERROR"
    await mc.disconnect()


async def test_startup_fails_closed_without_identity(tmp_path, daemon):
    cfg = host_config(tmp_path, daemon, tmp_path / "missing.key")
    with pytest.raises(Exception) as excinfo:
        HostApp(cfg)
    assert "identity" in str(excinfo.value).lower()


async def test_startup_fails_on_lax_identity_permissions(tmp_path, daemon):
    key_file, _ = write_identity(tmp_path)
    os.chmod(key_file, 0o644)
    cfg = host_config(tmp_path, daemon, key_file)
    with pytest.raises(Exception) as excinfo:
        HostApp(cfg)
    assert "600" in str(excinfo.value)
