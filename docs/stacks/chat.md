# Stack: Chat (433)

The interactive daemon-backed APRS/chat TUI (`loraham_chat`) on 433 MHz, transmitting through the
daemon in MANAGED mode. It needs a real terminal — there is no headless mode.

| | |
|---|---|
| Component | `loraham-chat` (interactive; readiness manual) |
| Source / pin | `src/LoRaHAM_Daemon` ← `makrohard/LoRaHAM_Daemon`, single-file artifact `clients/chat/lorachat_ncurses_113.c` |
| Build | `gcc clients/chat/lorachat_ncurses_113.c -o loraham_chat -lncurses -lpthread` (needs `libncurses-dev`) |
| Run | `<source>/loraham_chat` from `<runtime>/config/files`, so it reads the seeded config. `lhpc stack start chat` ensures the daemon (433, MANAGED) and prints the command (also on the Dashboard card); run it yourself, locally or over SSH |
| Config | `<runtime>/config/files/lorachat.conf` (`KEY=VALUE`); the in-app Ctrl-K menu saves back to it |
| History | `<runtime>/config/files/lorachat.log`, written by the client; `lhpc clean chat --purge` keeps it (the config goes) |
| Sockets | `/tmp/lora433.sock`, `/tmp/loraconf433.sock` (hard-coded 433) |
| Install channel | source only |

## Contents

- [Settings](#settings)
- [Position (GPS)](#position-gps)
- [Conflicts](#conflicts)

## Settings

| param | key | default | notes |
|---|---|---|---|
| `call` | `CALL` | inherits the global operator base callsign while empty | base 3–6 characters + optional APRS SSID `-1`…`-15` (bare = SSID 0); with neither a local nor a global callsign the start is refused |
| `tx_freq` | `TX` | `433.775` | MHz |
| `rx_freq` | `RX` | `433.775` | MHz |
| `dest` | `DEST` | `ALL` | APRS destination |
| `aprs_path` | `PATH` | `APRS,WIDE1-1` | |

433.775 both ways matches [kiss](kiss.md#settings) and stock ESP32 trackers, so chat boxes and a
Graywolf station hear each other. For the classic LoRa-APRS split: `lhpc config chat rx_freq 433.900`.

Radio parameters (the LoRaHAM amateur profile): [daemon](daemon.md#radio-parameters).

## Position (GPS)

None — chat has no position setting.

## Conflicts

- One app stack per band ([kiss](kiss.md#notes)): chat holds 433 like kiss/graywolf, voice on 433
  and meshcom.
