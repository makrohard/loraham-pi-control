# Stack: KISS/TCP TNC

A daemon-backed AX.25/KISS-over-TCP bridge for APRS clients on 433 (default) or 868 MHz. A daemon
socket consumer, never a radio owner: every frame goes out through the daemon in MANAGED mode.

| | |
|---|---|
| Components | `loraham-kiss-tnc` (main) · `loraham-kiss-serial` (optional socat PTY, needs `socat`) |
| Source / pin | `src/loraham-kiss-tnc` ← `makrohard/loraham-kiss-tnc` `v0.5.1` (`3c4461e4…`) |
| Run | `./loraham-kiss-tnc --config loraham_kiss_tnc.conf.example` + the params below as flags (`--kiss-port`, `--bind`, `--kiss-host`, `--rx-freq`, `--tx-freq`, `--data-socket`, `--conf-socket`, `--rx-only`, `--verbose`) |
| Endpoints | KISS over TCP `127.0.0.1:8001` (no auth — `--bind` is the only gate) · serial PTY `<runtime>/state/loraham_kiss` (the socat component, `socat PTY,link=… TCP:127.0.0.1:8001`) |
| Resources | `tcp.port.8001` exclusive · `loraham.daemon-socket.<band>` consumer · `serial.loraham-kiss` exclusive (PTY) |
| Depends on | `loraham-daemon`, `requires_daemon_tx = MANAGED` |
| Install channel | source only — `bash build.sh` |

## Contents

- [Settings](#settings)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

| param | default | notes |
|---|---|---|
| `kiss_port` | `8001` | |
| `rx_freq` / `tx_freq` | 433: `433.775` / `433.775` · 868: `869.525` / `869.525` | 433 is single-channel on 433.775 both ways — what stock ESP32 trackers (CA2RXU, OE5BPA) transmit and listen on; the classic RX 433.775 / TX 433.900 split is set here if wanted. 868 uses the 869.4–869.65 MHz sub-band |
| `kiss_bind` | `127.0.0.1` | allow-list — a LAN CIDR or `0.0.0.0/0` exposes the listener (no auth); drives the managed firewall |
| `kiss_host` | `127.0.0.1` | bind host (advanced) |
| `data_socket` / `conf_socket` | per band | advanced |
| `rx_only` | off | `lhpc config kiss rx_only on` — the TNC never transmits; visible in its argv and read by `lhpc status` |
| `verbose` | off | |

Radio parameters (the LoRaHAM amateur profile, SF12/BW125) live in [daemon](daemon.md).

## Position (GPS)

None — the TNC carries no position; an APRS position belongs to its client ([graywolf](graywolf.md)).

## Notes

- **One KISS client.** The TNC bridges exactly one KISS/TCP client to the daemon at a time; after
  a disconnect it waits for the next. Not a declarable resource, so not reslock-enforced.
- **One app stack per band.** The `loraham.daemon-socket.<band>` consumer claim records the
  dependency only; what refuses a second daemon client on a band is the start gate's
  same-frequency rule — "must be stopped first". Resource model:
  [architecture](../architecture.md#radios-bands-and-resource-claims).

## Conflicts

- `loraham-kiss-serial` and [graywolf](graywolf.md) cannot share the TNC — single client.
- Same band: chat, voice, meshcom (433) and meshcore (868) compete for the band the TNC uses.
