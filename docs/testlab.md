# Test lab: the real console without a Raspberry Pi

The test lab runs the real LHPC (Flask console, CLI, stack processes, installs and builds)
against deterministic fake hardware and OS backends. No Pi, no radio, no root.

## Contents

- [Quick start (one click)](#quick-start-one-click)
- [What is real, what is simulated](#what-is-real-what-is-simulated)
- [Launch](#launch)
- [Scenarios and the Test Lab panel](#scenarios-and-the-test-lab-panel)
- [Running the verification lanes](#running-the-verification-lanes)
- [Codespaces costs](#codespaces-costs)

## Quick start (one click)

1. Click **[Open in GitHub Codespaces](https://codespaces.new/makrohard/loraham-pi-control)**.
   Codespaces are **x86-only**. Enable the **prebuild** (Settings → Codespaces, needs repo
   admin) so the image is baked with every stack built; without one the first boot
   source-builds meshcom and meshtastic, a few minutes each.
2. Wait while it sets itself up (install → `init` → `reset`).
3. The **LHPC console opens in a browser tab** (port 8770); if not: **Ports** tab → **8770** →
   the globe icon.

The other stacks (kiss, graywolf, meshcore, reticulum with sideband, voice, meshcom, meshtastic) then
install and build in the background (binary channel on aarch64 wherever a stack ships one, else
source; log: `~/lhpc-populate.log`). To run one: **Apps** → the stack → **Start** (**Install →
Build → Start** if its background install has not finished). Fault scenarios and traffic injection:
the **Test Lab** panel (top banner link).

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)

## What is real, what is simulated

| Real | Simulated |
|---|---|
| The web console (waitress), every page/form/confirm flow | The radios: a fake `loraham_daemon` speaks the v112 wire protocol (raw + framed + CONF sockets) with scenario-driven `RADIO=` state |
| The CLI (`lhpc …`, the installed executable) | NetworkManager (`nmcli`: profiles, scan, join, wrong-password, AP fallback) |
| The headless stacks (`populate`: kiss, graywolf, meshcore, reticulum with sideband, voice, meshcom, meshtastic) install/build/start/stop through the production lifecycle; on x86 meshcom (emulated-ESP32 qemu) and meshtastic (sim radio) source-build from the pinned sources, on aarch64 they install from the binary channel | logind power handshake (`busctl` CanReboot/CanPowerOff) |
| nginx (the stackweb proxies run a real unprivileged nginx driven by the lab supervisor) | `systemctl` (stateful unit model in `state/testlab/units.json`) |
| PKI / certificates (pure Python) | The boot identity: a simulated reboot stops owned stacks, advances the boot id and uptime epoch; the host never reboots |
| gpsd: a real listener on 127.0.0.1:2947 streaming checksum-valid NMEA | The firewall receipt paths (relocated under the lab root via `LHPC_FW_PATH_PREFIX`; the real freshness logic runs on them) |

Never real in the lab: `sudo`, `apt`, `nft`, host shutdown. The lab user is unprivileged with
no sudo — that privilege drop, not an argv filter, is the safety boundary; the lab additionally
refuses direct host mutators with a typed message.

The manifest overlay forces Graywolf's APRS-IS uplink to a local sink (`127.0.0.1:14580`), never
the live network. meshtastic runs upstream's `sim` radio, reticulum fake spidev/gpiod shims;
voice and sideband (GTK/Kivy) run headless under Xvfb, not viewable. `chat`, `nomadnet` and
voice's terminal variant are interactive TUIs LHPC never auto-spawns.

## Launch

**Codespace:** the badge above builds from `.devcontainer/Dockerfile`, onCreate installs and
builds every stack, and the console starts on **8770** (forwarded privately); a restart re-runs
the idempotent start script. Stack UIs: graywolf **8080**, meshcom **18083**, meshcore's Web UI
**8788** (Companion TCP **5000**), meshtastic **9080** (a plain-HTTP socat bridge to
meshtasticd's self-signed `:9443`, which the Codespace proxy 502s on). **8444–8447** are
forwarded for stack proxy pages, which you create on the Webserver page.

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
                         # on aarch64 also binary-channel meshcom + meshtastic
lhpc-testlab web         # console (real lhpc + the lab panel) on http://127.0.0.1:8770
```

Activation is a two-key latch: `LHPC_TESTLAB=1` in the environment AND the
`state/testlab/enabled` marker from `init`; a production process lacks the env var and never
activates. While active, every page carries the purple **TEST LAB — SIMULATED HARDWARE** banner.

## Scenarios and the Test Lab panel

`lhpc-testlab scenario <name>` (or the panel at `/testlab`): `healthy`, `disconnected` (AP
fallback), `wrong-password`, `hardware-missing` (RADIO=FAILED + a missing dependency),
`degraded` (one band down, gpsd off, stale firewall receipt), `recovery` (faulty, self-heals
after 60 s). Running fakes poll the scenario file and follow within a second.

- `lhpc-testlab inject <433|868> <preset>` queues an RX frame the fake daemon delivers to the
  real chain (watch it arrive in graywolf).
- `lhpc-testlab reset` returns to the deterministic healthy baseline.
- `lhpc-testlab check` reports the fakes (gpsd, the APRS-IS sink; a fake that is down fails it)
  and per-stack readiness through the production gates;
  `lhpc-testlab status` shows scenario, simulated boot and recent lab events.

The dashboard's **Reboot** is simulated: owned process groups are killed (their ownership records
kept, as after a power cut), the boot identity advances and the production boot restore runs (its
journal: `state/boot-restore.json`); its one gate that reads the host's systemd user units counts
as passed, because the lab's console is not a user unit. The console stays up (the event log says
so).

## Running the verification lanes

```sh
python -m pytest -q testlab/tests                # unit lane; the opt-in lanes skip
LHPC_ACCEPTANCE=1 pytest testlab/tests/acceptance -q   # real server + real executable
LHPC_BROWSER=1 pytest testlab/tests/browser -q     # headless Chromium (pip install -e ./testlab[browser])
LHPC_RELEASE_VERIFY=1 pytest testlab/tests/release -q -x  # release lane: every stack installed, built, started
LHPC_SLOW_BUILD=1 SLOW_CPUS=0.25 SLOW_MEM=416m SLOW_WRITE_IOPS=120 SLOW_READ_IOPS=1200 \
  pytest testlab/tests/slowbuild -q             # slow-build lane: only inside the throttled container
```

**From a worktree, prefix these with `PYTHONPATH=$PWD`** (or re-run
`pip install -e . && pip install -e ./testlab` there): the lab lanes' console and CLI
subprocesses import `lhpc` from the editable install, i.e. whichever checkout was
`pip install -e`'d, and can pass on another checkout's code. CI installs from the commit itself.

`testlab/tests/unit` tests the simulator itself; the four opt-in lanes:

- **acceptance** drives the running server and the real `lhpc` executable and verifies the
  effect. `test_http_smoke.py` sweeps the app's own `url_map`: every parameterless GET must
  render, every POST must refuse a request without a CSRF token (existence and CSRF discipline,
  not effects — those are the named acceptance tests and LHPC's in-process web suite).
- **browser** drives headless Chromium (no virtual display) against the running console: the
  system box under controlled `/api/system` responses, the Apps page's lazy bodies, the GPS
  panel, a 390 px phone viewport.
- **release** is the evidence a pin release needs. On a fresh lab root with no known-working
  records it installs every stack on its default channel (published binary, else the pin),
  builds, starts and verifies it by its own state (port, client tool, node info); interactive
  components must draw, stay up and exit on a real terminal, GUI ones run where LHPC's GUI
  predicate allows. It ends by re-proving every managed checkout with the production identity
  verifier against the candidate manifest's pin, and every artifact against its receipt.
  - Case names are the contract (`test_release_<stack>`); the required ones are listed in
    `testlab/lhpc_testlab/data/required-release-cases.json`, so an automated release requires
    the stack it moved to have PASSED (a skip or a count is not proof). The first case fails if
    that list and the module disagree.
  - Only a failure at a genuine per-stack step carries the JUnit marker
    `STACK-REGRESSION stack=<id> phase=<install|build|start|readiness>` (grammar and unmarked
    sites: `lhpc_testlab.release.stack_regression`); a build only at a step the manifest marks
    `attributable = true` (a compile, a patch, a check over fetched code), never at a fetch. An
    interactive program's readiness is marked only when it exited or drew a traceback or an
    exception line; a timeout without that evidence is unmarked and carries the tails of its logs.
    A regression that only hangs is therefore unmarked: the release stops at its proof, nothing is
    released and nothing frozen (a deliberate trade). An automation may freeze a stack only on a
    marked failure.
  - **It runs with `-x`:** the cases chain over one radio pair, so nothing after the first
    failure is meaningful. A stopped run attributes its first regression but never satisfies the
    publication gate (every required case passed). Cleanup still runs; a failed stop is a
    teardown error.
  - In CI it is the `release-verify` job ([when it runs](maintenance.md#what-ci-enforces)). It
    uploads `junit-release.xml`, the recorded versions and the lab's own logs.
- **slow-build** is row C of the [slow-target build row](maintenance.md#branches-and-releases):
  the CI job `slow-build` runs it inside a container throttled to a Pi Zero 2 W's CPU, disk and
  memory (`--cpus=$SLOW_CPUS --memory=$SLOW_MEM --memory-swap=$SLOW_SWAP` and
  `--device-write-iops`/`--device-read-iops` on the disk behind the container's writable layer,
  measured as the overlay upperdir a container from the lab image reports for its root (a root
  that is not an overlay stops the job), on the disk behind Docker's storage, which holds the
  `/tmp` volume, each of which must resolve or the job stops before measuring, and on each
  resolvable swap disk; the numbers and their measured rationale are the job's `env`). Under the production limits it times
  each lane stack's install (clone, checkout, the Meshtastic CLI venv), every component's build
  (wall time and longest quiet gap), the graywolf upstream fetch and a self-update from the
  previous release tag, then checks them with `lhpc.core.slow_target` against
  `tests/data/slow-target-builds.toml`.
  - `test_slow_build_env` fails unless the throttle is in force — `io.max` holds a tight line for
    the writable layer's disk the job measured (`SLOW_IO_ROOT_DISK`) and for the disk behind the
    calibration work dir, each by name — and no
    `LHPC_BUILD_*` override is set; nothing is recorded otherwise. `test_slow_build_calibrated` runs
    `testlab/slowbuild/calibrate.sh` and fails while the container is faster than the Zero on any
    part (cpu, io, mem) of the same workload (then tighten that part's throttle; a throttle never
    loosens). `test_slow_build_budget` names every
    operation over budget or without evidence.
  - The job fails unless those three cases, every stack case and the self-update case PASSED
    (a skip is not a pass; the one exception is the
    [bootstrap state](maintenance.md#branches-and-releases)) and uploads
    `slow-build-evidence`: `slow-target-builds.toml` (`source = "throttled-ci"`), the JUnit and the
    E/Z summary, which is also the job summary.

## Codespaces costs

Compute is billed per core-hour and storage (prebuilds included) per GB-month against your
free tier. 4 cores build comfortably. Stop the codespace when done (auto-stop: 30 min idle by
default); delete it to stop storage billing.
