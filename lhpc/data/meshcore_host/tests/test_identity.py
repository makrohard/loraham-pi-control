"""Identity import — the migration's hard gate.

The existing LHPC MeshCore private key must map to the SAME public identity under
openHop, and nothing in the host may ever mint a replacement. Oracle for the seed →
public key relation: PyNaCl's standard Ed25519 (the same construction meshcore-pi's
ed25519_wrapper and LHPC's meshcore_identity module use).
"""

import os

import pytest
from nacl.signing import SigningKey

from meshcore_host.identity import IdentityError, load_identity


def write_key(tmp_path, text, mode=0o600):
    path = tmp_path / "meshcore_identity.key"
    path.write_text(text)
    os.chmod(path, mode)
    return path


def test_seed_key_maps_to_same_public_identity(tmp_path):
    seed = bytes(range(32))
    expected_pub = SigningKey(seed).verify_key.encode()
    path = write_key(tmp_path, seed.hex() + "\n")
    ident = load_identity(path)
    assert ident.get_public_key() == expected_pub


def test_key_is_stable_across_restarts(tmp_path):
    seed = os.urandom(32)
    path = write_key(tmp_path, seed.hex())
    first = load_identity(path).get_public_key()
    second = load_identity(path).get_public_key()
    assert first == second == SigningKey(seed).verify_key.encode()


def test_uppercase_and_whitespace_tolerated(tmp_path):
    seed = os.urandom(32)
    path = write_key(tmp_path, "  " + seed.hex().upper() + "  \n")
    assert load_identity(path).get_public_key() == SigningKey(seed).verify_key.encode()


def test_64_byte_firmware_key_imports(tmp_path):
    # MeshCore firmware expanded form [scalar||nonce]: public key must be the
    # unclamped scalar-mult base of the first 32 bytes (Identity.cpp readFrom(64)).
    from nacl.bindings import crypto_scalarmult_ed25519_base_noclamp
    scalar = SigningKey(os.urandom(32)).encode()  # arbitrary 32B usable as scalar
    # Build a scalar that nacl accepts for noclamp base mult (must be < L and != 0):
    scalar = (3).to_bytes(32, "little")
    fw_key = scalar + os.urandom(32)
    path = write_key(tmp_path, fw_key.hex())
    ident = load_identity(path)
    assert ident.get_public_key() == crypto_scalarmult_ed25519_base_noclamp(scalar)


def test_missing_key_fails_closed_and_mints_nothing(tmp_path):
    path = tmp_path / "meshcore_identity.key"
    with pytest.raises(IdentityError, match="missing"):
        load_identity(path)
    # FAIL CLOSED means fail closed: nothing may have been created.
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_malformed_hex_fails_closed(tmp_path):
    path = write_key(tmp_path, "zz" * 32)
    with pytest.raises(IdentityError, match="hex"):
        load_identity(path)


def test_wrong_length_fails_closed(tmp_path):
    path = write_key(tmp_path, "ab" * 31)
    with pytest.raises(IdentityError, match="length"):
        load_identity(path)
    path2 = write_key(tmp_path, "")
    with pytest.raises(IdentityError, match="length"):
        load_identity(path2)


def test_lax_permissions_fail_closed(tmp_path):
    seed = os.urandom(32)
    path = write_key(tmp_path, seed.hex(), mode=0o644)
    with pytest.raises(IdentityError, match="600"):
        load_identity(path)


def test_directory_instead_of_file_fails_closed(tmp_path):
    path = tmp_path / "meshcore_identity.key"
    path.mkdir(mode=0o700)
    with pytest.raises(IdentityError):
        load_identity(path)


def test_symlink_is_refused(tmp_path):
    real = write_key(tmp_path, os.urandom(32).hex())
    link = tmp_path / "link.key"
    link.symlink_to(real)
    with pytest.raises(IdentityError):
        load_identity(link)


def test_read_only_operation_never_writes(tmp_path):
    seed = os.urandom(32)
    path = write_key(tmp_path, seed.hex())
    before = path.read_bytes()
    load_identity(path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["meshcore_identity.key"]
