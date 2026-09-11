# Stack: LoRaHAM daemon

The hardware owner behind every daemon-backed stack (kiss, graywolf, chat, voice, meshcom,
meshcore): one `loraham_daemon` process per served band, 433 and/or 868 MHz, each exposing the
sockets its clients use. It never transmits on its own.

| | |
|---|---|
| Components | `loraham-daemon` (main) · `radiolib` — the RadioLib static library, a build-time dependency (`build_requires`), never started |
| Source / pin | `src/loraham-daemon` ← `makrohard/LoRaHAM_Daemon` `v0.9.0` (`ff3c26a4…`) · `src/RadioLib` ← `jgromes/RadioLib` `7.7.1-57-g187ef247` |
| Run | `loraham_daemon/loraham_daemon --radio <band> --hw <preset> --tx-mode managed\|direct --cad-monitor off\|on --cad-rssi <dBm>` — one process per band; lhpc computes every value at spawn |
| Sockets (per band) | `/tmp/loraconf<band>.sock` (CONF / status), `/tmp/lora<band>f.sock` (framed), `/tmp/lora<band>.sock` (raw) — `LORAHAM_SOCKET_DIR=/tmp` |
| State | `<runtime>/state/loraham` (mode 0700, `LORAHAM_RUNTIME_DIR`) — the lock files, including `spi0.lock` |
| Hardware | `/dev/spidev0.0` + GPIO (`/dev/gpiochip0`); the `--hw` preset comes from `lhpc hardware` |
| Resources | `spi.bus.0` cooperative · `loraham.radio.433` / `.868` provider · `loraham.daemon-socket.433` / `.868` provider |
| Install channel | **binary** by default (`lhpc install daemon`: a sha256-verified prebuilt that replaces the daemon + RadioLib builds); `--source pinned\|dev\|stable` clones the sources and `lhpc build daemon` runs `loraham_daemon/build.sh` (needs `cmake`, `liblgpio-dev`, `build-essential`). Policy: [provenance](../provenance.md); operator consequences: [operations](../operations.md) |

## Contents

- [Settings](#settings)
- [Radio parameters](#radio-parameters)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

**Hardware setup** — `lhpc hardware <setup>`, or the daemon stack's *Hardware* section in the
console (with a *Detect* probe). It fixes the served band(s) and the `--hw` preset per band:
`loraham` (433 + 868), `uputronics` (CE0 433 + CE1 868; `uputronics-x` = crossed),
`uputronics-433` / `-868`, `waveshare-433` / `-868` (SX1262). A fresh install is `unset` and the
daemon refuses to start until one is chosen; the catalog is in [cli](../cli.md).

**Stack params** — `lhpc config daemon <param> <value>` or the Settings panel:

| param | default | meaning |
|---|---|---|
| `tx_433` / `tx_868` | `managed` | TX mode per band. `MANAGED` = bounded CAD/LBT, a busy channel returns `CHANNEL_BUSY`; `DIRECT` = immediate TX, no CAD |
| `cadmon_433` / `cadmon_868` | `off` | continuous channel-activity monitor |
| `cadrssi_433` / `cadrssi_868` | `-90` | channel-busy RSSI threshold, dBm (−130…0) |

A client stack declares the TX mode it needs (`requires_daemon_tx`: MANAGED for kiss, graywolf,
chat, meshcom and meshcore; DIRECT for voice), and lhpc applies it live when that stack starts.
Live changes without a restart: `lhpc daemon <band> --set TXMODE=DIRECT` — a CONF `SET` followed
by a `GET STATUS` read-back; only whitelisted keys are accepted (TXMODE, TXQUEUE, TXRESULT, CAD*, GETRSSI, the radio params). `lhpc daemon <band> --feed` shows recent RX/TX activity.

## Radio parameters

Every daemon client (chat, kiss, voice, meshcom, meshcore) and the daemon itself carry a per-band
radio-parameter profile (`lhpc/core/daemon_params.py`). lhpc applies a stack's profile to the
daemon **once**, after the daemon reports READY and before the stack's components start.

| group | params | ranges |
|---|---|---|
| radio | `MODE`, `FREQ`, `SF`, `BW`, `CR`, `CRC`, `LDRO`, `PREAMBLE`, `SYNC`, `POWER` | LORA/FSK · 150–960 MHz · 7–12 · 7.8–500 kHz · 5–8 · 0/1 · AUTO/0/1 · 6–65535 symbols · hex byte · 0–20 dBm |
| listen-before-talk | `TXMODE`, `TXQUEUE`, `CADMONITOR`, `CADRSSI`, `CADWAIT`, `CADIDLE`, `CADTXAFTERTIMEOUT` | MANAGED/DIRECT · 0/1 · 0/1 · −130…0 dBm · 50–5000 ms · 0–2000 ms · 0/1 |

The client app re-`SET`s its own radio params and `TXMODE` when it connects, so those rows are
**app-owned**: lhpc still applies them, the app overwrites them, and the panel greys them.
`CADWAIT`, `CADIDLE`, `TXQUEUE`, `CADMONITOR`, `CADRSSI` and `CADTXAFTERTIMEOUT` are
operator-owned and stick. A profile is the app's own default, not a legal ceiling: the licensed
stacks carry amateur-service settings, while the licence-free ones (meshcore, reticulum) sit
inside the SRD limits in [reticulum](reticulum.md#band-limits). Defaults, taken from each app's
source:

| stack | 433 | 868 | both bands |
|---|---|---|---|
| daemon (base) | 433.175 MHz | 869.525 MHz | MANAGED, SF12, BW125, CR5, CRC on, preamble 8, sync 0x12, 17 dBm |
| chat, kiss | — | — | the LoRaHAM amateur profile: MANAGED, SF12, BW125, CR5, CRC on, preamble 8, sync 0x12, 17 dBm |
| voice | DIRECT, 434.700, SF7, BW125, CR5, preamble 8, sync 0x12, 17 dBm | DIRECT, 869.525, SF11, BW250, CR5, preamble 16, sync 0x2B, 10 dBm | CRC on |
| meshcom | 433.175, SF10, BW125, CR6, 17 dBm | 869.525, SF11, BW250, CR6, 10 dBm | MANAGED, CRC on, preamble 8, sync 0x2B, CADIDLE 28 ms |
| meshcore | — | MANAGED, 869.618, SF8, BW62.5, CR8, CRC on, preamble 16, sync 0x12, 14 dBm | — |
| base LBT (every stack) | | | CADWAIT 1500 ms, CADIDLE 250 ms, TXQUEUE 1, CADMONITOR 0, CADRSSI −90, CADTXAFTERTIMEOUT 0 |

Where they are set: the **Daemon radio parameters** panel under each stack's Settings (it follows
the band switch; *Save* persists, *Apply* pushes to the running daemon, *Reset* restores the
defaults), or the CLI — `<stack>` may be `daemon` itself:

```
lhpc config <stack> --band <433|868> --daemon-param KEY=VALUE [--daemon-param ...]   # persist
lhpc config <stack> --band <433|868> --apply-daemon                                  # push live (the stack must be running)
lhpc config <stack> --band <433|868> --reset-daemon
```

Every value is validated and canonicalised server-side (`daemon_control.validate_set`). An apply
is `ok` only when every SET landed; the radio params are applied to the chip but echoed by no
`GET`, so they report *sent*, not confirmed. `MODE=FSK` switches LoRa off and breaks every stack.

## Position (GPS)

The daemon reads no position. The stacks that do are listed in [GPS](../gps.md).

## Notes

- **Readiness** is a read-only `GET STATUS` on the CONF socket →
  `STATUS RADIO=READY|FAILED|UNINITIALIZED … TXMODE=…`. Only `READY` counts; a reachable socket
  alone does not. No side effects, no TX.
- TX is never enabled by lhpc itself — [TX safety](../operations.md#tx-safety).
- The daemon runs without `-d` (no double fork), so it stays an LHPC-owned process —
  [identity-verified stopping](../architecture.md#safety-model).

## Conflicts

- A **direct-SPI radio owner** (meshtastic, reticulum) claims `loraham.radio.<band>` exclusively:
  it cannot run while the daemon serves that band, and the daemon cannot start on a band such an
  owner holds. Opposite bands coexist (daemon 433 + reticulum 868).
- The SPI bus is shared cooperatively through `spi0.lock`; the model and the one non-participant
  are in [architecture](../architecture.md).
- Client stacks obey a **one app stack per band** rule — see [kiss](kiss.md).
