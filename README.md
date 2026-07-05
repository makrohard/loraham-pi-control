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

## Install (self-hosted)

Requires Python 3.11+. A deployment is **self-hosted**: the runtime root
`~/loraham-pi-control` is a plain container, and LHPC's own checkout lives *under* it at
`src/loraham-pi-control` (just like the stacks it manages), with the venv OUTSIDE the
checkout at `venv/lhpc`. That way `lhpc self-update` and the code it runs are one tree.

```bash
# 1. Clone LHPC into the runtime root's src/ — this is what makes it self-hosted
mkdir -p ~/loraham-pi-control/src
git clone https://github.com/makrohard/loraham-pi-control.git \
    ~/loraham-pi-control/src/loraham-pi-control

# 2. Create the venv OUTSIDE the checkout, then install
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control

# 3. Create the runtime layout + default config (owner-only, mode 0700)
~/loraham-pi-control/venv/lhpc/bin/lhpc bootstrap --yes

# 4. Adopt + build stacks (add venv/lhpc/bin to PATH, or use the full path)
export PATH="$HOME/loraham-pi-control/venv/lhpc/bin:$PATH"
lhpc install --check        # show which stack sources would be adopted
lhpc install daemon --yes   # adopt + verify a stack's source …
lhpc build daemon           # … then build it
lhpc web                    # http://127.0.0.1:8770/  (loopback only)
```

`lhpc status` then shows the controller row as **identity ok**. To run it persistently as a
user service, see [`docs/deployment.md`](docs/deployment.md) (the `deploy/lhpc-web.service`
template already uses this layout). Self-update requires the web service stopped.

Set your callsign once in a stack's web **Settings**; until then HAM apps default to
`N0CALL`. Secrets (passwords, HMAC keys) live only in
`~/loraham-pi-control/config/secrets.toml`.

> Working on LHPC itself? Clone anywhere and `pip install -e .` in a venv for a dev checkout
> — that instance is intentionally *not* self-hosted (the controller row shows "not
> self-hosted"). Commit and push from there; deploy self-hosted as above.

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

## Deployment & self-update

The supported deployment is **self-hosted**: the runtime root `~/loraham-pi-control` is a
plain container, LHPC's own checkout lives under it at `src/loraham-pi-control` (alongside
the managed stack sources), and the venv is at `venv/lhpc`. The systemd unit sets
`LHPC_RUNTIME_ROOT` explicitly. LHPC's checkout is a **controller identity** — observable
and updatable via `lhpc self-update`, but never installed/built/started/cleaned/etc.; every
generic verb aimed at it refuses and points to `lhpc self-update`, and `lhpc status` shows a
distinct `[controller]` row.

Self-update requires the web service **stopped** (it takes an exclusive lock the running
console holds shared), refuses a dirty checkout unless you overwrite, and — when a
dependency change is reported — needs a manual `pip install -e …` before restart. See
[`docs/deployment.md`](docs/deployment.md) and the operator relocation runbook in
[`docs/deployment-migration.md`](docs/deployment-migration.md).
