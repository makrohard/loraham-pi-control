# Architecture

## Contents

- [The runtime root](#the-runtime-root)
- [Package layout](#package-layout)
- [Manifest and config layers](#manifest-and-config-layers)
- [Probes and status](#probes-and-status)
- [Radios and conflicts](#radios-and-conflicts)
- [Daemon control](#daemon-control)
- [Web security](#web-security)
- [Safety hardening](#safety-hardening)
- [Controller identity & self-update](#controller-identity--self-update)

## The runtime root

Everything lives under one **runtime root** (`~/loraham-pi-control`, override
`LHPC_RUNTIME_ROOT`) — a plain container created by `lhpc bootstrap` (mode `0700`): adopted
stack sources (`src/`), generated config (`config/`), state (`state/`), logs (`logs/`) and
the venv (`venv/lhpc`).

What differs between setups is only **where LHPC's own checkout sits** relative to that root:

- **Self-hosted deployment (recommended)** — the checkout lives *inside* the runtime root at
  `src/loraham-pi-control`, alongside the stacks it manages; the venv sits outside it at
  `venv/lhpc`. `install.sh` sets this up, so `lhpc self-update` and the running code are one
  tree.
- **Dev checkout** — the checkout lives somewhere else entirely (you edit/commit/push from
  it) with its own venv; the runtime root is separate. Intentionally *not* self-hosted.
- **Tangled (legacy, tolerated)** — the checkout *is* the runtime root (older installs
  cloned LHPC directly onto `~/loraham-pi-control`). Still works; self-update is only
  unambiguous once migrated to self-hosted.

LHPC never writes into a checkout except via `lhpc self-update`; the controller-identity
check (below) reports which of these you are in.

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
    services.py            # ControllerService facade: init/state/locks, manifest, status,
                           # bootstrap/install plans — composes the service_* mixins below
    service_base.py        # shared types: ActionResult, ConfigWrite, typed exceptions
    service_webserver.py   # nginx/TLS/mTLS console + per-stack proxy operations
    service_selfupdate.py  # controller self-update orchestration + updater integration
    service_auto_install.py        # auto-install / ai-run driver, markers, log streaming
    service_maintenance.py # source update / uninstall / clean / known-working / source-check
    service_params.py      # param & config resolution, saves, config-file generation, daemon params
    service_lifecycle_ops.py # start/stop/restart/build/test orchestration, jobs, dashboards
  adapters/
    cli/main.py          # argparse  → ControllerService → render ActionResult
    web/app.py           # Flask HTTP → ControllerService → server-rendered pages
```

Dependency rule: `adapters/*` import `core/*`; `core/*` never imports `adapters/*`.
Adapters import only `lhpc.core.services`; the `service_*` modules are internal mixins of
`ControllerService` (composed by the `services.py` facade) and are never imported by adapters.
Both adapters are thin — they parse input, call one `ControllerService` method, and
render the returned `ActionResult`. The web adapter calls the service directly
(never shells out to the CLI), so validation, gating and results are identical.

## Manifest and config layers

- **Manifest** (`lhpc/data/manifest.example.toml`, shipped as package data): stacks →
  components. Each component declares its `kind`, build/run/test commands, source
  (remote/branch/pin), resource claims, run params and config-file params.
- **Config**, merged in order:
  1. tracked defaults (`lhpc/data/defaults.toml`) + the manifest;
  2. operator overrides — `~/loraham-pi-control/config/local.toml` (callsign, remotes);
  3. secrets — `config/secrets.toml`, mode `0600` (never tracked, never in output);
  4. per-stack settings — `config/stacks/<id>[@band].toml`, written from a stack's Settings.
- The config file each app reads is generated from its `config_file` params
  (`{callsign}`/`{band}`/`{runtime}`/`{source}` substituted; callsign defaults to `N0CALL`).

## Probes and status

Status is reconstructed on each call, never from a stale PID file: process identity
(`/proc/<pid>/cmdline`), TCP listeners, Unix sockets + a bounded daemon `GET STATUS`,
systemd unit state, and local git source/pin state. Every probe is bounded and turns
errors into evidence. A missing runtime root reports `not-installed`, not an error.
`RunState` ∈ {running, degraded, stopped, failed, unknown, not-applicable, not-installed}.

## Radios and conflicts

The LoRaHAM daemon runs one instance per band (`--radio 433|868`), each with its
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
Network exposure is provided by the production front-end (nginx terminating HTTPS + mTLS, opt-in
behind a typed confirmation), not by binding the app to a public address — see
[`webserver.md`](webserver.md).

## Safety hardening

All normal lifecycle execution is **structured argv with `shell=False`** (`core/commands.py`
builds argv from a manifest token template; typed pre/post steps run in Python or a
generated launcher — no shell). User values are validated by type (`core/validators.py`)
and become individual argv tokens; they cannot inject. Each launch is recorded with
full process identity (`state/owned/`); `stop` is record-driven and identity-verified,
signalling only an LHPC-owned session leader whose pid/start-time/pgid/sid/exec/argv
still match (the daemon/iGate run foreground, no `-d`). `resolve_source` confines source
dirs lexically (links allowed, observe-only, never built/tested into); `under` adds
symlink-escape rejection for mutable runtime paths, and atomic writes / log opens refuse
a pre-existing symlink leaf (`O_NOFOLLOW`). A per-stack Settings save is one validate-first,
all-or-recoverable bundle transaction; its journal uses logical target kinds + an
allowlist and blocks fail-closed on any malformed/malicious journal. Lifecycle stop is
typed (`core/outcomes.py`): a verified stop requires process cessation AND ready-endpoint
disappearance, markers clear only on a verified stop, and restart/owner-stop/cascade
propagate typed failures. End-to-end `CompResult` aggregation through the start loop remains
open — see `docs/hardening-0.1.md`. Manual `start/` wrappers are RETIRED: lhpc starts
services itself, interactive components get their copy-paste command on the dashboard
(rendered from the same structured spec), and bootstrap prunes legacy wrapper files.

## Controller identity & self-update

LHPC's own checkout is a **dedicated controller identity** — a top-level `[controller]`
manifest table (strict allow-list; fixed `source_path = "src/loraham-pi-control"` and
`branch = "main"`), NOT a stack. It is observable and self-updatable but never installed,
built, started, cleaned, or auto-install-processed: every generic verb aimed at its id refuses in
the central service layer and points to `lhpc self-update`.

`controller_identity_live()` reports a **tri-state** verdict, used only at startup refresh,
explicit "check now", and immediately before an apply:

- **ok** — self-hosted and verified: the checkout is under the runtime root, no symlink in
  the `root → src → checkout` chain, owned by the service user with no group/other write,
  its realpath equals both `repo_root()` and the imported package, on `main` with the
  approved canonical `origin`.
- **unsafe** — self-hosted but tampered/misconfigured (symlink, group-writable, wrong
  branch/origin, mismatch). **Blocks apply.**
- **not_applicable** — *not* self-hosted (a dev checkout or a plain/tangled deployment).
  Neutral: does not block; self-update proceeds via the normal `repo_root()` mechanism.

Status GETs are **cached-only**: they render the last verdict from a single versioned,
schema-validated self-update envelope — never a live git/network/identity call. `lhpc web`
holds a shared controller-runtime flock for its lifetime; `self-update --apply` takes it
exclusive first (then the self-update lock), so a running server can never have its own
source mutated underneath it.
