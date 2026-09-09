# Operations & safety

Operational rules for `lhpc`. Internals and the safety model: [architecture.md](architecture.md).

## Contents

- [Not a supervisor](#not-a-supervisor)
- [Install channels](#install-channels)
- [Fast vs explicit](#fast-vs-explicit)
- [TX safety](#tx-safety)
- [Secrets and passwords](#secrets-and-passwords)
- [Backup & restore](#backup--restore)
- [Operating the console](#operating-the-console)
- [Reboot / Shut down](#reboot--shut-down)
- [Network](#network)
- [Identity drift on clean or uninstall](#identity-drift-on-clean-or-uninstall)

## Not a supervisor

`lhpc` does not stay running. Closing the CLI or web server never stops a stack. State is
reconstructed on every run, never read from a stale PID file
([architecture.md](architecture.md#probes-and-status)). `lhpc` only stops a process whose full identity still matches an LHPC ownership record; a
manual or foreign process is never signalled — you get a manual `kill` hint instead
([safety model](architecture.md#safety-model)).

The one boot-time exception is **boot restore** (Home → System → Autostart, or `lhpc autostart`;
default on): after a reboot, `lhpc-boot-restore.service` runs the driver once and exits
([deployment.md](deployment.md)) — it is not a supervisor either. It restores the stacks that were **LHPC-owned and never verifiably stopped** before the reboot — not literally
"alive at power-off": a stack that crashed shortly before the reboot may be restored too, and
that is safe because every restored start replays the **saved** configuration through the
normal gated start path (hardware, band arbitration, callsign, firewall exposure, TX mode
strictly from saved config) — the same saved configuration every web or CLI start runs. It refuses to act unless the web
console unit is enabled AND byte-exact canonical — a customized or foreign console unit disables
autonomous restarts — and honours the fail-closed `[boot] restore` switch in `local.toml`
(strictly boolean; anything else disables restore). A failed restore is not
retried — the dashboard banner and `lhpc autostart` name the stacks to start manually.

## Install channels

A stack is installed either from **source** (`pinned` / `dev` / `stable` — a git checkout lhpc
adopts and builds) or, for the three long-compiling stacks (daemon, meshtastic, meshcom), from a
**binary** artifact. The channel is a per-install choice, not a stored preference: `lhpc status
--versions` shows what a stack currently runs on. Policy (what is accepted and why):
[provenance.md](provenance.md).

What the binary channel means in practice:

- **No source tree**, so `build` and host tests refuse; the bounded TX test still works (it
  exercises the running stack).
- **meshcom runs open auth** — the published firmware has no mesh password
  ([stacks/meshcom.md](stacks/meshcom.md)).
- **Every binary mutation needs the stack stopped** — install, update, retire, uninstall and
  clean recheck under the operation locks, so a start slipping in mid-flight cannot be overwritten.
- **A failed install never costs you the previous one** — any failure restores the previous
  artifact, receipt and password setting. While the install journal is open — mid-run, or after
  a power cut — the receipt is not authoritative: `lhpc doctor` names the stack, and the next
  binary operation recovers it.
- **Switching to source is transactional too** — a dirty, foreign or wrong-remote checkout
  refuses the switch with the artifact untouched ([provenance.md](provenance.md)).
- **Ordinary files you add to a source checkout survive an update** — logs a stack writes, your own
  settings or scratch files. Editing upstream files does not: that makes the checkout dirty and
  blocks the update, and the fix is a fork, not a local edit ([provenance.md](provenance.md)).
- **No binary rollback**: going back means installing from source.
- **meshcom keeps its pinned clone** even on this channel (its run scripts live there), and
  **meshtastic provisions its CLI virtualenv locally** after extraction (it embeds absolute paths,
  so it cannot ship in an artifact). lhpc owns that virtualenv as a whole directory.
- **A broken binary install is named by `lhpc doctor`** — if the artifact's files disappear behind
  lhpc's back, ordinary status still shows a source state, so `doctor` reports the receipt as
  unsafe or superseded with the command that repairs it. Re-installing *is* the repair; only an
  unreadable receipt refuses, because then lhpc cannot know what the old install owned.

## Fast vs explicit

- Fast & bounded (no build, no mutation, no RF): `status`, `explain`, `doctor`, `logs`,
  `web` page loads. These do no network I/O, with one bounded exception when the position
  source is `gpsd` ([gps.md](gps.md)).
- Explicit & gated (print a plan, need `--yes` or a confirmation): `install`,
  `build`, `update`, `stack start/stop`, `test`, `uninstall`.

## TX safety

- TX is never auto-enabled; a freshly installed/configured stack is RX-only.
- TX happens only through an explicit `test --tx` or a stack you start that
  transmits (e.g. Graywolf's beacons).
- A `test --tx` shows band, parameters and expected RF effect, warns to use a
  **dummy load**, and confirms unless `--yes`. It sends one frame per band and
  verifies `TXOK` incremented.
- Read-only status/doctor/page loads never transmit and never initialise a radio.

## Secrets and passwords

Callsign, passwords, HMAC keys and private keys live only in git-ignored local
config (`~/loraham-pi-control/config/local.toml`, `config/secrets.toml` mode
`0600`, and file-based secrets such as the MeshCom `xr_pw` and the web session
key in `config/secrets/`, mode `0600`); nothing of that ever reaches tracked files or output
([architecture.md](architecture.md)). The one place a stored password is reachable is the
stack page's authenticated Password section, which masks the value behind a *Show* toggle and a
copy button (a
file that is not `0600` shows a reason instead). A stack password is never changed by a generic Settings save: MeshCom's is renewed through the HMAC Password actions ([stacks/meshcom.md](stacks/meshcom.md)); an app's own stored password is edited in its file, with the command that section prints.
Uninstall keeps local config by default.

## Backup & restore

All of your settings live under the runtime root (`$LHPC_RUNTIME_ROOT`, default
`~/loraham-pi-control`). Three things hold operator-authored data worth backing up — all of them kept by a
default `uninstall.sh` (which also keeps `backups/` and the `.lhpc-root` marker) and reused by a reinstall; everything else (`src/`, `build/`,
`logs/`, `systemd/` and the controller's own entries under `state/`) is regenerated by
install/apply and can be discarded.

- **`config/`** — every setting: operator identity + per-stack params (`local.toml`,
  `stacks/*.toml`), secrets (`secrets.toml`, `0600`), and the webserver PKI (`tls/` — CAs, server
  and client certificates, private keys, CRL).
- **`profiles/`** — your confirmed known-working compositions (optional but not regenerable).
- **App data under `state/`** — the stacks' own databases, identities and message stores:
  `state/graywolf state/meshcore state/openhop state/meshtasticd state/reticulum state/nomadnet
  state/lxmd state/sideband` (the `APP_DATA` list in `install.sh`/`uninstall.sh`). Never back up
  `state/` wholesale — its other entries are process ownership, jobs, locks and registries that
  must not be restored onto a different run.

Back up with every stack stopped (`lhpc stack stop <stack>`), as the LHPC user; `-p` preserves the
`0600` modes on secrets and keys, and the app-data directories that do not exist yet are skipped:

```bash
cd ~/loraham-pi-control          # or: cd "$LHPC_RUNTIME_ROOT"
tar -czpf ~/lhpc-backup-$(date +%F).tgz --ignore-failed-read config profiles \
    state/graywolf state/meshcore state/openhop state/meshtasticd \
    state/reticulum state/nomadnet state/lxmd state/sideband
chmod 600 ~/lhpc-backup-*.tgz
```

> The archive contains your secrets and TLS **private keys** — treat it as sensitive as `config/`
> itself: keep it `0600` and store it off-device (encrypted) if you sync it anywhere.

Restore onto a bootstrapped runtime root, again with the stacks stopped:

```bash
systemctl --user stop lhpc-nginx.service lhpc-web.service   # if the managed console is running
cd ~/loraham-pi-control
tar -xzpf ~/lhpc-backup-YYYY-MM-DD.tgz
lhpc webserver apply                                        # regenerate nginx from the restored config
systemctl --user start lhpc-web.service lhpc-nginx.service
```

Sources are not in the backup — re-adopt them with `lhpc install` (or `lhpc auto-install`); the
restored `config/` and known-working records then drive the rebuild.

## Operating the console

The console is a front end to the CLI — every action is dispatched through the same service
layer as the `lhpc` verbs, so validation, gating and results are identical. How it is served and
exposed: [webserver.md](webserver.md). Four areas:

- **Dashboard** — per band: the daemon monitor (live RSSI/stats/CAD), the stacks running on that
  band, a control to start another, and the System box (live host metrics, Autostart, Reboot / Shut down).
- **Apps** (`/stacks`) — the controller row, then every stack with Install / Build / Start /
  Stop / Test / Update / Uninstall / Clean. Interactive (TUI) apps show the command to run
  yourself; services start and stop directly. **Auto-install** installs (or updates), builds and optionally tests every
  stack in one guided run.
- **Settings** (per stack, on the Apps page) — the **only** place configuration changes: run
  params, config-file params and, for daemon clients, the daemon radio parameters
  ([stacks/daemon.md](stacks/daemon.md)). A save is validated as a whole and patches only its
  own keys; an unsupported structure refuses the save and preserves the file byte-for-byte.
- **System panels** — Firewall, Webserver, GPS, Hardware, System dependencies, and per-target logs.

Every mutating action needs an **explicit confirm**: install, update, stop, uninstall and clean
show a dry-run plan first (TX-capable ones add an RF/dummy-load warning; clean requires typing
the stack id); daemon live settings apply only whitelisted keys (TX/CAD tuning and the radio
params — [stacks/daemon.md](stacks/daemon.md)). The safety model behind this:
[architecture.md](architecture.md#safety-model).

**Start means start.** A Start or Restart from the Dashboard or the Apps page runs exactly the
**saved** configuration — there are no per-launch values. The click freezes the operation band
(the Apps dropdown, else the running band, else the primary), plans the run (hardware, band
arbitration, GPS, radio mode, firewall exposure, resource conflicts, identity) and then:

- **nothing consequential** → the start runs and you land back where you came from with the
  result flashed;
- **a consequential choice** — another running stack owns the radio (*Stop owner(s) & start*) or a
  restart would take running dependents down with it (*Stop dependents & restart*) → a minimal
  confirmation page: the plan, the consequence, **Start/Restart** lower right, **Cancel** lower left;
- **a missing or unusable identity** → nothing runs; you land on the stack's Settings with the
  offending row highlighted, the refusal flashed. Fix the row, Save, click Start again.

The plan and the apply judge the saved identity alike, so the CLI's dry run
(`lhpc stack start <id>`) refuses exactly what the web refuses, printing the `lhpc config` remedy.

**A web Start/Restart is detached.** The page returns at once and the start runs as a tracked
job, exactly like a web install or build; its log is `logs/web-start-<stack>.log`
(`web-restart-…` for a restart), reachable from the banner's *view →*. While the job runs the stack's row and its dashboard card carry a yellow *starting…*
badge; when it ends the page reloads once. The banner turns green with the result or red with
the refusal — a red banner is dismissed with ✕, an *unsafe* one (the job could not be tracked)
needs *Recover*. A second Start while one runs is refused ("already in progress"); a pending
self-update or a contended admission refuses before anything is spawned. The CLI and boot
restore start synchronously.

## Reboot / Shut down

The dashboard's system card ends with **Reboot…** / **Shut down…** buttons (each behind a
confirm page). They act through logind (`systemctl reboot|poweroff`) — a graceful teardown, so
the SD card is safe and running stacks come back via boot restore on the next power-on. The
buttons render **only** when logind authorizes that action for the operator, probed per button
(`CanReboot` / `CanPowerOff`), because the rule file
`/etc/polkit-1/rules.d/49-lhpc-power.rules` lives in a directory the operator process cannot
read on stock Debian. Fresh installs get the rule from
`bootstrap-deps.sh` (opt out with `--no-power-controls`); on an existing box the
System-dependencies panel (and `lhpc doctor`) shows a paste-ready install command. lhpc never
installs the rule itself — it never runs privileged commands. A refusal at apply time is typed
and repeats the install command. Apply then records a short-lived pending marker that refuses
new builds and updates until the trigger fires (an unreadable or stale marker is named in that
refusal and is yours to delete); failures after the authorization land only in
`logs/power-<kind>.log`.

## Network

The Apps page gains a **Network** panel (scan, join a WLAN, preferred network, back to the AP)
when the box has an `lhpc-ap` NetworkManager profile and `nmcli` — a capability gate, not an
image type. Authorization is the polkit rule `/etc/polkit-1/rules.d/49-lhpc-network.rules`
from `bootstrap-deps.sh` (opt-out `--no-network-controls`). The AP, the panel and the
console-allowlist step: [wifi-access-point.md](wifi-access-point.md).

A purge (`uninstall.sh --purge` or `lhpc clean --purge`) removes the preferred-network record, so
the box falls back to its own access point at the next link loss or reboot. Re-declaring a
preferred WLAN in the Network panel afterwards is mandatory, not optional.

## Identity drift on clean or uninstall

Every adopted source carries an ownership record (`state/source-registry/`) naming the commit
LHPC checked out ([provenance.md](provenance.md)). A destructive command re-proves that record
first and refuses when the checkout's HEAD or origin no longer matches it — the tree changed outside an LHPC transaction,
so LHPC will not delete it. Inspect the checkout (`git -C src/<name> log -1`, `git remote -v`);
if it is yours to drop, remove it and its record by hand (`rm -rf src/<name>
state/source-registry/<name>-*.json`) and reinstall.
