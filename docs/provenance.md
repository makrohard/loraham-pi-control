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
| `stable` | Latest stable | Git-only: the newest version-shaped tag — the WHOLE name is an optional `v` and dot-separated numbers (`v112`, `v1.2`, `1.5.2`) — else the default-branch HEAD. A build-suffixed (`v2.8.0.7239fe8`) or prerelease (`1.8.2-pre`) tag is a snapshot, not a release, and is ignored. One rule for both the local and the remote resolution, so the selector resolves to one commit either way. The exact resolved commit is recorded. Explicit opt-in. | ❌ mutable |

Without `--source`, install, `update` and `auto-install` (and so the image builder) take the
per-stack default channel: the published **binary** where there is one for this platform, else
**`pinned`** ([cli.md](cli.md)). A default install therefore lands on a composition this release
proved, and a published image carries the release's pins. `dev` and `stable` are mutable and
explicit — nothing reaches them by leaving a selector alone.

An **unpinned** component cannot be installed as `pinned` — with no configured pin it is
`unverified-blocked`, and you must choose `dev` or `stable` explicitly. lhpc never fabricates
a missing pin or signature. An **artifact** source (`artifact = true`: chat) resolves every selector to the same declared artifact (`artifact-head`).
Every source lives under the runtime root as a managed clone. For a source checkout,
`lhpc status --versions` reads `match` only while the checked-out ref still equals the pin: a
`dev` checkout turns to `differs` once upstream moved.

## Ownership records

Every adoption records durable ownership (`state/source-registry/`): remote, selector, exact
resolved commit, transaction id — written inside the activation transaction and completable by
recovery. A record that no longer matches its tree is never rewritten silently, and a tree
without one is not LHPC's to touch. Update, uninstall and clean re-prove the record first and
refuse when the checkout's `HEAD` or origin no longer matches it — the tree changed outside an
LHPC transaction (update also needs the affected stacks stopped); the recovery commands are in
[operations.md](operations.md#identity-drift-on-clean-or-uninstall).

**New files: OK.** Files added by the user or by the stack to a managed source checkout —
logs, generated settings, a scratch script, whether ignored by Git or not — are preserved
across managed source updates: they are copied into the new source at the same path, and the
old checkout is discarded only once each of them is proven to be there. A path the new upstream
version also ships is a refusal naming the file, never a merge, a rename or an overwrite.

The exception is LHPC's own regenerable output, which is neither preserved nor allowed to block:
anything under `build/`, `.pio/`, `.venv/`, `.work/`, `.run/`, `__pycache__/` or `node_modules/`,
and a component's declared built binary. Those are LHPC's to recreate; put nothing there you
want to keep.

**Editing, deleting or staging an upstream-tracked file** makes the checkout dirty and blocks
the update. Revert or stash it. **To run a modified stack, fork the project and point the
component's remote and pin at your fork** ([Remote overrides](#remote-overrides)) — a managed
checkout is not a place to keep source changes.

Uninstall and clean protect the whole tree instead: they carry nothing forward, so for them any
local file the dirty check sees still counts. A **binary** install is stricter only where the
artifact runs code from the pinned checkout (today MeshCom's QEMU node): there a file an update
would carry refuses the install by name. What the dirty check sees, here as everywhere, is
tracked changes and untracked files, never Git-ignored ones.

## The binary channel

Three long-compiling stacks (daemon, meshtastic, meshcom) can be installed as a **prebuilt
artifact** instead of a source build. Same policy, different medium:

- trust anchor: **HTTPS + sha256** (size verified too), checked **before** anything is unpacked;
- the artifact's per-component commits must equal this lhpc's **manifest pins** exactly — a
  lagging artifact is refused, never installed "close enough";
- it must match this platform and have passed the builder's mandatory smoke test on a clean image;
- provenance is recorded per install in `state/binary/<stack>.json` and shown as
  `binary@<sha>` by `lhpc status --versions`.

Four different questions get four different answers, and they are easy to confuse:

| question | what answers it |
|---|---|
| is this checkout the commit it claims? | the ownership record plus its live `HEAD` (`source_registry.verify_identity`) |
| is this stack installed from an artifact at all? | the receipt's four-state read (`receipt_state`) — cheap, no hashing |
| is that artifact the right COMMITS? | the receipt's `components` map against the manifest pins — the same comparison the install gate makes. A start refuses a covered component whose installed artifact is behind the manifest (publishing a new artifact never updates an installed copy): `lhpc update <stack>` |
| are the artifact's FILES still as installed? | `verify_files`, which hashes them |

The last one is an integrity check on what was installed, not a statement of provenance, and it
is time-sensitive: an emulated node writes to its own flash as soon as it boots, so a mismatch
there after a start means the node ran. Check it before starting; identify by commits after.

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

**Changing the source itself** (the fork from [Ownership records](#ownership-records)): the
override selects the remote, and the commit it installs still comes from the selector and the
pin, so a `pinned` install also needs the pin moved to a commit of your fork (a manifest change).
