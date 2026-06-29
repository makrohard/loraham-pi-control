"""5.1 — source provenance policy + optional signed-commit/tag verification.

The verification status is TRUTHFUL and never fabricated — signed provenance is only ever
claimed when `git verify-commit`/`verify-tag` actually reports a good signature from a
configured trusted signer:

  * ``pinned-verified``       HEAD is exactly the configured immutable pin commit.
  * ``signature-verified``    ...AND the pin is signed by a configured trusted signer.
  * ``signature-unavailable`` pin verified, but no valid trusted-signer signature was
                              obtained (signers were configured but verification failed).
  * ``mutable-dev`` / ``mutable-stable``  explicit operator opt-in to a MUTABLE branch
                              (clearly non-default, not production-safe).
  * ``unverified-blocked``    no pin exists (or HEAD != pin) and no explicit mutable
                              choice — never silently treated as production-safe.

`git`'s machine-readable GPG status is parsed from ``verify-*  --raw`` output; a signature
counts ONLY when a VALIDSIG fingerprint matches a configured trusted fingerprint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A GPG v4 primary-key fingerprint is 40 hex chars (spaces allowed as visual grouping).
_FPR_RE = re.compile(r"[0-9A-F]{40}")


def load_trusted_signers(config) -> tuple[list[str], list[str]]:
    """Read `[provenance].trusted_signers` (a list of 40-hex GPG fingerprints) from runtime
    config with STRICT typed validation. Returns (fingerprints, diagnostics): entries are
    upper-cased, space-stripped and DEDUPED; a non-list section, non-string entry, or
    malformed fingerprint is DROPPED with a diagnostic — never a crash and never fabricated
    trust. With none configured, callers must not claim signed provenance."""
    diags: list[str] = []
    raw = config.get("provenance", "trusted_signers", None) if config is not None else None
    if raw is None:
        return [], diags
    if not isinstance(raw, (list, tuple)):
        return [], [f"ignored non-list [provenance].trusted_signers ({type(raw).__name__})"]
    seen, out = set(), []
    for item in raw:
        if not isinstance(item, str):
            diags.append(f"ignored non-string trusted signer ({type(item).__name__})")
            continue
        fpr = item.strip().replace(" ", "").upper()
        if not _FPR_RE.fullmatch(fpr):
            diags.append(f"ignored malformed signer fingerprint {item!r}")
            continue
        if fpr not in seen:
            seen.add(fpr)
            out.append(fpr)
    return out, diags


PINNED_VERIFIED = "pinned-verified"
SIGNATURE_VERIFIED = "signature-verified"
SIGNATURE_UNAVAILABLE = "signature-unavailable"
MUTABLE_DEV = "mutable-dev"
MUTABLE_STABLE = "mutable-stable"
UNVERIFIED_BLOCKED = "unverified-blocked"



@dataclass(frozen=True)
class ProvenanceResult:
    status: str
    ok: bool                 # the requested source selection may proceed
    production_safe: bool    # anchored to the immutable pin (pinned/signed)
    detail: str

    @property
    def signed(self) -> bool:
        return self.status == SIGNATURE_VERIFIED


def _head_commit(runner, dest: str) -> str | None:
    r = runner.run(["git", "-C", dest, "rev-parse", "HEAD"], timeout=10)
    return r.stdout.strip() if r.returncode == 0 else None


def verify_signature(runner, dest: str, ref: str, trusted_fingerprints,
                     is_tag: bool = False) -> tuple[bool, str]:
    """Verify a commit/tag GPG signature via git. A signature counts ONLY when git exits 0
    AND a VALIDSIG fingerprint matches a configured trusted signer. Never claims signed on
    failure, and never requires a real keyring/network in tests (the runner is injectable)."""
    trusted = {f.strip().upper() for f in (trusted_fingerprints or ()) if f.strip()}
    if not trusted:
        return False, "no trusted signer fingerprints configured"
    cmd = ["git", "-C", dest, "verify-tag" if is_tag else "verify-commit", "--raw", ref]
    r = runner.run(cmd, timeout=20)
    blob = (r.stderr or "") + "\n" + (r.stdout or "")   # git prints GPG status to stderr
    for line in blob.splitlines():
        parts = line.split()
        if "VALIDSIG" in parts:
            i = parts.index("VALIDSIG")
            fpr = parts[i + 1].upper() if i + 1 < len(parts) else ""
            # The VALIDSIG line's last token is the primary-key fingerprint; accept either.
            if r.returncode == 0 and ({fpr, parts[-1].upper()} & trusted):
                return True, f"good signature from trusted key {fpr[:16]}…"
    return False, "no valid signature from a trusted signer"


def evaluate(runner, dest: str, spec, source: str,
             trusted_fingerprints=()) -> ProvenanceResult:
    """Truthful provenance of the activated source at `dest` for the requested `source`
    selection ('pinned' | 'dev' | 'stable'). `spec` is the component's source spec
    (with `pin_commit` / `pin_tag`)."""
    if source == "dev":
        return ProvenanceResult(MUTABLE_DEV, True, False,
                                "explicit mutable dev branch — NOT production-safe")
    if source == "stable":
        return ProvenanceResult(MUTABLE_STABLE, True, False,
                                "explicit mutable stable branch — NOT production-safe")
    # 'pinned' (the production-safe default): require a configured pin AND HEAD==pin.
    pin = getattr(spec, "pin_commit", "") if spec else ""
    if not pin:
        return ProvenanceResult(UNVERIFIED_BLOCKED, False, False,
                                "no configured pin commit — not production-safe; choose "
                                "'dev' or 'stable' explicitly to use a mutable branch")
    head = _head_commit(runner, dest)
    if head != pin:
        return ProvenanceResult(UNVERIFIED_BLOCKED, False, False,
                                f"HEAD {head or '?'} != pinned {pin} (not at the pin)")
    if not trusted_fingerprints:
        return ProvenanceResult(PINNED_VERIFIED, True, True,
                                f"at pinned commit {pin[:12]} (no trusted signer configured)")
    tag = getattr(spec, "pin_tag", "") if spec else ""
    signed, why = verify_signature(runner, dest, tag or pin, trusted_fingerprints,
                                   is_tag=bool(tag))
    if signed:
        return ProvenanceResult(SIGNATURE_VERIFIED, True, True,
                                f"at pinned commit {pin[:12]}; {why}")
    return ProvenanceResult(SIGNATURE_UNAVAILABLE, True, True,
                            f"at pinned commit {pin[:12]}; signature NOT verified ({why})")
