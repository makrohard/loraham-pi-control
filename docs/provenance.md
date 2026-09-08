# Source provenance policy

lhpc treats managed source selection as a supply-chain decision. Source-mutating operations
(install / update) default to the **pinned immutable commit** where a pin exists.

## Contents

- [Selections](#selections)
- [Ownership records](#ownership-records)
- [The binary channel](#the-binary-channel)
- [Verification status](#verification-status)
- [Signed commits/tags](#signed-commitstags)
- [Remote overrides](#remote-overrides)

## Selections

| `--source` | Web label | Meaning | Production-safe? |
|---|---|---|---|
| `pinned` | Known working | The newest operator-confirmed known-working composition entry for the stack; else the manifest pin (clearly labelled `fallback`). `HEAD ==` the expected commit is verified either way. | ✅ (immutable) |
| `dev` | Development | The configured development branch tip. Never silently another ref — an unobtainable branch is a typed "selector unavailable". Explicit opt-in. | ❌ mutable |
| `stable` | Latest stable | Git-only: newest version-shaped tag ("release"), else newest tag, else the default-branch HEAD. The exact resolved commit is recorded. Explicit opt-in. | ❌ mutable |

Without `--source`, install and `auto-install` (and so the image builder) take the per-stack
default channel ([cli.md](cli.md)); only the all-stacks `lhpc install` form, with no stack named,
stays on `dev`. `pinned` is the known-working line you choose explicitly.

An **unpinned** component cannot be installed as `pinned` — with no configured pin it is
`unverified-blocked`, and you must choose `dev` or `stable` explicitly. lhpc never fabricates
a missing pin or signature. An **artifact** source (`artifact = true`: chat and voice) resolves every selector to the same declared artifact (`artifact-head`).
Every source lives under the runtime root as a managed clone. For a source checkout,
`lhpc status --versions` reads `match` only while the checked-out ref still equals the pin: a
`dev` checkout turns to `differs` once upstream moved.

## Ownership records

Every adoption records durable ownership (`state/source-registry/`): remote, selector, exact
resolved commit, transaction id — written inside the activation transaction and completable by
recovery. A record that no longer matches its tree is never rewritten silently, and a tree
without one is not LHPC's to touch. Update, uninstall and clean re-prove the record first
(update also needs the affected stacks stopped and refuses a dirty tree); what they refuse, and
how to recover, is in [operations.md](operations.md).

## The binary channel

Three long-compiling stacks (daemon, meshtastic, meshcom) can be installed as a **prebuilt
artifact** instead of a source build. Same policy, different medium:

- trust anchor: **HTTPS + sha256** (size verified too), checked **before** anything is unpacked;
- the artifact's per-component commits must equal this lhpc's **manifest pins** exactly — a
  lagging artifact is refused, never installed "close enough";
- it must match this platform and have passed the builder's mandatory smoke test on a clean image;
- provenance is recorded per install in `state/binary/<stack>.json` and shown as
  `binary@<sha>` by `lhpc status --versions`.

Any failed check is a typed refusal that offers the source channel — never a silent fallback.
Artifacts are built and published by
[lhpc-binaries](https://github.com/makrohard/lhpc-binaries), which compiles exactly the pin. What
the channel means when operating a stack: [operations.md](operations.md).

## Verification status

`lhpc.core.provenance.evaluate()` reports one of:

- **`pinned-verified`** — `HEAD` is exactly the configured pin commit.
- **`signature-verified`** — pin verified **and** signed by a configured trusted signer.
- **`signature-unavailable`** — pin verified, but no valid trusted-signer signature was
  obtained (signers were configured, verification did not succeed).
- **`mutable-dev` / `mutable-stable`** — explicit mutable selection (not production-safe).
- **`unverified-blocked`** — no pin (or `HEAD != pin`) and no explicit mutable choice.
- **`artifact-head`** — a declared artifact source; every selector resolves to it.

## Signed commits/tags

Optional. Signature verification uses Git's own facilities — `git verify-commit --raw` /
`git verify-tag --raw` — and parses the machine-readable GPG status. A signature counts
**only** when git exits 0 **and** a `VALIDSIG` fingerprint matches a configured trusted
signer fingerprint. Configure trusted signers as full GPG fingerprints; without them,
lhpc never claims signed provenance (it stays `pinned-verified`).

## Remote overrides

A per-component remote override (`[remotes]` in `local.toml`) is validated to a safe remote
URL (https or scp-style ssh) before any Git use, and a non-string/malformed remote is dropped
at config load — it can never silently weaken the selected pin/signature policy or reach Git.
Moving a pin is a maintainer task: [maintenance.md](maintenance.md).
