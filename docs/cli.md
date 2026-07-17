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

- [list](#list) · [status](#status) · [explain](#explain) · [doctor](#doctor) · [source-check](#source-check)
- [bootstrap](#bootstrap) · [install](#install) · [auto-install](#auto-install)
- [config](#config) · [hardware](#hardware)
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
`lhpc doctor` — bounded local health checks.

### source-check
`lhpc source-check [<target>]` — check managed sources for available upstream updates (read-only).

---

### bootstrap
`lhpc bootstrap [--yes]` — create the runtime root and a starter config.

### install
`lhpc install [<stack>] [--check] [--source pinned|dev|stable] [--yes]` — adopt/verify managed sources into the runtime root. `--check` is a dry run: it always shows the plan and *reports* any missing mandatory system dependencies (the apply run refuses until they are installed).

### auto-install
`lhpc auto-install [--source pinned|dev|stable] [--no-tests] [--tx] [--yes]` — install/update, build and test **all** stacks in one guided run. `--tx` transmits one bounded test frame per ready band (real RF — dummy loads); it requires host tests.

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

- `operator` is a reserved subcommand (not a stack id). `--callsign` applies only to it.
- Every licensed stack inherits `operator`'s callsign by default, so `lhpc config operator --callsign W1ABC` unblocks them all; use `lhpc config <stack> <call-param> <value>` for a per-stack override.
- A `<param>` name shared by several components must be qualified as `<component>.<param>` — the command refuses rather than guessing.
- `--band` selects the band for band-switchable stacks.

Example: `lhpc config chat call W1ABC` then `lhpc stack start chat`.

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

### stack
`lhpc stack {start|stop|restart} <stack> [--yes]` — start, stop or restart a stack or component.

### build
`lhpc build <target> [--yes]` — build a stack/component.

### test
`lhpc test <target> [--tx] [--yes]` — run host tests, or a bounded TX test with `--tx` (real RF, dummy loads).

### update
`lhpc update [<target>] [--source pinned|dev|stable] [--yes]` — update a stack/component to the selected source.

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
lhpc webserver proxy <stack> [--mode local|lan|public] [--port P] [--scheme https|http] [--access-mode M] [--cidr C ...] [--confirm-phrase P]
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
`lhpc hmac status|enable|disable|renew|abort|recover [<stack>] [--yes]` — MeshCom HMAC (bridge↔firmware) password. `status` prints enabled/disabled (default stack: meshcom). `enable`/`disable`/`renew` **rebuild the firmware and restart the link** (several minutes) — without `--yes` they warn and print the confirm hint; with `--yes` they apply, streaming each step (secret → firmware → bridge → node). The secret value is never printed. Password-auth is on by default at install. `abort` cooperatively cancels a running apply (SIGTERM to the driver, which stops the build and writes the terminal state). `recover` clears a blocking `unsafe` state left when a cancelled/timed-out build could not be proven stopped — auto for a `session-unverified` scope once the session is proven gone, or as your explicit acknowledgement (after inspecting `ps`) for an `escaped-or-output-unverified` scope.

### _hmac-apply
Internal driver — `lhpc _hmac-apply <stack> <enable|disable|renew> <run_id>` — spawned detached by the web/CLI apply flow to run the steps against a run marker + log. Not for direct use.

### help
`lhpc help [<topic>]` — detailed help on a topic: `safety`, `resources`, `profiles`.
