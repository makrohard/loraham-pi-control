"""Load the LHPC-owned MeshCore identity into an openHop LocalIdentity.

The private key IS the node's on-air identity. LHPC owns the file
(config/secrets/meshcore_identity.key, 0600); this module only ever READS it.

FAIL CLOSED: any unusable state (missing, malformed, lax permissions, wrong length)
raises IdentityError and the host must exit — a replacement identity is NEVER minted
here, so no code path can silently rotate the operator's identity.

Never log or expose private key material.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from openhop_core.protocol.identity import LocalIdentity

# LOCKSTEP: accepted key formats and the fail-closed rules mirror
# lhpc/core/meshcore_identity.py (normalize_key/_read_secret), the module that
# MINTS and stores this very file. This package installs standalone into the
# stack venv, so the rules are duplicated by necessity — change both together.
SEED_LEN = 32
MESHCORE_KEY_LEN = 64


class IdentityError(RuntimeError):
    """The stored identity is unusable. Startup must abort; never mint a new key."""


def load_identity_hex(text: str) -> LocalIdentity:
    """Import an inline hex key (LHPC injects it into the 0600 generated config).

    Same fail-closed rules as the file path; never logs key material.
    """
    cleaned = (text or "").strip().lower()
    if len(cleaned) not in (SEED_LEN * 2, MESHCORE_KEY_LEN * 2):
        raise IdentityError("inline MeshCore identity has invalid length")
    try:
        seed = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise IdentityError("inline MeshCore identity is not valid hex") from exc
    try:
        identity = LocalIdentity(seed)
    except Exception as exc:
        raise IdentityError("inline MeshCore identity could not be imported") from exc
    if len(identity.get_public_key()) != 32:
        raise IdentityError("imported identity produced an invalid public key")
    return identity


def load_identity(key_file: str | Path) -> LocalIdentity:
    path = Path(key_file)
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise IdentityError(
            f"MeshCore identity file missing: {path} — refusing to mint a replacement; "
            f"LHPC owns identity creation"
        ) from exc
    except OSError as exc:
        raise IdentityError(f"cannot stat MeshCore identity {path}: {exc}") from exc

    if not stat.S_ISREG(st.st_mode):
        raise IdentityError(f"MeshCore identity {path} is not a regular file")
    if st.st_mode & 0o077:
        raise IdentityError(
            f"MeshCore identity {path} is readable by group/other "
            f"({st.st_mode & 0o777:#o}) — chmod 600 it"
        )

    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip().lower()
    except OSError as exc:
        raise IdentityError(f"cannot read MeshCore identity {path}: {exc}") from exc

    if len(text) not in (SEED_LEN * 2, MESHCORE_KEY_LEN * 2):
        raise IdentityError(
            f"MeshCore identity {path} has invalid length — refusing to continue"
        )
    try:
        seed = bytes.fromhex(text)
    except ValueError as exc:
        raise IdentityError(
            f"MeshCore identity {path} is not valid hex — refusing to continue"
        ) from exc

    try:
        identity = LocalIdentity(seed)
    except Exception as exc:
        raise IdentityError(f"MeshCore identity {path} could not be imported") from exc

    pub = identity.get_public_key()
    if len(pub) != 32:
        raise IdentityError("imported identity produced an invalid public key")
    return identity
