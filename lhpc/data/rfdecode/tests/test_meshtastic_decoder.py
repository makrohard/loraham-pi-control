"""The meshtastic decoder against the node's own prefs layout: the bench-recorded G2 frame on
LongFast (public key) pins the nonce and channel-hash layout; a generated channel key proves the
general path; an unknown hash is `no-key`; a header-only trace line is `meta`."""

import json

import decode_meshtastic as dec
import pytest
from Cryptodome.Cipher import AES
from meshtastic.protobuf import (
    channel_pb2,
    deviceonly_pb2,
    mesh_pb2,
    portnums_pb2,
)

G2_ROUTING_ACK = ('{"bytes":"73C61C738F34F16C3FE0DB4FFC","channel":8,"from":2733223640,"id":3795803303,'
                  '"rssi":-91,"size":13,"snr":12,"time_ms":257382,"timestamp":1789238625,"to":2665732816,'
                  '"want_ack":false}')
OWN = 2665732816
TEST_KEY = bytes(range(16))
LONGFAST_KEY = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")   # the firmware's default PSK: index 1, "AQ=="

# The wire layout below is written out from the firmware, not taken from the decoder, so a slip
# mirrored in decode_meshtastic cannot pass; the bench-recorded G2 frame pins both.


def _nonce(packet_id: int, sender: int) -> bytes:
    """CryptoEngine::initNonce: the packet id as 64-bit LE, the sender node number LE, then the
    4-byte CTR block counter at zero."""
    return packet_id.to_bytes(8, "little") + sender.to_bytes(4, "little") + bytes(4)


def _channel_hash(name: str, key: bytes) -> int:
    """Channels::generateHash: xorHash(name) ^ xorHash(key), one byte."""
    h = 0
    for b in name.encode() + key:
        h ^= b
    return h


def _prefs(tmp_path, channels):
    d = tmp_path / "prefs"
    d.mkdir()
    cf = deviceonly_pb2.ChannelFile()
    for idx, (name, psk, role) in enumerate(channels):
        ch = cf.channels.add()
        ch.index = idx
        ch.role = role
        ch.settings.name = name
        ch.settings.psk = psk
    (d / "channels.proto").write_bytes(cf.SerializeToString())
    ds = deviceonly_pb2.DeviceState()
    ds.my_node.my_node_num = OWN
    (d / "device.proto").write_bytes(ds.SerializeToString())
    return str(d)


def _encrypt(key, pid, sender, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_CTR, initial_value=_nonce(pid, sender), nonce=b"").encrypt(data)


def _line(**obj) -> str:
    return json.dumps(obj)


def test_longfast_default_key_hashes_to_eight_and_opens_the_recorded_frame(tmp_path):
    assert _channel_hash("LongFast", LONGFAST_KEY) == 8          # the recorded frame's "channel":8
    decode = dec.make_decoder(_prefs(tmp_path, [("", b"\x01", channel_pb2.Channel.Role.PRIMARY)]))
    r = decode("k", G2_ROUTING_ACK)
    assert r["status"] == "ok" and r["kind"] == "routing" and "ack" in r["decoded"]
    assert r["peer"] == "!a2e9aed8 → !9ee3dad0" and r["decoded"].startswith("[LongFast]")


def test_a_generated_channel_key_decodes_text_position_and_nodeinfo(tmp_path):
    prefs = _prefs(tmp_path, [("", b"\x01", channel_pb2.Channel.Role.PRIMARY),
                              ("Lab", TEST_KEY, channel_pb2.Channel.Role.SECONDARY)])
    decode = dec.make_decoder(prefs)
    h = _channel_hash("Lab", TEST_KEY)
    d = mesh_pb2.Data(portnum=portnums_pb2.TEXT_MESSAGE_APP, payload="RF-LOG test ä".encode())
    ct = _encrypt(TEST_KEY, 42, 0x11223344, d.SerializeToString())
    r = decode("k1", _line(bytes=ct.hex().upper(), channel=h, **{"from": 0x11223344}, id=42, rssi=-80,
                           size=len(ct), snr=5, timestamp=1789238625, to=0xFFFFFFFF))
    assert r == {"key": "k1", "status": "ok", "kind": "text", "peer": "!11223344 → ^all",
                 "decoded": "[Lab] RF-LOG test ä"}
    pos = mesh_pb2.Position(latitude_i=484180000, longitude_i=116654000, altitude=484)
    d = mesh_pb2.Data(portnum=portnums_pb2.POSITION_APP, payload=pos.SerializeToString())
    ct = _encrypt(TEST_KEY, 43, 0x11223344, d.SerializeToString())
    r = decode("k2", _line(bytes=ct.hex(), channel=h, **{"from": 0x11223344}, id=43, rssi=-80, size=len(ct), snr=5, to=OWN))
    assert r["kind"] == "position" and r["decoded"] == "[Lab] 48.41800, 11.66540, alt 484 m"
    user = mesh_pb2.User(id="!11223344", long_name="Bench Node", short_name="BNCH")
    d = mesh_pb2.Data(portnum=portnums_pb2.NODEINFO_APP, payload=user.SerializeToString())
    ct = _encrypt(TEST_KEY, 44, 0x11223344, d.SerializeToString())
    r = decode("k3", _line(bytes=ct.hex(), channel=h, **{"from": 0x11223344}, id=44, rssi=-80, size=len(ct), snr=5, to=OWN))
    assert r["kind"] == "nodeinfo" and r["decoded"] == "[Lab] Bench Node (BNCH) !11223344"


def test_our_own_transmission_uses_the_node_number_as_sender(tmp_path):
    decode = dec.make_decoder(_prefs(tmp_path, [("Lab", TEST_KEY, channel_pb2.Channel.Role.PRIMARY)]))
    d = mesh_pb2.Data(portnum=portnums_pb2.TEXT_MESSAGE_APP, payload=b"from the box")
    ct = _encrypt(TEST_KEY, 7, OWN, d.SerializeToString())       # the node encrypts with its own num
    r = decode("k", _line(bytes=ct.hex(), channel=_channel_hash("Lab", TEST_KEY), **{"from": 0}, id=7,
                          size=len(ct), timestamp=1, to=0xFFFFFFFF))
    assert r["status"] == "ok" and r["decoded"] == "[Lab] from the box" and r["peer"] == "!9ee3dad0 → ^all"


def test_unknown_channel_hash_is_no_key_and_odd_lines_are_typed(tmp_path):
    decode = dec.make_decoder(_prefs(tmp_path, [("", b"\x01", channel_pb2.Channel.Role.PRIMARY)]))
    r = decode("k", _line(bytes="00ff", channel=0x8f, **{"from": 1}, id=1, rssi=-9, size=2, snr=1, to=2))
    assert r["status"] == "no-key" and "0x8f" in r["decoded"]
    r = decode("k", _line(channel=0, **{"from": 2733223640}, hop_start=0, hops_away=0, id=1, rssi=-91, sender="!9ee3dad0", snr=12, timestamp=1, to=2665732816, type=""))
    assert r["status"] == "ok" and r["kind"] == "meta"
    assert decode("k", "not json")["status"] == "malformed"
    assert decode("k", '{"pad": 1}')["status"] == "malformed"


def test_an_unreadable_channel_store_is_a_decoder_level_failure(tmp_path):
    with pytest.raises(SystemExit) as e:
        dec.make_decoder(str(tmp_path / "missing"))
    assert e.value.code == 3


def test_the_protocol_answers_every_line_with_its_key(tmp_path, capsys, monkeypatch):
    import io
    import sys
    decode = dec.make_decoder(_prefs(tmp_path, [("", b"\x01", channel_pb2.Channel.Role.PRIMARY)]))
    import _proto
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"key": "a", "raw": G2_ROUTING_ACK}) + "\n"
                                                  + json.dumps({"key": "b", "raw": "garbage"}) + "\n"))
    _proto.run(decode)
    out = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert [o["key"] for o in out] == ["a", "b"]
    assert out[0]["status"] == "ok" and out[1]["status"] == "malformed"


def test_the_nodes_own_decoded_records_are_named_by_shape_and_shown_as_they_are(tmp_path):
    """meshtasticd's trace also writes the packets it decoded itself (`payload` object, `channel`
    = the channel INDEX). Plaintext already: named by shape, never decrypted."""
    decode = dec.make_decoder(_prefs(tmp_path, [("", b"\x01", channel_pb2.Channel.Role.PRIMARY)]))
    node = '{"channel":0,"from":2733223640,"hop_start":3,"hops_away":0,"id":3090449671,"payload":{"hardware":31,"id":"!a2e9aed8","longname":"CHE Station","role":0,"shortname":"cheS"},"rssi":-97,"sender":"!9ee3dad0","snr":9,"timestamp":1789251720,"to":4294967295}'
    r = decode("k", node)
    assert r["status"] == "ok" and r["kind"] == "nodeinfo" and r["decoded"] == "CHE Station (cheS) !a2e9aed8"
    text = '{"channel":0,"from":2733223640,"id":1,"payload":{"text":"hi <b>there</b>\\u0007"},"rssi":-90,"snr":5,"timestamp":1789251720,"to":4294967295}'
    r = decode("k", text)
    assert r["kind"] == "text" and r["decoded"] == "hi <b>there</b>\x07"
    pos = '{"channel":0,"from":1,"id":2,"payload":{"latitude_i":484179968,"longitude_i":116654100,"altitude":505},"timestamp":1,"to":4294967295}'
    assert decode("k", pos)["decoded"] == "48.41800, 11.66541, alt 505 m"
    tele = '{"channel":0,"from":1,"id":3,"payload":{"battery_level":93,"voltage":4.1},"timestamp":1,"to":4294967295}'
    r = decode("k", tele)
    assert r["kind"] == "telemetry" and r["decoded"] == "battery_level=93 voltage=4.1"
    assert decode("k", '{"from":1,"id":4,"hops_away":0,"timestamp":1,"to":2}')["kind"] == "meta"


def _pkc_prefs(tmp_path, own_priv, peers):
    """prefs with our X25519 key in config.proto and peers' public keys + names in nodes.proto."""
    import pathlib

    from meshtastic.protobuf import localonly_pb2
    d = pathlib.Path(_prefs(tmp_path, [("", b"\x01", channel_pb2.Channel.Role.PRIMARY)]))
    cfg = localonly_pb2.LocalConfig()
    cfg.security.private_key = own_priv
    (d / "config.proto").write_bytes(cfg.SerializeToString())
    db = deviceonly_pb2.NodeDatabase()
    for num, pub, short in peers:
        n = db.nodes.add()
        n.num = num
        n.user.public_key = pub
        n.user.short_name = short
    (d / "nodes.proto").write_bytes(db.SerializeToString())
    return d


def _pkc_seal(sender_priv_key, receiver_pub: bytes, packet_id: int, sender: int, plain: bytes, extra=b"\x11\x22\x33\x44") -> bytes:
    """The firmware's encryptCurve25519: ciphertext || CCM tag(8) || extra nonce(4)."""
    from Cryptodome.Hash import SHA256
    from Cryptodome.Protocol.DH import key_agreement
    from Cryptodome.PublicKey import ECC
    pub = ECC.construct(curve="Curve25519", point_x=int.from_bytes(receiver_pub, "little"))
    key = SHA256.new(key_agreement(static_priv=sender_priv_key, static_pub=pub, kdf=lambda x: x)).digest()
    nonce = packet_id.to_bytes(4, "little") + extra + sender.to_bytes(4, "little") + b"\x00"
    ct, tag = AES.new(key, AES.MODE_CCM, nonce=nonce, mac_len=8).encrypt_and_digest(plain)
    return ct + tag + extra


def test_public_key_direct_messages_open_both_ways_and_name_the_nodes(tmp_path):
    """The layout was pinned on the bench against the G2 (nonce = id, extra nonce, sender);
    here with generated X25519 keys: to us, from us, between others, and a sender we have no
    key for. Node names from the node database ride along with the ids."""
    from Cryptodome.PublicKey import ECC
    me, g2, third = ECC.generate(curve="Curve25519"), ECC.generate(curve="Curve25519"), ECC.generate(curve="Curve25519")
    raw = lambda k: k.public_key().export_key(format="raw")  # noqa: E731
    G2 = 0xA2E9AED8
    decode = dec.make_decoder(_pkc_prefs(tmp_path, me.seed, [(G2, raw(g2), "cheS"), (0x11111111, raw(third), "3rd")]))
    d = mesh_pb2.Data(portnum=portnums_pb2.TEXT_MESSAGE_APP, payload=b"RF-DECRYPT g2e dm 222614")
    to_us = _pkc_seal(g2, raw(me), 352277087, G2, d.SerializeToString())
    line = ('{"bytes":"%s","channel":0,"from":%d,"hop_start":3,"hops_away":0,"id":352277087,"rssi":-100,"size":%d,'
            '"snr":9.5,"timestamp":1789251998,"to":%d,"want_ack":true}')
    r = decode("k", line % (to_us.hex().upper(), G2, len(to_us), OWN))
    assert r["status"] == "ok" and r["kind"] == "text" and r["decoded"] == "[direct] RF-DECRYPT g2e dm 222614"
    assert r["peer"] == f"!{G2:08x} (cheS) → !{OWN:08x}"
    from_us = _pkc_seal(me, raw(g2), 352277087, OWN, d.SerializeToString())
    r = decode("k", line % (from_us.hex(), OWN, len(from_us), G2))
    assert r["status"] == "ok" and r["decoded"].endswith("g2e dm 222614")
    others = _pkc_seal(third, raw(g2), 352277087, 0x11111111, d.SerializeToString())
    r = decode("k", line % (others.hex(), 0x11111111, len(others), G2))
    assert r["status"] == "undecryptable" and "between other nodes" in r["decoded"]
    unknown = _pkc_seal(third, raw(me), 352277087, 0x22222222, d.SerializeToString())
    r = decode("k", line % (unknown.hex(), 0x22222222, len(unknown), OWN))
    assert r["status"] == "no-key" and "no public key for !22222222" in r["decoded"]
    r = decode("k", line % ("0000020018", OWN, 5, OWN))                # the recorded self-addressed packet
    assert r["status"] == "ok" and r["kind"] == "local" and "5 B" in r["decoded"]
    tampered = to_us[:-13] + bytes([to_us[-13] ^ 1]) + to_us[-12:]
    r = decode("k", line % (tampered.hex(), G2, len(tampered), OWN))
    assert r["status"] == "no-key" and "did not open" in r["decoded"]


def test_an_opened_payload_without_a_summary_is_shown_whole(tmp_path):
    """An application port this decoder has no summary for: the decrypted bytes are shown as
    text or hex, bounded, never reduced to a byte count."""
    decode = dec.make_decoder(_prefs(tmp_path, [("", b"\x01", channel_pb2.Channel.Role.PRIMARY)]))
    for payload, shown in ((b"hello private app", "hello private app"), (b"\x01\x02\xff", "3 B 0102ff")):
        d = mesh_pb2.Data(portnum=portnums_pb2.PRIVATE_APP, payload=payload)
        ct = _encrypt(LONGFAST_KEY, 77, 2733223640, d.SerializeToString())
        line = ('{"bytes":"' + ct.hex() + '","channel":8,"from":2733223640,"id":77,"rssi":-90,"size":'
                + str(len(ct)) + ',"snr":5,"timestamp":1,"to":4294967295}')
        r = decode("k", line)
        assert r["status"] == "ok" and r["kind"] == "private_app" and r["decoded"] == f"[LongFast] {shown}"
