# Stack: Reticulum (RNS)

A [Reticulum](https://reticulum.network) node driving the LoRa radio **directly over SPI** on 868
(default) or 433 MHz. No rnoded, no RNode firmware, no KISS layer: one LoRa packet is one RNS
packet. The driver is [loraham-rns-interface](https://github.com/makrohard/loraham-rns-interface).

| | |
|---|---|
| Components | `rns` (main — owns the radio and the shared instance; runs `loraham-rns-node`) · `rns-lora-interface` (library: the direct-SPI driver, RNS interface and service runner; a build-time dependency) · `nomadnet` (optional, interactive) · `lxmd` (optional; propagation **off** by default) · `sideband` (optional desktop GUI, `--with-gui` only) |
| Source / pin | `src/reticulum` ← `markqvist/Reticulum` `1.5.2` · `src/loraham-rns-interface` ← `makrohard/loraham-rns-interface` `3fef542` · `src/nomadnet` `ad10301` · `src/lxmf` `795fdaa` · `src/sideband` `2.1.0` (installed as `sbapp==1.9.2` from PyPI — a source install drops every `.kv` layout) |
| Build | a venv with `--system-site-packages` (the SPI/GPIO bindings come from the `python3-libgpiod` + `python3-spidev` system packages, so the node needs no compiler — what makes it installable on a Pi Zero); Reticulum + the driver; the interface copied to `state/reticulum/interfaces/LoRaSPIInterface.py`; an import probe before the marker |
| Run | `.venv/bin/loraham-rns-node --config <runtime>/state/reticulum --interface LoRa --ready-file <runtime>/state/reticulum/ready --client-allow <allow-list>` — it exits rather than staying up without a radio |
| Endpoints | shared instance `127.0.0.1:37428` · instance control `:37429` · client access `:4242` · readiness = the `ready` file, written only after the node owns the instance **and** the radio is online |
| Config | `<runtime>/state/reticulum/config` (0600) from `lhpc/data/bases/reticulum.conf`, regenerated on every start — edit it through lhpc |
| Resources | `loraham.radio.868` + `.433` exclusive · `spi.bus.0` cooperative · `spi.bus.0.unlocked` exclusive · `tcp.port.37428` / `.37429` / `.4242` exclusive |
| System | `/dev/spidev0.0` with `dtoverlay=spi0-0cs` (`bootstrap-deps.sh --spi-mode soft-cs`); `spi` + `gpio` groups; `python3-libgpiod`, `python3-spidev` |
| Install channel | source only |

## Contents

- [Settings](#settings)
- [Hardware](#hardware)
- [Position (GPS)](#position-gps)
- [Clients](#clients)
- [Band limits](#band-limits)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

`lhpc config reticulum` or the stack's Settings panel:

| setting | default | notes |
|---|---|---|
| `frequency` | 868 500 000 Hz / 434 500 000 Hz | per band — see [Band limits](#band-limits) |
| `bandwidth` · `spreadingfactor` · `codingrate` | 125 000 Hz · 8 · 5 | BW 62.5–500 kHz, SF 7–12 (SF6 needs implicit-header mode, unsupported), CR 4/5–4/8 |
| `txpower` | 14 dBm (868) / 10 dBm (433) | the driver refuses anything above the band policy: 14 dBm on 868, 10 dBm on 433 |
| `airtime_limit_short` / `airtime_limit_long` | 868: 5 % (15 s) / 1 % (1 h) · 433: 10 % / 10 % | advanced |
| `rns_allow` | `127.0.0.1` | client-access allow-list; drives the managed firewall |
| `rns_bind` | `127.0.0.1` | the only offered value — see [Clients](#clients) |
| `ifac_netname` | unset | IFAC network name, written as RNS's `networkname` |
| `use_gps` | on | Sideband's position switch |

The IFAC passphrase lives only in `<runtime>/config/secrets.toml` (`[reticulum] ifac_netkey`),
written as RNS's `passphrase`; lhpc refuses to load that file with any group/other permission
bit (`install -m 0600 /dev/null <runtime>/config/secrets.toml` before editing). Set **both** the
network name and the passphrase or IFAC stays off; a missing key never becomes an empty key.
Pins, chip type, TCXO and PA settings are **not** settings: they come from `lhpc hardware`,
because a wrong PA or TCXO value can damage the module.

## Hardware

| `lhpc hardware` | bands | chip | driver profile |
|---|---|---|---|
| `loraham` | 433 + 868 | SX1278 / SX1276 | RESET wired (GPIO 5 / 6); tested on the air |
| `uputronics` (and `-433` / `-868`) | 433 / 868 | SX127x | no RESET line — soft reset; tested on the air |
| `waveshare-433` | 433 | SX1262 | tested on the air |
| `waveshare-868` | 868 | SX1262 | **not tested on silicon** |

**SX1262 (Waveshare LoRaWAN Node HAT).** The LF and HF boards are pin-identical (CS 21, IRQ 16,
RESET 18, BUSY 20, TXEN 6) and both carry an SX1262 — 433 does not imply an SX1268. DIO2 drives the
RF switch. The profile hints a 1.8 V TCXO on DIO3 and the driver **probes** it: a board without a
TCXO reports `XOSC_START_ERR` and would sit in `STBY_RC` with `SetTx` accepted but never started,
so the driver falls back to the crystal and logs one notice. The BUSY line is read through
libgpiod 2.x, whose `gpiod.line.Value` is not int-convertible — the driver reads its `.value`
(the SX1262 is the only profile with a BUSY line); a BUSY stuck high for 1 s is a radio error.

## Position (GPS)

Only Sideband reads position: its location plugin (`lhpc_location.py` from the driver, enabled
by `enable_sideband_plugins.py` at build) reads `LHPC_LOCATION_CONF` →
`<runtime>/state/sideband/location.conf`, generated at stack start from the global plan
(source, gpsd host/port, NMEA device/baud, fixed lat/lon/alt, `max_age` 30 s — all
controller-owned). A change to `lhpc gps` takes effect on the next start. `rns`, nomadnet and
lxmd read none, so a start without Sideband brings up no feed. Model: [GPS](../gps.md).

## Clients

**NomadNet** is an ncurses browser: lhpc shows the command (Dashboard card, `lhpc status
reticulum`) and you run it in a terminal. The generated command wraps it in
`loraham-rns-client --configdir <state/reticulum> --wait 10 -- .venv/bin/nomadnet …`, a guard that
proves an authenticated shared instance exists (exit 3 otherwise); `lxmd` and Sideband launch
through the same guard. Never run `.venv/bin/nomadnet` directly against the owner's config: with
`rns` absent, Reticulum makes the first process the shared-instance owner, so NomadNet would load
the LoRa interface and take the radio outside lhpc's arbitration. While it is open your node serves
its pages and files.

**Client access (TCP 4242)** is what Sideband and MeshChat attach to. It is loopback-only: the
setting offers no other value and the node refuses a non-loopback bind even from a hand-edited
config, because the port has no authentication of its own and an allow-list is firewall intent,
not enforcement. From another machine use an [SSH tunnel](../ssh-tunnel.md); add IFAC keys to
authenticate the interface itself.

**Sideband** is best run off the Pi (~277 MB resident, needs a display), pointed at the
client-access port. On-box it is installed only where `bootstrap-deps.sh --with-gui` has run —
gated by `python3-dev` (`sbapp` pulls `materialyoucolor`, a C++ extension without an aarch64 wheel)
and `libx11-dev` (the `--with-gui` marker; Kivy vendors its own SDL2); without `--with-gui` it is
skipped, never a build error.

## Band limits

| band | default | limit | licence |
|---|---|---|---|
| 868 | 868.500 MHz | 25 mW ERP (14 dBm), duty per sub-band | none (SRD) |
| 433 | 434.500 MHz | **10 mW ERP (10 dBm)**, 10 % duty | none (SRD / LPD433) |

The 433 default stays clear of the LoRaHAM APRS channel (433.775/433.900) and MeshCom (433.175)
while remaining inside 433.050–434.790 MHz. 10 mW is ERP — it includes antenna gain: 10 dBm into a
unity-gain whip is just inside; with a gain antenna turn the power down. The stack cannot know your
antenna.

**Permitted segments** (driver `PERMITTED_SEGMENTS`, BNetzA Vfg. 91/2025 / ERC 70-03). The driver
knows them and their hourly duty ceilings and refuses anything else; an operator limit may only
tighten a ceiling, never widen it:

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

The **whole occupied bandwidth** must fit inside one segment: 869.500 MHz is legal at 125 kHz but
not at 500 kHz, which spills outside the 250 kHz-wide 10 % segment. A channel that matches no
segment is refused, never defaulted.

**Duty cycle.** Airtime is reserved *before* transmitting and written to disk, so a restart or a
crash loop cannot wipe the hour's accounting. An unconfirmed transmission stays charged. Corrupt
accounting state blocks transmit but never receive.

## Notes

| symptom | cause |
|---|---|
| start fails, "another Reticulum shared instance already owns this configuration" | a stray `rnsd`/node is running; stop it |
| start fails, "interface … was not registered" | the radio or the SPI lock failed — the log above it names which |
| start fails, "refusing to expose an unauthenticated RNS interface" | a non-loopback bind |
| "SPI bus lock not acquired within 2s" | a peer holding `spi0.lock` is wedged (usually a stuck daemon) |
| TX seems to stall | the duty-cycle limiter is holding packets; check the airtime limits |

- `rnstatus` interface counters are the RX/TX evidence; `Valid announce` is not logged at the
  default log level, so grepping the node log proves nothing.
- Every SPI transaction takes the daemon's `spi0.lock` (bounded, 2 s), so sharing
  `/dev/spidev0.0` with the daemon on the other band is safe.

## Conflicts

- The stack claims its band **exclusively**: not with the daemon serving that band (and so not
  with its clients), not with meshtastic on that band. Opposite bands coexist — the 433 daemon and
  Reticulum on 868 run together.
- `spi.bus.0.unlocked`: `meshtastic + reticulum` is refused on any band pair, because meshtasticd
  drives the bus without the lock ([architecture](../architecture.md)).
