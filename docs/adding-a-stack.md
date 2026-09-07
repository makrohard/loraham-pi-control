# Adding & maintaining a stack

Everything LHPC manages is declared in **one manifest** (`lhpc/data/manifest.example.toml`,
shipped as package data). LHPC never hard-codes an app: you add a stack by
describing it in TOML, and the CLI/web get install / build / test / start / stop / update for
free. This guide uses the **MeshCom (QEMU)** stack as a worked example.

## Contents

- [Mental model](#mental-model)
- [Anatomy of a stack (MeshCom)](#anatomy-of-a-stack-meshcom)
- [The lifecycle](#the-lifecycle)
- [Add a new stack](#add-a-new-stack)
- [Maintain an existing stack](#maintain-an-existing-stack)
- [Validate your change](#validate-your-change)

## Mental model

- A **stack** is one runnable app plus its dependency **components**, in a start order.
- Each stack names a **`main`** component (the app itself); the rest are dependencies.
- A **component** with a `source` is adopted into the runtime root under `src/<name>` at a
  **pinned commit**, built there, and run by LHPC (which owns the process).
- LHPC verifies a start by **readiness** (a process is alive, or a `ready = true` endpoint came
  up) and reports a typed outcome; it never assumes.

The MeshCom stack chains `loraham-daemon → meshcom-bridge → meshcom-gps → meshcom-qemu` (plus the `meshcom-firmware` source component and the `meshcom-gps-relay` test fixture). The daemon owns the radio; the bridge exposes a TCP port the
firmware talks to; the GPS feed carries the global position; QEMU runs the MeshCom firmware.

## Anatomy of a stack (MeshCom)

### Stack header

```toml
[[stack]]
id = "meshcom"
name = "MeshCom (QEMU)"
summary = "MeshCom firmware under QEMU, bridged to the daemon. 433 MHz, daemon MANAGED."
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

`lhpc install` adopts this into `src/meshcom-loraham-bridge` and verifies the pinned commit. One
source path is **one checkout with one remote**: every component that declares the same `path`
shares it, a remote override is applied to all of them in one write, and diverging effective
remotes fail destructive operations closed.

```toml
    [stack.component.source]
    path = "src/meshcom-loraham-bridge"   # runtime-root-relative
    pin_commit = "<40-char commit>"   # the manifest carries the real pin
    remote = "https://github.com/makrohard/meshcom-loraham-bridge.git"
    branch = "main"
```

### Commands: two forms

Every component executes **shell-free**. Two ways to say a command:

- **Shorthand** `run` / `build` / `test`: a plain `prog arg arg` line with no shell syntax. At
  load it is split on whitespace into `run_argv` (with `run_cwd = "{source}"`), a one-step
  `build_steps`, or `test_argv`. In `run`, `{name}` placeholders become `{param:name}` (`{callsign}` becomes `{operator:callsign}`; `{runtime}`, `{source}` and `{band}` stay as they are); `build`/`test` shorthand is split verbatim.
- **Structured** `run_argv` / `build_steps` / `test_argv`: required for anything with shell
  semantics, i.e. any of `&& || | ; $( \` > < ${ &` or a shell word (`cd`, `env`, `export`,
  `exec`, `sleep`, `mkdir`, `chmod`, `ln`, `rm`, `set`) as a token. There is no shell fallback:
  such a line is not tokenized, and a component without a structured `run_argv` cannot be
  started ("no structured run command").

### Build

`lhpc build` runs the typed `build_steps` in the checkout. `bin` is the built artifact LHPC
checks to decide "is it built"; a long build sets `build_timeout`, and `build_marker` names a
file written only by a completed build.

```toml
  build_steps = [
    { argv = ["cmake", "-S", ".", "-B", "build"] },
    { argv = ["cmake", "--build", "build"] },
  ]
  bin = "build/meshcom-loraham-bridge"
```

`meshcom-qemu` builds the firmware image; its `run` is a wrapper script (`scripts/run.sh`) that
launches `qemu-system-xtensa`.

### Run & readiness

`run_argv` is the argv template (literals + `{param:…}` placeholders). `readiness` says how LHPC
verifies the start:

- `process`: the matching process is alive (see `[….process]` `exec_name`);
- `endpoint`: every `ready = true` endpoint came up (below);
- `manual`: an interactive TUI the operator runs themselves;
- `gps-feed`: the feed's own readiness marker (never the endpoint path existing);
- `daemon-band`: the LoRaHAM daemon, verified like `process`;
- `external-systemd`: a `units`-only component (no `run_argv`): LHPC prints
  `sudo systemctl start <unit>` and probes the unit.

`interactive = true` marks such a TUI: LHPC generates its config and prints the exact launch
command instead of spawning it. `gui_optional = true` on a stack's MAIN component marks a GUI
app a headless box may live without; the build/start preflights drop it instead of refusing the
stack. A non-main interactive component in such a stack (voice's ncurses variant) is that GUI's
**fallback**: offered only where the GUI cannot run, refused as a direct start target.

```toml
  readiness = "endpoint"
  run = "build/meshcom-loraham-bridge {bind} {port} {backend} {password_file} {ping_interval} {pong_timeout}"

    [stack.component.process]
    exec_name = "meshcom-loraham-bridge"   # identity for ownership + stop
```

**Slow starters:** a component that imports a big stack before opening its port can exceed the
default readiness window; give it a longer one with `readiness_timeout` (seconds, 0 = default),
e.g. `meshcore-node` uses `readiness_timeout = 120.0`. `reads_position = true` marks a
component that consumes the global position ([GPS](gps.md)).

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

A `client = true` endpoint with `scheme = "http"` or `"https"` makes the component a proxied web
**page**: its own port and policy in the console's Webserver panel and in `lhpc webserver
proxy`, fronted by nginx with the console's TLS/mTLS gate ([webserver](webserver.md)). Optional
`proxy_deny_paths` lists request paths the proxy refuses, spelling-tolerant: `/api/x` also
refuses `/api/x/…`, `/api-x` and `/api.x`. A stack's first such component is addressed by the
stack id, any further one by `<stack>-<component>`; "first" is manifest order with the stack's
`main` component sorted last, so a dedicated web component keeps the stack id even when the main
component later grows a dashboard. Page ids must be unique across the manifest (checked at load,
like component ids). A loopback `address` keeps the raw port off the network; every TCP listener
also needs `firewall` metadata and a `tcp.port.<n>` claim.

### Parameters & config files

`param` entries become CLI args (built from the SAVED values at start) and web **Settings**
fields; Settings is the only place a value changes, a start never takes per-launch input. A
`config_file` lets LHPC generate a component's config from a base, updating just the named keys.

```toml
    [[stack.component.param]]
    name = "port"
    kind = "int"
    arg = "--port"
    default = "7000"
    label = "Control TCP port (= firmware XR_PORT)"
```

**Identity params.** The validator you give the stack's identity param decides both the accepted
syntax and the enforcement class ([identity rules](architecture.md)):

| Validator | Accepts | Class | `default` |
|---|---|---|---|
| `callsign` | APRS/AX.25: base 3–6 chars + optional SSID `-1`…`-15` | licensed | `"{callsign}"` |
| `callsign_voice` | ≤ 11 chars, portable forms (`/`, `-`) | licensed | `"{callsign}"` |
| `callsign_meshcom` | digit-bearing base, suffix `-1`…`-99`, plus the firmware's `OE2YOTA-1` exception | licensed | `"{callsign}"` |
| `node` | ≤ 31 UTF-8 bytes | unlicensed local identity | `""` |
| `node_long` / `node_short` | ≤ 39 / ≤ 4 UTF-8 bytes | unlicensed local identity | `""` |

Licensed params inherit the global operator callsign through `default = "{callsign}"` while
the local field is empty. Unlicensed local identities get `default = ""` and never `{callsign}`:
they must be deliberately configured. A start without a resolvable identity is refused by the
plan, before any lifecycle mutation, so the web can send the operator to the exact Settings row.

### Resources & dependencies

`resource` claims prevent conflicts (two things can't own the same TCP port / radio / daemon
socket). `depends_on` + `start_order` sequence the stack.

```toml
    [[stack.component.resource]]
    key = "tcp.port.7000"
    kind = "tcp-port"
    mode = "exclusive"           # exclusive | provider | consumer | cooperative | requirement
```

## The lifecycle

```bash
lhpc install meshcom --source pinned --yes   # adopt + verify every component's source at its pin (bare install takes the binary where published)
lhpc build meshcom           # run each component's build_steps
lhpc test meshcom            # host tests (RX-safe), optional
lhpc stack start meshcom     # start in order; verify readiness per component
lhpc stack stop meshcom      # identity-verified stop (SIGTERM only), endpoints confirmed gone
lhpc update meshcom --yes    # refresh on the current channel (see cli.md § update); --source pinned for the pin
```

The web console exposes the same actions per stack, each with a plan + confirmation.

## Add a new stack

1. Copy an existing `[[stack]]` block that resembles yours (a daemon-backed app, a QEMU app, a
   socat bridge …) and rename `id` / `name` / `main`.
2. For each component set: `source` (repo + pinned commit), `build_steps` + `bin`,
   `run`/`run_argv` + `readiness` (+ `readiness_timeout` if slow), `process.exec_name`, any
   `endpoint`s (`ready = true` for the one that proves it's up), `param`s, `resource` claims,
   and `depends_on` / `start_order`.
3. Keep RF safety in mind: declare `requires_daemon_tx` and the `band`; LHPC never
   auto-enables TX.
4. `lhpc install <id> --check` → `lhpc build <id>` → `lhpc stack start <id>` and watch the typed
   outcomes.

## Maintain an existing stack

- **Bump a version:** the pin bump recipe is in [maintenance](maintenance.md); the binary-channel
  consequence of a new pin is in [provenance](provenance.md).
- **Fix a flaky "did not start/verify":** if the app is slow to open its port, raise
  `readiness_timeout`; if a wrapper backgrounds the real process, make sure `process.exec_name`
  matches the process that owns the ready endpoint.
- **Add a setting:** add a `param` (CLI) or a `config_file` key (generated config).
- **Retire a component:** remove it. `lhpc uninstall` removes a source path only when no
  remaining component declares it (or reaches it through `build_requires`); a checkout shared
  with another stack is kept, and the departing components are recorded as having left it.

## Validate your change

The manifest is validated at load: a bad readiness policy, command token, endpoint, duplicate
page id, or `readiness_timeout` fails fast rather than launching a misconfigured process.

```bash
python -m compileall -q lhpc
python -c "from lhpc.core.manifest import load_manifest; print(len(load_manifest()), 'stacks OK')"
pytest -q tests/test_manifest_validation.py tests/test_manifest_model.py tests/test_manifest_graph.py
```
