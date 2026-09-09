"""5.1 — source provenance policy + signed-commit verification. Faked command runner:
no real keyring, network, or clone. Signed provenance is claimed ONLY when git reports a
good signature from a configured trusted signer."""

from dataclasses import dataclass

from lhpc.core import provenance as pv
from lhpc.core.probes.backends import CommandResult


@dataclass
class _Spec:
    pin_commit: str = ""
    pin_tag: str = ""


class _Runner:
    """Runner keyed by the git subcommand; returns configured CommandResults."""

    def __init__(self, head="", verify=None):
        self._head = head
        self._verify = verify or CommandResult(1, "", "no signature")

    def run(self, argv, timeout=None, cwd=None, env=None):
        if "rev-parse" in argv:
            return CommandResult(0 if self._head else 128, self._head, "")
        if "verify-commit" in argv or "verify-tag" in argv:
            return self._verify
        return CommandResult(127, "", "unexpected")


PIN = "24606a7703a4c9f85f19698a00b69278b5f1c99b"
FPR = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"


def test_dev_is_mutable_not_production_safe():
    r = pv.evaluate(_Runner(), "/d", _Spec(pin_commit=PIN), "dev")
    assert r.status == pv.MUTABLE_DEV and r.ok and not r.production_safe


def test_stable_is_mutable_not_production_safe():
    r = pv.evaluate(_Runner(), "/d", _Spec(pin_commit=PIN), "stable")
    assert r.status == pv.MUTABLE_STABLE and not r.production_safe


def test_unpinned_pinned_request_is_blocked():
    r = pv.evaluate(_Runner(head=PIN), "/d", _Spec(pin_commit=""), "pinned")
    assert r.status == pv.UNVERIFIED_BLOCKED and not r.ok and not r.production_safe


def test_head_not_at_pin_is_blocked():
    r = pv.evaluate(_Runner(head="deadbeef"), "/d", _Spec(pin_commit=PIN), "pinned")
    assert r.status == pv.UNVERIFIED_BLOCKED and not r.ok


def test_pinned_verified_without_signers():
    r = pv.evaluate(_Runner(head=PIN), "/d", _Spec(pin_commit=PIN), "pinned")
    assert r.status == pv.PINNED_VERIFIED and r.ok and r.production_safe and not r.signed


def test_signature_verified_with_trusted_signer():
    good = CommandResult(0, "", f"[GNUPG:] VALIDSIG {FPR} 2020-01-01 0 4 0 1 8 00 {FPR}\n")
    r = pv.evaluate(_Runner(head=PIN, verify=good), "/d", _Spec(pin_commit=PIN), "pinned",
                    trusted_fingerprints=[FPR])
    assert r.status == pv.SIGNATURE_VERIFIED and r.signed and r.production_safe


def test_signature_unavailable_when_untrusted_key():
    other = CommandResult(0, "", "[GNUPG:] VALIDSIG 999 2020 0 4 0 1 8 00 999\n")
    r = pv.evaluate(_Runner(head=PIN, verify=other), "/d", _Spec(pin_commit=PIN), "pinned",
                    trusted_fingerprints=[FPR])
    assert r.status == pv.SIGNATURE_UNAVAILABLE and r.production_safe and not r.signed


def test_signature_unavailable_when_verify_fails():
    bad = CommandResult(1, "", "[GNUPG:] BADSIG ...")
    r = pv.evaluate(_Runner(head=PIN, verify=bad), "/d", _Spec(pin_commit=PIN), "pinned",
                    trusted_fingerprints=[FPR])
    assert r.status == pv.SIGNATURE_UNAVAILABLE and not r.signed


def test_signed_never_claimed_without_configured_signers():
    good = CommandResult(0, "", f"[GNUPG:] VALIDSIG {FPR} ... {FPR}\n")
    # signers not configured -> we never even claim signature verification.
    r = pv.evaluate(_Runner(head=PIN, verify=good), "/d", _Spec(pin_commit=PIN), "pinned")
    assert r.status == pv.PINNED_VERIFIED and not r.signed


def test_verify_signature_requires_trusted_list():
    ok, why = pv.verify_signature(_Runner(), "/d", "ref", trusted_fingerprints=[])
    assert not ok and "no trusted signer" in why


# --- C: trusted-signer config loading (typed, dedup, safe) --------------------

def test_load_trusted_signers_valid_dedup_and_drops():
    from lhpc.core.config import Config
    f = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
    cfg = Config(values={"provenance": {"trusted_signers": [f.lower(), " " + f + " ", "bad", 123]}})
    sigs, diags = pv.load_trusted_signers(cfg)
    assert sigs == [f]                                        # upper-cased, deduped, cleaned
    assert any("malformed" in d for d in diags) and any("non-string" in d for d in diags)


def test_load_trusted_signers_none_configured_is_empty():
    from lhpc.core.config import Config
    assert pv.load_trusted_signers(Config(values={})) == ([], [])   # no fabricated trust


def test_load_trusted_signers_non_list_blocks_safely():
    from lhpc.core.config import Config
    sigs, diags = pv.load_trusted_signers(Config(values={"provenance": {"trusted_signers": "x"}}))
    assert sigs == [] and any("non-list" in d for d in diags)
