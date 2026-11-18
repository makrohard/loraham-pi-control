# Source provenance policy

Which source commit or artifact lhpc may install, and what it records about it.

## Contents

- [Selections](#selections)
- [Ownership records](#ownership-records)
- [The binary channel](#the-binary-channel)
- [The web client](#the-web-client)
- [Verification status](#verification-status)
- [Signed commits/tags](#signed-commitstags)
- [Remote overrides](#remote-overrides)

## Selections

| `--source` | Web label | Meaning | Production-safe? |
|---|---|---|---|
| `pinned` | Known working | The manifest pin. `HEAD` must equal it; the update badge shows only when the checkout does not contain the pin (behind it or beside it); a checkout ahead of it reads "ahead of the pin". | ✅ (immutable) |
| `dev` | Development | The configured development branch tip; an unobtainable branch is a typed "selector unavailable", never another ref. | ❌ mutable |
| `stable` | Latest stable | The latest release or tag on the component's branch (its manifest branch; one the upstream lacks is refused by name), by history, never by comparing version numbers: the upstream's latest GitHub release, on any branch, unless a plain version tag (an optional `v` and one to three numbers: `v1.2`, `1.5.2`) on that branch's first-parent line comes after it: then that tag; without releases, the newest version tag on that first-parent line; neither: the branch's tip. Other tags (`cleanup-ready`, a hash-suffixed alpha, tags on other branches, also ones merged in from them) never count. While the releases cannot be read (also a repository the API does not show) nothing installs: "try again later". lhpc keeps a small history copy per upstream and branch under `state/stable-history-v2/` (commits and tags, no files). | ❌ mutable |

Every git lane: the update badge shows only when the checkout does not already contain the lane's target. A bare update never goes back ("not newer": the source is kept), with one exception: a development branch that cannot be read now installs the pin, and says so (one gone upstream installs the pin only when that is a plain forward move; otherwise the source is kept, named); a lane you name with `--source` installs its target wherever it lies. Back, sideways or over commits that are not upstream, the plan warns first and the move applies on Confirm or `--yes`; the stack's saved config may then not match: remove it and reinstall it as Known working.

Without `--source`, install and `auto-install` (and so the image builder) use the stack's default
channel (a bare `update` keeps the installed channel): the published **binary** where there is one for this platform, else
**`pinned`**. The exception is `lhpc install` without a stack: one plan covers every stack and
the binary channel installs one stack at a time, so it uses `pinned`; name a stack for its binary.
`dev` and `stable` are reached only by naming them.

A component with no configured pin cannot be installed as `pinned` (`unverified-blocked`); choose
`dev` or `stable` explicitly. lhpc never fabricates a missing pin or signature. An **artifact**
source (`artifact = true`) resolves every selector to the same declared artifact
(`artifact-head`); no shipped component declares it.

For a fetched package (the Meshtastic web client) `--source` picks the release, not a checkout:
[the web client](#the-web-client).

Every source lives under the runtime root as a managed clone. `lhpc status`, `lhpc status --versions`
and the stack page show one verdict per source against its own lane, from the last `lhpc source-check` of
the checked-out commit: `current`, `update available`, `update LHPC first` or `could not check`
(`unchecked` before a check). After a self-update a verdict reads `unchecked` until the next check; a manual
move of the LHPC checkout shows after the next check. A dirty or missing checkout shows that instead. The pin is a detail, not a verdict.

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
- the index (`index-v3.json`) keeps, per stack, the newest artifact of every published
  composition, newest first; lhpc installs the newest entry for this platform by `published_at`
  (equal stamps: the list order), proven or not, at whatever commits it was built from. It must name
  every component this lhpc's binary spec covers ("no published binary covers <component> yet:
  install from source", one refusal: self-update first, the source channel second); an extra one is
  accepted. An update offers it only when it was published after the installed one, so a binary the
  publisher withdrew stays installed and is never replaced by an older one; `lhpc source-check`
  reads the same target (it reads the index; "could not check" when the index cannot be read);
- it must match this platform and record a passed mandatory builder smoke test;
- the install is recorded in `state/binary/<stack>.json` and shown as `binary@<sha>` by
  `lhpc status --versions`.

| question | what answers it |
|---|---|
| is this checkout the commit it claims? | the ownership record plus its live `HEAD` (`source_registry.verify_identity`) |
| is this stack installed from an artifact at all? | the receipt's four-state read (`receipt_state`), no hashing |
| is that artifact the right COMMITS? | the receipt's `components` map: the commits it was built from, recorded at install. A start does not compare them with the manifest pins (a moved pin is an update, not a broken install); a covered component the receipt does not record refuses (`lhpc update <stack>` or a source install). `lhpc update <stack>` installs a newer published entry; `lhpc source-check` names one |
| are the artifact's FILES still as installed? | `verify_files`, which hashes them |

`verify_files` is an integrity check, not provenance, and time-sensitive: an emulated node writes
its own flash as soon as it boots, so a mismatch after a start means the node ran. Check files
before starting; identify by commits after.

Any failed check is a typed refusal that offers the source channel, never a silent fallback.
Artifacts are built and published by [lhpc-binaries](https://github.com/makrohard/lhpc-binaries),
which compiles exactly the pin. Operating a binary install:
[operations.md](operations.md#install-channels).

## The web client

The Meshtastic browser UI is its own component, `meshtastic-web`, served by meshtasticd from
`build/tools/meshtastic-web/web`: outside `build/tools/meshtasticd`, which a binary install
replaces, so switching the client never touches meshtasticd.

| `--source` | what is installed | trust anchor |
|---|---|---|
| `binary` (default, Known working) | the manifest pin's meshtastic/web release `build.tar` | the manifest's sha256 |
| `stable` | the newest upstream release, republished by lhpc-binaries | the index-v3 sha256 and the entry's lab smoke against that meshtasticd |
| `dev` | the latest lab-built snapshot of upstream `main` (it may trail `main` by one weekly build) | as `stable` |
| `pinned` | refused: the web client has no compiled lane | — |

In the console, the meshtastic stack's **Web client** control shows the installed client and its
line, and offers the three lines: *binary* (the known-working pin), *stable* (the newest build
paired with your meshtasticd) and *dev* (the newest nightly paired with it). Where the box has no
binary index, stable and dev are greyed out with the reason. **Switch** opens the plan page, which
names the exact version that line would install, or why there is none, and **Confirm** installs
it; nothing is installed before Confirm. The control itself shows no stable or dev version number:
that is read from the index only when you ask for that line, never when the page loads.

`lhpc update meshtastic-web --source binary|stable|dev --yes` switches it in seconds; a bare
`lhpc update meshtastic-web` and a rebuild keep the recorded choice. A `stable`/`dev` entry is taken
only when its smoke ran against the installed meshtasticd commit; none is a refusal naming
`--source binary`. An entry without a `stable`/`dev` channel, a valid version label (letters, digits
and `.@+_-`, at most 64 characters) or both 40-hex commits fails the web client's index read before
anything is downloaded. `build/tools/meshtastic-web/web.provenance` records the version, sha256,
selector and the meshtasticd commit it was paired with, and `lhpc status meshtastic` shows it:

- `binary` reads *current*, or *behind* after a pin move until `lhpc update meshtastic-web`;
- `stable`/`dev` read *differs* (not the known-working client); a pin move leaves them as they are;
- *unproven for this meshtasticd* once meshtasticd changed since the pairing (also in `lhpc doctor`).
  The binary install or update that moves meshtasticd re-pairs the kept choice in the same run;
  with no paired entry the installed client stays, named unproven;
- *not served* while an operator's own `web_root` makes meshtasticd serve another directory.

A switch is recorded only once it succeeded: a failed `--source binary` fetch leaves the installed
client and its record as they were. A saved `web_root` equal to the old managed root
(`build/tools/meshtasticd/web`, literal or rendered) serves from the new one at the next start; any
other saved value is the operator's.

## Verification status

`lhpc.core.provenance.evaluate()` reports one of:

- **`pinned-verified`**: `HEAD` is exactly the pin (or the planned) commit.
- **`signature-verified`**: pin verified **and** signed by a configured trusted signer.
- **`signature-unavailable`**: pin verified, signers configured, but no valid trusted signature: refused.
- **`mutable-dev` / `mutable-stable`**: explicit mutable selection (not production-safe).
- **`unverified-blocked`**: no pin, or `HEAD != pin`, and no explicit mutable choice.
- **`artifact-head`**: a declared artifact source.

## Signed commits/tags

Optional. lhpc runs `git verify-tag --raw` on the `pin_tag` when it is an annotated tag on the pinned
commit, else `git verify-commit --raw` on the pinned commit, and parses the GPG status; a
signature on anything but the pin never counts.
A signature counts **only** when git exits 0 **and** a `VALIDSIG` fingerprint matches a
configured trusted signer (full GPG fingerprints). Without configured signers the status stays
`pinned-verified`. With signers configured the signature is enforced: a source install or update whose
pin carries no trusted signature is refused before activation; installed sources keep running, and the
binary channel is not affected.

## Remote overrides

A per-component remote override (`[remotes]` in `local.toml`) must be `https://` or scp-style
`git@host:path`; it is validated before any Git use, and a non-string value is dropped at config
load. It changes only where the source comes from, never the pin/signature policy: a `pinned`
install of a fork also needs the pin moved to a commit of the fork, a manifest change
([moving a pin](maintenance.md#moving-a-pin)).
