# LoRaHAM Pi Control (`lhpc`)

Install, configure and run the amateur-radio LoRa software stacks on a Raspberry Pi from one place
— a CLI and a local web console. `lhpc` adopts each stack's source, builds it, starts/stops it in
dependency order, enforces one stack per radio band, and writes every app's config. For operators
bringing up a LoRaHAM / Meshtastic / MeshCom / MeshCore box on a Pi Zero 2W or Pi 5.

## Contents

- [Overview](#overview) — [Stacks](#stacks) · [Hardware](#hardware) · [Not included](#not-included)
- [Install](#install) — flashed card to running stacks (steps 0–8)
- [Configure & run stacks](#configure--run-stacks) · [Remote access](#remote-access) · [Autostart](#autostart) · [Updating](#updating)
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

Tested boards, on Pi **Zero 2W** and **Pi 5** (other SX127x/SX1262 SPI boards are expected to work
but are not validated):

- **LoRaHAM Pi HAT** — the [LoRaHAM project's](https://loraham.de) own dual-module board
  (SX1278 for 433 MHz + RFM95 for 868 MHz).
- **Uputronics Raspberry Pi Zero LoRa Expansion Board** ([Uputronics](https://store.uputronics.com))
  — one board for a single band, or two stacked boards for dual-band (CE0 = 433 MHz, CE1 = 868 MHz).
- **Waveshare SX1262 LoRaWAN/GNSS HAT**
  ([Waveshare](https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT)) — 433M and 868M
  variants; 868 is not yet on-air-validated.

**SPI mode:** `soft-cs` (`dtparam=spi=on` + `dtoverlay=spi0-0cs`) covers LoRaHAM Pi / Uputronics /
Waveshare (incl. dual, chip-selects as GPIOs); `hardware-cs` only for kernel-driven CE0/CE1.

### Not included

- **No firewall management** — `lhpc` gates its own console; ports a stack opens are yours to close ([firewall](docs/firewall.md)).
- **No GUI/desktop is ever installed** — only GUI *application* libraries, and only with `--with-gui`.
- **Licence & TX compliance** stay the operator's responsibility — TX is never implicit.

## Install

From a freshly flashed card to running stacks. Steps run in order.

### 0. tmux — your safety net over SSH

A Pi Zero 2W's Wi-Fi blips under build load: the interface stalls for seconds, `sshd` stops
answering while the box keeps working, and a long step in a bare SSH session gets cut off. Run the
install steps inside `tmux` so the work survives the drop and you just reattach:

```bash
sudo apt install -y tmux     # first thing after your first SSH login
tmux new -s lhpc             # then run everything below inside this session
#   detach: Ctrl-B then D  ·  after a drop: reconnect SSH, then  tmux attach -t lhpc
```

This matters for steps **3** (dependencies), **4** (install lhpc) and **8** (`auto-install` — the
long builds). If a drop does hit you outside tmux, reconnect and re-run the step: every step is
idempotent and resumes from cache.

### 1. Prepare the card

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
sudo apt install -y --no-install-recommends git python3 python3-venv python3-pip nginx ca-certificates curl
sudo apt install -y --no-install-recommends cmake liblgpio-dev build-essential          # daemon / RadioLib
sudo apt install -y --no-install-recommends libncurses-dev                              # chat / igate
sudo apt install -y --no-install-recommends socat                                       # kiss
sudo apt install -y --no-install-recommends libssl-dev libslirp0 meson ninja-build libglib2.0-dev libpixman-1-dev libslirp-dev zlib1g-dev libgcrypt20-dev   # meshcom (bridge + QEMU built headless from source)
sudo apt install -y --no-install-recommends libyaml-cpp-dev libuv1-dev libgpiod-dev libi2c-dev libusb-1.0-0-dev libulfius-dev libbluetooth-dev pkg-config   # meshtastic (built from source)
sudo apt install -y --no-install-recommends libcodec2-dev libgtk-3-dev libasound2-dev python3-tk           # only with --with-gui (Voice, MeshCore Node Manager)

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
lhpc config operator --callsign W1ABC     # your callsign (inherited by licensed stacks)
lhpc hardware loraham                     # pick your radio setup from the catalog:
```

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

The Uputronics chip-selects follow the stacking convention above (CE0 carries 433, CE1 carries
868). `lhpc hardware` without an argument prints this catalog; the web console's hardware panel
additionally offers an LED **Detect** probe to verify the wiring.

### 7. Bring up the web console

The install already started it: **`https://127.0.0.1:8443/`** — local access is open (no auth on
loopback; the self-signed-CA browser warning is expected). If you skipped it (`--no-service`):

```bash
lhpc webserver start-service      # local-only, no auth — nothing is exposed yet
```

- **Desktop-class box (Pi 5):** use the console. First steps there: the **Auto-install** page, then
  the Webserver panel to [proxy stack UIs / expose the console with cert auth](#remote-access).
- **Pi Zero 2W or headless box:** prefer the CLI (next step) — a multi-hour build shouldn't hang
  off a browser tab.

### 8. Auto-install the stacks (CLI)

Full run: **≈ 45 min on a Pi 5, ≈ 4 h on a Pi Zero 2W.** Run it on the box (inside your SSH
session — not on your desktop), and inside tmux:

```bash
tmux new -s lhpc                 # on the Pi; reattach after a drop: tmux attach -t lhpc
lhpc auto-install --yes
```

Host tests are **off** by default; `--tests` enables them, `--tx` implies `--tests` and transmits
**real RF** (dummy loads!). Build artifacts persist — a re-run resumes from what is already
compiled. Headless "optional deps missing" warnings are expected.

<details><summary>Per-stack instead of everything</summary>

```bash
# daemon — LoRaHAM daemon, owns the radios (both bands)
lhpc install daemon
lhpc build daemon

# chat — APRS/chat TUI
lhpc install chat
lhpc build chat

# igate — APRS iGate
lhpc install igate
lhpc build igate

# voice — LoRa voice GUI (needs --with-gui dependencies)
lhpc install voice
lhpc build voice

# kiss — KISS TNC over TCP
lhpc install kiss
lhpc build kiss

# meshtastic — builds meshtasticd from source: ≈ 15 min Pi 5 / ≈ 1¾ h Zero 2W
lhpc install meshtastic
lhpc build meshtastic

# meshcom — builds headless QEMU + firmware from source: ≈ 20 min Pi 5 / ≈ 2 h Zero 2W
lhpc install meshcom
lhpc build meshcom

# meshcore — MeshCore Pi node
lhpc install meshcore
lhpc build meshcore
```

```bash
lhpc stack start <stack>
lhpc status
lhpc stack stop <stack>
```
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
lhpc config chat call W1ABC       # set one option
lhpc config <stack> --band 868 <param> <value>    # per-band value on a band-switchable stack
lhpc stack start|stop|restart <stack>             # plans + confirms; --yes to skip the prompt
lhpc logs <target>                 # tail a component log
lhpc doctor                        # environment / dependency checks
lhpc test <stack> [--tx] --yes     # bounded RF test (real TX only with --tx — dummy loads!)
```

Mutating commands print a plan and need `--yes`; full reference [`docs/cli.md`](docs/cli.md).

## Remote access

The console you get after install is loopback-only. Making it reachable from the LAN keeps TLS on
and puts **client-certificate auth** in front of every remote request (local access stays open):

```bash
lhpc webserver init --dns lhpc-zero.local --ip 192.168.0.10     # PKI: CAs + server cert
lhpc webserver cert issue laptop
lhpc webserver cert export laptop ~/laptop.p12                  # import this into the browser
lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

A **public or auth-less** exposure works, but you are on your own — anyone who reaches the port
controls your radios:

```bash
lhpc webserver expose --cidr 0.0.0.0/0 --auth no-auth --confirm-phrase enable-remote-danger
```

Stack web UIs are proxied through the same front end (their raw ports bind all interfaces with no
auth of their own — proxy them instead of opening those ports):

```bash
lhpc webserver proxy meshtastic --mode lan --port 8445 --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver proxy meshcom    --mode lan --port 8446 --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

MeshCore has no web UI to proxy — the remote Node Manager talks to the node on TCP 5000 directly;
allow it per source range in the stack's own config ([meshcore](docs/stacks/meshcore.md)).

Opening ports beyond loopback needs a firewall ([`docs/firewall.md`](docs/firewall.md)); details
and the browser client-cert runbook: [`docs/webserver.md`](docs/webserver.md).

## Autostart

The install enables the console at boot (rootless user units + lingering). Stacks do **not**
auto-start — you start them via console or CLI.

```bash
systemctl --user disable lhpc-nginx lhpc-web     # console: don't start at boot
systemctl --user enable lhpc-nginx lhpc-web      # start at boot again (the default)
systemctl --user stop lhpc-nginx lhpc-web        # stop right now
systemctl --user start lhpc-nginx lhpc-web       # start right now
```

## Updating

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
| `lhpc: command not found` after install | PATH not applied | reboot (step 5), or open a new login shell |
| build log frozen / silent for minutes | logs update in batches (block-buffered), large downloads too | judge by CPU + object count (step 8); [field-notes](docs/field-notes.md) |
| build killed / OOM on small-RAM boards | RAM pressure | swapfile (step 3); [field-notes](docs/field-notes.md) |
| "optional deps missing" on a headless box | GUI components skipped by design | ignore, or `--with-gui` |
| web console unreachable from another machine | not exposed / firewalled | [Remote access](#remote-access); [firewall](docs/firewall.md) |
| SSH dropped **during install**, run stopped | orchestrator got SIGHUP; detached build steps may continue | re-run `lhpc auto-install` (resumes from cached artifacts); use tmux (step 0). **Install-time only** — running stacks are systemd/detached and survive Wi-Fi drops; a drop in normal operation never needs a reinstall. On a Zero 2W, a USB-LAN adapter for the install sidesteps the problem entirely |
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
