"""Canonical systemd user units for the managed web console + one-click self-update, and the
authoritative proof that a deployment's installed units are exactly the vetted ones.

ONE source of truth (`render`) feeds: the shipped `deploy/*.service`/`*.path` templates (with
`%h/...` paths), the per-target units `install.sh`/the repair op write (literal paths), and the
integrity proof (`verify`). Because the one-click updater executes checkout code with elevated
reach, "is this the unit we vetted?" is a security question — so the fixed units are proven
BYTE-EXACT (a customized web unit could otherwise silently drop the sandbox / bus block).

The trigger has NO user-systemd bus path: the web writes an in-root request marker, a static
`.path` unit starts the sandboxed helper, and web stop/restart is declarative
(`Conflicts=/After=/OnSuccess=/OnFailure=`). Neither the web nor the helper calls `systemctl`;
`InaccessiblePaths=%t/bus %t/systemd/private` makes the user bus unreachable to both (verified:
`systemctl --user`, `systemd-run --user`, and `--machine=@.host` all fail).
"""

from __future__ import annotations

import errno as _errno
import os
import stat as _stat
from pathlib import Path

# --- unit names (fixed; the web console + one-click integration is exactly these three) -------
WEB_UNIT = "lhpc-web.service"
HELPER_UNIT = "lhpc-selfupdate.service"
PATH_UNIT = "lhpc-selfupdate.path"
ALL_UNITS = (WEB_UNIT, HELPER_UNIT, PATH_UNIT)

# in-root request-transaction paths (relative to the runtime root)
REQUEST_REL = ("state", "selfupdate.request")
INFLIGHT_REL = ("state", "selfupdate.inflight")
UNINSTALL_GUARD = ".lhpc-uninstalling"
ROOT_MARKER = ".lhpc-root"

_MAX_UNIT_BYTES = 64 * 1024
# user-unit drop-in search dirs (a `<unit>.d/` in ANY of these overrides the fragment)
_DROPIN_DIRS = ("/usr/lib/systemd/user", "/etc/systemd/user")   # ~/.config/systemd/user added per call


# --------------------------------------------------------------------------- canonical render

_WEB = """\
# LoRaHAM Pi Control web console — CANONICAL managed unit (generated; do not hand-edit).
# Rendered by lhpc.core.updater_units; the one-click updater proves this file BYTE-EXACT.
# Restore with `lhpc self-update --repair-integration` after any change.
[Unit]
Description=LoRaHAM Pi Control web console (loopback-only)
Documentation=file://{checkout}/docs/deployment.md
After=network-online.target
Wants=network-online.target {path_unit}
# Refuse to (re)start mid-uninstall — the updater's OnFailure= must not resurrect the console
# while a teardown is removing this deployment.
ConditionPathExists=!{root}/{guard}
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
Environment=LHPC_RUNTIME_ROOT={root}
WorkingDirectory={checkout}
ExecStart={venv}/bin/lhpc web --host 127.0.0.1 --port 8770
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=lhpc-web
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
# runtime root + /tmp for lhpc itself; %h/.meshcore_nm because a stack GUI started under this
# unit (meshcore-nodegui) keeps its sessions/settings/favourites there. %h expanded by systemd.
ReadWritePaths={root} %h/.meshcore_nm /tmp
Environment=PLATFORMIO_CORE_DIR={root}/build/tool-cache/platformio
Environment=IDF_TOOLS_PATH={root}/build/tool-cache/espressif
Environment=XDG_CACHE_HOME={root}/build/tool-cache/cache
Environment=PIP_CACHE_DIR={root}/build/tool-cache/pip
# Close the user-systemd escape: checkout code runs in this process, so deny it the user bus.
# (systemctl --user / systemd-run --user / --machine=@.host all fail; AF_UNIX + journald stay.)
InaccessiblePaths=%t/bus %t/systemd/private
PrivateTmp=false
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK AF_BLUETOOTH
RestrictNamespaces=true
LockPersonality=true
# MemoryDenyWriteExecute is OMITTED here — QEMU's TCG JIT (meshcom) needs W+X. The updater
# helper (no QEMU) DOES set it.
SystemCallArchitectures=native

[Install]
WantedBy=default.target
"""

_HELPER = """\
# LoRaHAM Pi Control self-update helper — CANONICAL managed unit (generated; do not hand-edit).
# Started ONLY by lhpc-selfupdate.path when the web writes a request marker. Parameter-free:
# the normal/overwrite mode is read from the claimed request. No systemctl calls anywhere.
[Unit]
Description=LoRaHAM Pi Control self-update (stop console, update, restart)
Documentation=file://{checkout}/docs/deployment.md
# Only the .path may start this; a manual `systemctl start` is refused (protects the console
# from an errant Conflicts= stop).
RefuseManualStart=yes
# Declarative console stop/restart — the helper never calls systemctl. Conflicts+After stops
# the console (freeing its shared controller-runtime lock) BEFORE the update; OnSuccess/OnFailure
# brings it back on EVERY terminal state (success, failure, timeout, kill).
Conflicts=lhpc-web.service
After=lhpc-web.service
OnSuccess=lhpc-web.service
OnFailure=lhpc-web.service
# Belt-and-braces: never run without a request present.
ConditionPathExists={root}/state/selfupdate.request

[Service]
Type=oneshot
Environment=LHPC_RUNTIME_ROOT={root}
WorkingDirectory={checkout}
ExecStart={venv}/bin/lhpc self-update --run-service
TimeoutStartSec=900
StandardOutput=journal
StandardError=journal
SyslogIdentifier=lhpc-selfupdate
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={root} /tmp
Environment=PIP_CACHE_DIR={root}/build/tool-cache/pip
InaccessiblePaths=%t/bus %t/systemd/private
PrivateTmp=false
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=true
LockPersonality=true
# The helper runs only git/pip/CPython (no QEMU) — W^X memory is enforced.
MemoryDenyWriteExecute=true
SystemCallArchitectures=native
"""

_PATH = """\
# LoRaHAM Pi Control self-update request watcher — CANONICAL managed unit (generated).
# Pulled up by lhpc-web.service (Wants=), so a running console always has an active watcher.
[Unit]
Description=LoRaHAM Pi Control self-update request watcher
Documentation=file://{checkout}/docs/deployment.md

[Path]
PathExists={root}/state/selfupdate.request
Unit=lhpc-selfupdate.service

[Install]
WantedBy=default.target
"""

_TEMPLATES = {WEB_UNIT: _WEB, HELPER_UNIT: _HELPER, PATH_UNIT: _PATH}


def render(kind: str, root: str, checkout: str, venv: str) -> str:
    """The canonical unit text for `kind` (one of ALL_UNITS). `root`/`checkout`/`venv` may be
    literal absolute paths (installed units) or `%h/...` specifiers (shipped templates)."""
    tmpl = _TEMPLATES.get(kind)
    if tmpl is None:
        raise ValueError(f"unknown unit kind {kind!r}")
    return tmpl.format(root=root, checkout=checkout, venv=venv,
                       path_unit=PATH_UNIT, guard=UNINSTALL_GUARD)


def deployment_paths(root: str) -> tuple[str, str, str]:
    """Canonical (root, checkout, venv) for a self-hosted deployment rooted at `root`."""
    return root, f"{root}/src/loraham-pi-control", f"{root}/venv/lhpc"


# --------------------------------------------------------------------------- verify / classes

# verify() verdicts (exactly one per unit)
OK = "ok"                 # byte-exact canonical, no drop-in, no symlinked dir
MISSING = "missing"
MODIFIED_OURS = "modified_ours"   # not byte-exact but carries THIS root's provenance
FOREIGN = "foreign"       # provenance names another runtime root
AMBIGUOUS = "ambiguous"   # neither clearly ours nor clearly foreign
OVERRIDDEN = "overridden"  # a <unit>.d/ drop-in exists somewhere in the search path
UNSAFE = "unsafe"         # unit file or a unit/drop-in dir is a symlink
UNREADABLE = "unreadable"


def _read_unit(unit_path: Path) -> str | None:
    """Read a unit file no-follow, bounded. None on absent; raises on symlink/oversized (caller
    maps to UNSAFE/UNREADABLE)."""
    fd = os.open(str(unit_path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            raise OSError("not a regular file")
        if st.st_size > _MAX_UNIT_BYTES:
            raise OSError("unit file too large")
        return os.read(fd, _MAX_UNIT_BYTES + 1).decode("utf-8")
    finally:
        os.close(fd)


def _dir_is_symlink(p: Path) -> bool:
    try:
        return _stat.S_ISLNK(os.lstat(str(p)).st_mode)
    except OSError:
        return False


def _has_dropin(user_dir: Path, name: str) -> bool:
    """A `<name>.d/` drop-in dir in the user unit dir or any system search dir → the loaded
    fragment is NOT solely our file."""
    for base in (user_dir, *(Path(d) for d in _DROPIN_DIRS)):
        d = base / f"{name}.d"
        try:
            if _stat.S_ISLNK(os.lstat(str(d)).st_mode):
                return True                                  # a symlinked drop-in dir is unsafe too
            if _stat.S_ISDIR(os.stat(str(d)).st_mode):
                # only count it if it actually holds a .conf
                if any(f.endswith(".conf") for f in os.listdir(str(d))):
                    return True
        except OSError:
            continue
    return False


def _expand_h(value: str, home: str) -> str:
    """Expand a LEADING systemd `%h` specifier (the user's home) in a single provenance VALUE, so
    a `%h`-spelled unit is recognized as this deployment's. Only the specific provenance fields are
    passed here — never the whole unit text."""
    return home + value[2:] if value.startswith("%h") else value


def _classify_mismatch(kind: str, text: str, root: str) -> str:
    """A non-byte-exact unit: clearly OURS (this runtime root — literal OR a `%h` spelling that
    expands to it), clearly FOREIGN (a DIFFERENT expanded root), or AMBIGUOUS (no/partial
    provenance). Only three provenance fields are `%h`-normalized for comparison —
    `Environment=LHPC_RUNTIME_ROOT=`, `ExecStart=.../venv/lhpc/bin/lhpc `, and (`.path`)
    `PathExists=.../state/selfupdate.request` — the rest of the unit is matched literally."""
    home = os.path.expanduser("~")
    lines = text.splitlines()
    _SUF = "/state/selfupdate.request"
    if kind == PATH_UNIT:
        watched = [ln[len("PathExists="):] for ln in lines
                   if ln.startswith("PathExists=") and ln.endswith(_SUF)]
        ours = ("Unit=lhpc-selfupdate.service" in lines
                and any(_expand_h(v[:-len(_SUF)], home) == root for v in watched))
        if ours:
            return MODIFIED_OURS
        if watched:                                      # a request-watch naming ANOTHER root
            return FOREIGN
        return AMBIGUOUS
    # web / helper services
    envs = [ln[len("Environment=LHPC_RUNTIME_ROOT="):] for ln in lines
            if ln.startswith("Environment=LHPC_RUNTIME_ROOT=")]
    execs = [ln[len("ExecStart="):] for ln in lines if ln.startswith("ExecStart=")]
    root_ours = any(_expand_h(v, home) == root for v in envs)
    exec_ours = any(_expand_h(v, home).startswith(f"{root}/venv/lhpc/bin/lhpc ") for v in execs)
    if root_ours and exec_ours:
        return MODIFIED_OURS
    if any(_expand_h(v, home) != root for v in envs):    # names a DIFFERENT expanded root
        return FOREIGN
    return AMBIGUOUS


def verify(user_dir: Path, kind: str, root: str, checkout: str, venv: str) -> str:
    """Classify the installed `kind` unit under `user_dir` against the canonical render for this
    deployment. Returns exactly one verdict constant."""
    unit_path = user_dir / kind
    if _dir_is_symlink(user_dir):
        return UNSAFE
    try:
        text = _read_unit(unit_path)
    except FileNotFoundError:
        return MISSING
    except OSError as exc:
        # ELOOP / symlink leaf (e.g. a mask → /dev/null) is O_NOFOLLOW-rejected → unsafe;
        # other read errors → unreadable.
        return UNSAFE if getattr(exc, "errno", None) in (_errno.ELOOP,) else UNREADABLE
    if text is None:
        return MISSING
    if _has_dropin(user_dir, kind):
        return OVERRIDDEN
    if text == render(kind, root, checkout, venv):
        return OK
    return _classify_mismatch(kind, text, root)


def integration(user_dir: Path, root: str) -> dict:
    """Aggregate one-click integration status for the deployment rooted at `root`. FILE READS
    ONLY (GET-safe; no subprocess, no bus). `status` is `ok` only when ALL three units are `ok`."""
    _, checkout, venv = deployment_paths(root)
    per = {k: verify(user_dir, k, root, checkout, venv) for k in ALL_UNITS}
    if any(v in (FOREIGN, OVERRIDDEN, UNSAFE) for v in per.values()):
        status = "foreign" if FOREIGN in per.values() else \
                 ("overridden" if OVERRIDDEN in per.values() else "unsafe")
    elif all(v == OK for v in per.values()):
        status = "ok"
    else:
        status = "incomplete"
    return {"status": status, "per_unit": per}


def write_set(user_dir: Path, root: str) -> list[tuple[str, str]]:
    """Write/refresh the canonical unit set (repair op). Overwrites ONLY `missing`/`modified_ours`
    units; REFUSES the WHOLE set if ANY unit is `foreign`/`ambiguous`/`overridden`/`unsafe`/
    `unreadable` (never overwrite a unit we cannot prove is safely ours). Returns [(name, action)];
    raises ValueError(typed) on refusal. Caller does the daemon-reload."""
    _, checkout, venv = deployment_paths(root)
    verdicts = {k: verify(user_dir, k, root, checkout, venv) for k in ALL_UNITS}
    blocking = {k: v for k, v in verdicts.items()
                if v in (FOREIGN, AMBIGUOUS, OVERRIDDEN, UNSAFE, UNREADABLE)}
    if blocking:
        detail = ", ".join(f"{k}: {v}" for k, v in blocking.items())
        raise ValueError(f"refusing to write updater units — existing unit(s) are not "
                         f"provably this deployment's: {detail}. Resolve them manually first.")
    user_dir.mkdir(parents=True, exist_ok=True)
    actions = []
    for kind in ALL_UNITS:
        if verdicts[kind] == OK:
            actions.append((kind, "unchanged"))
            continue
        text = render(kind, root, checkout, venv)
        tmp = user_dir / f".{kind}.tmp"
        with open(os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644),
                  "w") as fh:
            fh.write(text)
        os.replace(str(tmp), str(user_dir / kind))
        actions.append((kind, "written" if verdicts[kind] == MISSING else "restored"))
    return actions


# --------------------------------------------------------------------------- CLI (install.sh)

def _main(argv: list[str]) -> int:
    # `python -m lhpc.core.updater_units render <kind> <root> <checkout> <venv>` — install.sh
    # emits the exact canonical unit through the fresh venv (no heredoc duplication).
    if len(argv) == 6 and argv[1] == "render":
        import sys
        sys.stdout.write(render(argv[2], argv[3], argv[4], argv[5]))
        return 0
    import sys
    sys.stderr.write("usage: python -m lhpc.core.updater_units render <kind> <root> <checkout> <venv>\n")
    return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
