# Stack: MeshCore (Pi)

Daemon-backed MeshCore node on 868 MHz. Consumes the 868 daemon sockets, requires the
daemon in `MANAGED` mode, claims no direct SPI.

| | |
|---|---|
| Components | `meshcore-pi` (node), optional `meshcore-nodegui` (GUI), `meshcore-cli` (REPL tool) |
| Source | `meshcore-pi` (managed clone under `<runtime>/src`; the `.venv` is built in-tree by `lhpc build meshcore`) |
| Node run | `.venv/bin/python meshcore.py <runtime>/config/files/meshcore-pi.toml` |
| Config | `meshcore-pi.toml` (mode `0600` — it carries the node's private key): preset, node name, txpower, frequency/SF/BW/CR, airtime, port |
| Identity | `<runtime>/config/secrets/meshcore_identity.key` (mode `0600`) |
| Companion | TCP `:5000` |
| Optional | `meshcore-nodegui` — Tkinter GUI (`lhpc`-started, needs a display); `meshcore-cli` — interactive REPL (run yourself) |

Daemon interface: `GET STATUS`, `SET TXMODE=MANAGED`. A future direct-SX1262 profile
would own SPI exclusively and conflict with the daemon; it is not the default.

## Node identity

The private key in `[device.companion]` **is** the node's identity: adverts are signed with
it and contacts recognise the node by the matching public key. `meshcore-pi` mints a fresh
random key whenever its config carries none, so LHPC owns the key instead — it lives at
`<runtime>/config/secrets/meshcore_identity.key` (`0600`) and is written into the generated
config on every regeneration. The public key therefore survives restart, rebuild, update and
reinstall.

On the first run LHPC **adopts** a key that is already there — from the generated config, or
from the upstream template if it was pinned there by hand — and only mints one if neither
has a usable key. A key that is present but malformed **blocks** the operation rather than
being replaced: rotating an identity silently is never the right recovery. Fix or remove the
value deliberately.

The generated config is `0600` because it contains the key. Keep it that way, and do not
paste its `privatekey` line anywhere.

## Position

`lhpc gps` is the single source, and `lhpc config meshcore use_gps on|off` (default `on`)
decides whether this stack uses it.

A **live** source is fed to the node continuously by a `meshcore-gps` bridge, which
publishes the global position source as an NMEA device the node reads. The node's position
therefore follows the box while it runs, rather than freezing at whatever it was when the
stack started.

| Global source | MeshCore |
|---|---|
| `off`, or `use_gps off` | no bridge, no coordinates |
| `fixed` | the configured coordinates are written to its config; no bridge is started |
| `auto` | a bridge when a gpsd is reachable, otherwise nothing — `auto` never blocks a start |
| `gpsd` (explicit) | a bridge fed from that gpsd |
| `nmea` (direct receiver) | a bridge that owns the receiver and republishes it |

A live source and static coordinates are never combined: with a feed running, no `lat`/`lon`
are written. Otherwise the node would keep a start-time position to fall back on the moment
the feed went stale — which is precisely the stale position both sides work to clear.

The node stops advertising a position if the feed delivers no valid fix for its stale
interval, and picks up again by itself when fixes resume. A direct NMEA receiver is read by
the bridge, never by the node, so there is only ever one reader on the hardware.

## Headless systems

MeshCore is fully usable without a graphical environment: `meshcore-pi` and `meshcore-cli` have no
GUI dependencies. Only the optional **Node Manager** (`meshcore-nodegui`) is a Tkinter application
and needs the host's `python3-tk` — its venv is built without `--system-site-packages`, so the
package must be present on the system.

`bootstrap-deps.sh` omits GUI dependencies by default. On a headless box the Node Manager is skipped
and the rest of the stack installs, builds and runs normally. Add `--with-gui` on a machine with a
display to include it.

Because the Node Manager is optional, a box that never installed it is not a broken MeshCore:
`lhpc build meshcore` skips it, `lhpc source-check meshcore` does not count it against the
stack, and the stack badge reflects `meshcore-pi` and the CLI. The component itself still
reports `not-installed`, and asking for it by name is answered honestly — `lhpc build
meshcore-nodegui` says it is not installed rather than reporting nothing to do.
