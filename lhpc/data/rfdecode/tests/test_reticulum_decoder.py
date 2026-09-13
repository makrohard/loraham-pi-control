"""The reticulum decoder: IFAC unmasked with RNS's own code through the offline context; a
recorded MeshChat announce (public, signed) decodes to its display name; a known-plaintext single
packet to a generated LXMF identity opens — plain and through an LXMF-format ratchet file read
read-only; link traffic and foreign packets are `undecryptable`. Generated identities only."""

import os

import decode_reticulum as dec
import pytest
import RNS
from RNS.vendor import umsgpack

MESHCHAT_ANNOUNCE = ("7100985da93dee9a836d661940e784583fbe32ee17caaf2ba50381830d8217ba2b60003dc8e1d46f4a75663f"
                     "0678b6cf8da33969c28da86793ed1b64fcc9457765f97ce9540cac38ec575b60e92e424d22f8ada88bc72e1"
                     "3e985aded73e08f9a280aa56ec60bc318e2c0f0d908b9e2a75ea8006aa5943e492d187e8eb6a4d0e4dfcfea4"
                     "355715a1b2c3851e3e3ae0380dc8053ce513e05097db5d07cc6d32e1295ba63f206bed02cf62a2f240679e9c"
                     "53a42ddb7abd33756c0aa45a6d9f47cdff8cbda10a68b3a14bcec0ff0b161204e7208fb55f2700093c40e416e6f"
                     "6e796d6f75732050656572c09100")


def _line(hexdata: str, direction="RX") -> str:
    sig = "rssi=-90.00 snr=5.00" if direction == "RX" else "rssi=- snr=-"
    return f'2026-09-12T18:05:02.248Z {direction} {sig} len={len(hexdata) // 2} hex={hexdata} ascii="x"'


def _config(tmp_path, netname=None, passphrase=None, ifac_size_bits=None):
    d = tmp_path / "reticulum"
    d.mkdir(exist_ok=True)
    lines = ["[reticulum]", "  enable_transport = No", "[interfaces]", "  [[LoRa]]",
             "    type = LoRaSPIInterface", "    enabled = yes", "    frequency = 434500000"]
    if netname:
        lines.append(f"    networkname = {netname}")
    if passphrase:
        lines.append(f"    passphrase = {passphrase}")
    if ifac_size_bits:
        lines.append(f"    ifac_size = {ifac_size_bits}")
    (d / "config").write_text("\n".join(lines) + "\n")
    return str(d)


def _meshchat(tmp_path, identity=None, ratchet_prv=None):
    d = tmp_path / "meshchat"
    d.mkdir(exist_ok=True)
    if identity is None:
        return str(d)
    identity.to_file(str(d / "identity"))
    if ratchet_prv is not None:
        dest = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", identity)
        rdir = d / "identities" / "abc" / "lxmf_router" / "lxmf" / "ratchets"
        rdir.mkdir(parents=True)
        packed = umsgpack.packb([ratchet_prv])                 # RNS's own persisted layout
        (rdir / (RNS.hexrep(dest, delimit=False) + ".ratchets")).write_bytes(
            umsgpack.packb({"signature": identity.sign(packed), "ratchets": packed}))
    return str(d)


def _data_packet(dest_hash: bytes, data: bytes, packet_type=RNS.Packet.DATA, context=RNS.Packet.NONE) -> bytes:
    flags = (RNS.Packet.HEADER_1 << 6) | (RNS.Transport.BROADCAST << 4) | (RNS.Destination.SINGLE << 2) | packet_type
    return bytes([flags, 0]) + dest_hash + bytes([context]) + data


class _RnsIfac:
    """What RNS gives an IFAC'd interface, derived as `Reticulum.__apply_config` does at the
    pinned RNS: full_hash(netname) ‖ full_hash(netkey), hashed again, hkdf(64) under
    Reticulum.IFAC_SALT. Written out here rather than taken from the decoder, so a slip in its
    copy of that derivation cannot pass. A frame recorded on the box would pin it independently
    of any copy; none has been recorded yet."""

    def __init__(self, netname: str, netkey: str, size: int):
        origin = RNS.Identity.full_hash(netname.encode("utf-8")) + RNS.Identity.full_hash(netkey.encode("utf-8"))
        self.ifac_size = size
        self.ifac_key = RNS.Cryptography.hkdf(length=64, derive_from=RNS.Identity.full_hash(origin),
                                              salt=RNS.Reticulum.IFAC_SALT, context=None)
        self.ifac_identity = RNS.Identity.from_bytes(self.ifac_key)


def test_the_recorded_meshchat_announce_decodes_to_its_display_name(tmp_path):
    decode = dec.make_decoder(_config(tmp_path), _meshchat(tmp_path))
    r = decode("k", _line(MESHCHAT_ANNOUNCE, "TX"))
    assert r["status"] == "ok" and r["kind"] == "announce"
    assert "Anonymous Peer" in r["decoded"] and r["peer"] == "32ee17caaf2b"   # a transported announce


def test_a_single_packet_to_our_lxmf_destination_opens_plain_and_with_a_ratchet(tmp_path):
    me = RNS.Identity()
    dest = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", me)
    # RNS privates: the public path (Destination.enable_ratchets / rotate_ratchets) needs a
    # Transport-registered destination, i.e. a running RNS — there is no offline API.
    ratchet = RNS.Identity._generate_ratchet()
    ratchet_pub = RNS.Identity._ratchet_public_bytes(ratchet)
    decode = dec.make_decoder(_config(tmp_path), _meshchat(tmp_path, me, ratchet))
    plain = _data_packet(dest, me.encrypt(b"RF-PROOF single packet"))
    r = decode("k1", _line(plain.hex()))
    assert r["status"] == "ok" and r["kind"] == "data" and "RF-PROOF single packet" in r["decoded"]
    ratcheted = _data_packet(dest, me.encrypt(b"RF-PROOF ratcheted", ratchet=ratchet_pub))
    r = decode("k2", _line(ratcheted.hex()))
    assert r["status"] == "ok" and "RF-PROOF ratcheted" in r["decoded"]
    other = _data_packet(os.urandom(16), me.encrypt(b"not ours"))
    assert decode("k3", _line(other.hex()))["status"] == "undecryptable"
    link = _data_packet(dest, b"\x00" * 80, packet_type=RNS.Packet.LINKREQUEST)
    r = decode("k4", _line(link.hex()))
    assert r["status"] == "undecryptable" and "link" in r["decoded"]


def test_the_ratchet_file_is_only_read(tmp_path):
    me = RNS.Identity()
    ratchet = RNS.Identity._generate_ratchet()          # RNS private, see above
    mc = _meshchat(tmp_path, me, ratchet)
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "meshchat").rglob("*") if p.is_file()}
    dec.make_decoder(_config(tmp_path), mc)
    after = {p: p.stat().st_mtime_ns for p in (tmp_path / "meshchat").rglob("*") if p.is_file()}
    assert before == after


def test_an_ifac_frame_is_unmasked_with_rns_own_code(tmp_path):
    me = RNS.Identity()
    dest = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", me)
    raw = _data_packet(dest, me.encrypt(b"RF-PROOF behind IFAC"))
    ctx = _RnsIfac("labnet", "generated-passphrase-42", 8)
    masked = RNS.Transport.handle_outgoing_ifac(ctx, raw)        # what the radio actually carried
    assert masked[0] & 0x80
    decode = dec.make_decoder(_config(tmp_path, "labnet", "generated-passphrase-42"), _meshchat(tmp_path, me))
    r = decode("k", _line(masked.hex()))
    assert r["status"] == "ok" and "RF-PROOF behind IFAC" in r["decoded"]
    # Without the network key the frame is typed `no-key`, never a gap; the wrong key too.
    r = dec.make_decoder(_config(tmp_path), _meshchat(tmp_path, me))("k", _line(masked.hex()))
    assert r["status"] == "no-key" and "IFAC" in r["decoded"]
    r = dec.make_decoder(_config(tmp_path, "labnet", "wrong"), _meshchat(tmp_path, me))("k", _line(masked.hex()))
    assert r["status"] == "no-key"


def test_odd_input_is_malformed_and_a_bad_config_is_a_decoder_failure(tmp_path):
    decode = dec.make_decoder(_config(tmp_path), _meshchat(tmp_path))
    assert decode("k", "no hex here")["status"] == "malformed"
    assert decode("k", _line("00"))["status"] == "malformed"
    with pytest.raises(SystemExit) as e:                                   # absent is an error, not "no IFAC"
        dec.make_decoder(str(tmp_path / "nowhere"), _meshchat(tmp_path))
    assert e.value.code == 3
    (tmp_path / "reticulum" / "config").write_text("[interfaces]\n  [[LoRa]]\n    type = LoRaSPIInterface\n  [[Broken\n")   # unterminated section header
    with pytest.raises(SystemExit) as e:
        dec.make_decoder(str(tmp_path / "reticulum"), _meshchat(tmp_path))
    assert e.value.code == 3


def test_path_requests_and_plain_destination_packets_are_readable(tmp_path):
    """Unencrypted by RNS's design: a path request names the destination it looks for; a packet
    to a PLAIN destination is shown as it is."""
    decode = dec.make_decoder(_config(tmp_path), _meshchat(tmp_path))
    sought = bytes.fromhex("32ee17caaf2ba50381830d8217ba2b60")
    pr_dest = RNS.Destination.hash(None, "rnstransport", "path", "request")
    raw = bytes([0x08, 0x00]) + pr_dest + bytes([0x00]) + sought + os.urandom(10)   # HEADER_1, PLAIN, DATA
    r = decode("k", _line(raw.hex(), "TX"))
    assert r["status"] == "ok" and r["kind"] == "path-request" and r["decoded"] == "path request for 32ee17caaf2b"
    plain_dest = RNS.Destination.hash(None, "lhpc", "plain")
    raw = bytes([0x08, 0x00]) + plain_dest + bytes([0x00]) + b"hello in the clear"
    r = decode("k", _line(raw.hex()))
    assert r["status"] == "ok" and r["kind"] == "plain" and r["decoded"] == "hello in the clear"


def test_an_opened_non_lxmf_packet_is_shown_whole(tmp_path):
    me = RNS.Identity()
    dest = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", me)
    decode = dec.make_decoder(_config(tmp_path), _meshchat(tmp_path, me))
    r = decode("k", _line(_data_packet(dest, me.encrypt(b"\x00\x01\x02\xff")).hex()))
    assert r["status"] == "ok" and r["decoded"] == "4 B 000102ff"
