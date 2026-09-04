# Test lab — the real console without a Raspberry Pi

The test lab runs the REAL LHPC — real Flask console, real CLI, real stack processes,
real installs and builds — against deterministic fake hardware and OS backends. No Pi,
no radio, no root.

## Quick start (one click)

That's the whole thing — you do three clicks and nothing on a command line:

1. Click **[Open in GitHub Codespaces](https://codespaces.new/makrohard/loraham-pi-control)**
   (the badge below). GitHub Codespaces are **x86-only** — there is no ARM machine type to
   pick. Enable the **prebuild** (Settings → Codespaces) so the image is baked with every
   stack already built and boots fast; without a prebuild the first boot SOURCE-BUILDS
   meshcom/meshtastic on x86 (their qemu / sim radio compile in a few minutes each).
2. Wait a few minutes while it builds and sets itself up (install → `init` → `reset`).
   You'll see this happen in the terminal; you don't type anything.
3. The **LHPC console opens in a browser tab by itself** (port 8770). Done — you're in.

The console comes up fast; every other stack (kiss, graywolf, igate, meshcore, reticulum,
voice, sideband, meshcom, meshtastic) then **installs and builds in the background** so it
becomes startable without holding up the web. Populate prefers our aarch64 `lhpc-binaries`
wherever a stack ships one — nothing compiles on the Pi/CI — and falls back to source
otherwise; on the **x86 Codespace** the two stacks with no x86 binary, meshcom (qemu-xtensa)
and meshtastic (sim radio), build from source here, a few minutes each (a prebuild bakes all
of this). Progress is in `~/lhpc-populate.log`; a stack simply appears as **installed** in
the console once its background build finishes.

To run a stack: in the console go to **Apps**, pick a stack (e.g. graywolf), and click
**Start** (or **Install → Build → Start** if you got there before its background install
finished); its web UI appears on its own forwarded port (graywolf 8080, meshcom 18083,
meshcore's Web UI 8788 (its Companion TCP is 5000), meshtastic 9080 — see below). Switch fault scenarios and inject traffic from the **Test Lab** panel (top
banner link). If the tab ever doesn't open, use the **Ports** tab → port **8770** → the
globe icon.

Nothing else is required. The rest of this document is reference.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)

## What is real, what is simulated

| Real | Simulated |
|---|---|
| The web console (waitress), every page/form/confirm flow | The radios: a fake `loraham_daemon` speaks the full v112 wire protocol (raw + framed + CONF sockets) with scenario-driven `RADIO=` state |
| The CLI (`lhpc …`, the installed executable) | NetworkManager (`nmcli` — profiles, scan, join, wrong-password, AP fallback) |
| Stack installs/builds/starts/stops through the production lifecycle (kiss TNC, graywolf, meshcore); on x86 Codespaces meshcom (emulated-ESP32 qemu) and meshtastic (sim radio) **SOURCE-BUILD** from the pinned sources (on the aarch64 Pi/CI they install from our `--source binary` artifacts, no compile) | logind power handshake (`busctl` CanReboot/CanPowerOff) |
| nginx (the stackweb proxies run a real unprivileged nginx driven by the lab supervisor) | `systemctl` (stateful unit model in `state/testlab/units.json`) |
| PKI / certificates (pure Python) | The boot identity: a simulated reboot stops owned stacks, advances the boot id and uptime epoch — the host never reboots |
| gpsd: a real listener on 127.0.0.1:2947 streaming checksum-valid NMEA | The firewall receipt paths (relocated under the lab root via `LHPC_FW_PATH_PREFIX`; the real freshness logic runs on them) |

Never real in the lab: `sudo`, `apt`, `nft`, host shutdown — the lab user is
unprivileged with no sudo (that privilege drop, not an argv filter, is the safety
boundary), and the lab runner refuses direct host mutators with a typed message.

`igate` is redirected to a LOCAL APRS-IS sink (an LD_PRELOAD DNS interposer) so it starts
without ever reaching the live ham network. meshtastic and reticulum run against
simulated radios (meshtastic's upstream `sim` radio via our binary; reticulum's fake
spidev/gpiod shims); voice and sideband are GTK/Kivy GUIs that LAUNCH headless under
Xvfb (not remotely viewable). `chat`, `nomadnet` and voice's terminal variant are interactive TUIs LHPC never
auto-spawns. See `grep limitation testlab/tests/coverage_matrix.toml` for every gap.

## Launch

**Codespace (one click):** the badge above. The container builds from
`.devcontainer/Dockerfile` (apt deps), then onCreate installs + **builds every stack** —
on x86 that compiles meshcom's qemu-xtensa (a few minutes). Enable prebuilds in Settings →
Codespaces to bake all of that so later creations are instant; the console starts
automatically on port **8770** (forwarded privately — only you can open it). Stack UIs:
graywolf on **8080**, meshcom on **18083**, meshcore's Web UI on **8788** (Companion TCP on
**5000**), stackweb proxy pages on **8444–8447**. meshtastic's UI is on **9080**, a plain-HTTP socat bridge `start.sh` runs in
front of meshtasticd's self-signed HTTPS on **:9443** (a Codespace's forwarding proxy 502s
on the self-signed TLS, so open **:9080**, not :9443). A restarted codespace re-runs the
idempotent start script (which also re-establishes the bridge).

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
                         # on ARM also `--source binary` meshcom + meshtastic
lhpc-testlab web         # console (real lhpc + the lab panel) on http://127.0.0.1:8770
```

Activation is a two-key latch: the `LHPC_TESTLAB=1` environment variable AND the
`state/testlab/enabled` marker `init` creates. A production box never has the env var; a
production process pointed at a lab root never activates. When the latch holds, every
page carries the purple **TEST LAB — SIMULATED HARDWARE** banner.

## Scenarios and the Test Lab panel

`lhpc-testlab scenario <name>` (or the panel at `/testlab`): `healthy`, `disconnected`
(AP fallback), `wrong-password`, `hardware-missing` (RADIO=FAILED + a missing
dependency), `degraded` (one band down, gpsd off, stale firewall receipt), `recovery`
(faulty, self-heals after 60 s). Running fakes poll the scenario file and follow within
a second. `lhpc-testlab inject <band> <preset>` queues an RX frame the fake daemon
delivers to the real chain (watch it arrive in graywolf). `lhpc-testlab reset` returns
to the deterministic healthy baseline; `lhpc-testlab check` reports fakes + per-stack
readiness through the production gates.

A simulated **Reboot** (dashboard button) behaves like a real one: owned stacks stop,
the boot identity advances, the admission gate clears on the "new boot" — but the
console stays reachable (it IS the lab; the event log says so).

## Running the verification lanes

```sh
pytest -q                                      # default lane (lab lanes skip)
LHPC_ACCEPTANCE=1 pytest testlab/tests/acceptance -q   # real server + real executable
LHPC_BROWSER=1 pytest testlab/tests/browser -q     # headless Chromium (pip install -e ./testlab[browser])
```

The coverage matrix (`testlab/tests/coverage_matrix.toml`, gated by
`testlab/tests/test_coverage_matrix.py`) forces every route operation, form, CLI
subcommand and stack phase to carry an EXPLICIT coverage class — the build fails on any
undocumented surface — but the classes differ in DEPTH, and the matrix does not claim
every function is fully exercised:

- **`acceptance`** — driven against the RUNNING app/executable (effect verified). ~77 rows.
- **`sweep`** — proven only that the route EXISTS and enforces CSRF (POST rows) or renders
  without mutating (parameterless GETs); the action's EFFECT is not asserted. ~55 rows.
- **`{ limitation = "…" }`** — a documented gap; the reason says whether it is covered by
  lhpc's own in-process web/CLI suite (tested, different layer) or genuinely deferred
  (e.g. running-app form acceptance, per-branch stack lifecycle, real-executable runs of
  destructive/slow verbs that the lane exercises only with `--help`). ~118 rows.

So: every surface is accounted for and the gate blocks silent drift, but many POST
effects, forms and CLI verbs are proven at the CSRF/existence or in-process level, not
end-to-end. `grep limitation` lists every gap. To deepen a row: add a
`@pytest.mark.covers("route:…")` acceptance test and change its class.

CI: `.github/workflows/testlab.yml` runs on a GitHub-hosted **aarch64** runner
(`ubuntu-24.04-arm`), builds the lab image from `.devcontainer/Dockerfile` (the same one
Codespaces builds), and runs the matrix gate + both lanes inside it — so the environment
CI proves is the environment users get, including meshcom/meshtastic from our binaries.

## Codespaces costs

Codespaces bills compute per core-hour and storage per GB-month against your personal
free tier (as of 2026: ~120 core-hours + 15 GB-month free for personal accounts, then
paid; prebuild storage is billed like storage). Codespaces are x86-only; a 4-core machine
builds comfortably (a prebuild removes the build wait entirely). Stop the
codespace when done — auto-stop defaults to 30 minutes idle; delete it to stop storage
billing. Prebuilds are enabled in the repo's Settings → Codespaces (needs repo admin).
