# Stack: LoRaHAM daemon

The radio owner behind every daemon-backed stack (kiss, graywolf, chat, voice, meshcom, meshcore):
one `loraham_daemon` process per served band (433/868 MHz). It never transmits on its own.

| | |
|---|---|
| Components | `loraham-daemon` (main) · `radiolib` — the RadioLib static library, a build-time dependency (`build_requires`), never started |
| Source / pin | `src/loraham-daemon` ← `makrohard/LoRaHAM_Daemon` · `src/RadioLib` ← `jgromes/RadioLib` |
| Run | `loraham_daemon/loraham_daemon --radio <band> --hw <preset> --tx-mode managed\|direct --cad-monitor off\|on --cad-rssi <dBm> [--high-power] --rflog on\|off --rflog-path <runtime>/logs/rf-daemon-<band>.log` — one process per band; lhpc computes every value at spawn |
| Sockets (per band) | `/tmp/loraconf<band>.sock` (CONF / status), `/tmp/lora<band>f.sock` (framed), `/tmp/lora<band>.sock` (raw) — `LORAHAM_SOCKET_DIR=/tmp` |
| State | `<runtime>/state/loraham` (mode 0700, `LORAHAM_RUNTIME_DIR`) — the lock files, including `spi0.lock` |
| Hardware | `/dev/spidev0.0` + GPIO (`/dev/gpiochip0`); the `--hw` preset comes from `lhpc hardware` |
| Resources | `spi.bus.0` cooperative · `loraham.radio.433` / `.868` provider · `loraham.daemon-socket.433` / `.868` provider |
| Install channel | **binary** by default (`lhpc install daemon`: a prebuilt replacing the daemon + RadioLib builds); `--source pinned\|dev\|stable` clones the sources and `lhpc build daemon` runs `loraham_daemon/build.sh` (needs `cmake`, `liblgpio-dev`, `build-essential`). Policy: [provenance](../provenance.md#the-binary-channel); operator consequences: [operations](../operations.md#install-channels) |

## Contents

- [Settings](#settings)
- [Radio parameters](#radio-parameters)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

**Hardware setup** — `lhpc hardware <setup>` or the console's daemon *Hardware* section sets the
served band(s) and `--hw` preset ([cli](../cli.md#hardware)). A single-radio setup blocks stacks
needing the absent band (`meshcore`: 868). A fresh install is `unset`; the daemon refuses to start
until a setup is chosen.

**Stack params** — in `config/stacks/daemon.toml`. `lhpc config daemon` exposes none of them:
`hipower_<band>` is set in the Hardware settings or with `lhpc hardware --high-power <band> on|off`,
`rf_log` on the daemon's log page, the rest in that file.

| param | default | meaning |
|---|---|---|
| `tx_433` / `tx_868` | `managed` | TX mode per band. `MANAGED` = bounded CAD/LBT, a busy channel returns `CHANNEL_BUSY`; `DIRECT` = immediate TX, no CAD |
| `cadmon_433` / `cadmon_868` | `off` | continuous channel-activity monitor |
| `cadrssi_433` / `cadrssi_868` | `-90` | channel-busy RSSI threshold, dBm (−130…0) |
| `hipower_433` / `hipower_868` | `off` | +20 dBm permission per band, strict `off`/`on`: `on` adds `--high-power` at the band's next daemon start, admitting exactly `POWER=20` on SX127x (inert on SX1262). Saving marks the daemon restart-required and restarts nothing; a running daemon keeps the permission it started with. Datasheet limits for +20 dBm: duty cycle ≤ 1 %, VSWR ≤ 3:1, VDD 2.4–3.7 V — nothing measures or enforces them; keep the chip cooled, **warranty void if disregarded**. LoRaHAM 433 RFM98PW: no sustained +20 dBm — unvalidated with the module's external PA; the release test matrix sends no +20 dBm on it |
| `rf_log` | `on` | RF log, one file per band (`logs/rf-daemon-433.log`, `-868.log`): every frame received (RSSI/SNR) or sent (after `transmit()` returned OK — a CAD-refused send writes nothing), raw hex + ASCII. Switch semantics: [maintenance](../maintenance.md#rf-logs) |

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
the board in the Hardware settings plus the saved switch; **live** requests against the running
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
| voice | DIRECT, 434.700, SF7, BW125, CR5, preamble 8, sync 0x12, 17 dBm | DIRECT, 869.525, SF11, BW250, CR5, preamble 16, sync 0x2B, 10 dBm | CRC on |
| meshcom | 433.175, SF10, BW125, CR6, 17 dBm | 869.525, SF11, BW250, CR6, 10 dBm | MANAGED, CRC on, preamble 8, sync 0x2B, CADIDLE 28 ms |
| meshcore | — | MANAGED, 869.618, SF8, BW62.5, CR8, CRC on, preamble 16, sync 0x12, 14 dBm | — |
| base LBT (every stack) | | | CADWAIT 1500 ms, CADIDLE 250 ms, TXQUEUE 1, CADMONITOR 0, CADRSSI −90, CADTXAFTERTIMEOUT 0 |

Set them in the **Daemon radio parameters** panel of each stack's Settings (*Save* persists,
*Apply* pushes live, *Reset* restores defaults) or with the CLI (`<stack>` may be `daemon`):

```
lhpc config <stack> --band <433|868> --daemon-param KEY=VALUE [--daemon-param ...]   # persist
lhpc config <stack> --band <433|868> --apply-daemon                                  # push live (the stack must be running)
lhpc config <stack> --band <433|868> --reset-daemon
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
