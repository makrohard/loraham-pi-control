# Adding & maintaining a stack

Everything LHPC manages is declared in **one manifest** (`lhpc/data/manifest.example.toml`,
copied to `config/manifest.toml` on bootstrap). LHPC never hard-codes an app — you add a
stack by describing it in TOML, and the CLI/web get install / build / test / start / stop /
update for free. This guide uses the **MeshCom (QEMU)** stack as a worked example.

## Contents

- [Mental model](#mental-model)
- [Anatomy of a stack](#anatomy-of-a-stack-meshcom)
  - [Stack header](#stack-header)
  - [A component](#a-component)
  - [Source (what gets cloned)](#source-what-gets-cloned)
  - [Build](#build)
  - [Run & readiness](#run--readiness)
  - [Endpoints](#endpoints)
  - [Parameters & config files](#parameters--config-files)
  - [Resources & dependencies](#resources--dependencies)
- [The lifecycle](#the-lifecycle)
- [Add a new stack](#add-a-new-stack)
- [Maintain an existing stack](#maintain-an-existing-stack)
- [Validate your change](#validate-your-change)

## Mental model

- A **stack** is one runnable app plus its dependency **components**, in a start order.
- Each stack names a **`main`** component (the app itself); the rest are dependencies.
- A **component** with a `source` is cloned into the runtime root under `src/<name>` at a
  **pinned commit**, built there, and run by LHPC (which owns the process).
- LHPC verifies a start by **readiness** (a process is alive, or a `ready = true` endpoint
  came up) and reports a typed outcome — it never assumes.

The MeshCom stack chains four components: `daemon → meshcom-bridge → meshcom-gps-relay →
meshcom-qemu`. The daemon owns the radio; the bridge exposes a TCP port the firmware talks
to; the GPS relay feeds NMEA; QEMU runs the actual MeshCom firmware.

## Anatomy of a stack (MeshCom)

### Stack header

```toml
[[stack]]
id = "meshcom"
name = "MeshCom (QEMU)"
summary = "MeshCom firmware under QEMU, bridged to the daemon. 433 MHz, daemon DIRECT."
main = "meshcom-qemu"          # the app; the others are its dependencies
```

### A component

```toml
  [[stack.component]]
  id = "meshcom-bridge"
  name = "MeshCom <-> LoRaHAM bridge"
  kind = "service"
  band = "433"
  depends_on = ["loraham-daemon"]   # runtime dependency (resolved across stacks)
  requires_daemon_tx = "MANAGED"    # the daemon TX mode this component needs
  start_order = 1                   # lower starts first within the stack
```

### Source (what gets cloned)

`lhpc install` adopts this into `src/meshcom-loraham-bridge` and verifies the pinned commit.
The whole checkout is one clone with one remote.

```toml
    [stack.component.source]
    path = "src/meshcom-loraham-bridge"   # runtime-root-relative
    pin_commit = "fe85c7900f7c095b2e28011ce5fec6125f5fe02a"
    remote = "https://github.com/makrohard/meshcom-loraham-bridge.git"
    branch = "main"
```

### Build

`lhpc build` runs the typed `build_steps` (no shell) in the checkout. `bin` is the built
artifact LHPC checks to decide "is it built".

```toml
  build_steps = [
    { argv = ["cmake", "-S", ".", "-B", "build"] },
    { argv = ["cmake", "--build", "build"] },
  ]
  bin = "build/meshcom-loraham-bridge"
```

QEMU-side (`meshcom-qemu`) builds the firmware image; its `run` is a wrapper script
(`scripts/run.sh`) that launches `qemu-system-xtensa`.

### Run & readiness

`run_argv` is the argv template (literals + `{param:…}` placeholders). `readiness` says how
LHPC verifies the start:

- `process` — the matching process is alive (see `[…​.process]` `exec_name`);
- `endpoint` — every `ready = true` endpoint came up (below);
- `manual` — an interactive TUI the operator runs themselves.

`interactive = true` marks such a TUI: LHPC generates its config and prints the exact
launch command instead of spawning it. `gui_optional = true` on a stack's MAIN component
marks a GUI app a headless box may live without — the build/start preflights drop it
instead of refusing the stack. A non-main interactive component in such a stack (voice's
ncurses variant) is treated as that GUI's **fallback**: offered only where the GUI cannot
run, refused as a direct start target (its config belongs to the main).

```toml
  readiness = "endpoint"
  run = "build/meshcom-loraham-bridge {bind} {port} {backend} {password_file}"

    [stack.component.process]
    exec_name = "meshcom-loraham-bridge"   # identity for ownership + stop
```

**Slow starters:** a component that imports a big stack before opening its port can exceed
the default readiness window — give it a longer one with `readiness_timeout` (seconds, 0 =
default), e.g. `meshcore-node` uses `readiness_timeout = 120.0`.

### Endpoints

A `ready = true` endpoint gates start/stop verification. `role = "provider"`/`"listener"`
endpoints also drive the running-vs-degraded status. TCP ready endpoints must be loopback.

```toml
    [[stack.component.endpoint]]
    kind = "tcp"                 # tcp | unix | path
    address = "127.0.0.1:7000"
    ready = true
    role = "listener"
    description = "MeshCom firmware client port."
```

A `client = true` endpoint with `scheme = "http"` or `"https"` makes the component a proxied
web **page**: it gets its own port and policy in the console's Webserver panel and in
`lhpc webserver proxy`, fronted by nginx with the console's TLS/mTLS gate
([webserver.md](webserver.md#stack-web-ui-proxies)). Optional `proxy_deny_paths` lists exact
request paths the proxy refuses. A stack's first such component is addressed by the stack id;
any further one by `<stack>-<component>`. The derived page ids must be unique across the whole
manifest — checked at load, like component ids. A loopback `address` is what keeps the raw port
off the network; every TCP listener also needs `firewall` metadata and a `tcp.port.<n>` claim.

### Parameters & config files

**Identity params.** Give the stack's identity param the validator that matches its
protocol — that choice decides both the accepted syntax and the enforcement class:
`callsign` (APRS/AX.25: base 3–6 + optional SSID `-1`…`-15`), `callsign_voice` (≤11 chars,
portable forms), `callsign_meshcom` (digit-bearing, suffix `-1`…`-99`, + the firmware's `OE2YOTA-1`
exception) are **licensed** —
they inherit the global operator callsign via a `default = "{callsign}"` while the local
field is empty. `node` (31 UTF-8 bytes), `node_long` (39), `node_short` (4) are
**unlicensed local identities** — give them `default = ""` and never `{callsign}`: they
must be deliberately configured and never inherit an amateur callsign. A start without a
resolvable identity is refused before any lifecycle mutation.

`param` entries become CLI args (start-time) or web **Settings** fields. A `config_file`
lets LHPC generate a component's config from a base, updating just the named keys.

```toml
    [[stack.component.param]]
    name = "port"
    kind = "int"
    arg = "--port"
    default = "7000"
    label = "Control TCP port (= firmware XR_PORT)"
```

### Resources & dependencies

`resource` claims prevent conflicts (two things can't own the same TCP port / radio /
daemon socket). `depends_on` + `start_order` sequence the stack.

```toml
    [[stack.component.resource]]
    key = "tcp.port.7000"
    kind = "tcp-port"
    mode = "exclusive"           # exclusive | provider | consumer | cooperative | requirement
```

## The lifecycle

```bash
lhpc install meshcom --yes   # adopt + verify every component's source (pinned)
lhpc build meshcom           # run each component's build_steps
lhpc test meshcom            # host tests (RX-safe) — optional
lhpc stack start meshcom     # start in order; verify readiness per component
lhpc stack stop meshcom      # identity-verified stop (SIGTERM only), endpoints confirmed gone
lhpc update meshcom --yes    # refresh sources to their pinned/branch state
```

The web console exposes the same actions per stack, each with a plan + confirmation.

## Add a new stack

1. Copy an existing `[[stack]]` block that resembles yours (a daemon-backed app, a QEMU
   app, a socat bridge …) and rename `id` / `name` / `main`.
2. For each component set: `source` (repo + pinned commit), `build_steps` + `bin`,
   `run`/`run_argv` + `readiness` (+ `readiness_timeout` if slow), `process.exec_name`,
   any `endpoint`s (`ready = true` for the one that proves it's up), `param`s, `resource`
   claims, and `depends_on` / `start_order`.
3. Keep RF safety in mind: declare `requires_daemon_tx` and the `band`; LHPC never
   auto-enables TX.
4. `lhpc install <id> --check` → `lhpc build <id>` → `lhpc stack start <id>` and watch the typed
   outcomes.

## Maintain an existing stack

- **Bump a version:** change `source.pin_commit` (and `pin_tag`), then
  `lhpc update <stack> --yes` and re-`build`. If the stack declares `[stack.binary]`
  (daemon, meshtastic, meshcom), the published artifact now lags the new pin: installs of it
  refuse the binary and offer source until [lhpc-binaries](https://github.com/makrohard/lhpc-binaries)
  republishes from the new commits. Pin equality is the whole gate — see
  [provenance](provenance.md#the-binary-channel).
- **Fix a flaky "did not start/verify":** if the app is slow to open its port, raise
  `readiness_timeout`; if a wrapper backgrounds the real process, make sure `process.exec_name`
  matches the process that owns the ready endpoint.
- **Add a setting:** add a `param` (CLI) or a `config_file` key (generated config).
- **Retire a component:** remove it; `lhpc uninstall` refcounts shared checkouts so a
  source used by another stack is never removed out from under it.

## Validate your change

The manifest is validated at load — a bad readiness policy, command token, endpoint, or
`readiness_timeout` fails fast rather than launching a misconfigured process.

```bash
python -m compileall -q lhpc
python -c "from lhpc.core.manifest import load_manifest; print(len(load_manifest()), 'stacks OK')"
pytest -q tests/test_manifest_validation.py tests/test_manifest_model.py tests/test_manifest_graph.py
```
