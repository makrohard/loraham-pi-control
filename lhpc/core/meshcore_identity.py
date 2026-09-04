"""The persistent MeshCore node identity.

MeshCore's private key IS the node's on-air identity: every advert is signed with it and
contacts recognise the node by the matching public key. The pinned `meshcore-pi` mints a
fresh random key whenever its config carries no `privatekey`, so before this module the
identity rotated on every config regeneration — live-found on a box that minted three
different keys in one afternoon — and could only be pinned by hand-editing the upstream
template, which any source refresh then discarded.

LHPC therefore owns the key: it lives at `<runtime>/config/secrets/meshcore_identity.key`
(0600, outside the managed source tree) and is injected into the generated TOML as a
`secret_file` FileParam, which forces that file to 0600 too.

Two entry points, deliberately split:
  * `adopt_identity` — rescue an identity that exists only in a file we are about to
    replace. NEVER mints: updating or uninstalling an unused stack must not create a key.
  * `ensure_identity` — adopt, else mint. Called ONLY from config generation, the one
    place that genuinely needs a key to exist.

A candidate holding an INVALID key is never treated as absent: it blocks, so a typo or a
truncated file can never be "fixed" by silently rotating the operator's identity.
"""

from __future__ import annotations

import secrets as _secrets
import tomllib
from pathlib import Path

from . import runtime_fs
from .paths import PathContainmentError, Paths

IDENTITY_FILENAME = "meshcore_identity.key"
# The openHop repeater's OWN secrets (0.2.8): a second node identity — the repeater is a distinct
# MeshCore node — and the dashboard admin password LHPC mints (upstream would otherwise start with
# a default and ask for a setup wizard). Same store, same 0600 rules, same minted-once contract.
REPEATER_IDENTITY_FILENAME = "openhop_repeater_identity.key"
REPEATER_ADMIN_FILENAME = "openhop_repeater_admin.txt"

# The pinned `ed25519_wrapper.ED25519_Wrapper` accepts exactly these two, hex-encoded: a
# 32-byte ed25519 seed, or the 64-byte Meshcore-style (a,RH) key. Any other length raises.
SEED_LEN = 32
MESHCORE_KEY_LEN = 64

# Where a generated config carries the key: the openHop-backed host reads
# `[identity] key`; the retired meshcore-pi generation wrote
# `[device.companion] privatekey`, and configs written by it are still the
# prime adoption candidates during migration.
_KEY_LOCATIONS = ((("identity",), "key"),
                  (("device", "companion"), "privatekey"))

# meshcore-pi reserves node IDs 0x00 and 0xff (the first public-key byte), and retries
# generation until the key avoids them. We mint to the same rule.
_RESERVED_IDS = (0x00, 0xFF)


class MeshCoreIdentityError(ValueError):
    """An identity is present but unusable. Never raised for a merely absent one.

    A ValueError so the config-generation boundary — which already turns ValueError into a
    typed, blocking `ConfigWrite` — reports it instead of letting it escape as a traceback.
    """


def secret_path(paths: Paths, filename: str = IDENTITY_FILENAME) -> Path:
    return paths.under("config", "secrets", filename)


def normalize_key(raw: object) -> str:
    """Return the canonical lowercase hex form of `raw`, or "" when it is not a valid
    MeshCore private key. Never raises, never logs — callers decide what absence means."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip().lower()
    if len(text) not in (SEED_LEN * 2, MESHCORE_KEY_LEN * 2):
        return ""
    try:
        bytes.fromhex(text)
    except ValueError:
        return ""
    return text


def _public_key(seed: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)


def _mint() -> str:
    """A fresh 32-byte seed whose public key avoids the reserved IDs.

    Only seeds are minted: the 64-byte Meshcore form throws its seed away and no library
    can derive a public key from it, so it can be adopted but never generated here.
    """
    while True:
        seed = _secrets.token_bytes(SEED_LEN)
        if _public_key(seed)[0] not in _RESERVED_IDS:
            return seed.hex()


def _read_secret(paths: Paths, filename: str = IDENTITY_FILENAME, *, normalize=None,
                 what: str = "MeshCore identity") -> str:
    """The stored secret, or "" if there is none.

    Raises `MeshCoreIdentityError` when the file exists but is unusable — including when it
    is readable by group or others, matching how `load_secrets` refuses a lax secrets.toml.
    A stored secret is NEVER silently replaced. `normalize` turns the raw text into the
    canonical value or "" (default: a MeshCore private key).
    """
    normalize = normalize or normalize_key
    path = secret_path(paths, filename)
    # Permissions FIRST: a lax secret must be refused rather than read. Checking after the
    # read would have already pulled key material out of a file we then declare untrusted.
    st = runtime_fs.stat_leaf_nofollow(paths, path)     # None = absent/unreadable/escaping
    if st is None:
        return ""                                      # nothing usable there yet
    if st.st_mode & 0o077:
        raise MeshCoreIdentityError(
            f"{what} at {path} is readable by group/other "
            f"({st.st_mode & 0o777:#o}) — chmod 600 it")
    try:
        raw = runtime_fs.read_bytes(paths, path)
    except FileNotFoundError:
        return ""
    except (OSError, PathContainmentError) as exc:
        raise MeshCoreIdentityError(f"unreadable {what} at {path}: {exc}") from exc
    key = normalize(raw.decode("utf-8", "replace"))
    if not key:
        raise MeshCoreIdentityError(
            f"{what} at {path} is not a valid value — refusing to replace it; "
            f"restore or remove the file deliberately")
    return key


def candidate_key(paths: Paths, path: Path) -> str:
    """`[device.companion] privatekey` from one TOML candidate.

    Returns "" when the file is missing or simply carries no key (a commented-out template
    line reads as absent, which is correct — it is not a key). Raises when the file HAS a
    `privatekey` that is invalid: that must block, never fall through to minting a new
    identity over the top of what the operator was trying to keep.
    """
    try:
        raw = runtime_fs.read_bytes(paths, path)
    except FileNotFoundError:
        return ""                       # genuinely absent — the only "no key" case
    except (OSError, PathContainmentError) as exc:
        # A symlinked leaf, a swapped parent or a non-regular file is NOT "no key": treating
        # it as absent would fall through to minting and silently rotate the identity that
        # candidate was holding. Refuse instead, the same way a bad stored secret does.
        raise MeshCoreIdentityError(
            f"cannot read {path} to check for an existing MeshCore identity ({exc}) — "
            f"refusing to continue and risk replacing it") from exc
    try:
        doc = tomllib.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError) as exc:
        # A file we cannot PARSE is not a file without a key. During migration the generated
        # config can hold the only surviving copy of the identity alongside some unrelated
        # TOML damage; falling through here would fail to find that key, mint a new one, and
        # then regenerate the file over the original — losing the node's identity precisely
        # when we were trying to rescue it. Refuse and let the operator fix the file.
        raise MeshCoreIdentityError(
            f"{path} is not valid TOML ({exc}) — refusing to continue and risk minting a "
            f"new identity over a key this file may still hold; fix or remove it first"
        ) from exc
    for section, key_name in _KEY_LOCATIONS:
        table: object = doc
        for part in section:
            if not isinstance(table, dict):
                table = None
                break
            table = table.get(part)
        if not isinstance(table, dict) or key_name not in table:
            continue
        key = normalize_key(table[key_name])
        if not key:
            raise MeshCoreIdentityError(
                f"{path} carries an invalid {key_name} — refusing to continue and mint a "
                f"new identity over it; fix or remove that value first")
        return key
    return ""


def _store(paths: Paths, key: str, filename: str = IDENTITY_FILENAME, *, normalize=None,
           what: str = "MeshCore identity") -> str:
    """Persist `key`, or return the winner of a concurrent first-write race.

    Target-exclusive creation, not an atomic replace: `atomic_write_bytes` renames over the
    target, so two processes minting at once would both "succeed" and the last rename would
    silently discard the other identity. Taking a lock instead is not an option — `start()`
    already holds the config guard SHARED and a nested exclusive under it is rejected.
    """
    runtime_fs.chmod(paths, paths.under("config", "secrets"), 0o700, create_dir=True)
    try:
        runtime_fs.create_exclusive_bytes(paths, secret_path(paths, filename),
                                          (key + "\n").encode("ascii"), mode=0o600)
    except FileExistsError:
        existing = _read_secret(paths, filename, normalize=normalize, what=what)
        if existing:                            # raises if the winner is unusable
            return existing
        raise
    return key


def adopt_identity(paths: Paths, candidates=(), filename: str = IDENTITY_FILENAME) -> str:
    """Adopt an existing identity so it survives an operation that replaces its file.

    Returns the stored key, or "" when there is nothing to adopt. NEVER mints: an operator
    updating or uninstalling a MeshCore they never ran must not end up with a key. Raises
    `MeshCoreIdentityError` if a stored secret or a candidate is present but invalid — the
    caller turns that into a refusal BEFORE deleting or replacing anything.
    """
    stored = _read_secret(paths, filename)
    if stored:
        return stored
    for cand in candidates:
        key = candidate_key(paths, cand)
        if key:
            return _store(paths, key, filename)
    return ""


def ensure_identity(paths: Paths, candidates=(), filename: str = IDENTITY_FILENAME) -> str:
    """The key to write into the generated config, minting one if none exists yet.

    The ONLY place a MeshCore identity is created (`filename` selects the node's or the
    repeater's). Adoption is tried first, so an install upgrading into this feature keeps
    the identity it already has on air.
    """
    return adopt_identity(paths, candidates, filename) or _store(paths, _mint(), filename)


_PASSWORD_LEN = 24          # token_urlsafe(24) -> 32 URL-safe characters


def _normalize_password(raw: object) -> str:
    """The stored dashboard password, or "" when the file holds nothing usable: one line of
    16..128 printable, non-blank ASCII characters."""
    s = str(raw or "").strip()
    if not (16 <= len(s) <= 128) or not s.isascii() or not s.isprintable() or " " in s:
        return ""
    return s


def ensure_password(paths: Paths, filename: str) -> str:
    """A controller-minted login secret (the openHop dashboard admin password): read the stored
    one, else mint a random URL-safe token ONCE and persist it 0600 like the identities. Never
    replaces a stored value; a lax or garbled file raises, exactly like a bad key."""
    what = "dashboard password"
    stored = _read_secret(paths, filename, normalize=_normalize_password, what=what)
    if stored:
        return stored
    return _store(paths, _secrets.token_urlsafe(_PASSWORD_LEN), filename,
                  normalize=_normalize_password, what=what)
