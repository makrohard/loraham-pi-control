# Architecture

## Two roots

- **Dev checkout** (this repo) — the controller's own source.
- **Runtime root** (`~/loraham-pi-control`, override `LHPC_RUNTIME_ROOT`) — created
  by `lhpc bootstrap`. Holds adopted stack sources (`src/`), generated config
  (`config/`), state (`state/`) and logs (`logs/`). Never written to the dev checkout.

## Package layout

```
lhpc/
  core/                  # all behaviour lives here
    model.py             # dataclasses + enums (Stack, Component, RunParam, FileParam, …)
    manifest.py          # parse the TOML manifest into the model
    config.py            # layered config + config-file writers (env/toml/yaml)
    daemon_control.py    # daemon CONF-socket SET/GET (whitelisted)
    lifecycle.py         # spawn/stop processes, build/test jobs, bounded TX test
    status.py            # compose probe evidence into a RunState
    resources.py         # declared/observed conflict interpretation
    install.py           # adopt/verify/update sources (git, pinned)
    jobs.py              # detached job spawn + bounded log tail
    probes/              # read-only bounded probes (process, net, unixsock, systemd, source, hardware)
    services.py          # ControllerService — the single API the adapters call
  adapters/
    cli/main.py          # argparse  → ControllerService → render ActionResult
    web/app.py           # Flask HTTP → ControllerService → server-rendered pages
```

Dependency rule: `adapters/*` import `core/*`; `core/*` never imports `adapters/*`.
Both adapters are thin — they parse input, call one `ControllerService` method, and
render the returned `ActionResult`. The web adapter calls the service directly
(never shells out to the CLI), so validation, gating and results are identical.

## Manifest and config layers

- **Manifest** (`config/manifest.example.toml`): stacks → components. Each component
  declares its `kind`, build/run/test commands, source (remote/branch/pin), resource
  claims, run params and config-file params.
- **Config**, merged in order:
  1. tracked defaults (`config/defaults.toml`) + the manifest;
  2. operator overrides — `~/loraham-pi-control/config/local.toml` (callsign, remotes);
  3. secrets — `config/secrets.toml`, mode `0600` (never tracked, never in output);
  4. per-stack settings — `config/stacks/<id>[@band].toml`, written from the Config page.
- The config file each app reads is generated from its `config_file` params
  (`{callsign}`/`{band}`/`{runtime}`/`{source}` substituted; callsign defaults to `N0CALL`).

## Probes and status

Status is reconstructed on each call, never from a stale PID file: process identity
(`/proc/<pid>/cmdline`), TCP listeners, Unix sockets + a bounded daemon `GET STATUS`,
systemd unit state, and local git source/pin state. Every probe is bounded and turns
errors into evidence. A missing runtime root reports `not-installed`, not an error.
`RunState` ∈ {running, degraded, stopped, failed, unknown, not-applicable, not-installed}.

## Radios and conflicts

The LoRaHAM daemon runs one instance per band (`--radio 433|868|both`), each with its
own CONF socket (`/tmp/loraconf{band}.sock`), raw data socket (`/tmp/lora{band}.sock`)
and framed socket (`/tmp/lora{band}f.sock`). Components declare resource claims (radio
band, SPI bus, daemon socket, TCP port). Starting a stack is blocked if another running
stack holds a band it needs; the daemon shares the SPI bus cooperatively, while a direct
radio user (meshtastic) claims a band exclusively.

## Daemon control

Live settings go to the per-band CONF socket. `SET` is fire-and-forget (the daemon
applies silently and only answers `GET`), so `lhpc` sends the `SET` then reads back with
`GET STATUS` to confirm. Only whitelisted keys (TXMODE, CAD*, radio params) are allowed;
nothing transmits by itself.

## Web security

Loopback bind only (`run_server` refuses any non-loopback host). Mutations are POST +
CSRF token; page loads call only bounded read-only service methods. Every response sends
`Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
no-referrer`, `Content-Security-Policy: default-src 'self'`; Jinja autoescaping is on.
Exposing it to a network would need explicit auth + HTTPS.
