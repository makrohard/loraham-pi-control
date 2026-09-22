"""The openHop plugin manager as part of the repeater: a child process of this host.

Upstream's dashboard reaches its plugin manager over a Unix socket that a SEPARATE process,
``python -m repeater.plugins``, serves; natively that process is a root systemd unit installed by
``manage.sh``. LHPC runs the repeater rootless, so this host spawns the manager itself, in the
repeater roles, the way upstream's own container entry does — one lifecycle, one process boundary.

What this module deliberately does NOT do (docs/stacks/meshcore.md, "Plugins"):

* it never restarts a manager that died on its own, and it never reaps plugin processes. Upstream
  launches every plugin in its OWN session, so a manager that died without its graceful shutdown
  may leave plugins running, and a replacement manager would start the persisted-enabled plugins
  AGAIN beside them. Instead a same-boot marker records "a manager owned plugins in this boot and
  did not shut down cleanly", and no manager is launched again until the box has rebooted;
* it never spoofs a container, never installs a unit, never touches the socket file (upstream
  creates, unlinks and binds it), never reads or writes the plugins root.

The marker protocol (fail-closed; the controller reads the same file through
``lhpc.core.meshcore_plugins`` — the two verdict functions are kept in lockstep by a test):

    absent                     -> write the current-boot marker, THEN spawn
    valid, same boot           -> do not spawn ("previous shutdown was unclean; reboot")
    valid, another boot        -> replace it with the current-boot marker, spawn
    malformed / unreadable     -> cannot prove safe: do not spawn, leave the file as found
    boot id unavailable        -> cannot prove safe: do not spawn
    marker write fails         -> do not spawn (a manager never runs without its crash guard)
    manager rc == 0            -> clear the marker (upstream's SIGTERM path returns 0 after
                                  `stop_all()`); a failed unlink keeps it, conservatively
    manager rc != 0 / signal   -> keep the marker
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("meshcore-host.plugins")

MARKER_NAME = ".lhpc-plugin-manager-active"
MARKER_VERSION = 1
TERM_GRACE_S = 8.0        # upstream's container supervisor: SIGTERM, then this long, then SIGKILL
KILL_GRACE_S = 2.0
WATCH_POLL_S = 0.5        # an LHPC choice (upstream's supervisor polls at 0.2 s)
REBOOT_HINT = "plugins will not be started again until the box is rebooted"


def boot_id() -> str:
    """The kernel's per-boot UUID; "" when unreadable. A faithful copy of
    ``lhpc.core.lifecycle.current_boot_id()`` — this package runs in the stack's venv, where
    ``lhpc`` is not importable — including its ``$LHPC_BOOT_ID_FILE`` test seam, so a simulated
    reboot advances "this boot" for the controller and the host alike. Empty = "cannot prove"."""
    boot_file = os.environ.get("LHPC_BOOT_ID_FILE")
    if boot_file:
        try:
            with open(boot_file, "rb") as fh:
                return fh.read(64).decode("ascii", errors="replace").strip()
        except OSError:
            pass
    try:
        with open("/proc/sys/kernel/random/boot_id", "rb") as fh:
            return fh.read(64).decode("ascii", errors="replace").strip()
    except OSError:
        return ""


# LOCKSTEP with lhpc/core/meshcore_plugins.py::marker_verdict — same text, same verdicts.
def marker_verdict(path: Path | str, current_boot: str) -> str:
    """One of "absent" (no marker), "safe" (a marker from another boot: its processes cannot have
    survived), "unsafe" (a marker from THIS boot: an unclean manager may still own plugins), or
    "unprovable" (malformed, unreadable, unknown version, or no boot id to compare against)."""
    if not current_boot:
        return "unprovable"
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unprovable"
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "unprovable"
    if not isinstance(rec, dict) or rec.get("version") != MARKER_VERSION:
        return "unprovable"
    recorded = rec.get("boot_id")
    if not isinstance(recorded, str) or not recorded:
        return "unprovable"
    return "unsafe" if recorded == current_boot else "safe"


def write_marker(path: Path, current_boot: str) -> None:
    """Atomic: a sibling temp file, then ``os.replace``. Creates the parent (the repeater's
    state dir — upstream tolerates a pre-existing empty one). Raises OSError on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"version": MARKER_VERSION, "boot_id": current_boot}) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def clear_marker(path: Path) -> bool:
    """Idempotent: an already-missing marker is "clear". False only when the unlink FAILED —
    then the file stays and the next same-boot start conservatively refuses."""
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning("Plugin-manager marker could not be cleared (%s): %s — the next start in "
                       "this boot will refuse to launch a manager", path, exc)
        return False
    return True


def paths_for(state_dir: str | Path) -> tuple[Path, Path]:
    """(plugins root, IPC socket) exactly as upstream's dashboard resolves them for the same
    storage dir — including its fallback to a hashed socket under the temp dir when the path
    would exceed AF_UNIX's limit. Imports the pinned package: an ImportError here is an
    integration failure (the wrong venv), not a runtime one."""
    from repeater.plugins.storage import resolve_plugin_socket_path, resolve_plugins_root
    root = Path(resolve_plugins_root({}, storage_dir=str(state_dir)))
    sock = Path(resolve_plugin_socket_path({}, storage_dir=str(state_dir)))
    return root, sock


def argv_for(python: str, plugins_root: Path, socket: Path) -> list[str]:
    return [python, "-m", "repeater.plugins", "--plugins-root", str(plugins_root),
            "--socket", str(socket), "--log-level", "INFO"]


class PluginManagerChild:
    """Spawn, watch and stop upstream's plugin manager for one repeater run.

    The child stays in THIS process group (no new session): LHPC's stack stop signals the whole
    group, so the manager receives the SIGTERM even if this host's own cleanup never runs. The
    plugins the manager starts keep the separate sessions upstream gives them."""

    def __init__(self, state_dir: str | Path, *,
                 popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
                 boot_id_fn: Callable[[], str] = boot_id,
                 python: str = sys.executable) -> None:
        self.state_dir = Path(state_dir)
        self.marker = self.state_dir / MARKER_NAME
        self.plugins_root, self.socket = paths_for(self.state_dir)   # ImportError -> caller
        self.argv = argv_for(python, self.plugins_root, self.socket)
        self._popen = popen_factory
        self._boot_id = boot_id_fn
        self.proc: Optional[subprocess.Popen] = None
        self._reaped = False

    # ---- start ---------------------------------------------------------------------------
    def start(self) -> bool:
        """Spawn the manager behind the marker protocol. False = the plugin subsystem stays
        offline for this run (the reason is logged once); the repeater is unaffected."""
        current = self._boot_id()
        verdict = marker_verdict(self.marker, current)
        if verdict == "unsafe":
            logger.error("Plugin manager not started: the previous plugin-manager shutdown in this "
                         "boot was unclean (%s) — %s", self.marker, REBOOT_HINT)
            return False
        if verdict == "unprovable":
            logger.error("Plugin manager not started: cannot prove the plugin-manager state "
                         "(marker %s unreadable/malformed, or no boot id) — %s",
                         self.marker, REBOOT_HINT)
            return False
        if verdict == "safe":
            logger.info("Plugin-manager marker from an earlier boot found; replacing it")
        try:
            write_marker(self.marker, current)
        except OSError as exc:
            logger.error("Plugin manager not started: could not write its marker %s: %s",
                         self.marker, exc)
            return False
        try:
            self.proc = self._popen(self.argv, start_new_session=False)
        except OSError as exc:
            logger.error("Plugin manager could not be spawned (%s): %s — plugins stay offline for "
                         "this run", self.argv[0], exc)
            clear_marker(self.marker)                     # nothing runs: no ownership to record
            return False
        logger.info("Plugin manager started (pid %s): root %s, socket %s",
                    self.proc.pid, self.plugins_root, self.socket)
        return True

    # ---- the one clean/unclean rule ------------------------------------------------------
    def _reap(self, rc: int) -> None:
        if self._reaped:
            return
        self._reaped = True
        if rc == 0:
            # upstream's SIGTERM/SIGINT path: stop flag -> server.stop() -> stop_all() -> return 0
            logger.info("Plugin manager stopped")
            clear_marker(self.marker)
        else:
            logger.error("Plugin manager exited (code %s) — %s", rc, REBOOT_HINT)

    # ---- watch ---------------------------------------------------------------------------
    async def watch(self) -> None:
        """Log ONE line when the manager exits on its own; never restart it."""
        proc = self.proc
        if proc is None:
            return
        while proc.poll() is None:
            await asyncio.sleep(WATCH_POLL_S)
        self._reap(proc.returncode)

    # ---- stop ----------------------------------------------------------------------------
    def stop(self) -> None:
        """SIGTERM, wait TERM_GRACE_S, SIGKILL, wait KILL_GRACE_S — upstream's shutdown numbers,
        applied to the process (not a group). Blocking by design; bounded by construction."""
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=TERM_GRACE_S)
            except subprocess.TimeoutExpired:
                logger.warning("Plugin manager did not stop within %.0f s; killing it", TERM_GRACE_S)
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=KILL_GRACE_S)
                except subprocess.TimeoutExpired:
                    logger.error("Plugin manager did not die after SIGKILL — %s", REBOOT_HINT)
                    self._reaped = True                   # marker stays; nothing more to say
                    return
        self._reap(proc.returncode if proc.returncode is not None else -1)


__all__ = ["MARKER_NAME", "MARKER_VERSION", "PluginManagerChild", "argv_for", "boot_id",
           "clear_marker", "marker_verdict", "paths_for", "write_marker"]
