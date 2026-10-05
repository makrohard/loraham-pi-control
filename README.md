# LoRaHAM Pi Control (`lhpc`)

**[Hier geht's zur deutschen Anleitung](README.de.md)**

Install, configure and run amateur-radio LoRa stacks on a Raspberry Pi — a CLI and a local WebGUI.
`lhpc` installs each stack from a pinned commit, a release, the development tip or a verified
prebuilt binary, writes every app's configuration from one set of settings, starts and stops them in
dependency order, and gives each band to one stack at a time. It runs rootless, as your own user.
Nine stacks: the LoRaHAM daemon with chat, voice, KISS TNC and Graywolf (APRS), plus Meshtastic,
MeshCom, MeshCore and Reticulum — on a Pi Zero 2W or Pi 5.

- **Have a Pi with a suitable [LoRa HAT](#hardware)?**

  > **The normal way in is a ready-made image:** Raspberry Pi OS with LHPC and every stack
  > installed and built; its first boot configures the box and reads an optional `lhpc-config.txt`
  > from the boot partition. This page is for building a box yourself.
  >
  > ## → [Get a ready-made image](https://github.com/makrohard/loraham-images)

- **No Pi? Two ways to try it, no hardware needed**

  > [![Live demo](https://img.shields.io/badge/%E2%96%B6%20Live%20demo-in%20your%20browser-2ea44f)](https://makrohard.github.io/loraham-pi-control/)
  > — the real WebGUI in your browser, simulated via Pyodide. **No sign-in.**
  >
  > [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)
  > — the test lab: real stack processes against simulated hardware. **Needs a (free) GitHub
  > account.** See [`docs/testlab.md`](docs/testlab.md).

## Contents

- [Hardware](#hardware)
- [Stacks](#stacks)
- [Install](#install)
- [Configure & run stacks](#configure--run-stacks)
- [After it runs](#after-it-runs)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)

## Hardware

Any Raspberry Pi HAT whose radio is an **SX1276**, **SX1278** or **SX1262** wired **directly to the
Pi's SPI bus** — `lhpc` drives the chip itself, so there is no LoRaWAN concentrator, no USB or
serial radio and no RNode firmware in between.

Three boards ship with a ready-made hardware preset:

- **LoRaHAM Pi HAT** — the [LoRaHAM project's](https://loraham.de) own dual-module board
  (SX1278 for 433 MHz + RFM95 for 868 MHz).
- **Uputronics Raspberry Pi Zero LoRa Expansion Board** ([Uputronics](https://store.uputronics.com))
  — RFM95/RFM98; one board for a single band, or two stacked for dual-band (CE0 = 433 MHz,
  CE1 = 868 MHz).
- **Waveshare SX1262 LoRaWAN/GNSS HAT**
  ([Waveshare](https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT)) — 433M and 868M
  variants.

Other boards with those chips: choose the preset whose wiring matches
([catalog](docs/cli.md#hardware)); they are not validated here.

Tested on **Pi Zero 2W** and **Pi 5**. On the air: LoRaHAM Pi HAT, Uputronics dual stack,
Waveshare SX1262 433M; dated records of the LoRaHAM Pi HAT runs: `docs/live-tests/`.

## Stacks

<details><summary><em>The nine stacks — bands, what each is, and its docs</em></summary>

| Stack | Band(s) | Licence | What it is | Docs |
|---|---|---|---|---|
| `daemon` | 433 + 868 | — | LoRaHAM daemon — owns the radios, exposes per-band sockets | [daemon](docs/stacks/daemon.md) |
| `chat` | 433 | yes | APRS/chat TUI (local or over SSH) | [chat](docs/stacks/chat.md) |
| `voice` | 433 / 868 | yes | LoRa voice — GTK app on desktop, ncurses terminal on Lite | [voice](docs/stacks/voice.md) |
| `kiss` | 433 / 868 | yes | KISS TNC over TCP (xastir, YAAC …) | [kiss](docs/stacks/kiss.md) |
| `graywolf` | via `kiss` | yes | Graywolf APRS station (web UI, digipeater, iGate) | [graywolf](docs/stacks/graywolf.md) |
| `meshtastic` | 433 / 868 | no | Rootless `meshtasticd`, drives the radio directly | [meshtastic](docs/stacks/meshtastic.md) |
| `meshcom` | 433 | yes | MeshCom firmware in QEMU, bridged to the daemon | [meshcom](docs/stacks/meshcom.md) |
| `meshcore` | 868 | no | MeshCore on openHop — chat node (TCP 5000) and/or repeater (dashboard :8000), by `mode` | [meshcore](docs/stacks/meshcore.md) |
| `reticulum` | 433 / 868 | no | Reticulum node, drives the radio directly over SPI | [reticulum](docs/stacks/reticulum.md) |
</details>

⚠️ **Licence.** The `yes` stacks need an amateur-radio licence, the `no` ones are for licence-free
ISM use; band, power, duty cycle and callsign are **your legal responsibility**.

Daemon-backed stacks start the daemon automatically; Meshtastic and Reticulum drive the radio
themselves and cannot share a band with the daemon (`lhpc` refuses the conflict).
Position (GPS) is one global source with a per-stack switch: [GPS](docs/gps.md).

## Install

### Manual install

From a freshly flashed card to running stacks, in order.

#### 1. Prepare the card

Raspberry Pi Imager: pick your **model**, **Raspberry Pi OS Lite (64-bit)**, and set **hostname,
username, Wi-Fi + country, enable SSH** before flashing.

<details><summary><em>Headless fallback — if the imager's first-boot customisation doesn't apply (observed repeatedly)</em></summary>

On the Pi, with keyboard and screen:

```bash
sudo rfkill unblock wifi
sudo raspi-config nonint do_wifi_country DE          # your ISO country code
sudo nmcli device wifi connect "<SSID>" password "<PSK>"
sudo systemctl enable --now ssh
sudo hostnamectl set-hostname lhpc-zero              # then match /etc/hosts:
echo "127.0.1.1 lhpc-zero" | sudo tee -a /etc/hosts
sudo sed -i 's/^# *\(en_US.UTF-8\)/\1/; s/^# *\(de_DE.UTF-8\)/\1/' /etc/locale.gen
sudo locale-gen && sudo update-locale
```
</details>

#### 2. First SSH login — with tmux on Wi-Fi/headless boxes

From your own machine, to open a session on the Pi:

```bash
ssh <user>@lhpc-zero.local       # the user + hostname you set in the imager
```

Then, on the Pi — everything from here to the end of the install runs there:

```bash
sudo apt update && sudo apt install -y tmux
tmux new -s lhpc                 # run everything below inside this session
#   detach: Ctrl-B then D
```

On Wi-Fi (a Zero 2W above all) the link stalls under build load and cuts SSH; tmux keeps the step
running. After a drop:

```bash
ssh <user>@lhpc-zero.local
tmux attach -t lhpc              # `tmux ls` lists the sessions
```

`bootstrap-deps.sh` and `lhpc auto-install` can be re-run (they resume); `install.sh` refuses an
existing checkout — if `~/loraham-pi-control/venv/lhpc/bin/lhpc --version` answers, do not re-run it.

#### 3. Check what will be installed

Read-only, **needs no root**. Exit 5 means apt cannot resolve the packages: run
`sudo apt-get update` and retry. Checks and exit codes: [deps](docs/cli.md#deps).

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run
```

#### 4. Install dependencies

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

- **Root required** (`sudo bash …`); the script itself never calls sudo.
- **`--spi-mode` is required** — `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, incl. dual) ·
  `hardware-cs` (kernel CE0/CE1) · `skip`. Which one, and the optional flags: [deps](docs/cli.md#deps).
- **Beyond apt it** disables the root `nginx.service` ([why](docs/webserver.md#first-time-bootstrap)) ·
  adds a swapfile on small-RAM boards and turns off Wi-Fi power-save during a Wi-Fi install
  ([Running on a Pi](docs/maintenance.md#running-on-a-pi)) · installs `polkitd` and two polkit rules
  for the WebGUI's Reboot/Shut down and Network panel · installs `chrony`, `gpsd` and `fake-hwclock`,
  replacing `systemd-timesyncd` ([Clock](docs/operations.md#clock)) · disables a packaged
  `meshtasticd.service`.

<details><summary><em>Manual — install only what the stacks you'll run need (bootstrap-deps.sh is the source of truth; preview with <code>--dry-run</code>, regenerate with <code>lhpc deps --script</code>)</em></summary>

<!-- test:deps-manual:start -->
```bash
# lhpc itself + fetch/TLS tools (nginx only if you want the WebGUI)
sudo apt install -y --no-install-recommends git python3 python3-venv python3-pip nftables nginx iw ca-certificates curl zstd
sudo apt install -y --no-install-recommends cmake liblgpio-dev build-essential          # daemon / RadioLib
sudo apt install -y --no-install-recommends libncurses-dev                              # chat / voice (terminal)
sudo apt install -y --no-install-recommends libcodec2-dev libasound2-dev                # voice (ncurses terminal UI — no graphical packages)
sudo apt install -y --no-install-recommends socat                                       # kiss
sudo apt install -y --no-install-recommends python3-libgpiod python3-spidev            # reticulum (direct-SPI radio, no compiler needed)
sudo apt install -y --no-install-recommends libssl-dev libslirp0 meson ninja-build libglib2.0-dev libpixman-1-dev libslirp-dev zlib1g-dev libgcrypt20-dev   # meshcom (bridge + QEMU built headless from source)
sudo apt install -y --no-install-recommends libyaml-cpp-dev libuv1-dev libgpiod-dev libi2c-dev libusb-1.0-0-dev libulfius-dev libbluetooth-dev pkg-config   # meshtastic (built from source)
sudo apt install -y --no-install-recommends libgtk-3-dev libx11-dev python3-dev        # only with --with-gui (Voice GTK app, Sideband)

sudo systemctl disable --now nginx.service               # keep the package, disable the ROOT service
# small-RAM boards (<600 MB): a disk swapfile stops the meshtasticd/meshcom builds OOM-ing
sudo fallocate -l 768M /var/swap.lhpc && sudo chmod 600 /var/swap.lhpc && sudo mkswap /var/swap.lhpc
echo '/var/swap.lhpc none swap sw,pri=10 0 0' | sudo tee -a /etc/fstab && sudo swapon -a
printf 'dtparam=spi=on\ndtoverlay=spi0-0cs\n' | sudo tee -a /boot/firmware/config.txt   # SPI overlay
sudo usermod -aG spi,gpio "$USER"                        # → applied by the reboot in step 6
```
<!-- test:deps-manual:end -->

The polkit rules (Reboot/Shut down, Network panel) and the clock packages are not in this list:
`lhpc doctor` (or Apps → LoRaHAM Pi Control → System dependencies) prints their commands while
they are missing.
</details>

#### 5. Install lhpc

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   or from a checkout: ./install.sh
#   options: --target <dir> · --no-service (skip the web service) · --no-path (skip the CLI symlink)
```

Everything but the `~/.local/bin/lhpc` link and the systemd user units lands under
`~/loraham-pi-control/` ([the runtime root](docs/architecture.md#the-runtime-root)).

<details><summary><em>Manual — clone / venv / bootstrap</em></summary>

```bash
mkdir -p ~/loraham-pi-control/src
git clone https://github.com/makrohard/loraham-pi-control.git ~/loraham-pi-control/src/loraham-pi-control
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control
~/loraham-pi-control/venv/lhpc/bin/lhpc bootstrap --yes
export PATH="$HOME/loraham-pi-control/venv/lhpc/bin:$PATH"
```
</details>

#### 6. Reboot

Applies the SPI overlay, the `spi`/`gpio` membership and the `PATH` with `lhpc` on it (without it:
`lhpc: command not found`).

```bash
sudo reboot
```

Reconnect SSH and start a fresh `tmux new -s lhpc`.

#### 7. Configure

```bash
lhpc config operator --callsign YOURCALL  # optional; YOURCALL = your base callsign
lhpc hardware loraham                     # pick your radio setup from the catalog:
```

<details><summary><em>The hardware catalog — every <code>lhpc hardware</code> setup</em></summary>

<!-- test:hw-table:start -->
| `lhpc hardware …` | Board(s) | Bands → daemon preset |
|---|---|---|
| `loraham` | LoRaHAM dual-module (SX1278 + RFM95) | 433 → loraham, 868 → loraham |
| `uputronics` | Uputronics dual (CE0 433 + CE1 868) | 433 → uputronics-ce0, 868 → uputronics-ce1 |
| `uputronics-x` | Uputronics dual, crossed modules (CE0 868 + CE1 433) | 433 → uputronics-ce1, 868 → uputronics-ce0 |
| `uputronics-433` | Uputronics 433 (CE0) | 433 → uputronics-ce0 |
| `uputronics-868` | Uputronics 868 (CE1) | 868 → uputronics-ce1 |
| `waveshare-433` | Waveshare SX1262 (433) | 433 → waveshare-sx1262 |
| `waveshare-868` | Waveshare SX1262 (868) | 868 → waveshare-sx1262 |
<!-- test:hw-table:end -->

Uputronics: CE0 carries 433, CE1 carries 868 (`uputronics-x` for crossed modules).
</details>

`lhpc hardware` alone prints the catalog. Which stacks inherit the callsign:
[identity](docs/architecture.md#identity-and-callsigns).

#### 8. The WebGUI — and reaching it from another machine

The install started it: **`https://127.0.0.1:8443/`** — no auth on loopback; the browser warns about
the self-signed CA. After `--no-service`:

```bash
lhpc self-update --repair-integration && lhpc webserver init && lhpc webserver start-service   # installs the units, then local-only, no auth
```

**From another machine**, five steps in this order (the certificate first: switching the policy
before your machine holds one locks you out). With the [access point](docs/wifi-access-point.md),
include `10.42.0.0/24` and `10.42.0.1` throughout. Set the date first (`timedatectl`; if it is
wrong: `sudo date -u -s 'YYYY-MM-DD HH:MM' && sudo fake-hwclock save`); while the clock is
unsynchronised, tick **Accept unverified clock** ([clock gate](docs/webserver.md#the-clock-gate)).

1. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → Certificates → Issue client cert**
   — copy the one-time passphrase shown.
2. **… → Stacks WebGUIs** — one policy for every stack UI (Access `lan`, Scheme `https`, Access
   mode `local-open-remote-auth`, Allowed CIDRs, Confirm `enable-remote`); it stays empty until the
   stacks are installed (step 9).
3. **… → LHPC WebGUI** — the same values, with **Bind** `0.0.0.0`; last, because it is the page you
   are working in. Its **IP SANs** must name every address you browse to (on an AP box add
   `10.42.0.1`), then re-issue with `lhpc webserver tls-renew`: Apply re-issues the certificate
   only when the box's own LAN address is missing from it, never for another SAN.
4. **Apply the managed firewall** on the Pi: run the two commands the panel shows
   ([firewall](docs/firewall.md#scenarios)).
5. **Apply** (the button, or `lhpc webserver apply`).

Commands, certificate import, public or auth-less exposure:
[remote exposure runbook](docs/webserver.md#remote-exposure-runbook). Exposing nothing:
[SSH tunnel](docs/ssh-tunnel.md).

#### 9. Auto-install the stacks (CLI)

- **Zero 2W / low RAM:** the CLI below, with the console stopped during the builds
  ([Running on a Pi](docs/maintenance.md#running-on-a-pi)).
- **Pi 5:** the WebGUI's **Auto-install** page works as well.

On the Pi, inside tmux:

```bash
tmux attach -t lhpc || tmux new -s lhpc   # on the Pi: the session from step 6, or a new one
lhpc auto-install --yes
```

daemon, meshtastic and meshcom install a prebuilt binary by default
([binary channel](docs/provenance.md#the-binary-channel)); the rest build from source. Measured on a
Pi Zero 2W: 19 min for the default run (0.2.10), ≈ 5 h with `--source pinned`. A re-run resumes
from what is compiled. Host tests and `--tx`: [auto-install](docs/cli.md#auto-install).

<details><summary><em>Per-stack instead of everything</em></summary>

```bash
# daemon — LoRaHAM daemon, owns the radios (both bands); binary by default (no build step)
lhpc install daemon
#   from source instead:  lhpc install daemon --source pinned && lhpc build daemon

# chat — APRS/chat TUI
lhpc install chat
lhpc build chat

# voice — LoRa voice (terminal variant builds headless; the GTK app needs --with-gui)
lhpc install voice
lhpc build voice

# kiss — KISS TNC over TCP
lhpc install kiss
lhpc build kiss

# graywolf — APRS station (digipeater + iGate, own web UI); drives the radio through the KISS TNC,
#            so kiss and daemon come with it. The build step fetches the pinned upstream .deb.
lhpc install graywolf
lhpc build graywolf

# meshtastic — binary by default (no build step); source: ≈ 2¾ h Zero 2W (measured)
lhpc install meshtastic
#   from source instead:  lhpc install meshtastic --source pinned && lhpc build meshtastic

# meshcom — binary by default (no build step); source: ≈ 2 h Zero 2W (measured)
lhpc install meshcom
#   from source instead:  lhpc install meshcom --source pinned && lhpc build meshcom

# meshcore — MeshCore on openHop (chat node, repeater, or both)
lhpc install meshcore
lhpc build meshcore

# reticulum — RNS node driving the radio directly over SPI, plus NomadNet, LXMF and Sideband
#             (Sideband is a desktop app — it needs bootstrap-deps.sh --with-gui)
lhpc install reticulum
lhpc build reticulum
```

```bash
lhpc stack start <stack>
lhpc status
lhpc stack stop <stack>
```
</details>

The emulated MeshCom node boots for minutes after its start ([meshcom](docs/stacks/meshcom.md#notes)).
Each step prints `[log] <component> -> tail -f <path>`; a quiet build, memory, an interrupted run:
[Running on a Pi](docs/maintenance.md#running-on-a-pi).

#### 10. Stack logins — created on a stack's first start

A stack with a login of its own creates it on its **first start** (**Apps → *stack* → Start**);
**Apps → *stack* → Password** then shows it with a copy button. Per stack:
[graywolf](docs/stacks/graywolf.md), [meshcore](docs/stacks/meshcore.md),
[meshcom](docs/stacks/meshcom.md); the rule: [secrets and passwords](docs/operations.md#secrets-and-passwords).

## Configure & run stacks

*Home* shows what runs, the radios and links to each stack's web UI; *Apps* has one row per stack:
Settings, Start/Stop, logs, Password. The console: [operations](docs/operations.md#operating-the-console).

<details><summary><em>The same from the CLI</em></summary>

```bash
lhpc status                        # what's running (read-only)
lhpc config <stack>                # list the stack's options and current values
lhpc config chat call YOURCALL-10 # set one option (YOURCALL-10 = your callsign+SSID)
lhpc config <stack> --band 868 <param> <value>    # per-band value on a band-switchable stack
lhpc stack start|stop|restart <stack>             # plans + confirms; --yes to skip the prompt
lhpc stack stop daemon --band 433                 # stop the daemon on one band only (daemon only)
lhpc logs <target>                 # tail a component log
lhpc rflog <stack> [--band B]      # tail a stack's RF log (what the radio heard and sent); --band: daemon
lhpc rflog <stack> --decrypt       # the same, decoded with the keys on this box (encrypted stacks)
lhpc doctor                        # environment / dependency checks
lhpc test <stack> [--tx] --yes     # host tests; --tx transmits
```

Full reference: [`docs/cli.md`](docs/cli.md); the RF rules: [TX safety](docs/operations.md#tx-safety).
</details>

## After it runs

### Wi-Fi access point

A box with an `lhpc-ap` NetworkManager profile gets the WebGUI's **Network** panel: join a WLAN
from the browser, with the box's own access point as fallback. Creating the profile:
[`docs/wifi-access-point.md`](docs/wifi-access-point.md).

### Autostart

A default install starts the WebGUI at boot, and the stacks running before a reboot come back
([boot restore](docs/operations.md#not-a-supervisor)); switch: `lhpc autostart on|off`.

### Updating

**Apps → LoRaHAM Pi Control → Update → Check for updates → Update now**, or
`lhpc self-update --apply`. Backup and mechanism: [self-update](docs/deployment.md#self-update).

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| `lhpc: command not found` after install | you are on your own machine, not on the Pi — `lhpc` only exists on the box | `ssh <user>@<host>` first, then run it there |
| `lhpc: command not found` on the Pi | PATH not applied | reboot (step 6), or open a new login shell |
| a build looks stalled, is OOM-killed, or the board drops off the network | RAM and Wi-Fi pressure on a small board | [Running on a Pi](docs/maintenance.md#running-on-a-pi) |
| "optional deps missing" on a headless box | GUI components skipped by design | ignore, or `--with-gui` |
| WebGUI unreachable from another machine | not exposed / firewalled | [step 8](#8-the-webgui--and-reaching-it-from-another-machine); [firewall](docs/firewall.md) |
| SSH dropped **during install**, run stopped | the run got SIGHUP; detached build steps may continue | re-run `lhpc auto-install` (it resumes); use tmux (step 2) or a USB-LAN adapter. Running stacks survive a drop |
| a source install reports "GitHub clone failed" | the clone or the checkout of the pinned commit gave up | reason at the end of `logs/adopt-<component>.log` (`[fail] <step>: …`); re-run the install |
| `auto-install` refuses to start after an interrupted run | leftover run markers | recover it: [auto-install](docs/cli.md#auto-install) |

## Documentation

Every doc, grouped by task: [`docs/README.md`](docs/README.md).
