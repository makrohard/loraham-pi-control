# Reticulum (RNS)

A [Reticulum](https://reticulum.network) node that drives the LoRa radio
**directly over SPI**. There is no rnoded, no RNode firmware and no KISS layer:
one LoRa packet is one RNS packet, so the radio's own framing does the job KISS
does over a serial link.

The driver lives in
[loraham-rns-interface](https://github.com/makrohard/loraham-rns-interface) and
is pinned like every other component.

## Contents

- [What you get](#what-you-get)
- [Install](#install)
- [Settings](#settings)
- [Hardware](#hardware)
- [Sharing the radio and the SPI bus](#sharing-the-radio-and-the-spi-bus)
- [NomadNet](#nomadnet)
- [Clients: on the Pi or on your phone](#clients-on-the-pi-or-on-your-phone)
- [Band limits](#band-limits)
- [Duty cycle](#duty-cycle)
- [Troubleshooting](#troubleshooting)

## What you get

| component | optional | what it is |
|---|---|---|
| `rns` | no | the node itself — owns the radio and the shared instance |
| `rns-lora-interface` | library | the direct-SPI driver, a build dependency of `rns` |
| `nomadnet` | yes | NomadNet browser/TUI — interactive, you start it yourself |
| `lxmd` | yes | LXMF delivery service (propagation **off** by default) |
| `sideband` | yes | Sideband desktop GUI — needs a display, and is heavy |

## Install

```bash
lhpc install reticulum --check     # what it would do
lhpc install reticulum --yes
lhpc build reticulum
lhpc stack start reticulum
```

The build makes a virtualenv with `--system-site-packages` and installs
Reticulum plus the driver into it. **The node needs no compiler**: the Python
SPI/GPIO bindings come from the `python3-libgpiod` and `python3-spidev` system
packages, which is what makes it installable on a Pi Zero.

**Sideband is the exception.** `sbapp` pulls `materialyoucolor`, a C++ extension
with no aarch64 wheel, so that optional component needs `python3-dev` (installed
by `bootstrap-deps.sh --with-gui`). Without it the Sideband build fails with
`Python.h: No such file or directory` — the node itself builds and runs fine, and
the failure does not block it. Measured on a Pi Zero 2 W: install 26 s, build
237 s, start 7 s.

## Settings

`lhpc config reticulum` (or the stack's Settings panel in the console). The
generated Reticulum config is written to
`<runtime>/state/reticulum/config` with mode `0600` and is **regenerated on every
start** — edit it through lhpc, not by hand.

| setting | default | notes |
|---|---|---|
| Frequency | 868.500 MHz / 434.500 MHz | per band — see [Band limits](#band-limits) |
| Bandwidth · SF · coding rate | 125 kHz · 8 · 4/5 | 3.12 kbps; SF 7-12, BW 62.5-500 kHz |
| TX power | 14 dBm (868) / 10 dBm (433) | per band; max 17 dBm (PA_BOOST); 433 SRD is 10 mW ERP |
| Airtime limits | 5 % / 15 s, 1 % / 1 h | 868 SRD is 1 %; 433 permits 10 % |
| Client-access bind | `127.0.0.1` | see below |
| Client-access allow-list | `127.0.0.1` | drives the managed firewall |
| IFAC network name | unset | with the key, authenticates the RF interface |

Pins, chip type, TCXO and PA settings are **not** settings: they come from the
hardware setup you picked with `lhpc hardware`. A wrong PA or TCXO value can
damage the module, so the combination is not left open.

## Hardware

| `lhpc hardware` | bands | chip | note |
|---|---|---|---|
| `loraham` | 433 + 868 | SX1278 / SX1276 | RESET wired |
| `uputronics` (and `-433`/`-868`) | 433 / 868 | SX127x | **no RESET line** — soft reset |
| `waveshare-433` / `waveshare-868` | 433 / 868 | SX1262 | **868 verified on hardware** (both directions vs SX1276, driver `bfb97b9`); 433 untested — the tested board is 868-only. DIO2 RF switch; the driver probes for a TCXO on DIO3 and falls back to the crystal |

> **SX1262 (Waveshare): hardware-verified on 868**, TX and RX against an SX1276
> peer. **On 433 it is code-complete but untested** — treat the first bring-up on
> that band as a bench test.
>
> The board tested here has **no TCXO**; the driver probes for one and falls back
> to the crystal. Other SX1262 boards do have one.

## Sharing the radio and the SPI bus

The stack claims its band **exclusively**, so lhpc refuses to start it alongside
another owner of the same radio (the daemon on that band, or meshtastic). The
opposite bands are fine: the 433 daemon and Reticulum on 868 run together.

Both take the daemon's `spi0.lock` around every SPI transaction, so sharing
`/dev/spidev0.0` is safe. See [field notes](../field-notes.md) for the one
direct-SPI owner that does *not* participate in that lock.

## NomadNet

NomadNet is an ncurses browser, so it is **not** run as a background service —
lhpc treats it like the chat stack: it shows you the command and you start it in
a terminal (locally or over SSH), because a TUI needs a real TTY.

Take the command from the dashboard card, or from the CLI — it is generated, so it
always carries the ownership guard:

```bash
lhpc status reticulum        # the nomadnet card prints the exact command
```

It looks like this, and the `loraham-rns-client` prefix is the important part:

```bash
cd ~/loraham-pi-control/src/nomadnet && \
  ~/loraham-pi-control/src/reticulum/.venv/bin/loraham-rns-client \
    --configdir ~/loraham-pi-control/state/reticulum --wait 10 -- \
    .venv/bin/nomadnet --rnsconfig ~/loraham-pi-control/state/reticulum \
    --config ~/loraham-pi-control/state/nomadnet; stty sane 2>/dev/null
```

The guard proves an authenticated Reticulum shared instance already exists and
exits 3 otherwise. **Do not run `.venv/bin/nomadnet` directly against the owner's
config**: with `rns` absent, Reticulum makes the first process the shared-instance
owner, so NomadNet would load the LoRa interface and take the radio outside lhpc's
arbitration. While it is open, your node also serves its own pages and files.

## Clients: on the Pi or on your phone

Reticulum's client-access interface (TCP 4242) is what Sideband and MeshChat
attach to. It is **loopback-only**: the setting offers no other value,
and the node refuses a non-loopback bind even if the config is edited by hand.
That port carries no authentication of its own, and an allow-list is firewall
*intent*, not enforcement — a firewall may be absent, stale or never applied.

Reach it from another machine through an SSH tunnel:

```bash
ssh -N -L 4242:127.0.0.1:4242 makro@<pi>     # then point the client at 127.0.0.1:4242
```

Lifting the restriction needs the start path to refuse a non-loopback bind unless the
managed firewall is installed, current and live-verified for that exact bind/allow pair.

There is **no transport authentication** on that port. Add IFAC keys to
authenticate the interface itself — the network name is a normal setting, the
key lives in `<runtime>/config/secrets.toml` and never appears in status output
or logs:

```toml
[reticulum]
ifac_netkey = "a long passphrase"
```

`secrets.toml` must not be readable beyond its owner — a 0600 generated config is
pointless if the key's source is world-readable, so lhpc refuses to load a file
with any group/other permission bits. Create it accordingly (some editors restore
0644 on save):

```sh
install -m 0600 /dev/null <runtime>/config/secrets.toml   # or: chmod 600 …
```

lhpc writes these into the interface as RNS's own `networkname` and `passphrase`
keys — those are the only names Reticulum recognises. Set **both** the network
name and the passphrase, or IFAC stays off.

A missing key means the interface is simply not IFAC-protected; it never becomes
an *empty* key.

**Sideband is best run off the Pi.** It costs ~277 MB resident against ~26 MB for
the node, needs a display, and cannot run on a Pi Zero at all. Install it on a
phone or laptop and point it at the client-access port. The on-Pi component
exists for graphical desk units and is only installed with `--with-gui`.

## Band limits

Both default frequencies sit in licence-free SRD bands, but the limits differ:

| band | default | limit | licence |
|---|---|---|---|
| 868 | 868.500 MHz | 25 mW ERP (14 dBm), duty per SUB-BAND (see below) | none (SRD) |
| 433 | 434.500 MHz | **10 mW ERP (10 dBm)**, <10 % duty cycle | none (SRD / LPD433) |

The 433 default is deliberately 434.500 MHz: it stays clear of the LoRaHAM
chat/APRS channel (433.775/433.900), the LoRaWAN EU433 mandatory channels
(433.175/433.375/433.575) and Meshtastic's EU_433 range (433.0–434.0), while
remaining inside 433.050–434.790 MHz.

> **10 mW is ERP — it includes antenna gain.** 10 dBm conducted into a roughly
> unity-gain whip is just inside the limit; with a gain antenna you must turn the
> TX power down. The stack cannot know your antenna, so this one is on you.

The shipped duty-cycle defaults (1 % long / 5 % short) are stricter than 433
requires and match what 868 requires, so they are safe on both bands.

### Permitted segments

863–870 MHz is **not** one continuous allocation. The driver knows the permitted
segments and their individual duty ceilings, and refuses anything else — an
operator setting may only tighten a ceiling, never widen it:

| segment | hourly duty ceiling |
|---|---|
| 863.0–865.0 MHz | 0.1 % |
| 865.0–868.6 MHz | 1 % |
| 868.6–868.7 MHz | *alarm systems only — refused* |
| 868.7–869.2 MHz | 0.1 % |
| 869.2–869.4 MHz | *alarm systems only — refused* |
| 869.4–869.65 MHz | 10 % |
| 869.65–869.7 MHz | *alarm systems only — refused* |
| 869.7–870.0 MHz | 1 % |
| 433.050–434.790 MHz | 10 % |

The **whole occupied bandwidth** must fit inside one segment: 869.500 MHz is legal
at 125 kHz but not at 500 kHz, which would spill outside the 250 kHz-wide 10 %
segment. A channel that matches no segment is refused rather than defaulted.

## Duty cycle

Airtime is reserved *before* transmitting and written to disk, so a restart or a
crash loop cannot wipe the hour's accounting. An unconfirmed transmission stays
charged — we cannot prove nothing was radiated. Corrupt accounting state blocks
transmit but never receive.

## Troubleshooting

| symptom | cause |
|---|---|
| start fails, "another Reticulum shared instance already owns this configuration" | a stray `rnsd`/node is running; stop it |
| start fails, "interface was not registered" | the radio or the SPI lock failed — the log above it names which |
| start fails, "refusing to expose an unauthenticated RNS interface" | non-loopback bind with a loopback allow-list |
| "SPI bus lock not acquired within 2s" | a peer holding `spi0.lock` is wedged (usually a stuck daemon) |
| TX seems to stall | the duty-cycle limiter is holding packets; check the airtime limits |

## Position (Sideband)

Sideband's location plugin takes its position from the global setting (`lhpc gps`, see
[GPS](../gps.md)) — gpsd (local or remote), a directly-read receiver, or a fixed position.

Its position fields are no longer editable per stack: one setting answers "where is this
box" for every stack, so two cannot disagree. If older per-stack values are still saved,
`lhpc gps` and `lhpc doctor` list them and say they are ignored.

The plugin reads its settings from `LHPC_LOCATION_CONF`, which lhpc points at the generated
`state/sideband/location.conf`. That file is (re)generated when the stack **starts**, so a change
to `lhpc gps` takes effect on the next start, not immediately.
