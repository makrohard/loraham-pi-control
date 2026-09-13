# LoRaHAM Pi Control — local web deployment

The console process itself is a **local operator tool**: it serves either a protected Unix
socket or loopback TCP, never a public address. Reaching it from another machine is a separate,
opt-in step through the nginx + mTLS front end ([webserver.md](webserver.md)). This document
covers running it persistently and updating it. **lhpc itself never runs `systemctl` and never
runs a privileged command**: the units are written by `install.sh`, the polkit rules by
`bootstrap-deps.sh` ([operations.md](operations.md),
[wifi-access-point.md](wifi-access-point.md)).

## Contents

- [Serving model](#serving-model)
- [Self-hosted deployment layout](#self-hosted-deployment-layout)
- [Self-update](#self-update)
- [Run it under systemd](#run-it-under-systemd)
- [Controller status & updates on the web console](#controller-status--updates-on-the-web-console)

## Serving model

`lhpc web` serves through **waitress** (a declared dependency, installed with lhpc): one
process, multi-threaded, no debug, no reloader.

- **Productive** (`lhpc web --socket`, what the managed unit runs): a protected Unix socket at
  `state/run/lhpc-web.sock`, mode `0600`, no TCP listener at all. Waitress is mandatory here — if
  it is missing the console **fails closed** rather than falling back.
- **Interactive** (`lhpc web`): loopback TCP (default `:8770`) for quick local use; without
  waitress this one does fall back to Flask's development server, with a warning.

The TCP mode binds loopback only; remote access is the nginx front end, never a public bind —
[webserver.md](webserver.md). The other guarantees of the web layer (trusted-host check, CSRF,
headers) are listed in the [safety model](architecture.md).

Use **one** process. The console keeps per-request state and CSRF assumptions that are only
safe single-process; do not run multiple workers.

## Self-hosted deployment layout

The layout — a plain runtime root with LHPC's own checkout under `src/` and the venv outside it —
is [the runtime root](architecture.md#the-runtime-root). The unit sets
`LHPC_RUNTIME_ROOT=~/loraham-pi-control` **explicitly**, runs `venv/lhpc/bin/lhpc web`, and works
from `src/loraham-pi-control`. Keeping the venv *outside* the checkout means self-update's
`git clean` can never reach it.

`lhpc status` shows a distinct `[controller]` row with its cached version / update / identity
state; the identity itself, its live verdicts and what every apply re-checks:
[controller identity](architecture.md#controller-identity--self-update).

## Self-update

Back up `config/`, `profiles/` and the app data under `state/` first
([backup & restore](operations.md#backup--restore)).

- **One-click (normal path).** The console **cannot** run `systemctl` — its unit blocks the
  user D-Bus (`InaccessiblePaths=%t/bus %t/systemd/private`). "Update now" writes an
  exclusively-created request marker (`state/selfupdate.request`, payload `normal`|`overwrite`);
  the static `lhpc-selfupdate.path` unit starts the sandboxed `lhpc-selfupdate.service`, which
  claims it (rename to `state/selfupdate.inflight` with process identity), applies (exclusive
  lock, live identity check, dirty refusal), syncs the venv, and records the outcome. Console
  stop/restart is declarative (`Conflicts`/`After` + `OnSuccess`/`OnFailure=lhpc-web.service`),
  not scripted. The browser reconnects on its own.
- **Canonical units are the contract.** One-click is offered only when the console is the
  managed unit (`INVOCATION_ID`) and the units are byte-for-byte canonical; a foreign, drop-in or
  masked unit is left for manual resolution. If the integration needs repair, run
  `lhpc self-update --repair-integration` from a shell — it restores the exact canonical set on
  an existing or `--no-service` deployment (the console's *Repair & update* does the same in one
  click while its unit still has bus access).
- **Manual path.** `lhpc self-update --apply` from an operator shell (refused inside a managed unit): when the console is running it stops `lhpc-web` itself (the console's shared lock would otherwise block the apply), applies, syncs the venv, then starts the console again.
- **Dirty checkout** blocks apply unless you choose `--overwrite`.
- **Venv sync** runs automatically after a real advance on both paths; if it fails the update
  is reported failed, never half-applied, and the result names the `pip install -e` command to
  run by hand.

### Recovery

- **Identity mismatch** (`self-update blocked: unsafe controller identity …`): fix the
  layout the message names — a stray symlink in the chain, wrong ownership/mode (`chmod 700`,
  `chown` to yourself), a detached/renamed branch (`git -C … checkout main`), or a changed
  `origin` — then re-check.
- **Failed / interrupted update**: inspect `state/selfupdate-migrate.json` as the message
  directs. Nothing is applied on a blocked or recovery-required journal.
- **Stuck one-click request** (`update recovery required` in the console): a request/in-flight
  marker was left behind (e.g. the helper was killed mid-run). Run
  `lhpc self-update --recover-request` — it clears a never-claimed request outright, and clears
  an interrupted in-flight record **only after proving the helper process has actually
  stopped** (a missing/unreadable identity is never auto-cleared; the command tells you what to
  check). One-click is blocked until this is resolved.

## Run it under systemd

`install.sh` writes all **seven canonical user units** — `lhpc-web.service`, the self-update
helper + watcher (`lhpc-selfupdate.service`/`.path`), the `lhpc-nginx.service` TLS front-end
(enabled, started once `lhpc webserver start-service` has generated its config) with its restart helper +
watcher (`lhpc-nginx-restart.service`/`.path`), and the `lhpc-boot-restore.service` oneshot
(enabled, never started at install — it runs at the next boot) — never overwriting a foreign one;
runs `daemon-reload`, enables them, and turns on lingering so the console autostarts at boot.
Pass `--no-service` to skip it. The generated units are byte-identical to the shipped
`deploy/*.service` templates (differing only in `%h` vs the resolved paths); their bytes are
frozen — see [backlog.md](backlog.md) for why.

They are **user** units: no root, hardened to be compatible with the runtime root and the
daemon's shared `/tmp` sockets. To install the console unit by hand:

```bash
mkdir -p ~/.config/systemd/user
cp ~/loraham-pi-control/src/loraham-pi-control/deploy/lhpc-web.service ~/.config/systemd/user/
# adjust ExecStart path / port in the copy if your layout differs
systemctl --user daemon-reload
systemctl --user enable --now lhpc-web.service
loginctl enable-linger "$USER"     # keep running after logout
```

- **Logs:** `tail -f ~/loraham-pi-control/logs/lhpc-web.log` (the unit appends stdout/stderr there; `journalctl --user -u lhpc-web` shows only systemd's own messages)
- **Stop:** `systemctl --user stop lhpc-web`
- **Disable:** `systemctl --user disable --now lhpc-web`
- **Recovery** (after the bounded restart limit trips): `systemctl --user reset-failed lhpc-web && systemctl --user restart lhpc-web`

### Why these unit settings

| Directive | Why |
|---|---|
| `Restart=on-failure`, `RestartSec=3`, `StartLimitBurst=5` / `StartLimitIntervalSec=60` | recovers from a crash, stops flapping instead of looping |
| `StandardOutput=append:` / `StandardError=append:` → `logs/lhpc-web.log`, `SyslogIdentifier=lhpc-web` | the file carries the app output; the journal only systemd's own unit messages |
| `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `RestrictNamespaces`, `ProtectKernel*`, `ProtectControlGroups`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK AF_BLUETOOTH` | least privilege; the only writable areas are `ReadWritePaths=%h/loraham-pi-control /tmp` plus the optional `-%h/.meshcore_nm` (the leading `-` skips it when absent; no shipped component uses it) |
| `KillMode=process` on `lhpc-web.service` and `lhpc-boot-restore.service` | the stacks and detached jobs LHPC starts live in the unit's control group; the default `control-group` would kill them on every web restart or self-update (controller uninstall is the one path that stops them explicitly) — for the `RemainAfterExit=yes` oneshot the same holds on a later stop, restart or start timeout |
| `PLATFORMIO_CORE_DIR`, `IDF_TOOLS_PATH`, `XDG_CACHE_HOME`, `PIP_CACHE_DIR` → `build/tool-cache/` | build and QEMU toolchain caches stay under the runtime root, inherited by every build/test/QEMU child — nothing in `~/.platformio`, `~/.espressif` or `~/.cache` (install the ESP QEMU/toolchain into `IDF_TOOLS_PATH`) |
| `MemoryDenyWriteExecute` omitted | QEMU's TCG JIT (the meshcom emulator) needs writable-executable memory — the single documented exception |
| `PrivateTmp=false` | the console must see the daemon's shared sockets in `/tmp` (`/tmp/loraconf*.sock`, `/tmp/lora*.sock`); `/tmp` is the one shared writable location (also the daemon self-test's scratch dir) |

## Controller status & updates on the web console

The controller row (first entry on **Apps**/`/stacks`) and the version indicator in the footer
are **cached-only on every page load** ([architecture.md](architecture.md)): a missing or stale
cache simply shows an "unchecked/unknown" state.

- **Background check:** the console refreshes that cache by itself — once at startup and then
  every `update_check_hours` (default 12; set it in `config/local.toml` under `[web]`,
  clamped 1–168, `0` disables the loop) — so the footer's "Update →" indicator appears
  without any clicking.
- **"Check for updates"** (in the controller row) does the same live work on demand —
  `git fetch` against upstream and a fresh identity check — and rewrites the cache.
- **"Update now"** runs the one-click path above.
