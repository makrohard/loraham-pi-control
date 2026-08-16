"""In-memory simulated host metrics for the demo's System box. Produces the RAW /proc
counter TEXT the real parsers (service_system.parse_*) consume, so the shapes can never
drift from the product — the demo just supplies plausible, time-advancing numbers (CPU
jiffies, net bytes and uptime GROW between polls so the browser computes live rates). No
/proc, no host reads."""
from __future__ import annotations

import math

_NCORES = 4
_UPTIME_BASE = 5 * 3600 + 1234          # a plausible "up 5h 20m" base
_MEM_TOTAL_KB = 8138240                 # ~8 GiB (Pi 5 8GB)
_SWAP_TOTAL_KB = 2097152                # 2 GiB zram/swap
_DISK_TOTAL_B = 31 * 10 ** 9           # a 32 GB card
_T0 = None


def _elapsed() -> float:
    global _T0
    try:
        import time
        t = time.time()                 # Pyodide maps this to Date.now()
    except Exception:
        t = 0.0
    if _T0 is None:
        _T0 = t
    return max(0.0, t - _T0)


def ts() -> float:
    try:
        import time
        return time.monotonic()
    except Exception:
        return _elapsed()


def proc_stat() -> str:
    """`cpu`/`cpuN` lines at 100 Hz since boot. The busy jiffies are the INTEGRAL of a
    slowly-varying per-core load, so the browser's poll-to-poll delta reads as the
    instantaneous load (~8–20 %) rather than the load's derivative."""
    up = _UPTIME_BASE + _elapsed()
    total = [0] * 8
    lines = []
    for c in range(_NCORES):
        period, base, amp = 6.0 + c, 0.14, 0.06          # load(t) = base + amp*sin(t/period)
        tot = int(up * 100)                              # USER_HZ jiffies since boot (all fields)
        # ∫ load dt = base*up - amp*period*cos(up/period); ×100 jiffies. Slope == load(up).
        busy = int(100 * (base * up - amp * period * math.cos(up / period)))
        busy = max(0, min(busy, tot))
        idle = tot - busy
        user, system = int(busy * 0.60), int(busy * 0.30)
        iowait, softirq = int(busy * 0.05), busy - int(busy * 0.60) - int(busy * 0.30) - int(busy * 0.05)
        fields = [user, 0, system, idle, iowait, 0, softirq, 0]
        lines.append(f"cpu{c} " + " ".join(map(str, fields)))
        for i in range(8):
            total[i] += fields[i]
    return "cpu " + " ".join(map(str, total)) + "\n" + "\n".join(lines) + "\n"


def loadavg() -> str:
    e = _elapsed()
    a = 0.35 + 0.25 * (0.5 + 0.5 * math.sin(e / 11.0))
    return f"{a:.2f} {a * 0.9:.2f} {a * 0.8:.2f} 1/312 {900 + int(e) % 50}\n"


def meminfo() -> str:
    e = _elapsed()
    used_frac = 0.28 + 0.06 * math.sin(e / 9.0)
    avail = int(_MEM_TOTAL_KB * (1 - used_frac))
    swap_free = int(_SWAP_TOTAL_KB * (0.96 + 0.03 * math.sin(e / 13.0)))
    return (f"MemTotal: {_MEM_TOTAL_KB} kB\nMemAvailable: {avail} kB\n"
            f"SwapTotal: {_SWAP_TOTAL_KB} kB\nSwapFree: {swap_free} kB\n")


def net_dev() -> str:
    """eth0 with the 16 rx/tx columns the parser needs (rx bytes = col 0, tx bytes = col 8),
    both counters GROWING so the browser derives a live throughput."""
    e = _elapsed()
    rx = int(1_200_000 + e * 4200 + 800 * math.sin(e / 3.0))
    tx = int(300_000 + e * 1500 + 400 * math.sin(e / 4.0))
    eth = f"{rx} 4100 0 0 0 0 0 0 {tx} 3300 0 0 0 0 0 0"
    return ("Inter-|   Receive                    |  Transmit\n"
            " face |bytes    packets ...           |bytes    packets ...\n"
            "  eth0: " + eth + "\n"
            "    lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n")


def uptime() -> str:
    e = _elapsed()
    up = _UPTIME_BASE + e
    return f"{up:.2f} {up * _NCORES * 0.85:.2f}\n"


def temp_mc() -> int:
    """SoC temperature in milli-°C, wandering ~44–52 °C."""
    e = _elapsed()
    return int(48000 + 4000 * math.sin(e / 7.0))


def disk() -> dict:
    e = _elapsed()
    root_free = int(_DISK_TOTAL_B * (0.56 + 0.01 * math.sin(e / 20.0)))
    return {"root": {"total_b": _DISK_TOTAL_B, "free_b": root_free},
            "runtime": {"path": "/home/loraham", "total_b": _DISK_TOTAL_B,
                        "free_b": root_free}}


def power() -> dict:
    return {"source": "hwmon-alarm", "undervolt_alarm": False, "core_mv": 5100}


def info() -> dict:
    return {"hostname": "loraham-demo", "model": "Raspberry Pi 5 Model B Rev 1.0",
            "os": "Debian GNU/Linux 13 (trixie)", "kernel": "7.1.0-rpi7-rpi-2712",
            "arch": "aarch64"}
