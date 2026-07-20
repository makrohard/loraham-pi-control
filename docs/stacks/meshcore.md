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

## Headless systems

MeshCore is fully usable without a graphical environment: `meshcore-pi` and `meshcore-cli` have no
GUI dependencies. Only the optional **Node Manager** (`meshcore-nodegui`) is a Tkinter application
and needs the host's `python3-tk` — its venv is built without `--system-site-packages`, so the
package must be present on the system.

`bootstrap-deps.sh` omits GUI dependencies by default. On a headless box the Node Manager is skipped
and the rest of the stack installs, builds and runs normally. Add `--with-gui` on a machine with a
display to include it.
