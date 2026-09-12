"""RF-log decoder: reticulum. Runs under the Reticulum stack venv (RNS + LXMF).

The RF log holds the physical frame. On an IFAC-enabled interface the frame is unmasked with
RNS's own `Transport.handle_ifac()` through a tiny offline context (the four attributes it reads,
keyed exactly as `Reticulum.py` derives them) — `RNS.Reticulum` itself is never instantiated
here: its constructor configures hardware and creates storage. Then `RNS.Packet` unpacks the
frame; announces are validated with `Identity.validate_announce`; single packets to MeshChat's
LXMF delivery destination are opened with `Identity.decrypt` and the destination's private
ratchets, read from LXMF's own ratchet file (read-only: never `enable_ratchets()`, which would
create one). Link traffic and relayed packets are undecryptable by design and say so.
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _proto

try:
    import RNS
    from RNS.vendor import umsgpack
    from RNS.vendor.configobj import ConfigObj
except ImportError as exc:  # pragma: no cover - the interpreter is the stack's venv
    _proto.fail(f"reticulum decoder needs the RNS venv: {exc}")
try:
    import LXMF
except ImportError:          # LXMF is optional: without it a message shows as opaque bytes
    LXMF = None

IFAC_FLAG = 0x80
DEFAULT_IFAC_SIZE = 8          # LoRaSPIInterface.DEFAULT_IFAC_SIZE (loraham-rns-interface)


class _IfacContext:
    """What `Transport.handle_ifac` reads from an interface, and nothing else."""

    def __init__(self, netname, netkey, size):
        self.ifac_size = size
        origin = b""
        if netname:
            origin += RNS.Identity.full_hash(netname.encode("utf-8"))
        if netkey:
            origin += RNS.Identity.full_hash(netkey.encode("utf-8"))
        self.ifac_key = RNS.Cryptography.hkdf(length=64, derive_from=RNS.Identity.full_hash(origin),
                                              salt=RNS.Reticulum.IFAC_SALT, context=None)
        self.ifac_identity = RNS.Identity.from_bytes(self.ifac_key)
        self.violations = 0

    def ifac_violation(self, reason):
        self.violations += 1


def _lora_ifac(configdir: str):
    """The [[LoRa]] interface's IFAC from the generated RNS config (0400, ours to read)."""
    path = os.path.join(configdir, "config")
    if not os.path.isfile(path):
        # ConfigObj answers an absent file with an EMPTY config — which read as "no IFAC" on a
        # box whose interface had one (the configdir was passed wrong). Absent is an error.
        _proto.fail(f"reticulum config missing: {path}")
    try:
        cfg = ConfigObj(path)
    except Exception as exc:
        _proto.fail(f"reticulum config unreadable: {path}: {exc}")
    ifaces = cfg.get("interfaces", {}) or {}
    lora = None
    for sec in ifaces.values():
        if isinstance(sec, dict) and str(sec.get("type", "")).endswith("LoRaSPIInterface"):
            lora = sec
            break
    if lora is None:
        return None
    netname = str(lora.get("networkname", lora.get("network_name", "")) or "").strip() or None
    netkey = str(lora.get("passphrase", lora.get("pass_phrase", "")) or "").strip() or None
    if not netname and not netkey:
        return None
    size = DEFAULT_IFAC_SIZE
    try:
        if lora.get("ifac_size"):
            size = max(int(lora["ifac_size"]) // 8, 1)
    except ValueError:
        pass
    return _IfacContext(netname, netkey, size)


def _meshchat_identity(meshchat: str):
    path = os.path.join(meshchat, "identity")
    if not os.path.isfile(path):
        return None, []
    try:
        ident = RNS.Identity.from_file(path)
    except Exception as exc:
        _proto.fail(f"meshchat identity unreadable: {path}: {exc}")
    if ident is None:
        _proto.fail(f"meshchat identity unreadable: {path}")
    dest_hash = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", ident)
    ratchets = []
    for rpath in glob.glob(os.path.join(meshchat, "identities", "*", "lxmf_router", "lxmf", "ratchets",
                                        RNS.hexrep(dest_hash, delimit=False) + ".ratchets")):
        try:
            with open(rpath, "rb") as f:
                persisted = umsgpack.unpackb(f.read())
            if ident.validate(persisted["signature"], persisted["ratchets"]):
                ratchets = list(umsgpack.unpackb(persisted["ratchets"]))
        except Exception as exc:
            _proto.fail(f"meshchat ratchet file unreadable: {rpath}: {exc}")
    return (ident, dest_hash), ratchets


def _announce_app_data(p) -> str:
    """The announce's app data, as RNS lays it out: public key (64), name hash (10), random
    hash (10), the ratchet (32) when the context flag is set, signature (64), then app data.
    LXMF app data is a msgpack list whose first element is the display name."""
    keysize = RNS.Identity.KEYSIZE // 8
    off = keysize + RNS.Identity.NAME_HASH_LENGTH // 8 + 10  # random hash: 10 bytes, no constant in RNS
    if getattr(p, "context_flag", 0):
        off += RNS.Identity.RATCHETSIZE // 8
    off += RNS.Identity.SIGLENGTH // 8
    app = bytes(p.data[off:]) if len(p.data) > off else b""
    if not app:
        return ""
    try:
        obj = umsgpack.unpackb(app)
        if isinstance(obj, (list, tuple)) and obj and isinstance(obj[0], (bytes, bytearray)):
            return _proto.clean(bytes(obj[0]))
    except Exception:
        pass
    return _proto.clean("".join(chr(b) if 0x20 <= b < 0x7f else "." for b in app[:80]))


def _printable(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
        if text.replace("\n", "").replace("\t", "").isprintable():
            return _proto.clean(text)
    except UnicodeDecodeError:
        pass
    return _proto.clean(f"{len(data)} B {data.hex()}")


_PATH_REQUEST_DEST = RNS.Destination.hash(None, "rnstransport", "path", "request") if hasattr(RNS.Destination, "hash") else b""


def _lxmf_summary(dest_hash: bytes, plain: bytes) -> str:
    if LXMF is not None:
        try:
            msg = LXMF.LXMessage.unpack_from_bytes(dest_hash + plain)
            title = msg.title_as_string() if hasattr(msg, "title_as_string") else ""
            content = msg.content_as_string() if hasattr(msg, "content_as_string") else ""
            src = RNS.hexrep(msg.source_hash, delimit=False)[:12] if getattr(msg, "source_hash", None) else "?"
            return _proto.clean(f"LXMF from {src}: {title + ' ' if title else ''}{content}")
        except Exception:
            pass
    return _printable(plain)                  # opened, not LXMF: shown whole (text or hex)


def make_decoder(configdir: str, meshchat: str):
    ifac = _lora_ifac(configdir)
    me, ratchets = _meshchat_identity(meshchat)

    def decode_one(key: str, raw: str) -> dict:
        try:
            frame = bytes.fromhex(raw.split(" hex=", 1)[1].split(" ", 1)[0])
        except (IndexError, ValueError):
            return _proto.result(key, "malformed", decoded="no hex payload")
        if not frame:
            return _proto.result(key, "malformed", decoded="empty frame")
        if frame[0] & IFAC_FLAG:
            if ifac is None:
                return _proto.result(key, "no-key", "ifac", "", "IFAC frame, no network name/passphrase configured")
            matched, unmasked = RNS.Transport.handle_ifac(frame, ifac)
            if not matched:
                return _proto.result(key, "no-key", "ifac", "", "IFAC does not match this network")
            frame = unmasked
        try:
            p = RNS.Packet(None, frame)
            unpacked = p.unpack()          # RNS logs and returns False on a malformed frame
        except Exception:
            unpacked = False
        if not unpacked:
            return _proto.result(key, "malformed", decoded="not a Reticulum packet")
        dest = RNS.hexrep(p.destination_hash, delimit=False)[:12]
        if p.packet_type == RNS.Packet.ANNOUNCE:
            try:
                valid = RNS.Identity.validate_announce(p, only_validate_signature=True)
            except Exception:
                valid = False
            return _proto.result(key, "ok" if valid else "malformed", "announce", dest,
                                 f"announce {dest} {_announce_app_data(p)}".strip()
                                 + ("" if valid else " (bad signature)"))
        if p.packet_type == RNS.Packet.LINKREQUEST:
            return _proto.result(key, "undecryptable", "link", dest, "link request (link traffic is never decryptable)")
        if p.packet_type == RNS.Packet.PROOF:
            return _proto.result(key, "undecryptable", "proof", dest, "proof")
        if me is not None and p.destination_hash == me[1] and p.packet_type == RNS.Packet.DATA:
            try:
                plain = me[0].decrypt(p.data, ratchets=ratchets or None)
            except Exception:
                plain = None
            if plain is None:
                return _proto.result(key, "no-key", "data", dest, "to this node, but no ratchet/key opened it")
            return _proto.result(key, "ok", "data", dest, _lxmf_summary(p.destination_hash, plain))
        if p.packet_type == RNS.Packet.DATA and p.context in (RNS.Packet.LRRTT, RNS.Packet.LINKCLOSE,
                                                              RNS.Packet.LINKIDENTIFY, RNS.Packet.CHANNEL,
                                                              RNS.Packet.RESOURCE, RNS.Packet.RESOURCE_ADV,
                                                              RNS.Packet.RESOURCE_REQ, RNS.Packet.RESOURCE_HMU,
                                                              RNS.Packet.RESOURCE_PRF, RNS.Packet.RESOURCE_ICL,
                                                              RNS.Packet.RESOURCE_RCL, RNS.Packet.KEEPALIVE):
            return _proto.result(key, "undecryptable", "link", dest, "link traffic (never decryptable)")
        if p.destination_type == RNS.Destination.PLAIN:
            # Unencrypted by definition: RNS's own path requests (the sought destination hash in
            # the payload) and any plain-destination packet.
            if p.destination_hash == _PATH_REQUEST_DEST and len(p.data) >= 16:
                sought = RNS.hexrep(p.data[:16], delimit=False)[:12]
                return _proto.result(key, "ok", "path-request", dest, f"path request for {sought}")
            return _proto.result(key, "ok", "plain", dest, _printable(bytes(p.data)))
        return _proto.result(key, "undecryptable", "data", dest, "not addressed to this node")
    return decode_one


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--meshchat", required=True)
    a = ap.parse_args()
    _proto.run(make_decoder(a.config, a.meshchat))


if __name__ == "__main__":
    main()
