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

- **Stop the web service first.** Self-update takes an EXCLUSIVE controller-runtime lock;
  the running console holds it SHARED for its whole lifetime, so an apply refuses while the
  service is up (and, symmetrically, the service fails closed at startup if an apply holds
  the lock). Run `systemctl --user stop lhpc-web`, then `lhpc self-update`, then start it
  again.
- **A dirty checkout blocks apply** unless you explicitly choose overwrite.
- **Dependency changes need a manual sync.** When an update reports `deps_changed`
  (`pyproject.toml` changed), run the editable install yourself before restarting:
  ```bash
  ~/loraham-pi-control/venv/lhpc/bin/python -m pip install -e ~/loraham-pi-control/src/loraham-pi-control
  ```
- The unit is **operator-managed** — self-update never rewrites or restarts it for you.

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
- **Hardening**: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only` with
  `ReadWritePaths=%h/loraham-pi-control` (the runtime root is the only writable path),
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, and related restrictions.
- **`PrivateTmp=false`** — deliberately: the console must see the daemon's shared Unix
  sockets in `/tmp` (`/tmp/loraconf*.sock`, `/tmp/lora*.sock`). A private `/tmp` would hide
  them and break status/monitor.

## Security boundary

The console is **loopback-only by design**. Remote access is **not** provided by this unit
and must never be obtained by binding a public address. Exposing it remotely requires a
separate, explicit design: authenticated **HTTPS** and/or a trusted **reverse proxy** with
its own access control — future work, opt-in, and out of scope here.
