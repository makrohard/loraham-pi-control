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
2. `sudo bash bootstrap-deps.sh --spi-mode soft-cs` → `sudo reboot`. **`--spi-mode` is required:**
   - `soft-cs` — single-radio LoRaHAM Pi / Uputronics (meshtasticd software CS, `/dev/spidev0.0`).
   - `hardware-cs` — a setup needing hardware CE0/CE1 (e.g. a **dual Uputronics**); SPI on, no overlay.
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

- The MeshCom QEMU firmware is the heavy build: **~26 min cold**, ~2–3 min incremental. RadioLib +
  daemon and the Python-venv stacks are minutes each. Its per-step build timeout is **3600 s** (>2×
  the measured cold build) so a slow step is never silently TERM-killed. A completion marker
  co-located with `flash.bin` (`.pio/build/<env>/.lhpc-build-complete`) is the authoritative
  built-state signal — a stale `flash.bin` from an earlier build never reads "built" after a
  failed/interrupted rebuild, and cleaning the firmware removes the marker with it.
- **Stop the web stack for heavy builds** on a 512 MB board — the controller and a `-j`-parallel
  compile competing for RAM is what triggers the OOM killer. lhpc already biases build children toward
  the OOM killer so the controller survives, and the QEMU build defaults to a memory-aware `-j`
  (`min(nproc, floor(MemTotal_GB))` → `-j1` on 512 MB, full parallelism on a Pi 5), but freeing RAM
  still makes the build faster and safer.
- Builds are detached: they survive a web-service restart. Every job prints a copy-pasteable
  `tail -f <log>` line the moment its log is created — follow the exact file from another terminal
  instead of guessing (`lhpc logs <comp>` resolves to the same newest file).

## MeshCom QEMU stack — managed tools

The QEMU firmware build provisions its two heavy tools **inside the runtime root**, so a fresh box
needs neither pipx nor a `~/.espressif` download:

- **PlatformIO** → a managed venv at `{root}/build/tools/platformio/.venv`, invoked by absolute path
  (the build scripts honor `PIO=…`). The pipx system dependency is retired. *Standalone-dev
  alternative:* `pipx install platformio` (then `pipx ensurepath`) — the build scripts fall back to
  `pio` on `PATH` when `PIO` is unset.
- **Espressif QEMU** → `scripts/fetch-qemu.sh` **transactionally** provisions the aarch64 tarball
  (`esp_develop_9.0.0_20240606`): it downloads/copies to a temp file, verifies the pinned **sha256
  BEFORE extraction**, extracts to a temp dir, checks the binary + version, then atomically publishes
  and writes a completion marker — a re-run skips only when that marker proves the pin, and a
  partial/failed provision never corrupts a previously verified install. It lands in
  `{root}/build/tool-cache/qemu-xtensa/…`; `run.sh` receives the resolved binary via the `qemu` stack
  parameter (and keeps `--qemu > PATH > IDF_TOOLS_PATH` fallbacks for standalone dev). aarch64 is
  correct for **both** a Zero 2 W and a Pi 5.

**Offline QEMU.** Provide the pinned tarball locally and set **`LHPC_QEMU_TARBALL=/absolute/path/…tar.xz`**
— it is the ONE allowlisted `LHPC_*` override forwarded through lhpc's sanitized command environment, so
a detached/web build honors it too (no other `LHPC_*` variables are forwarded). The path must be
**absolute** and **readable by the operator / web-service user**, and the file is subject to the **same
pinned SHA-256 verification** — a wrong file is refused.

- *CLI build:* a one-command assignment is enough —
  `LHPC_QEMU_TARBALL=/absolute/path/qemu-...tar.xz lhpc build meshcom` (or `export` it first).
- *Managed web-service build:* set it as a **temporary user-manager environment**, restart the service,
  build, then remove it — do **NOT** add a systemd drop-in (see the warning below):

  ```bash
  systemctl --user set-environment "LHPC_QEMU_TARBALL=/absolute/path/qemu-...tar.xz"
  systemctl --user restart lhpc-web.service
  # ... run the web/detached build ...
  systemctl --user unset-environment LHPC_QEMU_TARBALL     # cleanup is required
  systemctl --user restart lhpc-web.service
  ```

  > **Do not** add a permanent systemd `Environment=` drop-in for this variable. lhpc's canonical-unit
  > integrity contract classifies ANY drop-in as an override, and integration repair then refuses to
  > proceed. Permanent drop-ins are intentionally unsupported; the temporary user-manager environment
  > above is the supported offline path for the web service.

Because both tools are now provisioned by `lhpc build`, they no longer appear in `bootstrap-deps.sh`
— only `libslirp0`, plus the download/extract utilities (`wget`, `xz-utils`) and the OBS repo's
`curl`/`gnupg`/`ca-certificates`, remain apt-level.

**Pinned MeshCom source.** The controller pins `makrohard/meshcom-qemu-raspi` at the commit that
carries the transactional `fetch-qemu.sh`, the `PIO=`-honoring build scripts, and the memory-aware
`-j` (see the manifest `[stack.component.source] pin_commit`). Both consumers of that source
(`meshcom-qemu` + `meshcom-gps-relay`) share the identical full SHA, enforced by
`tests/test_managed_tool_contract.py` and `tests/test_pin_consistency.py`.

## MeshCom QEMU stack — what "normal" looks like

- The web UI at `:18083` returns **HTTP 502 until the firmware finishes booting** (~1–2 min) — that is
  expected, not a failure. It is **sluggish for the first minutes** while the emulated node settles.
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

The Uputronics meshtasticd template (`LoRaHAM_Pi/meshtastic/config.yaml`) uses plain BCM pin numbers
and **no hardcoded `gpiochip`**:

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
