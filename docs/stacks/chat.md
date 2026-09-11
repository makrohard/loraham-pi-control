# Stack: Chat (433)

The interactive daemon-backed APRS/chat TUI (`loraham_chat`) on 433 MHz, transmitting through the
daemon in MANAGED mode. It needs a real terminal — there is no headless mode.

| | |
|---|---|
| Component | `loraham-chat` (interactive; readiness manual) |
| Source / pin | `src/LoRaHAM_Daemon` ← `makrohard/LoRaHAM_Daemon` `v0.9.0`, single-file artifact `clients/chat/lorachat_ncurses_113.c` |
| Build | `gcc clients/chat/lorachat_ncurses_113.c -o loraham_chat -lncurses -lpthread` (needs `libncurses-dev`) |
| Run | `<source>/loraham_chat` from `<runtime>/config/files`, so it reads the seeded config; `lhpc stack start chat` ensures the daemon (433, MANAGED) and prints the command — you run it, locally or over SSH |
| Config | `<runtime>/config/files/lorachat.conf` (`KEY=VALUE`); the in-app Ctrl-K menu saves back to it |
| Sockets | `/tmp/lora433.sock`, `/tmp/loraconf433.sock` (hard-coded 433) |
| Install channel | source only |

## Contents

- [Settings](#settings)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

| param | key | default | notes |
|---|---|---|---|
| `call` | `CALL` | inherits the global operator base callsign while empty | base 3–6 characters + optional APRS SSID `-1`…`-15` (bare = SSID 0); with neither a local nor a global callsign the start is refused |
| `tx_freq` | `TX` | `433.775` | MHz |
| `rx_freq` | `RX` | `433.900` | MHz |
| `dest` | `DEST` | `ALL` | APRS destination |
| `aprs_path` | `PATH` | `APRS,WIDE1-1` | |

The frequency pair is the app's own default and is the tracker-facing half of the classic
LoRa-APRS split: chat transmits on 433.775, the channel stock ESP32 trackers listen on, and
receives on 433.900. `kiss` defaults to 433.775 both ways instead ([kiss](kiss.md)).

Radio parameters (the LoRaHAM amateur profile) live in [daemon](daemon.md).

## Position (GPS)

None — chat has no position setting.

## Notes

- The Dashboard card shows the same generated command the CLI prints; `lhpc` never spawns the TUI.

## Conflicts

- One app stack per band: chat holds 433 like every daemon client (kiss/graywolf, voice on 433,
  meshcom) — see [kiss](kiss.md).
