"""RF-log decoder: meshtastic. Runs under the managed Meshtastic CLI venv.

The trace line already carries `id`, `from`, `to`, `channel` (the 8-bit channel hash) and the
encrypted `bytes`. The channel PSKs are read from the node's own prefs (`channels.proto`), the
node number from `device.proto`; nothing is taken from argv but paths. AES-CTR with the matching
channel's key (nonce = packet id, sender), then the `Data` protobuf — meshtastic's own layout,
pinned by the bench-recorded frames in the tests. Public-key direct messages to and from this node are opened with its own key (see _pkc_open).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _proto

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Hash import SHA256
    from Cryptodome.Protocol.DH import key_agreement
    from Cryptodome.PublicKey import ECC
    from meshtastic.protobuf import (
        channel_pb2,
        deviceonly_pb2,
        localonly_pb2,
        mesh_pb2,
        portnums_pb2,
    )
except ImportError as exc:  # pragma: no cover - the interpreter is the stack's venv
    _proto.fail(f"meshtastic decoder needs the meshtastic venv: {exc}")

# Meshtastic's default PSK ("AQ==" = key index 1); index n uses last byte + (n - 1).
_DEFAULT_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
_NAMES = {portnums_pb2.TEXT_MESSAGE_APP: "text", portnums_pb2.POSITION_APP: "position",
          portnums_pb2.NODEINFO_APP: "nodeinfo", portnums_pb2.ROUTING_APP: "routing",
          portnums_pb2.TELEMETRY_APP: "telemetry"}


def _expand_psk(psk: bytes) -> bytes | None:
    if not psk or psk == b"\x00":
        return b""                              # no encryption on this channel
    if len(psk) == 1:
        return _DEFAULT_PSK[:-1] + bytes([(_DEFAULT_PSK[-1] + psk[0] - 1) & 0xFF])
    if len(psk) in (16, 32):
        return psk
    return None


def _channel_hash(name: str, key: bytes) -> int:
    h = 0
    for b in name.encode():
        h ^= b
    for b in key:
        h ^= b
    return h & 0xFF


def _load_channels(prefs: str) -> list:
    path = os.path.join(prefs, "channels.proto")
    try:
        with open(path, "rb") as f:
            cf = deviceonly_pb2.ChannelFile()
            cf.ParseFromString(f.read())
    except (OSError, ValueError) as exc:
        _proto.fail(f"meshtastic channel store unreadable: {path}: {exc}")
    out = []
    for ch in cf.channels:
        if ch.role == channel_pb2.Channel.Role.DISABLED:
            continue
        key = _expand_psk(bytes(ch.settings.psk))
        if key is None:
            continue
        # The firmware hashes the channel NAME; an unnamed primary channel hashes as the
        # modem preset's name (LongFast on the shipped default), which the bench frame pins.
        name = ch.settings.name or ("LongFast" if ch.role == channel_pb2.Channel.Role.PRIMARY else "")
        out.append((name or f"ch{ch.index}", key, _channel_hash(name, key)))
    return out


def _own_key(prefs: str):
    """The node's own X25519 private key (prefs/config.proto, security.private_key) — read in
    place, never copied; None when the node has no key yet."""
    try:
        with open(os.path.join(prefs, "config.proto"), "rb") as f:
            cfg = localonly_pb2.LocalConfig()
            cfg.ParseFromString(f.read())
    except (OSError, ValueError):
        return None
    priv = bytes(cfg.security.private_key)
    if len(priv) != 32:
        return None
    try:
        return ECC.construct(curve="Curve25519", seed=priv)
    except (ValueError, TypeError):
        return None


def _node_db(prefs: str) -> tuple:
    """(public keys, short names) by node number from prefs/nodes.proto."""
    keys, names = {}, {}
    try:
        with open(os.path.join(prefs, "nodes.proto"), "rb") as f:
            db = deviceonly_pb2.NodeDatabase()
            db.ParseFromString(f.read())
    except (OSError, ValueError):
        return keys, names
    for n in db.nodes:
        if len(n.user.public_key) == 32:
            keys[int(n.num)] = bytes(n.user.public_key)
        if n.user.short_name:
            names[int(n.num)] = str(n.user.short_name)
    return keys, names


def _pkc_open(own_key, peer_pub: bytes, packet_id: int, sender: int, ct: bytes):
    """A public-key direct message as the firmware builds it (CryptoEngine::encryptCurve25519):
    ciphertext || CCM tag (8) || extra nonce (4); key = SHA-256(X25519(our private, their
    public)); nonce = packet id (4 LE) || extra nonce (4 LE) || sender (4 LE) || 0 — 13 bytes
    (initNonce writes the extra nonce at offset 4). Verified on the bench against the G2."""
    if len(ct) < 12:
        return None
    body, tag, extra = ct[:-12], ct[-12:-4], ct[-4:]
    try:
        pub = ECC.construct(curve="Curve25519", point_x=int.from_bytes(peer_pub, "little"))
        key = SHA256.new(key_agreement(static_priv=own_key, static_pub=pub, kdf=lambda x: x)).digest()
        nonce = packet_id.to_bytes(4, "little") + extra + sender.to_bytes(4, "little") + b"\x00"
        return AES.new(key, AES.MODE_CCM, nonce=nonce, mac_len=8).decrypt_and_verify(body, tag)
    except (ValueError, OverflowError):
        return None


def _own_node(prefs: str) -> int:
    try:
        with open(os.path.join(prefs, "device.proto"), "rb") as f:
            ds = deviceonly_pb2.DeviceState()
            ds.ParseFromString(f.read())
        return int(ds.my_node.my_node_num)
    except (OSError, ValueError):
        return 0


def _nonce(packet_id: int, sender: int) -> bytes:
    return packet_id.to_bytes(8, "little") + sender.to_bytes(4, "little") + b"\x00" * 4


def _lossless(data: bytes) -> str:
    """An opened payload this decoder has no summary for: shown whole — as text when it is
    text, else as hex — bounded by _proto.clean, never reduced to a byte count."""
    try:
        text = bytes(data).decode("utf-8")
        if text.replace("\n", "").replace("\t", "").isprintable():
            return _proto.clean(text)
    except UnicodeDecodeError:
        pass
    return _proto.clean(f"{len(data)} B {bytes(data).hex()}")


def _describe(data: "mesh_pb2.Data") -> tuple:
    port = data.portnum
    kind = _NAMES.get(port, portnums_pb2.PortNum.Name(port).lower() if port in portnums_pb2.PortNum.values() else f"port{port}")
    payload = bytes(data.payload)
    if port == portnums_pb2.TEXT_MESSAGE_APP:
        return kind, _proto.clean(payload)
    if port == portnums_pb2.POSITION_APP:
        pos = mesh_pb2.Position()
        pos.ParseFromString(payload)
        return kind, f"{pos.latitude_i / 1e7:.5f}, {pos.longitude_i / 1e7:.5f}, alt {pos.altitude} m"
    if port == portnums_pb2.NODEINFO_APP:
        u = mesh_pb2.User()
        u.ParseFromString(payload)
        return kind, _proto.clean(f"{u.long_name} ({u.short_name}) {u.id}")
    if port == portnums_pb2.ROUTING_APP:
        r = mesh_pb2.Routing()
        r.ParseFromString(payload)
        return kind, "ack" if r.error_reason == 0 else f"error {mesh_pb2.Routing.Error.Name(r.error_reason)}"
    return kind, _lossless(payload)


_SHAPES = (("text", "text"), ("longname", "nodeinfo"), ("latitude_i", "position"), ("battery_level", "telemetry"),
           ("air_util_tx", "telemetry"), ("voltage", "telemetry"), ("temperature", "telemetry"),
           ("relative_humidity", "telemetry"), ("barometric_pressure", "telemetry"), ("request_id", "routing"))


def _payload_kind(payload) -> str:
    if not isinstance(payload, dict):
        return "meta"
    for field, kind in _SHAPES:
        if field in payload:
            return kind
    return "data"


def _payload_text(obj: dict) -> str:
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return f"packet id {obj.get('id')} hops {obj.get('hops_away', '?')}"
    if "text" in payload:
        return _proto.clean(str(payload["text"]))
    if "latitude_i" in payload:
        alt = payload.get("altitude")
        return f"{int(payload['latitude_i']) / 1e7:.5f}, {int(payload.get('longitude_i') or 0) / 1e7:.5f}" + \
               (f", alt {alt} m" if alt is not None else "")
    if "longname" in payload:
        return _proto.clean(f"{payload.get('longname', '')} ({payload.get('shortname', '')}) {payload.get('id', '')}")
    parts = [f"{k}={v}" for k, v in payload.items() if not isinstance(v, (dict, list))]
    return _proto.clean(" ".join(parts))


def make_decoder(prefs: str):
    channels = _load_channels(prefs)
    own = _own_node(prefs)
    own_key = _own_key(prefs)
    pubkeys, names = _node_db(prefs)

    def node(v: int) -> str:
        if v == 0xFFFFFFFF:
            return "^all"
        if v == 0:
            return "local"
        return f"!{v:08x}" + (f" ({names[v]})" if v in names else "")

    def decode_one(key: str, raw: str) -> dict:
        try:
            obj = json.loads(raw)
        except ValueError:
            return _proto.result(key, "malformed", decoded="not a trace record")
        if not isinstance(obj, dict) or "from" not in obj:
            return _proto.result(key, "malformed", decoded="not a trace record")
        frm, to = int(obj.get("from") or 0), int(obj.get("to") or 0)
        peer = f"{node(frm or own)} → {node(to)}"
        if "bytes" not in obj:
            # The node's own decoded record (its `channel` is an INDEX here, not the hash): the
            # payload is plaintext already — named by its shape and shown as it is.
            return _proto.result(key, "ok", _payload_kind(obj.get("payload")), peer, _payload_text(obj))
        ct = bytes.fromhex(str(obj["bytes"]))
        want = int(obj.get("channel") or 0)
        sender = frm or own
        pid = int(obj.get("id") or 0)
        for name, psk, h in channels:
            if h != want:
                continue
            if psk == b"":
                pt = ct
            else:
                pt = AES.new(psk, AES.MODE_CTR, initial_value=_nonce(pid, sender), nonce=b"").decrypt(ct)
            try:
                d = mesh_pb2.Data()
                d.ParseFromString(pt)
            except Exception:
                continue                        # a hash collision with another channel
            if d.portnum == 0 and not d.payload:
                continue
            kind, text = _describe(d)
            return _proto.result(key, "ok", kind, peer, _proto.clean(f"[{name}] {text}"))
        if want == 0 and to not in (0, 0xFFFFFFFF):
            # Channel hash 0 on a packet to one node: public-key encrypted with the pair's keys.
            # Openable when this node is one end: our private key + the other end's public key
            # from the node database. Between two other nodes: never.
            if sender == own and to == own:
                # The node addressing itself: a local API packet the trace records (a few bytes,
                # no radio payload) — not a direct message and not decryptable with any pair.
                return _proto.result(key, "ok", "local", peer, f"local packet, {len(ct)} B (the node to itself)")
            other = to if sender == own else (sender if to == own else None)
            if other is None:
                return _proto.result(key, "undecryptable", "direct", peer, "direct message between other nodes")
            if own_key is None:
                return _proto.result(key, "no-key", "direct", peer, "this node has no private key yet")
            if other not in pubkeys:
                return _proto.result(key, "no-key", "direct", peer, f"no public key for {node(other)} in the node database")
            pt = _pkc_open(own_key, pubkeys[other], pid, sender, ct)
            if pt is None:
                return _proto.result(key, "no-key", "direct", peer, "the pair's keys did not open it")
            try:
                d = mesh_pb2.Data()
                d.ParseFromString(pt)
            except Exception:
                return _proto.result(key, "malformed", "direct", peer, "decrypted, not a Data message")
            kind, text = _describe(d)
            return _proto.result(key, "ok", kind, peer, _proto.clean(f"[direct] {text}"))
        return _proto.result(key, "no-key", "", peer, f"no key for channel hash 0x{want:02x}")
    return decode_one


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--prefs", required=True)
    a = ap.parse_args()
    _proto.run(make_decoder(a.prefs))


if __name__ == "__main__":
    main()
