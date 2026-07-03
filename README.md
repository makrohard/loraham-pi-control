# LoRaHAM Pi Control (`lhpc`)

Install, configure and run the amateur-radio LoRa software stacks on a Raspberry
Pi from one place — a terminal CLI and a local web console.

The Pi's stacks share one set of LoRa radios. `lhpc` adopts each stack's source,
builds it, starts and stops it in dependency order, enforces that only one stack
uses a radio band at a time, writes each app's config from one place, and monitors
and live-tunes the LoRaHAM daemon.

## Stacks

| Stack | Band(s) | What it is |
|---|---|---|
| `daemon` | 433 + 868 | LoRaHAM daemon — owns the radios, exposes per-band sockets. The foundation the app stacks use. |
| `chat` | 433 | APRS/chat TUI (interactive — run in a terminal). |
| `igate` | 433 | APRS iGate. |
| `voice` | 433 / 868 | LoRa voice (GUI). |
| `kiss` | 433 / 868 | KISS TNC over TCP (port 8001). |
| `meshtastic` | 433 / 868 | Meshtastic (rootless `meshtasticd`; web 9443, API 4403). Uses the radio directly. |
| `meshcom` | 433 | MeshCom firmware in QEMU, bridged to the daemon (web 18083, bridge 7000). |
| `meshcore` | 868 | MeshCore Pi node (TCP 5000); optional CLI + node GUI. |

Daemon-backed stacks (chat, igate, voice, kiss, meshcom, meshcore) start the
daemon automatically. Meshtastic drives the radio itself, so it cannot run while
the daemon is serving its band — `lhpc` blocks the conflict.

## Install

Requires Python 3.11+ and Flask.

```bash
git clone https://github.com/makrohard/loraham-pi-control.git
cd loraham-pi-control
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

lhpc bootstrap              # create the runtime root (~/loraham-pi-control)
lhpc install --check        # show which stack sources would be adopted
lhpc install daemon --yes   # adopt + verify a stack's source …
lhpc build daemon           # … then build it
```

Set your callsign once in a stack's web **Settings**; until then HAM apps default to
`N0CALL`. Secrets (passwords, HMAC keys) live only in
`~/loraham-pi-control/config/secrets.toml`.

## CLI

```bash
lhpc status                  # what's running (read-only, bounded — no network)
lhpc list                    # stacks in the manifest
lhpc explain <stack>         # components, start order, resources

lhpc install <stack> --yes   # adopt/verify source
lhpc build <stack>           # build
lhpc stack start <stack>     # start (auto-starts the daemon if the stack needs it)
lhpc stack stop <stack>      # stop
lhpc logs <stack>            # tail a component log

lhpc daemon <433|868>                       # monitor RSSI / stats / CAD
lhpc daemon 433 --set TXMODE=DIRECT --yes   # apply a whitelisted live setting
lhpc test <stack> --tx --yes                # one bounded TX frame per band (real RF — dummy load)

lhpc update | uninstall <stack>
```

Mutating commands print a plan and require `--yes` (or a confirmation) before they
act. TX is never implicit.

## Web console

```bash
lhpc web                     # http://127.0.0.1:8770/  (loopback only)
```

- **Dashboard** — per band: the daemon monitor (live RSSI/stats), the stacks
  running on that band, and a control to start another.
- **Stack pages** — Install / Build / Start / Stop / Test / Update / Uninstall,
  each with a plan and confirmation. Interactive (TUI) apps show the
  command to run yourself; GUI/headless apps start and stop directly.
- **Settings** — per-stack settings (callsign, frequencies, presets …)
  written into each app's own config file.

Loopback-only bind, POST actions are CSRF-protected, `Content-Security-Policy:
default-src 'self'`. Not intended to be exposed to a network.
