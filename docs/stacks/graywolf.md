# Stack: Graywolf APRS

[Graywolf](https://github.com/chrissnell/graywolf) (Chris Snell, NW5W; GPL-2.0) is the
box's APRS station — AX.25 decode, digipeater, iGate, SQLite packet log and a web UI — on 433
(default) or 868 MHz. It speaks only KISS over TCP: the [KISS TNC](kiss.md) owns the band and the
daemon sockets, and the daemon owns the transmitter.

```text
APRS-IS <-> graywolf <-> KISS/TCP 8001 <-> loraham-kiss-tnc <-> framed DATA <-> loraham_daemon <-> RF
```

| | |
|---|---|
| Component | `graywolf` |
| Source / pin | upstream release `.deb`, version pinned in the manifest — no git source. `lhpc build graywolf` runs `lhpc/data/scripts/graywolf-fetch.sh`: download the `.deb` for this architecture, verify it against the recorded sha256 (arm64/armhf/amd64), unpack with `dpkg-deb -x` — rootless, no system package, no new bootstrap dependency (`curl`, `dpkg-deb`). Marker `build/tools/graywolf/.lhpc-built-<version>`; a rebuild at the same version needs no network |
| Binary | `<runtime>/build/tools/graywolf/usr/bin/graywolf` |
| Run | `graywolf -config <runtime>/state/graywolf/graywolf.db -http 127.0.0.1:8080` |
| Web UI | `127.0.0.1:8080` — password-authenticated, loopback only, no bind param. Reach it through the stack's web proxy (Webserver page, `lhpc webserver proxy graywolf`) or an [SSH tunnel](../ssh-tunnel.md) |
| Config | SQLite `<runtime>/state/graywolf/graywolf.db`, provisioned through the REST API on every start |
| Password file | `<runtime>/state/graywolf/graywolf-admin.txt` (0600), user `admin` |
| Resources | `tcp.port.8080` exclusive · `tcp.port.8001` consumer |
| Depends on | `loraham-kiss-tnc` + `loraham-daemon`, `requires_daemon_tx = MANAGED` |
| Install channel | the release fetch above (the stack's Install tab and `lhpc auto-install` do the same). `lhpc clean graywolf --purge` removes `build/tools/graywolf`. *Check upstream* compares the latest `chrissnell/graywolf` release; an opted-in newer version is verified against that release's own `checksums.txt` (`lhpc update graywolf --upstream`). Moving the version: [maintenance](../maintenance.md#moving-a-pin) |

## Contents

- [Settings](#settings)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

Graywolf keeps its configuration in SQLite behind its web API, so the params are re-applied
through the REST API by a **required post-start step** (`lhpc/data/scripts/graywolf-provision.py`);
a failed push fails the start rather than leaving graywolf up unconfigured. Idempotent: it
ensures the admin account, a `kiss-only` channel "LoRaHAM KISS" (no audio device, no modem, no
PTT), a `tcp-client` KISS interface dialling the TNC (a stale interface from an earlier
`tnc_host`/`tnc_port` is removed), the callsign, the GPS source and the iGate config.

| param | default | notes |
|---|---|---|
| `call` | inherits the global base callsign while empty | optional APRS SSID `-1`…`-15` (bare = SSID 0), shaped like `G0ABC-10` with your own call. Graywolf derives the APRS-IS passcode from it — LHPC stores no passcode |
| `tnc_host` / `tnc_port` | `127.0.0.1` / `8001` | where `loraham-kiss-tnc` listens |
| `use_gps` | `on` | use the global position source (`lhpc gps`) |
| `rf_log` (kiss) | `on` | the frames cross the radio at the kiss TNC, so the file (`logs/rf-kiss.log`) and the switch (`lhpc config kiss rf_log off`) are the kiss stack's — in the kiss Settings (RF-Logs group) and on graywolf's log page |
| `igate` | `0` | enable Graywolf's APRS-IS iGate |
| `igate_server` / `igate_port` | `rotate.aprs2.net` / `14580` | |
| `igate_filter` | *(empty)* | APRS-IS server filter, e.g. `r/48.4/9.9/100`. A negation filter (`-b/…`) cannot be a param — a leading `-` reads as an option — so set those in the UI |
| `gate_rf_to_is` | `1` | RF → APRS-IS |
| `gate_is_to_rf` | `0` | APRS-IS → RF — **transmits** |

The band (433/868) selects which TNC/daemon chain comes up, exactly as on the kiss stack; it is
chosen in the console (`lhpc stack start` takes no band flag).

**These params are LHPC-owned**: re-applied on every start, so a value edited in the web UI lasts
until the next restart — change it with `lhpc config graywolf <param> <value>` (this is how
`igate 1` sticks). Everything LHPC does not provision — beacons, digipeater rules, smart
beaconing, `simulation_mode`, `is_tx_via`, the software identity — is yours in the UI and
survives restarts (the iGate object is read first and only the owned fields overlaid). Graywolf's
iGate panel answers "igate not available" while the iGate is disabled. Two channel faults are
repaired at start because both silently stop the station: an audio `modem_type`, and a pure
`packet` mode; `aprs+packet` is left alone.

## Position (GPS)

Graywolf reads gpsd (host/port) and a serial NMEA device natively, so it needs no bridge
component. The global plan maps straight onto its own `/api/gps` settings, in **both**
directions — a global source turned off, or `use_gps = off`, actively pushes `none`:

| `lhpc gps --source` | pushed to graywolf |
|---|---|
| `gpsd` (local or remote) | `source=gpsd`, `gpsd_host`, `gpsd_port` |
| `auto` | as `gpsd` while a local one listens, else `source=none` |
| `nmea` | `source=serial`, `serial_port`, `baud_rate` |
| `fixed` | `source=none` — graywolf's GPS has no fixed mode; a fixed position belongs to its beacons, which are yours to set |
| `off`, or `use_gps = off` | `source=none` |

A beacon decides for itself whether to use GPS (`use_gps` on the beacon) or its own fixed
latitude/longitude — graywolf's setting, not LHPC's. The model is in [GPS](../gps.md).

## Notes

- **Password.** The first start generates the admin password into the file above; the stack
  page's **Password** section reaches it, beside an *Edit stored password file* command
  ([operations](../operations.md#secrets-and-passwords)). LHPC owns the account — provisioning
  logs in with it on every start — so a password changed in the web UI must be written into that
  file (one line) or the next start fails. The file is written *before* the account is created,
  so an interrupted first start retries cleanly.
- **RF.** Nothing beacons until a beacon is configured in the UI. For structural silence use the
  [kiss](kiss.md) stack's switch, `lhpc config kiss rx_only on` — it gates the component that
  owns the transmitter and `lhpc status` reads it; with the chain RX-only graywolf is never
  reported as TX-enabled.
- With the iGate on, received and sent traffic reaches the public APRS-IS network; `igate = 0`
  (the default) keeps a bench test local.
- Round trips against an ESP32 LoRa-APRS tracker are verified in the live tests.

## Conflicts

- **Not with `loraham-kiss-serial`** — the TNC serves one KISS client; if the PTY holds it
  graywolf's dial is refused and it retries, if graywolf holds it the PTY is dead. An operator
  constraint, not reslock-enforced.
- One app stack per band ([kiss](kiss.md)): graywolf claims no radio, but its `kiss`/daemon
  chain does, so a start on the other band is refused while that chain is up.
