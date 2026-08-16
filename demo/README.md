# LHPC interactive demo (GitHub Pages)

A fully static, **interactive** demo of the real LoRaHAM Pi Control console — the actual
Flask app compiled to WebAssembly with [Pyodide](https://pyodide.org) and driven against a
pure in-browser **simulation** backend. No server, no Raspberry Pi, no real radios.

**Live:** `https://makrohard.github.io/loraham-pi-control/` *(after Pages is enabled — see
Setup below).*

## What you can do

Browse the real dashboard and Apps, open a stack, and **install → build → start → stop** it —
every action is simulated in your browser and the real console reflects it. Your changes
persist across reloads (localStorage); **Reset demo** returns to a clean configured box.

## Independence (by design)

Three separate trees, zero coupling:

- `lhpc/` — the product. Untouched; unaware of the demo.
- `testlab/` — the Codespaces lab. The demo does **not** import or depend on it.
- `demo/` — **this**: its own `lhpc_demo` package, its own front-end, its own Pages
  workflow. It is **not** part of the lhpc wheel or image, and not part of testlab. It only
  *loads* the lhpc wheel into Pyodide — that is the whole point — but is not part of lhpc.

## How it works

- Pyodide loads the real **lhpc wheel** (plus Flask/Jinja/cryptography). A no-op `fcntl`
  shim (`lhpc_demo/shims.py`) is the only thing Pyodide needs.
- `lhpc_demo.DemoService(ControllerService)` overrides the low-level state predicates
  (`is_installed`/`is_built`/`stack_running`) to read an in-memory model and the lifecycle
  actions to flip it — so the **real** dashboard/apps render logic shows simulated state
  with no git, gcc, processes, or hardware.
- `lhpc_demo.bridge` holds one persistent `app.test_client()` (so the Flask session cookie
  and CSRF token survive), seeds a configured box (boot id, radio board, callsign), and
  exposes `handle(method, path, form)` for the front-end.
- `web/boot.js` loads Pyodide, installs the wheels (per `wheels.json`), and routes clicks
  and form submits through the bridge, swapping responses into the page. It relies on
  LHPC's no-JS server-rendered fallbacks; stack rows lazy-load their body through the
  bridge on expand.

## Develop & test locally

```
# assemble the bundle (rebuilds BOTH wheels fresh + copies lhpc static + writes wheels.json)
PYTHON=python tools/assemble.sh

# headless boot + lifecycle test (needs node + the pyodide npm package)
LHPC_WHEEL="$(ls web/wheels/loraham_pi_control-*.whl)" DEMO_DIR="$PWD" node tests/boot.mjs

# headless browser smoke test (needs puppeteer-core + Chrome)
( cd web && python3 -m http.server 8099 & )
DEMO_URL=http://127.0.0.1:8099/index.html node tests/browser.mjs
```

## Deploy

`.github/workflows/pages.yml` assembles the bundle, runs both gates, and deploys to Pages.
It runs on changes under `demo/**` and on manual dispatch (a manual run also refreshes the
bundled lhpc version).

### One-time setup

Repo **Settings → Pages → Build and deployment → Source: “GitHub Actions.”** Until that is
enabled the deploy step fails (the build + tests still run).

## What runs vs what's simulated

The demo runs the **real lhpc console** (Flask app, in Pyodide). The stacks it manages
cannot run in a browser, so they are simulated — this is a hard WASM-sandbox limit, not a
shortcut:

- **Native stacks** (kiss=C, graywolf=binary, meshtastic=`meshtasticd`, meshcom=qemu, chat,
  igate, voice) — a browser can't execute machine code, only WebAssembly. **Simulated.**
- **Python stacks** (Reticulum/rns, lxmd, nomadnet, sideband, meshcore-pi) — verified they
  can't run either: `RNS.Reticulum()` needs OS network-interface enumeration + threads
  (crashes in the sandbox), meshcore-pi's `aioble` (Bluetooth) has no Pyodide wheel, and the
  GUI/TUI ones (nomadnet, sideband) need a terminal/display. **Simulated.**
- **daemon** — the one piece that's **simulated but LIVE**: an in-memory model
  (`lhpc_demo/daemon_sim.py`) feeds the console's real radio panels with time-varying
  STATUS/STATS/CHANNEL and a rolling RX/TX packet feed, so a started band shows READY with a
  moving RSSI/monitor. The daemon comes up per-band when a stack runs on that band.

So the demo faithfully shows **operating the box** — install/build, start/stop with
one-stack-per-band handoff, and a live radio — but not the stacks' own software or web UIs.
For those, use the **Codespace** (real stack processes against simulated hardware).

## Other limitations

- Client-side JS beyond the essentials is not wired: the theme toggle and auto-install
  streaming don't run (the app's own scripts aren't re-executed). Live monitor polling
  works via the bridge. Stack web UIs (graywolf 8080, etc.) can't open — no real server.
- Fault scenarios and network-join flows are not simulated.
