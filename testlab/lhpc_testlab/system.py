"""The LabSystem: the real System with the runner and filesystem wrapped.

Runner dispatch is a three-stage pipeline — DENY (typed refusal for direct host
mutators), SIMULATE (nmcli/busctl/systemctl/journalctl answered from lab state; the real
binaries are never reached), PASSTHROUGH (everything else runs for real: builds, git,
socat, venvs — the lab's stacks are real processes). procfs and unix stay real for the
same reason. LabFs adds the scenario overlay (forced-missing files for dependency
scenarios, forced-present tool probes, the simulated /proc/uptime).
"""
from __future__ import annotations

from lhpc.core.probes.backends import CommandResult, RealSystem, System

from . import nm, rules, scenarios, supervisor, sysd


def build_lab_system(paths) -> System:
    real = RealSystem()
    return System(runner=LabRunner(real.runner, paths),
                  procfs=real.procfs,
                  fs=LabFs(real.fs, paths),
                  unix=real.unix)


class LabRunner:
    def __init__(self, real, paths) -> None:
        self._real = real
        self._paths = paths


    def _dispatch(self, argv, kind: str, detail: str) -> CommandResult:
        if kind == "deny":
            scenarios.log_event(self._paths, f"DENIED: {' '.join(map(str, argv))[:200]}")
            return CommandResult(1, "", f"testlab: refused host-mutating command: "
                                        f"{detail}")
        handler = {"nmcli": nm.simulate,
                   "busctl": sysd.simulate_busctl,
                   "systemctl": sysd.simulate_systemctl,
                   "journalctl": sysd.simulate_journalctl}[detail]
        try:
            return handler(self._paths, list(argv))
        except Exception as exc:                    # a sim bug must not crash the app
            return CommandResult(1, "", f"testlab simulator error ({detail}): {exc}")

    def run(self, argv, timeout: float = 30.0, cwd=None, env=None) -> CommandResult:
        kind, detail = rules.classify(argv)
        if kind != "pass":
            return self._dispatch(argv, kind, detail)
        scenarios.log_command(self._paths, argv)
        return self._real.run(argv, timeout, cwd=cwd, env=env)

    def run_streaming(self, argv, timeout: float, log_fh, cwd=None, env=None,
                      redactor=None, should_cancel=None,
                      low_priority: bool = False) -> CommandResult:
        kind, detail = rules.classify(argv)
        if kind != "pass":
            res = self._dispatch(argv, kind, detail)
            try:
                log_fh.write((res.stdout + res.stderr).encode())
            except Exception:
                pass
            return res
        scenarios.log_command(self._paths, argv)
        return self._real.run_streaming(argv, timeout, log_fh, cwd=cwd,
                                        env=env, redactor=redactor,
                                        should_cancel=should_cancel,
                                        low_priority=low_priority)


class LabFs:
    """Real filesystem with the scenario overlay. exists/read_text/mtime consult the
    overlay, is_char_device and the group probes honor the simulated hardware; every
    other probe delegates.

    Simulated hardware surface: /dev/spidev0.0 reads present (and as a char device) and
    the effective/configured groups include spi+gpio, so the PRODUCTION start gates for
    SPI-bound stacks pass — the stacks themselves run against simulated radios
    (meshtasticd `Module: sim`), never a real bus."""

    # /usr/include/lgpio.h: liblgpio-dev exists only on Raspberry Pi OS — in the lab
    # the daemon is the fake (no compile against it), so the dependency row reads
    # satisfied; the hardware-missing scenario's missing_files still overrides this.
    _PRESENT = frozenset({"/usr/bin/nmcli", "/usr/bin/busctl", "/usr/bin/systemctl",
                          "/dev/spidev0.0", "/usr/include/lgpio.h"})
    _CHAR_DEVICES = frozenset({"/dev/spidev0.0"})
    _GROUPS = frozenset({"spi", "gpio"})

    def __init__(self, real, paths) -> None:
        self._real = real
        self._paths = paths

    def _missing(self, path: str) -> bool:
        flags = scenarios.effective_state(self._paths)
        return path in (flags.get("missing_files") or [])

    def exists(self, path: str) -> bool:
        if self._missing(path):
            return False
        if path in self._PRESENT:
            return True
        return self._real.exists(path)

    def read_text(self, path: str, max_bytes: int) -> str:
        if self._missing(path):
            return ""
        if path == "/proc/uptime":
            up = supervisor.sim_uptime(self._paths)
            return f"{up:.2f} {up:.2f}\n"[:max_bytes]
        return self._real.read_text(path, max_bytes)

    def mtime(self, path: str):
        if self._missing(path):
            return None
        return self._real.mtime(path)

    def is_socket(self, path: str) -> bool:
        return self._real.is_socket(path)

    def is_char_device(self, path: str) -> bool:
        if path in self._CHAR_DEVICES and not self._missing(path):
            return True
        return self._real.is_char_device(path)

    def effective_groups(self):
        return frozenset(self._real.effective_groups()) | self._GROUPS

    def configured_groups(self):
        return frozenset(self._real.configured_groups()) | self._GROUPS

    def statvfs(self, path: str):
        return self._real.statvfs(path)

    def listdir(self, path: str, max_entries: int):
        return self._real.listdir(path, max_entries)

    def readlink(self, path: str) -> str:
        return self._real.readlink(path)
