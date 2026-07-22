# Field notes — fresh install & operation on Pi Zero 2W and Pi 5

Field-verified notes from from-zero installs on a Pi Zero 2 W (512 MB, quad A53) and a Pi 5
(reference rig). Both hardware classes run the **same Trixie arm64 packages** — nothing here is
hardware-conditional except the SPI overlay choice and the meshtasticd `gpiochip` (both noted below).

## First-boot / Raspberry Pi Imager

- Set the username, Wi-Fi, and SSH in Imager's OS-customisation before flashing.
- **Known gotcha:** Imager's first-boot customisation can *silently* fail to apply on some images. If
  the Pi comes up with no Wi-Fi or no user, at the console: set the Wi-Fi country with
  `sudo rfkill unblock wifi` + `raspi-config` (Localisation → WLAN country), and create the user
  manually. Then re-run the network config. This is an OS-imager issue, not lhpc.
- After flashing: run [`bootstrap-deps.sh`](../bootstrap-deps.sh) (or `lhpc deps --script`), reboot,
  then install lhpc and run auto-install.

## Fresh-install checklist

1. Flash Trixie arm64 (Imager settings incl. user / Wi-Fi / SSH; see the gotcha above).
2. `sudo bash bootstrap-deps.sh --spi-mode soft-cs` → `sudo reboot`. On a **lite (headless)** image
   this is all you need: GUI-only dependencies are omitted by default, so a headless rig never pulls
   the GTK/X11/Wayland dev chain. Add `--with-gui` only on a machine that already has a display —
   without it, the Voice stack is reported *skipped* and MeshCore runs via its CLI (the optional
   Node Manager GUI is skipped). **`--spi-mode` is required:**
   - `soft-cs` — LoRaHAM Pi / Uputronics rigs, **single-radio AND dual Uputronics** (software CS,
     `/dev/spidev0.0`; daemon + meshtasticd drive CS7/CS8 as GPIOs — field-verified on the dual rig).
   - `hardware-cs` — only for boards that really use kernel-driven CE0/CE1; SPI on, no overlay.
     **NOT for Uputronics** (kernel-claimed CE0/CE1 collides with the daemon's GPIO chip-selects).
   - `skip` — SPI already configured.
   Group grants (`spi`/`gpio`) go to the operator — `--operator-user <name>` if you run it as root,
   else `$SUDO_USER`/the invoking user; **never root**. The SPI write is idempotent and fails closed on
   a conflicting existing `config.txt`. (Both take effect on the reboot.)
3. Install lhpc (`install.sh`), then `lhpc auto-install`.
4. Bring up the web console (see `docs/webserver.md`).
5. **Set your callsign.** `mc_callsign` is an operator parameter applied over the MeshCom net-console
   after the node boots. A fresh install with it unset runs as the placeholder **XX0XX** — set it in
   the stack parameters (or `lhpc config meshcom-qemu mc_callsign <CALL-SSID>`) as part of first start.

## Build durations & memory pressure (512 MB Zero 2 W)

- The heavy builds are the from-source QEMU compile (~5 min on a Pi 5, ~68 min on a Zero 2W at `-j1`) and
  the MeshCom firmware (**~26 min cold**, ~2–3 min incremental); RadioLib + daemon and the Python-venv
  stacks are minutes each. The per-step build timeout is **28800 s** (8 h), sized for the Zero's cold QEMU
  compile so a slow step is never silently TERM-killed. A completion marker co-located with `flash.bin`
  (`.pio/build/<env>/.lhpc-build-complete`) is the authoritative built-state signal — a stale `flash.bin`
  from an earlier build never reads "built" after a failed/interrupted rebuild, and cleaning the firmware
  removes the marker with it.
- **Stop the web stack for heavy builds** on a 512 MB board — the controller and a `-j`-parallel
  compile competing for RAM is what triggers the OOM killer. lhpc already biases build children toward
  the OOM killer so the controller survives, and the QEMU build defaults to a memory-aware `-j`
  (`min(nproc, floor(MemTotal_GB))` → `-j1` on 512 MB, full parallelism on a Pi 5), but freeing RAM
  still makes the build faster and safer.
- **Disk swapfile as OOM insurance (small-RAM boards).** Trixie's default swap is **zram** —
  *compressed pages that still live in RAM*, so it adds no real backing store. A firmware build can
  still be OOM-killed with zram present (field-observed: cc1plus killed at `-j1` with 414 MiB of zram,
  only 78 MiB in use, while the web stack was resident). To prevent the *hard* OOM, `bootstrap-deps.sh`
  provisions a **disk-backed** swapfile (`/var/swap.lhpc`, default 768 MB) when `MemTotal < ~600 MB`,
  at a **lower priority than zram** — zram stays the fast tier and the file is overflow that only backs
  the peak. It is created only when there is no sufficient *other* disk swap (zram is *not* counted,
  and neither is our own file) **and** the target filesystem has enough free space (2× the target for a
  fresh image, 1× to rebuild an existing one) — else it refuses rather than fill the card.
  **Success means ACTIVE *and* DECLARED:** the image is built in a same-directory temp, fsynced and
  renamed into place (an interrupted run leaves an inert temp, never a half-formatted swapfile), and
  the `fstab` entry is published transactionally so there is always **exactly one** canonical line.
  A re-run therefore *repairs* whichever half is missing (active-but-undeclared, or declared-but-off)
  instead of merely no-oping. A non-regular file at the swap path — symlink, directory, FIFO, device —
  is **refused untouched**, as is a symlinked `/etc/fstab` (the script is privileged; following a link
  would let it overwrite an arbitrary target). **On a low-RAM host where swap is required but cannot be
  provisioned the bootstrap now exits 4** — the apt/SPI/group work still completes first, so you get a
  configured machine *and* an unambiguous failure. **Trade-off:** the swapfile lives on the SD card, so
  heavy paging adds flash wear — the cost of build reliability on a 512 MB box. Opt out with
  `--no-swapfile` (the only supported way to proceed without it), or size it with
  `--swap-size <MB>` (64–16384); on a Pi 5 or any board with ≥ 600 MB RAM it is never created. A build
  that pages heavily every time is a signal to stop the web stack (above) or move to more RAM.
- Builds are detached: they survive a web-service restart. Every job prints a copy-pasteable
  `tail -f <log>` line the moment its log is created — follow the exact file from another terminal
  instead of guessing (`lhpc logs <comp>` resolves to the same newest file). Build output is
  **block-buffered off a TTY**, so a `tail -f` that sits quiet for minutes is not a stalled build —
  judge by CPU (`ps -eo pcpu,etime,cmd --sort=-pcpu | head`) and the growing object count under
  `.pio/build/`.
- **Recovering an interrupted `auto-install`.** If a run crashes (SSH drop under load, power blip),
  its leftover markers block the next start. `lhpc auto-install --status` prints the reason;
  `lhpc auto-install --recover` clears the reservation + lease + run marker in one action (the CLI
  equivalent of the console's recover button), and `--confirm-orphan` acknowledges a child whose
  termination could not be proven (inspect `ps` first). The state lives in
  `state/auto-install-start.json`, `state/auto-install-lease.json`, `state/auto-install.json` and
  `state/auto-install-plan.json` — `--recover` is the supported path; do not hand-edit them.

## MeshCom QEMU stack — managed tools

The QEMU firmware build provisions its two heavy tools **inside the runtime root**, so a fresh box
needs neither pipx nor a `~/.espressif` download:

- **PlatformIO** → a managed venv at `{root}/build/tools/platformio/.venv`, invoked by absolute path
  (the build scripts honor `PIO=…`). The pipx system dependency is retired. *Standalone-dev
  alternative:* `pipx install platformio` (then `pipx ensurepath`) — the build scripts fall back to
  `pio` on `PATH` when `PIO` is unset.
- **qemu-system-xtensa** → `scripts/build-qemu.sh` builds it **from source, HEADLESS**, at the pinned
  Espressif commit (`esp-develop-9.0.0-20240606`). It shallow-clones the pinned tag (**no submodules** —
  the emulator links only system glib/pixman/slirp/zlib/gcrypt, so the heavy `roms/*` firmware submodules are
  never pulled), configures with every display/audio back-end disabled (`--disable-sdl/gtk/vnc/opengl/
  curses/alsa/oss/pa/jack --audio-drv-list= --disable-werror`) and **`--enable-gcrypt`** — the esp32
  machine's RSA/AES accelerator devices (`hw/misc/esp32_rsa.c`, gated on `if gcrypt.found()`) call
  libgcrypt, and without it the esp32 machine ABORTS at init on "missing object type 'misc.esp32.rsa'"
  (proven live). QEMU 9.0 detects libgcrypt via the legacy `libgcrypt-config` tool, which Debian TRIXIE's
  `libgcrypt20-dev` no longer ships (1.11 moved to pkg-config), so build-qemu.sh synthesizes a
  `libgcrypt-config→pkg-config` compat shim when the tool is absent. `--disable-werror` is needed because a
  PINNED 9.0 tree tripped `-Werror` on Trixie's newer gcc. QEMU's normal feature set is otherwise kept
  (`--without-default-features` would drop the crypto backend the esp32 needs). It builds and
  installs through a DESTDIR staging tree at the FINAL prefix, **strips** the binary, then **link-gates**
  it (readelf + ldd must show no SDL/X11/Wayland/Mesa/GL/PulseAudio/ALSA library) and hashes it BEFORE
  the `.lhpc-qemu-built` marker is written. Publication is transactional (the shared `lib-publish.sh`:
  per-destination flock, backup container, atomic rename, rollback, startup-recovery) and idempotent (a
  valid marker + matching binary/config hashes short-circuits). A bounded smoke launch of
  `-machine esp32 -nic user,model=open_eth` on the PUBLISHED binary proves the machine, NIC and loader
  before the marker lands. It builds from its own pinned QEMU meson subprojects (keycodemapdb, berkeley
  softfloat/testfloat) fetched by git from QEMU's mirror; the genuine reproducibility danger —
  QEMU pip-installing meson from PyPI when the SYSTEM meson is too old — is refused up front by a
  system-meson version gate (Trixie's meson keeps QEMU's mkvenv offline). `run.sh` receives the resolved
  binary via the `qemu` stack parameter (and keeps `--qemu > PATH > IDF_TOOLS_PATH` fallbacks). The build
  is **native to the box** — a Pi 5 build serves the Pi 5, a Zero 2 W builds its own.

**Why from source.** The prebuilt Espressif tarball hard-links **libSDL2**; on a headless box it fails to
load, and `fetch-qemu.sh` used to refuse with a MISLEADING "not the pinned build" (the sha256 had
actually matched — the binary simply couldn't `dlopen` libSDL2). Satisfying libSDL2 pulls a ~35-package
X11/GPU/audio cascade onto an appliance with no display. A source build configured without the
display/audio back-ends has **no such dependency**, and the link gate proves it on the final stripped
binary. `fetch-qemu.sh` now reports the missing library honestly and remains a **manual, opt-in**
prebuilt fallback — no longer part of the managed build.

**Offline / prebuilt QEMU (manual, standalone — not via lhpc).** The managed build no longer needs a
tarball. If you instead want the **manual** `scripts/fetch-qemu.sh` prebuilt path (to reuse a cached
Espressif binary on a box that DOES carry a display stack), run that script **directly** with the pinned
tarball. `LHPC_QEMU_TARBALL` is fetch-qemu.sh's **own** environment variable, read only when you invoke
the script yourself — it is **not** forwarded through lhpc, so `lhpc build meshcom` (which builds from
source via `build-qemu.sh`) neither reads nor honors it. Two equivalent standalone forms:

```bash
scripts/fetch-qemu.sh <dest-dir> --from-file /absolute/path/qemu-...tar.xz
# equivalently, via fetch-qemu.sh's own env var:
LHPC_QEMU_TARBALL=/absolute/path/qemu-...tar.xz scripts/fetch-qemu.sh <dest-dir>
```

The path must be **absolute** and **readable**, and the file is subject to the **same pinned SHA-256
verification** — a wrong file is refused.

Because PlatformIO and the emulator are provisioned by `lhpc build`, they do not appear in
`bootstrap-deps.sh` as tools. The from-source emulator instead adds its **build toolchain** to the apt
layer — `git`, `build-essential`, `meson`, `ninja-build`, `pkg-config`, and the headless QEMU library
headers `libglib2.0-dev`, `libpixman-1-dev`, `libslirp-dev`, `zlib1g-dev`, `libgcrypt20-dev` (the esp32
RSA device) — alongside the runtime `libslirp0` and `curl`/`ca-certificates` (used to fetch the
sha256-verified Meshtastic web UI). The old prebuilt-tarball utilities (`wget`, `xz-utils`) are gone.

**Build cost (measured).** A cold from-source QEMU build is **~5 min on a Pi 5** (`-j3`) and **~68 min on
a Pi Zero 2 W** (`-j1`; ~500 MiB peak build footprint either way, ~18 MB stripped binary) — a one-time
step per box, cached by the `.lhpc-qemu-built` marker thereafter. The heavy `roms/*` firmware submodules
are never fetched and only the `qemu-system-xtensa` target is built (not the qtest suite). The memory-
aware `-j` (`min(nproc, max(1, floor(MemTotal_GB)))`) drops a 415 MB Zero to `-j1`: a live Zero build
held ~42 MB min-available with **no OOM** and never touched swap heavily, so **binary distribution is not
required** — a Zero builds its own emulator.

**Wi-Fi drops on a Pi Zero 2 W under build load (bootstrap disables power-save by default).** Live
finding: during the long `-j1` QEMU/firmware build the Zero's **brcmfmac Wi-Fi firmware crashed** — the
`wlan0` interface *vanished entirely* (`ip link` showed only `lo`; a `modprobe -r/+ brcmfmac` did NOT
bring it back — the SDIO chip was wedged and needed a **reboot** to power-cycle). Proven cause (not a
guess): `brcmf_cfg80211_set_power_mgmt: power save enabled` in the kernel log and `vcgencmd
get_throttled = 0x0` (so it was **power-save**, not under-voltage). The durable fix is to disable Wi-Fi
power-save. `bootstrap-deps.sh` now does this **by default when the install actually runs over Wi-Fi** —
the default route's device is classified via `nmcli` (`TYPE=wifi`, never an interface-name glob, so
`wlp2s0`/`wlx…` naming still counts as Wi-Fi; an absent or unclassifiable route falls back to disabling,
since a mis-detection must never remove the protection). It writes one NetworkManager drop-in
(`wifi.powersave = 2`), sets it live, prints a WARNING that it changed a system setting plus the exact
revert, and takes **`--keep-wifi-powersave`** to opt out entirely (with a warning about the drop risk).
A **LAN-carried install leaves Wi-Fi untouched** and prints the manual one-liner instead. Accepted
boundary: on a dual-link box whose default route is wired but whose SSH session rides the wlan address,
the gate skips — a long build can still drop that session; the printed one-liner is the remedy.
`bootstrap-deps.sh` enables a persistent journal (`/var/log/journal`, effective after the reboot) so a
future drop or spontaneous reboot is actually captured. NOTE: the build tmux
survives a Wi-Fi drop; `lhpc build` is idempotent (the QEMU marker + platformio caches make a re-run
resume), so a drop mid-build costs a reconnect, not the build.

**Pinned MeshCom source.** The controller pins `makrohard/meshcom-qemu-raspi` at the commit that
carries `scripts/build-qemu.sh` (+ the shared `scripts/lib-publish.sh` transaction), the `PIO=`-honoring
build scripts, and the memory-aware `-j` (see the manifest `[stack.component.source] pin_commit`). Both
consumers of that source (`meshcom-qemu` + `meshcom-gps-relay`) share the identical full SHA, enforced
by `tests/test_pin_consistency.py` — which additionally fails closed if a build/run step references a
script (like `build-qemu.sh`) that is not present at the pinned commit, so the pin must be bumped in
lock-step with the shipped scripts.

## MeshCom QEMU stack — what "normal" looks like

- The web UI at `:18083` returns **HTTP 502 until the firmware finishes booting** — **~1 min on a
  Pi 5, ~5–6 min on a Pi Zero 2W** — that is expected, not a failure (two live "meshcom is broken"
  reports were exactly this wait). It is **sluggish for the first minutes** while the node settles.
- **~50 % steady-state CPU** for the QEMU process is normal on a Pi; don't mistake it for a hang.
- The node's callsign switches from the placeholder to yours once it finishes booting and the
  post-start net-console step lands (see the callsign note above).

## Log naming

- Build/host-test job logs are `logs/build-<comp>.log` (single-step) or `logs/build-<comp>-<N>.log`
  (multi-step); host tests use `test-<comp>…`; run logs use `start-<comp>[-<band>].log`.
- A component that changes step count can leave a stale sibling of the other form. `lhpc logs <comp>`
  resolves to the **newest** matching file by mtime, and each job announces its exact path at start —
  so you always follow the file actually being written.

## meshtasticd YAML — `gpiochip` portability

The Uputronics meshtasticd template ships **with lhpc** as package data
(`lhpc/data/bases/meshtasticd.yaml`) — meshtasticd itself is a managed build, so the meshtastic stack
adopts no source at all and a fresh install clones nothing for it. `lhpc stack start meshtastic`
regenerates `{runtime}/config/files/meshtasticd.yaml` from that base every time, so edit stack
settings in lhpc rather than the generated file. The template uses plain BCM pin numbers and **no
hardcoded `gpiochip`**:

- **Pi Zero 2 W:** the 40-pin header is `gpiochip0` (the kernel default) — no `gpiochip` line needed.
- **Pi 5:** the header GPIO moved to a different chip (commonly `gpiochip4`) and can shift between
  kernels — so hardcoding a chip number is *not portable*. meshtasticd supports a per-pin `gpiochip:`
  syntax for boards that need it; add it only if your kernel puts the header on a non-default chip.

The 868 block is **RF95, CS 7, IRQ 16 — no `Reset`, no `Busy`**: this board has no dedicated reset
line, and BCM 6/13 are the daemon-owned LEDs (held via lgpio). A stale `Reset: 6` makes meshtasticd
assert-abort (`gpiod_line_request_reconfigure_lines 'request' failed`) whenever the LoRaHAM daemon
runs first. Do **not** re-add Reset/Busy. (Safe on a Pi 5 too — reset is optional there.)

## GPIO provider (meshcore-pi / RPi.GPIO)

The managed **meshcore-pi (868)** node runs the daemon-socket interface (`lorahaminterface`): it reaches
the radio over the loraham daemon's unix sockets and never drives SPI/GPIO itself, so it needs no GPIO
bindings and no `LoRaRF` (dropped from its venv — that also drops the transitive `RPi.GPIO`, whose sdist
does not build on Trixie's Python). Both hardware classes keep their OS GPIO defaults.

- If you run the **direct-SPI** MeshCore path (`type = "lora"`, which this deployment does *not*
  configure) you will need a GPIO provider. On **Trixie lite** the shim `python3-rpi-lgpio` (dist name
  `rpi-lgpio`) is the portable choice on **both** boards; installing classic `python3-rpi.gpio` removes
  that shim.
- Classic `RPi.GPIO` is **incompatible with the Pi 5's RP1** GPIO. If the current Zero 2 W rig carries
  classic `RPi.GPIO` (fine on that SoC), **restore `python3-rpi-lgpio` if the card ever moves to a Pi 5.**

## Adding a third-party apt package — audit checklist

The bootstrap's real package closure must be known *before* an install, not discovered on hardware.
Two live acceptance runs were aborted because it was not.

1. Run `sudo bash bootstrap-deps.sh --dry-run` **first** on a fresh image. It simulates the exact
   default apt transaction (`apt-get install -s -y --no-install-recommends`), changes nothing, and
   exits nonzero if the set cannot be resolved or would pull anything graphical/audio.
2. Recommends are not optional detail — they are how the cascade arrived. `git` Recommends
   `openssh-client`, which Recommends `xauth`, which Depends on `libX11`. The generated install runs
   `--no-install-recommends` for exactly this reason.
3. Before declaring a new third-party package, check what it *actually* links (`readelf -d`, `ldd`)
   against what it *declares* (`apt-cache show <pkg>`). They differ: meshtasticd declared
   `libsdl2-2.0-0` and never linked it, and that one overdeclared dependency was the entire
   99-package / 308 MB desktop cascade.
4. Record the closure size and the denylist verdict here when a package is added. A package that
   genuinely needs a Recommends must list it explicitly, with a comment saying why.

Never installed, in any mode: a desktop environment, display manager, or X/Wayland server.
`--with-gui` is the only GUI opt-in and it installs GUI *application libraries* only.

**Running the test suite on a Pi.** Always give pytest a dedicated basetemp and remove exactly that
path afterwards: `--basetemp="$HOME/pt-lhpc"` then `rm -rf -- "$HOME/pt-lhpc"`. On a Zero 2W the
default basetemp lands on the 208 MB `/tmp` tmpfs and the full suite fills it (ENOSPC); on any box,
leaked basetemps accumulate (a Pi 5 once held 19 GB of stray `lpt-*` dirs under `/var/tmp`). For such
legacy leftovers: stop pytest, list with `find /var/tmp -maxdepth 1 -uid "$(id -u)" -type d -name
'lpt-*'`, review, then remove explicitly — never a broad glob.
