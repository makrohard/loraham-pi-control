# LoRaHAM Pi Control — local web deployment

The web console is a **local operator tool**, bound to **loopback only** (`127.0.0.1`).
It is not a public web service. This document describes the supported way to run it
persistently. **lhpc never installs, enables, or starts any systemd unit for you** — every
step below is manual and under your control.

## Serving model

`lhpc web` prefers a production-capable WSGI server (**waitress**): one process,
multi-threaded, no debug, no reloader. If waitress is not installed it falls back to the
Flask development server (fine for quick interactive use only) and prints a warning.

Install the supported server into the deployment venv (manual, one-time):

```bash
~/loraham-pi-control/venv/lhpc/bin/pip install waitress
```

Loopback-only is a **hard invariant**: `run_server` refuses any non-loopback `--host`
(`127.0.0.1` / `::1` only). There is no debug mode, no reloader, and no public bind.

Use **one** process. The console keeps per-request state and CSRF assumptions that are only
safe single-process; do not run multiple workers without explicitly re-designing that.

## Self-hosted deployment layout (the deployment standard)

The supported **deployment** makes the runtime root a **plain container** and keeps LHPC's
own source under it, exactly like the managed stack sources — so "the code that runs" and
"the code self-update fetches" are one tree:

```
~/loraham-pi-control/            runtime root — a PLAIN container, NOT a git checkout
├── src/
│   ├── loraham-pi-control/      LHPC's OWN checkout (.git lives HERE, nowhere else)
│   └── loraham-daemon/  RadioLib/  …   managed stack sources
├── config/  logs/  state/  backups/
└── venv/lhpc/                   the venv, OUTSIDE the checkout
```

The unit sets `LHPC_RUNTIME_ROOT=~/loraham-pi-control` **explicitly**, runs
`venv/lhpc/bin/lhpc web`, and works from `src/loraham-pi-control`. Keeping the venv
*outside* the checkout means self-update's `git clean` can never reach it.

LHPC's checkout is a **dedicated controller identity**: it is observable and self-updatable,
but it is **never** installed, adopted, built, tested, started, stopped, uninstalled,
cleaned, or bulk-processed — every generic verb (`lhpc install/update/uninstall/clean/
build/test/stack start|stop <controller-id>`) refuses centrally and points you at
`lhpc self-update`. `lhpc status` shows a distinct `[controller]` row with its cached
version / update / identity state.

### Security boundary (the identity policy)

The runtime root and the controller checkout must be **owned by the service user** and have
**no group/other write** (mode `0700`). This is the stated policy behind the identity
proof: before any self-update apply, LHPC verifies the fixed layout (no symlink anywhere in
the `runtime-root → src → checkout` chain, correct ownership/mode, the checkout realpath
equal to both the discovered git repo and the imported package), on the expected branch,
attached, with the approved canonical `origin`. A same-account process replacing the
checkout mid-check is **out of the threat model** — LHPC *detects and refuses* an unsafe
layout, it does not claim same-account race-proofness.

### Self-update operating rules

- **One-click from the web console (the normal path).** "Update now" (confirm step included)
  starts a static, **parameter-free** helper unit — `lhpc-selfupdate.service` — whose fixed
  job is: stop `lhpc-web` → apply the update (exclusive lock, fresh live identity check,
  dirty refusal) → sync the venv when the checkout advanced → **always start `lhpc-web`
  again** (also on failure/timeout, enforced twice: in the updater and by the unit's
  `ExecStopPost`). The browser shows a reconnecting page and returns on its own; the
  controller row then shows the recorded outcome. No free-form input reaches the updater:
  a **dirty** tree is detected fresh at the confirm step, and your explicit discard consent
  merely selects the second fixed unit (`lhpc-selfupdate-overwrite.service`).
- **Manual CLI path (equivalent, still supported).** The running console holds the
  controller-runtime lock SHARED, so an in-process apply refuses while it is up:
  `systemctl --user stop lhpc-web`, then `lhpc self-update --apply`, then start it again.
- **A dirty checkout blocks apply** unless you explicitly choose overwrite.
- **Dependency sync:** the one-click updater runs the editable install automatically after a
  real advance. On the manual path, when an update reports `deps_changed`, run it yourself
  before restarting:
  ```bash
  ~/loraham-pi-control/venv/lhpc/bin/python -m pip install -e ~/loraham-pi-control/src/loraham-pi-control
  ```
- The web unit is **operator-managed** — self-update never rewrites it. `install.sh` writes
  the two updater helpers next to it (start-on-demand, never enabled at boot).

### Recovery

- **Identity mismatch** (`self-update blocked: unsafe controller identity …`): fix the
  layout the message names — a stray symlink in the chain, wrong ownership/mode (`chmod 700`,
  `chown` to yourself), a detached/renamed branch (`git -C … checkout main`), or a changed
  `origin` — then re-check.
- **Failed / interrupted update**: the existing migration-journal recovery applies; inspect
  `state/selfupdate-migrate.json` as the message directs. Nothing is applied on a blocked or
  recovery-required journal.

## Run it under systemd (user service, no root)

`install.sh` already does this by default — it generates a user unit with the install's
absolute paths, enables it, and turns on lingering (so it starts on boot); pass `--no-service`
to skip it. To set it up by hand instead:

A ready-to-adapt template lives at `deploy/lhpc-web.service`. It is a **user** unit — it
runs as your normal user, needs no root, and is hardened to be compatible with the runtime
root and the daemon's shared `/tmp` sockets.

```bash
mkdir -p ~/.config/systemd/user
cp ~/loraham-pi-control/src/loraham-pi-control/deploy/lhpc-web.service ~/.config/systemd/user/
# adjust ExecStart path / port in the copy if your layout differs
systemctl --user daemon-reload
systemctl --user enable --now lhpc-web.service
loginctl enable-linger "$USER"     # optional: keep running after logout
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
  `ProtectHome=read-only`, `RestrictNamespaces`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, and related restrictions. The **only**
  writable areas are `ReadWritePaths=%h/loraham-pi-control /tmp` — the runtime root and the
  shared `/tmp`. The service does **not** get broad write access to the rest of your home or
  to `/var`.
- **Runtime-owned build/tool caches**: the console orchestrates builds (cmake / PlatformIO /
  pip) and the QEMU emulator, which write toolchain caches. The unit points them at a
  runtime-owned location under `build/tool-cache/` via
  `PLATFORMIO_CORE_DIR`, `IDF_TOOLS_PATH`, `XDG_CACHE_HOME` and `PIP_CACHE_DIR` — so nothing
  is written to `~/.platformio`, `~/.espressif` or `~/.cache`. These are inherited by every
  build/test/QEMU child the console spawns. (Install the ESP QEMU/toolchain into
  `IDF_TOOLS_PATH` rather than `~/.espressif`.)
- **`MemoryDenyWriteExecute` is deliberately omitted** — QEMU's TCG JIT (the meshcom
  emulator) needs writable-executable memory. It is the single documented exception; every
  other protection stays on.
- **`PrivateTmp=false`** — deliberately: the console must see the daemon's shared Unix
  sockets in `/tmp` (`/tmp/loraconf*.sock`, `/tmp/lora*.sock`). A private `/tmp` would hide
  them and break status/monitor. `/tmp` is the one shared writable location (it also holds
  the daemon self-test's scratch dir).

### Controller status & updates on the web console

The controller row (first entry on **Apps**/`/stacks`) and the version indicator in the
footer are **cached-only on every page load**: they read the last self-update envelope from
`state/` plus the running in-process version, and never touch the live checkout, `.git`, the
network, or the controller identity while rendering a GET. A missing or stale cache simply
shows an "unchecked/unknown" state.

- **Background check:** the console refreshes that cache by itself — once at startup and then
  every `update_check_hours` (default 12; set it in `config/local.toml` under `[web]`,
  clamped 1–168, `0` disables the loop) — so the footer's "Update →" indicator appears
  without any clicking.
- **“Check for updates”** (in the controller row) does the same live work on demand —
  `git fetch` against upstream and a fresh identity check — and rewrites the cache.
- **Applying an update** always performs a **fresh live identity/provenance check immediately
  before mutating** the checkout; it never trusts the cached verdict to authorise a change,
  and it always runs with the web service stopped (controller-runtime lock) — the one-click
  updater unit handles that stop/start for you.

## Security boundary

The console is **loopback-only by design**. Remote access is **not** provided by this unit
and must never be obtained by binding a public address. Exposing it remotely requires a
separate, explicit design: authenticated **HTTPS** and/or a trusted **reverse proxy** with
its own access control — future work, opt-in, and out of scope here.
