# Source provenance policy

lhpc treats managed source selection as a supply-chain decision. Source-mutating operations
(install / update) default to the **pinned immutable commit** where a pin exists.

## Contents

- [Selections](#selections)
- [Verification status](#verification-status-truthful)
- [Signed commits/tags](#signed-commitstags-optional)
- [Remote overrides](#remote-overrides)

## Selections

| `--source` | Web label | Meaning | Production-safe? |
|---|---|---|---|
| `pinned` *(default)* | Known working | The newest operator-confirmed known-working composition entry for the stack; else the manifest pin (clearly labelled `fallback`). `HEAD ==` the expected commit is verified either way. | ✅ (immutable) |
| `dev` | Development | The configured development branch tip. Never silently another ref — an unobtainable branch is a typed "selector unavailable". Explicit opt-in. | ❌ mutable |
| `stable` | Latest stable | Git-only: newest version-shaped tag ("release"), else newest tag, else the default-branch HEAD. The exact resolved commit is recorded. Explicit opt-in. | ❌ mutable |

An **unpinned** component cannot be installed as `pinned` — with no configured pin it is
`unverified-blocked`, and you must choose `dev` or `stable` explicitly. lhpc never fabricates
a missing pin or signature. An **artifact** source (`artifact = true`: chat, igate, voice,
meshtastic base) resolves every selector to the same declared artifact (`artifact-head`).
`strategy = "link"` is NO LONGER ACCEPTED at manifest load (containment: every source lives
under the runtime root as a managed clone). The link machinery is retained so a LEGACY
runtime symlink leaf is still recognized and refused safely.

Every adoption records durable ownership (`state/source-registry/`): remote, selector, exact
resolved commit, transaction id — written inside the activation transaction (journal v3),
completable by recovery. Update/uninstall/clean require ownership (a pre-registry tree must
origin-match its configured remote to be backfilled); update also requires the affected
stacks stopped and refuses dirty trees (tracked or non-ignored untracked changes).
`lhpc clean <stack> --purge` is the explicit destructive escape hatch (typed confirm on
the web); normal uninstall retains config, logs and history.

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
