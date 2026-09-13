# Architecture

## Contents

- [The runtime root](#the-runtime-root)
- [Package layout](#package-layout)
- [Manifest and config layers](#manifest-and-config-layers)
- [Identity and callsigns](#identity-and-callsigns)
- [Probes and status](#probes-and-status)
- [Radios, bands and resource claims](#radios-bands-and-resource-claims)
- [Daemon control](#daemon-control)
- [Safety model](#safety-model)
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
  tree ([deployment.md](deployment.md)).
- **Dev checkout** — the checkout lives somewhere else entirely (you edit/commit/push from
  it) with its own venv; the runtime root is separate. Intentionally *not* self-hosted.

LHPC never writes into a checkout except via `lhpc self-update`; the controller-identity
check (below) reports which of these you are in.

## Package layout

```
lhpc/
  core/                  # all behaviour lives here
    model.py             # dataclasses + enums (Stack, Component, RunParam, FileParam, …)
    manifest.py          # parse the TOML manifest into the model
    config.py            # layered config + config-file writers (env/toml/yaml)
    commands.py          # argv token templates → argv (no shell)
    daemon_control.py    # daemon CONF-socket SET/GET (whitelisted, bounded parser)
    lifecycle.py         # spawn/stop processes, build/test jobs, bounded TX test
    status.py            # compose probe evidence into a RunState
    resources.py         # declared/observed conflict interpretation, band-limited radio claims
    gps.py               # the ONE typed GPS resolver: plan, consumers, feed-marker rules (no service state)
    restart_required.py  # the durable restart-required marker: path, safe tri-state read, merge, clear
    power.py             # power controls: kinds, bounded trigger, busctl verdict parse, pending-marker schema
    install.py           # adopt/verify/update sources (git, pinned); source_fs.py the transaction
    runtime_fs.py        # descriptor-anchored path containment, atomic writes
    reslock.py           # named non-blocking operation locks
    jobs.py              # synchronous bounded job execution, job markers + launcher housekeeping, bounded log tail (the detached spawn lives in lifecycle.py)
    probes/              # read-only bounded probes (process, net, unixsock, systemd, source, hardware)
    binary_install.py    # binary-channel download/verify/publish transaction + crash journal
    binary_receipt.py    # per-stack binary ownership record (absent|valid|superseded|unsafe)
    firewall.py          # nftables ruleset model + rendering
    boot_restore.py      # what to restart after a reboot
    updater_units.py     # the seven canonical systemd units (frozen bytes) + verification
    services.py            # ControllerService facade: init/state/locks, manifest, status,
                           # bootstrap/install plans — composes the service_* mixins below
    service_base.py        # shared types: ActionResult, ConfigWrite, typed exceptions
    service_webserver.py   # nginx/TLS/mTLS console + per-stack proxy operations
    service_selfupdate.py  # controller self-update orchestration + updater integration
    service_auto_install.py  # auto-install / ai-run driver, markers, log streaming
    service_maintenance.py # source update / uninstall / clean / known-working / source-check / upstream release tracking / power actions
    service_params.py      # param & config resolution, saves, config-file generation, identity, daemon-parameter application
    service_lifecycle_ops.py # start/stop/restart/build/test orchestration, jobs, dashboards
    service_binary_ops.py  # binary install/retire/recover; service_binary_channel.py resolves it
    service_firewall.py    # firewall candidates, apply/verify; service_hmac.py the MeshCom password
    service_network.py     # Wi-Fi client / AP fallback panel (NetworkManager)
    service_boot_restore.py  # boot-restore driver
    service_system.py      # host metrics for the dashboard System box (read-only, no subprocess)
  adapters/
    cli/main.py          # argparse  → ControllerService → render ActionResult
    web/app.py           # Flask HTTP → ControllerService → server-rendered pages
```

Not every module is listed; the rule below is what matters.

Dependency rule: `adapters/*` import `core/*`; `core/*` never imports `adapters/*`.
Adapters obtain `ControllerService` and `ActionResult` from `lhpc.core.services`; the `service_*` modules are
internal mixins composed into `ControllerService` by the `services.py` facade (the CLI additionally imports the
two cooperative-abort flags from `service_auto_install` and `service_hmac` to install its signal handlers).
Both adapters are thin — they parse input, call one `ControllerService` method, and
render the returned `ActionResult`. The web adapter calls the service directly
(never shells out to the CLI), so validation, gating and results are identical.

Service mixins orchestrate controller operations — locks, admission, authoritative rechecks and the
order of a transaction. Reusable interpretation and policy belongs in plain core modules as functions
with explicit inputs (`resources.py`, `gps.py`, `jobs.py`, `restart_required.py`, `power.py`). Prefer a
plain function over another mixin, and keep safety-critical ordering local to the coordinator even
when that leaves it long.

## Manifest and config layers

- **Manifest** (`lhpc/data/manifest.example.toml`, shipped as package data): stacks →
  components. Each component declares its `kind`, build/run/test commands, source
  (remote/branch/pin), resource claims, run params and config-file params.
- **Config**, merged in order:
  1. tracked defaults (`lhpc/data/defaults.toml`) + the manifest;
  2. operator overrides — `~/loraham-pi-control/config/local.toml` (callsign, remotes);
  3. secrets — `config/secrets.toml`, mode `0600` (never tracked, never in output);
  4. per-stack settings — `config/stacks/<id>[@band].toml`, written ONLY from a stack's Settings
     (web) or `lhpc config`. A start runs exactly this saved configuration: there are no per-launch
     values, so the launch, the controller's stored truth and global-change tracking can never
     disagree. The one launch-time overlay is an inherited identity (below), materialized for
     that launch and never persisted.
- The config file each app reads is generated from its `config_file` params
  (`{callsign}`/`{band}`/`{runtime}`/`{hardware}`/`{gps_*}` substituted; `{callsign}` resolves to the
  effective identity).

## Identity and callsigns

There is one global base callsign (`lhpc config operator --callsign`), optional, and it takes
the **base** callsign only — the intersection every licensed stack accepts: the digit-bearing
amateur structure — prefix, digit, then 1–3 letters, 3–6 characters total (e.g. `G0ABC`,
`M7XYZ`) — no SSID, no `/P`. `N0CALL` is refused as a placeholder, and its four-letter suffix is
not a valid base shape either; a value any licensed stack would refuse cannot be saved globally.
A **licensed** identity field (a stack that transmits under a callsign: chat, Voice, Graywolf,
MeshCom) may be left empty and then **inherits** the global base at launch — the empty field
keeps meaning "inherit", and changing the global reports every running inheriting stack as
restart-required. A per-stack value overrides it and may carry that stack's SSID or portable
form. Meshtastic and MeshCore **node identities never inherit**: they are local node names,
required on the stack itself, and a placeholder (`N0CALL`, `YOURCALL`, `NODENAME`, …) is refused
like an empty one. The check is "configured, not a placeholder, and encodable by the protocol
that transmits it" — the byte and character limits, SSID ranges and callsign shape each stack's
firmware or app actually accepts; LHPC cannot verify that the callsign is licensed to you and
does not claim to. Using your own call remains yours; the gate stops a station transmitting
under a value nobody chose. Clearing an identity (`lhpc config`, or the Settings page) makes a
licensed callsign fall back to the global one; with no global left, or for a node name that
never inherits, the stack cannot be started until one is set again. Enforcement judges the saved
configuration at plan time, again under the operation locks immediately before the apply, and
before every identity-bearing post-start step (`service_params.enforce_identity`, called from
`service_lifecycle_ops`), so the CLI dry run, the web click and the locked mutation share one
verdict: a start with no resolvable identity is refused, never launched with a placeholder, and
the web sends the operator to the offending Settings row. The validator table per field type:
[adding-a-stack.md](adding-a-stack.md#parameters--config-files).

## Probes and status

Status is reconstructed on each call, never from a stale PID file: process identity
(`/proc/<pid>/cmdline`), TCP listeners, Unix sockets + a bounded daemon `GET STATUS`,
systemd unit state, and local git source/pin state. Every probe is bounded and turns
errors into evidence. A missing runtime root reports `not-installed`, not an error.
`RunState` ∈ {running, degraded, stopped, failed, unknown, not-applicable, not-installed}.

## Radios, bands and resource claims

The LoRaHAM daemon runs one instance per band (`--radio 433|868`), each with its
own CONF socket (`/tmp/loraconf{band}.sock`), raw data socket (`/tmp/lora{band}.sock`)
and framed socket (`/tmp/lora{band}f.sock`). Components declare **resource claims** in the
manifest (`[[stack.component.resource]]`: key, kind, mode); `core/resources.py` turns declared
claims plus observed state into conflicts, and a start is blocked, with the holder named, if a
running stack already holds a resource it needs. The modes: **exclusive** (one claimant),
**cooperative** (claimants of the same key coexist, but conflict with any exclusive claim),
**provider** / **consumer** (the provider creates the resource, e.g. a socket; two providers
conflict, a consumer only records a dependency), and **requirement** (a band-scoped daemon
configuration constraint, e.g. `loraham.profile.433 = MANAGED`; recorded and shown by
`lhpc explain`, never a conflict source).

The claims that matter for the radios:

- `loraham.radio.<band>` — the daemon claims it as **provider** for the band it runs; a direct
  radio user (meshtastic, reticulum) claims it **exclusive**. One stack owns a band at a time.
- `loraham.daemon-socket.<band>` — **provider** on the daemon, **consumer** on every
  daemon-client stack (kiss, meshcom, meshcore).
- `spi.bus.0` — the shared SPI bus, **cooperative** in group `spi.bus.0`: the daemon (and the
  Reticulum node) serialise every transfer through the daemon's fail-closed
  `<runtime>/state/loraham/spi0.lock` flock, so they may share the bus on opposite bands.
- `spi.bus.0.unlocked` — meshtasticd drives `/dev/spidev0.0` without taking that lock, so it
  claims this key **exclusive**; the Reticulum node claims it exclusive too. `meshtastic +
  reticulum` is therefore refused, while `daemon + meshtastic` on opposite bands stays allowed:
  that pair shares `/dev/spidev0.0` without mutual exclusion — an accepted hazard, not a safe
  design.

Each stack document lists its own claims; a band, a TCP port and a serial device are claimed the
same way. A claim marked `advisory = true` — the single MeshCore companion-client slot
([meshcore](stacks/meshcore.md#command-line-client)) — is reported as a conflict but never blocks
a start.

## Daemon control

Live settings go to the per-band CONF socket. `SET` is fire-and-forget (the daemon
applies silently and only answers `GET`), so `lhpc` sends the `SET` then reads back with
`GET STATUS` to confirm. Only whitelisted keys (TXMODE, CAD*, radio params) are allowed;
nothing transmits by itself. The parameters themselves: [stacks/daemon.md](stacks/daemon.md).

## Safety model

The guarantees the controller gives, each with where it is implemented and proven.

- **No shell.** Every launch, build, test and web job is structured argv with `shell=False`.
  The manifest defines argv token templates; `core/commands.py` expands them so a validated
  user value is always its own token — it cannot merge with an option, change the executable,
  cwd or env, or become shell syntax. Interactive components get their copy-paste command from
  the same spec. `tests/core/test_structured_exec.py` (rendered-runner AST check +
  spawn-argv capture).
- **Typed validation.** Every value is validated by type (`core/validators.py`) before
  persistence and before execution. `@file:` secrets fail closed — a missing, unreadable or empty
  secret blocks the launch (`core/commands.py`); a `pkg-config` failure aborts a build
  (`core/build_launcher_runtime.py`).
- **Identity-verified stopping.** Each launch records full process identity under a unique id
  (`state/owned/<comp>__<band>__<pid>__<nonce>.json`: pid, start time, pgid, sid, executable, argv
  fingerprint). `Lifecycle.stop` re-reads `/proc` and signals only an LHPC-owned session leader
  whose identity still matches; any mismatch means no signal and a `manual_required` verdict with
  the exact PID. It waits for verified cessation before clearing the record (no auto-SIGKILL).
  `core/lifecycle.py`; `tests/core/test_process_ownership.py`.
- **Path containment.** `core/runtime_fs.py` opens the runtime root and walks each parent with
  `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in mid-operation cannot redirect a write;
  atomic writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
  `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed
  (`PathContainmentError`) and caught at every boundary. `tests/core/test_runtime_fs.py`.
- **Source transactions.** An update clones a candidate beside the destination, archives the
  prior source to a transaction-owned `.prev`, activates by atomic no-clobber rename, writes the
  ownership record, then removes the `.prev` — journalled at every step; a failed activation
  never destroys the active source, and an unresolved or malformed journal blocks all source
  mutation until an operator resolves it. `core/install.py`, `core/source_fs.py`;
  `tests/install/test_staged_update.py`, `tests/install/test_source.py`.
- **Locally added files are never collateral.** An update carries the operator's and the
  stack's own added files into the new source inside the activation and destroys the archived
  prior only after each of them is proven present there; a path the new upstream also ships is a
  refusal, never a merge. The operator-facing rule: [provenance](provenance.md#ownership-records).
  `core/install.py`, `core/source_fs.py`; `tests/install/test_source.py`.
- **Locking.** Start, stop, restart, build, update, uninstall and clean take named non-blocking
  locks; a contended operation refuses immediately, naming the holder. `core/reslock.py`.
- **Config as a transaction.** A Settings save is validate-first and all-or-recoverable: the
  whole submission is validated before any write, files are journalled and atomically replaced,
  a mid-write failure rolls back. A malformed `local.toml` is preserved, never overwritten;
  a present-but-malformed per-stack file is a typed error (CLI: clean failure, web: 409, no echo
  of the bad value), only an *absent* file means "use defaults". `tests/core/test_config.py`,
  `tests/stacks/test_stack_params.py`.
- **Truthful outcomes.** Every component yields one typed `Outcome`; `ActionResult.ok` derives
  entirely from those. `start` fails unless every required component verified ready (a daemon
  start verifies each band's CONF socket); a stop counts as verified only when the process
  ceased AND every ready endpoint disappeared, and markers clear only then; `update` reports
  nonzero on partial failure; CLI exit status and web flash agree. `tests/core/test_post_start.py`.
- **Daemon sockets.** One bounded CONF parser for every read (≥4 KiB, over-long or
  over-tokenized replies are rejected); a TX-mode change is read back, and an unconfirmed change
  blocks dependents (`core/daemon_control.py`). A compatibility `/tmp` socket is peer-checked with
  `SO_PEERCRED` after `connect()` and before any payload — the peer UID must equal the
  controller's; protected `/run/loraham` sockets keep their dedicated-UID model and are exempt
  (`core/probes/backends.py`; `tests/web/test_socket_peercred.py`).
- **Web.** Loopback bind only (`run_server` refuses a non-loopback host); every serving mode
  rejects an empty, malformed or unrelated `Host` with 400 before any session or CSRF work
  (`tests/web/test_trusted_host.py`); mutations are POST + CSRF token + explicit confirm; every
  response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` and a `Content-Security-Policy` locked to `'self'` (with `base-uri`/`frame-ancestors` `'none'`); per-stack
  config paths are proven to stay inside `config/stacks/`
  (`tests/web/test_web.py::test_config_path_cannot_escape_via_band_or_id`). No GET route runs a
  network or git-remote command (`tests/web/test_web.py::test_get_routes_make_no_network_calls`).
  Network exposure is the nginx front end ([serving model](deployment.md#serving-model)).
- **Evidence once per request.** A page render reads each piece of evidence once (status
  snapshot, per-(stack, band) config, consumed-source SHAs, firewall status, listeners, git
  state per distinct checkout) through a thread-local request memo dropped at every request
  start and around every mutation; rechecks under operation locks use `build_snapshot(fresh=True)`.
- **Detached web jobs** (install, build, test, start, restart). The console reserves an attempt
  marker (`state/jobresults/<log>.json`), spawns the child under task admission, captures its
  process identity, releases its own admission, and only then publishes the `.job` tracking
  marker, so the child's `verify_tracked` gate passes only once the parent no longer holds the
  flock the child must take. An untrackable child is terminated, or the attempt marked *unsafe*
  when its stop is unproven (what the operator sees: [operations](operations.md#operating-the-console)).
  Job markers are PID-reuse-resistant; `prune_logs()` never deletes the log of an active job.
- **Uninstall protection.** Uninstall refuses while a target runs, never removes a source still
  referenced by another component (`loraham-kiss-tnc` and `loraham-kiss-serial` share `src/loraham-kiss-tnc`), and never deletes
  config, secrets or profiles (`tests/core/test_uninstall_safety.py`). What `uninstall.sh` does
  and keeps: [operations](operations.md#backup--restore).
- **Boot restore replays only saved configuration** through the normal gated start path —
  [operations.md](operations.md).
- **Packaging.** Tracked assets live in `lhpc/data/` and load via `importlib.resources`
  (`core/assets.py`) — a wheel installed into a fresh venv runs (`tests/repo/test_packaging.py`).

What is still open is tracked in [backlog.md](backlog.md).

## Controller identity & self-update

LHPC's own checkout is a **dedicated controller identity** — a top-level `[controller]`
manifest table (strict allow-list; fixed `source_path = "src/loraham-pi-control"` and
`branch = "main"`), NOT a stack. It is observable and self-updatable: every generic verb aimed
at its id refuses in the central service layer and points to `lhpc self-update` (operating
rules: [deployment.md](deployment.md)).

`controller_identity_live()` reports a **tri-state** verdict, used only at startup refresh,
explicit "check now", and immediately before an apply:

- **ok** — self-hosted and verified: the checkout is under the runtime root, no symlink in
  the `root → src → checkout` chain, owned by the service user with no group/other write,
  its realpath equals both `repo_root()` and the imported package, on `main` with the
  approved canonical `origin`.
- **unsafe** — self-hosted but tampered/misconfigured (symlink, group-writable, wrong
  branch/origin, mismatch). **Blocks apply.**
- **not_applicable** — *not* self-hosted (a dev checkout). Neutral: does not block;
  self-update proceeds via the normal `repo_root()` mechanism.

A same-account process replacing the checkout mid-check is **out of the threat model**: LHPC
detects and refuses an unsafe layout, it does not claim same-account race-proofness.

The verdict is cached in a single versioned, schema-validated self-update envelope, and status
GETs render only that — never a live git, network or identity call. `lhpc web` holds a shared controller-runtime flock for its lifetime;
`self-update --apply` takes it exclusive first (then the self-update lock), so a running
server can never have its own source mutated underneath it.
