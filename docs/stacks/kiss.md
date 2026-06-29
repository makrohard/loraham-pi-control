# Stack: KISS/TCP TNC

Daemon-backed AX.25/KISS bridge for APRS clients. Band-switchable (433/868); a daemon
socket consumer, not a radio owner. TX goes through the daemon.

| | |
|---|---|
| Component | `loraham-kiss-tnc` |
| Build | `loraham_kiss_tnc/build.sh` |
| TCP | `:8001` |
| Per-band config | `data_socket /tmp/lora<band>f.sock`, `conf_socket /tmp/loraconf<band>.sock`; 433: RX 433.775 / TX 433.900; 868: 869.525 |
| Optional | `loraham-kiss-serial` — socat PTY `/tmp/loraham_kiss` ↔ TCP 8001 (needs `socat`) |
| Resources | `tcp.port.8001` exclusive; `loraham.daemon-socket.<band>` consumer |
