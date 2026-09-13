# LHPC CLI reference

`lhpc` is the command-line interface to LoRaHAM Pi Control; the web console is a front end to
the same service layer ([architecture](architecture.md#package-layout)).

## Contents

- [Conventions](#conventions)
- [Commands](#commands)

## Conventions

- Mutating commands (`install`, `stack start`, `build`, `test`, `update`, …) print a
  **dry-run plan** first and apply only after a `[y/N]` confirmation, or immediately with `--yes`.
- Read-only commands (`list`, `status`, `explain`, `doctor`, `source-check`, `config <stack>`) never change anything.
- Exit codes: `0` success, `1` a command error (`ERR`), `2` a usage error.
- Layered help: `lhpc --help`, `lhpc <command> --help`, `lhpc help <topic>`.

## Commands

- [list](#list) · [status](#status) · [explain](#explain) · [doctor](#doctor) · [deps](#deps) · [source-check](#source-check)
- [bootstrap](#bootstrap) · [install](#install) · [auto-install](#auto-install)
- [config](#config) · [hardware](#hardware) · [gps](#gps) · [autostart](#autostart) · [firewall](#firewall) · [hmac](#hmac) · [meshtastic](#meshtastic)
- [stack](#stack) · [build](#build) · [test](#test) · [update](#update) · [uninstall](#uninstall) · [clean](#clean) · [known-working](#known-working)
- [daemon](#daemon) · [logs](#logs) · [rflog](#rflog)
- [web](#web) · [webserver](#webserver)
- [self-update](#self-update) · [help](#help)

---

### list
`lhpc list` — list the stacks defined in the manifest.

### status
`lhpc status [<stack>] [--versions]` — bounded, read-only stack/component status. `--versions` shows source/pin status instead.

### explain
`lhpc explain <stack>` — explain a stack and its components (order, bands, ownership).

### doctor
`lhpc doctor` — bounded health checks. Local except for one bounded query to gpsd when the
position source is `gpsd` ([gps](gps.md#health-and-what-the-console-shows)).

### deps
`lhpc deps` — list every declared system prerequisite (apt packages, the SPI/`config.txt` overlay,
`spi`/`gpio` group grants, and disabling the OS-managed `meshtasticd`). These are the sudo/apt-level
prerequisites only; the Python venv is provisioned by `install.sh` after cloning, so venv `pip
install` steps are deliberately excluded. LHPC never installs system packages itself — it shows the
exact copyable command for each missing one (the per-stack **System dependencies** view and the
**Checks** page in the web console).

`lhpc deps --script` renders them into ONE hardened, executable bootstrap script (standalone
`apt install` lines merged into a single non-interactive
`apt-get install -y --no-install-recommends` that runs FIRST, SPI/group sections re-rendered as
validated operator-safe logic). No third-party apt repository is configured — `meshtasticd` is built
from a pinned upstream checkout. You run the script yourself:

```
lhpc deps --script > bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run                      # PRE-FLIGHT: simulate only, change nothing; no root
sudo bash bootstrap-deps.sh --spi-mode soft-cs        # or hardware-cs | skip; --operator-user <name> if root
sudo bash bootstrap-deps.sh --spi-mode soft-cs --with-gui   # ONLY on a machine with a display
```

`--dry-run` simulates the exact default apt transaction and exits 0 only when it resolves cleanly and
pulls nothing graphical; it exits nonzero when the set is unresolved (5) or would install a
GUI/display package or an audio server such as PulseAudio (6; ALSA is part of the default set). Run it first on a fresh image.

`--spi-mode` is **required**: `soft-cs` (software CS — LoRaHAM Pi/Uputronics rigs, single-radio AND
dual Uputronics: daemon + meshtasticd drive CS7/CS8 as GPIOs, the kernel must not claim CE0/CE1),
`hardware-cs` (SPI on, no overlay — kernel-driven CE0/CE1, only for boards that really use them;
NOT for Uputronics), or `skip`. It is
idempotent and fails closed on a conflicting existing `config.txt`. Group grants go to the resolved
operator (`--operator-user`, else `$SUDO_USER`, else the invoking user) — never root. QEMU + PlatformIO
are provisioned later by `lhpc build`, not by this script.

Optional flags: `--with-gui` (GUI application libraries only) · `--with-gps` (gpsd, for a
receiver on this box) · `--no-swapfile` · `--swap-size <MB>` (default 768) · `--operator-user
<name>` (when running as root directly rather than through `sudo`) · `--keep-wifi-powersave` ·
`--no-power-controls` · `--no-network-controls` (skip the polkit rule for Reboot/Shut down, for
the Network panel). Root is required for everything but `--dry-run` and `--help`. What the
swapfile and the Wi-Fi power-save flags do: [running on a Pi](maintenance.md#running-on-a-pi).

The apt package set is identical on a Pi Zero 2W and a Pi 5. The shipped snapshot of this
script: [what CI enforces](maintenance.md#what-ci-enforces).

### source-check
`lhpc source-check [<target>]` — check managed sources for available upstream updates (read-only).

---

### bootstrap
`lhpc bootstrap [--yes]` — create the runtime root and a starter config.

### install
`lhpc install [<stack>] [--check] [--source binary|pinned|dev|stable] [--yes]` — install a stack:
download the published **binary** artifact, or adopt/verify managed sources into the runtime root.

- Without `--source`, a stack uses its default channel ([selections](provenance.md#selections));
  the all-stacks form stays on that source channel. `dev` is an explicit choice.
- A failed binary install asks **explicitly** whether to build from source; it never falls back
  silently.
- `--check` is a dry run: it shows the plan and reports missing mandatory system dependencies
  (the apply run refuses until they are installed).

### auto-install
`lhpc auto-install [--source binary|pinned|dev|stable] [--tests] [--tx] [--status]
[--recover [--confirm-orphan]] [--yes]` — install/update, build and test **all** stacks in one
guided run.

- Host tests are **off by default**; `--tests` runs them, and `--tx` implies `--tests` and
  transmits one bounded frame per ready band ([TX safety](operations.md#tx-safety)).
- `--status` prints the run state and any recovery reason, then exits.
- `--recover` acknowledges a crashed run and clears its leftover state so a new run can start.
  Add `--confirm-orphan` only when a spawned child's termination could not be proven (inspect and
  terminate it first).

---

### config
View or set per-stack settings and the global operator identity. Values are validated before saving.

```
lhpc config <stack>                    # list settable params (current value, default, * = identity/callsign)
lhpc config <stack> <param>            # show one parameter
lhpc config <stack> <param> <value>    # set + validate one parameter
lhpc config <stack> --reset [--yes]    # reset this stack's settings to defaults
lhpc config <stack> --daemon-param KEY=VALUE   # persist a band-scoped daemon param (repeatable)
lhpc config <stack> --apply-daemon     # apply saved daemon params to the running daemon
lhpc config <stack> --reset-daemon     # reset daemon params
lhpc config operator [--callsign CALL]   # show / set the GLOBAL operator identity
```

- `operator` is a reserved subcommand (not a stack id). `--callsign` applies only to it and
  takes the **base** callsign only (no SSID, no `/P`). A per-stack value overrides it and may
  carry that stack's SSID or portable form: `lhpc config chat call YOURCALL-10` ·
  `lhpc config voice callsign YOURCALL/P` · `lhpc config meshcom mc_callsign YOURCALL-99`
  (`YOURCALL` = your own callsign). Meshtastic's node names: `lhpc config meshtastic node_name
  "Field Node"` + `node_short FN1`. The rules — inheritance, placeholders, what is checked and
  what is not: [identity](architecture.md#identity-and-callsigns); the accepted syntax per
  field: [validators](adding-a-stack.md#parameters--config-files).
- A start without a required identity is refused — by the dry run already, before anything is
  queued or stopped — and prints a command template for every missing field (replace the
  UPPERCASE token with your value). `lhpc config` (like the Settings page) may CLEAR an identity.
- A `<param>` name shared by several components must be qualified as `<component>.<param>` — the command refuses rather than guessing.
- `lhpc config` sets one parameter per call; the stack's Settings page saves the whole form in
  one submission, so a setting whose validation spans several parameters is set here in the
  order its stack page gives.
- `--band` selects the band for band-switchable stacks.

Example: `lhpc config chat call YOURCALL-10` (`YOURCALL-10` = your callsign+SSID) then `lhpc stack start chat`.

### hardware
Show or set the **radio hardware setup** — which physical board(s) this box has. This fixes which
band(s) are served and the daemon `--hw` preset each radio launches with. A fresh install is **not
configured** ([daemon](stacks/daemon.md#settings)).

```
lhpc hardware                # show the current setup + served band(s) + the catalog
lhpc hardware unset          # back to 'not configured' (the daemon refuses to start)
lhpc hardware loraham        # LoRaHAM dual-module (SX1278 + RFM95) — serves 433 + 868
lhpc hardware uputronics     # Uputronics dual (CE0 433 + CE1 868)
lhpc hardware uputronics-x   # Uputronics dual, crossed modules (CE0 868 + CE1 433)
lhpc hardware uputronics-433 # Uputronics 433 only (CE0)
lhpc hardware uputronics-868 # Uputronics 868 only (CE1)
lhpc hardware waveshare-433  # Waveshare SX1262 (433)
lhpc hardware waveshare-868  # Waveshare SX1262 (868)
```

- Which combinations are offered and what a single-radio setup blocks: [daemon](stacks/daemon.md#settings).
- Also settable in the web console under the loraham daemon stack's **Hardware** settings section,
  which additionally offers a **Detect** probe (spawns the daemon briefly per candidate board and
  reports whether the chip responds — the board's LED lights during init).

---

### gps
Show or set the **position source shared by every stack** — a global controller setting, like
`hardware`, not a per-stack parameter.

```
lhpc gps                                        # show the current source (and what `auto` resolved to)
lhpc gps --source auto                          # gpsd on this box if one runs, else no position (default)
lhpc gps --source off                           # no position, explicitly
lhpc gps --source gpsd                          # gpsd on this box (127.0.0.1:2947)
lhpc gps --source gpsd --host 192.168.1.5       # gpsd on ANOTHER box
lhpc gps --source nmea --device /dev/ttyACM0    # a serial/USB GPS directly, no gpsd
lhpc gps --source nmea --device /dev/ttyACM0 --baud 9600
lhpc gps --source fixed --lat 51.4779 --lon -0.0015 --alt 45   # a station that does not move
```

The model, the refusals and the per-stack `use_gps` switch are in [GPS](gps.md).

---

### autostart
**Boot auto-restore** — restart the stacks that were running before a reboot (default: **on**).
What it restores, what it refuses and where its log is: [operations](operations.md#not-a-supervisor).

```
lhpc autostart               # show the switch + the last boot-restore result
lhpc autostart off           # disable (applies at the NEXT boot)
lhpc autostart on            # re-enable (the default)
```

Also switchable in the web console (Home → System → Autostart).

---

### firewall
Managed **nftables firewall** status and script rendering. `lhpc` renders the ruleset; you apply
it with one sudo command. See [Firewalling the Pi](firewall.md) for the full model (modes, the
three status dimensions, and how your existing configuration is preserved).

```
lhpc firewall                 # status: mode + Config/Boot/Live dimensions + foreign-table note
lhpc firewall --script        # print the apply script (run it yourself with sudo)
lhpc firewall --reset-script  # print the reset script (removes only lhpc-owned artifacts)

# policy (same fields as the console's Firewall panel; omitted flag = unchanged)
lhpc firewall --mode secure-default|compatibility
lhpc firewall --ap on --ap-interface wlan0 --ap-cidr 10.42.0.0/24   # AP DHCP/DNS rules
lhpc firewall --ssh-ports "22,2222"        # "" = back to automatic detection
lhpc firewall --allow-endpoints "id1,id2"  # "" = no direct-access exceptions
lhpc firewall --recommended                # safe preset; not combinable with the flags above
```

- **Config/Boot/Live**: [the three status dimensions](firewall.md#the-three-status-dimensions-and-why-green-is-strict).
- Also configurable in the web console: the controller row's **Firewall** panel on the Apps
  page (mode, per-listener
  direct-access exceptions, AP controls, and the copyable apply/check/reset commands).

---

### stack
`lhpc stack {start|stop|restart} <stack> [--yes]` — start, stop or restart a stack or component.

`lhpc stack poststart <stack> [--yes]` — re-run a RUNNING stack's post-start steps **without**
restarting it, with the same readiness-gated senders the start uses (any live retry runner is
cancelled first). Use it when a post-start setting did not land — e.g. the MeshCom callsign push
after a slow QEMU cold boot outlived its retry window (`lhpc status <stack>` shows
"post-start: … NOT applied"); a restart would cost another multi-minute QEMU boot.

### build
`lhpc build <target> [--yes]` — build a stack/component.

### test
`lhpc test <target> [--tx] [--yes]` — run host tests, or a bounded TX test with `--tx`
([TX safety](operations.md#tx-safety)).

An upstream's own suite, where a component declares one (openHop Core does), is a host test like
any other: `lhpc test <component>`, the button on the stack's install section, or the tests
checkbox in auto-install. It runs in the environment the build created, against the pinned
upstream that box installed, so it tells the operator whether the pinned upstream itself works on
that hardware ([policy](maintenance.md#running-on-a-pi)).

### update
`lhpc update [<target>] [--source binary|pinned|dev|stable] [--upstream] [--yes]` — update a stack/component to
the selected source.

- Without `--source` the target KEEPS its current channel: a binary-installed stack updates
  binary→binary, everything else defaults to `dev`.
- When the published binary lags this lhpc's pins, the update refuses and names the source build as
  the only way forward — cancelling keeps the working binary.
- Switching channels is an `install`, not an update, and the CLI says so.
- `--upstream` (fetched packages, i.e. graywolf): move to the latest upstream release, verified
  against its `checksums.txt`.

### uninstall
`lhpc uninstall [<target>] [--yes]` — uninstall a stack/component.

### clean
`lhpc clean <target> --purge [--yes]` — **destructive**: purge a stack's sources, config, logs and history. `--purge` is required.

### known-working
`lhpc known-working <stack>` — record a running stack's current commits as a known-good composition.

---

### daemon
`lhpc daemon <band> [--set KEY=VALUE] [--feed] [--yes]` — monitor a daemon band (433/868), apply a live CONF setting (e.g. `--set TXMODE=DIRECT`), or show recent RX/TX activity (`--feed`).
(Persisted, band-scoped daemon params live under [`config`](#config).)

### logs
`lhpc logs <target> [--lines N]` — bounded tail of a component's log.

### rflog
`lhpc rflog <stack> [--band 433|868] [--lines N] [--clear] [--decrypt [--follow]]` — a stack's
RF log: what its radio heard and sent, one line per frame, kept across restarts. `daemon` needs
`--band` (one file per band); no other stack takes one. `graywolf` shows the kiss TNC's log — its
switch is `lhpc config kiss rf_log off`; every other stack's is `lhpc config <stack> rf_log
on|off`. `--clear` empties the file in place and removes its previous segment. `--decrypt`
(meshtastic, meshcore, reticulum only — the others are plaintext already) prints the tail decoded
with the keys on this box, one frame per line: the time, direction and signal, then the kind, the
peer and the text, or a `[no-key …]` / `[undecryptable …]` / `[malformed …]` tag. `--follow`
keeps printing new frames every 2 s until Ctrl-C. Output goes to the terminal only — nothing is
written; piping it is the operator's choice. Exit 2 on a plaintext stack, 1 when the decoder
cannot run (the stack is not built, a key store is unreadable). The switch, retention and which
keys open what: [maintenance → RF logs](maintenance.md#rf-logs).

---

### meshtastic
`lhpc meshtastic <upstream args>` — a thin **guarded passthrough** to the LHPC-managed Meshtastic CLI, always targeting this box's local node. Every upstream argument works as usual (`lhpc meshtastic --help` shows the full upstream reference); only what LHPC owns is guarded: connection/transport selectors (`--host`/`--tcp`/`--serial`/`--ble`/…) are refused, LHPC-owned local settings (LoRa region, owner name/short incl. `--set-ham`, GPS mode, fixed position) are refused with a pointer to the right command, and factory-reset asks for confirmation (`--yes` skips it). Broad config imports (`--configure`/`--import-config`/`--seturl`/`--ch-set-url`/`--ch-add-url`) run, then LHPC auto-reasserts region/name/GPS via post-start convergence. Targeting a remote node with `--dest` is unrestricted. Node ops need the stack running; `--help`/`--version`/`--support`/`--test` do not. See [Meshtastic → Command line](stacks/meshtastic.md#command-line-lhpc-meshtastic).

---

### web
`lhpc web [--host H] [--port P] [--socket]` — start the local operator web console. `--socket` serves on the protected Unix socket behind nginx (production).

### webserver
Production webserver (HTTPS / mTLS) control. Access modes: `local-open-remote-auth | auth-everywhere | no-auth`.

```
lhpc webserver status                  # cached status (read-only)
lhpc webserver verify                  # verify effective state + persist evidence
lhpc webserver apply                   # validate + activate (reload) the current config
lhpc webserver start-service           # operator context: generate config + enable/start nginx
lhpc webserver init [--dns D ...] [--ip I ...] [--confirm-recreate]   # bootstrap PKI (CAs + server cert + CRL)
lhpc webserver configure [--bind B] [--port P] [--access-mode M] [--dns D ...] [--ip I ...]
lhpc webserver expose [--cidr C ...] [--access-mode M] [--confirm-phrase P]   # remote exposure (opt-in)
lhpc webserver proxy <page> [--mode local|lan|public] [--port P] [--scheme https|http] [--access-mode M] [--cidr C ...] [--confirm-phrase P]
#   <page> = the stack id (its first web UI), or <stack>-<component> for a stack's further web UIs
# --auth is an alias for --access-mode on configure / expose / proxy
lhpc webserver disable-remote          # bind back to loopback
lhpc webserver reset-defaults          # reset desired config to safe defaults
lhpc webserver tls-renew               # renew the HTTPS server certificate
lhpc webserver logs [--access] [--lines N]
lhpc webserver cert list
lhpc webserver cert issue <label>      # issue a cert + one-time .p12 passphrase (shown once)
lhpc webserver cert reissue <label>    # rotate a cert + new one-time passphrase
lhpc webserver cert export <label> <path> [--force]   # write the .p12 to a file (mode 0600; no overwrite without --force)
lhpc webserver cert revoke <label> --confirm-label <label>
lhpc webserver cert discard-export <label>
```

- `--port` on `proxy` is optional; `0` or absent = not proxied.
- `expose` and `proxy` increase exposure and need a confirm phrase — the same escalation rules as the web UI ([access modes](webserver.md#access-modes)).
- `configure`/`expose`/`proxy` write **intent** only — run `lhpc webserver apply` to activate.

---

### self-update
`lhpc self-update [--apply] [--overwrite] [--repair-integration] [--recover-request] [--yes]` — check for, or apply, lhpc's own update. `--apply` fast-forwards and restarts the console; `--overwrite` resets a diverged/dirty checkout; `--repair-integration` reinstalls the managed console + updater units.

### hmac
`lhpc hmac status|enable|disable|renew|abort|recover [<stack>] [--yes]` — the MeshCom HMAC
password between bridge and firmware (default stack: meshcom).

- `enable`/`disable`/`renew` **rebuild the firmware and restart the link** (several minutes).
  Without `--yes` they warn and print the confirm hint; with `--yes` they stream each step
  (secret → firmware → bridge → node). The secret value is never printed.
- `disable` also requires `--confirm-phrase remove-auth` — it downgrades the link to
  unauthenticated.
- Password auth is on by default for a **source** install; on the **binary** channel every
  change here is refused until you install from source ([meshcom](stacks/meshcom.md)).
- `abort` cancels a running apply; `recover` clears a blocking `unsafe` state left when a
  cancelled build could not be proven stopped — automatically once the session is proven gone, or
  as your explicit acknowledgement after inspecting `ps`.

### _gps-bridge
Internal service — `lhpc _gps-bridge <meshtastic|meshcom>` — started by the lifecycle when the global position source needs to be presented as a device. Publishes NMEA on a PTY (Meshtastic) or a UNIX socket (MeshCom) under `state/gps/<consumer>/`, with a readiness marker driven by the upstream source. One instance per consumer. Not for direct use.

### _network-finalize
Internal driver — `lhpc _network-finalize --uuid <uuid> --op-id <token> [--pwfile <path>] [--allow-console] [--delay <s>]` — spawned detached by the web Network panel's connect flow: activates the Wi-Fi profile (secrets via a 0600 passwd-file, unlinked after activation), waits for the lease, and extends the console allowlist for the joined subnet when asked. Only the helper carrying the pending record's own op-id token may run; the outcome lands in `state/network-outcome.json`. Not for direct use.

### _hmac-apply
Internal driver — `lhpc _hmac-apply <stack> <enable|disable|renew> <run_id>` — spawned detached by the web/CLI apply flow to run the steps against a run marker + log. Not for direct use.

### _stack-start
Internal detached runner — `lhpc _stack-start <target> --web-result web-<start|restart>-<target>.log --attempt-id <hex> [--band B] [--stop-owners] [--cascade] [--restart]` — spawned by the web console's Start/Restart (`spawn_start_job`). It proves the parent identity-tracked this exact attempt (`webjob_gate`), then runs the ordinary locked `start`/`restart` with a hook that marks the attempt admitted under every lock and before the first mutation (for a restart: before the stop); the result — summary and start notes — lands in the attempt marker the task banner shows. A superseded attempt cancels with zero side effects. Not for direct use.

### _controller-uninstall-prep
Internal quiescence gate — `lhpc _controller-uninstall-prep [--root <dir>]` — invoked by `uninstall.sh` before it removes any controller state. Refuses on active/unprovable build/test/web jobs, unresolved auto-install/HMAC state, or any UNKNOWN component state; otherwise stops the managed stacks (clients before the shared daemon) and verifies cessation. Exit 0 = safe to remove; nonzero = abort teardown. Not for direct use.

### _uninstall-guard-claim
Internal atomic guard claim — `lhpc _uninstall-guard-claim [--root <dir>] --pid <pid> --nonce <n> --start <starttime>` — invoked by `uninstall.sh` to claim the `.lhpc-uninstalling` guard `O_CREAT|O_EXCL|O_NOFOLLOW` (never truncating/following/replacing a pre-existing guard). A live-owner guard is refused; a stale (dead-owner) guard is reclaimed. Not for direct use.

### _uninstall-guard-release
Internal owned-only guard release — `lhpc _uninstall-guard-release [--root <dir>] --nonce <n>` — removes the `.lhpc-uninstalling` guard ONLY if its recorded nonce matches (a foreign/unreadable guard is left in place). Not for direct use.

### help
`lhpc help [<topic>]` — detailed help on a topic: `safety`, `resources`, `profiles`.
