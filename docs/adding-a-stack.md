# Adding & maintaining a stack

Every stack is declared in **one manifest** (`lhpc/data/manifest.example.toml`, shipped as
package data); the CLI and web derive install / build / test / start / stop / update from it.
Worked example: the **MeshCom (QEMU)** stack.

## Contents

- [Mental model](#mental-model)
- [Anatomy of a stack (MeshCom)](#anatomy-of-a-stack-meshcom)
- [The lifecycle](#the-lifecycle)
- [Add a new stack](#add-a-new-stack)
- [Maintain an existing stack](#maintain-an-existing-stack)
- [Validate your change](#validate-your-change)

## Mental model

- A **stack** is one app plus its dependency **components**, in a start order; its **`main`**
  component is the app itself.
- A component with a `source` is adopted into the runtime root at its **pinned commit**, built
  there, run by LHPC and verified by **readiness** ([architecture.md](architecture.md)).

The MeshCom components, start order and pins: [stacks/meshcom.md](stacks/meshcom.md).

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

`lhpc install` adopts this into `src/meshcom-loraham-bridge` at the pinned commit. One source path
is **one checkout with one remote**: every component declaring the same `path` must declare the
identical source spec (checked at load), and a remote override
([provenance.md](provenance.md#remote-overrides)) applies to all of them, and diverging effective
remotes fail destructive operations closed.

```toml
    [stack.component.source]
    path = "src/meshcom-loraham-bridge"   # runtime-root-relative
    pin_commit = "<40-char commit>"   # the manifest carries the real pin
    remote = "https://github.com/makrohard/meshcom-loraham-bridge.git"
    branch = "main"
```

### Commands: two forms

Every component executes [**shell-free**](architecture.md#safety-model):

- **Shorthand** `run` / `build` / `test`: a plain `prog arg arg` line, split on whitespace at
  load into `run_argv` (with `run_cwd = "{source}"`), a one-step `build_steps`, or `test_argv`.
  In `run`, `{name}` becomes `{param:name}`, `{callsign}` becomes `{operator:callsign}`, and
  `{runtime}`, `{source}`, `{band}` stay; `build`/`test` are split verbatim.
- **Structured** `run_argv` / `build_steps` / `test_argv`: required when the command contains
  any of `&& || | ; $( \` > < ${ &` or a shell word token (`cd`, `env`, `export`, `exec`,
  `sleep`, `mkdir`, `chmod`, `ln`, `rm`, `set`). A shorthand with shell syntax fails the
  manifest load; there is no shell fallback.

### Build

`lhpc build` runs `build_steps` in the checkout. `bin` is the file that decides "is it built";
a long build sets `build_timeout`; `build_marker` names a file written only by a completed
build.

```toml
  build_steps = [
    { argv = ["cmake", "-S", ".", "-B", "build"] },
    { argv = ["cmake", "--build", "build"] },
  ]
  bin = "build/meshcom-loraham-bridge"
```

### Run & readiness

`readiness` says how LHPC verifies the start:

- `process`: the `process.exec_name` process is alive;
- `endpoint`: every `ready = true` endpoint came up (below);
- `manual`: an interactive TUI the operator runs themselves;
- `gps-feed`: the feed's own readiness marker (never the endpoint path existing);
- `daemon-band`: the LoRaHAM daemon, verified like `process`;
- `external-systemd`: a `units`-only component (no `run_argv`): LHPC prints
  `sudo systemctl start <unit>` and probes the unit.

`interactive = true` marks such a TUI: LHPC generates its config and prints the launch command
instead of spawning it. `gui_optional = true` on a stack's `main` marks a GUI app a headless box
may lack; build and start drop it instead of refusing the stack. A non-main interactive component
in such a stack (voice's ncurses variant) is the GUI's **fallback**: offered only where the GUI
cannot run, refused as a direct start target.

```toml
  readiness = "endpoint"
  run = "build/meshcom-loraham-bridge {bind} {port} {backend} {password_file} {ping_interval} {pong_timeout}"

    [stack.component.process]
    exec_name = "meshcom-loraham-bridge"   # identity for ownership + stop
```

**Slow starters** set `readiness_timeout` (seconds, 0 = default, at most 600), e.g.
`meshcore-node` uses `120.0`. `reads_position = true` marks a component that consumes the
global position ([GPS](gps.md)).

### Endpoints

A `ready = true` endpoint gates start/stop verification (TCP ones must be loopback);
`role = "provider"`/`"listener"` endpoints also drive running-vs-degraded status.

```toml
    [[stack.component.endpoint]]
    kind = "tcp"                 # tcp | unix | path
    address = "127.0.0.1:7000"
    ready = true
    role = "listener"
    description = "MeshCom firmware client port."
```

A `client = true` endpoint with `scheme = "http"` or `"https"` makes the component a proxied web
**page** behind the console's nginx TLS/mTLS gate ([webserver](webserver.md#stack-web-ui-proxies)).
Optional `proxy_deny_paths` lists request paths the proxy refuses, spelling-tolerant (`/api/x`
also refuses `/api/x/…`, `/api-x`, `/api.x`). A stack's first page is addressed by the stack id,
any further one by `<stack>-<component>`; "first" is manifest order with `main` sorted last. Page
ids must not collide (checked at load). A loopback `address` keeps the raw port off the network;
every TCP listener also needs `firewall` metadata and a `tcp.port.<n>` claim.

### Parameters & config files

`param` entries become CLI args (from the saved values at start,
[config layers](architecture.md#manifest-and-config-layers)) and web **Settings** fields. A
`config_file` generates a component's config from a base, updating only the named keys.

```toml
    [[stack.component.param]]
    name = "port"
    kind = "int"
    arg = "--port"
    default = "7000"
    label = "Control TCP port (= firmware XR_PORT)"
```

**Identity params.** The validator decides the accepted syntax and the enforcement class
([identity rules](architecture.md#identity-and-callsigns)):

| Validator | Accepts | Class | `default` |
|---|---|---|---|
| `callsign` | APRS/AX.25: base 3–6 chars + optional SSID `-1`…`-15` | licensed | `"{callsign}"` |
| `callsign_voice` | ≤ 11 chars, portable forms (`/`, `-`) | licensed | `"{callsign}"` |
| `callsign_meshcom` | digit-bearing base, suffix `-1`…`-99`, plus the firmware's `OE2YOTA-1` exception | licensed | `"{callsign}"` |
| `node` | ≤ 31 UTF-8 bytes | unlicensed local identity | `""` |
| `node_long` / `node_short` | ≤ 39 / ≤ 4 UTF-8 bytes | unlicensed local identity | `""` |

### Resources & dependencies

`resource` claims stop two components owning the same TCP port, radio or daemon socket (modes:
[architecture.md](architecture.md#radios-bands-and-resource-claims)). `depends_on` and
`start_order` sequence the stack.

```toml
    [[stack.component.resource]]
    key = "tcp.port.7000"
    kind = "tcp-port"
    mode = "exclusive"           # exclusive | provider | consumer | cooperative | requirement
```

## The lifecycle

```bash
lhpc install meshcom --source pinned --yes   # adopt + verify every component's source at its pin
lhpc build meshcom           # run each component's build_steps
lhpc test meshcom            # host tests (RX-safe), optional
lhpc stack start meshcom     # start in order; verify readiness per component
lhpc stack stop meshcom      # identity-verified stop (SIGTERM only), endpoints confirmed gone
lhpc update meshcom --yes    # binary stays binary, source goes to its pin
```

The web console has the same actions per stack.

## Add a new stack

1. Copy a similar `[[stack]]` block and rename `id` / `name` / `main`.
2. Give each component the sections above, plus `requires_daemon_tx` and `band`
   ([TX safety](operations.md#tx-safety)).
3. `lhpc install <id> --check` → `lhpc build <id>` → `lhpc stack start <id>`; read the typed
   outcomes.

## Maintain an existing stack

- **Bump a version:** [maintenance.md → Moving a pin](maintenance.md#moving-a-pin).
- **Flaky "did not start/verify":** raise `readiness_timeout` for a slow port; for a wrapper
  that backgrounds the real process, set `process.exec_name` to the process owning the ready
  endpoint.
- **Add a setting:** add a `param` (CLI) or a `config_file` key (generated config).
- **Retire a component:** remove it. `lhpc uninstall` removes a source path only when no
  remaining component declares it or reaches it through `build_requires`; a shared checkout is
  kept and records the departing components.

## Validate your change

The manifest is validated at load: a bad readiness policy, command, endpoint, page id or
`readiness_timeout` fails the load.

```bash
python -m compileall -q lhpc
python -c "from lhpc.core.manifest import load_manifest; print(len(load_manifest()), 'stacks OK')"
pytest -q tests/stacks/test_manifest_validation.py tests/stacks/test_manifest_model.py tests/stacks/test_manifest_graph.py
```
