# Maintaining LoRaHAM Pi Control

The entry point for anyone, human or agent, who maintains LHPC: the repositories, house rules,
release checklists, regular chores and incidents. Each step is one line and a link; when this file
and a linked document disagree, the linked document is right.

## Contents

- [Before you start](#before-you-start)
- [The repositories](#the-repositories)
- [House rules](#house-rules)
- [Releasing](#releasing)
- [Regular maintenance](#regular-maintenance)
- [When something breaks](#when-something-breaks)

## Before you start

- **Read the current state.** `git fetch` every repository you will touch and work from
  `origin/main` / `origin/dev`. The releases API, not a changelog, says what shipped.
- **Nothing reaches `main`, `dev` or a tag without the maintainer's word**, in every repository
  below; push only your topic branch. The one exception is the release bot's scheduled patch.
- **Commits carry no generated trailers**: no `Co-Authored-By`, no tool attribution
  ([CONTRIBUTING](CONTRIBUTING.md#commits)).
- **The reference box and the radios are shared.** Ask before using them, say when you are done,
  and leave the box as you found it. Box addresses, credentials and radio locations stay in the
  maintainer's private notes, never in a repository.
- **The maintainer audits every change before it lands**: one report per author (what was done,
  which behaviour changes, which decisions are needed) and the code on a branch in the maintainer's
  GitHub account. Verify each audit finding against the code before acting on it; an audit fixes
  bugs, it does not add architecture. **A green audit is not the maintainer's GO**: only their
  explicit word merges or releases.
- **One release at a time, the bot included.** While someone else or a bot run is releasing, don't
  push to the same repository.

## The repositories

| Repository | Its job in a release | Maintainer doc |
|---|---|---|
| [loraham-pi-control](https://github.com/makrohard/loraham-pi-control) | the controller; its manifest pins every source a box installs; the version everything is named after | [docs/maintenance.md](docs/maintenance.md), [docs/test-matrix.md](docs/test-matrix.md) |
| [lhpc-binaries](https://github.com/makrohard/lhpc-binaries) | prebuilt aarch64 builds of the long compiles (daemon, meshtastic, meshcom), built from a controller commit | [README](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#updating-a-binary) |
| [loraham-images](https://github.com/makrohard/loraham-images) | Raspberry Pi OS images with the released controller preinstalled, built on a `v*` tag and refreshed monthly | [docs/maintenance.md](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md) |
| [lhpc-release-bot](https://github.com/makrohard/lhpc-release-bot) | the weekly pin patch: watch upstream, repin, rebuild binaries, prove, release, image | [README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md) |
| [LoRaHAM_Daemon](https://github.com/makrohard/LoRaHAM_Daemon) | the radio daemon; its pins are moved by hand, never by the bot | [CONTRIBUTING](https://github.com/makrohard/LoRaHAM_Daemon/blob/main/CONTRIBUTING.md) |

One release: the controller release commit → the binaries its moved pins need, built from that
commit → the image tag with the same version. The Pages demo also publishes from this repository,
on every `main` push that touches `lhpc/`, `demo/` or `pyproject.toml`
([demo/README](demo/README.md#deploy)).

**Temporary forks** (TEMPORARY-PR: remove each item, and this paragraph with the last one, when its
upstream has taken the change) carry upstream fixes until upstream merges them. Nobody syncs them
by hand: a weekly workflow does, and an issue it opens is the only signal to act on.

- **MeshCom firmware:** back on upstream icssw-org `dev` since 2026-09-29. The fork `makrohard/MeshCom-Firmware` is
  retired after the repoint release, once no open upstream PR of ours has its head there.
- **QEMU.** `meshcom-qemu-raspi` builds Espressif's `esp-develop-9.2.2-20260417` tag plus the ESP32
  cache-model fix, checked in as a patch; the fix is espressif/qemu PR
  [#183](https://github.com/espressif/qemu/pull/183) (issue [#182](https://github.com/espressif/qemu/issues/182)), and `makrohard/qemu`
  only carries it for review. A weekly check comments on its tracking issue
  ([meshcom-qemu-raspi#1](https://github.com/makrohard/meshcom-qemu-raspi/issues/1)) when the pull
  request or an Espressif release changes; once a release contains the fix, build that plain tag,
  drop the patch and the check, and delete the `makrohard/qemu` fork.

**Waiting on others** (TEMPORARY-PR: remove a row when its upstream has acted, the section with
the last one). What LHPC carries or accepts meanwhile, and what retires it:

| Upstream item | Meanwhile | Retires when |
|---|---|---|
| MeshCom `src/loop_functions.cpp`: `extern TinyGPSPlus gps` is declared only for GPS boards | our overlay also declares it under `QEMU_HEADLESS` ([patch](https://github.com/makrohard/meshcom-qemu-raspi/blob/b53b230c54732b39ea2b41f820c0b7b83f5d8914/overlay/patches/meshcom-qemu-headless.patch#L347-L356)); not yet offered upstream | upstream declares it for every build: drop the hunk |
| [openhop-dev/openhop_core#156](https://github.com/openhop-dev/openhop_core/issues/156) (the login server logs the password) | accepted: the repeater log holds the admin password (maintainer, 2026-09-27) | upstream stops logging it |
| [openhop-dev/openhop_core#133](https://github.com/openhop-dev/openhop_core/pull/133) (companion radio stats: noise floor) | MeshCore clients read the noise floor as 0 dBm | merged: give `loraham_radio.py` the `get_cached_noise_floor()` it probes |
| [LoRaHAM/LoRaHAM_Daemon#10](https://github.com/LoRaHAM/LoRaHAM_Daemon/pull/10) (draft) | LHPC pins `makrohard/LoRaHAM_Daemon` | upstream takes it |
| [LoRaHAM/LoRaHAM_Voice#1](https://github.com/LoRaHAM/LoRaHAM_Voice/pull/1) (draft, the new daemon socket path) | LHPC pins `makrohard/LoRaHAM_Voice` | upstream takes it |
| [meshtastic/web#1428](https://github.com/meshtastic/web/pull/1428) (mobile layout) | the console's Meshtastic web client has no phone layout | released upstream: move the web client pin |

The bot watches the other pinned sources (the MeshCom bridge and QEMU scripts, the Reticulum
interface, the KISS TNC, Voice, the upstream projects); they need a maintainer only when the bot
holds or refuses one.

## House rules

- Branches, minor versus patch and the recorded exceptions:
  [branches and releases](docs/maintenance.md#branches-and-releases).
- The release-verification lane runs on the candidate commit **before** the tag, not after
  ([bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#running-it-pausing-it-retrying-it)).
- Every controller release is followed by an image with the **same version**
  ([images](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md#version-numbers)),
  tagged only after every binary a moved pin needs is published ([moving a pin](docs/maintenance.md#moving-a-pin)).
- A pinned commit is never amended or force-pushed ([maintenance](docs/maintenance.md#moving-a-pin)),
  and every pinned source has a rule in the bot's policy ([bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#what-it-moves)).
- A binary or image records what it was **built from**, never only what the manifest says; a
  label not checked against the bytes is not provenance.
- A build step that fetches a repository by ref names it as `{pin:<source path>}`, resolved from
  that source's pin; a commit literal in a build step fails
  [`tests/repo/test_build_steps_reference_pins.py`](tests/repo/test_build_steps_reference_pins.py).
- A binary is built from **the commit that gets tagged** (its `lhpc_commit`); an amend or squash
  after that means a rebuild, or the artifact points at a commit no branch holds.
- Evidence is the controller's typed outcome plus the stack's own state; log greps are not
  evidence ([test matrix](docs/test-matrix.md)).
- Every code change gets a live proof on real hardware where possible, besides CI; a test-only
  change says so.
- Docs state the current contract; history belongs in the changelog
  ([CONTRIBUTING](CONTRIBUTING.md#what-a-good-change-looks-like)).
- Floating dev tools are never re-pinned
  ([maintenance](docs/maintenance.md#policy)).

## Releasing

Before any release: every change has its audit, CI and live proof, and a **docs pass** has brought
the docs for everything the release changes up to date (truthful, only what a reader needs, no
history).

### Minor release (`0.X.0`)

Before step 1 (each line was missed or found late in a real run):
- From the box that builds the from-source rows, one PlatformIO package download succeeds (the
  registry answers 429 per public address when over its limit); if not, arrange another uplink.
- A pin move whose binary is unpublished does not land on `dev` alone; it arrives with the release
  commit's push, once the binary is published and testlab is green on the release commit.
- Every summary sentence about the release flow here and in docs/maintenance.md is checked against
  the steps below before the release commit goes to review.
- A changelog line about a moved source pin names the commands an installed box needs:
  `lhpc update <stack> --yes`, then `lhpc build <stack> --yes`.
- After a purge a Meshtastic node has a new key: remove the peer's old entry before the
  direct-message rows, and let the peer send one broadcast.
- One owner per band gives the go for every transmitting step of the matrix.
- A BLE peer's pairing is proven by one connection with the stored bond, not by the host's list of
  paired devices.
- The release bot's schedule is switched off for a manual release only on the maintainer's word,
  and back on after the image run has finished.

1. Run the bot in `watch-only` and move, or deliberately hold, every pin that has moved upstream
   ([maintenance](docs/maintenance.md#branches-and-releases)).
2. On a release branch from `dev`: ONE release commit on top of the cycle's commits (not
   squashed) sets the version and the changelog heading
   ([branches and releases](docs/maintenance.md#branches-and-releases)).
3. Local gate green ([CONTRIBUTING](CONTRIBUTING.md#what-should-be-green)); push the branch;
   dispatch CI and `testlab.yml` with `release_verify=true` on it
   ([testlab](docs/testlab.md#running-the-verification-lanes)).
4. Run the [release test matrix](docs/test-matrix.md) on the box into the run report. The release
   commit is not amended (binaries are built from it); the result goes into
   `docs/live-tests/live-test.md` in the first docs commit after the tag. The
   [fast lane](docs/test-matrix.md#fast-lane) needs the maintainer's explicit waiver, and every
   skipped row is written down.
5. CI and testlab green again on the **final** commit, with the binaries of step 8 published.
6. Fast-forward `dev` and `main` to the release commit and tag `v0.X.0` on it
   ([branches and releases](docs/maintenance.md#branches-and-releases) says when and how).
7. Publish the GitHub Release (same section: title, body, latest).
8. Before step 4: build every binary whose pin moved from the release commit (the matrix's binary
   rows, testlab and `release-verify` need it published;
   [binaries](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#updating-a-binary)).
9. Tag `loraham-images` `v0.X.0`: changelog entry, a commit named by the version, annotated tag;
   watch both variants to the end
   ([images](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md#routine-release)).
10. Move the reference box to `main`, delete the release and merged topic branches, and rebase any
    open topic branch onto the new `dev`.

### Maintainer patch (`0.X.Y`)

1. The fix lands on `dev`, one commit per change, with the version bump and its changelog
   section; a fix that moves a binary pin lands only once that binary is published (step 4).
2. CI and `testlab.yml` with `release_verify=true` green on the exact `dev` tip to be released.
3. Fast-forward `main` to it and tag it; no GitHub Release. If the bot has released in between, cut
   the patch on top of `main` instead ([branches and releases](docs/maintenance.md#branches-and-releases)).
4. Before step 2: binaries for any moved pin, built from the tip step 3 tags. After step 3: the
   image tag with the same version (steps 8 and 9 above).

### Bot patch

The bot releases pin moves from `main` and opens a pull request back into `dev`:
**squash-merge it** ([branches and releases](docs/maintenance.md#branches-and-releases)). Stages,
holds and recovery: its [README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md).
The maintainer reads its summary and closes what it leaves open.

### Daemon release

The bot never moves the daemon, the chat source (same repository) or RadioLib.

1. In `LoRaHAM_Daemon`: land on `dev`, all four CI lanes green, bump
   `loraham_daemon/daemon_version.h` and its changelog, fast-forward `main`, tag `vX.Y.Z`
   ([daemon CONTRIBUTING](https://github.com/makrohard/LoRaHAM_Daemon/blob/main/CONTRIBUTING.md#branches)).
   Its CI RadioLib (`.github/ci/radiolib.lock`) must be the RadioLib the controller pins.
2. In the controller: move `src/loraham-daemon` and `src/LoRaHAM_Daemon` to the same commit, and
   RadioLib if it moved, in one commit ([maintenance](docs/maintenance.md#moving-a-pin)).
3. Rebuild the daemon binary from that controller commit and prove it with the
   [release test matrix](docs/test-matrix.md) on the box (every radio stack runs on the daemon).
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
| CI red on `dev` or `main` | a gate that fetches at test time: re-run it; otherwise fix forward on `dev` | [maintenance](docs/maintenance.md#gates-that-still-fetch-at-test-time) |
| a released version is broken in the field | roll forward with a patch `x.y.z+1` and its image; release tags cannot be moved or deleted | [maintenance](docs/maintenance.md#branches-and-releases) |
| the bot failed or left an issue | `recover` if nothing was released, `finish` if the controller release exists; never edit its record by hand | [bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#when-something-is-left-behind) |
| the bot froze a stack, or an upstream breaks us | the freeze holds that pin; fix or hold it by hand, then thaw and close the incident together | [bot README](https://github.com/makrohard/lhpc-release-bot/blob/main/README.md#freeze-a-pin) |
| a binary build failed | nothing was published; fix and dispatch again | [binaries](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#updating-a-binary) |
| a bad binary was published | roll the index back; a box that already installed it goes back by installing from source | [binaries](https://github.com/makrohard/lhpc-binaries/blob/main/README.md#rolling-back), [operations](docs/operations.md#install-channels) |
| an image build failed | a stale binary index means: publish the binary first; otherwise repair with `publish_to_tag`, the tag stays | [images](https://github.com/makrohard/loraham-images/blob/main/docs/maintenance.md#routine-release) |
| a box does not come back on the network | the access-point fallback | [Wi-Fi](docs/wifi-access-point.md#troubleshooting) |
| a box's self-update failed | the recovery path | [deployment](docs/deployment.md#recovery) |
| a box needs rebuilding from nothing | the from-zero procedure | [test matrix](docs/test-matrix.md#from-zero-reinstall) |
