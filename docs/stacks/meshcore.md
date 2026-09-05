# Stack: MeshCore (OpenHop)

Daemon-backed MeshCore on 868 MHz — a chat node, a repeater, or both in one process. Consumes the 868 daemon sockets, requires the
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
| Components | `meshcore-node` (the one openhop process: chat node and/or repeater, by `mode`), optional `meshcore-webui` (browser GUI), `meshcore-cli` (REPL tool), `openhop-repeater-src` (pinned upstream repeater, build-time dependency only) |
| Source | `openhop-core` — a managed clone of openHop Core under `<runtime>/src` (pinned commit), plus one small LHPC-shipped patch applied idempotently at build; the `.venv` is built in-tree by `lhpc build meshcore` and the `meshcore_host` host application (shipped with LHPC) is installed into it |
| Node run | `.venv/bin/python -m meshcore_host <runtime>/config/files/meshcore.toml` — the same command in every mode; the file's `[repeater] role` picks the program |
| Config | `meshcore.toml` (mode `0600` — it carries the node's private key): preset, node name (the node's own, never inherited from the operator callsign — the start is refused until it is set), txpower, frequency/SF/BW/CR, airtime, allow-list, port, persistence DB, GPS |
| Identity | `<runtime>/config/secrets/meshcore_identity.key` (mode `0600`) |
| Persistence | `<runtime>/state/meshcore/companion.db` (SQLite; contacts, channels, learned routes, prefs, offline messages survive restart) |
| Companion | TCP `:5000` (chat modes) |
| Repeater dashboard | `127.0.0.1:8000` (repeater modes) — openHop's own web dashboard, a proxied page of this stack (`meshcore-meshcore-node` in the Webserver panel and `lhpc webserver proxy`); login `admin` + the password LHPC minted (stack page → Password) |
| Optional | `meshcore-webui` — browser GUI (adradr/meshcore-webui) reached through the LHPC TLS/PKI proxy; `meshcore-cli` — interactive REPL (run yourself) |

Daemon interface: `GET STATUS`, `SET TXMODE=MANAGED`. A future direct-SX1262 profile
would own SPI exclusively and conflict with the daemon; it is not the default.

## Mode: chat, chat + repeater, repeater

One openhop process runs on the radio in every mode; `mode` (the Mode switch at the top of the
stack's page, the same row under Settings → Repeater, or `lhpc config meshcore mode …`) selects
which program it is. The Apps row carries a `mode:` pill and `lhpc status meshcore` prints the
saved mode — and, while the stack runs with another one, the running mode with "restart to apply".
The mode is changed in Settings before a start (a mode change decides which identities exist),
never per start — a start runs the saved configuration. Changing it flags the stack
restart-required like any other setting. The default is `chat`, so an updated box keeps running
exactly what it ran before.

| Mode | Program | Chat node (TCP 5000) | Repeater (dashboard :8000) | Web UI / CLI | Position (GPS) |
|---|---|---|---|---|---|
| `chat` | today's Companion host (`meshcore_host`) | yes | no | available | as configured |
| `chat+repeater` | upstream `openhop_repeater`, hosting the same Companion inside it | yes | yes | available | as configured (the Companion reads it) |
| `repeater` | upstream `openhop_repeater` alone | no (its identity is neither required nor minted, and a damaged key cannot block the start) | yes | refused (nothing to connect to) | none — no `meshcore-gps` feed, no receiver claim, no GPS refusal |

The chat rows of the Settings card (node name, allow-list, preset, TX, GPS) are the same rows in
every mode — the chat node inside the repeater is the same node, same name, same key, so other
MeshCore users see the same node on the air whichever mode runs. What does NOT carry across a mode
change is the local contact list: in `chat` the Companion persists in
`<runtime>/state/meshcore/companion.db`, in `chat+repeater` the hosted Companion persists in the
repeater's own database under `<runtime>/state/openhop/` (upstream's schema). Each store is kept as
it is; nothing is migrated between them. The **Repeater** rows are used only when the mode is not
`chat`:

- **Repeater node name** — required in the repeater modes (a save that selects a repeater mode
  without one is refused, and so is a start); the repeater is a distinct MeshCore node with its
  own identity, and like the chat node's name it never inherits the operator callsign. The mode
  rows are saved settings only — they cannot be overridden for a single start.
- **Repeater behaviour** — upstream's own switch: `forward` relays packets, `monitor` listens and
  advertises without relaying, `no_tx` only receives.
- The repeater's **identity key** and the dashboard's **admin password** are minted by LHPC into
  `<runtime>/config/secrets/openhop_repeater_identity.key` and `…/openhop_repeater_admin.txt`
  (mode `0600`) on first use and reused thereafter; the repeater's own storage (SQLite/RRD) lives
  under `<runtime>/state/openhop/`, separate from the chat node's database.

## Repeater dashboard

openHop's dashboard (statistics, neighbours, packets, logs, policy view) listens on
`127.0.0.1:8000` in the repeater modes and is reached like every stack web UI: through the LHPC
proxy, as the page `meshcore-meshcore-node` (the stack's first page `meshcore` stays the MeshCore
Web UI, so nothing saved for it moves). The login is `admin` with the password LHPC minted into
`<runtime>/config/secrets/openhop_repeater_admin.txt`. LHPC owns the repeater's configuration,
identities, radio settings and version, so the proxy refuses every dashboard route that would
change them — setup wizard, config import/export, web/MQTT/duty-cycle/advert-rate settings, the
policy editor, transport keys, region and flood policy, radio and CAD settings, repeater mode and
restart, the mesh CLI, identities and key export, API-token minting, the hosted companion's name
and position, password change, OTA updates and channel switches, and the companion-frame
websocket. A refused path is refused for reading too, so the dashboard's Policy, transport-key,
region and identity (Companions / Room Servers) pages do not load through the proxy — those
settings are LHPC's. Statistics, packets, neighbours, logs, the login, the packet stream and the
operational actions of a logged-in admin (send advert or text, ping, discovery, purge) pass.
The page exists in every mode, like every stack's page exists while its stack is stopped, so the
proxy can be configured before the mode is switched; its panel says when the upstream is not
served in the saved mode. In `chat` the proxy answers 502 for it (a saved LAN policy opens the
page's port in `chat` too), and the Password section says the admin password is not minted yet.
The stack body carries the Mode switch (the same saved setting as the row under Settings → Repeater).

**Upgrading to 0.2.8:** the stack's build now also consumes the pinned repeater checkout, so an
updated box reports the node as *not built* until it has run `lhpc install meshcore` (adopts the
new source) and `lhpc build meshcore` once — in `chat` mode too; the runtime behaviour is unchanged
until the mode is changed. Status reads the mode the node was *started* with, so a saved mode
change shows as restart-required and does not flip a running node to degraded.

LHPC owns the radio settings, the version and the configuration in every mode: the repeater gets
its RF parameters, duty-cycle budget and companion from the same file, never writes configuration
(it is given no config path, so an upstream save aims at `/etc/openhop_repeater/config.yaml`, which
the rootless unit cannot create; the routes that would save are denied at the proxy), and its MQTT, Glass and time-sync
integrations are off. The repeater's dashboard shows the daemon link as *ok* only while the LoRaHAM
daemon connection is up and, with TX enabled, the MANAGED-TX handshake is complete.

## openHop Core source

The `meshcore-node` source is upstream openHop Core, pinned to a known-working commit, plus
one small LHPC-shipped patch (the radio noise floor surfaced into the companion's radio stats —
the ACK fixes it used to carry were merged upstream at the `8cdb04e` rebase; see
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
* **Security boundary** — the proxy **refuses** (`404` — a `403` would log the operator out of the openHop dashboard, whose app treats it as an expired session) the operations LHPC owns: factory reset
  (would destroy the managed identity), radio / TX-power / tuning (the daemon owns the LoRa
  config), position, device name, and the device-touching admin reset. GPS **advert-location
  policy** is LHPC-owned too, but it rides a combined `POST /api/device/policy` alongside
  telemetry / manual-add / multi-ack controls that the GUI legitimately sets — so rather than
  denying the whole route, a small reviewable WebUI patch rejects only its `adv_loc_policy` field
  server-side. Enforced at the perimeter and in the backend, not in JavaScript; the backend has no
  private-key import/export endpoint. Everything else — messages, contacts, channels, TRACE,
  adverts — is ordinary Companion-client traffic and passes through.
* **One Companion slot** — the node serves a single Companion client at a time (a new connection
  evicts the existing one), so `meshcore-cli` and the WebUI contend for it. LHPC surfaces that as
  an active conflict on the Apps page (banner + component card), and the WebUI **yields** the slot
  to a running CLI: the CLI holds a lock while it runs and the WebUI waits on it instead of
  reconnecting, then resumes when the CLI exits. Reconnect timing alone can't arbitrate the slot,
  so the handoff is explicit.
* **State** — the WebUI keeps its OWN SQLite store (message history, GUI preferences, tile cache)
  under `<runtime>/state/meshcore-webui/`. It is a cache/display layer; the authoritative MeshCore
  identity, contacts, channels and routes live in the node, never in the WebUI database.

