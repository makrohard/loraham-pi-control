"""Read-only host prerequisite checks for `lhpc doctor`.

Reports presence/accessibility of relevant host facilities. It NEVER initializes
a radio or opens SPI/GPIO for operation — it only checks that device nodes and
tools exist, that `systemctl` (system and user scope) responds, and whether the
runtime root and configured source paths are present.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import System

_TIMEOUT_S = 3.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class ProbeResult:
    """Outcome of a bounded radio hardware probe (see `probe_radio`)."""
    present: bool
    busy: bool
    message: str
    diagnostic: str = ""


# Substrings the v112 daemon prints (loraham_daemon.cpp / daemon_io_runtime.cpp /
# sx127x_driver.cpp / sx1262_driver.cpp). A begin() failure prints a chip diagnostic then exits.
_NOT_READY = "Kein ausgewähltes Radio bereit"
_CHIP_MARKERS = ("antwortet nicht", "antwortet mit ID", "keine Antwort auf CS",
                 "falsche Chip-Familie", "falsches Profil", "CHIP_NOT_FOUND")
_EXIT_INSTANCE_BUSY = 3          # LORAHAM_EXIT_INSTANCE_BUSY — the band is already served


def _first_marker_line(text: str, markers) -> str:
    for line in text.splitlines():
        if any(m in line for m in markers):
            return line.strip()
    return ""


def probe_radio(system: System, binary: str, cwd: str, band: str, hw_preset: str, *,
                runtime_dir: str, timeout: float = 4.0, label: str = "",
                socket_dir: str = "/tmp") -> ProbeResult:
    """Bounded hardware probe: spawn the daemon for (band, hw_preset) and observe whether the radio
    comes up. On SUCCESS the daemon runs PAST init (it never exits), so the bounded runner TIMES OUT
    and terminates it — the board's LED lights during init as visual confirmation. On failure the
    daemon exits fast with a chip diagnostic. It NEVER clobbers a real daemon: pointing at the same
    runtime lock dir makes an already-served band exit BUSY (code 3) instead of stealing the radio.
    `label` is the friendly board name used in messages (defaults to the raw preset).

    `socket_dir` must match the daemon's direct-start socket dir (LORAHAM_SOCKET_DIR = /tmp in the
    manifest run_env): the v112 daemon defaults sockets to /run/loraham and does NOT create the dir,
    so without this the probe daemon fails at socket-open BEFORE begin() and every probe reads as
    'not detected'. Socket-open is past the instance lock, so a real daemon still trips BUSY first."""
    name = label or hw_preset
    argv = [binary, "--radio", band, "--hw", hw_preset, "--debug"]
    res = system.runner.run(argv, timeout=timeout, cwd=cwd,
                            env={"LORAHAM_RUNTIME_DIR": runtime_dir,
                                 "LORAHAM_SOCKET_DIR": socket_dir})
    if res.not_found:
        return ProbeResult(False, False, "daemon binary not found — build the daemon first")
    text = (res.stdout or "") + "\n" + (res.stderr or "")
    if res.returncode == _EXIT_INSTANCE_BUSY and not res.timed_out:
        return ProbeResult(False, True,
                           f"{band} MHz is already in use — stop the daemon on this band first")
    # Stayed up past init (never exited) and no explicit not-ready line -> the radio initialised.
    if res.timed_out and _NOT_READY not in text:
        return ProbeResult(True, False,
                           f"{name} responded on {band} MHz — radio ready (LED lit during init)")
    # Exited fast, or printed the not-ready line -> absent / wrong board; surface the daemon's own
    # chip diagnostic when it gave one.
    return ProbeResult(False, False, f"no {name} radio detected on {band} MHz",
                       diagnostic=_first_marker_line(text, _CHIP_MARKERS))


def check_char_device(system: System, path: str) -> Check:
    if not system.fs.exists(path):
        return Check(path, False, "absent")
    if system.fs.is_char_device(path):
        return Check(path, True, "present (character device)")
    return Check(path, False, "present but not a character device")


def check_systemctl(system: System, user: bool) -> Check:
    argv = ["systemctl"]
    if user:
        argv.append("--user")
    argv.append("is-system-running")
    res = system.runner.run(argv, timeout=_TIMEOUT_S)
    label = "systemctl --user" if user else "systemctl"
    if res.not_found:
        return Check(label, False, "not found")
    if res.timed_out:
        return Check(label, False, "timeout")
    stderr = res.stderr.lower()
    if "failed to connect to" in stderr and "bus" in stderr:
        return Check(label, False, "no bus / unavailable")
    # is-system-running may exit non-zero (e.g. "degraded") yet still prove the
    # manager responds; treat any parseable word as "responds".
    word = res.stdout.strip() or res.stderr.strip()
    return Check(label, True, f"responds ({word or 'ok'})")
