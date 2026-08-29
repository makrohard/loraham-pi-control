# Stack: MeshCore (868)

Daemon-backed MeshCore node on 868 MHz. Consumes the 868 daemon sockets, requires the
daemon in `MANAGED` mode, claims no direct SPI.

**MeshCore on LHPC is powered by [openHop Core](https://github.com/openhop-dev/openhop_core)
running on the LoRaHAM daemon.** openHop Core provides the MeshCore protocol implementation
(RF packet handling, crypto, routing, ACK/PATH/TRACE, transport/scoped routing, the Companion
protocol, and contact/channel/message semantics). LHPC ships a small host application
(`meshcore_host`) that adapts openHop's radio interface to the LoRaHAM daemon sockets and
owns host policy — identity, GPS, persistence, readiness, lifecycle. This replaced the
former standalone `meshcore-pi` node; LHPC no longer maintains a MeshCore fork of its own.

| | |
|---|---|
| Components | `meshcore-node` (openHop node), optional `meshcore-webui` (browser GUI), `meshcore-cli` (REPL tool) |
| Source | `openhop-core` — a managed clone of openHop Core under `<runtime>/src` (pinned commit), plus one small LHPC-shipped patch applied idempotently at build; the `.venv` is built in-tree by `lhpc build meshcore` and the `meshcore_host` host application (shipped with LHPC) is installed into it |
| Node run | `.venv/bin/python -m meshcore_host <runtime>/config/files/meshcore.toml` |
| Config | `meshcore.toml` (mode `0600` — it carries the node's private key): preset, node name, txpower, frequency/SF/BW/CR, airtime, allow-list, port, persistence DB, GPS |
| Identity | `<runtime>/config/secrets/meshcore_identity.key` (mode `0600`) |
| Persistence | `<runtime>/state/meshcore/companion.db` (SQLite; contacts, channels, learned routes, prefs, offline messages survive restart) |
| Companion | TCP `:5000` |
| Optional | `meshcore-webui` — browser GUI (adradr/meshcore-webui) reached through the LHPC TLS/PKI proxy; `meshcore-cli` — interactive REPL (run yourself) |

Daemon interface: `GET STATUS`, `SET TXMODE=MANAGED`. A future direct-SX1262 profile
would own SPI exclusively and conflict with the daemon; it is not the default.

## openHop Core source

The `meshcore-node` source is upstream openHop Core, pinned to a known-working commit, plus
one small LHPC-shipped patch (a focused, upstream-mergeable ACK-delivery fix; see
`lhpc/data/patches/`). The patch is applied idempotently by a build step — a refreshed
checkout is patched, a rebuilt one is left as-is, and a genuine conflict fails the build
rather than running unpatched code. The source therefore stays a pristine upstream tree plus
a reviewable diff, never a fork. Source/version selection follows openHop's release
discipline: `dev` (Development), `main`/PyPI (Latest stable), and the pinned commit (Known
working). Because the checkout carries the patch, `lhpc status` reports its source as
`dirty` — that is expected, not a fault.

## Node identity

The private key **is** the node's identity: adverts are signed with
it and contacts recognise the node by the matching public key. LHPC owns the key — it lives at
`<runtime>/config/secrets/meshcore_identity.key` (`0600`) and is written into the generated
config on every regeneration. The public key therefore survives restart, rebuild, update and
reinstall.

On the first run LHPC **adopts** a key that is already there — from the generated config
(the new `[identity] key`, or a legacy `[device.companion] privatekey` written by the former
`meshcore-pi` generation), and only mints one if none has a usable key. The openHop host
application itself **never** mints: a missing or malformed identity is a hard startup
failure, so the node can never silently come up as a stranger. A key that is present but malformed **blocks** the operation rather than
being replaced: rotating an identity silently is never the right recovery. Fix or remove the
value deliberately.

The generated config is `0600` because it contains the key. Keep it that way, and do not
paste its `privatekey` line anywhere.

## Position

`lhpc gps` is the single source, and `lhpc config meshcore use_gps on|off` (default `on`)
decides whether this stack uses it.

A **live** source is fed to the node continuously by a `meshcore-gps` bridge, which
publishes the global position source as a normalized position feed (line-JSON on a Unix
socket) the node consumes. The node's position therefore follows the box while it runs,
rather than freezing at whatever it was when the stack started. (The node needs a position,
not a simulated GPS chip, so the feed carries `{"fix": true, "lat": …, "lon": …}` /
`{"fix": false}` records rather than raw NMEA — no chip-probe emulation is required.)

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
the bridge, never by the node, so there is only ever one reader on the hardware. The bridge
is the single place that parses coordinates out of NMEA; the node receives only the
normalized position, and neither side logs coordinates.

## Headless systems

MeshCore is fully usable without a graphical environment. The retired Tk **Node Manager** needed
an X server and `python3-tk`, so it was skipped on a headless box; the replacement
**`meshcore-webui`** is a browser GUI with **no graphic dependency at all** — it installs and runs
on a headless Pi and is reached from any browser (desktop or phone) through the LHPC web proxy.
The MeshCore stack therefore has no gui-gated component: `lhpc build meshcore` builds everything on
a headless box.

## MeshCore Web UI

`meshcore-webui` is an ordinary **client of the MeshCore Companion TCP endpoint** (127.0.0.1:5000):
a FastAPI/uvicorn backend that connects to the openHop-backed node and serves a prebuilt React SPA.
LHPC keeps ownership of identity, GPS, the LoRa radio, lifecycle, PKI, the proxy and the firewall;
the GUI never becomes a second owner of those.

* **Runtime** — the Python backend runs natively (no Docker, no runtime `pip`/`npm`); the React
  frontend is **prebuilt** and shipped with LHPC as package data (a Pi Zero cannot run the Node
  build). `meshcore_py` is pinned for a reproducible Companion client.
* **Exposure** — the backend binds **loopback only**; the sole public path is the LHPC nginx
  reverse proxy (TLS, optional mTLS, CIDR gate), enabled per stack on the **Webserver** page.
* **Security boundary** — the proxy **refuses** (`403`) the operations LHPC owns: factory reset
  (would destroy the managed identity), radio / TX-power / tuning (the daemon owns the LoRa
  config), position (LHPC owns the GPS policy), device name, and the device-touching admin reset.
  Enforced server-side at the perimeter, not in JavaScript; the backend has no private-key
  import/export endpoint. Everything else — messages, contacts, channels, TRACE, adverts — is
  ordinary Companion-client traffic and passes through.
* **State** — the WebUI keeps its OWN SQLite store (message history, GUI preferences, tile cache)
  under `<runtime>/state/meshcore-webui/`. It is a cache/display layer; the authoritative MeshCore
  identity, contacts, channels and routes live in the node, never in the WebUI database.

