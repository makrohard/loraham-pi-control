# LoRaHAM Pi Control — local web deployment

Running the console persistently and updating it. The console serves a protected Unix socket or
loopback TCP, never a public address; remote access is the opt-in nginx + mTLS front end
([webserver.md](webserver.md)). The console process never runs `systemctl` (its unit blocks the
user bus), and lhpc never runs `sudo`: `install.sh` writes the user units, `bootstrap-deps.sh` the
polkit rules ([operations.md](operations.md#reboot--shut-down),
[wifi-access-point.md](wifi-access-point.md)).

## Contents

- [Serving model](#serving-model)
- [Self-hosted deployment layout](#self-hosted-deployment-layout)
- [Self-update](#self-update)
- [Run it under systemd](#run-it-under-systemd)
- [Controller status & updates on the web console](#controller-status--updates-on-the-web-console)

## Serving model

`lhpc web` serves through **waitress** (a declared dependency): one process, multi-threaded, no
debug, no reloader.

- **Productive** (`lhpc web --socket`, what the managed unit runs): a Unix socket at
  `state/run/lhpc-web.sock`, mode `0600`, no TCP listener. Without waitress the console **fails
  closed**.
- **Interactive** (`lhpc web`): loopback TCP (default `:8770`); without waitress it falls back to
  Flask's development server, with a warning.

The other web-layer guarantees (loopback-only bind, trusted-host check, CSRF, headers):
[safety model](architecture.md#safety-model).

Run **one** process: per-request state and CSRF handling are only safe single-process.

## Self-hosted deployment layout

The layout is [the runtime root](architecture.md#the-runtime-root). The unit sets
`LHPC_RUNTIME_ROOT=~/loraham-pi-control` explicitly, runs `venv/lhpc/bin/lhpc web`, and works from
`src/loraham-pi-control`. The venv sits outside the checkout so self-update's `git clean` cannot
reach it.

`lhpc status` shows a `[controller]` row with its cached version / update / identity state; the
identity verdicts: [controller identity](architecture.md#controller-identity--self-update).

## Self-update

Back up `config/`, `profiles/` and the app data under `state/` first
([backup & restore](operations.md#backup--restore)).

- **One-click (normal path).** "Update & restart now" checks every gate, answers with the
  "Restarting" page, and about 1 s after that response was closed (time for any remaining output to
  flush) writes an exclusively-created request marker (`state/selfupdate.request`, payload
  `normal`|`overwrite`); the `lhpc-selfupdate.path` unit starts the sandboxed
  `lhpc-selfupdate.service`, which claims it (rename to `state/selfupdate.inflight` with process
  identity), applies (exclusive lock, live identity check, dirty refusal), syncs the venv, and
  records the outcome. Console stop/restart is declarative (`Conflicts`/`After` +
  `OnSuccess`/`OnFailure=lhpc-web.service`). The "Restarting" page does not reload itself; its
  button returns to the console.
- **Canonical units are the contract.** One-click is offered only when the console is the managed
  unit (`INVOCATION_ID`) and the units are byte-for-byte canonical; a foreign, drop-in or masked
  unit is left for manual resolution. To repair the integration, run
  `lhpc self-update --repair-integration` from a shell — it restores the exact canonical set on an
  existing or `--no-service` deployment (the console's *Repair & update* does the same while its
  unit still has bus access).
- **Manual path.** `lhpc self-update --apply` from an operator shell (refused inside a managed
  unit): if the console is running it stops `lhpc-web`, applies, syncs the venv, then starts the
  console again.
- **Dirty checkout** blocks apply unless you choose `--overwrite`.
- **Venv sync** runs after a real advance on both paths; if it fails the update is reported
  failed and the result names the `pip install -e` command to run by hand.

### Recovery

- **Identity mismatch** (`self-update blocked: unsafe controller identity …`): fix what the
  message names — a stray symlink in the chain, wrong ownership/mode (`chmod 700`, `chown` to
  yourself), a detached/renamed branch (`git -C … checkout main`), or a changed `origin` — then
  re-check.
- **Failed / interrupted update**: inspect `state/selfupdate-migrate.json` as the message directs.
  Nothing is applied on a blocked or recovery-required journal.
- **Stuck one-click request** (`update recovery required` in the console): run
  `lhpc self-update --recover-request`. It clears a never-claimed request outright, and an
  in-flight record only after proving the helper process has stopped (a missing/unreadable
  identity is never auto-cleared; the command says what to check). One-click is blocked until
  then.

## Run it under systemd

`install.sh` writes all **seven canonical user units**, refusing to overwrite a foreign one:

- `lhpc-web.service` — the console;
- `lhpc-selfupdate.service` / `.path` — the self-update helper and its watcher;
- `lhpc-nginx.service` — the TLS front end, enabled when nginx is installed and started once
  `lhpc webserver start-service` has generated its config;
- `lhpc-nginx-restart.service` / `.path` — its restart helper and watcher;
- `lhpc-boot-restore.service` — a oneshot, enabled but not started at install (it runs at the
  next boot).

It then runs `daemon-reload`, enables the units, and turns on lingering so the console autostarts
at boot. `--no-service` skips all of this. The units match the shipped `deploy/*.service`
templates except for `%h` vs resolved paths; their bytes are frozen
([backlog](backlog.md#two-stage-unit-template-migration)).

The four hidden commands the units run (`self-update --run-service`,
`webserver --run-restart-service`, `webserver --firewall-boot-gate`, `autostart --run-service`)
refuse when the systemd marker `INVOCATION_ID` is absent (e.g. run by hand), naming the command
to use instead.

They are **user** units, no root. To install the console unit by hand:

```bash
mkdir -p ~/.config/systemd/user
cp ~/loraham-pi-control/src/loraham-pi-control/deploy/lhpc-web.service ~/.config/systemd/user/
# adjust ExecStart path / port in the copy if your layout differs
systemctl --user daemon-reload
systemctl --user enable --now lhpc-web.service
loginctl enable-linger "$USER"     # keep running after logout
```

- **Logs:** `tail -f ~/loraham-pi-control/logs/lhpc-web.log` (`journalctl --user -u lhpc-web` shows only systemd's own messages)
- **Stop:** `systemctl --user stop lhpc-web`
- **Disable:** `systemctl --user disable --now lhpc-web`
- **Recovery** (after the restart limit trips): `systemctl --user reset-failed lhpc-web && systemctl --user restart lhpc-web`

### Why these unit settings

| Directive | Why |
|---|---|
| `Restart=on-failure`, `RestartSec=3`, `StartLimitBurst=5` / `StartLimitIntervalSec=60` | recovers from a crash, stops flapping instead of looping |
| `StandardOutput=append:` / `StandardError=append:` → `logs/lhpc-web.log`, `SyslogIdentifier=lhpc-web` | the file carries the app output; the journal only systemd's unit messages |
| `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `RestrictNamespaces`, `ProtectKernel*`, `ProtectControlGroups`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK AF_BLUETOOTH` | least privilege; writable only `ReadWritePaths=%h/loraham-pi-control /tmp` plus the optional `-%h/.meshcore_nm` (skipped when absent; no shipped component uses it) |
| `InaccessiblePaths=%t/bus %t/systemd/private` | the console cannot reach the user bus, so it cannot run `systemctl --user` |
| `KillMode=process` on `lhpc-web.service` and `lhpc-boot-restore.service` | the stacks and detached jobs LHPC starts live in the unit's control group; the default `control-group` would kill them on every web restart, self-update, or (for the oneshot) stop, restart or start timeout. Controller uninstall stops them explicitly |
| `PLATFORMIO_CORE_DIR`, `IDF_TOOLS_PATH`, `XDG_CACHE_HOME`, `PIP_CACHE_DIR` → `build/tool-cache/` | build and QEMU toolchain caches stay under the runtime root, not in `~/.platformio`, `~/.espressif` or `~/.cache` (install the ESP QEMU/toolchain into `IDF_TOOLS_PATH`) |
| `MemoryDenyWriteExecute` omitted | QEMU's TCG JIT (the meshcom emulator) needs writable-executable memory |
| `PrivateTmp=false` | the console must see the daemon's shared sockets in `/tmp` (`/tmp/loraconf*.sock`, `/tmp/lora*.sock`) |

## Controller status & updates on the web console

The controller row (first entry on **Apps**/`/stacks`) and the footer version indicator render
only the cache ([controller identity](architecture.md#controller-identity--self-update)); a
missing or stale cache shows "unchecked/unknown".

- **Background check:** the console refreshes the cache at startup and then every
  `update_check_hours` (`config/local.toml`, `[web]`; default 12, clamped 1–168, `0` disables).
- **"Check for updates"** (controller row) does the same on demand — `git fetch` against upstream
  and a fresh identity check.
- **"Update now"** runs the [one-click path](#self-update).
