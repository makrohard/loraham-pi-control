# Stack: Graywolf APRS

[Graywolf](https://github.com/chrissnell/graywolf) (Chris Snell, NW5W; GPL-2.0) is the box's APRS
station — AX.25 decode, digipeater, iGate, SQLite packet log and a web UI — on 433 (default) or
868 MHz. It speaks only KISS over TCP: the [KISS TNC](kiss.md) owns the band and the daemon
sockets, the daemon owns the transmitter.

```text
APRS-IS <-> graywolf <-> KISS/TCP 8001 <-> loraham-kiss-tnc <-> framed DATA <-> loraham_daemon <-> RF
```

| | |
|---|---|
| Component | `graywolf` |
| Source / pin | upstream release `.deb`, version pinned in the manifest (no git source). `lhpc build graywolf` runs `lhpc/data/scripts/graywolf-fetch.sh`: fetch the `.deb` for this architecture, check its recorded sha256 (arm64/armhf/amd64), unpack rootless with `dpkg-deb -x` (needs `curl`, `dpkg-deb`). Marker `build/tools/graywolf/.lhpc-built-<version>`; a rebuild at the same version needs no network |
| Binary | `<runtime>/build/tools/graywolf/usr/bin/graywolf` |
| Run | `graywolf -config <runtime>/state/graywolf/graywolf.db -http 127.0.0.1:8080` |
| Web UI | `127.0.0.1:8080`, password-authenticated, loopback only (no bind param) — reach it via the web proxy (`lhpc webserver proxy graywolf`) or an [SSH tunnel](../ssh-tunnel.md) |
| Config | SQLite `<runtime>/state/graywolf/graywolf.db`, provisioned through the REST API on every start |
| Password file | `<runtime>/state/graywolf/graywolf-admin.txt` (0600), user `admin` |
| Resources | `tcp.port.8080` exclusive · `tcp.port.8001` consumer |
| Depends on | `loraham-kiss-tnc` + `loraham-daemon`, `requires_daemon_tx = MANAGED` |
| Install channel | the release fetch above. `lhpc clean graywolf --purge` removes `build/tools/graywolf` and `state/graywolf` (the whole station configuration, beacons included, and the UI password). `lhpc update graywolf --upstream` (*Check upstream*) installs the latest `chrissnell/graywolf` release, verified against that release's own `checksums.txt`. Moving the pin: [maintenance](../maintenance.md#moving-a-pin) |

## Contents

- [Settings](#settings)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

A **required post-start step** (`lhpc/data/scripts/graywolf-provision.py`, idempotent) pushes over
the REST API the admin account, a `kiss-only` channel "LoRaHAM KISS", a `tcp-client` KISS
interface dialling the TNC (a stale one from an earlier `tnc_host`/`tnc_port` is removed), the
callsign, the GPS source and the iGate config; a failed push fails the start.

| param | default | notes |
|---|---|---|
| `call` | inherits the global base callsign while empty | optional APRS SSID `-1`…`-15` (bare = SSID 0), shaped like `G0ABC-10` with your own call. Graywolf derives the APRS-IS passcode from it — LHPC stores none |
| `tnc_host` / `tnc_port` | `127.0.0.1` / `8001` | where `loraham-kiss-tnc` listens |
| `use_gps` | `on` | use the global position source (`lhpc gps`) |
| `rf_log` (kiss) | `on` | the frames cross the radio at the kiss TNC, so the file (`logs/rf-kiss.log`) and the switch (`lhpc config kiss rf_log off`) are the kiss stack's; graywolf's log page shows both |
| `igate` | `0` | enable Graywolf's APRS-IS iGate |
| `igate_server` / `igate_port` | `rotate.aprs2.net` / `14580` | |
| `igate_filter` | *(empty)* | APRS-IS server filter, e.g. `r/48.4/9.9/100`. A negation filter (`-b/…`) cannot be a param (a leading `-` reads as an option) — set it in the UI |
| `gate_rf_to_is` | `1` | RF → APRS-IS |
| `gate_is_to_rf` | `0` | APRS-IS → RF — **transmits** |

The band (433/868) selects the TNC/daemon chain; choose it in the console or with
`lhpc stack start graywolf --band 868`.

**These params are LHPC-owned**: a web-UI edit lasts until the next start — use `lhpc config
graywolf <param> <value>` (e.g. `igate 1`). Everything else — beacons, digipeater rules, smart
beaconing, `simulation_mode`, `is_tx_via`, the software identity — is yours in the UI and survives
restarts. Graywolf's iGate panel answers "igate not available" while the iGate is off. Start
repairs two channel faults that silently stop the station: an audio `modem_type` and a pure
`packet` mode (`aprs+packet` is left alone).

## Position (GPS)

Graywolf reads gpsd and serial NMEA natively (no feed component). The global plan
([GPS](../gps.md)) is pushed to its `/api/gps` settings, `none` included:

| `lhpc gps --source` | pushed to graywolf |
|---|---|
| `gpsd` (local or remote) | `source=gpsd`, `gpsd_host`, `gpsd_port` |
| `auto` | as `gpsd` while a local one listens, else `source=none` |
| `nmea` | `source=serial`, `serial_port`, `baud_rate` |
| `fixed` | `source=none` — graywolf's GPS has no fixed mode; set a fixed position on its beacons |
| `off`, or `use_gps = off` | `source=none` |

Each beacon chooses GPS (`use_gps` on the beacon) or its own fixed latitude/longitude in
graywolf's UI; a `use_gps` beacon sends nothing while there is no fix.

## Notes

- **Password.** The first start generates the admin password into the file above; the stack
  page's **Password** section shows it ([operations](../operations.md#secrets-and-passwords)).
  Provisioning logs in with it on every start, so a password changed in the web UI must be
  written into that file (one line) or the next start fails.
- **RF.** Nothing beacons until a beacon is configured in the UI. For structural silence use
  `lhpc config kiss rx_only on` ([kiss](kiss.md#settings)): it gates the transmitter's owner, and
  `lhpc status` then never reports graywolf as TX-enabled.
- With the iGate on, traffic reaches the public APRS-IS network; `igate = 0` (default) keeps a
  bench test local.

## Conflicts

- **Not with `loraham-kiss-serial`** — one KISS client: if the PTY holds the TNC, graywolf's
  dial is refused and retried; if graywolf holds it, the PTY is dead. LHPC does not lock it.
- One app stack per band ([kiss](kiss.md#notes)): its kiss/daemon chain claims the band, so a
  start on the other band is refused while that chain is up.
