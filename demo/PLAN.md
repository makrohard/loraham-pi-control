# LHPC GitHub Pages demo — plan

A fully static, interactive demo of the REAL LHPC console on GitHub Pages: the actual
Flask app compiled to WASM via Pyodide, driven against a pure in-browser simulation
backend. No server, no Pi, no real processes.

## Independence (hard constraint)
Three separate trees, zero coupling:
- `lhpc/`     — the product. Untouched. Not aware of the demo.
- `testlab/`  — the Codespaces lab. The demo does NOT import or depend on it.
- `demo/`     — THIS. Its own package (`lhpc_demo`), its own static front-end, its own
  Pages workflow. Not in the lhpc wheel, not in the image, not in testlab. It *loads*
  the lhpc wheel into Pyodide (that is the whole point) but is not part of lhpc.

## Feasibility (proven by spike)
Pyodide 0.26 + the lhpc wheel: `import lhpc` OK, `create_app()` OK, `/healthz` 200 — with
ONE shim: a no-op `fcntl` module (file locking is meaningless in a single tab). cryptography
loads as the Pyodide WASM build. `ctypes` is lazy and never hit. `/` 500 only because the
default provider probes real hardware — the demo supplies its own fake System.

## Architecture
- `lhpc_demo/shims.py`  — install the `fcntl` no-op (and any later shims) BEFORE lhpc import.
- `lhpc_demo/system.py` — a pure-sim `System`: a STATEFUL dispatching command runner +
  fake procfs/fs, built on lhpc's own `FakeSystem` primitives (NOT testlab). Simulates
  stack lifecycle (install/build/start/stop -> state), nmcli/systemctl/busctl, scenarios,
  daemon RX/TX — all in memory, no processes, no sockets.
- `lhpc_demo/provider.py` — `build(paths)` -> object with `.system/.manifest_path/.wrap_spawn`
  (the lhpc LHPC_SYSTEM_PROVIDER contract). Wires the sim System in.
- `web/index.html + boot.js + style.css` — loading screen; loadPyodide; install wheel+deps;
  install shims; set the provider; `create_app()`; drive via `app.test_client()`. A fetch/
  click/form interceptor routes navigation + POSTs (with CSRF) into the test client and
  swaps the returned HTML into the page. localStorage persists the sim state; a Reset
  button clears it.
- `build/` — assemble the deployable bundle: build the lhpc wheel from pinned source, copy
  Pyodide runtime + required wheels, emit `web/` + a manifest. No network at runtime.
- `.github/workflows/pages.yml` — build the bundle, deploy to Pages. Independent workflow.

## Slices (each ends green + tested)
S1  Skeleton + provider over FakeSystem; headless node/pyodide test: create_app via the
    demo provider renders the real routes 200 (dashboard, apps, network, settings).
S2  Stateful sim: install/build/start/stop mutate in-memory stack state; status reflects it.
S3  Scenarios (healthy/degraded/disconnected), network join/AP/wrong-password, power reboot.
S4  Daemon RX/TX + inject; the chain shows simulated traffic.
S5  JS glue: full clickability (nav, forms, CSRF), page-swap, error surface.
S6  localStorage persistence + Reset button; cold-load UX + styling.
S7  build/ bundler (offline runtime) + local static serve smoke test in headless chromium.
S8  pages.yml deploy workflow.
S9  Code-review, docs, deploy.

## Test strategy per slice
- Python/provider slices: headless node + pyodide harness (demo/tests/*.mjs) asserting real
  route responses and state transitions.
- JS-glue/UX slices: headless google-chrome/Playwright loading the built bundle.
