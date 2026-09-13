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

- `compileall lhpc` + `bash -n install.sh uninstall.sh bootstrap-deps.sh` — `bootstrap-deps.sh`
  is the rendered `lhpc deps --script` snapshot shipped in the repo root for the pre-clone
  moment; regenerate it when dependencies change (CI shell-syntax-checks the committed copy)
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

### Gates that still fetch at test time

Some gates reach the network **while they run**, so a third-party outage reddens a release whose
diff could not have caused it. The Pyodide boot gate once did exactly that (pulling `micropip`
from jsDelivr mid-test); that fetch is fixed — `demo/vendor/` holds the `micropip` and
`packaging` wheels and `boot.mjs` passes `packageCacheDir`, checked before the CDN — and
acquisition steps across CI, testlab and Pages are retried. Three findings are deliberately left:

- **The demo's dependency closure.** `micropip.install()` defaults to `deps=True`, so the lhpc
  wheel's `flask`, `werkzeug` and `waitress` still resolve from PyPI, and `cryptography` and its
  own dependencies from jsDelivr (measured: with only the two wheels cached and the network off,
  the boot gate dies in `micropip.install` with *"Can't find a pure Python 3 wheel for:
  'waitress<4,>=3', 'flask<4,>=3', 'werkzeug>=3.1'"*). Closing it means vendoring about a dozen
  wheels, several of which must be the exact `cp312 pyodide_2024_0_wasm32` files named in
  `pyodide-lock.json`, re-pinned whenever Pyodide moves. What holds the line: the gate is not
  retried, so a failure is visible, and the deployed demo depends on the same CDN at run time.
- **The testlab acceptance clones.** The chain fixture installs kiss, and the graywolf, meshcore
  and meshcom cases install theirs, from live remotes with no transport retry inside the
  product's `_clone`. The fix is baking those checkouts into the devcontainer image and serving
  them through the `adopt_search_root` the lab already configures — not faking the sources,
  because `test_kiss_rx_and_tx_round_trip` asserts real AX.25 bytes off a real TNC. What holds
  the line: an image change plus a lab change, wanting its own round.
- **The deploy-script full-install tests.** The six `slow` tests in
  `tests/host/test_deploy_scripts.py` run `install.sh` as shipped — a venv plus `pip install -e`,
  roughly eighteen cold PyPI installs per CI run, since the test overrides `HOME` and the
  runner's pip cache is never exported. The fix is a session wheelhouse plus
  `PIP_NO_INDEX`/`PIP_FIND_LINKS` in the test's environment, leaving `install.sh` byte-identical;
  rewriting the script or adding `--system-site-packages` would let the identity assertion
  resolve `lhpc` from the outer CI install instead of the deployed checkout. What holds the line:
  the job already installs from PyPI in its own setup step.

Findings in `loraham-images`, `lhpc-binaries` and `lhpc-release-bot` are owned by those
repositories and recorded there, not here.

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

- **`main` is the latest release.** Every released tip carries a tag; `install.sh` clones it,
  `self-update` fast-forwards deployed boxes along it, the image builder reads it. It only ever
  fast-forwards and is never rewritten. Two paths advance it, and they are the two release paths
  below: a **minor** release and a **maintainer patch** both fast-forward it from `dev`; a **bot
  patch** fast-forwards it from a one-commit branch taken off `main` itself. None is a direct push
  of unproven work: each lands a commit whose checks are already green.
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
  stack added or removed. It runs the full [release test matrix](test-matrix.md) on the box. The
  cycle's commits are
  squashed into **one commit named by the version** (subject `<version>`, body = the changelog
  section), CI runs on that exact SHA, then `main` is fast-forwarded to it and `git tag -a
  v<version>` goes on it. That squash is the one moment `dev` is rewritten: after the tag `dev`
  equals `main`, and an open topic branch rebases onto it. A minor release also publishes a
  GitHub Release from the tag with the changelog section as its body (title = the version, not a
  pre-release, marked latest), linking the matching `loraham-images` release and the binaries
  index. Before starting one, run the release bot's `watch-only` and bump anything upstream has
  moved, so the matrix proves the pins the release ships.
- **A patch release (`0.X.Y`) is pins or a fix**, and it has **two producers with two different
  shapes** — the maintainer, and the release bot on its schedule.
  - **The maintainer's patch lands on `dev`** once it is release-ready, and `main` fast-forwards to
    the proven `dev` tip and is tagged there. Work that must not ship yet stays on a topic branch,
    never on `dev`. `main` stays an ancestor of `dev`, so the release is a fast-forward and no
    pull request is created.
  - **The bot keeps the `main`-based lane**, but only while `dev` has not diverged; the guard below
    defines that refusal.

  Either way a patch is the tag and its changelog section, with CI and testlab green on the exact
  released SHA; it publishes no GitHub Release, and boxes follow `main`.
  - **Where the line runs.** Adding, removing or changing a selector, a CLI verb, a refusal, a
    unit template or the manifest model is a minor. Changing a **default** — what happens when
    the operator names nothing — is a patch, provided every explicit selector keeps its meaning
    and the release lane proves every stack on the new default.
  - **One recorded exception (0.3.10):** Voice losing `artifact = true` shipped as a patch because
    the pin and the branch tip were the same commit and the release lane already proved both Voice
    variants — unchanged bytes plus lane proof, not a change to the selector rule.
  - The proof a patch needs is the proof its own change calls for. A **pin move** is proved by
    the binary builder's smoke and clean-runtime test plus the
    [release-verification lane](testlab.md#running-the-verification-lanes) — no box. That lane
    covers the stacks whose pins may move automatically; the daemon, RadioLib and the shared
    chat source are not among them, because the real daemon needs a radio to start. Anything
    that changes behaviour on hardware is proved on the box and recorded in the live-test
    report under `docs/live-tests/`.
  - **A bot patch requires an undiverged `dev`.** The bot releases only while `dev` has not moved
    past `main`; afterwards `dev` fast-forwards to the new `main` and the branches are equal again.
    If `dev` already carries unreleased commits the bot does **not** release: it reports and leaves
    both branches untouched, and the maintainer's lane — whose patch is on `dev` already — carries
    the fix instead. There is deliberately no reconciliation machinery: no back-merge pull request,
    no automated rebase, no force-push repair. A squash cannot restore ancestry (it copies the
    content without the commit) and a merge commit would break `dev`'s linear history, so the only
    sound answer is not to diverge in the first place.
- **Every release is followed by an image.** `loraham-images` is tagged with the same version
  once the binaries a moved pin needs are published, so the published image always carries the
  latest release ([binary channel](provenance.md#the-binary-channel)).
- **The release bot** performs the pin patch — watch, repin, binaries, proof, release, image —
  and opens an issue instead of releasing when anything is not green. What it moves, what it only
  reports, and how to pause, retry or recover it:
  [`lhpc-release-bot`](https://github.com/makrohard/lhpc-release-bot). When the [release lane](testlab.md#running-the-verification-lanes) blames one stack by name, the
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
move ([above](#branches-and-releases)).

**Graywolf is a fetched release, not a commit pin.** A version bump is the version in the
manifest build step and its `build_marker` name plus a new sha256 in the fetch script's table.

**Two pins are not commits.** The Meshtastic web client is named in the manifest by
meshtastic/web release version and by the sha256 of that release's `build.tar`; the CLI is a pip
version in a build step. The bot moves both. By hand: download the release's `build.tar`,
`sha256sum` it, put version and digest into the `meshtastic-web-assets.sh` step, and open the UI
through the proxy on the box before tagging.

Either move has two consequences that a commit pin does not, and both are mandatory:

- **Republish the meshtastic binary** (step 4). The artifact ships the client and the marker.
- **Move the matching `build_inputs` entry** on the meshtastic component. Each entry names the
  build step that CONSUMES it (`command = "pip"`, `command = "meshtastic-web-assets.sh"`) and the
  argv token it fills (`token = "meshtastic=={value}"`, or `"{value}"` for a bare version token);
  the loader requires that step to carry the rendered token exactly once, so moving one without
  the other refuses to load. The values are recorded beside the completion marker, in a file of
  their own, next to the built artifact — that is what makes an already-built box read *Build
  required* (a binary-channel box: reinstall the artifact). Beside, not inside: the marker's bytes
  are compared by every controller that ever shipped, and publish roots come from the installed
  manifest, so a member outside them is refused.

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
  1. `bash bootstrap-deps.sh --dry-run` on a fresh image ([deps](cli.md#deps)).
  2. Recommends are how a cascade arrives (`git` → `openssh-client` → `xauth` → `libX11`), so
     the install runs `--no-install-recommends`; a package that genuinely needs one lists it
     explicitly, with a comment saying why.
  3. Check what a package *links* (`readelf -d`, `ldd`) against what it *declares*
     (`apt-cache show`) — one overdeclared `libsdl2` dependency is a 99-package desktop cascade.
  4. Never installed, in any mode: a desktop environment, display manager, or X/Wayland server
     ([`--with-gui`](cli.md#deps)).

## Security posture

- The guarantees and where they are implemented: [architecture.md](architecture.md#safety-model);
  the firewall's own model: [firewall.md](firewall.md). Don't loosen either.
- The one unconditional `0.0.0.0` exposure and how it is contained:
  [what actually listens](firewall.md#what-actually-listens).
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

**Memory on a 512 MB Zero 2W.** The three heavy stacks default to the
[binary channel](provenance.md#the-binary-channel); everything below is about source builds and
runtime load.

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
  `bootstrap-deps.sh` provisions a disk-backed swapfile (`/var/swap.lhpc`, 768 MB by default)
  at lower priority than zram (`--no-swapfile`, `--swap-size`: [deps](cli.md#deps)), only when no
  sufficient disk swap exists and the filesystem has room.
  Success means active AND declared
  (one canonical `fstab` line), so a re-run repairs whichever half is missing; a non-regular file
  at the swap path or a symlinked `/etc/fstab` is refused untouched, and if swap is required but
  cannot be provisioned the bootstrap exits 4 after the apt/SPI/group work. It lives on the SD card.
- **Wi-Fi under sustained build load.** The Zero's brcmfmac firmware drops the interface until a
  reboot when power-save is on; `bootstrap-deps.sh` disables Wi-Fi power-save when the install
  runs over Wi-Fi and enables a persistent journal so a drop is captured. `lhpc build` is
  idempotent, so a drop mid-build costs a reconnect, not the build.
- **An interrupted `auto-install`** is recovered with `lhpc auto-install --status` / `--recover`
  ([cli.md](cli.md#auto-install)); never hand-edit the `state/auto-install*.json` markers.

**LHPC CI runs LHPC's tests. An upstream's own suite is a host test, not a gate.** A failure
there is information; no external project's suite is a required check. How to run one:
[test](cli.md#test).

**Job logs.** Build/host-test logs are `logs/build-<comp>.log` (single-step) or
`logs/build-<comp>-<N>.log` (multi-step); host tests `test-<comp>…`; run logs
`start-<comp>[-<band>].log`. `lhpc logs <comp>` resolves to the newest matching file, and each
job announces its exact path at start.

### RF logs

`logs/rf-*.log` is the family of per-stack RF logs — what a stack's radio heard and sent, one
line per frame, written by the stack's own process at its radio boundary and kept across
restarts (run logs are overwritten; these are not). The six user-facing logs, their config owner
and their writer are the registry in `lhpc/core/rflog.py`, which is the **only** authorization
for the viewer, the switcher and Clear: a job absent from it is never an RF log, whatever its
name. `rf_log` is a stack-level, band-less switch (`_BANDLESS_STACK_PARAMS`), read at the
writer's next start; graywolf proxies to the kiss TNC's switch.

Retention, per job: the writer copy-truncates at 5 MB into `<job>.1` — the inode never changes,
so Clear (truncate in place, delete `.1`) is safe under a running writer — which retains at most
~10 MB per job (the daemon: per band). The viewer tails `.1` + the live file as one. Registered RF
logs are outside the generic job-log pruning and its count/byte budget (they are meant to outlive
every job log), and a roll or Clear of one job holds a per-job lock in `state/locks/`, so two
console workers can never interleave them. The one
exception is **meshtastic**: its log is meshtasticd's own `TraceFile`, which the node only ever
appends to, so lhpc rolls it opportunistically — at stack start and when a page read finds it over
the cap, keeping the last ~5 MB in `.1`. That is not a hard maximum; an unattended node grows the
trace until the next start, read or Clear.

The console's viewer for these logs: [operations](operations.md#operating-the-console).

**Decrypt.** For the three stacks whose payloads are encrypted — meshtastic, meshcore,
reticulum — the log page carries a **Decrypt** toggle on its own row below the switcher, and the CLI
`lhpc rflog <stack> --decrypt [--follow]`. Both run a small decoder script (`lhpc/data/rfdecode/`)
under the *stack's own interpreter*, where its libraries and its keys already live: the managed
Meshtastic CLI venv reads `state/meshtasticd/prefs/channels.proto` (channel PSKs; LongFast's is
public) and decrypts channel traffic with the node's own AES-CTR, and opens public-key direct
messages to and from this node with its own key in `prefs/config.proto` and the peer's public key
in `prefs/nodes.proto` (X25519 → SHA-256 → AES-256-CCM, the firmware's nonce); openHop's venv
reads the identity seed in `config/secrets/` and the contacts and channel keys in
`state/meshcore/companion.db` (or the repeater's store, by `mode`; MeshCore's well-known Public
channel key is built in) and opens adverts, channel text and data, and every pairwise frame this
node is one end of — direct messages, requests, responses, path returns, anonymous requests
addressed to it (a login shows as a login, never its password); LXMF's venv (or, before `lxmd` is
built, the RNS venv) reads `state/reticulum/config` (the LoRa interface's IFAC) and MeshChat's
identity and ratchets in `state/meshchat/` and opens announces, path requests and single packets
to this box — link traffic and relayed packets are undecryptable by design and say so. The rule
on every stack: what this node's own secrets can open is opened; a frame between two other
nodes stays closed. A frame the keys cannot open is `no-key`, a
frame nobody could open `undecryptable`, a non-frame `malformed`; every row keeps its raw line.

Nothing leaves its place: no key is copied, passed on argv or in the environment (the decoder
opens the files itself, as `lhpc`; reading a SQLite store touches its `-wal`/`-shm` lock files,
nothing more), no decoded text is written anywhere (`logs/`, `state/`, the access log sees the
URL only; `Cache-Control: no-store`), and the console keeps at most 2000
decoded records per job in memory, gone with a restart (a frame no key opened is retried after
30 s, so a key that appears later is picked up without a restart). The decoder is bounded (10 s
wall clock, 1 MB of accepted output, one run at a time per job — concurrent polls share one run) and its protocol is checked line for line — N frames in, N results
out, keys matching — so a broken or half-built decoder is one typed error on the page and on the
terminal, never a traceback and never a gap. A stack that is not built has nothing to decode with,
and the page says so. Decoding other stations' channel traffic afterwards is the same act as the
node reading it live; a private message between two other stations stays closed.
