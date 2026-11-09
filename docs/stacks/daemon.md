# Stack: LoRaHAM daemon

The radio owner behind every daemon-backed stack (kiss, graywolf, chat, voice, meshcom, meshcore):
one `loraham_daemon` process per enabled radio of the hardware list (433/868 MHz). It never transmits on its own.

| | |
|---|---|
| Components | `loraham-daemon` (main) · `radiolib` — the RadioLib static library, a build-time dependency (`build_requires`), never started · `libch341` — the CH341 library (the project's fork of pine64's), which the daemon's build turns into `lib/libloraham-ch341.so` for the ELECROW Meshstick; an optional build input (source checkout), adopted by default; never started |
| Source / pin | `src/loraham-daemon` ← `makrohard/LoRaHAM_Daemon` · `src/RadioLib` ← `jgromes/RadioLib` · `src/libch341-spi-userspace` ← `makrohard/libch341-spi-userspace`, the fork carrying the serial/port selection fix, at 98b656bf (the commit in the daemon's `.github/ci/libpinedio.lock`) |
| Run | `loraham_daemon/loraham_daemon --radio <band> --hw <preset> --tx-mode managed\|direct --cad-monitor off\|on --cad-rssi <dBm> [--high-power] --ocp <mA> --rflog on\|off --rflog-path <runtime>/logs/<rid>/rf-daemon.log --id <rid> [--usb-port <port>]` — one process per radio; lhpc computes every value at spawn |
| Sockets (per radio) | `/tmp/loraconf-<rid>.sock` (CONF / status), `/tmp/loraf-<rid>.sock` (framed), `/tmp/lora-<rid>.sock` (raw) — `LORAHAM_SOCKET_DIR=/tmp` |
| State | `<runtime>/state/loraham` (mode 0700, `LORAHAM_RUNTIME_DIR`) — the lock files, including `spi0.lock` |
| Hardware | `/dev/spidev0.0` (soft chip selects) or `/dev/spidev0.0`/`0.1` (kernel chip selects, [deps](../cli.md#deps)) + GPIO (`/dev/gpiochip0`); the `--hw` preset comes from `lhpc hardware` |
| Resources | `spi.bus.0` cooperative · `loraham.radio.{radio:<band>}` / `loraham.daemon-socket.{radio:<band>}` provider, resolved to a radio through the hardware list |
| Install channel | **binary** by default (`lhpc install daemon`: a prebuilt replacing the daemon + RadioLib builds); `--source pinned\|dev\|stable` clones the sources and `lhpc build daemon` runs `loraham_daemon/build.sh` (needs `cmake`, `liblgpio-dev`, `build-essential`, `libusb-1.0-0-dev`). The binary carries `loraham_daemon/lib/libloraham-ch341.so` beside the daemon, which loads it from there for the Meshstick; running it needs `libusb-1.0-0`. A source build without the library clone has no Meshstick support until `lhpc install daemon` adopts it; with the clone it needs `libusb-1.0-0-dev`, and the build stops naming it when the headers are missing. Policy: [provenance](../provenance.md#the-binary-channel); operator consequences: [operations](../operations.md#install-channels) |

## Contents

- [The hardware list (0.14.0)](#the-hardware-list-0140)
- [Settings](#settings)
- [Radio parameters](#radio-parameters)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## The hardware list (0.14.0)

From 0.14.0 the hardware list, `config/radios.toml`, names each radio and its band. A daemon is started for
each enabled radio of the list on a band the board setup serves (`lhpc hardware <setup>`, saved in
`config/local.toml`), one process per radio, with its own sockets (`/tmp/loraconf-<rid>.sock`, `lora-<rid>`, `loraf-<rid>`), its
`--id <rid>` and its RF log `logs/<rid>/rf-daemon.log`. `lhpc stack start|stop|restart daemon --radio rN` acts on
one radio's daemon alone. A radio on a band the
setup does not serve is not started: launching every enabled radio of the list whatever the setup is not in
this release.

- **Cutover:** an updated box keeps its old `[radio]` setup in `config/local.toml`, and nothing is converted.
  Until the list exists, no daemon starts and the console shows a banner. `lhpc hardware <setup>` writes the
  list for the setup (one radio per band) when there is none; an existing list is never changed by it.
- **USB radios:** the console's Hardware page (*Apps › Daemon › Hardware*, `/hardware`) lists each radio with its
  own daemon's state; *Add Hardware* adds a listed USB stick or a HAT as a new radio, a radio's detail page re-binds
  it to a stick, and the page offers the one-time USB setup as one command
  ([USB radios](../usb-radios.md)).
- **Two radios on one band:** two radios on one band each run their own daemon: address one by its radio id
  (`lhpc stack stop daemon --radio rN`). A client starts its own radio's daemon; a per-band stop stops the clients
  on the band's radios, then each radio's daemon.
- x86 hosts: not yet verified.

### The list file

`config/radios.toml` holds only `[[radio]]` rows. The console and `lhpc hardware` write it; a hand edit is
checked whole at every read. A list that cannot be read, is not valid TOML, or breaks any rule below is
*invalid*, and no daemon starts until it is fixed (the reasons name their row).

| field | required | value |
|---|---|---|
| `id` | yes | `r1` … `r99`; given once, the lowest free number, never renamed |
| `name` | yes | 1–12 letters, digits, space `.` `_` `-`; unique, case ignored |
| `model` | yes | a model of the model table (`lhpc/data/radio_models.toml`), e.g. `loraham`, `meshstick-1262` |
| `band` | yes | `433` or `868`, one the model has |
| `enabled` | no | `true` (default) / `false` |
| `ce` | for a model with more than one variant on that band | the variant key (Uputronics: `ce0` / `ce1`) |
| `usb_serial` | a USB stick with a serial | 8–64 characters, bytes 0x21–0x7E without `{` `}`; unique in its first 8 (the daemon compares 8) |
| `usb_port` | a USB stick without a serial, only then | the sysfs bus-port, like `1-1.3`; at most one such stick |
| `high_power` | no | `true` / `false`; only on a model with a high-power switch |
| `txmode` · `cadmon` · `cadrssi` | no | `managed`/`direct` · `off`/`on` · an integer −130…0 |
| `ocp_ma` | no | `"auto"` or a value the chip represents ([settings](#settings)) |
| `dp_<PARAM>` | no | a [radio parameter](#radio-parameters) for this radio's daemon, as a string; an integer without a leading zero or `+` |

Types are exact: `enabled = "true"` or `cadrssi = "-90"` is refused. The pins of every SPI row (enabled or
not) must not overlap.

**A field left out is not set** and is resolved at the daemon's start:
- `txmode`, `cadmon`, `cadrssi` and `high_power` take the band's old setting (`tx_<band>`, `cadmon_<band>`, …)
  when there is one, else the default. An old `hipower_<band>` is not carried to a model without a high-power
  switch.
- `ocp_ma` takes the old `ocp_<band>`, else `auto`.
- A `dp_<PARAM>` left out takes the old per-band value (`dp_<band>_<PARAM>`) when every client stack that holds it
  agrees; when they disagree, or a value is empty or not valid for the chip, it is not carried, and the start
  names it.
- A carried old value that breaks its rule refuses the start, naming the value and where it came from; it is
  never silently replaced.

A field that is set wins, `false` included. The row's `dp_` values reach that radio's daemon after READY and on
every Apply. Nothing is written back to the file at a start.

## Settings

**Hardware setup** — `lhpc hardware <setup>` sets the
served band(s) and `--hw` preset (the console adds radios on *Add Hardware*) ([cli](../cli.md#hardware)). A single-radio setup blocks stacks
needing the absent band (`meshcore`: 868). A fresh install is `unset`; the daemon refuses to start
until a setup is chosen.

**Stack params** — in `config/stacks/daemon.toml`. `lhpc config daemon` exposes none of them:
`hipower_<band>` is set with the daemon Settings' +20 dBm switch or with `lhpc hardware --high-power <band> on|off`,
`rf_log` on the daemon's log page, the rest in that file.

| param | default | meaning |
|---|---|---|
| `tx_433` / `tx_868` | `managed` | TX mode per band. `MANAGED` = bounded CAD/LBT, a busy channel returns `CHANNEL_BUSY`; `DIRECT` = immediate TX, no CAD |
| `cadmon_433` / `cadmon_868` | `off` | continuous channel-activity monitor |
| `cadrssi_433` / `cadrssi_868` | `-90` | channel-busy RSSI threshold, dBm (−130…0) |
| `hipower_433` / `hipower_868` | `off` | +20 dBm permission per band, strict `off`/`on`: `on` adds `--high-power` at the band's next daemon start, admitting exactly `POWER=20` on SX127x (inert on SX1262). Saving marks the daemon restart-required and restarts nothing; a running daemon keeps the permission it started with. Datasheet limits for +20 dBm: duty cycle ≤ 1 %, VSWR ≤ 3:1, VDD 2.4–3.7 V — nothing measures or enforces them; keep the chip cooled, **warranty void if disregarded**. LoRaHAM 433 RFM98PW: no sustained +20 dBm — unvalidated with the module's external PA; the release test matrix sends no +20 dBm on it |
| `ocp_433` / `ocp_868` | `auto` | PA over-current limit per band, `auto` or mA; every start passes `--ocp`. `auto` = 140 mA with the band's `hipower` on, else 100 mA. A value must be one the band's chip represents (SX127x: 0 = off, 45..120 in steps of 5, 130..240 in steps of 10; SX1262: 5..140 in steps of 5) — `lhpc hardware --ocp <band> auto\|<mA>` refuses others, and a hand-edited one blocks that band's start. Saving marks the daemon restart-required and restarts nothing |
| `rf_log` | `on` | RF log, one file per radio (`logs/<rid>/rf-daemon.log`): every frame received (RSSI/SNR) or sent (after `transmit()` returned OK — a CAD-refused send writes nothing), raw hex + ASCII. Switch semantics: [maintenance](../maintenance.md#rf-logs) |

Each client stack's `requires_daemon_tx` (DIRECT for voice, MANAGED for the rest) is applied live
when it starts. Live change without a restart ([daemon control](../architecture.md#daemon-control)): `lhpc daemon
<band> --set TXMODE=DIRECT`; accepted keys are TXMODE, TXQUEUE, TXRESULT, CAD*, GETRSSI and the
radio params. `lhpc daemon <band> --feed` shows recent RX/TX activity.

## Radio parameters

Each daemon client (chat, kiss, voice, meshcom, meshcore) and the daemon carry a per-band profile
(`lhpc/core/daemon_params.py`), applied **once** after the daemon reports READY and before the
stack's components start.

| group | params | ranges |
|---|---|---|
| radio | `MODE`, `FREQ`, `SF`, `BW`, `CR`, `CRC`, `LDRO`, `PREAMBLE`, `SYNC`, `POWER` | LORA/FSK · 150–960 MHz · 7–12 · 7.8–500 kHz · 5–8 · 0/1 · AUTO/0/1 · 6–65535 symbols · hex byte · **2–17 dBm on SX127x, 0–20 on SX1262** |
| listen-before-talk | `TXMODE`, `TXQUEUE`, `CADMONITOR`, `CADRSSI`, `CADWAIT`, `CADIDLE`, `CADTXAFTERTIMEOUT` | MANAGED/DIRECT · 0/1 · 0/1 · −130…0 dBm · 50–5000 ms · 0–2000 ms · 0/1 |

**`POWER`** (ranges above): below 2 dBm the SX127x driver uses the RFO pin instead of the
antenna's PA_BOOST pin, whose RadioLib API admits 2–17 and exactly 20 (no 18/19); 20 needs the
band's `hipower_<band>` on and the daemon restarted with it. Saved values are validated against
the radio's board (its model in the hardware list) plus the saved switch; **live** requests against the running
daemon's `STATUS` (`CHIPFAMILY=`, `HIGHPOWER=`) — a daemon reporting neither gets only 2–17. A saved
`POWER=20` applies only while the switch is on (off, the stack's default power is used); with the
switch on but the running daemon lacking the permission, the stack start is refused.
The daemon never echoes `POWER`: a sent 20 is "sent", not "confirmed".

A client app re-`SET`s its own radio params and `TXMODE` on connect, so those rows are
**app-owned** (applied, then overwritten; greyed in the panel); the LBT timing params (`CADWAIT`,
`CADIDLE`, `TXQUEUE`, `CADMONITOR`, `CADRSSI`, `CADTXAFTERTIMEOUT`) are operator-owned and stick.
A profile is the app's default, not a legal ceiling; the licence-free stacks sit inside the SRD
limits in [reticulum](reticulum.md#band-limits). Defaults:

| stack | 433 | 868 | both bands |
|---|---|---|---|
| daemon (base) | 433.175 MHz | 869.525 MHz | MANAGED, SF12, BW125, CR5, CRC on, preamble 8, sync 0x12, 17 dBm |
| chat, kiss | — | — | the LoRaHAM amateur profile: MANAGED, SF12, BW125, CR5, CRC on, preamble 8, sync 0x12, 17 dBm |
| voice | DIRECT, 434.700, SF7, BW125, CR5, preamble 8, sync 0x12, 17 dBm | DIRECT, 869.525, SF7, BW250, CR5, preamble 16, sync 0x2B, 10 dBm | CRC on |
| meshcom | 433.175, SF10, BW125, CR6, 17 dBm | 869.525, SF11, BW250, CR6, 10 dBm | MANAGED, CRC on, preamble 8, sync 0x2B, CADIDLE 28 ms |
| meshcore | — | MANAGED, 869.618, SF8, BW62.5, CR8, CRC on, preamble 16, sync 0x12, 14 dBm | — |
| base LBT (every stack) | | | CADWAIT 1500 ms, CADIDLE 250 ms, TXQUEUE 1, CADMONITOR 0, CADRSSI −90, CADTXAFTERTIMEOUT 0 |

Set them in the **Daemon radio parameters** panel of each stack's Settings (*Save* persists,
*Apply* pushes live, *Reset* restores defaults) or with the CLI (`<stack>` may be `daemon`). The daemon's own
panel is per radio (`?rid=rN`) and saves that radio's `dp_<PARAM>` in its row of `config/radios.toml`
([the list file](#the-list-file)):

```
lhpc config <stack>@<rid> --daemon-param KEY=VALUE [--daemon-param ...]   # persist
lhpc config <stack>@<rid> --apply-daemon                                  # push live (the stack must be running)
lhpc config <stack>@<rid> --reset-daemon
```

Values are validated server-side (`daemon_control.validate_set`). An apply is `ok` only when every
SET landed: a SET the daemon answers `ERR` (or does not answer) fails with its reason; radio params
the daemon accepted with `OK` are echoed by no `GET`, so they report *sent*, not confirmed. An app
stack's apply is refused, naming the stack, on a band another running stack uses; the band's radio
claim is held from that check to the last SET, so a start cannot take the band in between. `MODE=FSK`
switches LoRa off and breaks every stack.

## Position (GPS)

The daemon reads no position; the stacks that do are listed in [GPS](../gps.md).

## Notes

- **Readiness** is a read-only `GET STATUS` on the CONF socket →
  `STATUS RADIO=READY|FAILED|UNINITIALIZED … TXMODE=…`. Only `READY` counts; a reachable socket
  alone does not. No side effects, no TX.
- TX is never enabled by lhpc itself — [TX safety](../operations.md#tx-safety).
- The daemon runs without `-d` (no double fork), so it stays an LHPC-owned process —
  [identity-verified stopping](../architecture.md#safety-model).

## Conflicts

- A **direct-SPI radio owner** (meshtastic, reticulum) claims `loraham.radio.<band>` exclusively:
  it cannot run while the daemon serves that band, and vice versa. Opposite bands coexist
  (daemon 433 + reticulum 868).
- The SPI bus is shared through `spi0.lock`; model and the one non-participant:
  [architecture](../architecture.md#radios-bands-and-resource-claims).
- Client stacks obey **one app stack per band** — see [kiss](kiss.md#notes).
