"""Two-node RF simulation over REAL openHop protocol logic.

Two complete production stacks — meshcore_py client ⇄ CompanionFrameServer ⇄
CompanionRadio ⇄ LoRaHAMRadio ⇄ fake LoRaHAM daemon — cross-linked by a virtual
ether that forwards every transmitted RF payload to the other daemon's RX path.
Nothing protocol-level is mocked: every byte crosses the same code that will run
on air, and the CURRENT official meshcore_py acts as both operators' client.

Covers: adverts/contact learning both ways, direct messages both directions,
delivery ACKs, path learning + direct-path reuse, flood fallback after reset,
channel messages, scoped (transport) floods, path-hash modes, TRACE TX shape,
unknown-command error behaviour, and malformed-RF containment.
"""

import asyncio
import contextlib
import struct

import pytest
from meshcore import EventType, MeshCore
from openhop_core.protocol.constants import (
    PAYLOAD_TYPE_ADVERT,
    PAYLOAD_TYPE_GRP_TXT,
    PAYLOAD_TYPE_TRACE,
    PAYLOAD_TYPE_TXT_MSG,
    ROUTE_TYPE_DIRECT,
    ROUTE_TYPE_FLOOD,
    ROUTE_TYPE_TRANSPORT_FLOOD,
)
from openhop_core.protocol.packet import Packet

from fake_loraham_daemon import FakeLoRaHAMDaemon
from meshcore_host.app import HostApp
from test_companion_integration import host_config, wait_for, write_identity


class Ether:
    """Bidirectional lossless RF link between two fake daemons."""

    def __init__(self, daemon_a, daemon_b, *, rssi_cdbm=-7500, snr_cdb=800):
        self.log = []  # every payload on the air, in order
        self._paused = False

        async def a_to_b(payload):
            self.log.append(("A", bytes(payload)))
            if not self._paused and daemon_b.data_writer is not None:
                await daemon_b.send_rx(payload, rssi_cdbm=rssi_cdbm, snr_cdb=snr_cdb)

        async def b_to_a(payload):
            self.log.append(("B", bytes(payload)))
            if not self._paused and daemon_a.data_writer is not None:
                await daemon_a.send_rx(payload, rssi_cdbm=rssi_cdbm, snr_cdb=snr_cdb)

        daemon_a.on_tx = a_to_b
        daemon_b.on_tx = b_to_a

    def parsed(self, sender=None):
        out = []
        for who, payload in self.log:
            if sender is not None and who != sender:
                continue
            pkt = Packet()
            if pkt.read_from(payload):
                out.append((who, pkt, payload))
        return out


class Node:
    def __init__(self, name):
        self.name = name
        self.dir = None
        self.daemon = None
        self.app = None
        self.mc = None


async def start_node(tmp_path, name):
    node = Node(name)
    node.dir = tmp_path / name
    node.dir.mkdir()
    node.daemon = FakeLoRaHAMDaemon(node.dir, tx=True)
    await node.daemon.start()
    key_file, _ = write_identity(node.dir)
    cfg = host_config(node.dir, node.daemon, key_file,
                      name=name, db=str(node.dir / "companion.db"))
    node.app = HostApp(cfg)
    await node.app.start()
    assert await wait_for(lambda: node.app.radio.tx_ready)
    port = node.app.server._server.sockets[0].getsockname()[1]
    node.mc = await MeshCore.create_tcp("127.0.0.1", port, default_timeout=8)
    assert node.mc is not None
    return node


async def stop_node(node):
    with contextlib.suppress(Exception):
        if node.mc:
            await node.mc.disconnect()
    with contextlib.suppress(Exception):
        if node.app:
            await node.app.stop()
    with contextlib.suppress(Exception):
        if node.daemon:
            await node.daemon.close()


@pytest.fixture
async def mesh(tmp_path):
    a = await start_node(tmp_path, "NODEA")
    b = await start_node(tmp_path, "NODEB")
    ether = Ether(a.daemon, b.daemon)
    yield a, b, ether
    await stop_node(a)
    await stop_node(b)


async def learn_each_other(a, b, ether):
    """Advert both ways so each node has the other as a contact."""
    res = await a.mc.commands.send_advert(flood=True)
    assert res.type != EventType.ERROR
    assert await wait_for(
        lambda: b.app.companion.contacts.get_by_key(
            bytes.fromhex(a.app.public_key_hex)) is not None, timeout=8.0)
    res = await b.mc.commands.send_advert(flood=True)
    assert res.type != EventType.ERROR
    assert await wait_for(
        lambda: a.app.companion.contacts.get_by_key(
            bytes.fromhex(b.app.public_key_hex)) is not None, timeout=8.0)


# ---------------------------------------------------------------------------


async def test_adverts_learn_contacts_both_ways(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)
    ca = a.app.companion.contacts.get_by_key(bytes.fromhex(b.app.public_key_hex))
    cb = b.app.companion.contacts.get_by_key(bytes.fromhex(a.app.public_key_hex))
    assert ca.name == "NODEB"
    assert cb.name == "NODEA"
    # On-air advert shape: flood route, ADVERT payload.
    adverts = [p for _, p, _ in ether.parsed()
               if p.get_payload_type() == PAYLOAD_TYPE_ADVERT]
    assert adverts and all(
        p.get_route_type() in (ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD)
        for p in adverts
    )


async def test_direct_message_both_directions_with_ack(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)

    # A -> B. Subscribe to the ACK push BEFORE sending (delivery is near-instant).
    ack_task = asyncio.create_task(a.mc.wait_for_event(EventType.ACK, timeout=10.0))
    await asyncio.sleep(0.05)
    mark = len(ether.log)
    res = await a.mc.commands.send_msg(bytes.fromhex(b.app.public_key_hex), "hi B")
    assert res.type == EventType.MSG_SENT
    msg = await wait_for_contact_msg(b.mc)
    assert msg is not None and msg["text"] == "hi B"
    # A real acknowledgement crossed the air from B: for a flood DM the firmware
    # replies with a PATH packet carrying the embedded ack (returns the route);
    # for a direct DM it is a bare ACK packet. Accept either shape.
    from openhop_core.protocol.constants import PAYLOAD_TYPE_ACK, PAYLOAD_TYPE_PATH
    def acks_from_b():
        out = []
        for who, payload in ether.log[mark:]:
            if who != "B":
                continue
            pkt = Packet()
            if pkt.read_from(payload) and pkt.get_payload_type() in (
                    PAYLOAD_TYPE_ACK, PAYLOAD_TYPE_PATH):
                out.append(pkt)
        return out
    assert await wait_for(lambda: acks_from_b(), timeout=8.0), \
        "no ACK/PATH-with-ack packet on air"
    # ...and reached A's client as an ACK push (delivery confirmed end to end).
    ack = await ack_task
    assert ack is not None

    # B -> A
    res = await b.mc.commands.send_msg(bytes.fromhex(a.app.public_key_hex), "hi A")
    assert res.type == EventType.MSG_SENT
    msg = await wait_for_contact_msg(a.mc)
    assert msg is not None and msg["text"] == "hi A"


async def wait_for_contact_msg(mc, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        res = await mc.commands.get_msg()
        if res.type == EventType.CONTACT_MSG_RECV:
            return res.payload
        await asyncio.sleep(0.1)
    return None


async def test_path_learning_and_direct_reuse(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)

    # First exchange teaches the return path (zero-hop => direct, out_path_len 0).
    await a.mc.commands.send_msg(bytes.fromhex(b.app.public_key_hex), "teach")
    assert await wait_for_contact_msg(b.mc) is not None
    contact_b = a.app.companion.contacts.get_by_key(bytes.fromhex(b.app.public_key_hex))
    assert await wait_for(lambda: contact_b.out_path_len >= 0, timeout=8.0), \
        "path was not learned from the exchange"

    # Second message must go ROUTE_TYPE_DIRECT on the air.
    mark = len(ether.log)
    await a.mc.commands.send_msg(bytes.fromhex(b.app.public_key_hex), "direct now")
    assert await wait_for_contact_msg(b.mc) is not None
    txt_after = [p for who, p, _ in ether.parsed("A")[len([
        x for x in ether.parsed("A")]):] ] # placeholder, replaced below
    new_packets = []
    for who, payload in ether.log[mark:]:
        if who != "A":
            continue
        pkt = Packet()
        if pkt.read_from(payload) and pkt.get_payload_type() == PAYLOAD_TYPE_TXT_MSG:
            new_packets.append(pkt)
    assert new_packets, "no TXT_MSG seen after path learned"
    assert any(p.get_route_type() == ROUTE_TYPE_DIRECT for p in new_packets), \
        "learned path was not reused as a direct route"


async def test_flood_fallback_after_reset_path(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)
    await a.mc.commands.send_msg(bytes.fromhex(b.app.public_key_hex), "teach")
    assert await wait_for_contact_msg(b.mc) is not None

    res = await a.mc.commands.reset_path(bytes.fromhex(b.app.public_key_hex))
    assert res.type != EventType.ERROR
    mark = len(ether.log)
    await a.mc.commands.send_msg(bytes.fromhex(b.app.public_key_hex), "flood again")
    assert await wait_for_contact_msg(b.mc) is not None
    floods = []
    for who, payload in ether.log[mark:]:
        if who != "A":
            continue
        pkt = Packet()
        if pkt.read_from(payload) and pkt.get_payload_type() == PAYLOAD_TYPE_TXT_MSG:
            floods.append(pkt.get_route_type())
    assert floods and floods[0] in (ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD)


async def test_channel_message_between_nodes(mesh):
    a, b, ether = mesh
    secret = b"\x5a" * 16
    for node in (a, b):
        res = await node.mc.commands.set_channel(1, "simchan", secret)
        assert res.type != EventType.ERROR
    res = await a.mc.commands.send_chan_msg(1, "hello channel")
    assert res.type != EventType.ERROR
    got = await wait_for_channel_msg(b.mc)
    assert got is not None
    assert got["text"].endswith("hello channel")
    # On-air: GRP_TXT payload.
    grp = [p for _, p, _ in ether.parsed("A")
           if p.get_payload_type() == PAYLOAD_TYPE_GRP_TXT]
    assert grp


async def wait_for_channel_msg(mc, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        res = await mc.commands.get_msg()
        if res.type == EventType.CHANNEL_MSG_RECV:
            return res.payload
        await asyncio.sleep(0.1)
    return None


async def test_trace_tx_shape(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)
    mark = len(ether.log)
    # TRACE with an explicit (nonexistent-repeater) path: verify the on-air shape
    # only — companions do not answer traces, so no reply is expected.
    res = await a.mc.commands.send_trace(path="23")
    assert res.type != EventType.ERROR
    assert await wait_for(lambda: len(ether.log) > mark, timeout=8.0)
    traces = []
    for who, payload in ether.log[mark:]:
        if who != "A":
            continue
        pkt = Packet()
        if pkt.read_from(payload) and pkt.get_payload_type() == PAYLOAD_TYPE_TRACE:
            traces.append((pkt, payload))
    assert traces, "no TRACE packet on the air"
    pkt, payload = traces[0]
    # TRACE payload = tag(4) + auth(4) + flags(1) + hop hashes; the packet's
    # routing path field stays empty (firmware createTrace / MyMesh.cpp).
    assert pkt.get_route_type() in (ROUTE_TYPE_DIRECT,)
    assert bytes(pkt.path) == b""
    assert len(pkt.payload) == 10
    assert bytes(pkt.payload[9:]) == bytes.fromhex("23")
    flags = pkt.payload[8]
    # Default flags: 1-byte path hashes (low two bits select 1 << s).
    assert (1 << (flags & 0x03)) == 1


async def test_transport_scoped_flood_on_air(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)
    # Adverts are always scoped with the persisted DEFAULT scope (firmware
    # CMD_SEND_SELF_ADVERT semantics), so set that.
    res = await a.mc.commands.set_default_flood_scope("region1")
    assert res is None or getattr(res, "type", None) != EventType.ERROR
    mark = len(ether.log)
    res = await a.mc.commands.send_advert(flood=True)
    assert res.type != EventType.ERROR
    assert await wait_for(lambda: len(ether.log) > mark, timeout=8.0)
    routes = []
    for who, payload in ether.log[mark:]:
        if who == "A":
            pkt = Packet()
            if pkt.read_from(payload):
                routes.append(pkt.get_route_type())
    assert ROUTE_TYPE_TRANSPORT_FLOOD in routes, f"no transport flood on air: {routes}"


async def test_path_hash_mode_two_byte(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)
    # 2-byte path hashes (mode 1): messages still deliver, and the learned
    # direct path stays usable.
    for node in (a, b):
        # CMD_SET_PATH_HASH_MODE (61): [subtype=0][mode]; mode 1 = 2-byte hashes.
        res = await node.mc.commands.send(b"\x3d\x00\x01", [EventType.OK, EventType.ERROR])
        assert res.type != EventType.ERROR
    await a.mc.commands.send_msg(bytes.fromhex(b.app.public_key_hex), "2byte hash")
    got = await wait_for_contact_msg(b.mc)
    assert got is not None and got["text"] == "2byte hash"


async def test_unknown_companion_command_error(mesh):
    a, _, _ = mesh
    # 0xEE is not a known companion command: firmware replies ERR unsupported.
    res = await a.mc.commands.send(bytes([0xEE]), [EventType.OK, EventType.ERROR])
    assert res.type == EventType.ERROR


async def test_malformed_rf_contained(mesh):
    a, b, ether = mesh
    await learn_each_other(a, b, ether)
    # Blast garbage RF at both nodes; everything must keep working.
    for payload in (b"\x00", b"\xff" * 255, b"\x12\x34\x56", bytes(range(200))):
        await a.daemon.send_rx(payload)
        await b.daemon.send_rx(payload)
    await asyncio.sleep(0.3)
    await a.mc.commands.send_msg(bytes.fromhex(b.app.public_key_hex), "still alive")
    got = await wait_for_contact_msg(b.mc)
    assert got is not None and got["text"] == "still alive"
