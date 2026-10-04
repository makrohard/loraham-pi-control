# LHPC CLI reference

`lhpc` is the command-line interface to LoRaHAM Pi Control; the web console is a front end to
the same service layer ([architecture](architecture.md#package-layout)).

## Contents

- [Conventions](#conventions)
- [Commands](#commands)

## Conventions

- Plan-first commands (`bootstrap`, `install`, `auto-install`, `stack`, `build`, `test`, `update`,
  `uninstall`, `clean`, `daemon --set`) print a **dry-run plan** and apply only after a `[y/N]`
  confirmation, or at once with `--yes`.
- Setting commands (`config <stack> <param> <value>`, `hardware`, `gps`, `autostart on|off`,
  `firewall`, `webserver`, `known-working`) apply at once.
- Read-only commands (`list`, `status`, `explain`, `doctor`, `config <stack>`) change nothing;
  `source-check` writes only its cache, `state/stackupdates.json`.
- Exit codes: `0` success, `1` a command error (`ERR`), `2` a usage error.
- Help: `lhpc --help`, `lhpc <command> --help`, `lhpc help <topic>`.

## Commands

- [list](#list) · [status](#status) · [explain](#explain) · [doctor](#doctor) · [deps](#deps) · [source-check](#source-check)
- [bootstrap](#bootstrap) · [install](#install) · [auto-install](#auto-install)
- [config](#config) · [hardware](#hardware) · [gps](#gps) · [autostart](#autostart) · [firewall](#firewall) · [hmac](#hmac) · [meshtastic](#meshtastic)
- [stack](#stack) · [build](#build) · [test](#test) · [update](#update) · [uninstall](#uninstall) · [clean](#clean) · [known-working](#known-working)
- [daemon](#daemon) · [logs](#logs) · [rflog](#rflog)
- [web](#web) · [webserver](#webserver)
- [secrets](#secrets) · [self-update](#self-update) · [help](#help)

---

### list
`lhpc list` — the stacks defined in the manifest.

### status
`lhpc status [<stack>] [--versions]` — bounded, read-only stack/component status; `--versions`
shows source/pin status instead.

### explain
`lhpc explain <stack>` — a stack and its components (order, bands, ownership).

### doctor
`lhpc doctor` — bounded health checks; local except one bounded gpsd query when the position
source is `gpsd` ([gps](gps.md#health-and-what-the-console-shows)).

### deps
`lhpc deps [--script]` — list every declared system prerequisite (apt packages, the
SPI/`config.txt` overlay, `spi`/`gpio` group grants, disabling the OS-managed `meshtasticd`);
Python venv packages are `install.sh`'s. LHPC never installs system packages itself: it shows the
command for each missing one (web: the stack's **System dependencies** view, the **Checks** page).
`--script` prints them as one executable `bootstrap-deps.sh` (one non-interactive
`apt-get install -y --no-install-recommends` first, no third-party apt repository) that you run:

```
lhpc deps --script > bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run                      # pre-flight: simulate only, change nothing; no root
sudo bash bootstrap-deps.sh --spi-mode soft-cs        # or hardware-cs | skip; --operator-user <name> if root
sudo bash bootstrap-deps.sh --spi-mode soft-cs --with-gui   # ONLY on a machine with a display
```

`bootstrap-deps.sh` flags:

- `--dry-run` — simulate the default apt transaction; exit 0 only if it resolves cleanly and pulls
  nothing graphical, 5 if unresolved, 6 if it would install a GUI/display package or an audio
  server such as PulseAudio (ALSA is in the default set). Run it first on a fresh image.
- `--spi-mode soft-cs|hardware-cs|skip` (**required**) — `soft-cs`: software CS for LoRaHAM
  Pi/Uputronics rigs, single-radio and dual Uputronics (daemon + meshtasticd drive CS7/CS8 as
  GPIOs; the kernel must not claim CE0/CE1). `hardware-cs`: SPI on, no overlay, kernel-driven
  CE0/CE1 — only for boards that use them, not Uputronics. `skip`: no boot-config change.
  Idempotent; fails closed on a conflicting `config.txt`.
- `--operator-user <name>` — who gets the group grants when run as root directly (default
  `$SUDO_USER`, else the invoking user; never root).
- `--with-gui` — GUI application libraries only, never a desktop.
- `--with-gps` — compatibility only: gpsd comes with the default time source; installs nothing extra.
- `--no-time-source` — skip chrony/gpsd/fake-hwclock ([clock](operations.md#clock)).
- `--no-swapfile` · `--swap-size <MB>` (default 768) · `--keep-wifi-powersave`
  ([running on a Pi](maintenance.md#running-on-a-pi)).
- `--no-power-controls` · `--no-network-controls` — skip the polkit rule for Reboot/Shut down,
  for the Network panel.

Root is required for everything but `--dry-run` and `--help`. The package set is identical on a
Pi Zero 2W and a Pi 5; QEMU and PlatformIO come later with `lhpc build`. The shipped snapshot of
this script: [what CI enforces](maintenance.md#what-ci-enforces).

### source-check
`lhpc source-check [<target>]` — check managed sources for upstream updates (network;
refreshes only `state/stackupdates.json`).

---

### bootstrap
`lhpc bootstrap [--yes]` — create the runtime root and a starter config.

### install
`lhpc install [<stack>] [--check] [--source binary|pinned|dev|stable] [--accept-pin-mismatch] [--yes]`
— download the published **binary** artifact, or adopt/verify managed sources into the runtime
root. Channels and defaults: [provenance](provenance.md#selections); the binary channel in
operation: [operations](operations.md#install-channels).

- Without `--source`, a named stack takes its default channel; `lhpc install` without a stack uses
  `pinned`; `dev` is always explicit.
- A failed binary install asks whether to build from source; it never falls back silently.
- `--check` — dry run: the plan plus missing mandatory system dependencies (the apply refuses
  until they are installed). Never prompts or applies.
- `--accept-pin-mismatch` (binary channel; also on `update`) — install the published binary
  although it was built from other commits than this lhpc pins; exactly the pairs the plan shows
  ([provenance](provenance.md#the-binary-channel)).

### auto-install
`lhpc auto-install [--source binary|pinned|dev|stable] [--tests] [--tx] [--status]
[--recover [--confirm-orphan]] [--yes]` — install/update, build and test **all** stacks in one run.

- `--tests` runs host tests (off by default); `--tx` implies `--tests` and transmits one bounded
  frame per ready band ([TX safety](operations.md#tx-safety)).
- `--status` — print the run state and any recovery reason.
- `--recover` — acknowledge a crashed run and clear its leftover state. `--confirm-orphan` only
  when a spawned child's termination could not be proven (inspect and terminate it first).

---

### config
View or set per-stack settings and the global operator identity; values are validated before saving.

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

- `operator` is reserved (not a stack id); `--callsign` takes the **base** callsign only (no SSID,
  no `/P`). A per-stack value overrides it and may carry that stack's SSID or portable form:
  `lhpc config chat call YOURCALL-10` · `lhpc config voice callsign YOURCALL/P` ·
  `lhpc config meshcom mc_callsign YOURCALL-99`; Meshtastic: `lhpc config meshtastic node_name
  "Field Node"`, `node_short FN1`. Rules: [identity](architecture.md#identity-and-callsigns);
  syntax per field: [validators](adding-a-stack.md#parameters--config-files).
- A start without a required identity is refused by the dry run already, with a command template
  per missing field (replace the UPPERCASE token). `lhpc config` may clear an identity.
- A `<param>` shared by several components must be written `<component>.<param>`.
- One parameter per call: a setting validated across several parameters is set in the order its
  stack page gives.
- `--band` — the band, for band-switchable stacks.

### hardware
Show or set the **radio hardware setup**: which band(s) are served and the daemon `--hw` preset
each radio launches with. A fresh install is **not configured** ([daemon](stacks/daemon.md#settings)).

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
lhpc hardware --high-power 433 on|off  # allow POWER=20 on an SX127x band; takes effect at that band's next daemon start
```

Web: the loraham daemon stack's **Hardware** settings, which add a **Detect** probe (starts the
daemon briefly per candidate board and reports whether the chip responds; the board's LED lights
during init). Detect is refused, naming the stack, while a stack that drives the radio itself
(Meshtastic, Reticulum) runs on that band.

---

### gps
Show or set the **position source shared by every stack** ([GPS](gps.md)).

```
lhpc gps                                        # show the current source (and what `auto` resolved to)
lhpc gps --source auto                          # gpsd on this box if one runs, else no position (default)
lhpc gps --source off                           # no position, explicitly
lhpc gps --source gpsd                          # gpsd on this box (127.0.0.1:2947)
lhpc gps --source gpsd --host 192.168.1.5       # gpsd on ANOTHER box
lhpc gps --source nmea --device /dev/ttyACM0    # a serial/USB GPS directly, no gpsd
lhpc gps --source nmea --device /dev/ttyACM0 --baud 9600
lhpc gps --source fixed --lat 51.4779 --lon -0.0015 --alt 45   # a station that does not move
lhpc gps --monitor                              # live receiver state (read-only; coordinates on this terminal only)
lhpc gps --monitor --sats                       # ... plus the satellite table
```

`--port` sets the gpsd port (default 2947). `--monitor` takes no setting flag
([GPS → Monitor](gps.md#monitor)).

---

### autostart
**Boot auto-restore** (default **on**; also Home → System → Autostart). What it restores and
refuses: [operations](operations.md#not-a-supervisor).

```
lhpc autostart               # show the switch + the last boot-restore result
lhpc autostart off           # disable (applies at the NEXT boot)
lhpc autostart on            # re-enable (the default)
```

---

### firewall
Managed **nftables firewall**: `lhpc` renders the ruleset, you apply it with one sudo command.
Model, modes and the Config/Boot/Live dimensions: [firewall](firewall.md). Web: the controller
row's **Firewall** panel on the Apps page.

```
lhpc firewall                 # status: mode + Config/Boot/Live dimensions + foreign-table note
lhpc firewall --script        # print the apply script (run it yourself with sudo)
lhpc firewall --reset-script  # print the reset script (removes only lhpc-owned artifacts)

# policy (omitted flag = unchanged)
lhpc firewall --mode secure-default|compatibility
lhpc firewall --ap on --ap-interface wlan0 --ap-cidr 10.42.0.0/24   # AP DHCP/DNS rules
lhpc firewall --ssh-ports "22,2222"        # "" = back to automatic detection
lhpc firewall --allow-endpoints "id1,id2"  # "" = no direct-access exceptions
lhpc firewall --recommended                # safe preset; not combinable with the flags above
```

---

### stack
`lhpc stack {start|stop|restart} <stack> [--yes]` — start, stop or restart a stack or component.
`start --band 433|868` starts a band-switchable stack on that band (default: its saved band); a
band the hardware or the stack cannot serve is refused before anything starts. When another
running stack holds what the start needs, `start` asks, as the console's confirm page does,
whether to stop it and start. `--yes` answers yes: `lhpc stack start <stack> --yes` stops the
holders and starts, also from a script. A no, or no terminal to ask on, refuses with nothing
stopped.

`lhpc stack poststart <stack> [--yes]` — re-run a RUNNING stack's post-start steps without
restarting it (any live retry runner is cancelled first). Use it when `lhpc status <stack>` shows
"post-start: … NOT applied", e.g. a MeshCom callsign push that outlived its retry window after a
slow QEMU boot.

### build
`lhpc build <target> [--yes]` — build a stack/component.

### test
`lhpc test <target> [--tx] [--yes]` — run host tests, or a bounded TX test with `--tx`
([TX safety](operations.md#tx-safety)). A component's own upstream suite (openHop Core has one)
is a host test too; it runs against the pinned upstream in the built environment
([policy](maintenance.md#running-on-a-pi)).

### update
`lhpc update [<target>] [--source binary|pinned|dev|stable] [--accept-pin-mismatch] [--upstream] [--yes]`
— update a stack/component. Which command for which situation:
[keeping stacks current](operations.md#keeping-stacks-current).

- When the published binary lags this lhpc's pins, the update refuses and names the ways forward
  (self-update, a source build, `--accept-pin-mismatch` where allowed); cancelling keeps the
  working binary.
- Switching channels is an `install`, and the CLI says so.
- `--upstream` (fetched packages, i.e. graywolf) — the latest upstream release, verified against
  its `checksums.txt`.

### uninstall
`lhpc uninstall [<target>] [--yes]` — uninstall a stack/component; settings, state and identities stay.

### clean
`lhpc clean <target> --purge [--yes]` — **destructive** full wipe of one stack: sources, config,
generated config files, saved state (`state_root`), its own secrets (`secret_files`: node
identities, keys, passwords LHPC minted), logs and history. `--purge` is required; the stack must
be stopped. A reinstall is a new node; peers that knew the old one must forget it.
`config/local.toml`, `config/secrets.toml`, undeclared files (chat's `lorachat.log`) and other
stacks stay.

### known-working
`lhpc known-working <stack>` — record a running stack's current commits as a known-good composition.

---

### daemon
`lhpc daemon <band> [--set KEY=VALUE] [--feed] [--yes]` — monitor a daemon band (433/868), apply a
live CONF setting (`--set TXMODE=DIRECT`), or show recent RX/TX activity (`--feed`). Persisted
daemon params: [`config`](#config).

### logs
`lhpc logs <target> [--lines N]` — bounded tail of a component's log (default 200 lines). The
older part of a capped run log: `logs/start-<comp>[-<band>].prev.log`
([maintenance](maintenance.md#running-on-a-pi), "Run-log cap").

### rflog
`lhpc rflog <stack> [--band 433|868] [--lines N] [--clear] [--decrypt [--follow]]` ·
`lhpc rflog --all on|off` · `lhpc rflog --clear-all` — a stack's RF log (default 300 lines).
Model, retention and keys: [maintenance → RF logs](maintenance.md#rf-logs).

- `--band` — `daemon` only (one file per band); required there.
- `graywolf` shows the kiss TNC's log. Switch: `lhpc config <stack> rf_log on|off` (graywolf:
  `lhpc config kiss rf_log off`).
- `--clear` — empty the file in place and remove its previous segment.
- `--all on|off` / `--clear-all` — every stack's switch / every RF log; no stack, no `--band`, one
  at a time.
- `--decrypt` (meshtastic, meshcore, reticulum) — print the tail decoded with this box's keys, to
  the terminal only; `--follow` keeps printing new frames every 2 s until Ctrl-C. Exit 2 on a
  plaintext stack, 1 when the decoder cannot run.

---

### meshtastic
`lhpc meshtastic <upstream args>` — guarded passthrough to the managed Meshtastic CLI against this
box's node; `--yes` skips the factory-reset confirmation. What is refused and re-asserted:
[Meshtastic → Command line](stacks/meshtastic.md#command-line-lhpc-meshtastic).

---

### web
`lhpc web [--host H] [--port P] [--socket]` — start the local operator web console (default
`127.0.0.1:8770`, loopback only). `--socket` serves on the protected Unix socket behind nginx
(production).

### webserver
Production webserver (HTTPS / mTLS) control: [webserver](webserver.md). Access modes:
`local-open-remote-auth | auth-everywhere | no-auth`.

```
lhpc webserver status                  # cached status (read-only)
lhpc webserver verify                  # verify effective state + persist evidence
lhpc webserver apply                   # validate + activate (reload) the current config
lhpc webserver start-service           # operator context: generate config + enable/start nginx
lhpc webserver init [--dns D ...] [--ip I ...] [--confirm-recreate]   # bootstrap PKI (CAs + server cert + CRL)
lhpc webserver configure [--bind B] [--port P] [--access-mode M] [--dns D ...] [--ip I ...]
lhpc webserver expose [--cidr C ...] [--access-mode M] [--confirm-phrase P] [--replace-certificate]   # remote exposure (opt-in)
lhpc webserver proxy <page> [--mode local|lan|public] [--port P] [--scheme https|http] [--access-mode M] [--cidr C ...] [--confirm-phrase P]
#   <page> = the stack id (its first web UI), or <stack>-<component> for a stack's further web UIs
# --auth is an alias for --access-mode on configure / expose / proxy
lhpc webserver disable-remote          # bind back to loopback
lhpc webserver reset-defaults          # reset desired config to safe defaults
lhpc webserver tls-renew               # renew the HTTPS server certificate
lhpc webserver logs [--access] [--lines N]   # error log by default, 300 lines
lhpc webserver cert list
lhpc webserver cert issue <label>      # issue a cert + one-time .p12 passphrase (shown once)
lhpc webserver cert reissue <label>    # rotate a cert + new one-time passphrase
lhpc webserver cert export <label> <path> [--force]   # write the .p12 to a file (mode 0600; no overwrite without --force)
lhpc webserver cert revoke <label> --confirm-label <label>
lhpc webserver cert discard-export <label>
```

- `configure`/`expose`/`proxy` write intent only; `apply` activates it
  ([applying changes](webserver.md#applying-changes-and-recovery)).
- `expose`, and `proxy` in `lan`/`public` mode, need `--confirm-phrase` ([access modes](webserver.md#access-modes),
  [proxies](webserver.md#stack-web-ui-proxies)); `proxy --port 0` = not proxied (omitted: the saved port is kept).
- `expose --replace-certificate` — consent, without a prompt, to replacing the server certificate
  when it does not name this host's LAN address; off a terminal `expose` otherwise writes nothing.
- `--accept-unverified-clock` (`tls-renew`, `expose`, `cert issue|reissue|revoke`) — proceed on an
  unverified clock, this call only ([the clock gate](webserver.md#the-clock-gate)).

---

### secrets
`lhpc secrets backup [<file>]` · `lhpc secrets restore <file> [--only pki] [--yes | --overwrite]` —
one file with the box's certificates, secrets and stack identities. What it holds and how a
restore behaves: [operations → backup](operations.md#backup--restore).

- `backup` — default file `~/lhpc-secrets-<host>-<UTC time>.tar`.
- `restore` without a flag prints the plan and changes nothing; `--yes` applies when no target
  exists; `--overwrite` replaces existing targets; `--only pki` restores just the two CAs.

### self-update
`lhpc self-update [--apply] [--overwrite] [--repair-integration] [--recover-request] [--yes]` —
check for, or apply, lhpc's own update ([deployment → self-update](deployment.md#self-update)).

- `--apply` — fast-forward and restart the console.
- `--overwrite` — reset a diverged or dirty checkout to upstream.
- `--repair-integration` — reinstall the managed console and updater units.
- `--recover-request` — clear a stuck one-click request/in-flight record
  ([recovery](deployment.md#recovery)).

### hmac
`lhpc hmac status|enable|disable|renew|abort|recover [<stack>] [--confirm-phrase P] [--yes]` — the
MeshCom HMAC password between bridge and firmware (default stack: meshcom;
[meshcom](stacks/meshcom.md#settings)).

- `enable`/`disable`/`renew` rebuild the firmware and restart the link (minutes). Without `--yes`
  they warn and print the confirm hint; with it they stream each step. The secret is never printed.
- `disable` also needs `--confirm-phrase remove-auth`.
- On the **binary** channel every change is refused until you install from source.
- `abort` — cancel a running apply. `recover` — clear a blocking `unsafe` state left when a
  cancelled build could not be proven stopped: automatically once the session is proven gone, else
  as your acknowledgement after inspecting `ps`.

### _gps-bridge
Internal — `lhpc _gps-bridge <meshtastic|meshcom|meshcore>`: started by the lifecycle to feed one
consumer the global position source (NMEA on a PTY for Meshtastic, NMEA into QEMU's UART socket for
MeshCom, line-JSON on a socket for MeshCore).

### _network-finalize
Internal — `lhpc _network-finalize --uuid <uuid> --op-id <token> [--pwfile <path>] [--allow-console] [--delay <s>]`:
the Network panel's detached join helper; outcome in `state/network-outcome.json`.

### _hmac-apply
Internal — `lhpc _hmac-apply <stack> <enable|disable|renew> <run_id>`: the detached HMAC apply runner.

### _stack-start
Internal — `lhpc _stack-start <target> --web-result web-<start|restart>-<target>.log --attempt-id <hex> [--band B] [--stop-owners] [--cascade] [--restart]`:
the detached runner behind a web Start/Restart
([operations](operations.md#operating-the-console)).

### _controller-uninstall-prep
Internal — `lhpc _controller-uninstall-prep [--root <dir>]`: `uninstall.sh`'s quiescence gate; exit
0 = safe to remove ([operations → backup](operations.md#backup--restore)).

### _uninstall-guard-claim
Internal — `lhpc _uninstall-guard-claim [--root <dir>] --pid <pid> --nonce <n> --start <starttime>`:
claims the `.lhpc-uninstalling` guard for `uninstall.sh` (a live owner's guard is refused, a dead
owner's reclaimed).

### _uninstall-guard-release
Internal — `lhpc _uninstall-guard-release [--root <dir>] --nonce <n>`: removes the guard only if
its nonce matches.

### help
`lhpc help [<topic>]` — detailed help: `safety`, `resources`, `profiles`.
