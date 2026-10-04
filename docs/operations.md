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
- [Clock](#clock)
- [Identity drift on clean or uninstall](#identity-drift-on-clean-or-uninstall)

## Not a supervisor

`lhpc` does not stay running: closing the CLI or web server never stops a stack, and state is
reconstructed on every run, never read from a stale PID file
([probes](architecture.md#probes-and-status)). `lhpc` only stops a process whose full identity
still matches an LHPC ownership record; a manual or foreign process is never signalled — you get
a `kill` hint instead ([safety model](architecture.md#safety-model)).

**Boot restore** (Home → System → Autostart, or `lhpc autostart`; default on):
`lhpc-boot-restore.service` runs the driver once after a reboot and exits
([deployment.md](deployment.md)).

- It restores the stacks that were **LHPC-owned and never verifiably stopped** before the reboot —
  so a stack that crashed shortly before may come back too. Every restored start replays the
  **saved** configuration through the normal gated start path (hardware, band arbitration,
  callsign, firewall exposure, TX mode), as a web or CLI start does.
- It refuses to act unless the web console unit is enabled and byte-exact canonical, and honours
  `[boot] restore` in `local.toml` (strictly boolean; anything else disables restore).
- An explicit `lhpc stack stop` is the last word: the stack stays down across reboots even when
  the stop could not verify the process gone; the next `stack start` makes it restorable again.
- An optional service that was started on its own (e.g. the MeshCore web UI without its
  "auto-start with the stack" tick) is started again after its stack; one that was stopped stays
  stopped.
- A failed restore is not retried: the dashboard banner and `lhpc autostart` name the stacks to
  start by hand. Log: `logs/lhpc-boot-restore.log` (web: Controller logs → boot-restore).

## Install channels

A stack is installed from **source** (`pinned` / `dev` / `stable`, a git checkout lhpc adopts and
builds) or, for daemon, meshtastic and meshcom, from a **binary** artifact. The channel is a
per-install choice; `lhpc status --versions` shows the current one. Policy:
[provenance.md](provenance.md#the-binary-channel).

On the binary channel:

- **No source tree**: `build` and host tests refuse; the bounded TX test works.
- **meshcom runs open auth** ([stacks/meshcom.md](stacks/meshcom.md)).
- **Every binary mutation needs the stack stopped** — install, update, retire, uninstall and
  clean recheck this under the operation locks.
- **A failed install keeps the previous one** — artifact, receipt and password setting are
  restored. While the install journal is open (mid-run, or after a power cut) the receipt is not
  authoritative: `lhpc doctor` names the stack, and the next binary operation recovers it.
- **Switching to source is transactional** — a dirty, foreign or wrong-remote checkout refuses the
  switch with the artifact untouched.
- **No binary rollback**: going back means installing from source.
- **Only the latest release has binaries**: the index holds one per stack and it must match this
  lhpc's pins; otherwise install from source, self-update first, or (where allowed) accept the
  mismatch ([provenance](provenance.md#the-binary-channel)).
- **meshcom keeps its pinned clone** (its run scripts live there), and **meshtastic provisions its
  CLI virtualenv locally** after extraction (it embeds absolute paths); lhpc owns that virtualenv
  as a whole directory.
- **A broken binary install is named by `lhpc doctor`**: if the artifact's files disappear,
  ordinary status shows a source state, so `doctor` reports the receipt as unsafe or superseded
  with the repair command. Re-installing is the repair; only an unreadable receipt refuses,
  because then lhpc cannot know what the old install owned.

Files you add to a source checkout survive an update; editing upstream files blocks it
([ownership records](provenance.md#ownership-records)).

### Keeping stacks current

What to run in each situation and what you will see. The channels themselves:
[provenance](provenance.md#selections).

| situation | run | what you see |
|---|---|---|
| first install | `lhpc install <stack> --yes`, then on the source channel `lhpc build <stack> --yes`; or `lhpc auto-install --yes` for every stack (install and build) | the plan, then the install on the stack's default channel: the published binary where there is one for this platform, else `pinned` |
| keep current at the pins this release was tested with | `lhpc self-update --apply`, then `lhpc update <stack> --yes` | each source moves to `pinned` — your newest known-working composition for the stack, else the release's manifest pin — and the stack is built again when the new sources need it (in the console, as a build job with its live log). A binary-installed stack updates to the published binary; one already installed and intact is not downloaded again |
| follow development | `lhpc update <stack> --source dev --yes` | the branch tip; when adopting it fails, the update retries once at the known-working (else manifest-pin) commit and says *FELL BACK* |
| follow stable | `lhpc update <stack> --source stable --yes` | the newest plain version tag, else the default branch's head |
| binary where published | once: `lhpc install <stack> --source binary --yes`; afterwards `lhpc update <stack> --yes` stays on the binary | a published binary built from other commits than this lhpc's pins is refused; `Next:` offers `lhpc self-update --apply` and the source install |
| a fetched release (graywolf) | `lhpc update graywolf --upstream --yes` for the newest release; `lhpc build graywolf --yes` for the pinned one | the release fetched and checked against its checksums, then a restart if the stack was running |
| back to known-working after a failed update | [what happens when an update fails](#what-happens-when-an-update-fails) | *Update INCOMPLETE* with the failed source's line |
| an update was refused | the commands under `Next:`, or, where there is none, a *nothing to run here* line naming what has to be fixed on the box. That holds for every refusal the controller's `update`, graywolf-update and `build` functions themselves, and any function in its self-update and binary-channel modules, return outright as a refusal (`tests/repo/test_refusal_remedy.py` reads each one); where the step depends on a cause lhpc cannot tell apart, the line names each cause with its step. Not covered yet: a refusal by task admission (an uninstall or a reboot pending, an update state that cannot be checked; only a stuck self-update names `lhpc self-update --recover-request`); a failure these paths compute from the outcome of work done (a failed build, a graywolf update whose restart failed, a self-update recovery that combines two results, a console self-update whose unit refresh failed); refusals other code hands through these paths, for example the MeshCore plugin-manager refusal (it says to reboot) and a self-update's firewall preflight; `lhpc install` on the source channel. An *Update INCOMPLETE* result is not a refusal: its per-source lines say what failed | the cause on the first line. A running stack: `lhpc stack stop <stack> --yes`. A left-over `src/.<name>.prev`: move it out of `src/`, then retry. A busy lock: wait for the operation it names, then run the same command again |
| the first start after a source update | nothing: the update built the stack. Only if that build failed or could not start (*needs-rebuild*): `lhpc build <stack> --yes` | while a build is due, `lhpc status --versions` marks the component *needs-rebuild* and `lhpc stack start` refuses it as *not built* or *sources changed since the last build*, naming `lhpc build <stack>` |

### What happens when an update fails

An update — `lhpc update <stack>`, `lhpc self-update --apply`, or the same from the console — ends
in exactly one of three ways, or — only where lhpc cannot prove what it finds is its own, or cannot
record what it did — *recovery-required* (below the table):

1. **Converged.** The stack is at the version its selector names (`pinned` unless you chose `dev`
   or `stable`) and built; lhpc itself is at its upstream with its venv synced and its units
   refreshed. The result says so. A binary update to the artifact already installed and intact
   downloads nothing — said only after an interrupted binary install has been recovered first.
2. **Nothing changed, the remedy named.** The previous version is still the active one: a refusal,
   a failed carry or activation (rolled back), or Ctrl-C, a full disk or an I/O error at any one
   step — and a power loss while a source update stages its copy (once the copy's identity is
   recorded) — rolled back or finished by the next lhpc source command (a binary install: by the
   next binary command, which runs that recovery first under its locks). The result names the
   cause and the command that resolves it.
3. **A named state** — only where putting the previous version back is not one operation lhpc
   has. Each is recorded on disk, shown by `lhpc status` with its word, and resolved by one
   command:

| state | what happened | recorded in | shown by | resolved by |
|---|---|---|---|---|
| *needs-rebuild* | `lhpc update` activated the new sources, and the stack's build failed or could not start (in the console: its build job was refused). The previous build went with the tree the update replaced, so the old version would need a build too | the build marker in the source tree, which no longer matches its sources | `lhpc status --versions` | `lhpc build <stack> --yes` |
| *prior-in-use* | the new source is active; its archived prior `src/.<name>.prev` is kept because another process can still write into it (an open file, a working directory, a shared writable mapping, or a file the kernel reports open for writing) | the transaction journal under `state/source-txn/` | `lhpc status`, while that check still finds the process (once it has ended: *update-interrupted* — the next lhpc source command removes the prior, or keeps it as *prior-dirty* if it holds changes of yours) | `lhpc update <component> --yes` once that process has ended (any lhpc source command removes the prior then). If the line names a file that *could not be checked*, it is not your user's (created with `sudo`, say): give it back to your user (`sudo chown`), then the same command |
| *prior-dirty* | the new source is active; its archived prior is kept because it holds changes of yours, made while the update ran or while it was kept in use, and lhpc never deletes it | the transaction journal (`prior-dirty-retained`) | `lhpc status` | one command, the `mv` the result and `lhpc status` print, which moves the prior out of `src/` and keeps it: `lhpc status` then shows nothing, and the next lhpc source command clears the journal before anything else |
| *venv-unsynced* | `lhpc self-update` moved the checkout, and the venv sync failed | `state/selfupdate.incomplete` | `lhpc status` (the controller row) | `lhpc self-update --apply` (it finds the checkout current and runs the sync and the unit refresh it skipped) |
| *units-stale* | `lhpc self-update` moved the checkout, and the managed units could not be refreshed: boot restore is skipped until they are | `state/selfupdate.incomplete` | `lhpc status` (the controller row) | `lhpc self-update --repair-integration` |

Besides these, lhpc keeps — never deletes — what it cannot prove is its own, and names it:
something outside lhpc changed a tree or a journal while an update ran, or a disk refused the
undo itself. The result, and every later operation of that kind, says *recovery-required* and
what to inspect; `lhpc status` shows an unfinished source transaction as *update-interrupted*. So
are: a staged copy whose identity was never recorded (a power loss between its creation and its
record) or that is not the one its record names — kept, the line saying *a staging directory this
run cannot prove as its own* with its path; a staged copy a power loss caught while your files
were being copied into it — kept with the old tree and the journal, its path named, and once you
have removed it the next lhpc source command puts the old tree back; a transaction journal that
could not record why an archived prior is kept; a self-update state record that cannot be read
(`lhpc status` shows *recovery-required* on the controller row with the steps by hand; neither
self-update command clears it); a self-update whose state record cannot be written at all — it
leaves no state, so it fails *recovery-required* with the cause, where the checkout is, and what
to run: for a failed venv sync the sync and then `lhpc self-update --repair-integration` (the unit
refresh it skipped; `--apply` cannot resume it without the record), for stale units
`lhpc self-update --repair-integration`. These are resolved by hand.

Back to known-working from a broken `dev` or `stable` follow: `lhpc update <stack> --source pinned
--yes` for a source install — your newest known-working commit, else the manifest pin (`lhpc
install` keeps an installed source as it is and points there); a binary install goes back by
`lhpc install <stack> --source pinned --yes`.

## Fast vs explicit

- Fast & bounded (no build, no mutation, no RF): `status`, `explain`, `doctor`, `logs`, `web`
  page loads. No network I/O, except one bounded gpsd query when the position source is `gpsd`
  ([gps.md](gps.md)).
- Explicit & gated ([conventions](cli.md#conventions)): `install`, `build`, `update`,
  `stack start/stop`, `test`, `uninstall`.

## TX safety

- TX is never auto-enabled; a freshly installed/configured stack is RX-only.
- TX happens only through an explicit `test --tx` or a stack you start that transmits (e.g.
  Graywolf's beacons).
- `test --tx` shows band, parameters and expected RF effect, warns to use a **dummy load**, and
  confirms unless `--yes`. It sends one frame per band and verifies `TXOK` incremented. An app
  stack's TX test is refused, naming the stack, while another running stack uses one of its bands;
  it holds each band's radio claim from that check to the last frame, so a start cannot take the band
  in between.
- Read-only status/doctor/page loads never transmit and never initialise a radio.
- +20 dBm on an SX127x board is off by default and needs a per-band switch and a daemon restart;
  its limits, and the LoRaHAM 433 caveat, are in [daemon](stacks/daemon.md#settings).

## Secrets and passwords

Callsign, passwords, HMAC keys and private keys live only in git-ignored local config:
`config/local.toml`, `config/secrets.toml` (`0600`) and file secrets in `config/secrets/`
(`0600`, e.g. MeshCom's `xr_pw` and the web session key). None of it reaches tracked files or
output ([architecture.md](architecture.md)).

- No command prints a password, and none reaches a log, flash message or the API; from a shell on
  the Pi, read the file itself.
- In the console, a stored password is reachable only in the stack page's authenticated Password
  section, masked behind *Show* and a copy button (a file that is not `0600` shows a reason
  instead).
- A generic Settings save never changes a stack password: MeshCom's is renewed through the HMAC
  actions ([stacks/meshcom.md](stacks/meshcom.md)); an app's own password is edited in its file,
  with the command that section prints. Which file each stack uses is in its stack page.
- Uninstall keeps local config by default.

## Backup & restore

Your data lives under the runtime root (`$LHPC_RUNTIME_ROOT`, default `~/loraham-pi-control`).
Three things are not regenerable; a default `uninstall.sh` keeps them (plus `backups/` and the
`.lhpc-root` marker) and a reinstall reuses them. Everything else (`src/`, `build/`, `logs/`,
`systemd/`, the controller's own entries under `state/`) is regenerated by install/apply.

- **`config/`** — every setting: `local.toml`, `stacks/*.toml`, `secrets.toml` (`0600`),
  `secrets/`, and the webserver PKI (`tls/` — CAs, certificates, private keys, CRL).
- **`profiles/`** — your confirmed known-working compositions.
- **App data under `state/`** — the stacks' databases, identities and message stores:
  `state/graywolf state/meshcore state/openhop state/meshtasticd state/reticulum state/nomadnet
  state/lxmd state/sideband state/meshchat` (the `APP_DATA` list in `install.sh`/`uninstall.sh`).
  Never back up `state/` wholesale — its other entries are process ownership, jobs, locks and
  registries that must not be restored onto a different run.

`uninstall.sh` writes the `.lhpc-uninstalling` guard (no new tasks), refuses on active or
unprovable jobs or any UNKNOWN component, stops clients before the shared daemon and verifies
they stopped; if it cannot prove that, it removes the guard and deletes nothing. It stops and
removes only byte-exact canonical units of the same root.

**Full backup.** With every stack stopped (`lhpc stack stop <stack>`), as the LHPC user; `-p`
keeps the `0600` modes, and missing app-data directories are skipped:

```bash
cd ~/loraham-pi-control          # or: cd "$LHPC_RUNTIME_ROOT"
tar -czpf ~/lhpc-backup-$(date +%F).tgz --ignore-failed-read config profiles \
    state/graywolf state/meshcore state/openhop state/meshtasticd \
    state/reticulum state/nomadnet state/lxmd state/sideband state/meshchat
chmod 600 ~/lhpc-backup-*.tgz
```

> The archive contains your secrets and TLS **private keys**: keep it `0600` and store it
> off-device only encrypted.

Restore onto a bootstrapped runtime root, stacks stopped:

```bash
systemctl --user stop lhpc-nginx.service lhpc-web.service   # if the managed console is running
cd ~/loraham-pi-control
tar -xzpf ~/lhpc-backup-YYYY-MM-DD.tgz
lhpc webserver apply                                        # regenerate nginx from the restored config
systemctl --user start lhpc-web.service lhpc-nginx.service
```

Sources are not in the backup: re-adopt them with `lhpc install` (or `lhpc auto-install`); the
restored `config/` and known-working records drive the rebuild.

**Secrets only** (`lhpc secrets`, [synopsis](cli.md#secrets)): one plain tar of `config/tls/`,
`config/secrets/`, `config/secrets.toml` and each stack's declared state folder.

- `backup` writes it mode `0600`, in clear, to `~/lhpc-secrets-<host>-<UTC time>.tar` or `<file>` —
  never over an existing file, never inside the runtime root — and refuses while a stack that
  keeps its state there runs. Encrypt it before it leaves the box (e.g. `gpg -c <file>`).
- The `.p12` bundles' one-time passphrases are not in it; a device whose passphrase is lost gets
  a new certificate with `lhpc webserver cert reissue <label>`.
- `restore` needs the console stopped (`systemctl --user stop lhpc-web`, start it afterwards). It
  checks the whole file and prints what it would overwrite, create and leave; without a flag it
  changes nothing. `--yes` applies only when no target exists; `--overwrite` replaces them (on a
  terminal you type `overwrite`).
- A restore that fails part-way leaves a mixed state with no undo: back up this box first.
- `--only pki` restores just the two CAs, to share them with another box (then
  `lhpc webserver tls-renew` and `lhpc webserver apply`); a full restore on another box would give
  two boxes the same node identities.

## Operating the console

The console runs every action through the same service layer as the `lhpc` verbs; serving and
exposure: [webserver.md](webserver.md).

- **Dashboard** — per band: the daemon monitor (RSSI/stats/CAD), the stacks on that band, a
  start control; the System box (host metrics, Autostart, Reboot / Shut down).
- **Apps** (`/stacks`) — the controller row, then every stack with Install / Build / Start / Stop /
  Test / Update / Uninstall / Clean. Interactive (TUI) apps show the command to run yourself
  ([adding-a-stack](adding-a-stack.md#run--readiness)). **Auto-install** installs (or updates),
  builds and optionally tests every stack in one run.
- **Settings** (per stack, on the Apps page) — the **only** place configuration changes: run
  params, config-file params and, for daemon clients, the daemon radio parameters
  ([stacks/daemon.md](stacks/daemon.md)). A save is validated as a whole and patches only its own
  keys; an unsupported file structure refuses the save and leaves the file byte-for-byte.
- **System panels** — Firewall, Webserver, GPS, Hardware, System dependencies, per-target logs.

Every mutating action needs an **explicit confirm**; TX-capable ones add an RF/dummy-load warning,
and Clean requires typing the stack id. Daemon live settings:
[daemon control](architecture.md#daemon-control).

**Start means start.** Start or Restart runs the **saved** configuration
([config layers](architecture.md#manifest-and-config-layers)). The click fixes the band (the Apps
dropdown, else the running band, else the primary), plans the run (hardware, band arbitration,
GPS, radio mode, firewall exposure, resource conflicts, identity), and then:

- **nothing consequential** → the start runs; you return with the result flashed;
- **a consequential choice** — another running stack owns the radio (*Stop owner(s) & start*) or
  a restart would take running dependents down (*Stop dependents & restart*) → a confirmation
  page with the plan and the consequence;
- **a missing or unusable identity** → nothing runs; the stack's Settings open with the offending
  row highlighted.

The CLI dry run (`lhpc stack start <id>`) refuses exactly what the web refuses, printing the
`lhpc config` remedy.

**A web Start/Restart is detached**: the page returns at once and the start runs as a tracked job,
log `logs/web-start-<stack>.log` (`web-restart-…`), reachable from the banner's *view →*. While it
runs, the stack's row and dashboard card show *starting…*; when it ends the page reloads once and
the banner turns green (result) or red (refusal, dismissed with ✕). An *unsafe* banner (the job
could not be tracked) needs *Recover*. A second Start while one runs is refused; a pending
self-update or contended admission refuses before anything is spawned. The CLI and boot restore
start synchronously.

**Maintenance pass.** While the console runs, it does the box's housekeeping every 60 s (300 s on
a box without the access point and with no web-server Apply or PKI normalisation pending): it
finishes an Apply the firewall gate deferred, rebuilds an expired client-CA CRL, normalises
certificates minted under an unverified clock, cuts the run logs and the controller's own logs
over their trigger and rolls the Meshtastic trace. Each task's last success and last failure (UTC
time and message) are kept in `state/maintenance.json`; `lhpc doctor` lists every task, and a task
whose last run failed is shown on the dashboard until a later run succeeds. No file yet (a box
just upgraded, or a console that has not run since) reads *never run*. **Log retention while the
console is stopped:** no log is cut on a schedule — a run log is cut only when its component
starts, the Meshtastic trace when it starts or its log page is read, and the controller's own logs
not at all, until the console's next pass. The trigger sizes: [maintenance](maintenance.md#running-on-a-pi).

**RF logs** — one page for every stack's RF log: [maintenance → RF logs](maintenance.md#rf-logs).

## Reboot / Shut down

The dashboard's system card ends with **Reboot…** / **Shut down…** (each behind a confirm page).
They act through logind (`systemctl reboot|poweroff`), a graceful teardown; running stacks come
back via boot restore. A button renders only when logind authorizes that action for the operator
(`CanReboot` / `CanPowerOff`, probed per button), because the rule file
`/etc/polkit-1/rules.d/49-lhpc-power.rules` is not readable by the operator on stock Debian.
Install it: [README step 4](../README.md#4-install-dependencies); on an existing box the
System-dependencies panel and `lhpc doctor` show the command, as does a refusal at apply time.
Apply records a short-lived pending marker that refuses new builds and updates until the trigger
fires (an unreadable or stale marker is named in the refusal; delete it yourself); failures after
authorization land only in `logs/power-<kind>.log`.

## Network

The **Network** panel: [wifi-access-point.md](wifi-access-point.md#the-network-panel). An uninstall (`uninstall.sh`,
with or without `--purge`) removes the preferred-network record (`state/network-preferred.json`);
declare the preferred WLAN again in the Network panel afterwards.

## Clock

A Pi has no battery-backed clock (only the Pi 5 has an RTC), so an offline box boots with the last
time it wrote, and every log line, certificate and receipt is wrong until something corrects it.
What the PKI does with an unverified clock: [the clock gate](webserver.md#the-clock-gate).

`bootstrap-deps.sh` installs, by default (`--no-time-source` skips it):

- **chrony** to discipline the clock. It **replaces systemd-timesyncd** (on Trixie both are
  `time-daemon` packages), which is why the opt-out exists.
- **gpsd** to feed it the receiver's time.
- **fake-hwclock**: saves the time hourly and at shutdown and restores it at boot, before chrony,
  so a box without NTP or GPS starts at most about an hour behind (plus the time it was off)
  instead of at the boot floor, where certificates and the CRL issued later read "not yet valid".
  `/etc/default/fake-hwclock` gets `FORCE=true`: the restore is **forward-only** and never steps a
  Pi 5's RTC time back.

**NTP wins when it is reachable.** Setup adds `prefer` to the NTP declarations in
`/etc/chrony/chrony.conf` and `/etc/chrony/sources.d/`, so any of them outranks GPS. GPS carries
the clock only when none is selectable — on an offline box it is then the only source, so its time
is accepted, not corroborated. This covers the declarations present when bootstrap ran; NTP servers
from DHCP (`/run/chrony-dhcp/`) and later additions take part in normal selection without that
guarantee. To make one authoritative, add `prefer` to it or re-run `bootstrap-deps.sh`.

The System panel's Time row shows only whether the clock is synchronised and its error bound;
the source in use:

```
chronyc sources        # every source, and which one is selected (^*)
chronyc tracking       # the selected source and the current error estimate
```

**GPS for position too:** the time source needs `[gps] source = gpsd`. With `source = nmea`, LHPC
reads the receiver directly, so bootstrap skips the time source rather than give gpsd the device
([gps](gps.md)).

**Boot floor.** `/usr/lib/clock-epoch` makes systemd advance a clock below that date at startup, so
a box that comes up in 1970 is plausible before the first certificate is written. systemd takes the
highest of its build time, this file and `/var/lib/systemd/timesync/clock` (written by timesyncd, so
stale under chrony). `/usr/lib/clock-epoch.ok` records that the **most recent** setup run
completed: removed before setup changes anything, written only if everything succeeded. The
Dependencies panel reads it, so after a failed run it keeps offering the repair command
(paste-lines there, or re-run `bootstrap-deps.sh`).

**Turning it off.** `--no-time-source` only skips future setup. To disable it on a box that has it:

```
sudo rm -f /etc/chrony/conf.d/10-lhpc-gps.conf   # the refclock
sudo systemctl disable --now gpsd                # stop gpsd owning the receiver
```

and remove `prefer` from the lines bootstrap added it to. A full rollback also removes the
packages and restores timesyncd:

```
sudo apt purge gpsd chrony fake-hwclock && sudo apt install systemd-timesyncd
sudo rm -f /usr/lib/clock-epoch /usr/lib/clock-epoch.ok /etc/default/fake-hwclock
```

## Identity drift on clean or uninstall

A destructive command re-proves the source's ownership record first and refuses on drift
([ownership records](provenance.md#ownership-records)). Inspect the checkout
(`git -C src/<name> log -1`, `git remote -v`); if it is yours to drop, remove it and its record by
hand (`rm -rf src/<name> state/source-registry/<name>-*.json`) and reinstall.
