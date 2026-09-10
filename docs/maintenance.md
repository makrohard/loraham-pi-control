# LHPC maintenance

What CI enforces, what stays manual, the pin-bump recipe, and the local gotchas on a Pi. Open
work lives in [backlog.md](backlog.md); the release-matrix procedure in
[test-matrix.md](test-matrix.md).

## Contents

- [What CI enforces](#what-ci-enforces)
- [What CI does not enforce](#what-ci-does-not-enforce)
- [Policy](#policy)
- [Branches and releases](#branches-and-releases)
- [Moving a pin](#moving-a-pin)
- [Dependencies and platform](#dependencies-and-platform)
- [Security posture](#security-posture)
- [Running on a Pi](#running-on-a-pi)

## What CI enforces

On pushes to `main` and `dev`, on pull requests and on manual dispatch — Python 3.11/3.12/3.13, GitHub runners (`.github/workflows/ci.yml`):

- `compileall lhpc` + `bash -n install.sh uninstall.sh bootstrap-deps.sh`
- `ruff check lhpc testlab` (the frozen ruleset) and `ruff check tests --select F,E9`
- `pytest -q --cov=lhpc --cov-branch` — the **whole** suite with branch coverage measured and published (terminal summary, `coverage.xml` artifact per Python version, the total in the job summary); no `-m` lane, not under `setsid`
- `bandit -q -r lhpc -lll` (high severity only) and `pip-audit . --strict` (dependency CVEs)
- a separate `pin-validation` job: **every pinned source is validated against its live branch**
- `testlab.yml`, on the same branches and on pull requests: the console lane on an aarch64 runner
  ([testlab.md](testlab.md#running-the-verification-lanes)). Its second job, `release-verify`,
  installs, builds, starts and identity-proves every stack a pin release may move; it runs on
  pushes to `main` and on dispatch with `release_verify=true` — not on `dev` and not on a pull
  request, because it installs and builds everything
- a separate `meshcore-host` job: **LHPC's own** tests for `lhpc/data/meshcore_host`, which ships
  inside `lhpc/data/` so `pytest -q` does not collect it. Most of them exercise LHPC behaviour
  against the external API, so the manifest-pinned openHop core is installed as a dependency,
  patched as a box builds it. No external project's own suite runs in CI

## What CI does not enforce

- **A coverage threshold.** CI measures and publishes branch coverage but does not gate on it — no
  `--cov-fail-under` on purpose: a drop is judged in review. Touch `lhpc/` and you run it locally
  (the command: [tests/README.md](../tests/README.md); why it needs its own basetemp:
  [Running on a Pi](#running-on-a-pi)).
- **Contract lane.** `pytest -m contract` (~20 s) runs inside `pytest -q` but is not a separate
  gate; use it as a fast pre-flight.
- **Docs formatting.** The suite pins documentation only where the words are a contract: a `### `
  section in `cli.md` per CLI verb, and the two READMEs' dependency list and hardware table against
  the generators (`tests/repo/test_readme_not_drifted.py`). Headings, Contents blocks and prose are
  not tested — `adding-a-stack.md` when the manifest model changes, the operator docs when
  behaviour changes, all by hand.
- Everything Pi-specific below only bites locally, never in CI.

## Policy

- **Freeze the config, float the tool.** Dev tools are unpinned (`pytest`, `ruff`, `bandit`,
  `pytest-cov`, `zstandard`). Ruff's *rules* are pinned in `[tool.ruff.lint] select`/`ignore`;
  bandit runs `-lll`. When a floated tool complains, fix the code or adjust the config **with a
  reason** — never re-pin the tool.
- **The contract is the map, the net is the protection.** `-m contract` states what LHPC
  promises; the full suite protects it. What a new capability tags, and how:
  [CONTRIBUTING.md](../CONTRIBUTING.md) and [tests/README.md](../tests/README.md).
- **Version bump** = `pyproject.toml` **and** `lhpc/version.py` (a test pins them equal and
  requires the matching `CHANGELOG.md` heading) + tag.

## Branches and releases

- **`main` is the latest release.** Every commit on it carries a tag; `install.sh` clones it,
  `self-update` fast-forwards deployed boxes along it, the image builder reads it. It only ever
  fast-forwards and is never rewritten. Two paths advance it, and they are the two release paths
  below: a **minor** release fast-forwards it from `dev`, and a **patch** release fast-forwards
  it from a one-commit branch taken off `main` itself. Neither is a direct push of unproven
  work: both land a commit whose checks are already green.
- **`dev` is the integration branch.** All work lands there, one complete commit per change,
  from a topic branch rebased on `dev` and squash-merged; CI and testlab run on every push; the
  reference box runs it for testing (its self-update identity check reports `unsafe: checkout
  branch 'dev' != 'main'` — expected on a test box, wrong on an operator's box). `dev` is not
  rewritten during a cycle (contributors branch from it); the release squash below is the one
  exception, announced by the tag.
- **A cycle starts with the version bump** (`pyproject.toml`, `lhpc/version.py`, the
  `CHANGELOG.md` heading), so a `dev` deployment never reports the released number.
- **A minor release (`0.X.0`) is a feature release.** It comes from `dev`: a new capability or a
  changed contract — a CLI verb, a manifest model change, a unit template, a changed refusal, a
  stack added or removed. It runs the full [release test matrix](test-matrix.md) on the box, and
  its result replaces the section in [live-test.md](live-test.md). The cycle's commits are
  squashed into **one commit named by the version** (subject `<version>`, body = the changelog
  section), CI runs on that exact SHA, then `main` is fast-forwarded to it and `git tag -a
  v<version>` goes on it. That squash is the one moment `dev` is rewritten: after the tag `dev`
  equals `main`, and an open topic branch rebases onto it. A minor release also publishes a
  GitHub Release from the tag with the changelog section as its body (title = the version, not a
  pre-release, marked latest), linking the matching `loraham-images` release and the binaries
  index. Before starting one, run the release bot's `watch-only` and bump anything upstream has
  moved, so the matrix proves the pins the release ships.
- **A patch release (`0.X.Y`) is pins or a fix**, and it has **two producers** — the maintainer,
  and the release bot on its schedule. Both take the same shape: branch from `main` (never from
  `dev`, so nothing unreleased rides along), one commit named by the version, CI and testlab
  green on that SHA, fast-forward `main`, tag. A patch is the tag and its changelog section; it
  publishes no GitHub Release. Boxes follow `main` either way.
  - **Where the line runs.** Adding, removing or changing a selector, a CLI verb, a refusal, a
    unit template or the manifest model is a minor. Changing a **default** — what happens when
    the operator names nothing — is a patch, provided every explicit selector keeps its meaning
    and the release lane proves every stack on the new default.
  - **One recorded exception (0.3.10).** Voice losing `artifact = true` changed what every
    selector resolves to for that one source, which the line above calls a minor. It shipped as a
    patch by maintainer decision: the pin and the branch tip were the same commit, so no box
    installed anything different, and the release lane already proves both Voice variants through
    the path the change introduces. The exception is the unchanged bytes plus lane proof, not the
    selector rule itself.
  - The proof a patch needs is the proof its own change calls for. A **pin move** is proved by
    the binary builder's smoke and clean-runtime test plus the
    [release-verification lane](testlab.md#running-the-verification-lanes) — no box. That lane
    covers the stacks whose pins may move automatically; the daemon, RadioLib and the shared
    chat source are not among them, because the real daemon needs a radio to start. Anything
    that changes behaviour on hardware is proved on the box and recorded in
    [live-test.md](live-test.md).
  - **Bringing the patch back to `dev`:** fast-forward `dev` when it still equals the old `main`;
    otherwise open a pull request. `dev` is linear and never rewritten outside a minor release,
    so a patch is never merged into it.
- **Every release is followed by an image.** `loraham-images` is tagged with the same version
  once the binaries a moved pin needs are published, so the published image always carries the
  latest release ([binary channel](provenance.md#the-binary-channel)).
- **The release bot** performs the pin patch — watch, repin, binaries, proof, release, image —
  and opens an issue instead of releasing when anything is not green. What it moves, what it only
  reports, and how to pause, retry or recover it:
  [`lhpc-release-bot`](https://github.com/makrohard/lhpc-release-bot). It never moves the daemon,
  RadioLib or the shared chat source: those need the radio hardware the lane does not have.
  When the [release lane](testlab.md#running-the-verification-lanes) blames one stack by name, the
  bot holds that stack's pins itself and retries once, so the others keep releasing; the hold is
  lifted by hand, and the bot's own README owns that procedure.
- **GitHub rulesets** (repository settings, not in the tree): `main` — no force push, no
  deletion, linear history, required checks `test (3.11)`, `test (3.12)`, `test (3.13)`,
  `pin-validation`, `testlab`, `meshcore-host` and `release-verify` on the pushed SHA (they ran
  on the release branch, so the fast-forward passes without a PR); `dev` — the same minus
  `pin-validation` and `release-verify`; `v*` tags — no deletion, no
  update. Pull requests: squash merging only, the PR title and body become the commit, the topic
  branch is deleted on merge. The repository admin is the bypass actor of all three rulesets
  (always allowed, every bypass logged), so the maintainer's own pushes and hotfixes are never
  blocked while contributors meet the checks by construction.

## Moving a pin

`lhpc/data/manifest.example.toml` pins every managed source (`pin_commit` + `pin_tag`); CI
validates each pin against its live branch on every push, and the `lhpc-binaries` builder
compiles **exactly the pin**, never "latest". Pin bumps are the **last** step of a release,
after the final source-repository batch is pushed and its commit is reachable from the
advertised branch. **Never amend or force-push a published commit referenced by a pin** — it
orphans the SHA and breaks fresh installs at checkout (`tests/install/test_pin_consistency.py` and the
CI job hard-fail on an orphaned or predating pin).

1. **Bump** `pin_commit`/`pin_tag`. Every component sharing that source gets the identical SHA
   (`tests/install/test_pin_consistency.py`; both meshcom-qemu-raspi consumers reference one full 40-hex
   SHA); meshcom: `apply-overlay.sh` must still apply (it fails closed).
2. **Validate locally** before pushing — the pin must be reachable on its declared branch:
   `python tools/manifest_pin.py --list` names the sources; CI's job (`ci.yml`, "Validate EVERY
   pinned source") is the reference recipe.
3. **Commit + push**; note the SHA.
4. **Binary-covered stack** (`daemon`, `meshtastic`, `meshcom`): `lhpc-binaries` → Actions →
   **build-binary** → `stack`, `lhpc_ref = <that SHA>`, `source_commit` blank, `smoke_test = true`.
   The pins-must-match gate rejects a binary whose `components` ≠ the manifest pins, so the pin
   lands FIRST; the meshcom firmware is not bit-reproducible (a new sha per build is expected).
   Until the binary is published, installs of that stack refuse the binary and offer source.
5. **Images**: tag `loraham-images` only after every moved binary is published — a stale index
   blocks the binary stacks and the image build dies.
6. **On the box**: `lhpc install <stack> --source binary` (or `update` + `build` from source),
   smoke, then `lhpc known-working <stack>`. When the release runs the full pass, it is the
   [release test matrix](test-matrix.md).

Steps 1–5 are what the [release bot](https://github.com/makrohard/lhpc-release-bot) does on its
schedule, with the release-verification lane in place of step 6 — for the pins it is allowed to
move. The daemon, RadioLib and the shared chat source it only reports; those move by hand,
through the recipe above.

**Two pins are not commits.** The Meshtastic web client is named in the manifest by
meshtastic/web release version and by the sha256 of that release's `build.tar`; the CLI is a pip
version in a build step. The bot moves both. By hand: download the release's `build.tar`,
`sha256sum` it, put version and digest into the `meshtastic-web-assets.sh` step, and open the UI
through the proxy on the box before tagging.

Either move has two consequences that a commit pin does not, and both are mandatory:

- **Republish the meshtastic binary** (step 4). The artifact ships the client and the marker.
- **Move the matching `build_inputs` entry** on the meshtastic component. Each entry names the
  build step that CONSUMES it (`command = "pip"`, `command = "meshtastic-web-assets.sh"`) and the
  argv token it fills (`token = "meshtastic=={value}"`, or `"{value}"` for a bare version token),
  and the loader requires that step to carry the rendered token exactly once — so moving one
  without the other refuses to load, and a matching token in some other command does not count. Those values are recorded BESIDE the completion marker,
  in a file of its own, which is what makes an already-built box read *Build required* — without
  them the checkout never moves, so the box kept serving the OLD client and still called itself
  built, and `lhpc status` called the artifact current. Beside and not inside: the marker's
  content is compared byte for byte by every controller that ever shipped, so recording them in
  it would make a republished artifact read *not built* on every box that had not upgraded yet,
  and there would be no safe order for a release at all. It sits beside the BUILT ARTIFACT rather
  than beside the source marker for a second reason: publish roots come from the installed
  manifest, never from the artifact, so a member outside them is refused — a candidate that
  widens its own roots proves nothing about released boxes, and an artifact shipped that way was
  refused on all of them while installing perfectly in the lane. A box on the binary channel is told to
  reinstall the artifact rather than to build, because `lhpc build` is refused there.

Watch upstream **build systems**, not just releases: meshtasticd and `qemu-system-xtensa` are
built from source, so a toolchain change upstream breaks the recipe silently. Builder internals:
[lhpc-binaries README](https://github.com/makrohard/lhpc-binaries#updating-a-binary).

## Dependencies and platform

- `pip-audit` red / new ruff or bandit finding → fix or justify-in-config (don't pin).
- Runtime deps are floors, not pins (`flask>=3,<4`, `werkzeug>=3.1`, `waitress>=3,<4`,
  `cryptography>=42`) — watch a breaking major (Werkzeug Host parsing, Flask 4).
- Python matrix 3.11–3.13 (`requires-python >= 3.11`): add 3.14 once the deployment image ships it, drop 3.11 when
  no longer targeted.
- OS/kernel drift (Raspberry Pi OS Trixie): meshtasticd + qemu-from-source are the most fragile
  to toolchain bumps; a kernel change once flipped the `in0_input` voltage-file path.
- **PKI has no auto-renewal** — server/client certs default to 825 days; rotate before expiry
  on long-lived deployments ([webserver.md](webserver.md)).
- **Adding a third-party apt package** — audit before it reaches hardware:
  1. `bash bootstrap-deps.sh --dry-run` on a fresh image (no root needed): it simulates the exact default
     apt transaction (`apt-get install -s --no-install-recommends`), changes nothing, and exits
     nonzero if the set cannot be resolved or would pull anything graphical/audio.
  2. Recommends are how a cascade arrives (`git` → `openssh-client` → `xauth` → `libX11`), so
     the install runs `--no-install-recommends`; a package that genuinely needs one lists it
     explicitly, with a comment saying why.
  3. Check what a package *links* (`readelf -d`, `ldd`) against what it *declares*
     (`apt-cache show`) — one overdeclared `libsdl2` dependency is a 99-package desktop cascade.
  4. Never installed, in any mode: a desktop environment, display manager, or X/Wayland server;
     `--with-gui` installs GUI application libraries only.

## Security posture

- The guarantees and where they are implemented: [architecture.md](architecture.md#safety-model);
  the firewall's own model: [firewall.md](firewall.md). Don't loosen either.
- `meshtasticd 4403/9443` is the one unconditional `0.0.0.0` exposure with no upstream knob —
  keep it firewall-contained.
- HMAC apply/abort/recover is transactional and the token never leaks.
- `bandit -lll` + `pip-audit` are the automated floor; everything else is review.

## Running on a Pi

**The test suite.** Give pytest a dedicated basetemp and remove exactly that path afterwards
: `pytest --basetemp="$HOME/pt-lhpc"` then `rm -rf -- "$HOME/pt-lhpc"` — the default basetemp
lands on the `/tmp` tmpfs (208 MB on a Zero 2W) and the full suite fills it (ENOSPC). Run under
`setsid` or `needs_session` tests silently SKIP (you lose boot-restore/ownership coverage);
`zstd` must be installed or `requires_zstd` tests skip; don't run as root or `needs_nonroot`
tests skip — the markers themselves: [tests/README.md](../tests/README.md). Serialize heavy jobs — one full-suite/coverage run at a
time (full `--cov` ~13 min, the suite's fast lane ~8 min on a Pi 5).

**Memory on a 512 MB Zero 2W.** The three heavy stacks install from the binary channel by
default; everything below is about source builds and runtime load.

- The heavy builds are the from-source QEMU compile (~5 min on a Pi 5, ~68 min on a Zero 2W at
  `-j1`) and the MeshCom firmware (~26 min cold). The per-step build timeout defaults to 900 s;
  the manifest raises it per component (`build_timeout`, up to 28800 s for the Zero's cold QEMU
  compile) so a slow step is never silently TERM-killed. Builds are detached and survive a web-service restart; output is block-buffered off a TTY, so a
  quiet `tail -f` is not a stalled build — judge by CPU and the growing `.pio/build/`:

  ```bash
  ps -eo pcpu,etime,cmd --sort=-pcpu | head -3          # is a compiler actually running?
  while sleep 60; do echo "$(date +%T) objs=$(find ~/loraham-pi-control/src -path '*/.pio/build/*' -name '*.o' | wc -l)"; done
  ```
- **Stop the web stack for heavy builds** (`systemctl --user stop lhpc-web lhpc-nginx`): the
  controller and a parallel compile competing for RAM is what triggers the OOM killer. lhpc
  biases build children toward the OOM killer so the controller survives
  (`core/build_launcher_runtime.py`), and the QEMU build uses a memory-aware `-j`
  (`min(nproc, floor(MemTotal_GB))` → `-j1` on 512 MB), but freeing RAM still makes the build
  faster and safer.
- **Runtime concurrency has the same ceiling.** meshtasticd + the emulated MeshCom node + nginx
  + the console together drive a Zero into swap thrash — Wi-Fi drops first, then SSH, and only a
  power cycle recovers it. Run MeshCom **or** Meshtastic on a Zero, not both, and stop the
  console while the QEMU node boots. A Pi 5 has no such limit.
- **Disk swapfile as OOM insurance.** Trixie's default swap is zram (compressed pages still in
  RAM), so a build can still be OOM-killed at `-j1`. When `MemTotal < ~600 MB`,
  `bootstrap-deps.sh` provisions a disk-backed swapfile at lower priority than zram (its flags
  are in the README), only when no sufficient disk swap exists and the filesystem has room.
  Success means active AND declared
  (one canonical `fstab` line), so a re-run repairs whichever half is missing; a non-regular file
  at the swap path or a symlinked `/etc/fstab` is refused untouched, and if swap is required but
  cannot be provisioned the bootstrap exits 4 after the apt/SPI/group work. It lives on the SD card.
- **Wi-Fi under sustained build load.** The Zero's brcmfmac firmware drops the interface until a
  reboot when power-save is on; `bootstrap-deps.sh` disables Wi-Fi power-save when the install
  runs over Wi-Fi and enables a persistent journal so a drop is captured. `lhpc build` is
  idempotent, so a drop mid-build costs a reconnect, not the build.
- **An interrupted `auto-install`** is recovered with `lhpc auto-install --status` / `--recover`
  ([cli.md](cli.md)); never hand-edit the `state/auto-install*.json` markers.

**LHPC CI runs LHPC's tests. An upstream's own suite is a host test, not a gate.** Where a
component declares one — openHop Core does — it is reachable exactly like any other host test: the
button on the stack's install section, `lhpc test <component>`, or the tests checkbox in
auto-install. It runs in the environment the build created, against the pinned upstream that box
installed, so it tells the operator whether the pinned upstream itself works on that hardware. A
failure there is information; no external project's suite is a required check.

**Job logs.** Build/host-test logs are `logs/build-<comp>.log` (single-step) or
`logs/build-<comp>-<N>.log` (multi-step); host tests `test-<comp>…`; run logs
`start-<comp>[-<band>].log`. `lhpc logs <comp>` resolves to the newest matching file, and each
job announces its exact path at start.
