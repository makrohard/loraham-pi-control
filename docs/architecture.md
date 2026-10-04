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
`LHPC_RUNTIME_ROOT`), created by `lhpc bootstrap` (mode `0700`): adopted stack sources (`src/`),
generated config (`config/`), state (`state/`), logs (`logs/`) and the venv (`venv/lhpc`).

Setups differ only in where LHPC's own checkout sits:

- **Self-hosted deployment (recommended)** — the checkout is inside the runtime root at
  `src/loraham-pi-control`, the venv outside it at `venv/lhpc`. `install.sh` sets this up, so
  `lhpc self-update` and the running code are one tree ([deployment.md](deployment.md)).
- **Dev checkout** — the checkout is elsewhere with its own venv; the runtime root is separate.

LHPC never writes into a checkout except via `lhpc self-update`; the
[controller-identity check](#controller-identity--self-update) reports which setup you are in.

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
    gps.py               # the one GPS resolver: plan, consumers, feed-marker rules
    restart_required.py  # the durable restart-required marker
    power.py             # reboot/shutdown controls and their pending marker
    clock.py             # clock policy: when the clock may date certificates (gate, floor, refusal)
    install.py           # adopt/verify/update sources (git, pinned); source_fs.py the transaction
    runtime_fs.py        # descriptor-anchored path containment, atomic writes
    reslock.py           # named non-blocking operation locks
    jobs.py              # bounded job execution, job markers, log tail
    probes/              # read-only bounded probes (process, net, unixsock, systemd, source, hardware)
    binary_install.py    # binary-channel install transaction + crash journal
    binary_receipt.py    # per-stack binary ownership record (absent|valid|superseded|unsafe)
    firewall.py          # nftables ruleset model + rendering
    boot_restore.py      # what to restart after a reboot
    updater_units.py     # the seven canonical systemd units (frozen bytes) + verification
    services.py            # ControllerService facade; composes the service_* mixins below
    service_base.py        # shared types: ActionResult, ConfigWrite, typed exceptions
    service_webserver.py   # nginx/TLS/mTLS front end + per-stack proxies
    service_selfupdate.py  # controller self-update + updater integration
    service_auto_install.py  # auto-install driver
    service_maintenance.py # source update, uninstall, clean, known-working, upstream tracking, power
    service_params.py      # params, config saves and generation, identity, daemon parameters
    service_lifecycle_ops.py # start/stop/restart/build/test, jobs, dashboards
    service_binary_ops.py  # binary install/retire/recover; service_binary_channel.py resolves it
    service_firewall.py    # firewall candidates, apply/verify; service_hmac.py the MeshCom password
    service_network.py     # Wi-Fi client / AP fallback panel (NetworkManager)
    service_boot_restore.py  # boot-restore driver
    service_system.py      # host metrics for the dashboard (read-only)
  adapters/
    cli/main.py          # argparse  → ControllerService → render ActionResult
    web/app.py           # Flask HTTP → ControllerService → server-rendered pages
```

Not every module is listed.

Dependency rule: `adapters/*` import `core/*`; `core/*` never imports `adapters/*`. Adapters
obtain `ControllerService` and `ActionResult` from `lhpc.core.services`; the `service_*` modules
are internal mixins composed by that facade. Exceptions: the CLI imports the two cooperative-abort
flags from `service_auto_install` and `service_hmac` for its signal handlers, and the web imports
the disk helpers from `service_system`. Both adapters parse input, call one `ControllerService`
method and render the `ActionResult`; the web never shells out to the CLI, so validation, gating
and results are identical.

Service mixins orchestrate — locks, admission, authoritative rechecks, transaction order.
Reusable interpretation and policy go in plain core modules as functions with explicit inputs
(`resources.py`, `gps.py`, `jobs.py`, `restart_required.py`, `power.py`, `clock.py`). Prefer a
plain function over another mixin, and keep safety-critical ordering local to the coordinator even
when that leaves it long.

## Manifest and config layers

- **Manifest** (`lhpc/data/manifest.example.toml`, package data): stacks → components. Each
  component declares its `kind`, build/run/test commands, source (remote/branch/pin), resource
  claims, run params and config-file params.
- **Config**, merged in order:
  1. tracked defaults (`lhpc/data/defaults.toml`) + the manifest;
  2. operator overrides — `~/loraham-pi-control/config/local.toml` (callsign, remotes);
  3. secrets — `config/secrets.toml`, mode `0600` (never tracked, never in output);
  4. per-stack settings — `config/stacks/<id>[@band].toml`, written ONLY from a stack's Settings
     (web) or `lhpc config`. A start runs exactly this saved configuration; there are no
     per-launch values. The one launch-time overlay is an inherited identity (below),
     materialized for that launch and never persisted.
- The config file each app reads is generated from its `config_file` params
  (`{callsign}`/`{band}`/`{runtime}`/`{hardware}`/`{gps_*}` substituted; `{callsign}` resolves to the
  effective identity).

## Identity and callsigns

- **Global base callsign** (`lhpc config operator --callsign`, optional): the base callsign only
  — prefix, digit, then 1–3 letters, 3–6 characters (e.g. `G0ABC`, `M7XYZ`), no SSID, no `/P`.
  `N0CALL` is refused. A value any licensed stack would refuse cannot be saved globally.
- **Licensed identity fields** (stacks that transmit under a callsign: chat, Voice, Graywolf,
  MeshCom) may be left empty and then **inherit** the global base at launch; changing the global
  reports every running inheriting stack as restart-required. A per-stack value overrides it and
  may carry that stack's SSID or portable form.
- **Meshtastic and MeshCore node identities never inherit**: they are local node names, required
  on the stack itself.
- **The check** is "configured, not a placeholder (`N0CALL`, `YOURCALL`, `NODENAME`, …), and
  encodable by the protocol that transmits it" — the byte and character limits, SSID ranges and
  callsign shape each stack's firmware or app accepts. LHPC cannot verify that the callsign is
  licensed to you.
- **Clearing** an identity (`lhpc config` or Settings) makes a licensed callsign fall back to the
  global one; with no global, or for a node name, the stack cannot start until one is set.
- **Enforcement** (`service_params.enforce_identity`, called from `service_lifecycle_ops`) judges
  the saved configuration at plan time, again under the operation locks immediately before the
  apply, and before every identity-bearing post-start step, so the CLI dry run, the web click and
  the locked mutation share one verdict: a start with no resolvable identity is refused, never
  launched with a placeholder, and the web sends the operator to the offending Settings row.

The validator table per field type: [adding-a-stack.md](adding-a-stack.md#parameters--config-files).

## Probes and status

Status is reconstructed on each call, never from a stale PID file: process identity
(`/proc/<pid>/cmdline`), TCP listeners, Unix sockets + a bounded daemon `GET STATUS`, systemd unit
state, and local git source/pin state. Every probe is bounded and turns errors into evidence. A
missing runtime root reports `not-installed`, not an error.
`RunState` ∈ {running, degraded, stopped, failed, unknown, not-applicable, not-installed}.

## Radios, bands and resource claims

The LoRaHAM daemon runs one instance per band (`--radio 433|868`), each with its own CONF socket
(`/tmp/loraconf{band}.sock`), raw data socket (`/tmp/lora{band}.sock`) and framed socket
(`/tmp/lora{band}f.sock`). Components declare **resource claims** in the manifest
(`[[stack.component.resource]]`: key, kind, mode); `core/resources.py` turns declared claims plus
observed state into conflicts, and a start is blocked, with the holder named, if a running stack
already holds a resource it needs. The modes:

- **exclusive** — one claimant;
- **cooperative** — claimants of the same key coexist, but conflict with any exclusive claim;
- **provider** / **consumer** — the provider creates the resource (e.g. a socket); two providers
  conflict, a consumer only records a dependency;
- **requirement** — a band-scoped daemon configuration constraint (e.g.
  `loraham.profile.433 = MANAGED`), shown by `lhpc explain`, never a conflict source.

The radio claims:

- `loraham.radio.<band>` — **provider** on the daemon for the band it runs; **exclusive** for a
  direct radio user (meshtastic, reticulum). One stack owns a band at a time.
- `loraham.daemon-socket.<band>` — **provider** on the daemon, **consumer** on every daemon-client
  stack (kiss, meshcom, meshcore).
- `spi.bus.0` — the shared SPI bus, **cooperative** in group `spi.bus.0`: the daemon (and the
  Reticulum node) serialise every transfer through the daemon's fail-closed
  `<runtime>/state/loraham/spi0.lock` flock, so they may share the bus on opposite bands.
- `spi.bus.0.unlocked` — meshtasticd drives `/dev/spidev0.0` without that lock and claims this key
  **exclusive**; so does the Reticulum node. `meshtastic + reticulum` is refused; `daemon +
  meshtastic` on opposite bands is allowed (the policy below).

**SPI policy.** On the Pi 5 with the shipped trees there is one SPI node, `/dev/spidev0.0`
(overlay `spi0-0cs`; the chip selects are driven from userspace as GPIOs): the daemon's 433 radio
is CS 8, its 868 radio CS 7, and meshtasticd's 868 radio CS 7. The daemon and the Reticulum
interface take `spi0.lock`; meshtasticd does not. LHPC refuses meshtastic + reticulum together and
allows daemon + meshtastic on opposite bands.

Measured (dual-TX soak, 2026-10-03, Pi 5: 15 min of non-overlapping transmissions as control, then
1 h with 16 forced overlaps; record `S0-partB-summary.md` of the S0 SPI records): no frame was
corrupted on either band — 433: 8/8 control and 15/16 soak frames received intact; 868: 10/10 and
39/40. At one of the 16 overlaps (22:56:59Z) meshtasticd logged RadioLib `err=-16`
(`RADIOLIB_ERR_SPI_WRITE_FAILED`) and aborted on its `setStandby` assertion, and the daemon's frame
of that second was never transmitted (`STATS TXERR 1`, no daemon log line). So on the shared bus
the pair daemon 433 + meshtasticd 868 is **not safe under concurrent transmission**: one real
overlap gave an SPI write failure, a meshtasticd crash and a lost daemon frame.

Decided by the maintainer (2026-10-04): **allow, but warn.** The pair stays admitted, with this
warning where it is admitted: "daemon 433 and meshtastic 868 share one SPI bus; transmitting at the
same moment can crash meshtasticd (measured) — avoid simultaneous transmission". The warning is
shown where the start is admitted: a `[warning]` line in the start's plan and in its result, in
both directions (meshtastic on 868 while the daemon serves 433, or the daemon on 433 while
meshtasticd runs on 868). Serialising the
two through kernel-owned chip selects (overlay `spi0-2cs`, each radio its own spidev node) is
planned for 0.13.0; it is not present.

Each stack document lists its claims; a band, a TCP port and a serial device are claimed the same
way. A claim marked `advisory = true` (the single MeshCore companion-client slot,
[meshcore](stacks/meshcore.md#command-line-client)) is reported as a conflict but never blocks a
start.

## Daemon control

Live settings go to the per-band CONF socket. The daemon answers every line with one `OK` or
`ERR <reason>`; `lhpc` reads that reply to each `SET` (an `ERR`, or no `OK` within 1 s, is a failure)
and confirms a key the daemon reports back by reading back `GET STATUS`. Only whitelisted keys are allowed (the list:
[stacks/daemon.md](stacks/daemon.md#radio-parameters)); nothing transmits by itself.

## Safety model

The guarantees the controller gives. Where each one is enforced, which locks it takes, what it
leaves on disk and which test proves it: the [invariant table](#invariant-table).

- **No shell.** Every launch, build, test and web job is structured argv with `shell=False`.
  `core/commands.py` expands the manifest's argv token templates so a validated user value is
  always its own token — it cannot merge with an option, change the executable, cwd or env, or
  become shell syntax. Interactive components get their copy-paste command from the same spec.
- **Typed validation.** Every value is validated by type before persistence and before
  execution. `@file:` secrets fail closed — a missing, unreadable or empty secret, or one that is
  a symlink, not a regular file or over 64 KiB, blocks the launch; a `pkg-config` failure aborts a
  build.
- **Identity-verified stopping.** Each launch records full process identity under a unique id
  (`state/owned/<comp>__<band>__<pid>__<nonce>.json`: pid, start time, pgid, sid, executable, argv
  fingerprint). `Lifecycle.stop` re-reads `/proc` and signals only an LHPC-owned session leader
  whose identity still matches; any mismatch means no signal and a `manual_required` verdict with
  the exact PID. It waits for verified cessation before clearing the record (no auto-SIGKILL).
- **Path containment.** Every runtime write opens the runtime root and walks each parent with
  `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in mid-operation cannot redirect a write; atomic
  writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
  `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
  and caught at every boundary. A checkout's `.git` that is a symlink which does not resolve
  (dangling, a loop) is treated as present and the tree is not reported clean (before 0.12.0 it
  was ignored); it is never handed to git, which would walk up to an enclosing repository. These
  presence decisions use `runtime_fs.probe_exists`, or `probe_stat` where the kind (directory,
  symlink) decides: binary retire's check of each receipt file and owned folder, the
  binary->source switch's listing of a tree, a checkout's dirty/carry inventory (`.git`), the
  boot-restore markers and journal, and the config journal recovery. In each, only ENOENT/ENOTDIR
  read as absent, any other error refuses or keeps; the stdlib-only firewall helper applies the
  same rule inline to its journal.
- **Source transactions.** An update clones a candidate beside the destination (recorded before
  the clone starts, so recovery removes a clone a crash interrupted), archives the
  prior source to a transaction-owned `.prev`, activates by atomic no-clobber rename, writes the
  ownership record, then removes the `.prev` — journalled at every step. A failed activation never
  destroys the active source; an unresolved or malformed journal blocks all source mutation until
  an operator resolves it.
- **Locally added files are never collateral.** An update carries the operator's and the stack's
  own added files into the new source inside the activation and destroys the archived prior only
  after each is proven present there; a path the new upstream also ships is a refusal, never a
  merge. Operator rule: [provenance](provenance.md#ownership-records).
- **Locking.** Start, stop, restart, build, update, uninstall and clean take named non-blocking
  locks; a contended operation refuses immediately, naming the holder. Lock order: task
  admission, then configuration stability, then the source-transaction index and the source
  paths (or a stack's lifecycle bundle), then the self-update lock; a self-update takes the
  controller-runtime lock after admission and before the self-update lock. A build holds the lock of
  every source it consumes — each built component's own and every `build_requires` dependency's,
  transitively — for its whole run, and reads the revisions its receipt records under them; a
  detached build holds a dependency's lock shared, so the parallel builds of one stack can consume
  it while any operation that would change it is refused.
- **Config as a transaction.** A Settings save or reset validates the whole submission before any write;
  files are journalled and atomically replaced, and a mid-write failure rolls back; a journal a
  crashed process left behind is finished under the config lock before ANY writer runs (the
  non-transactional hardware, GPS, operator and remote saves included), or the lock is refused;
  each `lhpc` process also finishes it eagerly at start. A malformed `local.toml` is preserved, never overwritten; a present-but-malformed
  per-stack file is a typed error (CLI: clean failure, web: 409, no echo of the bad value) — only
  an *absent* file means "use defaults".
- **Truthful outcomes.** Every component yields one typed `Outcome`; `ActionResult.ok` derives
  entirely from those. `start` fails unless every required component verified ready (a daemon
  start verifies each band's CONF socket); a stop counts as verified only when the process ceased
  AND every ready endpoint disappeared, and markers clear only then; `update` reports nonzero on
  partial failure; CLI exit status and web flash agree.
- **Daemon sockets.** One bounded CONF parser for every read (a reply reaching the 4 KiB read cap,
  or over-tokenized, is rejected); a TX-mode change is read back, and an unconfirmed change blocks
  dependents. A compatibility `/tmp` socket is peer-checked with `SO_PEERCRED` after `connect()`
  and before any payload — the peer UID must equal the controller's; protected `/run/loraham`
  sockets keep their dedicated-UID model and are exempt.
- **Web.** Loopback bind only; every serving mode rejects an empty, malformed or unrelated `Host`
  with 400 before any session or CSRF work; mutations are POST + CSRF token + explicit confirm; every
  response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` and a `Content-Security-Policy` locked to `'self'` (with
  `base-uri`/`frame-ancestors` `'none'`); per-stack config paths stay inside `config/stacks/`.
  No GET route runs a network or git-remote command. Network exposure is the nginx front end ([serving model](deployment.md#serving-model)).
- **Evidence once per request.** A page render reads each piece of evidence once (status
  snapshot, per-(stack, band) config, consumed-source SHAs, firewall status, listeners, git state
  per distinct checkout) through a thread-local request memo dropped at every request start and
  around every mutation; rechecks under operation locks use `build_snapshot(fresh=True)`. Every
  public `ControllerService` entry that writes anything is `@invalidates_snapshot`; the rest are
  listed with a reason in `snapshot_memo.SNAPSHOT_NEUTRAL`. The
  neutral writers whose writes no snapshot input observes (lock files, the native RF-log roll,
  the GPS receiver's tty mode) name them in `SNAPSHOT_NEUTRAL_WRITERS`. Every neutral entry is
  driven under a write trace on a live runtime root: a "read-only" one that writes fails, a
  writer must write exactly its listed paths; a trace of a fresh snapshot on that state proves
  none is read.
- **Detached web jobs** (install, build, test, start, restart). The console reserves an attempt
  marker (`state/jobresults/<log>.json`), spawns the child under task admission, captures its
  process identity, releases its own admission, and only then publishes the `.job` tracking
  marker, so the child's `verify_tracked` gate passes only once the parent no longer holds the
  flock the child must take. An untrackable child is terminated, or the attempt marked *unsafe*
  when its stop is unproven ([operations](operations.md#operating-the-console)). Job markers are
  PID-reuse-resistant; `prune_logs()` never deletes the log of an active job.
- **Uninstall protection.** Uninstall refuses while a target runs, never removes a source still
  referenced by another component (`loraham-kiss-tnc` and `loraham-kiss-serial` share
  `src/loraham-kiss-tnc`), and never deletes config, secrets or profiles. `uninstall.sh`:
  [operations](operations.md#backup--restore).
- **Boot restore replays only saved configuration** through the normal gated start path
  ([operations](operations.md#not-a-supervisor)).
- **Packaging.** Tracked assets live in `lhpc/data/` and load via `importlib.resources`; a wheel
  installed into a fresh venv runs.

### Invariant table

One row per safety invariant: the statement is in the bullet or page the second column links to;
the row says where it is enforced (`file:line` of the owning function), which locks it takes, what
it keeps on disk and how a crash is recovered, and the tests that prove it. The order in which the
locks are taken is stated once, in [Locking](#safety-model); the table does not repeat it. A
proving test runs at least one owner function of its row, or names a table the row lists as its
owner (checked by tracing its calls; not traced: a row whose owner is outside LHPC, and a test in
the opt-in acceptance lane), and asserts the row's claim (read, not machine-checked). *Gap* marks a
claim, or a part of one, with no proving test. Paths in the durable-state column are relative to
the runtime root unless absolute.

| area | invariant | owner | locks | durable state · recovery | proving tests |
|---|---|---|---|---|---|
| admission / locks | task admission: no new task while an uninstall or a self-update is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py:2532` `_admit`; `lhpc/core/service_selfupdate.py:1230` `_task_admission_blocked` | `controller-task-admission` | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
| admission / locks | named non-blocking locks; a contended operation refuses naming the holder ([Locking](#safety-model)) | `lhpc/core/reslock.py:106` `operation_lock`; `lhpc/core/services.py:2595` `_acquire_key` | one flock per key under `state/locks/` | owner record beside the lock; the kernel drops a dead holder's flock | `tests/core/test_reslock.py::test_second_acquire_is_blocked_and_names_holder`, `::test_dead_holder_lock_is_free`, `::test_concurrent_lifecycle_op_is_blocked` |
| admission / locks | one lock order for every operation ([Locking](#safety-model)) | `lhpc/core/services.py:2582` `_admission_guard`, `:218` `_config_stable`, `:360` `_source_operation_guard`, `:2659` `_lifecycle_guard` | the order is stated once, in [Locking](#safety-model) | — | `tests/core/test_task_admission.py::test_admission_acquired_before_config_stable`, `::test_second_thread_start_and_config_do_not_invert`; `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` |
| admission / locks | a build holds every source it consumes, transitively, for its whole run; the receipt's revisions are read under those locks ([Locking](#safety-model)) | `lhpc/core/build_plan.py:36` `lock_sources`, `:81` `consumed_lines`; `lhpc/core/service_lifecycle_ops.py:3206` `build`, `:3542` `spawn_web_job`; `lhpc/core/build_launcher_runtime.py:206` `run` | the CLI: admission, index, every source exclusive; a detached build: index, its own source exclusive, each dependency's shared | the completion marker (the receipt) and its build-inputs sidecar, written only after the last step | `tests/core/test_build_plan.py::test_a_provider_source_cannot_move_during_a_dependent_build`, `::test_the_receipt_records_the_revision_read_under_the_locks`, `::test_both_paths_lock_and_record_the_same`, `::test_parallel_jobs_share_a_dependency` |
| config transactions | a save validates everything first, is journalled and rolls back; a crashed save is finished before any writer runs ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1905` `apply_config_transaction`, `:1858` `_finish_pending_journal`, `:1885` `recover_config_journal_at_startup` | config lock `config/.lock` (`lhpc/core/config.py:77` `config_lock`) | `state/config-txn.json`; `lhpc/core/config.py:1800` `recover_config_transaction` rolls back, or blocks and keeps the journal | `tests/core/test_config.py::test_pending_journal_is_recovered_before_next_save`, `::test_rollback_failure_retains_journal_and_blocks_later`, `::test_a_non_transactional_save_finishes_a_pending_journal_before_it_writes`; `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` |
| config transactions | a malformed `local.toml` is kept; only an absent per-stack file means defaults ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1320` `_write_local_tables`, `:2023` `load_stack_config` | config lock (writes only) | `config/local.toml`, `config/stacks/<id>[@band].toml` | `tests/core/test_config.py::test_local_unsupported_structures_block_and_preserve`, `::test_malformed_stack_config_raises_and_is_preserved`, `::test_absent_stack_config_is_defaults`, `::test_web_returns_409_on_malformed_config`; gap: a write over a `local.toml` that is not valid TOML |
| install / recovery | source transactions: candidate recorded before the clone, prior archived to `.prev`, no-clobber activation, ownership record, unresolved journal blocks all source mutation ([Source transactions](#safety-model)) | `lhpc/core/install.py:532` `_stage_and_activate`, `:1308` `_staged_clone_record`, `:1945` `_activate_held`, `:1235` `_pending_journals` | admission, `source-txn-index`, source paths | `state/source-txn/<name>-<sha256>.json`, its `.staging` record, `src/.<name>.prev`; `lhpc/core/install.py:1258` `_recover_scan` finishes or rolls back, else blocks | `tests/install/test_source.py::test_recovery_removes_a_clone_killed_before_its_journal`, `::test_recovery_finishes_an_activation_interrupted_right_after_the_archive_rename`, `::test_retained_journal_blocks_every_source_op`; `tests/install/test_staged_update.py::test_failed_clone_leaves_active_source_intact` |
| install / recovery | locally added files are carried, never collateral ([Locally added files](#safety-model)) | `lhpc/core/source_fs.py:546` `carry_extras`, `:688` `extras_preserved`; `lhpc/core/install.py:1945` `_activate_held` | as source transactions | `.prev` and its journal are kept while a carry is unproven | `tests/install/test_source.py::test_an_added_file_colliding_with_the_new_upstream_refuses`, `::test_a_carry_failure_restores_the_prior_and_refuses`, `::test_an_addition_made_after_the_carry_retains_the_archived_prior` |
| install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py:1255` `stop`, `:1117` `verify_owned`; `lhpc/core/procident.py:96` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py:2467` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
| install / recovery | path containment ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:74` `_walk_parent`, `:156` `atomic_write` | none | — | `tests/core/test_runtime_fs.py::test_symlinked_parent_refuses_every_runtime_op`, `::test_atomic_write_rejects_symlink_leaf` |
| install / recovery | a path that cannot be examined is never absent: only ENOENT/ENOTDIR read as absent, any other error refuses or keeps; a `.git` symlink that does not resolve is present ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:736` `probe_exists`, `:752` `probe_stat`; `lhpc/core/firewall_helper.py:991` `_read_json_state` (the stdlib-only helper's inline rule) | none | the receipt, journal or record is kept while its path cannot be examined | `tests/core/test_existence_probe.py::test_any_other_error_is_unknown_with_the_error`, `::test_an_unexaminable_path_is_never_absent`, `::test_a_git_symlink_that_does_not_resolve_is_never_clean`; `tests/host/test_firewall.py::test_a_journal_that_cannot_be_examined_is_not_absent` |
| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2313` `uninstall` | admission, config stability (shared), every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
| install / recovery | boot restore replays saved configuration through the gated start, honours stop notes ([operations](operations.md#not-a-supervisor)) | `lhpc/core/service_boot_restore.py:286` `boot_restore_run`, `:127` `_write_stop_intent` | admission and the normal start locks | `state/boot-restore.json`, `state/stop-intent/<stack>.json`; `lhpc/core/service_boot_restore.py:264` `_boot_journal_recovery` consumes an item left *attempting*, never retries it | `tests/core/test_boot_restore.py::test_crash_after_attempting_never_retries_but_pending_survives`, `::test_boot_restore_run_honors_stop_intent_end_to_end`, `::test_driver_admission_refusal_consumes_nothing` |
| install / recovery | a wheel installed into a fresh venv runs ([Packaging](#safety-model)) | `lhpc/core/assets.py:20` `asset_path` | none | — | `tests/repo/test_packaging.py::test_data_assets_resolve`; gap: no test installs the wheel into a fresh venv |
| binary channel | a failed or interrupted binary install restores the previous one ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:96` `binary_install`, `:577` `binary_recover`; `lhpc/core/binary_install.py:556` `rollback_files` | admission, the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything` |
| binary channel | the receipt is absent, valid, superseded or unsafe; unreadable is never absent ([operations](operations.md#install-channels)) | `lhpc/core/binary_receipt.py:272` `receipt_state` | none (read) | `state/binary/<stack>.json` | `tests/install/test_binary_channel.py::test_malformed_receipt_is_unsafe_never_absent`, `::test_superseded_when_txn_id_differs`, `::test_absent_when_no_receipt` |
| binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:885` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
| PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py:2473` `run_server`, `:311` `_trusted_host`, `:244` `_csrf_ok`, `:295` `_set_headers`; `lhpc/core/config.py:1999` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
| PKI / web | no GET route runs a network or git-remote command ([Web](#safety-model)) | no single owner: every GET renders cached state (`lhpc/core/selfupdate.py:690` `status_view` for self-update) | none | — | `tests/web/test_web.py::test_get_routes_make_no_network_calls`, `::test_page_load_is_read_only`; `tests/core/test_controller.py::test_controller_status_makes_no_live_calls` |
| PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py:24` `invalidates_snapshot`; `lhpc/core/services.py:881` `invalidate_snapshot`, `:596` `build_snapshot`; the tables `lhpc/core/snapshot_memo.py:43` `SNAPSHOT_NEUTRAL`, `:280` `SNAPSHOT_NEUTRAL_WRITERS` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
| PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:3542` `spawn_web_job`, `:4041` `active_jobs`, `:3881` `prune_logs`; `lhpc/core/webjob_gate.py:64` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
| PKI / web | no shell: a validated value is always one argv token ([No shell](#safety-model)) | `lhpc/core/commands.py:75` `expand_argv` | none | — | `tests/core/test_structured_exec.py::test_hostile_value_stays_one_token`, `::test_started_process_argv_is_not_a_shell` |
| PKI / web | typed validation; an `@file:` secret that is missing, empty, a symlink, not a regular file or over 64 KiB blocks the launch; a `pkg-config` failure aborts a build ([Typed validation](#safety-model)) | `lhpc/core/validators.py:434` `validate_param`; `lhpc/core/commands.py:130` `build_env`, `:241` `build_step_argv`; `lhpc/core/runtime_fs.py:511` `read_secret_text` | none | — | `tests/core/test_validators.py::test_start_rejects_malicious_runparam`; `tests/core/test_structured_exec.py::test_at_file_secret_missing_blocks`, `::test_at_file_secret_empty_blocks`, `::test_a_secret_that_is_not_a_bounded_regular_file_is_a_command_error` (a symlink; a 1 MiB file), `::test_a_fifo_secret_is_refused_without_blocking` (not a regular file); `tests/stacks/test_daemon_bounds.py::test_build_step_argv_pkgconfig_failure_fails_closed`; gap: a secret the service user cannot read (no read permission); the 64 KiB bound itself (no test reads a file just over it) |
| PKI / web | an unverified clock never dates a certificate ([operations](operations.md#clock)) | `lhpc/core/clock.py:27` `verdict`, `:79` `clock_refusal` | the caller's | the original certificate stays active on a refusal | `tests/core/test_clock_gate.py::test_every_mutating_path_refuses_an_unverified_clock`, `::test_a_refused_reissue_leaves_the_original_certificate_active` |
| firewall | the helper's apply is journalled and verified; a crash is finished forward or rolled back ([firewall](firewall.md)) | `lhpc/core/firewall_helper.py:1252` `op_apply`, `:1162` `op_check`, `:1054` `recover` | root flock `/etc/lhpc/.firewall.lock` | `/etc/lhpc/firewall.{journal,meta,snapshot,transition}.json`, receipt `/run/lhpc-firewall/check.json`; a corrupt journal fails closed | `tests/host/test_firewall.py::test_recovery_finishes_promoted_apply_forward`, `::test_recovery_rolls_back_unpromoted_apply`, `::test_corrupt_journal_fails_closed`, `::test_apply_happy_path_writes_snapshot_and_verified_receipt` |
| firewall | remote exposure only with this boot's verified receipt, else loopback-only; a stale helper is named before the reboot ([firewall](firewall.md)) | `lhpc/core/service_firewall.py:609` `firewall_gate_activation`, `:781` `firewall_boot_gate`, `:705` `firewall_reapply_notice` | the caller's | the boot receipt above | `tests/host/test_firewall.py::test_gate_refuses_even_already_exposed_when_unverified`, `::test_boot_gate_falls_back_to_loopback_when_unverified`, `::test_boot_gate_fails_closed_when_fallback_cannot_stage`, `::test_reapply_notice_only_for_a_stale_helper` |
| self-update | an unsafe controller identity blocks apply ([Controller identity](#controller-identity--self-update)) | `lhpc/core/services.py:495` `controller_identity_live` | none (read) | the cached verdict in the self-update envelope | `tests/core/test_controller.py::test_identity_ok`, `::test_missing_checkout_is_not_applicable_not_unsafe`, `::test_self_update_apply_blocked_by_unsafe_identity` |
| self-update | a running console never has its source changed underneath it ([Controller identity](#controller-identity--self-update)) | `lhpc/core/selfupdate.py:100` `controller_runtime_lock`, `:65` `update_lock` | admission, controller-runtime (exclusive; the console holds it shared), self-update lock | `state/locks/` | `tests/core/test_controller.py::test_apply_refused_while_web_shared_lock_held`, `::test_web_shared_fails_closed_while_apply_exclusive_held` |
| self-update | one-click update and boot restore run only on the canonical, unoverridden units ([deployment](deployment.md#self-update)) | `lhpc/core/updater_units.py:555` `verify`, `:578` `integration` | none (read) | the unit files under the user unit folder | `tests/host/test_updater_units.py::test_verify_ok_and_integration_ok`, `::test_verify_overridden_by_dropin`, `::test_verify_unsafe_symlinked_unit` |
| self-update | the update request is claimed once; an in-flight record is cleared only when its helper is proven gone ([deployment](deployment.md#recovery)) | `lhpc/core/service_selfupdate.py:920` `self_update_trigger`, `:1047` `_self_update_run_service_locked`, `:1298` `self_update_recover_request` | admission | `state/selfupdate.request`, `state/selfupdate.inflight`; `lhpc self-update --recover-request` | `tests/install/test_selfupdate_service.py::test_trigger_writes_exclusive_request_no_systemctl`, `::test_recover_inflight_requires_dead_helper`, `::test_run_service_refuses_preexisting_inflight_preserving_both` |
| self-update | config migration is recorded before the checkout moves; a damaged journal blocks with no change ([deployment](deployment.md#self-update)) | `lhpc/core/selfupdate.py:600` `classify_journal` | the self-update lock | `state/selfupdate-migrate.json` and its git anchor | `tests/install/test_selfupdate_migration.py::test_interrupted_migration_recovered_by_fresh_service`, `::test_journal_persist_failure_refuses_before_mutation`, `::test_malformed_journal_blocks_without_mutation_or_deletion` |
| radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py:49` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
| radio / band ownership | the SPI bus is shared through the daemon's `spi0.lock` by the daemon and the Reticulum node; meshtasticd takes no part and is admitted beside the daemon only on the opposite band ([Radios](#radios-bands-and-resource-claims)) | the daemon, not LHPC; LHPC models it as the `spi.bus.0` / `spi.bus.0.unlocked` claims | the daemon's flock `state/loraham/spi0.lock` | — | `tests/core/test_resources_conflicts.py::test_cooperative_peers_do_not_conflict`, `::test_meshtastic_conflicts_with_daemon_on_868_radio_not_spi`; gap: the lock itself (daemon side) |
| radio / band ownership | bounded CONF replies, TX-mode read-back, `/tmp` socket peer check ([Daemon sockets](#safety-model)) | `lhpc/core/daemon_control.py:179` `parse_conf_reply`, `:519` `apply_set`; `lhpc/core/probes/backends.py:767` `_authenticate_tmp_peer` | none | — | `tests/stacks/test_daemon_bounds.py::test_oversized_response_rejected`, `::test_too_many_tokens_rejected`; `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent`; `tests/core/test_post_start.py::test_tx_mode_fails_when_readback_mismatches`; `tests/web/test_socket_peercred.py::test_foreign_uid_tmp_peer_is_refused` |
| radio / band ownership | truthful outcomes: a start fails unless every required component is verified ready; a stop counts only when the process ceased and its endpoints are gone; an update with a failed part reports failure; the CLI exit status follows the result ([Truthful outcomes](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:915` `_start_impl_inner`, `:2465` `_stop_impl`; `lhpc/core/outcomes.py:71` `applied_ok`; `lhpc/core/service_maintenance.py:1756` `update`; `lhpc/adapters/cli/main.py:228` `_render` | the start, stop and update locks | owned records and markers cleared only on verified stop | `tests/core/test_outcomes.py::test_applied_ok_requires_all_verified`; `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified`, `::test_dependent_not_started_when_daemon_unready`; `tests/core/test_stop_propagation.py::test_stop_unverified_keeps_markers`; `tests/core/test_known_working.py::test_candidate_clear_failure_is_truthful_incomplete`; `tests/cli/test_cli.py::test_status_unknown_stack_exits_one`; gap: a daemon start's check of each band's CONF socket separately (only a daemon that is not ready at all is driven); the web flash agreeing with the CLI exit status (no test drives one result through both) |
| radio / band ownership | no start without a resolvable identity ([Identity](#identity-and-callsigns)) | `lhpc/core/service_params.py:2960` `enforce_identity` | the start locks (re-judged under them) | — | `tests/core/test_identity.py::test_start_refuses_identity_before_boot_hook_and_feed_clear`, `::test_licensed_with_neither_value_is_refused`; `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` |
| lab | a simulated reboot kills only owned groups and runs the real boot restore ([testlab](testlab.md)) | `testlab/lhpc_testlab/ops.py:283` `power` | admission (waited for) | the lab root's boot identity | `testlab/tests/unit/test_testlab.py::test_simulated_reboot_kills_owned_groups_and_runs_boot_restore`; `testlab/tests/acceptance/test_power_network.py::test_simulated_reboot_advances_boot_identity_and_recovers` |
| lab | the lab's controls never signal a reused PID ([testlab](testlab.md)) | `testlab/lhpc_testlab/supervisor.py:118` `pid_alive`, `:132` `_nginx_master` | none | — | `testlab/tests/unit/test_testlab.py::test_nginx_ctl_never_signals_a_reused_pid`, `::test_a_reused_pid_naming_the_config_is_not_the_lab_nginx`, `::test_the_lab_nginx_master_is_verified_and_signalled`, `::test_check_does_not_take_a_reused_pid_for_the_fake_gpsd` |

Open gaps: [backlog.md](backlog.md).

## Controller identity & self-update

LHPC's own checkout is a **controller identity** — a top-level `[controller]` manifest table
(strict allow-list; fixed `source_path = "src/loraham-pi-control"` and `branch = "main"`), NOT a
stack. Every generic verb aimed at its id refuses in the service layer and points to
`lhpc self-update` ([deployment.md](deployment.md#self-update)).

`controller_identity_live()` returns a **tri-state** verdict, computed only at startup refresh,
explicit "check now", and immediately before an apply:

- **ok** — self-hosted and verified: the checkout is under the runtime root, no symlink in the
  `root → src → checkout` chain, owned by the service user with no group/other write, its realpath
  equals both `repo_root()` and the imported package, on `main` with the approved canonical
  `origin`.
- **unsafe** — self-hosted but tampered/misconfigured (symlink, group-writable, wrong
  branch/origin, mismatch). **Blocks apply.**
- **not_applicable** — a dev checkout. Does not block; self-update uses `repo_root()`.

A same-account process replacing the checkout mid-check is **out of the threat model**: LHPC
refuses an unsafe layout; it does not claim same-account race-proofness.

The verdict is cached in one versioned, schema-validated self-update envelope; status GETs render
only that — never a live git, network or identity call. `lhpc web` holds a shared
controller-runtime flock for its lifetime; `self-update --apply` takes it exclusive (its place
among the other locks: [Locking](#safety-model)), so a running server never has its own source
mutated underneath it.
