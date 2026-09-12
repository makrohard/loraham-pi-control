# LoRaHAM Pi Control (`lhpc`)

**[Hier geht's zur deutschen Anleitung](README.de.md)**

Install, configure and run amateur-radio LoRa stacks on a Raspberry Pi from one place — a CLI and a
local WebGUI. `lhpc` installs each stack from the channel you pick — a pinned commit, a release,
or the development tip — or from a verified prebuilt binary, writes every app's own configuration
from one set of settings, starts and stops them in dependency order, and arbitrates the radios so two
stacks can never fight over a band. It runs rootless, as your own user. Nine stacks: the LoRaHAM
daemon with chat, voice, KISS TNC and Graywolf (APRS), plus Meshtastic, MeshCom, MeshCore and Reticulum — on a
Pi Zero 2W or Pi 5.

- **Have a Pi with a suitable [LoRa HAT](#hardware)?**

  > **The easiest way to a running box is to flash a ready-made image.** It ships Raspberry Pi OS
  > with LHPC and every stack already installed and built, and its README walks the whole setup in
  > twelve short steps, in English and German. That is the normal way in; everything on this page
  > is for building a box yourself.
  >
  > ## → [Get a ready-made image](https://github.com/makrohard/loraham-images)

- **No Pi? Two ways to try it, no hardware needed**

  > [![Live demo](https://img.shields.io/badge/%E2%96%B6%20Live%20demo-in%20your%20browser-2ea44f)](https://makrohard.github.io/loraham-pi-control/)
  > — the real WebGUI in your browser. Nothing to install, **no sign-in**, works for anyone
  > (simulated, via Pyodide).
  >
  > [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)
  > — the full test lab with real stack processes against simulated hardware. **Requires signing in
  > to a (free) GitHub account.** See [`docs/testlab.md`](docs/testlab.md).

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

Other boards with those chips should work — choose the preset whose wiring matches
(`lhpc hardware`, [catalog](docs/cli.md#hardware)) — but they are not validated here.

Tested on **Pi Zero 2W** and **Pi 5**. On the air: LoRaHAM Pi HAT dual-module controller,
Uputronics dual stack, Waveshare SX1262 433M. Not tested on silicon: Waveshare SX1262 868M. Dated evidence for the LoRaHAM Pi HAT runs:
[live tests](docs/live-test.md).

**SPI mode:** `soft-cs` (`dtparam=spi=on` + `dtoverlay=spi0-0cs`) covers LoRaHAM Pi / Uputronics /
Waveshare (incl. dual, chip-selects as GPIOs); `hardware-cs` only for kernel-driven CE0/CE1.

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

⚠️ **Licence.** The `yes` stacks are amateur radio and need a licence; the `no` ones are built for
licence-free ISM use. **Operating lawfully is your responsibility** — band, power and duty cycle
differ by country, and the callsign you transmit is yours. Nothing transmits until you pick
hardware and start a stack.

Daemon-backed stacks start the daemon automatically; Meshtastic and Reticulum drive the radio
themselves and can't share a band with the daemon (`lhpc` blocks the conflict).

**Position (GPS)** is one global setting shared by every stack that can use it — a gpsd on this box
or another, a receiver read directly, or a fixed position — and each stack has its own on/off
switch. `lhpc gps --source gpsd`, then `lhpc config meshtastic use_gps on`. See
[GPS](docs/gps.md).

## Install

### Manual install

> **Most people should stop here and go to
> [`loraham-images`](https://github.com/makrohard/loraham-images).** It ships ready-to-use Raspberry
> Pi OS images with LHPC and every stack already installed and built, and its README is the complete
> walkthrough: flash the card, get in, pick your board, set your callsign, start a stack, close the
> shipped defaults — twelve short steps, in English and German. **Nothing below is needed.**

The image's first boot configures the box on its own and reads an optional `lhpc-config.txt` from
the boot partition. The rest of this page is for building it yourself.

From a freshly flashed card to running stacks. Steps run in order.

#### 1. Prepare the card

Raspberry Pi Imager: pick your **model**, **Raspberry Pi OS Lite (64-bit)**, and set **hostname,
username, Wi-Fi + country, enable SSH** before flashing.

<details><summary><em>Headless fallback — if the imager's first-boot customisation doesn't apply (observed repeatedly)</em></summary>

On the Pi, with a keyboard and screen attached (this is the case where it never joined your network):

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

The tmux part matters on a **Pi Zero 2W or any Wi-Fi-connected headless box**: its Wi-Fi blips
under build load — the interface stalls for seconds, `sshd` stops answering while the box keeps
working — and a long step in a bare SSH session gets cut off. tmux keeps the work running; you just
reattach. Relevant for steps **4** (dependencies), **5** (install lhpc) and **9** (`auto-install` —
the long builds); on a Pi 5 over LAN you can skip it.

**Recovering from a blip.** Your SSH session dies; the tmux session does not, and the work inside it
keeps running on the box. Reconnect and step back into it:

```bash
ssh <user>@lhpc-zero.local       # on your machine: the terminal froze or dropped, open it again
tmux attach -t lhpc              # on the Pi: back in the session, output and all; `tmux ls` lists them
```

If `attach` answers *no sessions*, the step was not running inside tmux — reconnect and re-run it.
`bootstrap-deps.sh` and `lhpc auto-install` can be re-run as often as needed (they resume from
cache); before re-running `install.sh`, check whether the controller install already completed
(`~/loraham-pi-control/venv/lhpc/bin/lhpc --version` answers) — it is a fresh installer and
refuses an existing checkout.

#### 3. Check what will be installed

Read-only pre-flight — resolves the package closure of a fresh image and **fails closed** if
anything graphical would be pulled in. Changes nothing, and deliberately **needs no root**: vet what
the script would install *before* ever granting it privileges (everything else requires `sudo`).

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run
```

#### 4. Install dependencies

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

- **Root required** — run it exactly as shown (`sudo bash …`); a plain-user run refuses up front.
  The script itself **never invokes sudo**, so it also works unattended or where sudo is absent.
- **`--spi-mode` is required** — `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, incl. dual) ·
  `hardware-cs` (kernel CE0/CE1) · `skip`.
- **Optional flags** — `--with-gui` (GUI app libraries) · `--with-gps` (gpsd, for a receiver on
  this box) · `--no-swapfile` · `--swap-size <MB>` (default 768) · `--operator-user <name>` (when
  running as root directly rather than through `sudo`) · `--keep-wifi-powersave` · `--no-power-controls` · `--no-network-controls`.
- **Beyond apt it also** — disables the system `nginx.service` (the package stays; `lhpc` serves
  via its own rootless unit) · creates `/var/swap.lhpc` (768 MB, below zram) on boards under
  ~600 MB RAM as OOM insurance for the long builds · disables Wi-Fi power-save, but **only when the
  install actually runs over Wi-Fi** (a Zero 2W's Wi-Fi drops under sustained build load; a
  LAN-carried install leaves Wi-Fi untouched, and a warning prints the revert) · installs two
  polkit rules (and the `polkitd` package) so the WebGUI's Reboot/Shut down buttons and its Network
  panel are authorised (`--no-power-controls` / `--no-network-controls` skip them) · enables a
  persistent journal (`/var/log/journal`) · disables a packaged `meshtasticd.service` if one exists.

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

The WebGUI's Reboot/Shut down buttons and its Network panel additionally need `polkitd` plus two
polkit rules — `lhpc deps` (or Apps → LoRaHAM Pi Control → Dependencies) prints the exact commands.
</details>

#### 5. Install lhpc

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   or from a checkout: ./install.sh
#   options: --target <dir> · --no-service (skip the web service) · --no-path (skip the CLI symlink)
```

Everything lands under `~/loraham-pi-control/`: LHPC's checkout at `src/loraham-pi-control`, the
venv at `venv/lhpc`, settings/secrets/certs under `config/`.

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

One reboot applies everything at once: the SPI overlay and your new `spi`/`gpio` membership from
step 4 (not needed until a stack talks to the radio — which is exactly what comes next), and the
`PATH` with `lhpc` on it. Skip it and the next command fails with `lhpc: command not found`.

```bash
sudo reboot
```

Reconnect SSH afterwards (and start a fresh `tmux new -s lhpc` for the steps below).

#### 7. Configure

```bash
lhpc config operator --callsign YOURCALL  # optional; YOURCALL = your base callsign — licensed
                                          # stacks inherit it (N0CALL-style placeholders refused)
                                          # while their own callsign field is empty; Meshtastic/
                                          # MeshCore instead need their own local node names
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

The Uputronics chip-selects follow the stacking convention above (CE0 carries 433, CE1 carries
868).
</details>

`lhpc hardware` without an argument prints the catalog; the WebGUI's hardware panel
additionally offers an LED **Detect** probe to verify the wiring.

#### 8. The WebGUI — and reaching it from another machine

The install already started it: **`https://127.0.0.1:8443/`** — local access is open (no auth on
loopback; the self-signed-CA browser warning is expected). If you skipped it (`--no-service`):

```bash
lhpc self-update --repair-integration && lhpc webserver init && lhpc webserver start-service   # installs the units, then local-only, no auth
```

**Reaching it from another machine** is five steps, in this order. The certificate comes first: the
remote access modes require one, so switching the policy before your own machine holds it locks you
out.

The steps below name `10.42.0.0/24` and `10.42.0.1`, the box's own
[access point](docs/wifi-access-point.md). **That applies only where the AP exists** — the Lite
image creates `lhpc-ap` on first boot; a Desktop or hand-built box has one only if you made it.
Without an AP, skip those two values everywhere they appear and use your own network alone.

1. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → Certificates → Issue client cert**<br>
   Mints the certificate and shows a one-time passphrase — copy it there and then, it is never
   stored and never shown again. The same section offers copy boxes for fetching the `.p12` and the
   server CA to your own machine. Lost the passphrase? `cert reissue` mints a new bundle. Bundle
   gone astray? `cert revoke <label> --confirm-label <label>` withdraws it — the label is typed
   twice on purpose, because revoking the wrong credential is how you lock yourself out. Commands
   and the browser or phone import: [`docs/webserver.md`](docs/webserver.md).

2. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → Stacks WebGUIs**<br>
   One policy for *every* stack web UI at once, so you do not visit them one by one: Access `lan`,
   Scheme `https` (http forces no-auth), Access mode `local-open-remote-auth`, Allowed CIDRs — the
   networks you connect from, required for lan and public — and Confirm `enable-remote`
   (`enable-remote-danger` for a public or unauthenticated listener). Ports stay per page. This half
   stays empty until the stacks are installed (step 9) — come back to it then.<br>
   **With an AP, list two: your own network, e.g. `192.168.1.0/24`, and `10.42.0.0/24`.** The
   second is the box's own Access Point, which is how you reach a box that has left its network —
   the field case, and the one where nothing else works. Without an AP, your own network alone.

3. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → LHPC WebGUI**<br>
   The WebGUI itself, and last, because it is the page you are working in. The same values as
   step 2, except it has **Bind** instead of Access: `0.0.0.0` to listen beyond loopback. This
   same panel carries the server certificate's **DNS SANs** and **IP SANs** — on a box with an AP,
   add `10.42.0.1` to the IP SANs here, then re-issue with `lhpc webserver tls-renew`. An **Apply**
   reloads nginx and never re-issues the certificate, so a SAN added without `tls-renew` is saved
   and not served. (The *Certificates* section further down is for client credentials, not this.)

4. **Apply the managed firewall — on the Pi.** If this box has the recovery AP, first set
   **Apps → LoRaHAM Pi Control → Firewall** to enable the AP rules — interface `wlan0`, CIDR
   `10.42.0.0/24` — and do it *before* the radio becomes an AP: without them a joining phone never
   gets a DHCP lease, so the console is unreachable over the AP no matter what the allow-list says
   ([firewall](docs/firewall.md)). Then apply. Once the managed integration is in use, exposure is
   gated on it: `webserver apply` is refused while the firewall is unapplied, and nginx binds
   loopback-only at boot until the live check passes. Until you install it, nothing is filtered and
   nothing gates. The script exists from the install on (every Firewall save refreshes it); the
   WebGUI shows the same two copy-paste lines — the root script, then the Apply that activates the
   listeners it was gating:

   ```bash
   sudo bash ~/loraham-pi-control/config/files/firewall/firewall-apply.sh
   lhpc webserver apply
   ```

   LHPC never edits your own firewall, and a port on your router stays yours. See
   [`docs/firewall.md`](docs/firewall.md).

5. **Apply.** The **Apply** button in the WebGUI, or on the Pi:

   ```bash
   lhpc webserver apply
   ```

   Either way it validates and activates the listeners. An Apply refused while the firewall was
   pending is recorded and completes on its own once step 4 has run; run it again only if the panel
   still shows it pending.

<details><summary><em>The same five steps from a shell</em></summary>

All of these run **on the Pi**. The WebGUI's one-policy-for-every-stack form has no CLI equivalent —
from a shell you set each page by name (`lhpc webserver proxy <page> --port <port>`, one per stack
web UI, with the port the panel suggests for it;
`<page>` is the stack id, or `<stack>-<component>` for a stack's further web UIs — see
[`docs/cli.md`](docs/cli.md)):

```bash
lhpc webserver configure --dns lhpc-zero.local --ip 192.168.1.10 --ip 10.42.0.1   # 0 — every address you will use
lhpc webserver tls-renew                   # re-issue the server cert with those SANs
lhpc webserver cert issue lhpc-laptop      # 1 — prints a ONE-TIME passphrase; record it now
lhpc webserver cert export lhpc-laptop ~/lhpc-laptop.p12
lhpc webserver proxy <page> --port <port> --mode lan --scheme https \      # --port is required (0 = not proxied)
    --access-mode local-open-remote-auth --cidr 192.168.1.0/24 --cidr 10.42.0.0/24 --confirm-phrase enable-remote
lhpc webserver expose --cidr 192.168.1.0/24 --cidr 10.42.0.0/24 \
    --access-mode local-open-remote-auth --confirm-phrase enable-remote   # 2 — the WebGUI itself
lhpc firewall --ap on --ap-interface wlan0 --ap-cidr 10.42.0.0/24         # 3 — the AP's own rules,
sudo bash ~/loraham-pi-control/config/files/firewall/firewall-apply.sh    #     then apply: exposure is gated on it
lhpc webserver apply                                                      # 4 — validate + activate
systemctl --user restart lhpc-nginx lhpc-web                              # only if it does not come back
```

**On a box with the recovery AP, leaving `10.42.0.1` and `10.42.0.0/24` out is how it becomes
unreachable in the field.** Such a box raises its own Wi-Fi when it cannot find a network it knows,
and the console then tells you to open `https://10.42.0.1:8443` — but the console answers only
sources the allow-list names, over a certificate that has to carry that address. A LAN-only setup
is fine on the bench and silently useless the first time you take the box somewhere, which is
exactly when the AP is the only way in.

The Lite image creates that AP (`lhpc-ap`) on first boot. A Desktop or hand-built box has none
unless you create one — see [Wi-Fi access point](docs/wifi-access-point.md) — and without it these
two values are simply not yours to add. Substitute your own LAN range for `192.168.1.0/24`; the AP
range is the same on every box that has one.

Then, **on your own machine**, copy the bundle and the server CA across with one `scp` per file.
The full runbook —
`configure` replaces the SAN lists, never re-run `init`, the exact paths, the browser and phone
import, public or auth-less exposure — is [`docs/webserver.md`](docs/webserver.md).
</details>

*Alternative, when you do not want to expose anything at all:* an SSH tunnel brings the WebGUI and
any stack UI to your machine without adding a listener (meshtasticd is the exception — it binds all
interfaces itself, and the managed firewall is what closes it). On your own machine:

```bash
ssh -N -L 8443:127.0.0.1:8443 <user>@<host>     # leave it running, then open the URL below
```

Then `https://127.0.0.1:8443/` in your browser. Useful for a quick look or for debugging:
[`docs/ssh-tunnel.md`](docs/ssh-tunnel.md).

#### 9. Auto-install the stacks (CLI)

- **Small box (Zero 2W / low RAM):** prefer the CLI below and leave the WebGUI stopped
  during the big builds — the WebGUI itself (nginx + web app + status polling) costs RAM and CPU
  the builds can use; closing the browser tab alone frees nothing, the server side keeps running:
  ```bash
  systemctl --user stop lhpc-web lhpc-nginx      # start them again after auto-install
  ```
- **Pi 5 / desktop-class box:** the WebGUI's load doesn't matter — use it. First steps there: the
  **Auto-install** page, then the Webserver panel for the five steps above. Ports stay per page
  (missing ones get the normal suggested default; a stack with two web UIs has two), and the
  per-stack panels remain available for exceptions.

Run it on the box (inside your SSH session — not on your desktop), and inside tmux:

```bash
tmux attach -t lhpc || tmux new -s lhpc   # on the Pi: the session from step 6, or a new one
lhpc auto-install --yes
```

The three long-compiling stacks (daemon, meshtastic, meshcom) install a **prebuilt binary** by
default where one is published for the platform — verified by sha256 and size before anything is
unpacked, refusing rather than falling back silently ([provenance](docs/provenance.md); what the
channel means in operation: [operations](docs/operations.md)). The rest build from source in
minutes each: the whole default run
measured 19 min on a Pi Zero 2W at 0.2.10 ([live tests](docs/live-test.md)). Building **everything**
from source instead (`--source pinned`) is ≈ 5 h on a Pi Zero 2W, summing the measured per-stack
builds in the same live tests.

Host tests are **off** by default; `--tests` enables them, `--tx` implies `--tests` and transmits
**real RF** (dummy loads!). Build artifacts persist — a re-run resumes from what is already
compiled. Headless "optional deps missing" warnings are expected.

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

A start — CLI or web — runs exactly the **saved** configuration; **Settings** (web) and
`lhpc config` are the only places a value changes. In the WebGUI *Start* runs at once and asks
only when it would stop another stack; a missing identity sends you to that Settings row.
</details>

After `lhpc stack start meshcom`, the **emulated node itself still boots** — about a minute on a
Pi 5, 6 to 14 minutes on a Zero 2W ([live tests](docs/live-test.md)). Its web UI answers 502 and
the callsign stays a placeholder until then: expected, not a failure.

**Watching progress.** `lhpc` prints a copy-pasteable `[log] <component> -> tail -f <path>` per
step. A quiet log is not a stalled build: output is block-buffered off a TTY. How to judge it by CPU
and object count, the memory ceiling on a small board, and recovering an interrupted run:
[Running on a Pi](docs/maintenance.md#running-on-a-pi).

#### 10. Stack logins — created on a stack's first start

A stack that has a login of its own creates it the **first time it starts**, and the WebGUI then
shows the value. Start it once; you do not have to log in yet.

- **Apps → *stack* → Start**<br>
  The login is minted during that first start. Nothing to read yet.

- **Apps → *stack* → Password**<br>
  The account and the password, with a copy button. The section appears only for stacks that have
  one, and only once it exists — MeshCore says so in as many words until a repeater mode has run.

| Stack | Login | Where it is stored (under the runtime root) |
|---|---|---|
| **Graywolf APRS** | `admin` | `state/graywolf/graywolf-admin.txt` — LHPC creates it on the first start |
| **MeshCore** repeater dashboard | `admin` | `config/secrets/openhop_repeater_admin.txt` — only once **Mode** is `chat+repeater` or `repeater`; until then the panel says so |
| **MeshCom** | HMAC password, not a web login | `config/secrets/xr_pw` — changed only through the stack's HMAC actions, never by editing the file |

No command prints a password: the value never reaches a log, a flash message or the API. From a
shell on the Pi, read the file itself:

```bash
cat ~/loraham-pi-control/state/graywolf/graywolf-admin.txt
```

## Configure & run stacks

**Start on Home, configure on Apps.** *Home* is the overview — what is running, the radios and
their bands, and a link to each running stack's own web UI. *Apps* is where you work: one row per
stack with Settings, Start and Stop, logs and the Password section. Install, update, clean, power,
Wi-Fi and self-update show a plan and ask first; a routine Start runs at once and asks only when it
would stop another stack; Settings save on the first click.

<details><summary><em>The same from the CLI</em></summary>

```bash
lhpc status                        # what's running (read-only)
lhpc config <stack>                # list the stack's options and current values
lhpc config chat call YOURCALL-10 # set one option (YOURCALL-10 = your callsign+SSID)
lhpc config <stack> --band 868 <param> <value>    # per-band value on a band-switchable stack
lhpc stack start|stop|restart <stack>             # plans + confirms; --yes to skip the prompt
lhpc logs <target>                 # tail a component log
lhpc rflog <stack> [--band B]      # tail a stack's RF log (what the radio heard and sent)
lhpc rflog <stack> --decrypt       # the same, decoded with the keys on this box (encrypted stacks)
lhpc doctor                        # environment / dependency checks
lhpc test <stack> [--tx] --yes     # bounded RF test (real TX only with --tx — dummy loads!)
```

Mutating commands print a plan and confirm before applying (`--yes` skips the prompt); full
reference [`docs/cli.md`](docs/cli.md).
</details>

## After it runs

### Wi-Fi access point

Reaching the WebGUI from another machine is
[step 8](#8-the-webgui--and-reaching-it-from-another-machine) — certificate, policy, firewall,
apply. Deeper: [`docs/webserver.md`](docs/webserver.md), [`docs/firewall.md`](docs/firewall.md), and
[`docs/ssh-tunnel.md`](docs/ssh-tunnel.md) for reaching everything over SSH with nothing exposed.

A box that carries an `lhpc-ap` NetworkManager profile gets the WebGUI's **Network** panel: join a
WLAN from the browser, with the box's own access point as the fallback when that WLAN is out of
range.

**The Lite image creates that profile; the Desktop image does not** — Desktop joins your network
instead and never raises an access point. So this is the piece to add by hand on a **Desktop box or
a manual install** that has to work in the field, away from a known WLAN. Creating the profile and
the field setup: [`docs/wifi-access-point.md`](docs/wifi-access-point.md).

### Autostart

The install enables the WebGUI at boot. Stacks that were running before a reboot are restarted
by `lhpc-boot-restore.service` through the normal start path, from their **saved** configuration:

```bash
lhpc autostart          # show the switch and the last boot-restore result
lhpc autostart off      # disable (applies at the NEXT boot)
lhpc autostart on       # re-enable (the default)
```

When restore runs, what happens on a failed restore, and the WebGUI units at boot:
[`docs/operations.md`](docs/operations.md).

### Updating

**Apps → LoRaHAM Pi Control (the first row) → Update → Check for updates → Update now**, or from a
shell — back up `config/`, `profiles/` and the app data under `state/` first
([`docs/operations.md`](docs/operations.md#backup--restore)):

```bash
lhpc self-update --apply      # from an operator shell; it stops and restarts the WebGUI itself
```

Serving model, the one-click mechanism and `--repair-integration`:
[`docs/deployment.md`](docs/deployment.md).

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| `lhpc: command not found` after install | you are on your own machine, not on the Pi — `lhpc` only exists on the box | `ssh <user>@<host>` first, then run it there |
| `lhpc: command not found` on the Pi | PATH not applied | reboot (step 6), or open a new login shell |
| a build looks stalled, is OOM-killed, or the board drops off the network | RAM and Wi-Fi pressure on a small board | [Running on a Pi](docs/maintenance.md#running-on-a-pi) |
| "optional deps missing" on a headless box | GUI components skipped by design | ignore, or `--with-gui` |
| WebGUI unreachable from another machine | not exposed / firewalled | [step 8](#8-the-webgui--and-reaching-it-from-another-machine); [firewall](docs/firewall.md) |
| SSH dropped **during install**, run stopped | orchestrator got SIGHUP; detached build steps may continue | re-run `lhpc auto-install` (resumes from cached artifacts); use tmux (step 2). **Install-time only** — running stacks are systemd/detached and survive Wi-Fi drops; a drop in normal operation never needs a reinstall. On a Zero 2W, a USB-LAN adapter for the install sidesteps the problem entirely |
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
