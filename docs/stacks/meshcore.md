# Stack: MeshCore (openHop)

Daemon-backed MeshCore on 868 MHz — a chat (Companion) node, a repeater, or both in one process.
[openHop Core](https://github.com/openhop-dev/openhop_core) provides the MeshCore protocol; LHPC's
host application `meshcore_host` connects it to the LoRaHAM daemon sockets and owns identity, GPS,
persistence, readiness and lifecycle. The node never drives SPI or GPIO itself.

| | |
|---|---|
| Components | `meshcore-node` (main; chat node and/or repeater, by `mode`) · `meshcore-gps` (position feed, admitted by the global GPS plan) · `meshcore-webui` (optional browser GUI) · `meshcore-cli` (optional REPL) · `openhop-repeater-src` (library, build-time only) |
| Source / pin | `src/openhop-core` ← `openhop-dev/openhop_core`, built **pristine** (no LHPC patch, so any modification reads `dirty`); selector policy: [provenance](../provenance.md) · `src/openhop-repeater` ← `openhop-dev/openhop_repeater` · `src/meshcore-webui` ← `adradr/meshcore-webui` (+ `meshcore-webui-lhpc-guards.patch`, applied idempotently at build; a conflict fails the build; the declared patch still reads `match`) · `src/meshcore-cli` ← `meshcore-dev/meshcore-cli` |
| Build | `lhpc build meshcore`: `.venv` (`--system-site-packages`) → openHop Core → `meshcore_host` → the repeater's closure (`openhop-repeater-constraints.txt`) and checkout. Web UI: a backend venv from `meshcore-webui-constraints.txt`; the frontend is prebuilt package data (no npm). All headless |
| Run | `.venv/bin/python -m meshcore_host <runtime>/config/files/meshcore.toml` — the same command in every mode |
| Config | `<runtime>/config/files/meshcore.toml` (0600 — it carries the private key), rendered from `lhpc/data/bases/meshcore.toml` on every start |
| Identity | `<runtime>/config/secrets/meshcore_identity.key` (0600) → the config's `[identity] key`; repeater: `openhop_repeater_identity.key` + `openhop_repeater_admin.txt`. Updates and `uninstall` keep them; `lhpc clean meshcore --purge` removes them, so a reinstall is a new node |
| Endpoints | Companion TCP `:5000` (chat modes; binds loopback while `meshcore_allow` is `127.0.0.1`, else `0.0.0.0`) · repeater dashboard `127.0.0.1:8000` (repeater modes) · Web UI backend `127.0.0.1:8788` — reached through the LHPC proxy or an [SSH tunnel](../ssh-tunnel.md) |
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
| `node_name` | *(empty)* | max 31 bytes; the node's own name, never the operator callsign; the start is refused until set ([architecture](../architecture.md#identity-and-callsigns)) |
| `meshcore_allow` | `127.0.0.1` | who may connect to TCP 5000 (no auth); drives the managed firewall |
| `txpower` | 14 dBm | advanced (0–20) |
| `frequency` | blank = the preset's | Hz; an explicit value overrides the preset frequency |
| `airtime` | 10 % | duty-cycle limit |
| `use_gps` | on | use the global position source |
| `rf_log` | on | `logs/rf-meshcore.log`, written by the host's radio adapter in every mode: frames received (RSSI/SNR) or sent (`ok` after `TX_RESULT_STATUS_OK`; `unconfirmed` when the result was never learned; a refused send writes nothing). Read at the next start; Decrypt: [maintenance](../maintenance.md#rf-logs) |
| `mode` | `chat` | see [Mode](#mode) |
| `repeater_name` | *(empty)* | required in the repeater modes; the repeater's own name, never the operator callsign |
| `repeater_mode` | `forward` | upstream's behaviour: `forward` relays, `monitor` listens and advertises without relaying, `no_tx` only receives |
| `plugins` | on | the repeater's plugin manager — see *Plugins* under [Mode](#mode) |

Controller-owned, not settings rows: the private keys, the dashboard password (Password section), `db`, the GPS
socket/coordinates, `state_dir`. The daemon-side radio parameters live in [daemon](daemon.md).

## Mode

`mode` (`[repeater] role` in the generated config) selects the program of the one openhop process:
stack page Mode switch, Settings → Repeater, or `lhpc config meshcore mode …`. It is a saved
setting, never a per-start override. A change flags the stack restart-required; `lhpc status
meshcore` prints the saved mode plus "restart to apply" while another one runs.

| mode | program | Companion (5000) | dashboard (8000) | Web UI / CLI | position |
|---|---|---|---|---|---|
| `chat` | `meshcore_host` Companion | yes | no | available | as configured |
| `chat+repeater` | upstream `openhop_repeater` hosting the same Companion | yes | yes | available | as configured |
| `repeater` | `openhop_repeater` alone | no — its identity is neither required nor minted | yes | refused | none: no feed, no receiver claim, no GPS refusal |

The chat node is the same node (name, key) in `chat` and `chat+repeater`; only the contact store
differs (`companion.db` vs the repeater's database under `state/openhop/`), and nothing is copied
between them. The repeater is a distinct MeshCore node: its key and the dashboard password are
minted on the first repeater-mode start and reused. LHPC owns its radio settings, version and
configuration: it gets no config path (an upstream save would aim at
`/etc/openhop_repeater/config.yaml`, which it cannot create); MQTT, Glass and time-sync are off. Its dashboard shows the daemon link *ok* only while the daemon connection
is up and, with TX enabled, the MANAGED-TX handshake is complete.

**Repeater dashboard** — `127.0.0.1:8000`, proxied as the page `meshcore-meshcore-node`
(`lhpc webserver proxy meshcore-meshcore-node`; the page `meshcore` is the Web UI); in `chat` the
proxy answers 502 for it. Login `admin` + the minted password, shown in the stack page's Password
section. The proxy refuses (404, reads included) every route that changes upstream configuration,
identities, radio settings or LHPC-owned state (`proxy_deny_paths` on the 8000 endpoint). Statistics, packets, neighbours, logs, login and a logged-in
admin's operational actions pass, and, deliberately, two writing routes: the plugin manager
(`/api/plugins/…`, including an uploaded wheel) and `/api/sensors_config_update`, which fails
without change because the repeater has no config file.

The pinned openhop_core logs each decrypted over-the-air login, password included, at INFO
([openhop-dev/openhop_core#156](https://github.com/openhop-dev/openhop_core/issues/156)).

**Plugins** — the dashboard's *Plugins* page needs upstream's plugin manager
(`python -m repeater.plugins`), reached over a socket in the repeater's state directory. The host
starts it beside the repeater in the repeater modes, from the same venv, and stops it with the
stack. `plugins = off` turns it off; the dashboard then shows "Plugin manager is unavailable",
whose advice (`manage.sh upgrade`) is for native installs — do not follow it. The repeater's log
line *"Plugin-manager bootstrap skipped: venv not present"* is expected.

* A plugin is a **third-party wheel** from upstream's catalogue, downloaded and pip-installed at
  the operator's click into its own venv under `state/openhop/plugins/<id>/`. The catalogue
  checksum covers the wheel, not the dependencies pip resolves — a plugin is **outside lhpc's
  pinned closure**.
* Plugins are **not sandboxed**: they run as the repeater's user and can reach everything that
  account can, the runtime state and the daemon's sockets included.
* Updates and `lhpc uninstall` keep them; **`lhpc clean meshcore --purge` removes them**.
* On a Zero 2 W run one plugin at a time (memory).

The manager stops gracefully with the stack, taking its plugins down; a node killed without its
shutdown (SIGKILL, OOM killer) still sends it that stop through the kernel. The
manager is **not restarted** after a crash (the repeater keeps running; the banner returns).
Plugins run in their own sessions, so an unclean death may leave plugins running unseen. Then (marker `state/openhop/.lhpc-plugin-manager-active`, cleared only by a clean
stop) lhpc refuses a **replacement** manager in the same boot, and `lhpc build/update/uninstall/clean
meshcore` and the controller uninstall refuse until the box is rebooted — the recovery. A stop
that had to kill the manager after its 8 s grace counts as unclean. `plugins = off` cannot
terminate plugins an earlier unclean failure orphaned.

## Position (GPS)

`lhpc gps` is the source; `use_gps` opts this node in or out. A **live** source is fed
continuously by the `meshcore-gps` bridge (line-JSON `{"fix": true, "lat": …, "lon": …}` /
`{"fix": false}` on a Unix socket), so the position follows the box.

| global source | MeshCore |
|---|---|
| `off`, or `use_gps off` | no bridge, no coordinates |
| `fixed` | the coordinates are written to the config; no bridge |
| `auto` | a bridge while a local gpsd listens, else nothing |
| `gpsd` | a bridge fed from that gpsd |
| `nmea` | a bridge that owns the receiver and republishes it — the node never reads the hardware |

With a feed running no `lat`/`lon` are written, so no start-time position survives a stale feed.
The node drops its position when the feed delivers no valid fix for the stale interval and
resumes by itself. Neither side logs coordinates. Model: [GPS](../gps.md).

## Web UI

`meshcore-webui` is a Companion client (5000): a FastAPI/uvicorn backend plus a prebuilt SPA on loopback, published through the LHPC proxy ([webserver](../webserver.md#stack-web-ui-proxies)).
The proxy refuses (404) what LHPC owns — factory reset, radio/TX-power/tuning, position, device
name, admin reset (the component's `proxy_deny_paths`). The advert-location policy rides the
combined `POST /api/device/policy`, so the shipped patch rejects only its `adv_loc_policy` field.
The backend has no private-key import/export endpoint; messages, contacts, channels, TRACE and
adverts pass.

## Command-line client

`meshcore-cli` (`meshcli`, params `host`/`port`/`json`/`debug`) is interactive — you run it. The
node serves one Companion client at a time: the CLI wrapper holds `state/meshcore-cli.lock` while
it runs and the Web UI waits on it instead of reconnecting. A one-shot `meshcli` invocation evicts
the previous session and its pending confirmation, so it can report an error for work the node
did, and a read right after an arrival can return nothing — use the REPL for send-and-read; the
node log is the truth.

## Notes

- **Identity.** The private key *is* the node (adverts are signed with it). The host application never mints: a missing or malformed key is a hard startup
  failure, never replaced. On the first run LHPC adopts a key found in the generated config's
  `[identity] key`, else mints one. The public key survives restart, rebuild, update and reinstall.
- TX is allowed only after the connect handshake verified the daemon READY and MANAGED.
- A stop can take about a minute and a half on a Pi Zero 2 W; that is not a hang.
- **A box built while LHPC still patched openHop Core** reads `dirty`, and an update of that
  source is refused (*"local modifications to the upstream source — not overwritten"*). Do not
  force it; move the checkout aside once (a move keeps your edits and gitignored files):

  ```bash
  lhpc stack stop meshcore --yes
  mv ~/loraham-pi-control/src/openhop-core ~/openhop-core.before-pristine
  lhpc install meshcore --source pinned --yes     # clones pristine at the pin
  lhpc build meshcore --yes                       # ~8 min on a Zero 2 W
  ```

  `git -C ~/openhop-core.before-pristine status --porcelain --ignored` shows what you had; remove
  it when satisfied. Config, identity keys and the admin file live outside the checkout, so the
  node keeps its public key and settings.

## Conflicts

- One app stack per band ([kiss](kiss.md)): not with meshtastic or reticulum on 868 (they own the
  band exclusively), not with the daemon serving 868 for another client.
- `meshcore.companion-client`: advisory — one Companion client at a time, see
  [Command-line client](#command-line-client).
