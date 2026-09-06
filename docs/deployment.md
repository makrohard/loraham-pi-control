# LoRaHAM Pi Control — local web deployment

The console process itself is a **local operator tool**: it serves either a protected Unix
socket or loopback TCP, never a public address. Reaching it from another machine is a separate,
opt-in step through the nginx + mTLS front end ([webserver.md](webserver.md)). This document
covers running it persistently and updating it. **lhpc itself never runs `systemctl` and never
runs a privileged command**: the units are written by `install.sh`, and the two polkit rules that
let the console reboot the box and manage Wi-Fi are installed by `bootstrap-deps.sh`
([operations.md](operations.md), [wifi-access-point.md](wifi-access-point.md)).

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

Loopback-only is a hard invariant for the TCP mode: `run_server` refuses any non-loopback
`--host` (`127.0.0.1` / `::1`). Remote access is the nginx front end (`lhpc-nginx.service`,
HTTPS + mTLS + source-CIDR gate, opt-in behind a typed confirmation), never a public bind —
[webserver.md](webserver.md). The other guarantees of the web layer (trusted-host check, CSRF,
headers) are listed in the [safety model](architecture.md).

Use **one** process. The console keeps per-request state and CSRF assumptions that are only
safe single-process; do not run multiple workers.

## Self-hosted deployment layout

The supported deployment makes the runtime root a **plain container** and keeps LHPC's own
source under it, exactly like the managed stack sources — so "the code that runs" and "the code
self-update fetches" are one tree:

```
~/loraham-pi-control/            runtime root — a PLAIN container, NOT a git checkout
├── src/
│   ├── loraham-pi-control/      LHPC's OWN checkout (.git lives HERE, nowhere else)
│   └── loraham-daemon/  RadioLib/  …   managed stack sources
├── config/  logs/  state/  backups/
└── venv/lhpc/                   the venv, OUTSIDE the checkout
```

The unit sets `LHPC_RUNTIME_ROOT=~/loraham-pi-control` **explicitly**, runs
`venv/lhpc/bin/lhpc web`, and works from `src/loraham-pi-control`. Keeping the venv *outside*
the checkout means self-update's `git clean` can never reach it.

LHPC's checkout is a **dedicated controller identity**: observable and self-updatable, but
never installed, adopted, built, tested, started, stopped, uninstalled, cleaned, or
auto-install-processed — every generic verb aimed at it refuses centrally and points you at
`lhpc self-update`. `lhpc status` shows a distinct `[controller]` row with its cached
version / update / identity state.

**The identity policy.** The runtime root and the controller checkout must be **owned by the
service user** with **no group/other write** (mode `0700`). Before any self-update apply, LHPC
verifies the fixed layout — no symlink anywhere in the `runtime-root → src → checkout` chain,
correct ownership/mode, the checkout realpath equal to both the discovered git repo and the
imported package, on the expected branch, attached, with the approved canonical `origin` — and
refuses (`unsafe`) otherwise; the verdicts are defined in [architecture.md](architecture.md). A
same-account process replacing the checkout mid-check is **out of the threat model**: LHPC
detects and refuses an unsafe layout, it does not claim same-account race-proofness.

## Self-update

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
- **Manual path.** With the console up its shared lock blocks an in-process apply:
  `systemctl --user stop lhpc-web`, then `lhpc self-update --apply`, then start it again.
- **Dirty checkout** blocks apply unless you choose `--overwrite`.
- **Venv sync** runs automatically after a real advance; if it fails the update is reported
  failed (never half-applied). On the manual path, when it reports `deps_changed`, run:
  ```bash
  ~/loraham-pi-control/venv/lhpc/bin/python -m pip install -e ~/loraham-pi-control/src/loraham-pi-control
  ```
- **Applying always re-checks live.** Every apply performs a fresh identity/provenance check
  immediately before mutating the checkout — it never trusts the cached verdict — and runs with
  the web service stopped (controller-runtime lock); the one-click updater unit handles that
  stop/start for you.

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
(enabled, started once `lhpc webserver apply` has generated its config) with its restart helper +
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

- **Logs:** `journalctl --user -u lhpc-web -f`
- **Stop:** `systemctl --user stop lhpc-web`
- **Disable:** `systemctl --user disable --now lhpc-web`
- **Recovery** (after the bounded restart limit trips): `systemctl --user reset-failed lhpc-web && systemctl --user restart lhpc-web`

### Why these unit settings

- **Bounded restart** (`Restart=on-failure`, `RestartSec=3`, `StartLimitBurst=5` /
  `StartLimitIntervalSec=60`): auto-recovers from a crash but stops flapping instead of
  looping forever.
- **journald logging**: all stdout/stderr goes to the journal (`SyslogIdentifier=lhpc-web`).
- **Least-privilege hardening**: `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome=read-only`, `RestrictNamespaces`, `ProtectKernel*`, `ProtectControlGroups`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK AF_BLUETOOTH`. The **only**
  writable areas are `ReadWritePaths=%h/loraham-pi-control /tmp` — the runtime root and the
  shared `/tmp` — plus the optional `-%h/.meshcore_nm` entry (the leading `-` skips it when
  absent; no shipped component uses it). The service gets no write access to the rest of your
  home or to `/var`.
- **`KillMode=process`** on `lhpc-web.service` AND `lhpc-boot-restore.service`: LHPC
  identity-tracks and lifecycle-manages the stacks and detached build/test jobs it starts, so a
  web restart or a self-update must not tear those workloads down (the default
  `control-group` would kill them on every web restart; controller uninstall is the one path
  that stops and verifies them explicitly). For the boot-restore oneshot
  (`RemainAfterExit=yes`) the same applies on a later stop, restart or start timeout of the
  unit: the restored stacks live in its control group.
- **Runtime-owned build/tool caches**: builds (cmake / PlatformIO / pip) and the QEMU emulator
  write toolchain caches; the unit points `PLATFORMIO_CORE_DIR`, `IDF_TOOLS_PATH`,
  `XDG_CACHE_HOME` and `PIP_CACHE_DIR` at `build/tool-cache/` under the runtime root, inherited
  by every build/test/QEMU child — nothing is written to `~/.platformio`, `~/.espressif` or
  `~/.cache`. (Install the ESP QEMU/toolchain into `IDF_TOOLS_PATH` rather than `~/.espressif`.)
- **`MemoryDenyWriteExecute` is deliberately omitted** — QEMU's TCG JIT (the meshcom
  emulator) needs writable-executable memory. It is the single documented exception; every
  other protection stays on.
- **`PrivateTmp=false`** — deliberately: the console must see the daemon's shared Unix
  sockets in `/tmp` (`/tmp/loraconf*.sock`, `/tmp/lora*.sock`). A private `/tmp` would hide
  them and break status/monitor. `/tmp` is the one shared writable location (it also holds
  the daemon self-test's scratch dir).

## Controller status & updates on the web console

The controller row (first entry on **Apps**/`/stacks`) and the version indicator in the
footer are **cached-only on every page load**: they read the last self-update envelope from
`state/` plus the running in-process version, and never touch the live checkout, `.git`, the
network, or the controller identity while rendering a GET. A missing or stale cache simply
shows an "unchecked/unknown" state.

- **Background check:** the console refreshes that cache by itself — once at startup and then
  every `update_check_hours` (default 12; set it in `config/local.toml` under `[web]`,
  clamped 1–168, `0` disables the loop) — so the footer's "Update →" indicator appears
  without any clicking.
- **"Check for updates"** (in the controller row) does the same live work on demand —
  `git fetch` against upstream and a fresh identity check — and rewrites the cache.
- **"Update now"** runs the one-click path above.
