# LHPC maintenance

What CI enforces, branches and releases, the pin-bump recipe, and the gotchas on a Pi. Open work:
[backlog.md](backlog.md); the release-matrix procedure: [test-matrix.md](test-matrix.md).

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

`.github/workflows/ci.yml`, on pushes to `main` and `dev`, on pull requests and on dispatch:

- `test`, on Python 3.11/3.12/3.13:
  - `compileall lhpc` and `bash -n install.sh uninstall.sh bootstrap-deps.sh`. `bootstrap-deps.sh`
    is the committed `lhpc deps --script` snapshot for the pre-clone moment; the suite fails when it
    differs from the generator (`tests/install/test_bootstrap_deps.py`).
  - `ruff check lhpc testlab` (the frozen ruleset) and `ruff check tests --select F,E9`
  - `python -m pytest -q -rs --cov=lhpc --cov-branch`: the whole suite, no `-m` lane; branch
    coverage in the log, a `coverage.xml` artifact per Python version and the total in the job
    summary
  - `bandit -q -r lhpc -lll` (high severity) and `pip-audit . --strict`, also after a test failure
- `pin-validation`: every pinned source has a rule in the release bot's policy, and every pin is
  an ancestor of its live branch with its referenced scripts present
- `meshcore-host`: LHPC's own tests for `lhpc/data/meshcore_host` (not collected by `pytest -q`)
  and the MeshCore RF-log decoder, against the manifest-pinned openHop core installed unmodified
- `rfdecode-meshtastic`, `rfdecode-reticulum`: the other two RF-log decoders against the
  manifest-pinned `meshtastic` package and Reticulum/LXMF/driver checkouts

`.github/workflows/testlab.yml`, on the same events: the `testlab` job runs the lab lanes on an
aarch64 runner; `release-verify` installs, builds, starts and identity-proves every stack a pin
release may move, on pushes to `main` and on dispatch with `release_verify=true` only
([testlab.md](testlab.md#running-the-verification-lanes)); `slow-build`, on the same triggers,
is the slow-target build row below.

No external project's own test suite runs in CI.

## What CI does not enforce

- **A coverage threshold.** Coverage is published, not gated (no `--cov-fail-under`); a drop is
  judged in review. Touch `lhpc/` and run it locally ([tests/README.md](../tests/README.md#how-to-run)).
- **The contract lane** (`pytest -m contract`, ~20 s) runs inside the suite but is no separate gate;
  use it as a pre-flight.
- **Docs formatting.** Tests pin only a `### ` section in `cli.md` per CLI verb and the two
  READMEs' dependency list and hardware table (`tests/repo/test_readme_not_drifted.py`). Headings,
  Contents blocks and prose are kept by hand: `adding-a-stack.md` when the manifest model changes,
  the operator docs when behaviour changes.
- Nothing Pi-specific ([Running on a Pi](#running-on-a-pi)).

### Gates that still fetch at test time

These gates reach the network while they run, so a third-party outage can redden a release its
diff did not cause. (The Pyodide boot gate's own `micropip`/`packaging` come from `demo/vendor/`
via `packageCacheDir`; acquisition steps in CI, testlab and Pages are retried.) Left open:

- **The demo's dependency closure.** `micropip.install()` resolves the lhpc wheel's `flask`,
  `werkzeug` and `waitress` from PyPI and `cryptography` with its dependencies from jsDelivr.
  Closing it means vendoring about a dozen wheels, several the exact `cp312 pyodide_2024_0_wasm32`
  files in `pyodide-lock.json`, re-pinned with every Pyodide move. The gate is not retried, so a
  failure stays visible; the deployed demo uses the same CDN at run time.
- **The testlab acceptance clones.** The chain fixture's kiss and the graywolf, meshcore and
  meshcom cases clone from live remotes; the product's `_clone` has no transport retry. The fix is
  baking the checkouts into the devcontainer image and serving them through the lab's
  `adopt_search_root`, not faking sources (`test_kiss_rx_and_tx_round_trip` asserts real AX.25
  bytes off a real TNC): an image plus a lab change.
- **The deploy-script full-install tests.** The six `slow` tests in
  `tests/host/test_deploy_scripts.py` run `install.sh` as shipped, about eighteen cold PyPI
  installs per CI run (the test overrides `HOME`, so the runner's pip cache is unused). The fix is a
  session wheelhouse plus `PIP_NO_INDEX`/`PIP_FIND_LINKS` in the test's environment with
  `install.sh` unchanged; `--system-site-packages` would let the identity assertion resolve `lhpc`
  from the outer CI install.

Findings in `loraham-images`, `lhpc-binaries` and `lhpc-release-bot` are recorded in those
repositories.

## Policy

- **Freeze the config, float the tool.** Dev tools are unpinned (`pytest`, `ruff`, `bandit`,
  `pytest-cov`, `zstandard`); ruff's rules are pinned in `[tool.ruff.lint] select`/`ignore`,
  bandit runs `-lll`. When a tool complains, fix the code or change the config with a reason;
  never re-pin the tool.
- **`-m contract` states what LHPC promises; the full suite protects it.** What to tag:
  [tests/README.md](../tests/README.md#markers).
- **Version bump** = `pyproject.toml` **and** `lhpc/version.py` (a test pins them equal and
  requires the matching `CHANGELOG.md` heading) + tag.

## Branches and releases

- **`main` is the latest release.** Every released tip is tagged; `install.sh` clones it,
  `self-update` fast-forwards boxes along it, the image builder reads it. It only fast-forwards,
  and only to a commit whose checks are green, by one of the three paths below.
- **`dev` is the integration branch.** Every change lands as one complete commit, squash-merged
  from a topic branch rebased on `dev`; `dev` is not rewritten during a cycle. The reference box
  runs it (its self-update identity check reports `unsafe: checkout branch 'dev' != 'main'`:
  expected there, wrong on an operator's box).
- **During a minor's cycle `dev` carries the last released version**; changes go under a
  `## Unreleased` changelog heading; the release commit sets `pyproject.toml`,
  `lhpc/version.py` and the `CHANGELOG.md` heading to the new version.
- **A minor release (`0.X.0`)** comes from `dev` and carries a new capability or a changed
  contract: a CLI verb, a manifest model change, a unit template, a changed refusal, a stack added or removed. Before it,
  run the release bot's `watch-only` and move what upstream has moved, so the
  [release test matrix](test-matrix.md) proves the pins the release ships. The cycle's commits stay
  one per feature; **one release commit** (subject = the version, body = the changelog section)
  goes on top, and CI runs on that SHA. When `main` is an ancestor of `dev` (no bot release in the
  cycle), `dev` and `main` fast-forward to it and `git tag -a v<version>` goes on it with the same
  message; open topic branches rebase onto it. Otherwise the maintainer decides how, for that
  release. A minor also gets a GitHub Release from the tag: title = the version, body = the
  changelog section plus links to the matching `loraham-images` release and the binaries index,
  not a pre-release, marked latest.
- **A patch release (`0.X.Y`)** is pins or a fix, from one of two producers:
  - **The maintainer's patch** lands on `dev` when release-ready; `main` fast-forwards to the
    proven `dev` tip and is tagged there. Work that must not ship stays on a topic branch. If the
    bot has released since `dev` last equalled `main`, the patch is cut as one release commit on
    top of `main` instead, then CI, fast-forward `main`, tag.
  - **The bot's patch** is cut from `main` on its schedule, also while `dev` carries unreleased
    work; `dev` never gates it. The bot then opens a pull request bringing the release into `dev`:
    **squash-merge it** (`dev` is linear, a merge commit cannot land).

  A patch is the tag plus its changelog section, with CI and testlab green on the released SHA; no
  GitHub Release.
  - **Where the line runs.** Adding, removing or changing a selector, a CLI verb, a refusal, a
    unit template or the manifest model is a minor. Changing a **default** (what happens when the
    operator names nothing) is a patch if every explicit selector keeps its meaning and the
    release lane proves every stack on the new default.
  - **Recorded exceptions.** 0.3.10 (Voice) and 0.8.1 (chat) dropped `artifact = true` as a patch:
    the installed bytes were unchanged (pin and branch tip identical, or differing in docs only)
    and the release lane proved both variants; in 0.8.1 the flag had made the v0.8.0 image
    unbuildable. 0.6.2, by the maintainer's decision, narrowed `POWER` to the fitted chip (a
    changed refusal) inside the pin patch that moved the daemon to 1.0.0, because that daemon
    introduces the narrower range, so controller and daemon agree; a refusal change still needs a
    minor's justification.
  - **The proof a patch needs** is what its change calls for. A pin move: the binary builder's
    smoke and clean-runtime test plus the
    [release-verification lane](testlab.md#running-the-verification-lanes), no box. That lane
    does not cover the daemon, RadioLib or the shared chat source (the real daemon needs a radio).
    Anything that changes behaviour on hardware is proved on the box and recorded under
    `docs/live-tests/`.
- **Every release has one slow-target build row:** the `slow-build` job (row C,
  [testlab.md](testlab.md#running-the-verification-lanes)) runs on the release SHA of every
  release, minor, patch and bot alike, and is green with `test_slow_build_calibrated` and
  `test_slow_build_budget` PASSED (a skip is not a pass); its artifact is the evidence. A pin moved
  by a patch or bot release needs no Zero row: it is budgeted against the last Zero entry of that
  (component, op), carried from this or the previous minor, and a fresh row A becomes required only
  when row C measures the new pin above limit/4 (= 50 % of the budget) or that (component, op) has
  no Zero entry at all. A minor runs rows 6, 7, 8, the self-update and `calibrate.sh` on the Zero 2 W
  before the tag and commits the numbers ([test matrix](test-matrix.md#slow-target-baseline)),
  which refreshes the baselines and the calibration. Bootstrap: while
  `tests/data/slow-target-builds.toml` holds no measured entry at all (before the first row A),
  `test_coverage`, `test_slow_build_calibrated` and `test_slow_build_budget` SKIP with a
  "bootstrap: no row A yet" reason naming every unmeasured operation, and the `slow-build` gate
  accepts those skips only then; every row-C case must still pass. The first committed entry ends
  it: from then on every unmeasured operation is red. L4 (`selfupdate-pip`) has **no evidence on
  the release that introduces its `[selfupdate] pip sync` line — measured from the next release
  on**: that release's self-update runs the previous tag's helper, which cannot print the line,
  in row C and in row A alike. The lane detects it (the previous tag's
  `lhpc/core/service_selfupdate.py` lacks the line), still times the whole helper (L3), and names
  the gap in the bootstrap skip reason and the job summary; the gap is waived only in the
  bootstrap state, so the first row A is taken on the release after the introducing one.
- **Every release is followed by an image.** `loraham-images` is tagged with the same version once
  the binaries a moved pin needs are published ([binary channel](provenance.md#the-binary-channel)).
- **The release bot** runs the pin patch (watch, repin, binaries, proof, release, image) and opens
  an issue instead of releasing when anything is red. When the release lane blames one stack, the
  bot holds that stack's pins and retries once; the hold is lifted by hand. What it moves, pausing,
  retrying, recovery: [`lhpc-release-bot`](https://github.com/makrohard/lhpc-release-bot).
- **GitHub rulesets** (repository settings, not in the tree): `main`: no force push, no deletion,
  linear history, required checks `test (3.11)`, `test (3.12)`, `test (3.13)`, `pin-validation`,
  `testlab`, `meshcore-host` and `release-verify` on the pushed SHA (they ran on the release branch,
  so the fast-forward passes without a PR); `dev`: the same minus `pin-validation` and
  `release-verify`; `v*` tags: no deletion, no update. Pull requests: squash only, title and body
  become the commit, the branch is deleted on merge. The repository admin is the bypass actor of
  all three (every bypass logged).

## Moving a pin

`lhpc/data/manifest.example.toml` pins every managed source (`pin_commit` + `pin_tag`);
`lhpc-binaries` builds exactly the pin. Bump a pin last,
after the source repository's final batch is pushed and reachable from the advertised branch.
**Never amend or force-push a published commit a pin references**: it orphans the SHA and breaks
fresh installs (`tests/install/test_pin_consistency.py` and CI hard-fail on an orphaned or
predating pin).

1. **Bump** `pin_commit`/`pin_tag`. Every component sharing a source gets the identical SHA
   (`tests/install/test_pin_consistency.py`; both meshcom-qemu-raspi consumers use one full 40-hex
   SHA). meshcom: `apply-overlay.sh` must still apply (it fails closed).
2. **Validate locally**: the pin must be reachable on its declared branch.
   `python tools/manifest_pin.py --list` names the sources; CI's "Validate EVERY pinned source"
   step in `ci.yml` is the recipe.
3. **Commit + push**; note the SHA.
4. **Binary-covered stack** (`daemon`, `meshtastic`, `meshcom`): `lhpc-binaries` → Actions →
   **build-binary** → `stack`, `lhpc_ref = <that SHA>`, `source_commit` blank, `smoke_test = true`.
   The builder rejects a binary whose `components` differ from the manifest pins, so the pin lands
   first. The meshcom firmware is not bit-reproducible (a new sha per build is expected). Until
   the binary is published, installs of that stack refuse it and offer source.
5. **Images**: tag `loraham-images` only after every moved binary is published; a stale index
   fails the image build.
6. **On the box**: `lhpc install <stack> --source binary` (or `update` + `build`), smoke, then
   `lhpc known-working <stack>`; for a full pass, the [release test matrix](test-matrix.md).

The [release bot](https://github.com/makrohard/lhpc-release-bot) does steps 1–5 for the pins it
may move, with the release-verification lane in place of step 6.

**Graywolf is a fetched release, not a commit pin.** A bump changes the version in the manifest
build step and its `build_marker` name, plus a new sha256 in the fetch script's table.

**Two pins are not commits**, and the bot moves both: the Meshtastic web client (meshtastic/web
release version + sha256 of its `build.tar`) and the Meshtastic CLI (a pip version in a build
step). By hand: download the release's `build.tar`, `sha256sum` it, put version and digest into the
`meshtastic-web-assets.sh` step, and open the UI through the proxy on the box before tagging.
Either move also requires:

- **Republishing the meshtastic binary** (step 4); the artifact ships the client and the marker.
- **Moving the matching `build_inputs` entry** on the meshtastic component. Each entry names the
  build step that consumes it (`command = "pip"`, `command = "meshtastic-web-assets.sh"`) and the
  argv token it fills (`token = "meshtastic=={value}"`, or `"{value}"`); the loader requires
  exactly one such token in that command's steps, so moving one without the other fails to load.
  The values are recorded in a sidecar file beside the completion marker, so a built box reads
  *Build required* (binary channel: reinstall the artifact).

**Packaged assets in build steps are build inputs too.** Every `{asset}/…` token in a component's
`build_steps` is recorded in the same sidecar as `asset <rel> <sha256>`, so an lhpc update that
changes such an asset makes the component read *Build required* until rebuilt (binary channel:
reinstall). A `config_file.base`, `run` line or `post_steps` entry resolves the asset fresh and is
not a build input. An asset in a build step without a `build_marker` fails to load. A binary whose
sidecar predates these records reads *behind*, so a release that adds or changes recorded inputs
republishes every binary stack that consumes one (meshtastic, meshcom), not only those whose pins
moved.

Watch upstream **build systems**, not just releases: meshtasticd and `qemu-system-xtensa` are built
from source, and a toolchain change upstream breaks the recipe silently. Builder internals:
[lhpc-binaries README](https://github.com/makrohard/lhpc-binaries#updating-a-binary).

## Dependencies and platform

- `pip-audit` red, or a new ruff or bandit finding: fix, or justify in the config; don't pin.
- Runtime dependencies are ranges, not pins (`flask>=3,<4`, `werkzeug>=3.1`, `waitress>=3,<4`,
  `cryptography>=42`); watch for a breaking major (Werkzeug Host parsing, Flask 4).
- Python matrix 3.11–3.13 (`requires-python >= 3.11`): add 3.14 once the deployment image ships
  it, drop 3.11 when no longer targeted.
- OS/kernel drift (Raspberry Pi OS Trixie): meshtasticd and QEMU-from-source are the most fragile
  to toolchain bumps; a kernel change has moved the `in0_input` voltage-file path before.
- **PKI has no auto-renewal**: rotate before expiry ([lifetimes](webserver.md#certificates-and-the-two-ca-pki)).
- **Adding a third-party apt package**: audit it before it reaches hardware.
  1. `bash bootstrap-deps.sh --dry-run` on a fresh image ([deps](cli.md#deps)).
  2. The install runs `--no-install-recommends` (recommends bring cascades: `git` →
     `openssh-client` → `xauth` → `libX11`); a package that needs a recommend lists it explicitly,
     with a comment saying why.
  3. Compare what a package links (`readelf -d`, `ldd`) with what it declares (`apt-cache show`):
     one overdeclared `libsdl2` dependency is a 99-package desktop cascade.
  4. Never installed, in any mode: a desktop environment, display manager, or X/Wayland server
     ([`--with-gui`](cli.md#deps)).

## Security posture

- Don't loosen the [safety model](architecture.md#safety-model) or the [firewall's](firewall.md).
- The one unconditional `0.0.0.0` exposure and its containment:
  [what actually listens](firewall.md#what-actually-listens).
- HMAC enable/disable/renew are atomic and roll back on failure; the secret never reaches its
  marker, its log (redacted at read) or a result.
- `bandit -lll` + `pip-audit` are the automated floor; everything else is review.

## Running on a Pi

**Disk space.** `lhpc doctor`, `lhpc status`, a notice on every console page and one line in
`logs/lhpc-web.log` per change warn when `/` (and the runtime root's filesystem, if different) runs
short: `low` below max(1.5 GiB, 10 %) free or below 10 % free inodes, `critical` below
max(500 MiB, 5 %) or below 5 % free inodes; `critical` makes `lhpc doctor` non-OK. Free first: the
build trees of stacks installed from binaries, the PlatformIO caches, old job logs, and
`/var/cache/apt` (`sudo apt-get clean`). For scale: a from-zero reinstall on Raspberry Pi OS Lite
used 4.5 G of a 29 G root filesystem (2026-09-28).

**The test suite.** Give pytest a dedicated basetemp and remove exactly that path afterwards
(`pytest --basetemp="$HOME/pt-lhpc"`, then `rm -rf -- "$HOME/pt-lhpc"`): the default lands on the
`/tmp` tmpfs (208 MB on a Zero 2W), which the full suite fills (ENOSPC). Run under `setsid` or the
`needs_session` tests skip (boot-restore/ownership coverage); install `zstd` or the `requires_zstd`
tests skip; don't run as root or the `needs_nonroot` tests skip
([markers](../tests/README.md#markers)). One full-suite or coverage run at a time (full `--cov`
~13 min, the fast lane ~8 min on a Pi 5).

**Memory on a 512 MB Zero 2W.** The three heavy stacks default to the
[binary channel](provenance.md#the-binary-channel); the points below concern source builds and
runtime load.

- The heavy builds are the from-source QEMU compile (~5 min on a Pi 5, ~68 min on a Zero 2W at
  `-j1`) and the MeshCom firmware (~26 min cold). A build step is not ended for being slow. LHPC
  ends it when its processes show no CPU time, no output and no disk/network I/O for 10 minutes
  (`LHPC_BUILD_STALL_S`, a policy value: a wait that would have recovered later is ended too). It
  also ends a step after 24 h. That limit is a runaway guard for a step that loops forever, not a
  performance limit: no supported build comes near it. `LHPC_BUILD_STEP_TIMEOUT_S` replaces that
  guard when set. The same holds on the command line and the web Build button (host tests keep a
  plain timeout: 600 s, `test_timeout`). Builds are detached and survive a web-service restart.
  Output is block-buffered off a TTY, so a quiet `tail -f` is not a stalled build; judge by CPU and
  the growing `.pio/build/`:

  ```bash
  ps -eo pcpu,etime,cmd --sort=-pcpu | head -3          # is a compiler actually running?
  while sleep 60; do echo "$(date +%T) objs=$(find ~/loraham-pi-control/src -path '*/.pio/build/*' -name '*.o' | wc -l)"; done
  ```
- **Stop the web stack for heavy builds** (`systemctl --user stop lhpc-web lhpc-nginx`): the
  controller competing with a compile for RAM triggers the OOM killer. Build children are biased
  toward the OOM killer so the controller survives (`core/build_launcher_runtime.py`), and the
  QEMU build uses `-j min(nproc, floor(MemTotal_GB))` (`-j1` on 512 MB), but freeing RAM still makes
  the build faster and safer.
- **Runtime has the same ceiling.** meshtasticd + the emulated MeshCom node + nginx + the console
  drive a Zero into swap thrash; only a power cycle recovers it. Run
  MeshCom **or** Meshtastic on a Zero, not both, and stop the console while the QEMU node boots. A
  Pi 5 has no such limit.
- **Disk swapfile.** Trixie's default swap is zram (compressed pages still in RAM), so a build can
  still be OOM-killed at `-j1`. When `MemTotal < ~600 MB`, no sufficient disk swap exists and the
  filesystem has room, `bootstrap-deps.sh` provisions `/var/swap.lhpc` (768 MB by default, on the
  SD card) at lower priority than zram (`--no-swapfile`, `--swap-size`: [deps](cli.md#deps)).
  Success means active and declared (one `fstab` line), so a re-run repairs a missing half; a
  non-regular file at the swap path or a symlinked `/etc/fstab` is refused untouched; swap required
  but not provisionable exits 4 after the apt/SPI/group work.
- **Wi-Fi under build load.** With power-save on, the Zero's brcmfmac firmware drops the interface
  until a reboot; `bootstrap-deps.sh` disables Wi-Fi power-save when the install runs over Wi-Fi.
  `lhpc build` is idempotent, so a drop mid-build costs a reconnect, not the build.
- **The journal is volatile.** Raspberry Pi OS ships
  `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` (`Storage=volatile`), so a reboot
  loses the previous boot's journal; LHPC leaves that alone. To keep it on a box being debugged, add
  a drop-in that sorts after it, capped because the journal then writes to the SD card:
  ```sh
  sudo mkdir -p /etc/systemd/journald.conf.d
  printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/90-persistent.conf
  sudo systemctl restart systemd-journald
  ```
  Remove the file and restart journald to go back.
- **An interrupted `auto-install`** is recovered with `lhpc auto-install --status` / `--recover`
  ([cli.md](cli.md#auto-install)); never hand-edit the `state/auto-install*.json` markers.

**An upstream's own test suite is a host test, not a gate**: a failure there is information. How
to run one: [test](cli.md#test).

**Job logs.** Build and host-test logs are `logs/build-<comp>.log` (single step) or
`logs/build-<comp>-<N>.log` (multi-step), host tests `test-<comp>…`; run logs are
`start-<comp>[-<band>].log`. `lhpc logs <comp>` resolves to the newest matching file; each job
announces its path at start. A job log is recreated per run; a run log is appended to by every
start. When a build or console job runs, the pruner deletes the oldest counted logs while they
exceed 200 files or 64 MiB. It neither deletes nor counts: the registered RF logs, the controller's
own logs, an active job's log, the log of a failed, unsafe or incomplete job whose result is still
shown, and the logs of a live auto-install run or a live or unsafe HMAC-apply run; `logs/` as a
whole can exceed those figures.

**Run-log cap.** A run log is checked when its component starts and at each console maintenance
pass (every 60 s on a box with the access point or while a web-server Apply or PKI normalisation is
pending, else every 300 s). Above 8 MiB its last 1 MiB, from just after the first newline in it, is
written to `start-<comp>[-<band>].prev.log` (replacing the previous one) and the run log is
emptied; the running process keeps writing to it. A window that starts on a line start loses that
line; a last line without a newline is kept; a 1 MiB window without a newline keeps nothing. Lines
written during the cut can be lost. A failed cut (a full disk, say) leaves the run log unchanged,
and at a start LHPC appends one line saying the cut was skipped; a log another cut holds is left
for the next check. The 8 MiB is a trigger, not a maximum: a log grows by what is written between
checks, and without a running console only the cut at start applies. RF logs and job logs are not
cut this way. The cut relies on the process writing through LHPC's append-mode descriptor; a
custom launcher that re-opens its run log without append mode writes at its old offset, so the
file grows holes and is cut again later.

**The controller's own logs** are checked the same way at each console pass (not at a start):
`lhpc-web.log`, `lhpc-selfupdate.log`, `lhpc-boot-restore.log`, `lhpc-nginx-restart.log` (written by
their units) and `nginx-error.log`, `nginx-access.log`. Their older part is `<name>.prev.log`; with
the console stopped nothing cuts them.

### RF logs

`logs/rf-*.log` are the per-stack RF logs: what a stack's radio heard and sent, one line per frame,
written by the stack's own process at its radio boundary, kept across restarts. The registry in
`lhpc/core/rflog.py` (six stacks) is the **only** authorization for the log page, its rows,
switches and Clear: a file absent from it is never an RF log, whatever its name. `rf_log` is a
stack-level, band-less switch (`_BANDLESS_STACK_PARAMS`) in the owner's Settings (group
*RF-Logs*; the daemon has no Settings form, its switch is the log page or the CLI), read at the
writer's next start; graywolf uses the kiss TNC's switch.

Retention, per job: the writer copy-truncates at 5 MB into `<job>.1` (the inode never changes, so
Clear — truncate in place, delete `.1` — is safe under a running writer), keeping at most ~10 MB
per job (the daemon: per band). The viewer reads `.1` + the live file as one. RF logs are outside
the job-log pruning; a roll or Clear holds a per-job lock in `state/locks/`. **Meshtastic** is the
exception: its log is meshtasticd's own `TraceFile`, which the node only appends to, so lhpc rolls
it when over the cap (keeping the last ~5 MB in `.1`) at stack start, when a page read finds it
over, and at each console pass; a failed roll is retried at the next check. Between checks it grows
by what the node writes.

**The log page** (`/logs/<writer>?job=<file>`) serves every RF log. Each dashboard radio card links
it under the daemon control: the band's daemon log, then *RF log:* the daemon's file for that band
and the file of each running registered stack. Under the header, a band row lists every band; a
stack row lists the shown band's RF logs (the daemon's file, then every stack whose components
declare that band, in registry order). The shown band is the `band` query arg, else the file's own
band (daemon), else the band the stack runs on. The table shows one row per frame, oldest first;
a header tap sorts, a second reverses. Columns are per browser (default *dir*, *ascii*,
*decoded*); *Raw* shows the file as the CLI prints it; a row tap expands its payload. Rows come from
`GET /api/rflog/<writer>?job=…` (parsed by `rflog.parse_line`; an unknown line is still a row with
its raw text). Column and sort choices live in the browser's `localStorage`
(`lhpc.rflog2.<file>`); Decrypt is never remembered. At the bottom, in this order: **Stack RF
Logging** (saves the shown stack's switch on its config owner, the key Settings saves; "restart
required" while the writer runs), **Clear stack RF log** (empties the file, removes its `.1`),
**Global RF Logging** (every stack's switch; reads *on*, *off* or *mixed*), **Clear all RF logs**
(every registered file, each under its own lock; run logs untouched). CLI:
[`lhpc rflog`](cli.md#rflog).

**Decrypt.** For meshtastic, meshcore and reticulum the log page has a **Decrypt** toggle below the
stack row; the CLI has `lhpc rflog <stack> --decrypt [--follow]`. Both run a decoder
(`lhpc/data/rfdecode/`) under the stack's own interpreter, where its libraries and keys are:

- **meshtastic** (the managed CLI venv): channel traffic with the PSKs in
  `state/meshtasticd/prefs/channels.proto` (LongFast's is public) and the node's AES-CTR; public-key
  direct messages to and from this node with its key in `prefs/config.proto` and the peer's in
  `prefs/nodes.proto` (X25519 → SHA-256 → AES-256-CCM, the firmware's nonce).
- **meshcore** (openHop's venv): the identity seed in `config/secrets/`, contacts and channel keys
  in `state/meshcore/companion.db` (or the repeater's store, by `mode`; the Public channel key is
  built in). Opens adverts, channel text and data, and every pairwise frame this node is one end of
  (direct messages, requests, responses, path returns, anonymous requests to it; a login shows as a
  login, never its password).
- **reticulum** (LXMF's venv, or the RNS venv before `lxmd` is built): the LoRa interface's IFAC in
  `state/reticulum/config` and MeshChat's identity and ratchets in `state/meshchat/`. Opens
  announces, path requests and single packets to this box; link traffic and relayed packets are
  undecryptable and say so.

What this node's own secrets can open is opened; a frame between two other nodes stays closed. A
frame the keys cannot open is `no-key`, one nobody could open `undecryptable`, a non-frame
`malformed`; every row keeps its raw line.

No key is copied or passed on argv or in the environment (the decoder opens the files itself, as
`lhpc`; reading a SQLite store touches its `-wal`/`-shm` files). No decoded text is written
(`logs/`, `state/`; the access log sees the URL only; `Cache-Control: no-store`). The console keeps
at most 2000 decoded records per job in memory, gone with a restart; a `no-key` frame is retried
after 30 s, so a key that appears later is picked up. The decoder is bounded (10 s wall clock,
1 MB of accepted output, one run at a time per job; concurrent polls share it) and its protocol is
checked line for line (N frames in, N results out, keys matching), so a broken decoder is one typed
error on the page and the terminal; so is an unbuilt stack.
