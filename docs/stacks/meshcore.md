# Stack: MeshCore (Pi)

Daemon-backed MeshCore node on 868 MHz. Consumes the 868 daemon sockets, requires the
daemon in `MANAGED` mode, claims no direct SPI.

| | |
|---|---|
| Components | `meshcore-pi` (node), optional `meshcore-nodegui` (GUI), `meshcore-cli` (REPL tool) |
| Source | `meshcore-pi` (managed clone under `<runtime>/src`; the `.venv` is built in-tree by `lhpc build meshcore`) |
| Node run | `.venv/bin/python meshcore.py <runtime>/config/files/meshcore-pi.toml` |
| Config | `meshcore-pi.toml`: preset, node name, txpower, frequency/SF/BW/CR, airtime, port |
| Companion | TCP `:5000` |
| Optional | `meshcore-nodegui` — Tkinter GUI (`lhpc`-started, needs a display); `meshcore-cli` — interactive REPL (run yourself) |

Daemon interface: `GET STATUS`, `SET TXMODE=MANAGED`. A future direct-SX1262 profile
would own SPI exclusively and conflict with the daemon; it is not the default.
