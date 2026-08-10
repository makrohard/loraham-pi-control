# Stack: Graywolf APRS

[Graywolf](https://github.com/chrissnell/graywolf) (Chris Snell, NW5W) is a full APRS
station: AX.25 decode, digipeater, iGate, SQLite packet log and a web UI. Here it is the
modern substitute for [`igate`](aprs.md#igate--loraham-igate) — same job, far more of it,
with an operator UI instead of CLI flags.

It never touches the radio. Graywolf speaks KISS over TCP and the
[KISS TNC](kiss.md) owns the band and the daemon sockets:

```text
APRS-IS <-> graywolf <-> KISS/TCP 8001 <-> loraham-kiss-tnc <-> framed DATA <-> loraham_daemon <-> RF
```

| | |
|---|---|
| Component | `graywolf` (upstream release, unpacked into the runtime root) |
| Binary | `<runtime>/build/tools/graywolf/usr/bin/graywolf` |
| Web UI | `127.0.0.1:8080` — password-authenticated, loopback only |
| Config DB | `<runtime>/state/graywolf/graywolf.db` |
| UI password | `<runtime>/state/graywolf/graywolf-admin.txt` (0600, generated on first start) |
| Depends on | `loraham-kiss-tnc` + `loraham-daemon` (the daemon owns the transmitter, so the RF chain names it explicitly) |
| Resources | `tcp.port.8080` exclusive; `tcp.port.8001` consumer |

## Install

Nothing manual, and **no root**:

```bash
lhpc build graywolf          # fetches + verifies + unpacks the pinned release
lhpc stack start graywolf
```

`lhpc build` runs `lhpc/data/scripts/graywolf-fetch.sh`, which downloads the pinned upstream
`.deb` for this box's architecture, checks it against a recorded sha256, and unpacks it with
`dpkg-deb -x` into `build/tools/graywolf`. A `.deb` is an ar archive, so unpacking needs no
privileges — the binaries end up runtime-owned like every other managed artifact. The stack's
Install tab and every `lhpc auto-install` run do the same thing.

Consequences worth knowing:

- **No system package is installed**, so there is no packaged `graywolf.service` to collide
  with LHPC's process and nothing to `systemctl disable`. If you previously installed the
  `.deb` by hand, remove it (`sudo apt remove graywolf`) so only the runtime copy is used.
- **No new system dependency**: `curl` is already a bootstrap package and `dpkg-deb` ships
  with dpkg, so `bootstrap-deps.sh` is untouched.
- A rebuild is a no-op once the pinned version is unpacked, so it needs no network — only a
  version bump re-downloads. `lhpc clean graywolf --purge` removes `build/tools/graywolf`.
- graywolf is **not** in the Debian archive, so it can never be a plain apt dependency.
- Bumping the version means editing the version in the manifest's build step *and* adding the
  new sha256 to the table in `graywolf-fetch.sh` — deliberately a reviewable two-line diff.

Pinned at **v0.14.12** (GPL-2.0-or-later; each box fetches it from upstream, so LHPC
redistributes nothing).

## Configuration

Graywolf keeps its configuration in SQLite behind its web API, so this stack cannot express
settings as generated config files. Instead every start re-applies the params through the
REST API in a **required post-start step**
(`lhpc/data/scripts/graywolf-provision.py`): a failed push fails the start rather than
leaving graywolf up with an empty config. The step is idempotent, so restarts are cheap.

It ensures a `kiss-only` channel (no audio device, no modem, no PTT), a `tcp-client` KISS
interface dialling the TNC, the station callsign, and the iGate settings.

| Param | Default | Notes |
|---|---|---|
| `call` | operator callsign | Needs an APRS SSID, e.g. `N0CALL-10`. Graywolf derives the APRS-IS passcode from it — LHPC never stores a passcode. |
| `tnc_host` / `tnc_port` | `127.0.0.1` / `8001` | Where `loraham-kiss-tnc` listens |
| `igate` | `0` | Enable APRS-IS gating |
| `igate_server` / `igate_port` | `rotate.aprs2.net` / `14580` | |
| `igate_filter` | *(empty)* | APRS-IS server filter, e.g. `r/48.4/9.9/100`. A **negation** filter (`-b/…`) cannot be a param — a value starting with `-` would be read as an option — so set those in graywolf's UI. |
| `gate_rf_to_is` | `1` | RF → APRS-IS |
| `gate_is_to_rf` | `0` | APRS-IS → RF — **transmits** |

The band (`433`/`868`) selects which TNC/daemon chain to bring up, exactly as on the kiss
stack — graywolf itself only speaks TCP.

The web UI exposes far more than these (beacons, digipeater rules, smart beaconing). The
params cover what a station needs to come up correctly; anything else is set in the UI and
persists in the config database.

**The params above are LHPC-owned.** Because they are re-applied on every start, editing
one of *those* fields in the web UI is overwritten at the next restart — change it with
`lhpc config graywolf <param> <value>` instead. Everything LHPC does not provision
(beacons, digipeater rules, simulation mode, `is_tx_via`, the software identity) is yours to
set in the UI and survives restarts untouched: graywolf's config endpoints are full
replacements, so provisioning reads the current object first and overlays only its own fields.

Two things about the LHPC-owned channel are repaired rather than left broken, because both
silently stop the station: an audio `modem_type` (no TNC behind it), and a **pure `packet`**
mode, which makes graywolf log `beacon skipped: channel mode is packet`. `aprs+packet` is left
alone — APRS still works there and connected-mode sessions are a deliberate choice.

The listener is deliberately loopback-only and has no bind param. Reach it either through
this stack's **web proxy** in the console (Stacks → Graywolf → Web server — the sanctioned
path, with the console's own TLS and allow-list) or over a tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 lhpc@<pi>
```

## Conflicts

- **Replaces `igate`.** Both gate the same RF to APRS-IS; running both double-gates and
  both retune the 433 radio. Run one.
- **Not with `loraham-kiss-serial`.** The TNC accepts a *single* KISS client. If the socat
  PTY holds it, graywolf's dial is refused and it retries forever; if graywolf holds it,
  the PTY is dead. This is not reslock-enforced (a KISS client slot is not a declarable
  resource) — it is an operator constraint, like `chat` vs `igate`.
- `chat` retunes the same radio; don't pair it with a transmitting graywolf.

## Web UI credentials

The first start generates the admin password and writes it to
`<runtime>/state/graywolf/graywolf-admin.txt` (0600). **LHPC owns that account**: the
provisioning step logs in with it on every start, so if you change the password in the web
UI, write the new one into that file (one line) or the next start will fail. The file is
written *before* the account is created, so an interrupted first start retries cleanly
instead of leaving an account nobody can log into.

## RF safety

Graywolf transmits when a beacon, the digipeater or IS→RF gating fires. Nothing beacons
until you configure a beacon in the UI, so a freshly provisioned station is quiet.

For structural silence use the **kiss** stack's switch — `lhpc config kiss rx_only on` —
not a graywolf setting. That gates the component which actually owns the transmitter, it is
visible in the TNC's own argv, and `lhpc status` reads it: with the chain RX-only, graywolf
is never reported as TX-enabled. A graywolf-side "do not transmit" would only be an
intention recorded in its database, invisible to the controller.
