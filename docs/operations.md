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

Each stack is installed either from **source** (`pinned` / `dev` / `stable` — a git checkout lhpc
adopts and builds) or, for the three long-compiling stacks, from a **binary** artifact published
by lhpc-binaries. The channel is a per-install choice, not a stored preference: what a stack is
running on right now is recorded in `state/binary/<stack>.json` (its receipt) and shown as
`src: binary  binary@<sha>` in status.

The binary channel accepts an artifact only when it verifies by sha256 AND size, was built from
exactly the commits this lhpc pins (per component, not a single "built from" field), matches this
platform, and passed the builder's mandatory smoke test. Anything else is a typed refusal that
offers the source channel — never a silent source build.

Consequences worth knowing before you rely on it:

- **No source tree.** Build and host tests refuse (they need the checkout); the bounded TX test
  still works, since it exercises the running stack.
- **meshcom runs open auth** — the published firmware has no mesh password, so the bridge runs
  without one and HMAC changes are refused until you install from source. The managed firewall
  models that listener as unauthenticated accordingly.
- **No binary rollback.** The release keeps the latest artifact per stack; going back means
  installing from source (`lhpc install <stack> --source pinned --yes`), which also retires the
  binary install.
- Switching to source retires the artifact first, and refuses if you have edited an installed
  file by hand rather than overwriting your change.
- **Every binary mutation is a stopped-stack operation.** Install, update, retire, uninstall and
  clean all refuse while a component of that stack is running (checked again under the operation
  locks, so a start that slips in mid-flight still cannot be overwritten).
- **A failed install never costs you the previous one.** The mesh-password switch, the file
  promotion, the meshtastic CLI provisioning, the executable probes and the receipt write are ONE
  journaled transaction, opened before the first change: if any step fails, the previous artifact
  (including files only the old artifact shipped), its receipt and the previous mesh-password
  setting are all restored. While that journal is open — during a run, or after a power cut in the
  middle of one — the receipt is **not** treated as authoritative: status reports the stack as
  needing attention, and the next binary operation recovers it. `clean --purge` is the escape
  hatch when a journal is damaged beyond recovery.
- **Switching to source cannot cost you the binary.** The artifact is set aside — locally, inside
  the same journaled transaction — so the clone meets a clean destination; if the adoption then
  fails, the exact previous install is restored from disk. No download, no release lookup and no
  pin re-check: switching to source is often done *because* the published binary is behind, and a
  restore that re-downloaded would refuse for that very reason. The retirement becomes final only
  once the source install has succeeded.
- **meshcom keeps its pinned source checkout** even on the binary channel (its run scripts live
  there). lhpc verifies that checkout is ours and at the pin before reusing it, and adopts it if
  absent — a stale or foreign tree is refused rather than combined with pinned binaries.
- **meshtastic provisions its managed CLI locally** after extraction: that virtualenv embeds
  absolute paths and cannot be shipped in an artifact, yet the stack cannot start without it
  (it applies the LoRa region after every start). lhpc owns it as a whole DIRECTORY — half a
  virtualenv is symlinks pointing outside the runtime root — so it is moved aside intact during
  an install, restored intact if the install fails, and removed intact at retirement.
- **A broken binary install is named by `lhpc doctor`.** If the artifact's files are removed
  behind lhpc's back (a source adoption of a shared checkout does exactly that), ordinary status
  still shows the stack's source state; `doctor` reports the receipt as unsafe or superseded,
  with the reason and the command that repairs it. Re-installing IS the repair — only a receipt
  that cannot be read at all refuses, because then lhpc cannot know what the old install owned.

## Fast vs explicit

- Fast & bounded (no network, no build, no mutation, no RF): `status`, `explain`,
  `doctor`, `logs`, `web` page loads.
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
headers (incl. `Content-Security-Policy: default-src 'self'`) on every response.

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

**Apply live** is truthful: `ok` only when every set is applied; `PARTIAL`/total failure is a warning;
radio params the daemon does not echo are reported SENT, not confirmed. It takes the band
lifecycle/radio lock (re-entrant with an in-progress Start; a contended band returns a typed busy
result); a failure leaves the saved profile persisted.

**Start-confirm** shows one panel per band the launch touches (two when both bands are requested), with
band-scoped `dp_<band>_<PARAM>` fields applied for **that start only** (never persisted). Every
`dp_*` field is strictly parsed and validated (band/param/value) before any launch or CONF `SET`; a
malformed, duplicated, unknown, wrong-band or invalid field fails the start. Blank/absent = no
override; **Reset to defaults** submits the defaults.

**Local config (`local.toml`).** Writes are type-safe and fail-closed: scalars and flat tables keep
their exact types (bool/int/finite-float/string, quotes, control chars, Unicode, quoted keys all
round-trip), validated by re-parse before the atomic write. A managed update **patches only its own
keys** — an operator save touches only `callsign`, a remote save only that component's key
(blank clears it); everything else is preserved. An unsupported structure (array, nested table,
datetime, NaN/inf, control-char key, invalid Unicode) or a wrong table shape (`operator = "text"`,
`remotes = "x"`) refuses the save and preserves the file byte-for-byte. A remote may be changed only
for a source component of the target stack.

## Safety

`lhpc` only stops a process whose full identity still matches an LHPC ownership
record (pid, start time, pgid, sid, executable, argv); a stale record, a manual or
foreign process, or the controller's own group is never signalled — the operator
gets a manual `kill` instead. A `start` reports failure unless required components
verify ready. A failed update leaves the active source intact. Uninstall refuses
while running and preserves shared checkouts and config/secrets. GET routes do no
network. See `docs/hardening-0.1.md` (including what is still open).
