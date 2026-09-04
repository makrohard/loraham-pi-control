# LHPC CLI reference

`lhpc` is the command-line interface to LoRaHAM Pi Control. Everything the web console
does is available here too.

**Conventions**

- Mutating commands (`install`, `stack start`, `build`, `test`, `update`, …) print a
  **dry-run plan** first and apply only after a `[y/N]` confirmation, or immediately with `--yes`.
- Read-only commands (`list`, `status`, `explain`, `doctor`, `source-check`, `config <stack>`) never change anything.
- Exit codes: `0` success, `1` a command error (`ERR`), `2` a usage error.
- Layered help: `lhpc --help`, `lhpc <command> --help`, `lhpc help <topic>`.

## Commands

- [list](#list) · [status](#status) · [explain](#explain) · [doctor](#doctor) · [deps](#deps) · [source-check](#source-check)
- [bootstrap](#bootstrap) · [install](#install) · [auto-install](#auto-install)
- [config](#config) · [hardware](#hardware) · [autostart](#autostart) · [firewall](#firewall) · [hmac](#hmac)
- [stack](#stack) · [build](#build) · [test](#test) · [update](#update) · [uninstall](#uninstall) · [clean](#clean) · [known-working](#known-working)
- [daemon](#daemon) · [logs](#logs)
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
`lhpc doctor` — bounded health checks. Local except for one thing: when the position source is
`gpsd`, it asks that gpsd (which may be on another box) whether it actually owns a receiver —
one bounded query, because a gpsd that answers while owning nothing yields no position at all.

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
from a pinned upstream checkout. lhpc never runs privileged commands — you run the script yourself:

```
lhpc deps --script > bootstrap-deps.sh
sudo bash bootstrap-deps.sh --dry-run                 # PRE-FLIGHT: simulate only, change nothing
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

The apt package set is identical on a Pi Zero 2W and a Pi 5. A rendered snapshot (`bootstrap-deps.sh`)
is shipped in the repo root for the pre-clone moment; regenerate it with the command above when
dependencies change (CI shell-syntax-checks the committed snapshot).

### source-check
`lhpc source-check [<target>]` — check managed sources for available upstream updates (read-only).

---

### bootstrap
`lhpc bootstrap [--yes]` — create the runtime root and a starter config.

### install
`lhpc install [<stack>] [--check] [--source binary|pinned|dev|stable] [--yes]` — install a stack:
download the published **binary** artifact, or adopt/verify managed sources into the runtime root.

- Without `--source`, a named stack uses its default channel — binary where one is published for
  this platform, else `dev`. The all-stacks form stays on the source channel.
- A failed binary install asks **explicitly** whether to build from source; it never falls back
  silently.
- `--check` is a dry run: it shows the plan and reports missing mandatory system dependencies
  (the apply run refuses until they are installed).

### auto-install
`lhpc auto-install [--source binary|pinned|dev|stable] [--tests] [--tx] [--status]
[--recover [--confirm-orphan]] [--yes]` — install/update, build and test **all** stacks in one
guided run.

- Host tests are **off by default**; `--tests` runs them, and `--tx` implies `--tests` and
  transmits one bounded frame per ready band (real RF — dummy loads).
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
  takes the **base** callsign only — the intersection every licensed stack accepts: the
  digit-bearing amateur structure — prefix, digit, then 1–3 letters, 3–6 characters total
  (e.g. `G0ABC`, `DJ0CHE`) — no SSID, no `/P`. `N0CALL` is refused as a placeholder, and its
  four-letter suffix is not a valid base shape either. A value any licensed stack would refuse cannot be saved globally.
- The global setting is **optional**. Licensed stacks (chat, iGate, Voice, Graywolf, MeshCom)
  inherit it only while their own callsign field is empty; the local field stays empty while
  inheriting. A per-stack value overrides it and may carry that stack's SSID or portable form:
  `lhpc config chat call YOURCALL-10` · `lhpc config voice callsign YOURCALL/P` ·
  `lhpc config meshcom mc_callsign YOURCALL-99` (`YOURCALL` = your own callsign — the
  N0CALL placeholder is refused). APRS/AX.25 stacks take SSID `-1`…`-15` (a bare
  callsign means SSID 0); MeshCom takes a numeric suffix `-1`…`-99` (plus the pinned firmware's one whitelisted
  real-station exception `OE2YOTA-1`; its protocol-control identifiers are deliberately
  not accepted as operator identities); Voice transmits at most
  11 characters and allows `/` and `-` (portable forms).
- **What this checks, and what it does not.** LHPC verifies that an identity is *configured,
  not a placeholder, and encodable by the protocol that will transmit it* — the byte and
  character limits, SSID ranges and callsign shape each stack's firmware or app actually accepts.
  It cannot and does not verify that the callsign is licensed to you. Using your own call remains
  yours; the gate stops a station transmitting under a value nobody chose.
- **Non-licensed stacks never inherit the global callsign.** Meshtastic needs both local node
  names (`lhpc config meshtastic node_name "Field Node"` + `node_short FN1`, 39/4 UTF-8
  bytes); MeshCore needs its local node name (max 31 bytes). A start without a required
  identity is refused and prints a command template for every missing field (replace the
  UPPERCASE token with your value). An identity you type on a start or restart is SAVED as that
  stack's configuration before the launch; every other start/restart parameter applies to that
  launch only. `lhpc config` (like the Settings page) may CLEAR an identity — a licensed callsign
  then falls back to the global one, and with no global left, or for a Meshtastic/MeshCore node
  name that never inherits, the stack simply cannot be started until you set one again. On the
  Start/Restart panel the same blank is refused instead, because that operation is a launch and a
  refused launch must not change your configuration.
- A `<param>` name shared by several components must be qualified as `<component>.<param>` — the command refuses rather than guessing.
- `--band` selects the band for band-switchable stacks.

Example: `lhpc config chat call YOURCALL-10` (`YOURCALL-10` = your callsign+SSID) then `lhpc stack start chat`.

### hardware
Show or set the **radio hardware setup** — which physical board(s) this box has. This fixes which
band(s) are served and the daemon `--hw` preset each radio launches with. A fresh install is **not
configured**, and the daemon refuses to start until a setup is chosen.

```
lhpc hardware                # show the current setup + served band(s) + the catalog
lhpc hardware loraham        # LoRaHAM dual-module (SX1278 + RFM95) — serves 433 + 868
lhpc hardware uputronics     # Uputronics dual (CE0 433 + CE1 868)
lhpc hardware uputronics-433 # Uputronics 433 only (CE0)
lhpc hardware uputronics-868 # Uputronics 868 only (CE1)
lhpc hardware waveshare-433  # Waveshare SX1262 (433)
lhpc hardware waveshare-868  # Waveshare SX1262 (868, on-air-untested)
```

- Only **legit** board combinations are offered (illegal ones — e.g. Waveshare + Uputronics — are
  absent from the catalog and can never be selected).
- With a single-radio setup lhpc shows only that radio, disables the other band's choosers, and blocks
  stacks that need the absent band (e.g. `meshcore` needs 868) with a clear reason.
- Also settable in the web console under the loraham daemon stack's **Hardware** settings section,
  which additionally offers a **Detect** probe (spawns the daemon briefly per candidate board and
  reports whether the chip responds — the board's LED lights during init).

---

### gps
Show or set the **position source shared by every stack**. Like `hardware`, this is a *global*
controller setting, not a per-stack parameter: Meshtastic, MeshCom, Sideband and Graywolf all take
position from here, so they can never disagree about where it comes from. Per-stack settings only
turn GPS **on or off**.

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

- **`auto` (the default) never refuses a start**: it uses a gpsd listening on `127.0.0.1:2947`
  and otherwise runs the stack **without position**. Explicit sources keep their fail-closed
  refusals. `auto` probes localhost only — a remote gpsd or a device is always an explicit choice.

- **gpsd covers the most cases.** How gpsd gets its data is gpsd's business, not lhpc's — a USB
  receiver, a HAT, or a hardware GPS server on the network all look the same through it. lhpc never
  edits `/etc/default/gpsd`; see [GPS](gps.md) for that side.
- **`nmea` opens the device directly** and therefore *excludes* gpsd: two readers on one receiver
  present as intermittent position loss, so lhpc refuses the combination by resolving the device's
  real identity (`st_rdev`) — `/dev/ttyACM0` and `/dev/serial/by-id/...` are recognised as the same
  receiver.
- **Malformed settings fail closed** to `source = off` rather than half-enabling a source.
- The source **cannot be changed while a stack that uses it is running** — stop the stack first; its
  claims and generated config were derived from the current source. What counts as "in use" is a
  stack's position readers and its feed, in any live state: a feed running on its own counts, and so
  does a component whose state cannot be determined — "could not tell" blocks the change rather than
  being read as "not running". A stack running with `use_gps = off` takes no position from the global
  setting and does not block it.
- Coordinates are never echoed back by the CLI, the console, or any log.
- **Each stack keeps its own saved switch**: `lhpc config <stack> use_gps on|off`
  (meshtastic, meshcom, reticulum, graywolf; default **on**). With the switch on and no usable
  source the stack starts **without position** — only a malformed section or an unresolvable
  explicit source refuses. The switch is stored band-lessly, so it survives a band change, and —
  like the source — it cannot be changed while that stack is running.
- Everything the console's **Position (GPS)** card offers is available here — the two surfaces
  call the same code, so validation and refusals are identical.
- `gpsd` is opt-in at bootstrap: `./bootstrap-deps.sh --spi-mode <mode> --with-gps`, and only
  when the source is a gpsd on *this* box.

---

### autostart
**Boot auto-restore** — restart the stacks that were running before a reboot (default: **on**).
At boot, `lhpc-boot-restore.service` restores every stack that was LHPC-started and not stopped
before the reboot, replaying its saved configuration through the normal gated start path. An
explicit `lhpc stack stop` is the last word: the stack stays down across reboots even when the
stop could not verify the process gone — the next `stack start` makes it restorable again. It only
runs while the web console unit is enabled and canonical. A failed restore is not retried —
start that stack yourself with `lhpc stack start <id>`.

```
lhpc autostart               # show the switch + the last boot-restore result
lhpc autostart off           # disable (applies at the NEXT boot)
lhpc autostart on            # re-enable (the default)
```

Also switchable in the web console's Webserver panel ("Boot restore"). The unit's log is
`logs/lhpc-boot-restore.log` (web: Controller logs → boot-restore).

---

### firewall
Managed **nftables firewall** status and script rendering. `lhpc` renders the ruleset; you apply
it with one sudo command. It never edits your own firewall configuration. See
[Firewalling the Pi](firewall.md) for the full model (modes, the three status dimensions, and how
your existing configuration is preserved).

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

- **Config/Boot/Live** are independent: the dashboard turns the firewall green ONLY with a
  verified current-boot live check — declared-and-persistent alone is never green.
- Also configurable in the web console under **Webserver → Firewall** (mode, per-listener
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
`lhpc test <target> [--tx] [--yes]` — run host tests, or a bounded TX test with `--tx` (real RF, dummy loads).

### update
`lhpc update [<target>] [--source binary|pinned|dev|stable] [--yes]` — update a stack/component to
the selected source.

- Without `--source` the target KEEPS its current channel: a binary-installed stack updates
  binary→binary, everything else defaults to `dev`.
- When the published binary lags this lhpc's pins, the update refuses and names the source build as
  the only way forward — cancelling keeps the working binary.
- Switching channels is an `install`, not an update, and the CLI says so.

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

- `expose` and `proxy` increase exposure: `lan`/remote need `--confirm-phrase enable-remote`; a public range (`0.0.0.0/0`), a `no-auth` mode, or an `http` listener need `enable-remote-danger`. Same phrases as the web UI.
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
- `disable` also requires `--confirm-phrase disable-hmac-auth` — it downgrades the link to
  unauthenticated.
- Password auth is on by default for a **source** install. On the **binary** channel the
  published firmware has no password, so meshcom runs open auth and every change here is refused
  until you install from source.
- `abort` cancels a running apply; `recover` clears a blocking `unsafe` state left when a
  cancelled build could not be proven stopped — automatically once the session is proven gone, or
  as your explicit acknowledgement after inspecting `ps`.

### _gps-bridge
Internal service — `lhpc _gps-bridge <meshtastic|meshcom>` — started by the lifecycle when the global position source needs to be presented as a device. Publishes NMEA on a PTY (Meshtastic) or a UNIX socket (MeshCom) under `state/gps/<consumer>/`, with a readiness marker driven by the upstream source. One instance per consumer. Not for direct use.

### _network-finalize
Internal driver — `lhpc _network-finalize --uuid <uuid> --op-id <token> [--pwfile <path>] [--allow-console] [--delay <s>]` — spawned detached by the web Network panel's connect flow: activates the Wi-Fi profile (secrets via a 0600 passwd-file, unlinked after activation), waits for the lease, and extends the console allowlist for the joined subnet when asked. Only the helper carrying the pending record's own op-id token may run; the outcome lands in `state/network-outcome.json`. Not for direct use.

### _hmac-apply
Internal driver — `lhpc _hmac-apply <stack> <enable|disable|renew> <run_id>` — spawned detached by the web/CLI apply flow to run the steps against a run marker + log. Not for direct use.

### _controller-uninstall-prep
Internal quiescence gate — `lhpc _controller-uninstall-prep [--root <dir>]` — invoked by `uninstall.sh` before it removes any controller state. Refuses on active/unprovable build/test/web jobs, unresolved auto-install/HMAC state, or any UNKNOWN component state; otherwise stops the managed stacks (clients before the shared daemon) and verifies cessation. Exit 0 = safe to remove; nonzero = abort teardown. Not for direct use.

### _uninstall-guard-claim
Internal atomic guard claim — `lhpc _uninstall-guard-claim [--root <dir>] --pid <pid> --nonce <n> --start <starttime>` — invoked by `uninstall.sh` to claim the `.lhpc-uninstalling` guard `O_CREAT|O_EXCL|O_NOFOLLOW` (never truncating/following/replacing a pre-existing guard). A live-owner guard is refused; a stale (dead-owner) guard is reclaimed. Not for direct use.

### _uninstall-guard-release
Internal owned-only guard release — `lhpc _uninstall-guard-release [--root <dir>] --nonce <n>` — removes the `.lhpc-uninstalling` guard ONLY if its recorded nonce matches (a foreign/unreadable guard is left in place). Not for direct use.

### help
`lhpc help [<topic>]` — detailed help on a topic: `safety`, `resources`, `profiles`.
