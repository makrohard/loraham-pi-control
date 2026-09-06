# LoRaHAM Pi Control (`lhpc`)

[![Live demo](https://img.shields.io/badge/%E2%96%B6%20Live%20demo-in%20your%20browser-2ea44f)](https://makrohard.github.io/loraham-pi-control/)
<br>
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)

> Deutsche Anleitung: [`README.de.md`](README.de.md)
>
> **No Pi? Two ways to try it, no hardware needed:**
> - **[Live demo](https://makrohard.github.io/loraham-pi-control/)** — the real console
>   in your browser. Nothing to install, **no sign-in**, works for anyone (simulated, via
>   Pyodide).
> - **Codespace** (badge above) — the full test lab with real stack processes against
>   simulated hardware. **Requires signing in to a (free) GitHub account.** See
>   [`docs/testlab.md`](docs/testlab.md).

Install, configure and run the amateur-radio LoRa software stacks on a Raspberry Pi from one place
— a CLI and a local web console. `lhpc` adopts each stack's source, builds it, starts/stops it in
dependency order, enforces one stack per radio band, and writes every app's config. For operators
bringing up a LoRaHAM / Meshtastic / MeshCom / MeshCore box on a Pi Zero 2W or Pi 5.

## Contents

- [Overview](#overview)
- [Install](#install)
- [Configure & run stacks](#configure--run-stacks)
- [Remote access](#remote-access)
- [Autostart](#autostart)
- [Binary channel (prebuilt)](#binary-channel-prebuilt)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)

## Overview

### Stacks

| Stack | Band(s) | What it is | Docs |
|---|---|---|---|
| `daemon` | 433 + 868 | LoRaHAM daemon — owns the radios, exposes per-band sockets | [daemon](docs/stacks/daemon.md) |
| `chat` | 433 | APRS/chat TUI (local or over SSH) | [chat](docs/stacks/chat.md) |
| `voice` | 433 / 868 | LoRa voice — GTK app on desktop, ncurses terminal on Lite | [voice](docs/stacks/voice.md) |
| `kiss` | 433 / 868 | KISS TNC over TCP (xastir, YAAC …) | [kiss](docs/stacks/kiss.md) |
| `graywolf` | via `kiss` | Graywolf APRS station (web UI, digipeater, iGate) | [graywolf](docs/stacks/graywolf.md) |
| `meshtastic` | 433 / 868 | Rootless `meshtasticd`, drives the radio directly | [meshtastic](docs/stacks/meshtastic.md) |
| `meshcom` | 433 | MeshCom firmware in QEMU, bridged to the daemon | [meshcom](docs/stacks/meshcom.md) |
| `meshcore` | 868 | MeshCore on openHop — chat node (TCP 5000) and/or repeater (dashboard :8000), by `mode` | [meshcore](docs/stacks/meshcore.md) |
| `reticulum` | 433 / 868 | Reticulum node, drives the radio directly over SPI | [reticulum](docs/stacks/reticulum.md) |

Daemon-backed stacks start the daemon automatically; Meshtastic drives the radio itself and can't
share a band with the daemon (`lhpc` blocks the conflict).

**Position (GPS)** is one global setting shared by every stack that can use it — a gpsd on this box
or another, a receiver read directly, or a fixed position — and each stack has its own on/off
switch. `lhpc gps --source gpsd`, then `lhpc config meshtastic use_gps on`. See
[GPS](docs/gps.md).

### Hardware

Boards, on Pi **Zero 2W** and **Pi 5** (other SX127x/SX1262 SPI boards are expected to work but
are not validated):

- **LoRaHAM Pi HAT** — the [LoRaHAM project's](https://loraham.de) own dual-module board
  (SX1278 for 433 MHz + RFM95 for 868 MHz).
- **Uputronics Raspberry Pi Zero LoRa Expansion Board** ([Uputronics](https://store.uputronics.com))
  — one board for a single band, or two stacked boards for dual-band (CE0 = 433 MHz, CE1 = 868 MHz).
- **Waveshare SX1262 LoRaWAN/GNSS HAT**
  ([Waveshare](https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT)) — 433M and 868M
  variants.

Tested on the air: LoRaHAM Pi HAT dual-module controller, Uputronics dual stack, Waveshare
SX1262 433M. Not tested on silicon: Waveshare SX1262 868M. Dated evidence:
[live tests](docs/live-test.md).

**SPI mode:** `soft-cs` (`dtparam=spi=on` + `dtoverlay=spi0-0cs`) covers LoRaHAM Pi / Uputronics /
Waveshare (incl. dual, chip-selects as GPIOs); `hardware-cs` only for kernel-driven CE0/CE1.

## Install

> **Easiest path — a prebuilt image.** [`loraham-images`](https://github.com/makrohard/loraham-images)
> ships ready-to-use Raspberry Pi OS images with LHPC and every stack preinstalled — flash, boot, use.
> The steps below are the manual alternative.

From a freshly flashed card to running stacks. Steps run in order.

### 0. Prepare the card

Raspberry Pi Imager: pick your **model**, **Raspberry Pi OS Lite (64-bit)**, and set **hostname,
username, Wi-Fi + country, enable SSH** before flashing.

<details><summary>Headless fallback — if the imager's first-boot customisation doesn't apply (observed repeatedly)</summary>

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

### 1. First SSH login — with tmux on Wi-Fi/headless boxes

```bash
ssh <user>@lhpc-zero.local       # the user + hostname you set in the imager
sudo apt install -y tmux
tmux new -s lhpc                 # run everything below inside this session
#   detach: Ctrl-B then D  ·  after a drop: reconnect SSH, then  tmux attach -t lhpc
```

The tmux part matters on a **Pi Zero 2W or any Wi-Fi-connected headless box**: its Wi-Fi blips
under build load — the interface stalls for seconds, `sshd` stops answering while the box keeps
working — and a long step in a bare SSH session gets cut off. tmux keeps the work running; you just
reattach. Relevant for steps **3** (dependencies), **4** (install lhpc) and **8** (`auto-install` —
the long builds); on a Pi 5 over LAN you can skip it. If a drop does hit you outside tmux,
reconnect and re-run the step: every step is idempotent and resumes from cache.

### 2. Check what will be installed

Read-only pre-flight — resolves the package closure of a fresh image and **fails closed** if
anything graphical would be pulled in. Changes nothing, and deliberately **needs no root**: vet what
the script would install *before* ever granting it privileges (everything else requires `sudo`).

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run
```

### 3. Install dependencies

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

- **Root required** — run it exactly as shown (`sudo bash …`); a plain-user run refuses up front.
  The script itself **never invokes sudo**, so it also works unattended or where sudo is absent.
- **`--spi-mode` is required** — `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, incl. dual) ·
  `hardware-cs` (kernel CE0/CE1) · `skip`.
- **Optional flags** — `--with-gui` (GUI app libraries) · `--no-swapfile` · `--swap-size <MB>`
  (default 768) · `--operator-user <name>` (when run as root) · `--keep-wifi-powersave`.
- **Beyond apt it also** — disables the system `nginx.service` (the package stays; `lhpc` serves
  via its own rootless unit) · creates `/var/swap.lhpc` (768 MB, below zram) on boards under
  ~600 MB RAM as OOM insurance for the long builds · disables Wi-Fi power-save, but **only when the
  install actually runs over Wi-Fi** (a Zero 2W's Wi-Fi drops under sustained build load; a
  LAN-carried install leaves Wi-Fi untouched, and a warning prints the revert).

<details><summary>Manual — install only what the stacks you'll run need (bootstrap-deps.sh is the source of truth; preview with <code>--dry-run</code>, regenerate with <code>lhpc deps --script</code>)</summary>

<!-- test:deps-manual:start -->
```bash
# lhpc itself + fetch/TLS tools (nginx only if you want the web console)
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
echo '/var/swap.lhpc none swap sw,pri=-2 0 0' | sudo tee -a /etc/fstab && sudo swapon -a
printf 'dtparam=spi=on\ndtoverlay=spi0-0cs\n' | sudo tee -a /boot/firmware/config.txt   # SPI overlay
sudo usermod -aG spi,gpio "$USER"                        # → applied by the reboot in step 5
```
<!-- test:deps-manual:end -->
</details>

### 4. Install lhpc

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   or from a checkout: ./install.sh
#   options: --target <dir> · --no-service (skip the web service) · --no-path (skip the CLI symlink)
```

Everything lands under `~/loraham-pi-control/`: LHPC's checkout at `src/loraham-pi-control`, the
venv at `venv/lhpc`, settings/secrets/certs under `config/`.

<details><summary>Manual — clone / venv / bootstrap</summary>

```bash
mkdir -p ~/loraham-pi-control/src
git clone https://github.com/makrohard/loraham-pi-control.git ~/loraham-pi-control/src/loraham-pi-control
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control
~/loraham-pi-control/venv/lhpc/bin/lhpc bootstrap --yes
export PATH="$HOME/loraham-pi-control/venv/lhpc/bin:$PATH"
```
</details>

### 5. Reboot

One reboot applies everything at once: the SPI overlay and your new `spi`/`gpio` membership from
step 3 (not needed until a stack talks to the radio — which is exactly what comes next), and the
`PATH` with `lhpc` on it. Skip it and the next command fails with `lhpc: command not found`.

```bash
sudo reboot
```

Reconnect SSH afterwards (and start a fresh `tmux new -s lhpc` for the steps below).

### 6. Configure

```bash
lhpc config operator --callsign YOURCALL  # optional; YOURCALL = your base callsign — licensed
                                          # stacks inherit it (N0CALL-style placeholders refused)
                                          # while their own callsign field is empty; Meshtastic/
                                          # MeshCore instead need their own local node names
lhpc hardware loraham                     # pick your radio setup from the catalog:
```

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

The Uputronics chip-selects follow the stacking convention above (CE0 carries 433, CE1 carries
868). `lhpc hardware` without an argument prints this catalog; the web console's hardware panel
additionally offers an LED **Detect** probe to verify the wiring.

### 7. Bring up the web console

The install already started it: **`https://127.0.0.1:8443/`** — local access is open (no auth on
loopback; the self-signed-CA browser warning is expected). If you skipped it (`--no-service`):

```bash
lhpc webserver start-service      # local-only, no auth — nothing is exposed yet
```

- **Small box (Zero 2W / low RAM):** prefer the CLI (next step) and leave the console stopped
  during the big builds — the console itself (nginx + web app + status polling) costs RAM and CPU
  the builds can use; closing the browser tab alone frees nothing, the server side keeps running:
  ```bash
  systemctl --user stop lhpc-web lhpc-nginx      # start them again after auto-install
  ```
- **Pi 5 / desktop-class box:** the console's load doesn't matter — use it. From your desktop
  without exposing anything: `ssh -L 8443:127.0.0.1:8443 <user>@lhpc-zero.local`, then open
  `https://127.0.0.1:8443/`. First steps there: the **Auto-install** page, then the Webserver
  panel to [proxy stack UIs / expose the console with cert auth](#remote-access).
  The **Stacks WebGUIs** subpanel applies one common policy (access, scheme,
  authentication, CIDRs) to all stack WebGUIs at once — ports stay per page (missing ones get
  the normal suggested default; a stack with two web UIs has two); **LHPC WebGUI** keeps
  configuring the console itself. Per-stack
  panels remain available for exceptions; confirmations (enable-remote / enable-remote-danger)
  and firewall behavior are unchanged.

### 8. Auto-install the stacks (CLI)

Run it on the box (inside your SSH session — not on your desktop), and inside tmux:

```bash
tmux new -s lhpc                 # on the Pi; reattach after a drop: tmux attach -t lhpc
lhpc auto-install --yes
```

The three long-compiling stacks (daemon, meshtastic, meshcom) install from the
[binary channel](#binary-channel-prebuilt) by default — downloads of seconds to a couple of
minutes instead of hours. The rest build from source in minutes each. Building **everything** from
source instead (`--source pinned`) is ≈ 35–45 min on a Pi 5 and ≈ 4 h on a Pi Zero 2W.

Host tests are **off** by default; `--tests` enables them, `--tx` implies `--tests` and transmits
**real RF** (dummy loads!). Build artifacts persist — a re-run resumes from what is already
compiled. Headless "optional deps missing" warnings are expected.

<details><summary>Per-stack instead of everything</summary>

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

# meshtastic — binary by default (no build step); source: ≈ 15 min Pi 5 / ≈ 1¾ h Zero 2W
lhpc install meshtastic
#   from source instead:  lhpc install meshtastic --source pinned && lhpc build meshtastic

# meshcom — binary by default (no build step); source: ≈ 20 min Pi 5 / ≈ 2 h Zero 2W
lhpc install meshcom
#   from source instead:  lhpc install meshcom --source pinned && lhpc build meshcom

# meshcore — MeshCore on openHop (chat node, repeater, or both)
lhpc install meshcore
lhpc build meshcore
```

```bash
lhpc stack start <stack>
lhpc status
lhpc stack stop <stack>
```

A start — CLI or web — runs exactly the **saved** configuration; **Settings** (web) and
`lhpc config` are the only places a value changes. In the web console *Start* runs at once and asks
only when it would stop another stack; a missing identity sends you to that Settings row.
</details>

After `lhpc stack start meshcom`, the **emulated node itself still boots** (~1 min on a Pi 5,
~5–6 min on a Zero 2W) — its web UI answers 502 and the callsign stays a placeholder until then
(expected, not a failure).

**Watching progress.** `lhpc` prints a copy-pasteable `[log] <component> -> tail -f <path>` per
step — use those, not guessed names. Logs update in **batches** (block-buffered off a TTY), so a
quiet `tail -f` is not a stall — judge by CPU and object count:

```bash
ps -eo pcpu,etime,cmd --sort=-pcpu | head -3          # is a compiler actually running?
while sleep 60; do echo "$(date +%T) objs=$(find ~/loraham-pi-control/src -path '*/.pio/build/*' -name '*.o' | wc -l)"; done
```

## Configure & run stacks

```bash
lhpc status                        # what's running (read-only)
lhpc config <stack>                # list the stack's options and current values
lhpc config chat call YOURCALL-10 # set one option (YOURCALL-10 = your callsign+SSID)
lhpc config <stack> --band 868 <param> <value>    # per-band value on a band-switchable stack
lhpc stack start|stop|restart <stack>             # plans + confirms; --yes to skip the prompt
lhpc logs <target>                 # tail a component log
lhpc doctor                        # environment / dependency checks
lhpc test <stack> [--tx] --yes     # bounded RF test (real TX only with --tx — dummy loads!)
```

Mutating commands print a plan and need `--yes`; full reference [`docs/cli.md`](docs/cli.md).

## Remote access

After install the console is loopback-only. Exposing it to the LAN keeps TLS on and puts
**client-certificate auth** in front of every remote request; local access stays open:

```bash
lhpc webserver configure --dns localhost --dns lhpc-zero.local --ip 127.0.0.1 --ip 192.168.0.10
lhpc webserver tls-renew                # re-issue the server cert with those SANs
lhpc webserver cert issue lhpc-laptop   # prints a ONE-TIME passphrase — write it down
lhpc webserver cert export lhpc-laptop ~/lhpc-laptop.p12
lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

The runbook — `configure` replaces the SAN lists, never re-run `init`, the order with the managed
firewall, copying the bundle and the server CA to the device (one `scp` per file), browser and
phone import, proxying stack web UIs (`lhpc webserver proxy <stack> …`), public or auth-less
exposure — is [`docs/webserver.md`](docs/webserver.md). Opening ports beyond loopback needs the
managed firewall (native nftables, applied once as root): [`docs/firewall.md`](docs/firewall.md). Reaching the console and every stack UI over SSH
alone, with nothing exposed: [`docs/ssh-tunnel.md`](docs/ssh-tunnel.md).

## Autostart

The install enables the console at boot. Stacks that were running before a reboot are restarted
by `lhpc-boot-restore.service` through the normal start path, from their **saved** configuration:

```bash
lhpc autostart          # show the switch and the last boot-restore result
lhpc autostart off      # disable (applies at the NEXT boot)
lhpc autostart on       # re-enable (the default)
```

When restore runs, what happens on a failed restore, and the console units at boot:
[`docs/operations.md`](docs/operations.md).

## Binary channel (prebuilt)

The three long-compiling stacks — the LoRaHAM daemon, meshtasticd and MeshCom's QEMU — install a
**prebuilt binary** by default where one is published for the platform (aarch64 / Debian Trixie),
built by [lhpc-binaries](https://github.com/makrohard/lhpc-binaries) from exactly the commits this
lhpc pins. Every artifact is verified by sha256 and size before anything is unpacked; a failed
check refuses and offers the source channel, never a silent fallback. `lhpc install <stack>
--source pinned` builds from source instead; `lhpc status --versions` shows the channel. Policy:
[`docs/provenance.md`](docs/provenance.md); what the channel means in operation (updates, tests,
MeshCom auth): [`docs/operations.md`](docs/operations.md).

## Updating

One click in the console, or from a shell — back up `config/` + `profiles/` first
([`docs/operations.md`](docs/operations.md#backup--restore)):

```bash
systemctl --user stop lhpc-web && lhpc self-update --apply
```

**Updating to 0.3.0.** This release drops read compatibility for on-disk state that no supported
release writes. Everything a 0.2.10 box wrote is read unchanged. A deployment that has carried its
runtime root since 0.1.7 or earlier may need two one-time corrections: a `[radio].hardware` value of
`legacy` now reads as unset — re-pick the board with `lhpc hardware <setup>`; and pre-0.1.8 source
ownership records or transaction journals are refused as unreadable — clear them as described under
[identity drift](docs/operations.md#identity-drift-on-clean-or-uninstall). Old development images are
reflashed rather than updated in place.

Serving model, the one-click mechanism and `--repair-integration`:
[`docs/deployment.md`](docs/deployment.md).

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| `lhpc: command not found` after install | PATH not applied | reboot (step 5), or open a new login shell |
| build log frozen / silent for minutes | logs update in batches (block-buffered), large downloads too | judge by CPU + object count (step 8) |
| build killed / OOM on small-RAM boards | RAM pressure | swapfile (step 3); [Running on a Pi](docs/maintenance.md#running-on-a-pi) |
| "optional deps missing" on a headless box | GUI components skipped by design | ignore, or `--with-gui` |
| web console unreachable from another machine | not exposed / firewalled | [Remote access](#remote-access); [firewall](docs/firewall.md) |
| SSH dropped **during install**, run stopped | orchestrator got SIGHUP; detached build steps may continue | re-run `lhpc auto-install` (resumes from cached artifacts); use tmux (step 1). **Install-time only** — running stacks are systemd/detached and survive Wi-Fi drops; a drop in normal operation never needs a reinstall. On a Zero 2W, a USB-LAN adapter for the install sidesteps the problem entirely |
| board unreachable during a long build | low-RAM boards can lose the network under load | check the console, restart NetworkManager or reboot, then re-run; [Running on a Pi](docs/maintenance.md#running-on-a-pi) |
| a source install reports "GitHub clone failed" | the clone, or a step after it (checkout of the pinned commit), gave up | the reason is at the end of `logs/adopt-<component>.log` (`[fail] <step>: …`); re-run the install — a slow link is retried, not remembered |
| `auto-install` refuses to start after an interrupted run | leftover run markers | `lhpc auto-install --status`, then `lhpc auto-install --recover`; [CLI](docs/cli.md) |

## Documentation

| Group | Docs |
|---|---|
| Understand | [Architecture](docs/architecture.md) |
| Operate | [CLI](docs/cli.md) · [Operations](docs/operations.md) · [GPS](docs/gps.md) · [Maintenance](docs/maintenance.md) · [Backlog](docs/backlog.md) |
| Reach it | [Deployment](docs/deployment.md) · [Webserver (HTTPS + mTLS)](docs/webserver.md) · [SSH tunnel](docs/ssh-tunnel.md) · [WiFi access point](docs/wifi-access-point.md) · [Firewall](docs/firewall.md) |
| Stacks | [Adding a stack](docs/adding-a-stack.md) · [daemon](docs/stacks/daemon.md) · [kiss](docs/stacks/kiss.md) · [graywolf](docs/stacks/graywolf.md) · [chat](docs/stacks/chat.md) · [meshcore](docs/stacks/meshcore.md) · [meshcom](docs/stacks/meshcom.md) · [meshtastic](docs/stacks/meshtastic.md) · [reticulum](docs/stacks/reticulum.md) · [voice](docs/stacks/voice.md) |
| Verify | [Test matrix](docs/test-matrix.md) · [Live tests](docs/live-test.md) · [Test lab](docs/testlab.md) |
| Policy | [Provenance](docs/provenance.md) |

Full index: [`docs/README.md`](docs/README.md).
