"""The meshcore decoder against openHop's own frame builders: generated identities and channel
keys only; the bench-recorded CHEMobile advert (public, signed) pins the advert path; every LHPC
mode (chat, chat+repeater, repeater) resolves its own identity and store."""

import json
import sqlite3

import decode_meshcore as dec
import pytest
from openhop_core.protocol import constants as C
from openhop_core.protocol.crypto import CryptoUtils
from openhop_core.protocol.identity import LocalIdentity
from openhop_core.protocol.packet import Packet
from openhop_core.protocol.packet_utils import PacketHeaderUtils
from openhop_core.protocol.utils import (
    derive_channel_hash,
    normalize_channel_secret,
)

CHEMOBILE_ADVERT = ("110037bd5d25f79fffe0646c339963cad2da611c6894226a6bfe3207db2f6c52f7c1c09ba56a2a1bc31fb1bd3ee157"
                    "d70c698dee5d637b16c3f61e6ccb273d3180ff3151a0f48e32d2f1dce3d987df065c33fc5c49aa6e18409aad68955bf"
                    "23d0f6381f0e90d814348454d6f62696c65")
SEED_ME = bytes(range(32))
SEED_PEER = bytes(range(32, 64))
CHANNEL_SECRET = bytes(range(100, 116))          # a generated 16-byte channel key


def _line(frame: bytes, direction="RX") -> str:
    sig = "rssi=-67.00 snr=11.25" if direction == "RX" else "rssi=- snr=-"
    return f'2026-09-12T18:36:49.968Z {direction} {sig} len={len(frame)} hex={frame.hex()} ascii="x"'


def _frame(payload_type: int, payload: bytes) -> bytes:
    p = Packet()
    p.header = PacketHeaderUtils.create_header(route_type=C.ROUTE_TYPE_FLOOD,
                                               payload_type=payload_type, version=0)
    p.path = bytearray()
    p.path_len = 0
    p.payload = bytearray(payload)
    p.payload_len = len(payload)
    return p.write_to()


def _text(ts: int, text: str) -> bytes:
    return ts.to_bytes(4, "little") + b"\x00" + text.encode()


def _secrets(tmp_path, chat=SEED_ME, repeater=None):
    d = tmp_path / "secrets"
    d.mkdir(exist_ok=True)
    (d / "meshcore_identity.key").write_text(chat.hex() + "\n")
    if repeater is not None:
        (d / "openhop_repeater_identity.key").write_text(repeater.hex() + "\n")
    return str(d)


def _companion_db(tmp_path, contacts, channels):
    p = tmp_path / "companion.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE contacts (public_key TEXT PRIMARY KEY, data TEXT NOT NULL)")
    con.execute("CREATE TABLE channels (idx INTEGER PRIMARY KEY, name TEXT NOT NULL, secret BLOB NOT NULL)")
    for pub, name in contacts:
        con.execute("INSERT INTO contacts VALUES (?, ?)", (pub.hex(), json.dumps({"name": name})))
    for i, (name, secret) in enumerate(channels):
        con.execute("INSERT INTO channels VALUES (?, ?, ?)", (i, name, secret))
    con.commit()
    con.close()
    return str(p)


def _repeater_store(tmp_path, contacts, channels):
    d = tmp_path / "openhop"
    d.mkdir(exist_ok=True)
    con = sqlite3.connect(d / "repeater.db")
    con.execute("CREATE TABLE companion_contacts (companion_hash TEXT, pubkey BLOB NOT NULL, name TEXT NOT NULL)")
    con.execute("CREATE TABLE companion_channels (companion_hash TEXT, name TEXT NOT NULL, secret BLOB NOT NULL)")
    for pub, name in contacts:
        con.execute("INSERT INTO companion_contacts VALUES ('ab', ?, ?)", (pub, name))
    for name, secret in channels:
        con.execute("INSERT INTO companion_channels VALUES ('ab', ?, ?)", (name, secret))
    con.commit()
    con.close()
    return str(d)


def _direct(sender: LocalIdentity, receiver_pub: bytes, text: str) -> bytes:
    from openhop_core.protocol.identity import Identity
    ss = Identity(receiver_pub).calc_shared_secret(sender.get_private_key())
    enc = CryptoUtils.encrypt_then_mac(ss[:16], ss, _text(1789238625, text))
    return _frame(C.PAYLOAD_TYPE_TXT_MSG, bytes([receiver_pub[0], sender.get_public_key()[0]]) + enc)


def _group(secret: bytes, text: str) -> bytes:
    norm = normalize_channel_secret(secret)
    padded = (norm + b"\x00" * 32)[:32]
    enc = CryptoUtils.encrypt_then_mac(padded[:16], padded, _text(1789238625, text))
    return _frame(C.PAYLOAD_TYPE_GRP_TXT, bytes([derive_channel_hash(norm)]) + enc)


def test_the_recorded_chemobile_advert_verifies_and_names_the_node(tmp_path):
    decode = dec.make_decoder("chat", _secrets(tmp_path), _companion_db(tmp_path, [], []), str(tmp_path))
    r = decode("k", _line(bytes.fromhex(CHEMOBILE_ADVERT)))
    assert r["status"] == "ok" and r["kind"] == "advert"
    assert r["decoded"].startswith("CHEMobile key 37bd5d25f79fffe0") and r["peer"] == "37bd5d25"


@pytest.mark.parametrize("mode", ["chat", "chat+repeater"])
def test_direct_and_channel_messages_open_with_the_companion_identity(tmp_path, mode):
    me, peer = LocalIdentity(SEED_ME), LocalIdentity(SEED_PEER)
    contacts = [(peer.get_public_key(), "Bench Peer")]
    channels = [("#lab", CHANNEL_SECRET)]
    store = _companion_db(tmp_path, contacts, channels)
    rstore = _repeater_store(tmp_path, contacts, channels)
    decode = dec.make_decoder(mode, _secrets(tmp_path), store, rstore)
    r = decode("k1", _line(_direct(peer, me.get_public_key(), "hello box")))
    assert r == {"key": "k1", "status": "ok", "kind": "direct", "peer": "Bench Peer → me", "decoded": "hello box"}
    r = decode("k2", _line(_direct(me, peer.get_public_key(), "hello peer"), "TX"))
    assert r["status"] == "ok" and r["peer"] == "me → Bench Peer" and r["decoded"] == "hello peer"
    r = decode("k3", _line(_group(CHANNEL_SECRET, "group hello")))
    assert r == {"key": "k3", "status": "ok", "kind": "channel", "peer": "#lab", "decoded": "group hello"}
    # A pair we are not part of, and a channel we do not hold.
    third = LocalIdentity(bytes(range(64, 96)))
    r = decode("k4", _line(_direct(third, peer.get_public_key(), "not for us")))
    assert r["status"] == "undecryptable" and r["kind"] == "direct"
    r = decode("k5", _line(_group(bytes(range(116, 132)), "other channel")))
    assert r["status"] == "no-key" and "0x" in r["decoded"]
    # An unknown sender to us: the store lacks the contact.
    r = decode("k6", _line(_direct(third, me.get_public_key(), "stranger")))
    assert r["status"] == "no-key"


def test_repeater_mode_uses_the_repeater_identity_and_no_companion_store(tmp_path):
    rep, peer = LocalIdentity(bytes(range(200, 232))), LocalIdentity(SEED_PEER)
    secrets = _secrets(tmp_path, repeater=bytes(range(200, 232)))
    rstore = _repeater_store(tmp_path, [(peer.get_public_key(), "Bench Peer")], [])
    decode = dec.make_decoder("repeater", secrets, str(tmp_path / "absent.db"), rstore)
    r = decode("k1", _line(_direct(peer, rep.get_public_key(), "to the repeater")))
    assert r["status"] == "ok" and r["decoded"] == "to the repeater"
    r = decode("k2", _line(bytes.fromhex(CHEMOBILE_ADVERT)))
    assert r["status"] == "ok" and r["kind"] == "advert"
    r = decode("k3", _line(_group(CHANNEL_SECRET, "no channels here")))
    assert r["status"] == "no-key"


def test_the_public_channel_opens_in_every_mode_without_a_store_entry(tmp_path):
    from openhop_core.companion.constants import DEFAULT_PUBLIC_CHANNEL_SECRET
    frame = _group(DEFAULT_PUBLIC_CHANNEL_SECRET, "hello public")
    chat = dec.make_decoder("chat", _secrets(tmp_path), _companion_db(tmp_path, [], []), str(tmp_path))
    assert chat("k", _line(frame)) == {"key": "k", "status": "ok", "kind": "channel", "peer": "Public", "decoded": "hello public"}
    rep = dec.make_decoder("repeater", _secrets(tmp_path, repeater=bytes(range(200, 232))), str(tmp_path / "absent.db"),
                           _repeater_store(tmp_path, [], []))
    assert rep("k", _line(frame))["decoded"] == "hello public"


def test_other_frames_are_typed_and_stores_gate_startup(tmp_path):
    decode = dec.make_decoder("chat", _secrets(tmp_path), _companion_db(tmp_path, [], []), str(tmp_path))
    r = decode("k", _line(_frame(C.PAYLOAD_TYPE_ACK, b"\x01\x02\x03\x04")))
    assert r["status"] == "ok" and r["kind"] == "ack"
    r = decode("k", _line(_frame(C.PAYLOAD_TYPE_PATH, b"\x00" * 6)))
    assert r["status"] == "undecryptable" and r["kind"] == "path"
    assert decode("k", "nothing here")["status"] == "malformed"
    with pytest.raises(SystemExit) as e:
        dec.make_decoder("chat", _secrets(tmp_path), str(tmp_path / "missing.db"), str(tmp_path))
    assert e.value.code == 3
    with pytest.raises(SystemExit) as e:
        dec.make_decoder("chat", str(tmp_path / "nosecrets"), str(tmp_path / "companion.db"), str(tmp_path))
    assert e.value.code == 3


def _pair(sender: LocalIdentity, receiver_pub: bytes, ptype: int, plain: bytes) -> bytes:
    from openhop_core.protocol.identity import Identity
    ss = Identity(receiver_pub).calc_shared_secret(sender.get_private_key())
    return _frame(ptype, bytes([receiver_pub[0], sender.get_public_key()[0]]) + CryptoUtils.encrypt_then_mac(ss[:16], ss, plain))


def test_every_pairwise_frame_this_node_is_part_of_opens(tmp_path):
    """Requests, responses and path returns share the direct-message layout (openHop's handlers
    read them alike): dest hash, src hash, MAC + cipher under the pair's shared secret."""
    import struct
    me, peer = LocalIdentity(SEED_ME), LocalIdentity(SEED_PEER)
    store = _companion_db(tmp_path, [(peer.get_public_key(), "Bench Peer")], [])
    decode = dec.make_decoder("chat", _secrets(tmp_path), store, str(tmp_path))
    req = _pair(peer, me.get_public_key(), C.PAYLOAD_TYPE_REQ, struct.pack("<I", 1789238625) + b"\x01")
    r = decode("k", _line(req))
    assert r["status"] == "ok" and r["kind"] == "request" and r["decoded"] == "request get-status ts 1789238625"
    resp = _pair(me, peer.get_public_key(), C.PAYLOAD_TYPE_RESPONSE, struct.pack("<I", 0xCAFEBABE) + b"version\n1.2\n")
    r = decode("k", _line(resp, "TX"))
    assert r["kind"] == "response" and r["peer"] == "me → Bench Peer" and r["decoded"] == "response tag cafebabe version\n1.2"
    path = _pair(peer, me.get_public_key(), C.PAYLOAD_TYPE_PATH, bytes([2, 0x11, 0x22]) + b"\x00" + b"ack")
    r = decode("k", _line(path))
    assert r["kind"] == "path" and r["decoded"] == "path 2 hop(s) 11 22 extra type 0x00 ack"
    other = LocalIdentity(bytes(range(64, 96)))
    r = decode("k", _line(_pair(other, peer.get_public_key(), C.PAYLOAD_TYPE_REQ, b"\x00" * 5)))
    assert r["status"] == "undecryptable" and r["kind"] == "request"


def test_an_anonymous_request_to_us_opens_and_a_login_never_shows_its_password(tmp_path):
    from openhop_core.protocol.identity import Identity
    me, client = LocalIdentity(SEED_ME), LocalIdentity(SEED_PEER)
    decode = dec.make_decoder("repeater", _secrets(tmp_path, repeater=SEED_ME), str(tmp_path / "absent.db"),
                              _repeater_store(tmp_path, [], []))
    ss = Identity(me.get_public_key()).calc_shared_secret(client.get_private_key())
    login = (1789238625).to_bytes(4, "little") + b"hunter2secret"
    frame = _frame(C.PAYLOAD_TYPE_ANON_REQ, bytes([me.get_public_key()[0]]) + client.get_public_key()
                   + CryptoUtils.encrypt_then_mac(ss[:16], ss, login))
    r = decode("k", _line(frame))
    assert r["status"] == "ok" and r["kind"] == "anon-request"
    assert r["decoded"] == "anonymous request ts 1789238625, 13 B (body withheld: may carry a login password)"
    assert "hunter2" not in json.dumps(r)
    elsewhere = _frame(C.PAYLOAD_TYPE_ANON_REQ, bytes([0x99]) + client.get_public_key() + b"\x00" * 8)
    assert decode("k", _line(elsewhere))["status"] == "undecryptable"


def test_channel_data_and_trace_frames_are_readable(tmp_path):
    decode = dec.make_decoder("chat", _secrets(tmp_path), _companion_db(tmp_path, [], [("#lab", CHANNEL_SECRET)]), str(tmp_path))
    norm = normalize_channel_secret(CHANNEL_SECRET)
    padded = (norm + b"\x00" * 32)[:32]
    data = _frame(C.PAYLOAD_TYPE_GRP_DATA, bytes([derive_channel_hash(norm)]) + CryptoUtils.encrypt_then_mac(padded[:16], padded, b"\x01\x02\xff"))
    r = decode("k", _line(data))
    assert r == {"key": "k", "status": "ok", "kind": "channel-data", "peer": "#lab", "decoded": "3 B 0102ff"}
    import struct
    trace = _frame(C.PAYLOAD_TYPE_TRACE, struct.pack("<IIB", 0x11223344, 0xAABBCCDD, 0x01) + bytes([0x37, 0x87]))
    r = decode("k", _line(trace))
    assert r["status"] == "ok" and r["decoded"] == "trace tag 11223344 auth aabbccdd flags 01 path 37 87"


def test_control_multipart_and_raw_custom_frames_are_readable(tmp_path):
    """Plaintext by protocol (openHop's ControlHandler / MultipartHandler; RAW_CUSTOM is created
    without encryption): shown for what they are, never 'undecryptable'."""
    import struct
    decode = dec.make_decoder("chat", _secrets(tmp_path), _companion_db(tmp_path, [], []), str(tmp_path))
    req = _frame(C.PAYLOAD_TYPE_CONTROL, bytes([0x81, 0x03]) + struct.pack("<I", 0xDEADBEEF) + struct.pack("<I", 1789238625))
    r = decode("k", _line(req))
    assert r["status"] == "ok" and r["kind"] == "control"
    assert r["decoded"] == "discovery request tag deadbeef filter 0x03 prefix-only since 1789238625"
    resp = _frame(C.PAYLOAD_TYPE_CONTROL, bytes([0x92, 0x14]) + struct.pack("<I", 0xDEADBEEF) + bytes(range(32)))
    r = decode("k", _line(resp))
    assert r["decoded"] == "discovery response tag deadbeef node-type 2 snr-byte 0x14 key 0001020304050607…"
    other = _frame(C.PAYLOAD_TYPE_CONTROL, bytes([0xA0, 0x01, 0x02]))
    assert decode("k", _line(other))["decoded"] == "control type 0xa0 3 B a00102"
    mack = _frame(C.PAYLOAD_TYPE_MULTIPART, bytes([0x03]) + (0xCAFEF00D).to_bytes(4, "little"))
    r = decode("k", _line(mack))
    assert r["status"] == "ok" and r["kind"] == "multipart" and r["decoded"] == "multi-ack crc cafef00d"
    inner = _frame(C.PAYLOAD_TYPE_MULTIPART, bytes([0x02, 0xaa, 0xbb]))
    assert decode("k", _line(inner))["decoded"] == "multipart inner type 2 2 B aabb"
    raw = _frame(C.PAYLOAD_TYPE_RAW_CUSTOM, b"custom payload")
    r = decode("k", _line(raw))
    assert r["status"] == "ok" and r["kind"] == "raw-custom" and r["decoded"] == "custom payload"
    assert decode("k", _line(_frame(C.PAYLOAD_TYPE_RAW_CUSTOM, b"\x00\x01\x02")))["decoded"] == "3 B 000102"
