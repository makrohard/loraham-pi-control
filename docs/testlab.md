# Test lab: the real console without a Raspberry Pi

The test lab runs the REAL LHPC (real Flask console, real CLI, real stack processes, real
installs and builds) against deterministic fake hardware and OS backends. No Pi, no radio, no
root.

## Contents

- [Quick start (one click)](#quick-start-one-click)
- [What is real, what is simulated](#what-is-real-what-is-simulated)
- [Launch](#launch)
- [Scenarios and the Test Lab panel](#scenarios-and-the-test-lab-panel)
- [Running the verification lanes](#running-the-verification-lanes)
- [Codespaces costs](#codespaces-costs)

## Quick start (one click)

1. Click **[Open in GitHub Codespaces](https://codespaces.new/makrohard/loraham-pi-control)**
   (the badge below). Codespaces are **x86-only**. Enable the **prebuild** (Settings →
   Codespaces) so the image is baked with every stack already built; without one the first boot
   source-builds meshcom and meshtastic on x86, a few minutes each.
2. Wait while it builds and sets itself up (install → `init` → `reset`); you type nothing.
3. The **LHPC console opens in a browser tab by itself** (port 8770).

The console comes up fast; every other stack (kiss, graywolf, meshcore, reticulum, voice,
sideband, meshcom, meshtastic) then installs and builds in the background, preferring the
aarch64 `lhpc-binaries` wherever a stack ships one: progress is in `~/lhpc-populate.log`, and a
stack appears as **installed** once its build finishes.

To run a stack: **Apps**, pick a stack, **Start** (or **Install → Build → Start** if you got there
before its background install finished); its web UI appears on its own forwarded port (below).
Switch fault scenarios and inject traffic from the **Test Lab** panel (top banner link). If the
tab does not open, use the **Ports** tab → port **8770** → the globe icon.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)

## What is real, what is simulated

| Real | Simulated |
|---|---|
| The web console (waitress), every page/form/confirm flow | The radios: a fake `loraham_daemon` speaks the full v112 wire protocol (raw + framed + CONF sockets) with scenario-driven `RADIO=` state |
| The CLI (`lhpc …`, the installed executable) | NetworkManager (`nmcli`: profiles, scan, join, wrong-password, AP fallback) |
| Stack installs/builds/starts/stops through the production lifecycle (kiss TNC, graywolf, meshcore); on x86 Codespaces meshcom (emulated-ESP32 qemu) and meshtastic (sim radio) **source-build** from the pinned sources (on aarch64 they install from the binary channel, no compile) | logind power handshake (`busctl` CanReboot/CanPowerOff) |
| nginx (the stackweb proxies run a real unprivileged nginx driven by the lab supervisor) | `systemctl` (stateful unit model in `state/testlab/units.json`) |
| PKI / certificates (pure Python) | The boot identity: a simulated reboot stops owned stacks, advances the boot id and uptime epoch; the host never reboots |
| gpsd: a real listener on 127.0.0.1:2947 streaming checksum-valid NMEA | The firewall receipt paths (relocated under the lab root via `LHPC_FW_PATH_PREFIX`; the real freshness logic runs on them) |

Never real in the lab: `sudo`, `apt`, `nft`, host shutdown. The lab user is unprivileged with
no sudo (that privilege drop, not an argv filter, is the safety boundary), and the lab runner
refuses direct host mutators with a typed message.

Graywolf's APRS-IS uplink is forced to the lab's local sink (`127.0.0.1:14580`) by the manifest
overlay, so the lab never reaches the live ham network. meshtastic and reticulum run against
simulated radios (meshtastic's upstream `sim` radio; reticulum's fake spidev/gpiod shims); voice
and sideband are GTK/Kivy GUIs that launch headless under Xvfb (not remotely viewable). `chat`,
`nomadnet` and voice's terminal variant are interactive TUIs LHPC never auto-spawns.

## Launch

**Codespace (one click):** the badge above. The container builds from
`.devcontainer/Dockerfile` (apt deps), then onCreate installs and builds every stack; the console
starts automatically on port **8770** (forwarded privately). Stack UIs: graywolf **8080**, meshcom
**18083**, meshcore's Web UI **8788** (Companion TCP **5000**); ports **8444–8447** are forwarded
for stack proxy pages, of which the lab installs none — create them on the Webserver page.
meshtastic's UI is on **9080**: a plain-HTTP socat bridge `start.sh` runs in front
of meshtasticd's self-signed HTTPS on `:9443`, because a Codespace's forwarding proxy 502s on the
self-signed TLS. A restarted codespace re-runs the idempotent start script.

**Any container/box:**

```sh
export LHPC_SYSTEM_PROVIDER=lhpc_testlab.provider:build   # the generic lhpc hook
export LHPC_TESTLAB=1
export LHPC_RUNTIME_ROOT="$HOME/loraham-pi-control"          # or anywhere empty
export LHPC_BOOT_ID_FILE="$LHPC_RUNTIME_ROOT/state/testlab/host/boot_id"
export LHPC_FW_PATH_PREFIX="$LHPC_RUNTIME_ROOT/state/testlab/host"
lhpc-testlab init        # the ONLY verb that works before the lab root exists;
                         # refuses any non-empty root that is not already a lab root
lhpc-testlab reset       # bootstrap + hardware + callsign + fakes + fake daemon;
                         # on ARM also binary-channel meshcom + meshtastic
lhpc-testlab web         # console (real lhpc + the lab panel) on http://127.0.0.1:8770
```

Activation is a two-key latch: the `LHPC_TESTLAB=1` environment variable AND the
`state/testlab/enabled` marker `init` creates. A production box never has the env var; a
production process pointed at a lab root never activates. When the latch holds, every page
carries the purple **TEST LAB — SIMULATED HARDWARE** banner.

## Scenarios and the Test Lab panel

`lhpc-testlab scenario <name>` (or the panel at `/testlab`): `healthy`, `disconnected` (AP
fallback), `wrong-password`, `hardware-missing` (RADIO=FAILED + a missing dependency),
`degraded` (one band down, gpsd off, stale firewall receipt), `recovery` (faulty, self-heals
after 60 s). Running fakes poll the scenario file and follow within a second.
`lhpc-testlab inject <band> <preset>` queues an RX frame the fake daemon delivers to the real
chain (watch it arrive in graywolf). `lhpc-testlab reset` returns to the deterministic healthy
baseline; `lhpc-testlab check` reports fakes and per-stack readiness through the production
gates.

A simulated **Reboot** (dashboard button) behaves like a real one: owned stacks stop, the boot
identity advances, the admission gate clears on the "new boot", but the console stays reachable
(it IS the lab; the event log says so).

## Running the verification lanes

```sh
pytest -q                                      # default lane (lab lanes skip)
LHPC_ACCEPTANCE=1 pytest testlab/tests/acceptance -q   # real server + real executable
LHPC_BROWSER=1 pytest testlab/tests/browser -q     # headless Chromium (pip install -e ./testlab[browser])
LHPC_RELEASE_VERIFY=1 pytest testlab/tests/release -q -x  # release lane: every stack installed, built, started
```

The lab has four lanes — `testlab/tests/unit` for the simulator itself, `acceptance` for the real
executable and server over simulated hardware, `browser` for real headless Chromium, and
`release` for a release's evidence. What they prove differs in depth, and the difference matters:

- **acceptance** drives the RUNNING server and the real `lhpc` executable, and verifies the
  effect. `test_http_smoke.py` additionally enumerates the app's own `url_map`, so a new route is
  swept the moment it exists: every parameterless GET must render, and every POST must refuse a
  request without a CSRF token.
- **browser** drives real headless Chromium against the running console — the system box's state
  machine under controlled `/api/system` responses, the Apps page's lazy bodies, the GPS panel,
  and one 390 px phone viewport. No virtual display: Chromium is launched `headless=True`.

- **release** is the evidence a pin release needs, and it is the slowest: on a FRESH lab root
  with no known-working records it installs every stack on its default channel — the published
  binary, else the pin — builds it, starts it and verifies the stack's own state (its port, its
  own client tool, its node info). Interactive components run on a real terminal and must draw,
  stay up and exit; GUI ones run where LHPC's own GUI predicate says they can. It ends by
  re-proving every managed checkout through the production identity verifier and comparing its
  HEAD with the candidate manifest's pin, and every artifact against its receipt. Case names are
  the contract (`test_release_<stack>`) and the required ones are named in
  `testlab/lhpc_testlab/data/required-release-cases.json`, so an automated release can require
  the stack it moved to have PASSED — a skip is not proof, and neither is a count. The lane's
  first case fails if that list and the module ever disagree. A failure at a genuine per-stack
  step carries the one-line marker `STACK-REGRESSION stack=<id> phase=<install|build|start|readiness>`
  in its JUnit failure text; everything else deliberately carries none (the grammar and the
  unmarked sites: `lhpc_testlab.release.stack_regression`). A build is marked only where the
  recipe declares the failed step its own (`attributable = true` in the manifest — a compile, a
  patch, a check over what earlier steps fetched), never at a step that fetches. An automation
  may freeze a stack only on a marked failure.
  - **It runs with `-x`.** The cases chain over one radio pair, so after the first failure
    nothing later is judged in a meaningful state. A stopped run may still supply the attribution
    for its FIRST regression; it can never satisfy the publication gate, which needs every
    required case to have passed. Cleanup still runs, and a stop that fails is a visible teardown
    error.
  - In CI it is the `release-verify` job: pushes to `main`, or a dispatch with
    `release_verify=true` on a candidate branch. It uploads `junit-release.xml`, the recorded
    versions and the lab's own logs.

The route sweep proves existence and CSRF discipline, not each action's effect; effects are
proven by the named acceptance tests and by LHPC's own in-process web suite.

## Codespaces costs

Codespaces bills compute per core-hour and storage per GB-month against your personal free tier
(prebuild storage is billed like storage). A 4-core machine builds comfortably; a prebuild
removes the build wait entirely. Stop the codespace when done (auto-stop defaults to 30 minutes
idle); delete it to stop storage billing. Prebuilds are enabled in the repo's Settings →
Codespaces (needs repo admin).
