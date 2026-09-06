# LHPC maintenance

What CI enforces, what stays manual, the pin-bump recipe, and the local gotchas on a Pi. Open
work lives in [backlog.md](backlog.md); the per-release procedure in [test-matrix.md](test-matrix.md).

## Contents

- [What CI enforces](#what-ci-enforces)
- [What CI does not enforce](#what-ci-does-not-enforce)
- [Policy](#policy)
- [Moving a pin](#moving-a-pin)
- [Dependencies and platform](#dependencies-and-platform)
- [Security posture](#security-posture)
- [Running on a Pi](#running-on-a-pi)

## What CI enforces

Every push, Python 3.11/3.12/3.13, GitHub runners (`.github/workflows/ci.yml`):

- `compileall lhpc` + `bash -n install.sh uninstall.sh bootstrap-deps.sh`
- `ruff check lhpc` (the frozen ruleset) and `ruff check tests --select F,E9`
- `pytest -q` — the **whole** suite, but no coverage, no `-m` lane, not under `setsid`
- `bandit -q -r lhpc -lll` (high severity only) and `pip-audit . --strict` (dependency CVEs)
- a separate `pin-validation` job: **every pinned source is validated against its live branch**

## What CI does not enforce

- **Coverage.** No `--cov-fail-under` on purpose. If you touch `lhpc/`, run it and check the
  branch-inclusive total does not drop:
  `pytest -q -p no:cacheprovider --basetemp="$HOME/pt-lhpc" --cov=lhpc --cov-branch; rm -rf -- "$HOME/pt-lhpc"`
- **Contract lane.** `pytest -m contract` (~20 s) runs inside `pytest -q` but is not a separate
  gate; use it as a fast pre-flight.
- **Docs.** `cli.md` is test-enforced (a new CLI verb reddens `test_cli`), every Contents block
  and the docs index by `tests/test_docs_contents.py`; `adding-a-stack.md` must be updated by
  hand when the manifest/source model changes.
- Everything Pi-specific below only bites locally, never in CI.

## Policy

- **Freeze the config, float the tool.** Dev tools are unpinned (`pytest`, `ruff`, `bandit`,
  `pytest-cov`, `zstandard`). Ruff's *rules* are pinned in `[tool.ruff.lint] select`/`ignore`;
  bandit runs `-lll`. When a floated tool complains, fix the code or adjust the config **with a
  reason** — never re-pin the tool.
- **The contract is the map, the net is the protection.** `-m contract` states what LHPC
  promises; the full suite protects it. New capability → tag its widest-seam happy + boundary-
  refusal case `@pytest.mark.contract` (and `@pytest.mark.safety("id")` if it guards a safety
  invariant), keep `-m contract` green and < 30 s, and tag only isolation-robust cases.
- **Version bump** = `pyproject.toml` **and** `lhpc/version.py` (a test pins them equal and
  requires the matching `CHANGELOG.md` heading) + tag.

## Moving a pin

`lhpc/data/manifest.example.toml` pins every managed source (`pin_commit` + `pin_tag`); CI
validates each pin against its live branch on every push, and the `lhpc-binaries` builder
compiles **exactly the pin**, never "latest". Pin bumps are the **last** step of a release,
after the final source-repository batch is pushed and its commit is reachable from the
advertised branch. **Never amend or force-push a published commit referenced by a pin** — it
orphans the SHA and breaks fresh installs at checkout (`tests/test_pin_consistency.py` and the
CI job hard-fail on an orphaned or predating pin).

1. **Bump** `pin_commit`/`pin_tag`. Every component sharing that source gets the identical SHA
   (`tests/test_pin_consistency.py`; both meshcom-qemu-raspi consumers reference one full 40-hex
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
   smoke, then `lhpc known-working <stack>`. The full pass is the [release test matrix](test-matrix.md).

Watch upstream **build systems**, not just releases: meshtasticd and `qemu-system-xtensa` are
built from source, so a toolchain change upstream breaks the recipe silently. Builder internals:
[lhpc-binaries README](https://github.com/makrohard/lhpc-binaries#updating-a-binary).

## Dependencies and platform

- `pip-audit` red / new ruff or bandit finding → fix or justify-in-config (don't pin).
- Runtime deps are floors, not pins (`flask>=3,<4`, `werkzeug>=3.1`, `waitress>=3,<4`,
  `cryptography>=42`) — watch a breaking major (Werkzeug Host parsing, Flask 4).
- Python matrix 3.11–3.13 (`requires-python >= 3.11`): add 3.14 when it ships, drop 3.11 when
  no longer targeted.
- OS/kernel drift (Raspberry Pi OS Trixie): meshtasticd + qemu-from-source are the most fragile
  to toolchain bumps; a kernel change once flipped the `in0_input` voltage-file path.
- **PKI has no auto-renewal** — server/client certs default to 825 days; rotate before expiry
  on long-lived deployments ([webserver.md](webserver.md)).
- **Adding a third-party apt package** — audit before it reaches hardware:
  1. `sudo bash bootstrap-deps.sh --dry-run` on a fresh image: it simulates the exact default
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

- Managed firewall (nftables) fail-closed + receipt trust (owner identity, cgroup-leaf gating,
  `O_NOFOLLOW` receipt reads) — don't loosen ([firewall.md](firewall.md)).
- Exposure stays opt-in, loopback fail-safe, mTLS. `meshtasticd 4403/9443` is the one
  unconditional `0.0.0.0` exposure with no upstream knob — keep it firewall-contained.
- HMAC apply/abort/recover transactional; the token never leaks. `bandit -lll` + `pip-audit` are
  the automated floor. The guarantees themselves: [architecture.md](architecture.md).

## Running on a Pi

**The test suite.** Always give pytest a dedicated basetemp on the SD card and remove exactly
that path afterwards: `--basetemp="$HOME/pt-lhpc"` then `rm -rf -- "$HOME/pt-lhpc"`. The default
basetemp lands on the `/tmp` tmpfs (208 MB on a Zero 2W) and the full suite fills it (ENOSPC);
leaked basetemps accumulate under `/var/tmp` — list them first
(`find /var/tmp -maxdepth 1 -uid "$(id -u)" -type d -name 'lpt-*'`), review, then remove
explicitly, never a broad glob. Run under `setsid` or `needs_session` tests silently SKIP (you
lose boot-restore/ownership coverage); `zstd` must be installed or `requires_zstd` tests skip;
don't run as root or `needs_nonroot` tests skip. Serialize heavy jobs — one full-suite/coverage
run at a time (full `--cov` ~13 min, fast lane ~8 min on a Pi 5). **Stop any real daemon before a
full local run** (the hermeticity item in [backlog.md](backlog.md)).

**Memory on a 512 MB Zero 2W.** The three heavy stacks install from the binary channel by
default; everything below is about source builds and runtime load.

- The heavy builds are the from-source QEMU compile (~5 min on a Pi 5, ~68 min on a Zero 2W at
  `-j1`) and the MeshCom firmware (~26 min cold). The per-step build timeout defaults to 900 s;
  the manifest raises it per component (`build_timeout`, up to 28800 s for the Zero's cold QEMU
  compile) so a slow step is never silently TERM-killed. Builds are detached and survive a web-service restart; output is block-buffered off a TTY, so a
  quiet `tail -f` is not a stalled build — judge by CPU and the growing `.pio/build/`.
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
  `bootstrap-deps.sh` provisions a disk-backed swapfile (`/var/swap.lhpc`, default 768 MB,
  `--swap-size 64–16384`, `--no-swapfile` to opt out) at lower priority than zram, only when no
  sufficient disk swap exists and the filesystem has room. Success means active AND declared
  (one canonical `fstab` line), so a re-run repairs whichever half is missing; a non-regular file
  at the swap path or a symlinked `/etc/fstab` is refused untouched, and if swap is required but
  cannot be provisioned the bootstrap exits 4 after the apt/SPI/group work. It lives on the SD card.
- **Wi-Fi under sustained build load.** The Zero's brcmfmac firmware drops the interface until a
  reboot when power-save is on; `bootstrap-deps.sh` disables Wi-Fi power-save when the install
  runs over Wi-Fi (one NetworkManager drop-in; `--keep-wifi-powersave` opts out) and enables a
  persistent journal so a drop is captured. `lhpc build` is idempotent, so a drop mid-build costs
  a reconnect, not the build.
- **Recovering an interrupted `auto-install`.** `lhpc auto-install --status` prints the reason;
  `--recover` clears the reservation + lease + run marker in one action; `--confirm-orphan`
  acknowledges a child whose termination could not be proven (inspect `ps` first). Do not
  hand-edit the `state/auto-install*.json` markers.

**Job logs.** Build/host-test logs are `logs/build-<comp>.log` (single-step) or
`logs/build-<comp>-<N>.log` (multi-step); host tests `test-<comp>…`; run logs
`start-<comp>[-<band>].log`. `lhpc logs <comp>` resolves to the newest matching file, and each
job announces its exact path at start.
