# LHPC interactive demo (GitHub Pages)

A static, interactive demo of the LoRaHAM Pi Control console: the real Flask app running in the
browser under [Pyodide](https://pyodide.org) against an in-browser simulation backend. No
server, no Raspberry Pi, no real radios.

**Live:** `https://makrohard.github.io/loraham-pi-control/`

## Contents

- [What you can do](#what-you-can-do)
- [Independence (by design)](#independence-by-design)
- [How it works](#how-it-works)
- [Develop & test locally](#develop--test-locally)
- [Deploy](#deploy)
- [What runs vs what's simulated](#what-runs-vs-whats-simulated)
- [Other limitations](#other-limitations)

## What you can do

Browse the dashboard and Apps, open a stack, and **install → build → start → stop** it; every
action is simulated and the real console reflects it. State persists across reloads
(localStorage); **Reset demo** returns to a clean configured box.

## Independence (by design)

`demo/` is its own `lhpc_demo` package, front-end and Pages workflow. It is not part of the lhpc
wheel or image and does not import `testlab/`; it only loads the lhpc wheel into Pyodide.
`lhpc/` knows nothing of it.

## How it works

- Pyodide loads the lhpc wheel and its dependencies (Flask, Jinja, cryptography). A no-op
  `fcntl` shim (`lhpc_demo/shims.py`) is the only adaptation Pyodide needs.
- `lhpc_demo.DemoService(ControllerService)` overrides the state predicates
  (`is_installed`/`is_built`/`stack_running`) to read an in-memory model and the lifecycle
  actions to flip it, so the real dashboard/apps render logic shows simulated state without
  git, gcc, processes or hardware.
- `lhpc_demo.bridge` holds one persistent `app.test_client()` (the session cookie and CSRF
  token survive), seeds a configured box (boot id, radio board, callsign), and exposes
  `handle(method, path, form)` to the front-end.
- `web/boot.js` loads Pyodide, installs the wheels listed in `wheels.json`, and routes clicks
  and form submits through the bridge, relying on LHPC's no-JS server-rendered fallbacks;
  stack rows lazy-load their body through the bridge on expand.

## Develop & test locally

From `demo/`, assemble the bundle (rebuilds both wheels, copies lhpc's static assets, writes
`wheels.json`):

```
PYTHON=python tools/assemble.sh
```

Then run the two gates: [demo tests](tests/README.md).

## Deploy

`.github/workflows/pages.yml` assembles the bundle, runs both gates, and deploys to Pages. It
runs on manual dispatch (which also refreshes the bundled lhpc version) and on pushes to `main`
that touch `demo/**`, `lhpc/**`, `pyproject.toml`, `.github/scripts/retry.sh` or the workflow
itself.

## What runs vs what's simulated

The console is real; the stacks it manages cannot run in a browser sandbox, so they are
simulated:

- **Native stacks** (kiss, graywolf, meshtastic's `meshtasticd`, meshcom's qemu, chat, voice) —
  a browser runs only WebAssembly, not machine code.
- **Python stacks** (Reticulum/rns, lxmd, nomadnet, sideband, the MeshCore host) —
  `RNS.Reticulum()` needs OS network-interface enumeration and threads (it crashes in the
  sandbox), the MeshCore host's `aioble` (Bluetooth) has no Pyodide wheel, and nomadnet and
  sideband need a terminal/display.
- **daemon** — simulated but live: `lhpc_demo/daemon_sim.py` feeds the real radio panels with
  time-varying STATUS/STATS/CHANNEL and a rolling RX/TX feed, so a started band shows READY
  with a moving RSSI/monitor. It comes up per band when a stack runs on that band.
- **GPS receiver** — simulated but live: Apps → Position → Monitor shows a 3D fix (fixed
  position, nine satellites, seven used). Its synthetic NMEA goes through the real parser
  (`gps.NmeaSnapshot`), which fills the fields, Skyview and NMEA stream. The GPS Settings form
  is the real one.

The demo shows operating the box — install/build, start/stop with one-stack-per-band handoff,
a live radio — but not the stacks' own software or web UIs; for those use the
[Codespace](../docs/testlab.md).

## Other limitations

- The app's own scripts are not re-executed: the theme toggle and auto-install streaming do
  not run. Live monitor polling works through the bridge. Stack web UIs (graywolf 8080, etc.)
  cannot open.
- Fault scenarios and network-join flows are not simulated.
