# LoRaHAM Pi Control (`lhpc`)

Install, configure and run the amateur-radio LoRa software stacks on a Raspberry Pi from one place
— a CLI and a local web console. `lhpc` adopts each stack's source, builds it, starts/stops it in
dependency order, enforces one stack per radio band, and writes every app's config. For operators
bringing up a LoRaHAM / Meshtastic / MeshCom / MeshCore box on a Pi Zero 2W or Pi 5.

## Contents

- [Overview](#overview) — [Stacks](#stacks) · [Hardware](#hardware) · [Not included](#not-included)
- [Install](#install) — flashed card to running stacks (steps 0–7)
- [Use](#use) — [CLI](#cli) · [Web console](#web-console) · [Updating](#updating)
- [Troubleshooting](#troubleshooting) · [Documentation](#documentation)

## Overview

### Stacks

| Stack | Band(s) | What it is | Docs |
|---|---|---|---|
| `daemon` | 433 + 868 | LoRaHAM daemon — owns the radios, exposes per-band sockets | [daemon](docs/stacks/daemon.md) |
| `chat` | 433 | APRS/chat TUI (local or over SSH) | [aprs](docs/stacks/aprs.md) |
| `igate` | 433 | APRS iGate | [aprs](docs/stacks/aprs.md) |
| `voice` | 433 / 868 | LoRa voice GUI | [voice](docs/stacks/voice.md) |
| `kiss` | 433 / 868 | KISS TNC over TCP (xastir, YAAC …) | [kiss](docs/stacks/kiss.md) |
| `meshtastic` | 433 / 868 | Rootless `meshtasticd`, drives the radio directly | [meshtastic](docs/stacks/meshtastic.md) |
| `meshcom` | 433 | MeshCom firmware in QEMU, bridged to the daemon | [meshcom](docs/stacks/meshcom.md) |
| `meshcore` | 868 | MeshCore Pi node (TCP 5000) | [meshcore](docs/stacks/meshcore.md) |

Daemon-backed stacks start the daemon automatically; Meshtastic drives the radio itself and can't
share a band with the daemon (`lhpc` blocks the conflict).

### Hardware

Tested: **LoRaHAM Pi, Uputronics (single and dual), Waveshare** on Pi **Zero 2W** and **Pi 5**;
other boards are expected to work but are not validated. `lhpc hardware` lists the catalog:

<!-- test:hw-table:start -->
| `lhpc hardware …` | Board(s) | Bands → daemon preset |
|---|---|---|
| `loraham` | LoRaHAM dual-module (SX1278 + RFM95) | 433 → loraham, 868 → loraham |
| `uputronics` | Uputronics dual (CE0 433 + CE1 868) | 433 → uputronics-ce0, 868 → uputronics-ce1 |
| `uputronics-433` | Uputronics 433 (CE0) | 433 → uputronics-ce0 |
| `uputronics-868` | Uputronics 868 (CE1) | 868 → uputronics-ce1 |
| `waveshare-433` | Waveshare SX1262 (433) | 433 → waveshare-sx1262 |
| `waveshare-868` | Waveshare SX1262 (868) | 868 → waveshare-sx1262 |
<!-- test:hw-table:end -->

Waveshare 868 is not yet on-air-validated; a fresh install is unconfigured. **SPI mode:** `soft-cs`
(`dtparam=spi=on` + `dtoverlay=spi0-0cs`) covers LoRaHAM Pi / Uputronics / Waveshare (incl. dual,
chip-selects as GPIOs); `hardware-cs` only for kernel-driven CE0/CE1.

### Not included

- **No firewall management** — `lhpc` gates its own console; ports a stack opens are yours to close ([firewall](docs/firewall.md)).
- **No GUI/desktop is ever installed** — only GUI *application* libraries, and only with `--with-gui`.
- **Licence & TX compliance** stay the operator's responsibility — TX is never implicit.

## Install

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
sudo hostnamectl set-hostname loraham                # then match /etc/hosts:
echo "127.0.1.1 loraham" | sudo tee -a /etc/hosts
sudo sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen && sudo locale-gen && sudo update-locale
```
</details>

### 1. Check what will be installed  (~30 s, measured)

Read-only pre-flight — resolves the package closure of a fresh image and **fails closed** if
anything graphical would be pulled in. Changes nothing.

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
sudo bash bootstrap-deps.sh --dry-run
```

### 2. Install dependencies  (~2.5 min cold / 30–50 s re-run, measured)

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

`--spi-mode` is **required**: `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, incl. dual) ·
`hardware-cs` (kernel CE0/CE1) · `skip`. Also: `--with-gui` (GUI app libs) · `--no-swapfile` ·
`--swap-size <MB>` (default 768) · `--operator-user <name>` if run as root. Beyond apt it
**disables the system `nginx.service`** (package stays; `lhpc` serves via its own rootless unit)
and, under ~600 MB RAM, creates `/var/swap.lhpc` (768 MB, below zram) as OOM insurance for the long
builds — at some SD-card wear.

<details><summary>Manual — install only what the stacks you'll run need (bootstrap-deps.sh is the source of truth; preview with <code>--dry-run</code>, regenerate with <code>lhpc deps --script</code>)</summary>

<!-- test:deps-manual:start -->
```bash
# lhpc itself + fetch/TLS tools (nginx only if you want the web console)
sudo apt install -y --no-install-recommends git python3 python3-venv python3-pip nginx ca-certificates curl wget xz-utils
sudo apt install -y --no-install-recommends cmake liblgpio-dev build-essential          # daemon / RadioLib
sudo apt install -y --no-install-recommends libncurses-dev                              # chat / igate
sudo apt install -y --no-install-recommends socat                                       # kiss
sudo apt install -y --no-install-recommends libssl-dev libslirp0                        # meshcom (bridge + QEMU)
sudo apt install -y --no-install-recommends libyaml-cpp-dev libuv1-dev libgpiod-dev libi2c-dev libusb-1.0-0-dev libulfius-dev libbluetooth-dev pkg-config   # meshtastic (built from source)
sudo apt install -y --no-install-recommends libcodec2-dev libgtk-3-dev libasound2-dev python3-tk           # only with --with-gui (Voice, MeshCore Node Manager)

sudo systemctl disable --now nginx.service               # keep the package, disable the ROOT service
# small-RAM boards (<600 MB): a disk swapfile stops the meshtasticd/meshcom builds OOM-ing
sudo fallocate -l 768M /var/swap.lhpc && sudo chmod 600 /var/swap.lhpc && sudo mkswap /var/swap.lhpc
echo '/var/swap.lhpc none swap sw,pri=-2 0 0' | sudo tee -a /etc/fstab && sudo swapon -a
printf 'dtparam=spi=on\ndtoverlay=spi0-0cs\n' | sudo tee -a /boot/firmware/config.txt   # SPI overlay
sudo usermod -aG spi,gpio "$USER"                        # → continue at step 3 (reboot)
```
<!-- test:deps-manual:end -->
</details>

### 3. Reboot

The SPI overlay and your new `spi`/`gpio` membership take effect on reboot.

```bash
sudo reboot
```

### 4. Install lhpc  (~1.5 min, measured)

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   or from a checkout: ./install.sh
#   options: --target <dir> · --no-service (skip the web service) · --no-path (skip the CLI symlink)
```

Everything lands under `~/loraham-pi-control/`: LHPC's checkout at `src/loraham-pi-control`, the
venv at `venv/lhpc`, settings/secrets/certs under `config/`. (A transient PyPI retry downloading
`cryptography` is benign — pip retries.)

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

### 5. Log in again

`~/.local/bin` isn't on `PATH` in your current shell yet. **Reconnect SSH or open a new login
shell**, or the next command fails with `lhpc: command not found`.

### 6. Configure

```bash
lhpc config operator --callsign W1ABC     # your callsign (inherited by licensed stacks)
lhpc hardware                             # list the catalog
lhpc hardware uputronics                  # pick your radio (dual Uputronics shown)
```

### 7. Bring up the stacks

The **web console** is the primary path: reach it locally (step 4 brought it up at
`https://127.0.0.1:8443/`, or run `lhpc web` → `http://127.0.0.1:8770/`) and use the **Auto-install**
page. On a **Pi Zero 2W / low-RAM board, prefer the CLI** — a multi-hour build shouldn't depend on a
browser session. Run it detached so a dropped SSH connection can't abort it:

```bash
sudo apt install -y tmux
tmux
lhpc auto-install --yes          # detach: Ctrl-B then D · reattach: tmux attach
```

Host tests are **off** by default; `--tests` enables them, `--tx` implies `--tests` and transmits
**real RF** (dummy loads). Build artifacts persist, so a re-run resumes from what is already
compiled. Durations (**extrapolated**): meshtasticd ~2.5–3.5 h, meshcom ~26 min cold / ~2.5 min
incremental; full total **pending**. Headless "optional deps missing" warnings are expected.

**Watching progress.** `lhpc` prints a copy-pasteable `[log] <component> -> tail -f <path>` per
step — use those, not guessed names. Logs update in **batches** (block-buffered off a TTY), so a
quiet `tail -f` is not a stall — judge by CPU and object count:

```bash
ps -eo pcpu,etime,cmd --sort=-pcpu | head -3          # is a compiler actually running?
while sleep 60; do echo "$(date +%T) objs=$(find ~/loraham-pi-control/src -path '*/.pio/build/*' -name '*.o' | wc -l)"; done
while sleep 30; do free -m | awk '/Mem:/{print "mem",$3"/"$2} /Swap:/{print "swap",$3}'; vcgencmd measure_temp; done >> ~/watch.log
```

<details><summary>Per-stack instead of everything</summary>

```bash
lhpc install <stack>
lhpc build <stack>
lhpc stack start <stack>
lhpc status
```
</details>

## Use

### CLI

```bash
lhpc status                        # what's running (read-only)
lhpc doctor                        # environment / dependency checks
lhpc logs <target>                 # tail a component log
lhpc stack start|stop <stack>      # start / stop (plans + confirms)
lhpc build <target>                # build a stack
lhpc test <target> [--tx] --yes    # bounded RF test (real TX with --tx)
lhpc hardware [<setup>]            # show or set the radio hardware
lhpc config operator --callsign <CALL>
```

Mutating commands print a plan and need `--yes`; full reference [`docs/cli.md`](docs/cli.md).

### Web console

`lhpc web` serves a loopback console at `:8770`; the production HTTPS + mTLS front end (nginx,
`:8443`) is what you expose to a network:

```bash
lhpc webserver init --dns pi.local --ip 192.168.0.10
lhpc webserver start-service
lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver cert issue laptop && lhpc webserver cert export laptop ~/laptop.p12
lhpc webserver apply
```

Details: [`docs/webserver.md`](docs/webserver.md); opening ports beyond loopback needs a firewall
([`docs/firewall.md`](docs/firewall.md)).

### Updating

One click in the console, or from a shell (back up `config/` + `profiles/` first —
[`docs/operations.md`](docs/operations.md#backup--restore)):

```bash
systemctl --user stop lhpc-web && lhpc self-update --apply
lhpc self-update --repair-integration      # reinstall the managed units
```

Serving model and the one-click mechanism: [`docs/deployment.md`](docs/deployment.md).

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| `lhpc: command not found` after install | PATH not applied | log in again (step 5) |
| build log frozen / silent for minutes | logs update in batches (block-buffered), large downloads too | judge by CPU + object count (step 7); [field-notes](docs/field-notes.md) |
| build killed / OOM on small-RAM boards | RAM pressure | swapfile (step 2); [field-notes](docs/field-notes.md) |
| "optional deps missing" on a headless box | GUI components skipped by design | ignore, or `--with-gui` |
| web console unreachable from another machine | not exposed / firewalled | [Web console](#web-console); [firewall](docs/firewall.md) |
| SSH dropped, run stopped | orchestrator got SIGHUP; detached build steps may continue | re-run `lhpc auto-install` (resumes from cached artifacts); use tmux (step 7) |
| board unreachable during a long build | low-RAM boards can lose the network under load | check the console, restart NetworkManager or reboot, then re-run; [field-notes](docs/field-notes.md) |
| `auto-install` refuses to start after an interrupted run | leftover run markers | `lhpc auto-install --status`, then `lhpc auto-install --recover`; [field-notes](docs/field-notes.md) |

## Documentation

| Group | Docs |
|---|---|
| Understand it | [Architecture](docs/architecture.md) |
| Use it | [CLI](docs/cli.md) · [Operations & safety](docs/operations.md) · [Field notes](docs/field-notes.md) |
| Web console & remote access | [Deployment](docs/deployment.md) · [Webserver (HTTPS + mTLS)](docs/webserver.md) · [WiFi access point](docs/wifi-access-point.md) · [Firewall](docs/firewall.md) · [Migration](docs/deployment-migration.md) |
| Stacks | [Adding a stack](docs/adding-a-stack.md) · [daemon](docs/stacks/daemon.md) · [kiss](docs/stacks/kiss.md) · [aprs](docs/stacks/aprs.md) · [meshcore](docs/stacks/meshcore.md) · [meshcom](docs/stacks/meshcom.md) · [meshtastic](docs/stacks/meshtastic.md) · [voice](docs/stacks/voice.md) |
| Reference & policy | [Hardening](docs/hardening-0.1.md) · [Provenance](docs/provenance.md) |

Full index: [`docs/README.md`](docs/README.md).
