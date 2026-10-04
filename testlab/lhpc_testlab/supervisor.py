"""Stateful lab supervisor: the unit-state model behind the simulated `systemctl --user`,
the simulated boot identity, and (for `lhpc-nginx`) a driver that runs the REAL nginx
binary unprivileged so webserver apply and the stack proxies genuinely work.

Unit state lives in `state/testlab/units.json`; mutating verbs TRANSITION it (never
no-op) so enable/start/stop/restart flows succeed and read back consistently.
"""
from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import time
import uuid as _uuid
from pathlib import Path

from lhpc.core import runtime_fs

from . import scenarios


def _units_path(paths) -> Path:
    return paths.under("state", "testlab", "units.json")


def load_units(paths) -> dict:
    try:
        st = json.loads(runtime_fs.read_text_regular(paths, _units_path(paths),
                                                     max_bytes=1 << 20) or "")
        if isinstance(st, dict):
            return st
    except Exception:
        pass
    return {}


def save_units(paths, st: dict) -> None:
    runtime_fs.atomic_write(paths, _units_path(paths), json.dumps(st, indent=1), 0o600)


def unit(paths, name: str) -> dict:
    return load_units(paths).get(name, {"active": False, "enabled": False})


def set_unit(paths, name: str, *, active=None, enabled=None) -> dict:
    st = load_units(paths)
    rec = st.get(name, {"active": False, "enabled": False})
    if active is not None:
        rec["active"] = bool(active)
    if enabled is not None:
        rec["enabled"] = bool(enabled)
    st[name] = rec
    save_units(paths, st)
    return rec


# ---- simulated boot identity ---------------------------------------------------------


def boot_file(paths) -> Path:
    return paths.under("state", "testlab", "host", "boot_id")


def epoch_file(paths) -> Path:
    return paths.under("state", "testlab", "host", "boot_epoch")


def ensure_boot_identity(paths) -> str:
    """Create the simulated boot id + epoch if absent; returns the boot id."""
    bf = boot_file(paths)
    if not bf.exists():
        advance_boot(paths, reason="init")
    try:
        return bf.read_text().strip()
    except OSError:
        return ""


def advance_boot(paths, reason: str = "reboot") -> str:
    """A new simulated boot: fresh boot id, uptime epoch reset to now."""
    new = str(_uuid.uuid4())
    runtime_fs.atomic_write(paths, boot_file(paths), new + "\n", 0o600)
    runtime_fs.atomic_write(paths, epoch_file(paths),
                            f"{time.clock_gettime(time.CLOCK_BOOTTIME):.3f}\n", 0o600)
    scenarios.log_event(paths, f"boot identity advanced ({reason})")
    return new


def sim_uptime(paths) -> float:
    """Seconds since the simulated boot; falls back to the real uptime on any problem."""
    try:
        epoch = float(runtime_fs.read_text_regular(paths, epoch_file(paths),
                                                   max_bytes=64).strip())
        up = time.clock_gettime(time.CLOCK_BOOTTIME) - epoch
        # The stored epoch is rounded (%.3f may round UP), so a just-reset boot can
        # read a few hundred microseconds "in the future" — clamp, don't fall back.
        if up >= -1.0:
            return max(0.0, up)
    except Exception:
        pass
    return time.clock_gettime(time.CLOCK_BOOTTIME)


# ---- the real-nginx driver -----------------------------------------------------------


def _nginx_pid(paths) -> int:
    # The production renderer's own pid directive: state/run/nginx.pid (webserver.py
    # NGINX_PID). The lab runs the SAME rendered config the production unit would.
    try:
        return int((paths.under("state", "run", "nginx.pid")).read_text().strip())
    except (OSError, ValueError):
        return 0


def pid_alive(pid: int, needle: str) -> bool:
    """`pid` is a live process whose /proc cmdline contains `needle`. A pid file alone can name a
    REUSED pid (a stale file left when a Codespace stopped): `kill(pid, 0)` called any process of
    this user alive, and a stop then signalled it."""
    if pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return needle in cmd


def _nginx_master(paths) -> int:
    """The pid in the lab's nginx pid file when it is OUR nginx master, else 0. Verified, not
    guessed: the process name (`comm`) is `nginx`, its title is the master's ("nginx: master
    process …") and it carries `-c <the lab's config>` as one argument pair. A reused pid whose
    command line merely contains the config path (an editor open on the file) is not it. A config
    path containing whitespace is never verified (the title is space-joined), so it reads as no
    master and is never signalled."""
    pid = _nginx_pid(paths)
    if pid <= 0:
        return 0
    conf = str(paths.under("config", "nginx", "lhpc.conf"))
    try:
        with open(f"/proc/{pid}/comm", "rb") as fh:
            comm = fh.read().decode("utf-8", "replace").strip()
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            title = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return 0
    tokens = title.split()
    if (comm == "nginx" and title.startswith("nginx: master process ")
            and any(a == "-c" and b == conf for a, b in itertools.pairwise(tokens))):
        return pid
    return 0


def _nginx_alive(paths) -> bool:
    return _nginx_master(paths) > 0


def _nginx_unverified(paths) -> int:
    """The pid in the lab's nginx pid file when a live `nginx` runs there that `_nginx_master`
    cannot verify as ours (a config path with whitespace), else 0 — up, but never signalled."""
    pid = _nginx_pid(paths)
    if pid <= 0 or _nginx_master(paths):
        return 0
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:          # a zombie is gone, not up
            if fh.read().rsplit(b")", 1)[1].split()[0] == b"Z":
                return 0
        with open(f"/proc/{pid}/comm", "rb") as fh:
            return pid if fh.read().decode("utf-8", "replace").strip() == "nginx" else 0
    except (OSError, IndexError):
        return 0


def nginx_ctl(paths, verb: str) -> tuple[bool, str]:
    """start/stop/restart/reload the REAL nginx exactly as the production user unit
    does (`nginx -c <root>/config/nginx/lhpc.conf` — pid/logs/temp paths come from the
    rendered config itself, all under the runtime root; ports are >1024 so no root).
    Returns (ok, detail)."""
    conf = paths.under("config", "nginx", "lhpc.conf")
    if verb in ("start", "restart", "reload") and not conf.exists():
        return False, f"no rendered nginx config at {conf}"
    if verb in ("stop", "restart"):
        # RE-REVIEW: escalate until the old master is PROVEN gone — success was
        # reported while it kept serving the old config. SIGQUIT (graceful) ->
        # SIGTERM -> SIGKILL, each with a bounded wait; still alive = honest failure.
        # Each signal goes to the pid verified as our master just before it, never to the
        # pid file's bare content.
        pid = _nginx_pid(paths)
        if pid > 0:
            for sig, waits in ((signal.SIGQUIT, 40), (signal.SIGTERM, 40),
                               (signal.SIGKILL, 20)):
                master = _nginx_master(paths)
                if not master:
                    break
                try:
                    os.kill(master, sig)
                except OSError:
                    break
                for _ in range(waits):
                    if not _nginx_alive(paths):
                        break
                    time.sleep(0.05)
        if _nginx_alive(paths):
            return False, f"old nginx master (pid {pid}) would not exit"
        if (u := _nginx_unverified(paths)):
            return False, (f"nginx (pid {u}) is not verified as the lab's master (its config path "
                           "cannot be matched) — not signalled; stop it by hand")
        if verb == "stop":
            return True, "stopped"
    if verb == "reload" and (master := _nginx_master(paths)):
        try:
            os.kill(master, signal.SIGHUP)
            return True, "reloaded"
        except OSError:
            pass                                    # fall through to a fresh start
    if _nginx_alive(paths):
        return True, "already running"
    try:
        r = subprocess.run(["nginx", "-c", str(conf)], capture_output=True, text=True,
                           timeout=20, check=False)
    except FileNotFoundError:
        return False, "nginx binary not installed"
    except subprocess.TimeoutExpired:
        return False, "nginx start timed out"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:300]
    return True, "started"
