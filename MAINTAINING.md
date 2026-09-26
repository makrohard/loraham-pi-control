# Maintaining LoRaHAM Pi Control

The entry point for anyone, human or agent, who maintains LHPC: which repositories make up a
release, the house rules, the release checklists, the regular chores, and what to do when
something breaks. Every step here is one line and a link. The detail lives in the repository that
owns it, and this file never repeats it: when the two disagree, the linked document is right and
this file is the bug.

## Contents

- [Before you start](#before-you-start)
- [The repositories](#the-repositories)
- [House rules](#house-rules)
- [Releasing](#releasing)
- [Regular maintenance](#regular-maintenance)
- [When something breaks](#when-something-breaks)

## Before you start

- **Read the current state, not your memory of it.** `git fetch` every repository you will touch
  and work from `origin/main` / `origin/dev`. The releases API, not a changelog, says what shipped.
- **Nothing reaches `main`, `dev` or a tag without the maintainer's word.** Work on a topic branch
  and push only that branch until the maintainer says so. This holds in every repository below;
  the one standing exception is the release bot's own scheduled patch.
- **Commits carry no generated trailers**: no `Co-Authored-By`, no tool attribution
  ([CONTRIBUTING](CONTRIBUTING.md#commits)).
- **The reference box and the radios are shared.** Ask before using them, say when you are done,
  and leave the box as you found it. Box addresses, credentials and which radios are where are
  kept in the maintainer's private notes, never in a repository.
- **The maintainer audits every change before it lands.** An audit reads one report per author
  (what was done, which behaviour changes, which decisions are needed) and the code on a branch in
  the maintainer's own GitHub account. Verify every audit finding against the code before acting on
  it; the audit is there to fix bugs, not to add architecture. **An audit that comes back green is not
  the maintainer's GO**: only their explicit word merges or releases.
- **One person or agent runs a release at a time, the bot included.** If someone else is
  releasing, or a bot run is in progress, wait for its end before pushing to the same repository.

## The repositories

| Repository | Its job in a release | Maintainer doc |
|---|---|---|
| [loraham-pi-control](https://github.com/makrohard/loraham-pi-control) | the controller; its manifest pins every source a box installs; the version everything is named after | [docs/maintenance.md](docs/maintenance.md), [docs/test-matrix.md](docs/test-matrix.md) |
| [lhpc-binaries](https://github.com/makrohard/lhpc-binaries) | prebuilt aarch64 builds of the long compiles (daemon, meshtastic, meshcom), built from a controller commit | [README](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#updating-a-binary) |
| [loraham-images](https://github.com/makrohard/loraham-images) | Raspberry Pi OS images with the released controller preinstalled, built on a `v*` tag and refreshed monthly | [docs/maintenance.md](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md) |
| [lhpc-release-bot](https://github.com/makrohard/lhpc-release-bot) | the weekly pin patch: watch upstream, repin, rebuild binaries, prove, release, image | [README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md) |
| [LoRaHAM_Daemon](https://github.com/makrohard/LoRaHAM_Daemon) | the radio daemon; its pins are moved by hand, never by the bot | [CONTRIBUTING](https://github.com/makrohard/LoRaHAM_Daemon/blob/main/CONTRIBUTING.md) |

The flow of one release: a controller commit on `main` → the binaries its moved pins need, built
from that commit → the image tag with the same version. One more surface publishes from this
repository: the Pages demo, redeployed on every `main` push that touches `lhpc/` or `demo/`
([demo/README](demo/README.md#deploy)).

**Temporary forks** (TEMPORARY-PR: remove each item, and this paragraph with the last one, when
its upstream has taken the change). Two upstream fixes reach boxes through our own copies until
upstream merges them. Nobody syncs them by hand: a weekly workflow does, and an issue it opens is the
only signal to act on.

- **MeshCom firmware.** LHPC builds branch `lhpc-speed` of `makrohard/MeshCom-Firmware`: upstream
  `dev` plus the speed fixes meant for icssw-org. Its weekly workflow (`lhpc-speed.yml`) merges
  upstream `dev` in, builds the boards and runs the QEMU proof; green moves the branch forward, red
  opens an issue and moves nothing. It merges rather than rebases, because the bot follows
  `lhpc-speed` at its tip and a rebased branch no longer contains the pinned commit. When upstream
  contains the fixes, the workflow says so on the retire issue
  ([makrohard/MeshCom-Firmware#1](https://github.com/makrohard/MeshCom-Firmware/issues/1)): pin
  upstream again in the manifest, both the MeshCom-Firmware remote **and** the `--src` of the QEMU
  setup step, point the bot's policy back, delete the workflow and the branch.
- **QEMU.** `meshcom-qemu-raspi` builds Espressif's `esp-develop-9.2.2-20260417` tag plus the ESP32
  cache-model fix, checked in as a patch; the fix is espressif/qemu PR #183, and `makrohard/qemu`
  only carries it for review. A weekly check comments on its tracking issue
  ([meshcom-qemu-raspi#1](https://github.com/makrohard/meshcom-qemu-raspi/issues/1)) when the pull
  request or an Espressif release changes; once a release contains the fix, build that plain tag,
  drop the patch and the check, and delete the `makrohard/qemu` fork.

The other sources the manifest pins (the MeshCom bridge and QEMU scripts, the Reticulum
interface, the KISS TNC, Voice, and the upstream projects) are watched by the bot. They need a
maintainer only when the bot holds or refuses one of them.

## House rules

Each rule is defined where the link points.

- `main` is the latest release and only fast-forwards; `dev` is linear integration, one complete
  commit per change, never rewritten except by a minor's release squash
  ([maintenance](docs/maintenance.md#branches-and-releases)).
- A cycle starts with the version bump: `pyproject.toml`, `lhpc/version.py`, the `CHANGELOG.md`
  heading ([maintenance](docs/maintenance.md#branches-and-releases)).
- A new capability, a changed contract, default or refusal is a **minor**; pins or a fix is a
  **patch**. Recorded exceptions are listed in the same section
  ([maintenance](docs/maintenance.md#branches-and-releases)).
- The release-verification lane runs on the candidate commit **before** the tag, not after
  ([bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#running-it-pausing-it-retrying-it)).
- Every controller release is followed by an image with the **same version**
  ([images](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md#version-numbers)), and every binary a moved pin needs is published **before** that
  image tag ([maintenance](docs/maintenance.md#moving-a-pin)).
- A pinned commit is never amended or force-pushed ([maintenance](docs/maintenance.md#moving-a-pin)),
  and every pinned source has a rule in the bot's policy ([bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#what-it-moves)).
- A binary or image records what it was **built from**, never only what the manifest says it
  should be. A label that is not checked against the bytes is not provenance.
- A build step that fetches a repository by ref names it as `{pin:<source path>}`, resolved from
  that source's pin; a commit literal in a build step fails
  [`tests/repo/test_build_steps_reference_pins.py`](tests/repo/test_build_steps_reference_pins.py).
- A binary is built from **the commit that gets tagged**: its recorded `lhpc_commit` is the
  release commit. Anything that changes that commit afterwards, an amend or a squash, means a
  rebuild, or the artifact points at a commit no branch holds.
- Evidence is the controller's typed outcome plus the stack's own state; log greps are not
  evidence ([test matrix](docs/test-matrix.md)).
- Every code change gets a live proof on real hardware wherever one is possible, in addition
  to CI; a change that only touches tests says so instead.
- Docs state the current contract; history belongs in the changelog
  ([CONTRIBUTING](CONTRIBUTING.md#what-a-good-change-looks-like)).
- Floating dev tools are never re-pinned
  ([maintenance](docs/maintenance.md#policy)).

## Releasing

Before any release: every change has its audit, its CI and its live proof, and a **docs pass** over
everything the release changes has brought the docs up to date — truthful, only what a reader needs,
no history (that belongs in the changelog).

### Minor release (`0.X.0`)

1. Run the bot in `watch-only` and move, or deliberately hold, every pin that has moved upstream
   ([maintenance](docs/maintenance.md#branches-and-releases)).
2. On a release branch from `dev`: make sure the version scalars and the changelog heading carry
   the new version, and squash the cycle into one commit whose subject is the version and whose
   body is the changelog section.
3. Local gate green ([CONTRIBUTING](CONTRIBUTING.md#what-should-be-green)); push the branch;
   dispatch CI and `testlab.yml` with `release_verify=true` on it
   ([testlab](docs/testlab.md#running-the-verification-lanes)).
4. Run the release test matrix on the box and write the result into
   `docs/live-tests/live-test.md`, amending the release commit ([test matrix](docs/test-matrix.md)).
   The [fast lane](docs/test-matrix.md#fast-lane) needs the maintainer's explicit waiver, and every
   skipped row is written down.
5. CI and testlab green again on the **final** commit.
6. Push the release commit to `dev` (the one allowed rewrite; the `dev` ruleset blocks it for
   everyone but the maintainer's bypass), fast-forward `main`, and put an annotated tag `v0.X.0`
   on it with the same message.
7. Publish the GitHub Release: title = version, body = the changelog section plus links to the
   image release and the binary index, marked latest.
8. Build every binary whose pin moved from the release commit
   ([binaries](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#updating-a-binary)).
9. Tag `loraham-images` `v0.X.0`: changelog entry, a commit named by the version, annotated tag;
   watch both variants to the end
   ([images](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md#routine-release)).
10. Move the reference box to `main`, delete the release and merged topic branches, and rebase any
    open topic branch onto the new `dev`.

### Maintainer patch (`0.X.Y`)

1. The fix lands on `dev`, one commit per change, with the version bump and its changelog
   section ([maintenance](docs/maintenance.md#branches-and-releases)).
2. CI and `testlab.yml` with `release_verify=true` green on the exact `dev` tip to be released.
3. Fast-forward `main` to it and put the annotated tag on it. No GitHub Release. If the bot has
   released since `dev` last equalled `main`, `main` is no longer an ancestor of `dev`: cut the patch
   as one release commit on top of `main` instead, then CI, fast-forward `main` and tag.
4. Binaries for any moved pin, built from the tagged commit, then the image tag with the same
   version (steps 8 and 9 above).

### Bot patch

The bot releases pin moves from `main` on its schedule, also while `dev` carries unreleased work;
`dev` never gates, delays or shapes a bot release. It then opens a pull request that brings the
release back into `dev`: **squash-merge it**, `dev` keeps a linear history
([maintenance](docs/maintenance.md#branches-and-releases)). Its stages, holds and recovery are in
its [README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md). A maintainer's
part is reading its summary and closing what it leaves open.

### Daemon release

The bot never moves the daemon, the chat source (same repository) or RadioLib.

1. In `LoRaHAM_Daemon`: land on `dev`, all four CI lanes green, bump
   `loraham_daemon/daemon_version.h` and its changelog, fast-forward `main`, tag `vX.Y.Z`
   ([daemon CONTRIBUTING](https://github.com/makrohard/LoRaHAM_Daemon/blob/main/CONTRIBUTING.md#branches)).
   Its CI RadioLib (`.github/ci/radiolib.lock`) must be the RadioLib the controller pins.
2. In the controller: move `src/loraham-daemon` and `src/LoRaHAM_Daemon` to the same commit, and
   RadioLib if it moved, in one commit ([maintenance](docs/maintenance.md#moving-a-pin)).
3. Rebuild the daemon binary from that controller commit, then prove it on the box: the release
   test matrix is the proof for these pins, since every radio stack runs on the daemon
   ([test matrix](docs/test-matrix.md)).
4. Release it as a patch or a minor by the usual rule, followed by the image.

## Regular maintenance

| When | What | Where |
|---|---|---|
| Mondays after 21:30 UTC, when the schedule is enabled | read the bot's run summary; no `attempt` or `auto-freeze` issue left open without a reason | [bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#when-something-is-left-behind) |
| the 1st of each month | the images' OS refresh ran and published or said why not | [images](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md#monthly-os-refresh-dated-releases) |
| after ~60 days without repository activity | GitHub disables idle schedules: a manual dispatch re-enables the bot's and the images' | same two links |
| when a fork's weekly workflow opens or comments on an issue (TEMPORARY-PR: remove this row with the forks) | red: fix the carry or the patch for the new upstream, re-run the workflow; retire: undo the fork as its issue lists | the issue; [Temporary forks](#the-repositories) |
| before each minor | the bot's `watch-only`; the list of held pins and why each is still held | [bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#freeze-a-pin) |
| as due | dependency audit findings, Python versions, OS drift, PKI expiry, upstream toolchains | [maintenance](docs/maintenance.md#dependencies-and-platform) |
| when a new console feature ships | the demo simulates it too, or the demo shows a dead page | [demo/README](demo/README.md) |

## When something breaks

| Symptom | First step | Detail |
|---|---|---|
| CI red on `dev` or `main` | if it is one of the gates the linked section names as fetching from the network, re-run it; otherwise fix forward on `dev` | [maintenance](docs/maintenance.md#gates-that-still-fetch-at-test-time) |
| a released version is broken in the field | roll forward with a patch `x.y.z+1` and its image; release tags cannot be moved or deleted | [maintenance](docs/maintenance.md#branches-and-releases) |
| the bot failed or left an issue | `recover` if nothing was released, `finish` if the controller release exists; never edit its record by hand | [bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#when-something-is-left-behind) |
| the bot froze a stack, or an upstream breaks us | the freeze holds that pin; fix or hold it by hand, then thaw and close the incident together | [bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#freeze-a-pin) |
| a binary build failed | nothing was published; fix and dispatch again | [binaries](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#updating-a-binary) |
| a bad binary was published | roll the index back; a box that already installed it goes back by installing from source | [binaries](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#rolling-back), [operations](docs/operations.md#install-channels) |
| an image build failed | a stale binary index means: publish the binary first; otherwise repair with `publish_to_tag`, the tag stays | [images](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md#routine-release) |
| a box does not come back on the network | the access-point fallback | [Wi-Fi](docs/wifi-access-point.md#troubleshooting) |
| a box's self-update failed | the recovery path | [deployment](docs/deployment.md#recovery) |
| a box needs rebuilding from nothing | the from-zero procedure | [test matrix](docs/test-matrix.md#from-zero-reinstall) |
