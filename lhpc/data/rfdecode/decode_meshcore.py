"""RF-log decoder: meshcore. Runs under the MeshCore node venv (openHop core + the host).

Frames are parsed with openHop's own `Packet`; adverts with `parse_advert_payload` /
`decode_appdata`; direct messages with the file-backed identity path (`LocalIdentity(seed)`,
`Identity.calc_shared_secret()`, `CryptoUtils.mac_then_decrypt()`); group messages with
`normalize_channel_secret()` + `derive_channel_hash()` and the same MAC-then-decrypt. Which
identity and which store apply depends on the LHPC mode: chat and chat+repeater use the
Companion identity (`meshcore_identity.key`) — the Companion store is LHPC's `companion.db` in
chat, the repeater's database under `state/openhop/` when hosted; pure repeater has no Companion
and only the repeater identity. Everything is read-only; nothing is written.
"""

import argparse
import glob
import json
import os
import sqlite3
import struct
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _proto

try:
    from openhop_core.companion.constants import DEFAULT_PUBLIC_CHANNEL_SECRET
    from openhop_core.node.handlers.crypto_helpers import iter_decrypt_by_src_hash
    from openhop_core.protocol import constants as C
    from openhop_core.protocol.crypto import CryptoUtils
    from openhop_core.protocol.identity import Identity, LocalIdentity
    from openhop_core.protocol.packet import Packet
    from openhop_core.protocol.utils import (
        decode_appdata,
        derive_channel_hash,
        normalize_channel_secret,
        parse_advert_payload,
    )
except ImportError as exc:  # pragma: no cover - the interpreter is the stack's venv
    _proto.fail(f"meshcore decoder needs the openHop venv: {exc}")


class Contact(NamedTuple):
    public_key: bytes
    name: str


def _read_identity(secrets: str, filename: str) -> "LocalIdentity":
    path = os.path.join(secrets, filename)
    try:
        with open(path, encoding="utf-8") as f:
            seed = bytes.fromhex(f.read().strip())
    except (OSError, ValueError) as exc:
        _proto.fail(f"meshcore identity unreadable: {path}: {exc}")
    return LocalIdentity(seed)


def _companion_store(path: str) -> tuple:
    """LHPC's Companion store (chat mode): contacts.public_key (hex text), channels.secret (blob)."""
    if not os.path.isfile(path):
        _proto.fail(f"meshcore companion store missing: {path}")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            contacts = [Contact(bytes.fromhex(pk), _name_from(data))
                        for pk, data in con.execute("SELECT public_key, data FROM contacts")]
            channels = [(name, bytes(secret)) for name, secret in con.execute("SELECT name, secret FROM channels")]
        finally:
            con.close()
    except (sqlite3.Error, ValueError) as exc:
        _proto.fail(f"meshcore companion store unreadable: {path}: {exc}")
    return contacts, channels


def _repeater_store(dirpath: str) -> tuple:
    """The repeater's database (hosted Companion in chat+repeater): companion_contacts.pubkey,
    companion_channels.secret. A pure repeater may carry none — that is an empty store, not a
    failure."""
    contacts, channels = [], []
    if not os.path.isdir(dirpath):
        _proto.fail(f"meshcore repeater store missing: {dirpath}")
    for path in sorted(glob.glob(os.path.join(dirpath, "*.db")) + glob.glob(os.path.join(dirpath, "*.sqlite*"))):
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "companion_contacts" in tables:
                    contacts += [Contact(bytes(pk), str(name)) for pk, name in con.execute("SELECT pubkey, name FROM companion_contacts")]
                if "companion_channels" in tables:
                    channels += [(str(name), bytes(secret)) for name, secret in con.execute("SELECT name, secret FROM companion_channels")]
            finally:
                con.close()
        except sqlite3.Error as exc:
            _proto.fail(f"meshcore repeater store unreadable: {path}: {exc}")
    return contacts, channels


def _name_from(data) -> str:
    try:
        return str(json.loads(data).get("name", ""))
    except (ValueError, AttributeError, TypeError):
        return ""


def _printable(data: bytes) -> str:
    """Bytes as text when they are text, else hex — never lost. The cipher pads to 16-byte
    blocks with zeros; the padding is stripped (the firmware's readers do the same by length)."""
    data = bytes(data).rstrip(b"\x00")
    if not data:
        return ""
    try:
        text = bytes(data).decode("utf-8")
        if text.replace("\n", "").replace("\t", "").isprintable():
            return _proto.clean(text)
    except UnicodeDecodeError:
        pass
    return _proto.clean(f"{len(data)} B {bytes(data).hex()}")


def _request(plain: bytes) -> str:
    """timestamp(4) req_type(1) data — the protocol request a repeater answers."""
    if len(plain) < 5:
        return _printable(plain)
    ts, rtype = struct.unpack("<I", bytes(plain[:4]))[0], plain[4]
    name = _REQ_NAMES.get(rtype, f"type 0x{rtype:02x}")
    extra = _printable(plain[5:])
    return _proto.clean(f"request {name} ts {ts}" + (f" {extra}" if extra else ""))


def _response(plain: bytes) -> str:
    """tag(4) data — the reply to a request (status, telemetry, owner info…)."""
    if len(plain) < 4:
        return _printable(plain)
    tag = struct.unpack("<I", bytes(plain[:4]))[0]
    return _proto.clean(f"response tag {tag:08x} {_printable(plain[4:])}".rstrip())


def _path_return(plain: bytes) -> str:
    """path_len(1) path… extra_type(1) extra… — the return path a node sends back."""
    if not plain:
        return "empty path"
    n = plain[0]
    hops = " ".join(f"{b:02x}" for b in plain[1:1 + n])
    rest = plain[1 + n:]
    extra = f" extra type 0x{rest[0]:02x} {_printable(rest[1:])}".rstrip() if rest else ""
    return _proto.clean(f"path {n} hop(s) {hops or '-'}{extra}")


def _anon_request(plain: bytes) -> str:
    """timestamp(4) then the request body. A repeater login is one of these (timestamp +
    sync_since + password) and the layouts cannot be told apart without the handler's context,
    so the body is never shown — it may be a password."""
    if len(plain) < 4:
        return "short anonymous request"
    ts = struct.unpack("<I", bytes(plain[:4]))[0]
    body = bytes(plain[4:]).rstrip(b"\x00")
    return f"anonymous request ts {ts}, {len(body)} B (body withheld: may carry a login password)"


def _text_payload(plain: bytes) -> str:
    """timestamp(4) flags(1) text — the layout both direct and group texts share."""
    if len(plain) < 5:
        return ""
    body = plain[5:]
    nul = body.find(b"\x00")
    return _proto.clean(body[:nul] if nul >= 0 else body)


def _control(payload: bytes) -> str:
    """Plaintext discovery control frames (openHop's ControlHandler): 0x80 request = flags(1)
    filter(1) tag(4) [since(4)]; 0x90 response = type|node_type(1) snr(1) tag(4) pubkey."""
    if not payload:
        return "empty control frame"
    ctype = payload[0] & 0xF0
    if ctype == 0x80 and len(payload) >= 6:
        tag = struct.unpack("<I", payload[2:6])[0]
        since = struct.unpack("<I", payload[6:10])[0] if len(payload) >= 10 else 0
        return (f"discovery request tag {tag:08x} filter 0x{payload[1]:02x}"
                + (" prefix-only" if payload[0] & 0x01 else "") + (f" since {since}" if since else ""))
    if ctype == 0x90 and len(payload) >= 6:
        tag = struct.unpack("<I", payload[2:6])[0]
        return (f"discovery response tag {tag:08x} node-type {payload[0] & 0x0F} snr-byte 0x{payload[1]:02x}"
                f" key {payload[6:].hex()[:16]}…" if len(payload) > 6 else
                f"discovery response tag {tag:08x} node-type {payload[0] & 0x0F} snr-byte 0x{payload[1]:02x}")
    return _proto.clean(f"control type 0x{ctype:02x} {len(payload)} B {payload.hex()}")


def _multipart(payload: bytes) -> str:
    """The one-byte wrapper (inner type in the low nibble) + the inner frame; an embedded ACK
    carries its 4-byte CRC (openHop's MultipartHandler)."""
    if not payload:
        return "empty multipart frame"
    inner = payload[0] & 0x0F
    if inner == C.PAYLOAD_TYPE_ACK and len(payload) >= 5:
        return f"multi-ack crc {int.from_bytes(payload[1:5], 'little'):08x}"
    return _proto.clean(f"multipart inner type {inner} {len(payload) - 1} B {payload[1:].hex()}")


_REQ_NAMES = {0x01: "get-status", 0x02: "keep-alive", 0x03: "get-telemetry", 0x05: "get-access-list",
              0x06: "get-neighbours", 0x07: "get-owner-info"}
_PAIRWISE = {C.PAYLOAD_TYPE_TXT_MSG: ("direct", _text_payload),
             C.PAYLOAD_TYPE_REQ: ("request", _request),
             C.PAYLOAD_TYPE_RESPONSE: ("response", _response),
             C.PAYLOAD_TYPE_PATH: ("path", _path_return)}


def make_decoder(mode: str, secrets: str, store: str, repeater_store: str):
    mode = mode if mode in ("chat", "chat+repeater", "repeater") else "chat"
    if mode == "repeater":
        me = _read_identity(secrets, "openhop_repeater_identity.key")
        contacts, channels = _repeater_store(repeater_store)
    else:
        me = _read_identity(secrets, "meshcore_identity.key")
        contacts, channels = _companion_store(store) if mode == "chat" else _repeater_store(repeater_store)
    my_pub = me.get_public_key()
    my_hash = my_pub[0]
    by_name = {c.public_key: c.name for c in contacts}
    chans = []
    # MeshCore's Public channel has a well-known key every node carries (openHop's own constant);
    # the stores hold only channels the operator added, so it is built in, in every mode.
    for name, secret in [*channels, ("Public", DEFAULT_PUBLIC_CHANNEL_SECRET)]:
        try:
            norm = normalize_channel_secret(secret)
            padded = (norm + b"\x00" * 32)[:32]
            chans.append((name, derive_channel_hash(norm), padded))
        except Exception:
            continue

    def who(pub: bytes) -> str:
        return by_name.get(pub) or pub[:4].hex()

    def decode_one(key: str, raw: str) -> dict:
        try:
            hexpart = raw.split(" hex=", 1)[1].split(" ", 1)[0]
            frame = bytes.fromhex(hexpart)
        except (IndexError, ValueError):
            return _proto.result(key, "malformed", decoded="no hex payload")
        p = Packet()
        try:
            if not p.read_from(frame):
                return _proto.result(key, "malformed", decoded="not a MeshCore frame")
        except Exception:
            return _proto.result(key, "malformed", decoded="not a MeshCore frame")
        ptype = p.get_payload_type()
        payload = p.get_payload()
        if ptype == C.PAYLOAD_TYPE_ADVERT:
            try:
                adv = parse_advert_payload(payload)
                pub = bytes.fromhex(adv["pubkey"])
                appdata = bytes(adv["appdata"])
                # The signed region is pubkey + timestamp + appdata (upstream advert handler).
                signed = pub + adv["timestamp"].to_bytes(4, "little") + appdata
                ok = Identity(pub).verify(signed, bytes.fromhex(adv["signature"]))
                app = decode_appdata(appdata) if appdata else {}
            except Exception:
                return _proto.result(key, "malformed", "advert", "", "unparsable advert")
            name = _proto.clean(app.get("node_name", "") if isinstance(app, dict) else "")
            pos = ""
            if isinstance(app, dict) and app.get("latitude") is not None:
                pos = f" @ {app.get('latitude')}, {app.get('longitude')}"
            return _proto.result(key, "ok" if ok else "malformed", "advert", pub[:4].hex(),
                                 f"{name or '(unnamed)'} key {pub.hex()[:16]}…{pos}" + ("" if ok else " (bad signature)"))
        if ptype == C.PAYLOAD_TYPE_GRP_TXT:
            if len(payload) < 4:
                return _proto.result(key, "malformed", "channel", "", "short group frame")
            want, data = payload[0], payload[1:]
            for name, h, secret in chans:                 # stored channels first, then Public
                if h != want:
                    continue
                try:
                    plain = CryptoUtils.mac_then_decrypt(secret[:16], secret, data)
                except Exception:
                    continue                    # MAC mismatch: another channel with this hash
                return _proto.result(key, "ok", "channel", _proto.clean(name), _text_payload(plain))
            return _proto.result(key, "no-key", "channel", "", f"no key for channel hash 0x{want:02x}")
        if ptype in _PAIRWISE:
            # dest_hash(1) src_hash(1) MAC+cipher with the pair's shared secret — one layout for
            # text, requests, responses and path returns (openHop's handlers read them alike).
            kind, render = _PAIRWISE[ptype]
            if len(payload) < 4:
                return _proto.result(key, "malformed", kind, "", f"short {kind} frame")
            dest_hash, src_hash, data = payload[0], payload[1], payload[2:]
            if dest_hash == my_hash:
                for _contact, pub, _ss, plain in iter_decrypt_by_src_hash(contacts, src_hash, me, data):
                    return _proto.result(key, "ok", kind, f"{who(pub)} → me", render(plain))
            elif src_hash == my_hash:
                for _contact, pub, _ss, plain in iter_decrypt_by_src_hash(contacts, dest_hash, me, data):
                    return _proto.result(key, "ok", kind, f"me → {who(pub)}", render(plain))
            else:
                return _proto.result(key, "undecryptable", kind, f"{src_hash:02x} → {dest_hash:02x}",
                                     "between other nodes")
            return _proto.result(key, "no-key", kind, f"{src_hash:02x} → {dest_hash:02x}", "no matching contact")
        if ptype == C.PAYLOAD_TYPE_ANON_REQ:
            # dest_hash(1) + the sender's public key (32) + MAC+cipher with secret(our key, theirs):
            # openable when addressed to us — the sender's key travels in the frame.
            if len(payload) < 1 + 32 + 4:
                return _proto.result(key, "malformed", "anon-request", "", "short anonymous request")
            dest_hash, client_pub, data = payload[0], bytes(payload[1:33]), payload[33:]
            if dest_hash != my_hash:
                return _proto.result(key, "undecryptable", "anon-request", f"{client_pub[:4].hex()} → {dest_hash:02x}",
                                     "addressed to another node")
            try:
                secret = Identity(client_pub).calc_shared_secret(me.get_private_key())
                plain = CryptoUtils.mac_then_decrypt(secret[:16], secret, data)
            except Exception:
                return _proto.result(key, "no-key", "anon-request", f"{who(client_pub)} → me", "MAC did not verify")
            return _proto.result(key, "ok", "anon-request", f"{who(client_pub)} → me", _anon_request(plain))
        if ptype == C.PAYLOAD_TYPE_GRP_DATA:
            if len(payload) < 4:
                return _proto.result(key, "malformed", "channel-data", "", "short group data frame")
            want, data = payload[0], payload[1:]
            for name, h, secret in chans:
                if h != want:
                    continue
                try:
                    plain = CryptoUtils.mac_then_decrypt(secret[:16], secret, data)
                except Exception:
                    continue
                return _proto.result(key, "ok", "channel-data", _proto.clean(name), _printable(plain))
            return _proto.result(key, "no-key", "channel-data", "", f"no key for channel hash 0x{want:02x}")
        if ptype == C.PAYLOAD_TYPE_TRACE:
            if len(payload) < 9:
                return _proto.result(key, "malformed", "trace", "", "short trace frame")
            tag, auth, flags = struct.unpack("<IIB", bytes(payload[:9]))
            hops = " ".join(f"{b:02x}" for b in payload[9:])
            return _proto.result(key, "ok", "trace", "", f"trace tag {tag:08x} auth {auth:08x} flags {flags:02x} path {hops or '-'}")
        if ptype == C.PAYLOAD_TYPE_ACK:
            return _proto.result(key, "ok", "ack", "", f"ack {payload[:4].hex()}")
        if ptype == C.PAYLOAD_TYPE_CONTROL:
            return _proto.result(key, "ok", "control", "", _control(bytes(payload)))
        if ptype == C.PAYLOAD_TYPE_MULTIPART:
            return _proto.result(key, "ok", "multipart", "", _multipart(bytes(payload)))
        if ptype == C.PAYLOAD_TYPE_RAW_CUSTOM:          # created without encryption, by definition
            return _proto.result(key, "ok", "raw-custom", "", _printable(payload) or "empty")
        return _proto.result(key, "undecryptable", f"type{ptype}", "", f"unknown payload type {ptype} ({len(payload)} B)")
    return decode_one


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--mode", default="chat")
    ap.add_argument("--secrets", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--repeater-store", required=True)
    a = ap.parse_args()
    _proto.run(make_decoder(a.mode, a.secrets, a.store, a.repeater_store))


if __name__ == "__main__":
    main()
