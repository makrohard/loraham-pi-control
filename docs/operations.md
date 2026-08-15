# Operations & safety

Operational rules for `lhpc`. See `architecture.md` for internals.

## Contents

- [Not a supervisor](#not-a-supervisor)
- [Install channels](#install-channels)
- [Fast vs explicit](#fast-vs-explicit)
- [TX safety](#tx-safety)
- [Resource ownership](#resource-ownership)
- [Secrets](#secrets)
- [Backup & restore](#backup--restore)
- [Web console](#web-console)
- [Reboot / Shut down](#reboot--shut-down)
- [Daemon radio parameters](#daemon-radio-parameters)
- [Safety](#safety)

## Not a supervisor

`lhpc` does not stay running. Closing the CLI or web server never stops a stack.
On each run it reconstructs real state from systemd, process identity, endpoint
probes, source/pin state and resource ownership — never from a stale PID file.

The one boot-time exception is **boot restore** (`lhpc autostart`, default on): after a reboot,
`lhpc-boot-restore.service` runs the driver ONCE and exits — it is not a supervisor either. It
restores the stacks that were **LHPC-owned and never verifiably stopped** before the reboot —
not literally "alive at power-off": a stack that crashed shortly before the reboot may be
restored too, and that is safe because every restored start replays the **saved** configuration
through the normal gated start path (hardware, band arbitration, callsign, firewall exposure,
TX mode strictly from saved config). One-off confirm-page overrides from the previous session
are never replayed. Each piece of pre-reboot evidence is consumed exactly once: a failed restore
is not retried — the dashboard banner and `lhpc autostart` name the stacks to start manually.

## Install channels

A stack is installed either from **source** (`pinned` / `dev` / `stable` — a git checkout lhpc
adopts and builds) or, for the three long-compiling stacks (daemon, meshtastic, meshcom), from a
**binary** artifact. The channel is a per-install choice, not a stored preference: what a stack
runs on now is recorded in its receipt (`state/binary/<stack>.json`) and shown as `binary@<sha>`
by `lhpc status --versions`.

An artifact is accepted only when it verifies by sha256 **and** size, was built from exactly the
commits this lhpc pins (per component), matches this platform, and passed the builder's mandatory
smoke test. Anything else is a typed refusal that offers the source channel — never a silent
source build. Policy detail: [provenance](provenance.md#the-binary-channel).

What it means in practice:

- **No source tree**, so `build` and host tests refuse; the bounded TX test still works (it
  exercises the running stack).
- **meshcom runs open auth** — the published firmware has no mesh password, so the bridge runs
  without one, password changes are refused, and the firewall models that listener as
  unauthenticated.
- **Every binary mutation needs the stack stopped** — install, update, retire, uninstall and
  clean recheck under the operation locks, so a start slipping in mid-flight cannot be overwritten.
- **A failed install never costs you the previous one.** The mesh-password switch, the file
  promotion, the CLI provisioning, the probes and the receipt write are ONE journaled transaction:
  any failure restores the previous artifact, its receipt and the previous password setting.
  While that journal is open — mid-run, or after a power cut — the receipt is not authoritative:
  `lhpc doctor` names the stack, and the next binary operation recovers it.
- **Switching to source is transactional too.** The requested selector is enforced: a checkout at
  a different commit is replaced through the normal source transaction, and a dirty, foreign or
  wrong-remote tree refuses the switch with the artifact untouched. The artifact is set aside
  locally until the whole switch (every source group, its ownership record, and the MeshCom
  password step) has succeeded — a failure restores it from disk, with no download and no pin
  re-check.
- **No binary rollback**: the release keeps the latest artifact per stack, so going back means
  installing from source.
- **meshcom keeps its pinned clone** even on this channel (its run scripts live there), and
  **meshtastic provisions its CLI virtualenv locally** after extraction (it embeds absolute paths,
  so it cannot ship in an artifact, and the stack cannot apply its region without it). lhpc owns
  that virtualenv as a whole directory.
- **A broken binary install is named by `lhpc doctor`** — if the artifact's files disappear behind
  lhpc's back, ordinary status still shows a source state, so `doctor` reports the receipt as
  unsafe or superseded with the command that repairs it. Re-installing *is* the repair; only an
  unreadable receipt refuses, because then lhpc cannot know what the old install owned.

## Fast vs explicit

- Fast & bounded (no build, no mutation, no RF): `status`, `explain`, `doctor`, `logs`,
  `web` page loads. These do no network I/O, with one exception: when the position source is
  `gpsd`, `doctor` makes one bounded query to that gpsd to see whether it owns a receiver.
- Explicit & gated (print a plan, need `--yes` or a confirmation): `install`,
  `build`, `update`, `stack start/stop`, `test`, `uninstall`.

## TX safety

- TX is never auto-enabled; a freshly installed/configured stack is RX-only.
- TX happens only through an explicit `test --tx` or a stack you start that
  transmits (e.g. iGate beacons).
- A `test --tx` shows band, parameters and expected RF effect, warns to use a
  **dummy load**, and confirms unless `--yes`. It sends one frame per band and
  verifies `TXOK` incremented.
- Read-only status/doctor/page loads never transmit and never initialise a radio.

## Resource ownership

One active stack owns a LoRa band at a time. The daemon's 433/868 instances
cooperate on the SPI bus (internal serialisation); a direct radio user
(meshtastic) claims a band exclusively. Daemon sockets are provider (daemon) /
consumer (kiss, bridge, meshcore). Starting a stack is blocked, with the holder
named, if a running stack already holds a band it needs.

## Secrets

Callsign, passwords, HMAC keys and private keys live only in git-ignored local
config (`~/loraham-pi-control/config/local.toml`, `config/secrets.toml` mode
`0600`, and file-based secrets such as the MeshCom `xr_pw` and the web session
key in `config/secrets/`, mode `0600`) — never in tracked files, status output
or web actions. Uninstall keeps local config by default.

## Backup & restore

All of your settings live under the runtime root (`$LHPC_RUNTIME_ROOT`, default
`~/loraham-pi-control`). Only two directories hold operator-authored data worth backing up;
everything else (`src/`, `build/`, `state/`, `logs/`, `systemd/`) is regenerated by
install/apply and can be discarded.

- **`config/`** — every setting: operator identity + per-stack params (`local.toml`,
  `stacks/*.toml`), secrets (`secrets.toml`, `0600`), and the webserver PKI (`tls/` — CAs, server
  and client certificates, private keys, CRL).
- **`profiles/`** — your confirmed known-working compositions (optional but not regenerable).

Back up (run as the LHPC user; `-p` preserves the `0600` modes on secrets and keys):

```bash
cd ~/loraham-pi-control          # or: cd "$LHPC_RUNTIME_ROOT"
tar -czpf ~/lhpc-backup-$(date +%F).tgz config profiles
chmod 600 ~/lhpc-backup-*.tgz
```

> The archive contains your secrets and TLS **private keys** — treat it as sensitive as `config/`
> itself: keep it `0600` and store it off-device (encrypted) if you sync it anywhere.

Restore onto a bootstrapped runtime root:

```bash
systemctl --user stop lhpc-nginx.service lhpc-web.service   # if the managed console is running
cd ~/loraham-pi-control
tar -xzpf ~/lhpc-backup-YYYY-MM-DD.tgz
lhpc webserver apply                                        # regenerate nginx from the restored config
systemctl --user start lhpc-web.service lhpc-nginx.service
```

Sources are not in the backup — re-adopt them with `lhpc install` (or `lhpc auto-install`); the
restored `config/` and known-working records then drive the rebuild.

## Web console

Productive mode: HTTPS via nginx (default `127.0.0.1:8443`) → Waitress over a protected Unix
socket (the managed `lhpc-web.service` runs `lhpc web --socket`, no TCP listener). A bare
`lhpc web` (loopback TCP, default `:8770`) is a non-productive interactive mode for local use.
See `docs/webserver.md`. GET routes are read-only.
Mutating routes follow one pattern — **POST + CSRF token + explicit confirm**,
dispatched through the same service layer as the CLI: stack/component actions show
a dry-run plan first (TX-capable ones add an RF/dummy-load warning); daemon live
settings apply only a whitelisted non-RF tuning (TX mode, CAD/LBT). Security
headers (incl. `Content-Security-Policy: default-src 'self'`) on every response. Exposing the
console to your LAN is opt-in and gated: [webserver](webserver.md), [firewall](firewall.md).

## Reboot / Shut down

The dashboard's system card ends with **Reboot…** / **Shut down…** buttons (each behind a
confirm page). They act through logind (`systemctl reboot|poweroff`) — a graceful teardown, so
the SD card is safe and running stacks come back via boot-restore on the next power-on. The
buttons render **only** when logind actually authorizes the operator (`CanReboot` — probed
bounded and cached, since the rule file `/etc/polkit-1/rules.d/49-lhpc-power.rules` lives in a
directory the operator process cannot read on stock Debian). Fresh installs get
both from `bootstrap-deps.sh` (opt out with `--no-power-controls`); on an existing box the
System-dependencies panel (and `lhpc doctor`) shows a paste-ready install command. lhpc never
installs the rule itself — it never runs privileged commands. Apply performs a synchronous
logind authorization check (a refusal is typed and repeats the install command), records a
short-lived pending marker that refuses new builds/updates until the trigger fires, then
requests the action detached so the HTTP response reaches the browser first; failures after
that authorization land only in `logs/power-<kind>.log`.

## Daemon radio parameters

Each daemon-client stack has a collapsible **Daemon radio parameters** panel (config, stack and
start-confirm pages; follows the 433/868 band switch). Editable params: MODE, FREQ, SF, BW, CR, CRC,
LDRO, PREAMBLE, SYNC, POWER, TXMODE, TXQUEUE, CADMONITOR, CADRSSI, CADWAIT, CADIDLE,
CADTXAFTERTIMEOUT. Defaults come from each app's source (`lhpc/core/daemon_params.py`); MeshCom
CADIDLE is 28 ms. Every value is validated + canonicalised server-side (`daemon_control.validate_set`)
on save and read. **Save** persists, **Apply** pushes to the running daemon, **Reset** restores
defaults. lhpc applies a stack's values to the daemon **once**, at daemon-READY before the stack's
components (CLI and web share this path); params the app re-SETs on connect (radio + TXMODE) are shown
**greyed**. MODE=FSK triggers a browser-only OK/Cancel warning. The daemon page also has per-parameter
**live** controls prefilled with reported values, and closable STATUS/STATS readouts.

**Apply live** is truthful: `ok` only when every set was applied; a partial or total failure is a
warning, and params the daemon does not echo are reported SENT, not confirmed. A contended band
returns a typed busy result and leaves the saved profile intact.

**Start-confirm** shows one panel per band the launch touches, whose values apply to **that start
only** (never persisted). Every field is validated before any launch or CONF `SET` — a malformed,
duplicated, unknown or wrong-band field fails the start.

Config writes are type-safe and fail-closed, and a managed save patches only its own keys: an
unsupported structure or wrong table shape refuses the save and preserves the file byte-for-byte.

## Safety

`lhpc` only stops a process whose full identity still matches an LHPC ownership
record (pid, start time, pgid, sid, executable, argv); a stale record, a manual or
foreign process, or the controller's own group is never signalled — the operator
gets a manual `kill` instead. A `start` reports failure unless required components
verify ready. A failed update leaves the active source intact. Uninstall refuses
while running and preserves shared checkouts and config/secrets. GET routes do no
network. See `docs/hardening-0.1.md` (including what is still open).
