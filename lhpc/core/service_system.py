"""Host system metrics for the dashboard System box.

Stateless by design: `system_stats()` returns RAW counters plus a monotonic timestamp and the
BROWSER computes rates between its own polls — no server-side history or sampler (SD-card wear),
no psutil, and NO subprocess of any kind (the route is GET-safe under P0.6 by construction: every
read goes through the injected `System.fs`, so a fake drives it entirely in tests).

Every section is fail-soft: an absent/unreadable source omits its key. Unknown stays unknown —
in particular the Power section never synthesizes values its source cannot see.
"""

from __future__ import annotations

import ctypes
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


# --- time state ----------------------------------------------------------------------------------
#
# The System panel's Time row. LHPC only ever REPORTS the clock: it never sets, steps or
# disciplines it, never installs or enables a time daemon, and never invokes `timedatectl`.
#
# One deliberate widening of this module's file-reads-only mechanism: the kernel's synchronisation
# state is not exposed as a file anywhere, so it is read with the `ntp_adjtime` syscall through
# ctypes. That stays GET-safe under P0.6 because it is a READ-ONLY syscall in this process — no
# fork, no exec, no subprocess, and no mutation: `modes` is asserted to be 0 before the call, which
# is what makes the kernel treat the struct as a pure query, and no ADJ_* bit is ever set. Any
# failure at all (no libc, unexpected ABI, EPERM) yields UNKNOWN, never a red pin: an unreadable
# clock state is not evidence of a bad clock.

_STA_UNSYNC = 0x0040          # kernel: clock is not synchronised
_TIME_ERROR = 5               # ntp_adjtime() return code when unsynchronised
_MAXERROR_CAP_US = 16_000_000  # kernel clamps maxerror here; the "nothing is steering it" ceiling
_GREEN_MAXERROR_US = 1_000_000    # <= 1 s of estimated error to call the state green

# Nothing lhpc writes can predate the commit that introduced this check. A realtime clock reading
# before this is not merely unsynchronised, it is demonstrably wrong.
_NOT_BEFORE = 1_735_689_600   # 2025-01-01T00:00:00Z

# Operator guidance, shown only when the pin is not green. TEXT ONLY — lhpc never runs any of it.
# The COMMAND is kept separate from the prose so the element carrying it can be select-all
# copyable: the previous single string ended in "(or install chrony)", which pastes into a shell
# as a syntax error. And the advice is per-state, because "enable NTP" is wrong guidance when a
# daemon is already running or when two are fighting.
_HINT_ENABLE_NTP = "sudo timedatectl set-ntp true"
_HINT_NO_SOURCE = ("nothing is synchronising this clock — enable a time daemon "
                   "(systemd-timesyncd), or install chrony")
_HINT_RESTORED = "the time was restored, not synchronised — enable a time daemon to correct it"
_HINT_WAITING = "a time daemon is running and has not completed its first sync yet — no action"
# With a daemon ALREADY running, "enable/install one" is wrong advice whatever else is off: the
# useful direction is that daemon's own upstream.
_HINT_CHECK_SOURCE = "{} is running but the clock is not yet within tolerance — wait, or check that daemon's time source"
_HINT_CONFLICT = ("two time daemons are steering this clock; disable one of them "
                  "(they will fight and the time will jitter)")

# Keyed on /proc/<pid>/comm, which the kernel TRUNCATES to 15 characters + NUL: the real value
# for systemd-timesyncd is "systemd-timesyn", not the full name (verified live — matching the
# untruncated name found nothing while the daemon was plainly running).
_TIME_DAEMONS = {
    "systemd-timesyn": "systemd-timesyncd",
    "chronyd": "chrony",
    "ntpd": "ntpd",
    "ntpsec": "ntpsec",
}
# Deliberately NOT here: gpsd. It supplies time to chrony/ntpd over SHM but never disciplines the
# kernel clock itself, so counting it made the ordinary "gpsd + timesyncd" pairing look like two
# daemons fighting, and let gpsd alone be named as the thing steering the clock.
_PROC_SCAN_MAX = 4096         # bound on the /proc listing, like every other scan here


class _Timex(ctypes.Structure):
    """`struct timex` as the kernel expects it (Linux, glibc `ntp_adjtime`). Only ever passed with
    `modes = 0`, i.e. read-only; the field layout matters solely so `status`/`maxerror` land in the
    right place."""

    _fields_ = [
        ("modes", ctypes.c_int), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
        ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
        ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long), ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long), ("jitter", ctypes.c_long), ("shift", ctypes.c_int),
        ("stabil", ctypes.c_long), ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long),
        ("errcnt", ctypes.c_long), ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int),
        ("padding", ctypes.c_int * 11),
    ]


# Loaded ONCE: re-resolving the process image on every poll cost 8.6 ms on a Zero 2W. A dict
# rather than module globals so there is no rebinding to reason about.
_LIBC_CACHE: dict = {}


def _libc():
    """The process's own libc handle, resolved once and remembered (including a failure, so a box
    without a usable libc does not retry on every dashboard poll)."""
    if "h" not in _LIBC_CACHE:
        try:
            _LIBC_CACHE["h"] = ctypes.CDLL(None, use_errno=True)
        except Exception:
            _LIBC_CACHE["h"] = None
    return _LIBC_CACHE["h"]


def read_kernel_time_state() -> dict | None:
    """{"synced": bool, "maxerror_us": int} from `ntp_adjtime`, or None when it cannot be read.

    READ-ONLY BY CONSTRUCTION: `modes` is left at 0 (the struct is zero-initialised and asserted
    before the call), so the kernel answers without changing anything. No ADJ_* bit is ever set.
    """
    try:
        # `ctypes.util.find_library("c")` SHELLS OUT to `/sbin/ldconfig -p` on glibc — once per
        # request, which is exactly the process churn this module forbids. `CDLL(None)` resolves
        # against the already-loaded image in THIS process: no fork, no exec, no lookup.
        libc = _libc()
        if libc is None:
            return None
        tx = _Timex()                       # zero-initialised => modes == 0
        if tx.modes != 0:                   # belt and braces: never query with a mode set
            return None
        rc = libc.ntp_adjtime(ctypes.byref(tx))
        if rc < 0:
            return None
        return {"synced": rc != _TIME_ERROR and not (tx.status & _STA_UNSYNC),
                "maxerror_us": int(tx.maxerror)}
    except Exception:                       # no libc, odd ABI, anything at all -> UNKNOWN
        return None


def tz_name_from_link(target: str) -> str:
    """Zone name out of an `/etc/localtime` symlink target ('.../zoneinfo/Europe/Berlin' ->
    'Europe/Berlin'). "" when the target does not look like a zoneinfo path."""
    marker = "/zoneinfo/"
    if marker in target:
        name = target.split(marker, 1)[1].strip().strip("/")
        if name and name != "posixrules":
            return name
    return ""

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

        out["time"] = self._time_state(fs)

        out["info"] = self._host_info(fs)
        return out

    def _time_state(self, fs) -> dict:
        """Time row: what the clock SAYS, and how much reason there is to believe it.

        LHPC cannot prove the time is correct — that needs an external reference it does not have.
        So nothing here claims correctness: the pin reports SYNC STATE and the text names the
        source.

        YELLOW is a legitimate steady state — a daemon that has not synced yet, or a clock
        restored from an RTC or fake-hwclock — and is worded as unverified, never as a fault.
        A box with NO source at all (no daemon, no restore artifact, unsynced) is RED, including
        a portable one with neither network nor RTC: there is nothing holding that clock up.
        """
        now = time.time()
        lt = time.localtime(now)
        out: dict = {
            "epoch": now,
            "local": time.strftime("%Y-%m-%d %H:%M:%S", lt),
            "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "tz": self._tz_name(fs, lt),
        }

        rtc_present = bool(fs.read_text("/sys/class/rtc/rtc0/name", _MAX_SMALL).strip())
        out["rtc_present"] = rtc_present

        kernel = self._kernel_time_state()
        if kernel is None:
            # Its own state. "We could not read it" is not "the clock is bad" — and the facts we
            # CAN read (zone, RTC presence) are still reported rather than silently dropped.
            out.update(state="unknown", source="", label="unknown",
                       detail="kernel time state unavailable")
            return out
        synced = bool(kernel["synced"])
        maxerror = int(kernel["maxerror_us"])
        out["maxerror_us"] = maxerror

        daemons = self._time_daemons(fs)
        out["daemons"] = daemons
        # 1 = the kernel set the system clock FROM the RTC at boot (so the time has a provenance
        # even when nothing has ever synchronised it).
        hctosys = fs.read_text("/sys/class/rtc/rtc0/hctosys", _MAX_SMALL).strip() == "1"
        if fs.exists("/sys/class/pps/pps0"):
            out["pps"] = True

        synced_at = fs.mtime("/run/systemd/timesync/synchronized")   # exists only once synced
        if synced_at is None and fs.exists("/run/systemd/timesync/synchronized"):
            synced_at = now
        last_good = fs.mtime("/var/lib/systemd/timesync/clock")      # last known good, survives boot
        fake_hw = fs.mtime("/etc/fake-hwclock.data")
        if synced_at is not None:
            out["synced_age_s"] = max(0.0, now - synced_at)

        # --- who, if anyone, is steering this clock
        if daemons:
            source = daemons[0]
        elif synced_at is not None:
            source = "NTP"
        elif hctosys and rtc_present:
            source = "RTC"
        elif last_good is not None:
            source = "timesyncd clock file"
        elif fake_hw is not None:
            source = "fake-hwclock"
        else:
            source = ""
        out["source"] = source

        # --- demonstrably wrong beats every other consideration
        # Only meaningful when NOTHING is steering the clock — not merely "not synced yet".
        # A synced clock corrected BACKWARDS legitimately reads earlier than files written before
        # the correction, and so does a restore from a box that ran fast; calling either red
        # punished the very repair we ask for. The same applies while a daemon is present but has
        # not yet completed its first sync: "chronyd running, not synced yet" is the accurate and
        # more useful state, and this heuristic was masking it.
        no_candidate = (not daemons and synced_at is None and not hctosys
                        and last_good is None and fake_hw is None)
        floor = max([t for t in (self._runtime_write_floor(fs), float(_NOT_BEFORE)) if t], default=0.0)
        if no_candidate and not synced and now < floor:
            out.update(state="red", label="implausible",
                       detail="clock reads earlier than files this box has written",
                       hint=_HINT_NO_SOURCE, hint_cmd=_HINT_ENABLE_NTP)
            return out

        if len(daemons) > 1:
            # Two of them fighting over one clock is a configuration conflict, and the operator
            # cannot see it anywhere else.
            # No command offered: which of the two to disable is the operator's call, and
            # "enable NTP" would be actively wrong advice here.
            out.update(state="yellow", label="conflict",
                       detail="two time daemons running (" + ", ".join(daemons) + ")",
                       hint=_HINT_CONFLICT)
            return out

        if synced and maxerror <= _GREEN_MAXERROR_US and source:
            out.update(state="green", label="synced")
            return out

        if not synced and no_candidate:
            out.update(state="red", label="no time source", detail="no time source",
                       hint=_HINT_NO_SOURCE, hint_cmd=_HINT_ENABLE_NTP)
            return out
        if maxerror >= _MAXERROR_CAP_US and not source:
            out.update(state="red", label="no time source", detail="no time source",
                       hint=_HINT_NO_SOURCE, hint_cmd=_HINT_ENABLE_NTP)
            return out

        # Plausible, unverified. Say WHY, because the reasons want different reactions.
        hint, hint_cmd = _HINT_NO_SOURCE, _HINT_ENABLE_NTP
        if synced_at is not None and not synced:
            detail = "synced earlier this boot, source now unreachable"
            hint = "the time source stopped answering — check the network or the daemon"
            hint_cmd = ""
        elif source in ("RTC", "fake-hwclock", "timesyncd clock file"):
            detail = ("RTC restore, never synced this boot" if source == "RTC"
                      else "saved timestamp restored, never synced this boot")
            hint = _HINT_RESTORED
        elif daemons and not synced:
            detail = "time daemon running, not synced yet"
            hint, hint_cmd = _HINT_WAITING, ""     # nothing to run; waiting IS the right action
        elif source:
            # A REAL daemon is active (synced, but the estimated error is above tolerance, or the
            # state is otherwise unproven). Telling the operator to enable or install another one
            # is wrong and would create the very two-daemon conflict flagged above.
            detail = ("synced, estimated error above tolerance" if synced else "unverified")
            hint, hint_cmd = _HINT_CHECK_SOURCE.format(source), ""
        else:
            detail = "unverified"
        out.update(state="yellow", label="unverified", detail=detail, hint=hint)
        if hint_cmd:
            out["hint_cmd"] = hint_cmd
        return out

    def _kernel_time_state(self) -> dict | None:
        """Seam: the syscall lives at module level so tests can drive every branch (including the
        failure path) without a real kernel state."""
        return read_kernel_time_state()

    # The ONE piece of state in this module, and deliberately not the kind its header forbids:
    # not history, not a sampler, nothing written to the SD card — just a short memo of a
    # /proc scan. Which time daemons exist is a configuration fact that changes when someone
    # installs or enables one, not between two dashboard ticks; rescanning ~200 processes every
    # 2 s cost 11.7 ms per poll on a Zero 2W, over half the entire endpoint.
    _TIME_DAEMON_TTL_S = 30.0

    def _time_daemons(self, fs) -> list:
        """Names of running time daemons, from /proc/<pid>/comm — file reads only, no ps.
        Memoised for `_TIME_DAEMON_TTL_S`; a daemon appearing or vanishing shows up within that."""
        memo = getattr(self, "_time_daemon_memo", None)
        if memo is not None and (time.monotonic() - memo[0]) < self._TIME_DAEMON_TTL_S:
            return list(memo[1])
        found = self._scan_time_daemons(fs)
        self._time_daemon_memo = (time.monotonic(), list(found))
        return found

    def _scan_time_daemons(self, fs) -> list:
        found = []
        for entry in fs.listdir("/proc", _PROC_SCAN_MAX):
            if not entry.isdigit():
                continue
            comm = fs.read_text("/proc/" + entry + "/comm", _MAX_SMALL).strip()
            name = _TIME_DAEMONS.get(comm)
            if name and name not in found:
                found.append(name)
        return sorted(found)

    def _tz_name(self, fs, lt) -> str:
        """The zone the displayed LOCAL TIME is actually in.

        `TZ` in the environment wins, because that is what `time.localtime()` above honoured:
        reading the label from `/etc/localtime` while the timestamp came from `TZ` labelled a
        Bogota reading as Europe/Berlin. After that: the symlink, `/etc/timezone`, and finally the
        UTC offset — always available, and it never pretends to be a zone name.
        """
        env_tz = os.environ.get("TZ", "").strip()
        if env_tz:
            return env_tz.lstrip(":")
        name = tz_name_from_link(fs.readlink("/etc/localtime"))
        if name:
            return name
        name = fs.read_text("/etc/timezone", _MAX_SMALL).strip()
        if name:
            return name
        off = -(time.altzone if lt.tm_isdst and time.daylight else time.timezone)
        sign = "+" if off >= 0 else "-"
        off = abs(off)
        return f"UTC{sign}{off // 3600:02d}:{(off % 3600) // 60:02d}"

    def _runtime_write_floor(self, fs) -> float:
        """Newest mtime among directories LHPC itself writes. The clock cannot legitimately read
        earlier than something this box has already written."""
        newest = 0.0
        root = str(self._paths.runtime_root)
        for rel in ("state", "config", "logs"):
            m = fs.mtime(root + "/" + rel)
            if m and m > newest:
                newest = m
        return newest

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
