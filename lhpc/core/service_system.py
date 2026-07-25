"""Host system metrics for the dashboard System box.

Stateless by design: `system_stats()` returns RAW counters plus a monotonic timestamp and the
BROWSER computes rates between its own polls — no server-side history or sampler (SD-card wear),
no psutil, and NO subprocess of any kind (the route is GET-safe under P0.6 by construction: every
read goes through the injected `System.fs`, so a fake drives it entirely in tests).

Every section is fail-soft: an absent/unreadable source omits its key. Unknown stays unknown —
in particular the Power section never synthesizes values its source cannot see.
"""

from __future__ import annotations

import os
import socket
import time

# Byte bounds for the procfs/sysfs reads. /proc/stat grows with core count; everything else is
# tiny. 64 KiB matches the established bounded-read size (RealProcFs.cmdlines).
_MAX_READ = 64 * 1024
_MAX_SMALL = 4 * 1024
_HWMON_DIR = "/sys/class/hwmon"
_HWMON_MAX_ENTRIES = 64        # scan bound: listdir is capped, never an unbounded walk


# --- pure parsers (string-in / data-out; unit-tested directly) -----------------------------------

def parse_proc_stat(text: str) -> dict | None:
    """First `cpu ` aggregate line -> {"total": [8 jiffy ints], "cores": N,
    "percore": [[8 jiffy ints] per cpuN line]}.

    `cores` is the count of per-core `cpuN` lines in the SAME text — deterministic and
    fake-drivable (never os.cpu_count(), which reads the host under test). A malformed
    per-core line is skipped (the aggregate stays authoritative)."""
    total = None
    percore = []
    for line in text.splitlines():
        if line.startswith("cpu "):
            fields = line.split()[1:]
            if len(fields) < 8:
                return None
            try:
                total = [int(f) for f in fields[:8]]
            except ValueError:
                return None
        elif line.startswith("cpu") and len(line) > 3 and line[3].isdigit():
            fields = line.split()[1:]
            try:
                if len(fields) >= 8:
                    percore.append([int(f) for f in fields[:8]])
            except ValueError:
                continue
    if total is None:
        return None
    return {"total": total, "cores": len(percore), "percore": percore}


def parse_meminfo(text: str) -> dict | None:
    """MemTotal/MemAvailable (fallback MemFree) + SwapTotal/SwapFree, all in kB."""
    vals: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and key in ("MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree"):
            try:
                vals[key] = int(parts[0])
            except ValueError:
                continue
    if "MemTotal" not in vals:
        return None
    available = vals.get("MemAvailable", vals.get("MemFree"))
    if available is None:
        return None
    return {"total_kb": vals["MemTotal"], "available_kb": available,
            "swap_total_kb": vals.get("SwapTotal", 0), "swap_free_kb": vals.get("SwapFree", 0)}


def parse_net_dev(text: str) -> dict | None:
    """Sum rx/tx byte counters over every interface except `lo`. Malformed lines are skipped.
    Returns None when no interface line parsed (unknown, not zero)."""
    rx = tx = 0
    seen = False
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep:
            continue                       # header lines have no colon
        if name.strip() == "lo":
            continue
        fields = rest.split()
        if len(fields) < 16:
            continue
        try:
            rx += int(fields[0])
            tx += int(fields[8])
        except ValueError:
            continue
        seen = True
    return {"rx_bytes": rx, "tx_bytes": tx} if seen else None


def parse_loadavg(text: str) -> list | None:
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except ValueError:
        return None


def parse_uptime(text: str) -> float | None:
    parts = text.split()
    if not parts:
        return None
    try:
        return float(parts[0])
    except ValueError:
        return None


def parse_os_release(text: str) -> str:
    """PRETTY_NAME value, quoted or unquoted; "" when absent."""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "PRETTY_NAME":
            return value.strip().strip('"').strip("'")
    return ""


def parse_dt_model(text: str) -> str:
    """/proc/device-tree/model is NUL-terminated."""
    return text.replace("\x00", "").strip()


# --- the mixin -----------------------------------------------------------------------------------

class SystemStatsMixin:
    """Read-only host metrics (`GET /api/system`). File reads only, via the injected System.fs."""

    def system_stats(self) -> dict:
        fs = self._system.fs
        out: dict = {"ts": time.monotonic()}

        cpu = parse_proc_stat(fs.read_text("/proc/stat", _MAX_READ))
        if cpu is not None:
            out["cpu"] = cpu
        load = parse_loadavg(fs.read_text("/proc/loadavg", _MAX_SMALL))
        if load is not None:
            out["load"] = load
        mem = parse_meminfo(fs.read_text("/proc/meminfo", _MAX_READ))
        if mem is not None:
            out["mem"] = mem
        net = parse_net_dev(fs.read_text("/proc/net/dev", _MAX_READ))
        if net is not None:
            out["net"] = net

        disk = self._disk_stats(fs)
        if disk:
            out["disk"] = disk

        temp_raw = fs.read_text("/sys/class/thermal/thermal_zone0/temp", _MAX_SMALL).strip()
        try:
            out["temp_mc"] = int(temp_raw)
        except ValueError:
            pass

        power = self._power_stats(fs)
        if power is not None:
            out["power"] = power

        uptime = parse_uptime(fs.read_text("/proc/uptime", _MAX_SMALL))
        if uptime is not None:
            out["uptime_s"] = uptime

        out["info"] = self._host_info(fs)
        return out

    def _disk_stats(self, fs) -> dict:
        """Root filesystem + the runtime root's filesystem; the runtime entry is omitted when it
        lives on the SAME filesystem as / (st_dev match) so the row is never a duplicate."""
        out: dict = {}
        root = fs.statvfs("/")
        if root is not None:
            out["root"] = {"total_b": root["total_b"], "free_b": root["free_b"]}
        runtime_path = str(self._paths.runtime_root)
        runtime = fs.statvfs(runtime_path)
        if runtime is not None and (root is None or runtime.get("dev") != root.get("dev")):
            out["runtime"] = {"path": runtime_path,
                              "total_b": runtime["total_b"], "free_b": runtime["free_b"]}
        return out

    def _power_stats(self, fs) -> dict | None:
        """Truthful Pi power state. The ONLY file-readable source on our fleet (verified live on
        the Pi 5 and the Zero 2W, 2026-07-25) is the `raspberrypi-hwmon` under-voltage alarm —
        the full `get_throttled` bitmask exists solely behind the vcgencmd mailbox, which is a
        subprocess and therefore forbidden on a GET. The alarm is a periodically-refreshed sticky
        bit: it can say "under-voltage (alarm)" but can NOT see throttling or frequency capping,
        so nothing beyond the alarm is ever synthesized (a future comprehensive source would
        declare itself via a different `source` value). None = no source -> key omitted."""
        for entry in fs.listdir(_HWMON_DIR, _HWMON_MAX_ENTRIES):
            base = f"{_HWMON_DIR}/{entry}"
            if fs.read_text(f"{base}/name", _MAX_SMALL).strip() != "rpi_volt":
                continue
            alarm_raw = fs.read_text(f"{base}/in0_lcrit_alarm", _MAX_SMALL).strip()
            if alarm_raw not in ("0", "1"):
                return None                # driver present but unreadable: unknown stays unknown
            out = {"source": "hwmon-alarm", "undervolt_alarm": alarm_raw == "1"}
            # Newer kernels (raspberrypi-hwmon voltage patch, queued for Linux 7.2) additionally
            # expose the CORE voltage as in0_input (millivolts). Include it when present — the
            # display self-activates once the fleet's kernel ships it. Note: this is the core
            # rail (~0.85 V), not the 5 V supply; the 5 V figure stays firmware-mailbox-only.
            core_raw = fs.read_text(f"{base}/in0_input", _MAX_SMALL).strip()
            try:
                out["core_mv"] = int(core_raw)
            except ValueError:
                pass
            return out
        return None

    def _host_info(self, fs) -> dict:
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = ""
        uname = os.uname()
        return {"hostname": hostname,
                "model": parse_dt_model(fs.read_text("/proc/device-tree/model", _MAX_SMALL)),
                "os": parse_os_release(fs.read_text("/etc/os-release", _MAX_SMALL)),
                "kernel": uname.release, "arch": uname.machine}
