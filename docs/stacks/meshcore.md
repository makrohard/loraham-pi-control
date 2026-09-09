# Stack: MeshCore (openHop)

Daemon-backed MeshCore on 868 MHz — a chat (Companion) node, a repeater, or both in one process.
It runs on [openHop Core](https://github.com/openhop-dev/openhop_core) over the LoRaHAM daemon:
openHop provides the MeshCore protocol (RF packet handling, crypto, routing, ACK/PATH/TRACE, the
Companion protocol, contacts/channels/messages); LHPC ships a small host application
(`meshcore_host`) that adapts openHop's radio interface to the daemon sockets and owns identity,
GPS, persistence, readiness and lifecycle. The node never drives SPI or GPIO itself.

| | |
|---|---|
| Components | `meshcore-node` (main — the one openhop process: chat node and/or repeater, by `mode`) · `meshcore-gps` (position feed, admitted by the global GPS plan) · `meshcore-webui` (optional browser GUI) · `meshcore-cli` (optional REPL) · `openhop-repeater-src` (library: the pinned repeater checkout, build-time only) |
| Source / pin | `src/openhop-core` ← `openhop-dev/openhop_core` `dev` @ `8cdb04e` + one LHPC patch (`lhpc/data/patches/openhop-core-companion-fixes.patch`: the radio noise floor in the radio stats), applied idempotently at build — a conflict fails the build; because the patch is declared, that patched checkout still reports `match`, while changes beyond the declared patch make it dirty. `main`/PyPI = Latest stable; selector policy: [provenance](../provenance.md) · `src/openhop-repeater` ← `openhop-dev/openhop_repeater` `dev` @ `efc5616` · `src/meshcore-webui` ← `adradr/meshcore-webui` `94dcc3d` (+ `meshcore-webui-lhpc-guards.patch`) · `src/meshcore-cli` ← `meshcore-dev/meshcore-cli` `v1.6.3` |
| Build | `lhpc build meshcore`: patch → in-tree `.venv` (`--system-site-packages`) → openHop Core → `meshcore_host` (shipped with lhpc) → the repeater's pinned closure (`openhop-repeater-constraints.txt`) and checkout. Web UI: a backend venv from `meshcore-webui-constraints.txt`; the React frontend is prebuilt package data (no npm on the box). Nothing is gui-gated — everything builds headless |
| Run | `.venv/bin/python -m meshcore_host <runtime>/config/files/meshcore.toml` — the same command in every mode |
| Config | `<runtime>/config/files/meshcore.toml` (0600 — it carries the private key), rendered from `lhpc/data/bases/meshcore.toml` on every start |
| Identity | `<runtime>/config/secrets/meshcore_identity.key` (0600), written into the generated config as `[identity] key`; repeater: `openhop_repeater_identity.key` + `openhop_repeater_admin.txt` |
| Endpoints | Companion TCP `:5000` (chat modes; binds loopback while `meshcore_allow` is `127.0.0.1`, else `0.0.0.0`) · repeater dashboard `127.0.0.1:8000` (repeater modes) · Web UI backend `127.0.0.1:8788`, loopback — reached through the LHPC proxy or an [SSH tunnel](../ssh-tunnel.md) |
| Persistence | chat: `<runtime>/state/meshcore/companion.db` (contacts, channels, routes, prefs, queued messages) · repeater modes: `<runtime>/state/openhop/` (the repeater's own SQLite/RRD; the hosted Companion persists there) · Web UI: `<runtime>/state/meshcore-webui/` (a display cache, never the identity) |
| Resources | `tcp.port.5000` / `.8000` / `.8788` exclusive · `loraham.daemon-socket.868` consumer · `loraham.profile.868` requirement `MANAGED` · `meshcore.companion-client` exclusive, advisory (webui and cli) |
| Depends on | `loraham-daemon` (868, MANAGED), `meshcore-gps`; the hardware setup must serve 868 |
| Install channel | source only |

## Contents

- [Settings](#settings)
- [Mode](#mode)
- [Position (GPS)](#position-gps)
- [Web UI](#web-ui)
- [Command-line client](#command-line-client)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

| param | default | notes |
|---|---|---|
| `preset` | `eu_uk_narrow` | RF preset (`eu_uk_long` / `eu_uk_medium` / `eu_uk_narrow`); narrow = 869.618 MHz, BW 62.5 kHz, SF8, CR8 — the T-Deck MeshCore firmware default |
| `enable_tx` | on | off = RX only |
| `node_name` | *(empty)* | max 31 bytes; the node's own name, never the operator callsign; the start is refused until set — node names never inherit ([architecture](../architecture.md#identity-and-callsigns)) |
| `meshcore_allow` | `127.0.0.1` | who may connect to TCP 5000 (no auth); drives the managed firewall |
| `txpower` | 14 dBm | advanced (0–20) |
| `frequency` | blank = the preset's | Hz; an explicit value overrides the preset frequency |
| `airtime` | 10 % | duty-cycle limit |
| `use_gps` | on | use the global position source |
| `mode` | `chat` | see [Mode](#mode) |
| `repeater_name` | *(empty)* | required in the repeater modes; the repeater's own name, never the operator callsign |
| `repeater_mode` | `forward` | upstream's behaviour: `forward` relays, `monitor` listens and advertises without relaying, `no_tx` only receives |

Controller-owned, not settings rows: the private keys, the dashboard password (Password section), `db`, the GPS
socket/coordinates, `state_dir`. The daemon-side radio parameters live in [daemon](daemon.md).

## Mode

Three modes (`lhpc/core/meshcore_mode.py`); one openhop process runs on the radio in every one,
and `mode` (`[repeater] role` in the generated config) selects the program. Set it with the Mode
switch on the stack page, the row under Settings → Repeater, or `lhpc config meshcore mode …`. It is
a saved setting, never a per-start override; a change flags the stack restart-required, and
`lhpc status meshcore` prints the saved mode plus, while another one runs, "restart to apply".

| mode | program | Companion (5000) | dashboard (8000) | Web UI / CLI | position |
|---|---|---|---|---|---|
| `chat` | `meshcore_host` Companion | yes | no | available | as configured |
| `chat+repeater` | upstream `openhop_repeater` hosting the same Companion | yes | yes | available | as configured |
| `repeater` | `openhop_repeater` alone | no — its identity is neither required nor minted | yes | refused | none: no feed, no receiver claim, no GPS refusal |

The chat node is the same node in `chat` and `chat+repeater` (same name, same key); only the local
contact store differs (`companion.db` vs the repeater's database under `state/openhop/`), and
nothing is copied between them. The repeater is a distinct MeshCore node: its key and the
dashboard password are minted on the first repeater-mode start and reused. LHPC owns its radio
settings, version and configuration — it gets no config path (an upstream save would aim at
`/etc/openhop_repeater/config.yaml`, which the rootless process cannot create), and MQTT, Glass
and time-sync are off. Its dashboard shows the daemon link *ok* only while the daemon connection
is up and, with TX enabled, the MANAGED-TX handshake is complete.

**Repeater dashboard** — `127.0.0.1:8000`, proxied as the page `meshcore-meshcore-node`
(`lhpc webserver proxy meshcore-meshcore-node`; the stack's first page `meshcore` is the Web UI);
in `chat` the proxy answers 502 for it. Login `admin` + the minted password, shown in the stack
page's Password section once a repeater mode has minted it. The proxy refuses (404, reads included)
every dashboard route that would change upstream configuration, identities, radio settings or
LHPC-owned state; the list is `proxy_deny_paths` on the node's 8000 endpoint in the manifest.
Statistics, packets, neighbours, logs, login and a logged-in admin's operational actions pass.

## Position (GPS)

`lhpc gps` is the source; `use_gps` opts this node in or out. A **live** source is fed
continuously by the `meshcore-gps` bridge as a normalized feed (line-JSON `{"fix": true, "lat":
…, "lon": …}` / `{"fix": false}` on a Unix socket), so the position follows the box while it runs.

| global source | MeshCore |
|---|---|
| `off`, or `use_gps off` | no bridge, no coordinates |
| `fixed` | the coordinates are written to the config; no bridge |
| `auto` | a bridge when a gpsd is reachable, otherwise nothing — never blocks a start |
| `gpsd` | a bridge fed from that gpsd |
| `nmea` | a bridge that owns the receiver and republishes it — the node never reads the hardware |

A live feed and static coordinates are never combined: with a feed running no `lat`/`lon` are
written, so no start-time position survives a stale feed. The node drops its position when the feed
delivers no valid fix for the stale interval and resumes by itself. Neither side logs coordinates.
In `repeater` mode nothing consumes position. Model: [GPS](../gps.md).

## Web UI

`meshcore-webui` is a client of the Companion endpoint (5000): a FastAPI/uvicorn backend plus the
prebuilt React SPA, bound to loopback and published through the LHPC proxy
([webserver](../webserver.md)). The proxy refuses (404) the operations LHPC owns — factory reset,
radio/TX-power/tuning, position, device name, admin reset; the list is the component's
`proxy_deny_paths` in the manifest. The advert-location policy rides a combined
`POST /api/device/policy`, so the shipped WebUI patch rejects only its `adv_loc_policy` field. The
backend has no private-key import/export endpoint; messages, contacts, channels, TRACE and adverts
pass.

## Command-line client

`meshcore-cli` (`meshcli`, params `host`/`port`/`json`/`debug`) is interactive — you run it. The
node serves one Companion client at a time, so `meshcore-cli` and the Web UI contend for the slot:
the CLI wrapper holds a lock (`state/meshcore-cli.lock`) while it runs and the Web UI waits on it
instead of reconnecting, then resumes. A one-shot `meshcli` invocation evicts the previous session
and its pending confirmation, so it can report an error for work the node did and a read right
after an arrival can return nothing — use the REPL for send-and-read; the node log is the truth.

## Notes

- **Identity.** The private key *is* the node: adverts are signed with it and contacts recognise
  the public key. LHPC owns the key file and the host application never mints: a missing or
  malformed identity is a hard startup failure, and a malformed key blocks rather than being
  replaced. On the first run LHPC adopts a key found in the generated config's `[identity] key`,
  else mints one. The public key survives restart, rebuild, update and reinstall.
- TX is allowed only after the connect handshake verified the daemon READY and MANAGED.
- On-air validation against a MeshCore T-Deck Pro: [live tests](../live-test.md).

## Conflicts

- One app stack per band ([kiss](kiss.md)): not with meshtastic or reticulum on 868 (they own the
  band exclusively), not with the daemon serving 868 for another client.
- `meshcore.companion-client`: the Web UI and the CLI share one slot — shown as an advisory
  conflict on the Apps page; the lock handoff above resolves it at run time.
