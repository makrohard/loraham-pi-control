# Source provenance policy

lhpc treats managed source selection as a supply-chain decision. Source-mutating operations
(install / update) default to the **pinned immutable commit** where a pin exists.

## Selections

| `--source` | Meaning | Production-safe? |
|---|---|---|
| `pinned` *(default)* | Check out the manifest's pinned known-good commit and verify `HEAD == pin`. | ✅ (immutable) |
| `dev` | The operator's mutable dev branch/tree. Explicit opt-in. | ❌ mutable |
| `stable` | The latest release tag. Explicit opt-in. | ❌ mutable |

An **unpinned** component cannot be installed as `pinned` — with no configured pin it is
`unverified-blocked`, and you must choose `dev` or `stable` explicitly. lhpc never fabricates
a missing pin or signature. A **linked external tree** (`strategy = "link"`) is inherently an
explicit mutable dev checkout (a symlink can't be pinned) and stays read-only to lhpc.

## Verification status (truthful)

`lhpc.core.provenance.evaluate()` reports one of:

- **`pinned-verified`** — `HEAD` is exactly the configured pin commit.
- **`signature-verified`** — pin verified **and** signed by a configured trusted signer.
- **`signature-unavailable`** — pin verified, but no valid trusted-signer signature was
  obtained (signers were configured, verification did not succeed).
- **`mutable-dev` / `mutable-stable`** — explicit mutable selection (not production-safe).
- **`unverified-blocked`** — no pin (or `HEAD != pin`) and no explicit mutable choice.

## Signed commits/tags (optional)

Signature verification uses Git's own facilities — `git verify-commit --raw` /
`git verify-tag --raw` — and parses the machine-readable GPG status. A signature counts
**only** when git exits 0 **and** a `VALIDSIG` fingerprint matches a configured trusted
signer fingerprint. Configure trusted signers as full GPG fingerprints; without them,
lhpc never claims signed provenance (it stays `pinned-verified`).

There is **no raw SET passthrough** and no network/keyring requirement in tests — the command
runner is injectable, so signature behavior is covered with a faked runner.

## Remote overrides

A per-component remote override (`[remotes]` in `local.toml`) is validated to a safe remote
URL before any Git use, and a non-string/malformed remote is dropped at config load — it can
never silently weaken the selected pin/signature policy or reach Git.
