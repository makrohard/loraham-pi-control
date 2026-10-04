# Source provenance policy

Which source commit or artifact lhpc may install, and what it records about it.

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
| `pinned` | Known working | The newest operator-confirmed known-working composition entry for the stack, else the manifest pin (labelled `fallback`). `HEAD` must equal that commit either way. | ✅ (immutable) |
| `dev` | Development | The configured development branch tip; an unobtainable branch is a typed "selector unavailable", never another ref. | ❌ mutable |
| `stable` | Latest stable | Git-only: the newest tag whose WHOLE name is an optional `v` and dot-separated numbers (`v112`, `v1.2`, `1.5.2`), else the default-branch HEAD. Build-suffixed (`v2.8.0.7239fe8`) and prerelease (`1.8.2-pre`) tags are ignored. Local and remote resolution use the same rule; the resolved commit is recorded. | ❌ mutable |

Without `--source`, install and `auto-install` (and so the image builder) use the stack's default
channel (a bare `update` keeps the installed channel): the published **binary** where there is one for this platform, else
**`pinned`**. `dev` and `stable` are reached only by naming them.

A component with no configured pin cannot be installed as `pinned` (`unverified-blocked`); choose
`dev` or `stable` explicitly. lhpc never fabricates a missing pin or signature. An **artifact**
source (`artifact = true`) resolves every selector to the same declared artifact
(`artifact-head`); no shipped component declares it.

Every source lives under the runtime root as a managed clone. `lhpc status --versions` reads
`match` only while the checked-out commit equals the pin: a `dev` checkout reads `differs` once
upstream moved.

## Ownership records

Every adoption records ownership in `state/source-registry/` (remote, selector, resolved commit,
transaction id), written inside the activation transaction and completable by recovery. A record
that no longer matches its tree is never rewritten silently, and a tree without one is not LHPC's
to touch. Update, uninstall and clean re-prove the record first and refuse when the checkout's
`HEAD` or origin no longer matches it (update also needs the affected stacks stopped). Recovery:
[operations.md](operations.md#identity-drift-on-clean-or-uninstall).

**New files: OK.** Files you or the stack add to a managed checkout (logs, generated settings, a
scratch script, Git-ignored or not) are copied into the new source at the same path on update;
the old checkout is discarded only once each is proven there. A path the new upstream version
also ships is a refusal naming the file, never a merge or overwrite. A Git repository of your own
inside a checkout (a folder with its own `.git`) is not carried: the update refuses naming it; move it
out of the checkout.

Not preserved and never blocking: anything under `build/`, `.pio/`, `.venv/`, `.work/`, `.run/`,
`__pycache__/` or `node_modules/`, and a component's declared built binary. LHPC recreates
these; keep nothing there.

**Editing, deleting or staging an upstream-tracked file** makes the checkout dirty and blocks the
update; revert or stash it. **To run a modified stack, fork it and point the component's remote
and pin at your fork** ([Remote overrides](#remote-overrides)).

Uninstall and clean carry nothing forward, so for them any local file the dirty check sees
counts. A **binary** install whose artifact runs code from the pinned checkout (MeshCom's QEMU
node) refuses when that checkout holds a file an update would carry, naming it. The dirty check
sees tracked changes and untracked files, never Git-ignored ones.

## The binary channel

Three long-compiling stacks (daemon, meshtastic, meshcom) can be installed as a **prebuilt
artifact** instead of a source build:

- trust anchor: **HTTPS + sha256 + size**, checked **before** anything is unpacked;
- the artifact's per-component commits must equal this lhpc's **manifest pins**; a lagging
  artifact is refused unless the operator accepts that exact mismatch
  ([`--accept-pin-mismatch`](cli.md#install));
- it must match this platform and record a passed mandatory builder smoke test;
- the install is recorded in `state/binary/<stack>.json` and shown as `binary@<sha>` by
  `lhpc status --versions`.

| question | what answers it |
|---|---|
| is this checkout the commit it claims? | the ownership record plus its live `HEAD` (`source_registry.verify_identity`) |
| is this stack installed from an artifact at all? | the receipt's four-state read (`receipt_state`), no hashing |
| is that artifact the right COMMITS? | the receipt's `components` map against the manifest pins, as at install. A start refuses while the started component, or another covered runnable component, is behind its pin without an acceptance in force (a new published artifact never updates an installed copy): `lhpc update <stack>`. A covered library or firmware image (the daemon's RadioLib, MeshCom's firmware) never blocks a start; its lag shows in the update status |
| are the artifact's FILES still as installed? | `verify_files`, which hashes them |

`verify_files` is an integrity check, not provenance, and time-sensitive: an emulated node writes
its own flash as soon as it boots, so a mismatch after a start means the node ran. Check files
before starting; identify by commits after.

Any failed check is a typed refusal that offers the source channel, never a silent fallback.
Artifacts are built and published by [lhpc-binaries](https://github.com/makrohard/lhpc-binaries),
which compiles exactly the pin. Operating a binary install:
[operations.md](operations.md#install-channels).

## Verification status

`lhpc.core.provenance.evaluate()` reports one of:

- **`pinned-verified`**: `HEAD` is exactly the pin (or known-working) commit.
- **`signature-verified`**: pin verified **and** signed by a configured trusted signer.
- **`signature-unavailable`**: pin verified, signers configured, but no valid trusted signature.
- **`mutable-dev` / `mutable-stable`**: explicit mutable selection (not production-safe).
- **`unverified-blocked`**: no pin, or `HEAD != pin`, and no explicit mutable choice.
- **`artifact-head`**: a declared artifact source.

## Signed commits/tags

Optional. lhpc runs `git verify-tag --raw` on the `pin_tag` when it is an annotated tag on the pinned
commit, else `git verify-commit --raw` on the pinned commit, and parses the GPG status; a
signature on anything but the pin never counts.
A signature counts **only** when git exits 0 **and** a `VALIDSIG` fingerprint matches a
configured trusted signer (full GPG fingerprints). Without configured signers the status stays
`pinned-verified`.

## Remote overrides

A per-component remote override (`[remotes]` in `local.toml`) must be `https://` or scp-style
`git@host:path`; it is validated before any Git use, and a non-string value is dropped at config
load. It changes only where the source comes from, never the pin/signature policy: a `pinned`
install of a fork also needs the pin moved to a commit of the fork, a manifest change
([moving a pin](maintenance.md#moving-a-pin)).
