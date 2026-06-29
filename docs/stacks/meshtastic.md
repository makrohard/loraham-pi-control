# Stack: Meshtastic

Rootless `meshtasticd` driving the RF95 radio directly (band-switchable; default 868).
`lhpc` starts and stops it — no sudo, no systemd. Because it owns the radio, it claims
its band exclusively and **cannot run while the daemon serves that band** (`lhpc` blocks
the conflict).

| | |
|---|---|
| Component | `meshtastic` |
| Run | `meshtasticd -c <runtime>/config/files/meshtasticd.yaml -d <runtime>/state/meshtastic` |
| Config | `meshtasticd.yaml` (per-band LoRa pins, region, web port) |
| Web UI | `:9443` (rootless can't bind 443) |
| API | `:4403` |

The runtime data dir (`-d …`) is writable, so the web TLS cert is generated there.
Region and node name are applied once after start via the device API.
