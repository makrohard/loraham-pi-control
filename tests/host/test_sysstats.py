"""System box backend: pure procfs/sysfs parsers + the stateless `system_stats()` contract.

Behaviour under test: raw counters + monotonic ts (browser computes rates), fail-soft omission of
absent sources, truthful Power reporting (unknown is never synthesized), and the no-subprocess
guarantee of the GET path.
"""

import ctypes
import os

import pytest

from lhpc.core.paths import Paths

from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_system import (
    parse_dt_model,
    parse_loadavg,
    parse_meminfo,
    parse_net_dev,
    parse_proc_wireless,
    parse_os_release,
    parse_proc_stat,
    parse_uptime,
)
from lhpc.core.services import ControllerService

_PROC_STAT = """cpu  10 20 30 4000 50 6 7 8 0 0
cpu0 1 2 3 400 5 0 0 0 0 0
cpu1 1 2 3 400 5 0 0 0 0 0
cpu2 1 2 3 400 5 0 0 0 0 0
cpu3 1 2 3 400 5 0 0 0 0 0
intr 12345
ctxt 6789
"""

_MEMINFO = """MemTotal:         425392 kB
MemFree:           50120 kB
MemAvailable:     231234 kB
SwapTotal:        786428 kB
SwapFree:         665852 kB
"""

_NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  999999    1000    0    0    0     0          0         0   999999    1000    0    0    0     0       0          0
 wlan0: 1234567    2222    0    0    0     0          0         0   7654321    3333    0    0    0     0       0          0
  eth0:  100000     444    0    0    0     0          0         0    200000     555    0    0    0     0       0          0
"""


# --- parsers -------------------------------------------------------------------------------------

def test_parse_proc_stat_totals_and_cores():
    d = parse_proc_stat(_PROC_STAT)
    assert d["total"] == [10, 20, 30, 4000, 50, 6, 7, 8]
    assert d["cores"] == 4
    assert len(d["percore"]) == 4 and d["percore"][0] == [1, 2, 3, 400, 5, 0, 0, 0]


@pytest.mark.parametrize("text", ["", "cpu  1 2 3\n", "intr 5\n", "cpu  a b c d e f g h\n"])
def test_parse_proc_stat_garbage_is_none(text):
    assert parse_proc_stat(text) is None


def test_parse_meminfo_prefers_memavailable_and_reads_swap():
    d = parse_meminfo(_MEMINFO)
    assert d == {"total_kb": 425392, "available_kb": 231234,
                 "swap_total_kb": 786428, "swap_free_kb": 665852}


def test_parse_meminfo_falls_back_to_memfree_and_defaults_swap_zero():
    d = parse_meminfo("MemTotal: 1000 kB\nMemFree: 400 kB\n")
    assert d == {"total_kb": 1000, "available_kb": 400, "swap_total_kb": 0, "swap_free_kb": 0}


@pytest.mark.parametrize("text", ["", "MemFree: 400 kB\n", "MemTotal: x kB\n"])
def test_parse_meminfo_garbage_is_none(text):
    assert parse_meminfo(text) is None


def test_parse_net_dev_sums_all_but_loopback():
    d = parse_net_dev(_NET_DEV)
    assert d == {"rx_bytes": 1234567 + 100000, "tx_bytes": 7654321 + 200000}


def test_parse_net_dev_skips_malformed_lines_and_needs_one_interface():
    text = _NET_DEV + " bad0: 1 2 3\n"
    assert parse_net_dev(text)["rx_bytes"] == 1234567 + 100000   # malformed line ignored
    assert parse_net_dev("header only\n") is None                # no interface parsed -> unknown


@pytest.mark.parametrize("text,expect", [
    ("0.42 0.31 0.22 1/234 5678\n", [0.42, 0.31, 0.22]),
    ("garbage\n", None),
    ("", None),
])
def test_parse_loadavg(text, expect):
    assert parse_loadavg(text) == expect


@pytest.mark.parametrize("text,expect", [
    ("12345.67 23456.78\n", 12345.67),
    ("nope\n", None),
    ("", None),
])
def test_parse_uptime(text, expect):
    assert parse_uptime(text) == expect


@pytest.mark.parametrize("text,expect", [
    ('PRETTY_NAME="Raspberry Pi OS Lite"\nID=debian\n', "Raspberry Pi OS Lite"),
    ("PRETTY_NAME=Plain\n", "Plain"),
    ("ID=debian\n", ""),
])
def test_parse_os_release(text, expect):
    assert parse_os_release(text) == expect


def test_parse_dt_model_strips_nul():
    assert parse_dt_model("Raspberry Pi Zero 2 W Rev 1.0\x00") == "Raspberry Pi Zero 2 W Rev 1.0"


_WIRELESS_HEAD = ("Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
                  " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n")


@pytest.mark.parametrize("level, expect", [
    ("-70.", {"wlan0": -70}),          # brcmfmac on the Zero 2 W: the dot is the "updated" flag
    ("-70", {"wlan0": -70}),           # no flag
    ("-81.5", {"wlan0": -81}),         # only the integer part
    ("0.", {}),                        # not associated
    ("62.", {}),                       # a 0-100 quality scale, not dBm
    ("-256", {}),                      # an "invalid" level
    ("n/a", {}),                       # malformed
])
def test_parse_proc_wireless_reads_the_level_in_dbm(level, expect):
    text = _WIRELESS_HEAD + f" wlan0: 0000   40.  {level}  -256        0      0      0    319      0        0\n"
    assert parse_proc_wireless(text) == expect


def test_parse_proc_wireless_several_interfaces_and_no_file():
    text = (_WIRELESS_HEAD
            + " wlan0: 0000   40.  -70.  -256  0 0 0 0 0 0\n"
            + " wlan1: 0000    0.    0.  -256  0 0 0 0 0 0\n"
            + "  wlx0: 0000   55.  -55   -95   0 0 0 0 0 0\n"
            + "broken line\n")
    assert parse_proc_wireless(text) == {"wlan0": -70, "wlx0": -55}
    assert parse_proc_wireless("") == {}               # no /proc/net/wireless (Ethernet-only box)


# --- system_stats() contract ---------------------------------------------------------------------

def _svc(tmp_path, fake):
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def _full_fake(tmp_path):
    return FakeSystem(
        files={
            "/proc/stat": _PROC_STAT,
            "/proc/meminfo": _MEMINFO,
            "/proc/net/dev": _NET_DEV,
            "/proc/loadavg": "0.42 0.31 0.22 1/234 5678\n",
            "/proc/uptime": "12345.67 23456.78\n",
            "/sys/class/thermal/thermal_zone0/temp": "48312\n",
            "/proc/device-tree/model": "Raspberry Pi 5 Model B Rev 1.0\x00",
            "/etc/os-release": 'PRETTY_NAME="Raspberry Pi OS Lite"\n',
            "/sys/class/hwmon/hwmon3/name": "rpi_volt\n",
            "/sys/class/hwmon/hwmon3/in0_lcrit_alarm": "0\n",
            "/sys/class/hwmon/hwmon0/name": "cpu_thermal\n",
        },
        statvfs_data={
            "/": {"total_b": 31000000000, "free_b": 21000000000, "dev": 100,
                  "free_inodes": 1800000, "total_inodes": 1900000},
            str(tmp_path): {"total_b": 31000000000, "free_b": 21000000000, "dev": 100,
                            "free_inodes": 1800000, "total_inodes": 1900000},
        },
        dirs={"/sys/class/hwmon": ["hwmon0", "hwmon3"]},
    )


def test_system_stats_full_contract(tmp_path):
    svc = _svc(tmp_path, _full_fake(tmp_path))
    d = svc.system_stats()
    assert isinstance(d["ts"], float)
    assert d["cpu"]["total"] == [10, 20, 30, 4000, 50, 6, 7, 8]
    assert d["cpu"]["cores"] == 4 and len(d["cpu"]["percore"]) == 4
    assert d["load"] == [0.42, 0.31, 0.22]
    assert d["mem"]["available_kb"] == 231234
    assert d["net"] == {"rx_bytes": 1334567, "tx_bytes": 7854321}
    assert d["disk"]["root"] == {"total_b": 31000000000, "free_b": 21000000000,
                                 "free_inodes": 1800000, "total_inodes": 1900000,
                                 "level": "ok", "reason": ""}
    assert "runtime" not in d["disk"]           # same st_dev as / -> no duplicate row
    assert d["temp_mc"] == 48312
    assert d["power"] == {"source": "hwmon-alarm", "undervolt_alarm": False}
    assert d["uptime_s"] == 12345.67
    assert d["info"]["model"] == "Raspberry Pi 5 Model B Rev 1.0"
    assert d["info"]["os"] == "Raspberry Pi OS Lite"
    assert "wifi" not in d                      # no /proc/net/wireless -> no signal key


def test_system_stats_carries_the_wifi_signal_when_associated(tmp_path):
    fake = _full_fake(tmp_path)
    fake.files["/proc/net/wireless"] = (_WIRELESS_HEAD
                                        + " wlan0: 0000   40.  -77.  -256  0 0 0 319 0 0\n")
    assert _svc(tmp_path, fake).system_stats()["wifi"] == {"wlan0": -77}
    fake.files["/proc/net/wireless"] = _WIRELESS_HEAD + " wlan0: 0000    0.    0.  -256  0 0 0 0 0 0\n"
    assert "wifi" not in _svc(tmp_path, fake).system_stats()      # not associated -> left out


def test_system_stats_runtime_disk_on_other_device(tmp_path):
    fake = _full_fake(tmp_path)
    fake.statvfs_data[str(tmp_path)] = {"total_b": 500, "free_b": 400, "dev": 200}
    d = _svc(tmp_path, fake).system_stats()
    assert d["disk"]["runtime"] == {"path": str(tmp_path), "total_b": 500, "free_b": 400,
                                    "free_inodes": 0, "total_inodes": 0,
                                    "level": "critical", "reason": "bytes"}    # 400 B < 500 MiB


def test_system_stats_empty_host_is_fail_soft(tmp_path):
    # Non-Pi/CI case: nothing readable -> only ts + info, and NO raise.
    d = _svc(tmp_path, FakeSystem()).system_stats()
    assert isinstance(d["ts"], float)
    for key in ("cpu", "load", "mem", "net", "disk", "temp_mc", "power", "uptime_s"):
        assert key not in d
    assert "kernel" in d["info"]                # uname-based fields still present


# --- Power truthfulness --------------------------------------------------------------------------

def test_power_alarm_set(tmp_path):
    fake = _full_fake(tmp_path)
    fake.files["/sys/class/hwmon/hwmon3/in0_lcrit_alarm"] = "1\n"
    d = _svc(tmp_path, fake).system_stats()
    assert d["power"] == {"source": "hwmon-alarm", "undervolt_alarm": True}


def test_power_hwmon_only_never_synthesizes_throttle_fields(tmp_path):
    d = _svc(tmp_path, _full_fake(tmp_path)).system_stats()
    # The alarm source cannot see throttling/frequency capping — those keys MUST be absent,
    # never synthesized to false (unknown stays unknown).
    assert set(d["power"]) == {"source", "undervolt_alarm"}


def test_power_absent_source_omits_key(tmp_path):
    fake = _full_fake(tmp_path)
    fake.dirs = {}                              # no hwmon directory at all
    assert "power" not in _svc(tmp_path, fake).system_stats()


def test_power_unreadable_alarm_is_unknown(tmp_path):
    fake = _full_fake(tmp_path)
    fake.files["/sys/class/hwmon/hwmon3/in0_lcrit_alarm"] = "garbage"
    assert "power" not in _svc(tmp_path, fake).system_stats()


def test_power_hwmon_index_is_scanned_not_hardcoded(tmp_path):
    # Live fact: rpi_volt sits at hwmon3 on the Pi 5 but hwmon1 on the Zero — the index is
    # dynamic. A high index must still be found through the bounded directory scan.
    fake = _full_fake(tmp_path)
    fake.dirs = {"/sys/class/hwmon": ["hwmon0", "hwmon42"]}
    fake.files["/sys/class/hwmon/hwmon42/name"] = "rpi_volt\n"
    fake.files["/sys/class/hwmon/hwmon42/in0_lcrit_alarm"] = "0\n"
    d = _svc(tmp_path, fake).system_stats()
    assert d["power"] == {"source": "hwmon-alarm", "undervolt_alarm": False}


# --- no-subprocess proof -------------------------------------------------------------------------

def test_system_stats_runs_no_commands_at_all(tmp_path):
    # Stronger than the no-NETWORK-command GET invariant: this endpoint must be purely
    # file-read-based — zero runner invocations of any kind.
    fake = _full_fake(tmp_path)
    _svc(tmp_path, fake).system_stats()
    assert fake.calls == []


# --- FakeSystem read_text fidelity ---------------------------------------------------------------

def test_fake_read_text_bound_is_bytes_not_characters(tmp_path):
    # The real implementation slices BYTES then decodes with replacement; the fake must match,
    # or multibyte content would behave differently in tests than in production.
    fake = FakeSystem(files={"/x": "ä" * 10})   # 2 bytes per char in UTF-8
    assert len(fake.system.fs.read_text("/x", 4)) == 2
    assert fake.system.fs.read_text("/x", 3) == "ä�"   # split multibyte -> replacement


def test_power_includes_core_voltage_when_kernel_exposes_it(tmp_path):
    # Newer kernels (raspberrypi-hwmon voltage patch, Linux 7.2 queue) add in0_input (mV) next
    # to the alarm; the reader picks it up without any config — display self-activates.
    fake = _full_fake(tmp_path)
    fake.files["/sys/class/hwmon/hwmon3/in0_input"] = "852\n"
    d = _svc(tmp_path, fake).system_stats()
    assert d["power"] == {"source": "hwmon-alarm", "undervolt_alarm": False, "core_mv": 852}


def test_power_garbage_core_voltage_is_omitted_not_zero(tmp_path):
    fake = _full_fake(tmp_path)
    fake.files["/sys/class/hwmon/hwmon3/in0_input"] = "garbage"
    d = _svc(tmp_path, fake).system_stats()
    assert "core_mv" not in d["power"]           # unknown stays unknown, never 0


# --- listdir cap regressions ---------------------------------------------------------------------

def test_hwmon_scan_is_capped(tmp_path):
    # An entry BEYOND the 64-entry scan cap must not be found — the bound is real, not cosmetic.
    fake = _full_fake(tmp_path)
    fake.dirs = {"/sys/class/hwmon": [f"hwmon{i}" for i in range(65)]}
    fake.files["/sys/class/hwmon/hwmon64/name"] = "rpi_volt\n"     # entry #65: outside the cap
    fake.files["/sys/class/hwmon/hwmon64/in0_lcrit_alarm"] = "0\n"
    for k in list(fake.files):
        if k.startswith("/sys/class/hwmon/hwmon3/"):
            del fake.files[k]                                      # the in-cap rpi_volt goes away
    assert "power" not in _svc(tmp_path, fake).system_stats()


def test_real_listdir_stops_at_cap(tmp_path):
    from lhpc.core.probes.backends import RealFileSystem
    d = tmp_path / "many"
    d.mkdir()
    for i in range(10):
        (d / f"f{i}").touch()
    out = RealFileSystem().listdir(str(d), 4)
    assert len(out) == 4 and out == sorted(out)
    assert RealFileSystem().listdir(str(tmp_path / "absent"), 4) == []


# --- Time row -------------------------------------------------------------------------------------
#
# LHPC only REPORTS the clock. The pin is the SYNC STATE, never a claim of correctness — nothing
# here can prove the time is right without an external reference.

_NOW = 1_800_000_000.0          # a plausible "now", well after the compiled-in not-before date


from lhpc.core.probes.backends import CommandResult as _CR

_CHRONYC = ("chronyc", "-n", "-c", "tracking")
_TRACK_NTP = {_CHRONYC: _CR(0, "C0A8B201,192.168.178.1,3,1790461010.9,-0.0002,0,0,-27.9,0.01,0.1,0.02,0.0005,64.4,Normal\n", "")}
_TRACK_PPS = {_CHRONYC: _CR(0, "50505300,PPS,1,1790461010.9,-0.0000,0,0,-27.9,0.01,0.1,0.001,0.0001,16.0,Normal\n", "")}
_TRACK_GPS = {_CHRONYC: _CR(0, "47505300,GPS,1,1790461010.9,-0.0000,0,0,-27.9,0.01,0.1,0.001,0.0001,16.0,Normal\n", "")}


def _time_fake(tmp_path, **kw):
    """A fake with the minimum for `_time_state`; `kw` overrides files/dirs/mtimes/links."""
    fake = _full_fake(tmp_path)
    fake.files.setdefault("/sys/class/rtc/rtc0/name", "")
    fake.files.setdefault("/sys/class/rtc/rtc0/hctosys", "0")
    fake.dirs.setdefault("/proc", [])
    for key, val in kw.items():
        getattr(fake, key).update(val)
    return fake


def _time_state(tmp_path, fake, kernel, now=_NOW, monkeypatch=None):
    """Drive `system_stats()["time"]` with a pinned kernel state and a pinned wall clock."""
    import time as _time

    from lhpc.core.services import ControllerService as _CS
    svc = _svc(tmp_path, fake)
    mp = monkeypatch or pytest.MonkeyPatch()
    mp.setattr(_CS, "_kernel_time_state", lambda self: kernel)
    mp.setattr(_time, "time", lambda: now)
    try:
        return svc.system_stats()["time"]
    finally:
        if monkeypatch is None:
            mp.undo()


def test_time_green_needs_sync_low_error_and_a_named_source(tmp_path):
    fake = _time_fake(tmp_path,
                      dirs={"/proc": ["1", "412"]},
                      files={"/proc/412/comm": "systemd-timesyn\n"},
                      mtimes={"/run/systemd/timesync/synchronized": _NOW - 240})
    d = _time_state(tmp_path, fake, {"synced": True, "maxerror_us": 300_000})
    assert d["state"] == "green"
    assert d["source"] == "systemd-timesyncd"
    assert 239 <= d["synced_age_s"] <= 241
    assert "hint" not in d, "a green row must not nag"


def test_time_green_requires_the_error_to_be_small(tmp_path):
    """Synced but with seconds of estimated error is not green — the pin tracks confidence."""
    fake = _time_fake(tmp_path, dirs={"/proc": ["412"]},
                      files={"/proc/412/comm": "chronyd\n"})
    d = _time_state(tmp_path, fake, {"synced": True, "maxerror_us": 5_000_000})
    assert d["state"] == "yellow"


@pytest.mark.parametrize("why, kernel, kw, expect", [
    ("synced earlier this boot, source now gone",
     {"synced": False, "maxerror_us": 2_000_000},
     {"mtimes": {"/run/systemd/timesync/synchronized": _NOW - 600}}, "unreachable"),
    ("RTC restore, never synced this boot",
     {"synced": False, "maxerror_us": 900_000},
     {"files": {"/sys/class/rtc/rtc0/name": "rtc-ds3231\n",
                "/sys/class/rtc/rtc0/hctosys": "1"}}, "RTC restore"),
])
def test_time_yellow_triggers(tmp_path, why, kernel, kw, expect):
    """Plausible but unverified: something is holding the clock up, but nothing has proven it.
    A correct steady state, so nothing may word or style it as a fault. (A box with NO source at
    all is red, not yellow — see `test_time_red_when_nothing_steers_the_clock`.)"""
    d = _time_state(tmp_path, _time_fake(tmp_path, **kw), kernel)
    assert d["state"] == "yellow", why
    assert expect in d["detail"], d["detail"]
    assert d["hint"], "not-green rows carry the operator hint"


_FAKE = {"/etc/fake-hwclock.data": _NOW - 3600}
_CHRONY = {"dirs": {"/proc": ["412"]}, "files": {"/proc/412/comm": "chronyd\n"}}


def _chronyd_stat(started_s: float) -> str:
    """/proc/412/stat of a chronyd started `started_s` after boot (field 22 in clock ticks)."""
    import os as _os
    ticks = int(started_s * _os.sysconf("SC_CLK_TCK"))
    rest = ["S", "1", "412", "412", "0", "-1", "4194560", "100", "0", "0", "0", "1", "2", "0", "0",
            "20", "0", "1", "0", str(ticks), "12345678", "400", "18446744073709551615"]
    return "412 (chronyd) " + " ".join(rest) + "\n"


def _unsynced_chronyc(rms: str):
    """What `chronyc -n -c tracking` prints while chronyd is NOT synchronised (reference.c: ref id
    UNSYNC, stratum 0, ref time 0); only the RMS offset (index 6) remembers an earlier reference."""
    return {_CHRONYC: _CR(0, f"00000000,,0,0.000000000,0.000001234,-0.000040000,{rms},-27.9,0.000,"
                             "0.000,1.000000000,1.000000000,0.0,Not synchronised\n", "")}


def _chrony_box(started_s=24.4, rms="0.000000000", **extra):
    """An unsynced chrony box: chronyd pid 412 started `started_s` after boot, uptime 38514 s."""
    files = {"/proc/412/comm": "chronyd\n", "/proc/412/stat": _chronyd_stat(started_s),
             "/proc/uptime": "38514.00 70000.00\n", **extra.pop("files", {})}
    return {"dirs": {"/proc": ["412"]}, "files": files,
            "commands": _unsynced_chronyc(rms), **extra}


@pytest.mark.parametrize("why, kernel, kw, state, label", [
    ("fake-hwclock only, nothing synced",
     {"synced": False, "maxerror_us": 900_000}, {"mtimes": _FAKE}, "red", "fake"),
    ("chrony running but not synced yet, fake-hwclock installed (the usual LHPC boot)",
     {"synced": False, "maxerror_us": 800_000}, _chrony_box(mtimes=_FAKE), "red", "fake"),
    ("an RTC restored the clock: not fake, even with fake-hwclock installed",
     {"synced": False, "maxerror_us": 900_000},
     _chrony_box(mtimes=_FAKE, files={"/sys/class/rtc/rtc0/name": "rtc-ds3231\n",
                                      "/sys/class/rtc/rtc0/hctosys": "1"}), "yellow", "RTC"),
    ("synced earlier this boot keeps precedence over fake",
     {"synced": False, "maxerror_us": 2_000_000},
     {"mtimes": {**_FAKE, "/run/systemd/timesync/synchronized": _NOW - 600}}, "yellow", "lost"),
    ("timesyncd's clock file is a saved time too",
     {"synced": False, "maxerror_us": 900_000},
     {"mtimes": {"/var/lib/systemd/timesync/clock": _NOW - 7200}}, "red", "fake"),
    ("synced to an NTP server",
     {"synced": True, "maxerror_us": 200_000},
     {**_CHRONY, "mtimes": _FAKE, "commands": _TRACK_NTP}, "green", "NTP"),
    ("synced to the GPS refclock",
     {"synced": True, "maxerror_us": 200_000},
     {**_CHRONY, "mtimes": _FAKE, "commands": _TRACK_GPS}, "green", "GNSS"),
    ("synced to GPS but above tolerance: the source name in yellow",
     {"synced": True, "maxerror_us": 5_000_000}, {**_CHRONY, "commands": _TRACK_GPS}, "yellow", "GNSS"),
    ("timesyncd synced: NTP without any call",
     {"synced": True, "maxerror_us": 200_000},
     {"dirs": {"/proc": ["7"]}, "files": {"/proc/7/comm": "systemd-timesyn\n"}}, "green", "NTP"),
    ("synced to a non-GPS refclock (PPS): not guessed as GNSS",
     {"synced": True, "maxerror_us": 200_000}, {**_CHRONY, "commands": _TRACK_PPS}, "green", "…"),
    ("chrony synced but chronyc unreadable: source not known",
     {"synced": True, "maxerror_us": 200_000}, _CHRONY, "green", "…"),
    ("chrony running, not synced, no saved time: red none",
     {"synced": False, "maxerror_us": 800_000}, _chrony_box(), "red", "none"),
    ("nothing at all: red none",
     {"synced": False, "maxerror_us": 2_000_000}, {}, "red", "none"),
])
def test_time_pill_every_state_of_the_table(
        tmp_path, why, kernel, kw, state, label):
    d = _time_state(tmp_path, _time_fake(tmp_path, **kw), kernel)
    assert (d["state"], d["label"]) == (state, label), (why, d)
    if label == "fake":   # no stronger than known: a saved time, or the build epoch
        assert d["detail"] == ("not synced yet; clock from a saved time (fake-hwclock or timesyncd's "
                               "clock file) or the build epoch")


_UNSYNCED = {"synced": False, "maxerror_us": 2_000_000}


@pytest.mark.parametrize("why, kw, state, label", [
    ("chrony synced earlier this boot, source lost (the round-6 finding): lost, not fake",
     _chrony_box(rms="0.000183544", mtimes=_FAKE), "yellow", "lost"),
    ("the same after a chronyd restart that has since synced: lost",
     _chrony_box(started_s=26813.5, rms="0.000183544", mtimes=_FAKE), "yellow", "lost"),
    ("chronyd started at boot, never synced, saved time: fake",
     _chrony_box(mtimes=_FAKE), "red", "fake"),
    ("chronyd restarted since boot (bootstrap-deps.sh, install/repair), not synced since: unknown",
     _chrony_box(started_s=26813.5, mtimes=_FAKE), "unknown", "…"),
    ("chronyd start time unreadable: unknown, never fake",
     {**_chrony_box(mtimes=_FAKE), "files": {"/proc/412/comm": "chronyd\n",
                                             "/proc/uptime": "38514.00 70000.00\n"}}, "unknown", "…"),
    ("restarted chronyd on a box whose RTC set the clock: the source is known, yellow RTC",
     _chrony_box(started_s=26813.5, mtimes=_FAKE, files={"/sys/class/rtc/rtc0/name": "rtc-ds3231\n",
                                                        "/sys/class/rtc/rtc0/hctosys": "1"}),
     "yellow", "RTC"),
    ("DECIDED corner (maintainer, 2026-09-27): a restart within 300 s of boot, RMS 0, saved time: "
     "fake until the next sync, not unknown",
     _chrony_box(started_s=150.0, mtimes=_FAKE), "red", "fake"),
    ("a two-daemon conflict still wins over an unknown chrony history",
     {**_chrony_box(started_s=26813.5, mtimes=_FAKE), "dirs": {"/proc": ["412", "7"]},
      "files": {"/proc/412/comm": "chronyd\n", "/proc/7/comm": "systemd-timesyn\n",
                "/proc/412/stat": _chronyd_stat(26813.5), "/proc/uptime": "38514.00 70000.00\n"}},
     "red", "dup"),
])
def test_chrony_history_decides_lost_fake_or_unknown(tmp_path, why, kw, state, label):
    """Audit round 6: chrony replaces timesyncd on an LHPC box, so timesyncd's
    /run/systemd/timesync/synchronized never exists there, and a chrony box that had synced and
    then lost its source read red "fake". chrony's RMS offset (> 0 once it took a reference since
    it started) is the evidence; a chronyd started late (restarted) with 0 is unknown."""
    d = _time_state(tmp_path, _time_fake(tmp_path, **kw), _UNSYNCED)
    assert (d["state"], d["label"]) == (state, label), (why, d)
    assert "synced_age_s" not in d, "chrony gives no timestamp: no invented age"


def test_a_restarted_chrony_on_an_rtc_box_does_not_claim_never_synced(tmp_path):
    # Not "never synced": after a chronyd restart, an earlier sync is unknown.
    kw = _chrony_box(started_s=26813.5, mtimes=_FAKE, files={"/sys/class/rtc/rtc0/name": "rtc-ds3231\n",
                                                            "/sys/class/rtc/rtc0/hctosys": "1"})
    d = _time_state(tmp_path, _time_fake(tmp_path, **kw), _UNSYNCED)
    assert d["label"] == "RTC", d
    assert d["detail"] == ("RTC restore; chrony restarted since boot, not synced since; an earlier "
                           "sync is unknown"), d["detail"]


@pytest.mark.parametrize("result", [
    _CR(0, "", ""),                                                     # empty
    _CR(0, "00000000,,0,0.000000000\n", ""),                            # fewer than 7 fields
    _CR(0, "00000000,,0,0.000000000,0.0,0.0,soon,x\n", ""),             # non-numeric RMS offset
    _CR(0, "00000000,,0,0.000000000,0.0,0.0,nan,x\n", ""),              # NaN
    _CR(0, "00000000,,0,0.000000000,0.0,0.0,inf,x\n", ""),              # infinity
    _CR(1, "", "506 Cannot talk to daemon"),                            # chronyc failed
    _CR(127, "", "chronyc: not found"),                                 # chronyc missing
])
def test_unreadable_chrony_tracking_is_unknown_never_fake(tmp_path, result):
    kw = {**_chrony_box(mtimes=_FAKE), "commands": {_CHRONYC: result}}
    d = _time_state(tmp_path, _time_fake(tmp_path, **kw), _UNSYNCED)
    assert (d["state"], d["label"]) == ("unknown", "…"), d


def test_a_raising_chronyc_is_no_evidence(tmp_path, monkeypatch):
    svc = _svc(tmp_path, _time_fake(tmp_path, **_CHRONY))
    def boom(*a, **k):
        raise OSError("no such file")
    monkeypatch.setattr(svc._system.runner, "run", boom)
    assert svc._chrony_tracking() == ("", 0.0, False)


def test_the_timesyncd_marker_path_is_unchanged(tmp_path):
    d = _time_state(tmp_path, _time_fake(tmp_path, dirs={"/proc": ["7"]},
                                         files={"/proc/7/comm": "systemd-timesyn\n"},
                                         mtimes={**_FAKE, "/run/systemd/timesync/synchronized": _NOW - 600}),
                    _UNSYNCED)
    assert (d["state"], d["label"]) == ("yellow", "lost")
    assert 599 <= d["synced_age_s"] <= 601, "timesyncd keeps its age"


def test_time_red_dup_names_a_two_daemon_conflict(tmp_path):
    """Two daemons fighting over one clock is a config conflict the operator sees nowhere else."""
    fake = _time_fake(tmp_path, dirs={"/proc": ["10", "11"]},
                      files={"/proc/10/comm": "chronyd\n", "/proc/11/comm": "systemd-timesyn\n"})
    d = _time_state(tmp_path, fake, {"synced": True, "maxerror_us": 200_000})
    assert (d["state"], d["label"]) == ("red", "dup")
    assert "two time daemons" in d["detail"]
    assert "chrony" in d["detail"] and "systemd-timesyncd" in d["detail"]


def test_time_red_when_nothing_steers_the_clock(tmp_path):
    d = _time_state(tmp_path, _time_fake(tmp_path), {"synced": False, "maxerror_us": 2_000_000})
    assert d["state"] == "red" and d["detail"] == "no time source"
    assert d["hint"]


def test_time_red_at_the_maxerror_cap_with_no_candidate(tmp_path):
    d = _time_state(tmp_path, _time_fake(tmp_path), {"synced": False, "maxerror_us": 16_000_000})
    assert d["state"] == "red"


def test_time_red_when_an_unsteered_clock_predates_files_this_box_wrote(tmp_path):
    """With nothing steering it, a clock reading earlier than files this box has already written
    is demonstrably wrong — and says so, rather than being lumped in with "no time source"."""
    fake = _time_fake(tmp_path, mtimes={str(tmp_path) + "/state": _NOW + 86_400})
    d = _time_state(tmp_path, fake, {"synced": False, "maxerror_us": 2_000_000})
    assert d["state"] == "red"
    assert "earlier than files" in d["detail"]
    assert d["label"] == "bad", "distinct from the no-source red, which the pill shows"


def test_a_synced_clock_is_not_red_just_because_a_directory_is_in_the_future(tmp_path):
    """A backward correction (or a restore from a box that ran fast) legitimately leaves LHPC's
    own directories stamped in the future. The clock is now STEERED and correct — calling that red
    would punish exactly the repair we ask operators to make."""
    fake = _time_fake(tmp_path, dirs={"/proc": ["412"]},
                      files={"/proc/412/comm": "chronyd\n"},
                      mtimes={str(tmp_path) + "/state": _NOW + 86_400})
    d = _time_state(tmp_path, fake, {"synced": True, "maxerror_us": 100_000})
    assert d["state"] == "green", d


def test_time_unknown_when_the_kernel_state_cannot_be_read(tmp_path):
    """The adjtimex path failing means we do not KNOW — which is not evidence of a bad clock and
    must never be shown as red."""
    d = _time_state(tmp_path, _time_fake(tmp_path), None)
    assert d["state"] == "unknown" and d["label"] == "…"      # the pill: source not known
    assert d["state"] != "red"
    assert d["local"] and d["utc"], "the clock is still reported, just not vouched for"


# --- the adjtimex call must stay a pure QUERY -----------------------------------------------------

def test_kernel_time_state_never_asks_the_kernel_to_change_anything(real_kernel_time_state, monkeypatch):
    """`modes = 0` is what makes `ntp_adjtime` a read. Setting any ADJ_* bit would turn this
    diagnostic into a clock adjustment — LHPC never sets, steps or disciplines the clock.
    """
    from lhpc.core import service_system as ss

    seen = {}

    class _FakeLibc:
        def ntp_adjtime(self, ptr):
            tx = ctypes.cast(ptr, ctypes.POINTER(ss._Timex)).contents
            seen["modes"] = tx.modes
            tx.status = 0
            tx.maxerror = 250_000
            return 0

    monkeypatch.setattr(ss, "_libc", lambda: _FakeLibc())
    out = ss.read_kernel_time_state()
    assert out == {"synced": True, "maxerror_us": 250_000}
    assert seen["modes"] == 0, "the struct must reach the kernel with NO mode bits set"
    # This IS the guard. A source scan for ADJ_* used to sit beside it and added nothing: the
    # struct that actually reaches the kernel is observed here, whatever the code looks like.


def _code_only(src: str) -> str:
    """`src` with comments and string literals removed — so a guard checks what the code DOES,
    not what its prose says about itself (the docstring here necessarily mentions ADJ_*)."""
    import io
    import tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


@pytest.mark.parametrize("rc, status, expect_synced", [
    (0, 0, True),
    (0, 0x0040, False),          # STA_UNSYNC set
    (5, 0, False),               # TIME_ERROR return code
])
def test_kernel_time_state_reads_sync_from_rc_and_status(real_kernel_time_state, monkeypatch, rc, status, expect_synced):
    from lhpc.core import service_system as ss

    class _FakeLibc:
        def ntp_adjtime(self, ptr):
            tx = ctypes.cast(ptr, ctypes.POINTER(ss._Timex)).contents
            tx.status = status
            tx.maxerror = 1
            return rc

    monkeypatch.setattr(ss, "_libc", lambda: _FakeLibc())
    assert ss.read_kernel_time_state()["synced"] is expect_synced


def test_kernel_time_state_is_none_when_anything_goes_wrong(real_kernel_time_state, monkeypatch):
    """No libc, odd ABI, EPERM — all of it means UNKNOWN, never a verdict about the clock."""
    from lhpc.core import service_system as ss

    def _boom(*a, **k):
        raise OSError("no libc here")

    # Both routes to UNKNOWN: the library will not load at all...
    monkeypatch.setattr(ss.ctypes, "CDLL", _boom)
    monkeypatch.setattr(ss, "_LIBC_CACHE", {})       # not already remembered from an earlier test
    assert ss.read_kernel_time_state() is None
    # ...and the call itself failing.
    monkeypatch.setattr(ss, "_libc", lambda: None)
    assert ss.read_kernel_time_state() is None


# --- timezone resolution --------------------------------------------------------------------------

def test_timezone_comes_from_the_localtime_symlink(tmp_path):
    fake = _time_fake(tmp_path, links={"/etc/localtime": "../usr/share/zoneinfo/Europe/Berlin"})
    assert _time_state(tmp_path, fake, None)["tz"] == "Europe/Berlin"


def test_timezone_falls_back_to_etc_timezone(tmp_path):
    """No symlink (some images ship a copied file) — the text file still names the zone."""
    fake = _time_fake(tmp_path, files={"/etc/timezone": "Atlantic/Reykjavik\n"})
    assert _time_state(tmp_path, fake, None)["tz"] == "Atlantic/Reykjavik"


def test_timezone_falls_back_to_the_utc_offset(tmp_path):
    """Neither source available: an offset is always computable and never pretends to be a zone."""
    tz = _time_state(tmp_path, _time_fake(tmp_path), None)["tz"]
    assert tz.startswith("UTC") and ":" in tz, tz


def test_timezone_ignores_a_link_target_that_is_not_a_zone(tmp_path):
    fake = _time_fake(tmp_path, links={"/etc/localtime": "/etc/something-else"})
    assert _time_state(tmp_path, fake, None)["tz"].startswith("UTC")


# --- GET-safety: the widened mechanism must not have widened into subprocess ----------------------

def test_the_host_metrics_module_never_reaches_a_subprocess(tmp_path):
    """The one place a TEXTUAL check earns its keep: "this module spawns nothing, ever" is a
    negative invariant over the whole file, and driving code paths can only prove it for the
    paths driven. `_code_only` tokenizes away strings and comments first, so `timedatectl` and
    `chrony` may still appear as hint TEXT for the operator to run themselves — which is exactly
    not the same thing as lhpc invoking them. The behavioural half is the test below.
    """
    import inspect

    from lhpc.core import service_system as ss
    code = _code_only(inspect.getsource(ss))
    for banned in ("subprocess", "os.system", "os.popen", "os.fork", "os.exec", "pty.spawn",
                   "timedatectl"):
        assert banned not in code, f"service_system must never reach {banned}"
    # The ONE approved exception: on a chrony box, one `chronyc -n -c tracking` through the
    # injected runner (the source when synced, the RMS offset when not); nothing else.
    fake = _time_fake(tmp_path, dirs={"/proc": ["412"]},
                      files={"/proc/412/comm": "chronyd\n"}, commands=_TRACK_NTP)
    _time_state(tmp_path, fake, {"synced": True, "maxerror_us": 100})
    assert fake.calls == [list(_CHRONYC)], fake.calls
    # Unsynced chrony: the same ONE call, for its RMS offset (did it sync earlier this run?).
    fake2 = _time_fake(tmp_path, dirs={"/proc": ["412"]}, files={"/proc/412/comm": "chronyd\n"})
    _time_state(tmp_path, fake2, {"synced": False, "maxerror_us": 900_000})
    assert fake2.calls == [list(_CHRONYC)], fake2.calls
    fake3 = _time_fake(tmp_path, dirs={"/proc": ["7"]}, files={"/proc/7/comm": "systemd-timesyn\n"})
    _time_state(tmp_path, fake3, {"synced": False, "maxerror_us": 900_000})
    assert fake3.calls == [], "no call on a box without chrony"


def test_the_chrony_source_read_is_cached_for_a_minute(tmp_path, monkeypatch):
    import time as _time
    fake = _time_fake(tmp_path, dirs={"/proc": ["412"]}, files={"/proc/412/comm": "chronyd\n"},
                      commands=_TRACK_GPS)
    svc = _svc(tmp_path, fake)
    clock = [1000.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    assert svc._sync_source_name(["chrony"], "chrony") == "GNSS"
    clock[0] += 30
    assert svc._sync_source_name(["chrony"], "chrony") == "GNSS"
    assert fake.calls == [list(_CHRONYC)], "one read within the minute"
    clock[0] += 31
    svc._sync_source_name(["chrony"], "chrony")
    assert len(fake.calls) == 2, "re-read after 60 s"


def test_reading_the_kernel_clock_forks_nothing(real_kernel_time_state, monkeypatch):
    """BEHAVIOURAL guard, because the textual one above cannot see through a helper.

    `ctypes.util.find_library("c")` runs `/sbin/ldconfig -p` — a real subprocess, once per
    `/api/system` poll, inside the very module whose contract forbids one. Text-matching the
    source never showed it; only watching for a fork does.
    """
    import subprocess

    from lhpc.core import service_system as ss
    spawned = []
    for name in ("Popen", "run", "call", "check_output"):
        monkeypatch.setattr(subprocess, name,
                            lambda *a, _n=name, **k: spawned.append(_n) or (_ for _ in ()).throw(
                                AssertionError(f"time path spawned a process via subprocess.{_n}")))
    monkeypatch.setattr(os, "system",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("os.system")))
    ss.read_kernel_time_state()
    assert spawned == []


def test_time_never_claims_the_clock_is_correct(tmp_path):
    """Wording constraint: LHPC cannot prove correctness without an external reference."""
    for kernel in ({"synced": True, "maxerror_us": 100}, {"synced": False, "maxerror_us": 9_000_000},
                   None):
        d = _time_state(tmp_path, _time_fake(tmp_path), kernel)
        blob = " ".join(str(v) for v in d.values()).lower()
        for claim in ("correct", "accurate", "exact", "verified time", "right time"):
            assert claim not in blob, f"{claim!r} appears in {d}"


def test_gpsd_is_not_treated_as_a_clock_discipliner(tmp_path):
    """gpsd feeds time to chrony/ntpd over SHM; it never disciplines the kernel clock itself.
    Counting it made the ORDINARY "gpsd + timesyncd" pairing look like two daemons fighting."""
    fake = _time_fake(tmp_path, dirs={"/proc": ["10", "11"]},
                      files={"/proc/10/comm": "gpsd\n", "/proc/11/comm": "systemd-timesyn\n"},
                      mtimes={"/run/systemd/timesync/synchronized": _NOW - 60})
    d = _time_state(tmp_path, fake, {"synced": True, "maxerror_us": 200_000})
    assert d["state"] == "green", "a normal, supported configuration must not read as a conflict"
    assert d["daemons"] == ["systemd-timesyncd"]


def test_gpsd_alone_is_never_named_as_the_time_source(tmp_path):
    """It cannot be: nothing is steering the kernel clock in that state."""
    fake = _time_fake(tmp_path, dirs={"/proc": ["10"]}, files={"/proc/10/comm": "gpsd\n"})
    d = _time_state(tmp_path, fake, {"synced": False, "maxerror_us": 3_000_000})
    assert d["source"] != "gpsd"
    assert d["daemons"] == []


def test_the_zone_label_matches_the_zone_the_timestamp_is_in(tmp_path, monkeypatch):
    """`TZ` in the environment is what `time.localtime()` honours. Reading the label from
    /etc/localtime instead labelled a Bogota reading as Europe/Berlin."""
    monkeypatch.setenv("TZ", "America/Bogota")
    fake = _time_fake(tmp_path, links={"/etc/localtime": "../usr/share/zoneinfo/Europe/Berlin"})
    assert _time_state(tmp_path, fake, None)["tz"] == "America/Bogota"


def test_rtc_presence_is_reported_even_when_the_kernel_state_is_unreadable(tmp_path):
    """RTC presence is a file read, independent of adjtimex. Omitting it let the panel render
    "RTC: no" for a box that has one."""
    fake = _time_fake(tmp_path, files={"/sys/class/rtc/rtc0/name": "rtc-ds3231\n"})
    d = _time_state(tmp_path, fake, None)
    assert d["state"] == "unknown"
    assert d["rtc_present"] is True, "the facts we CAN read are still reported"
    assert d["tz"], "same for the zone"


def test_each_state_carries_its_own_label(tmp_path):
    """The pill showed "no time source" for every red, including the future-timestamp case."""
    seen = {}
    seen["green"] = _time_state(tmp_path, _time_fake(
        tmp_path, dirs={"/proc": ["1"]}, files={"/proc/1/comm": "chronyd\n"}, commands=_TRACK_NTP),
        {"synced": True, "maxerror_us": 1000})
    seen["nosource"] = _time_state(tmp_path, _time_fake(tmp_path),
                                   {"synced": False, "maxerror_us": 2_000_000})
    seen["future"] = _time_state(tmp_path, _time_fake(
        tmp_path, mtimes={str(tmp_path) + "/state": _NOW + 86_400}),
        {"synced": False, "maxerror_us": 2_000_000})
    assert seen["green"]["label"] == "NTP"
    assert seen["nosource"]["label"] == "none"
    assert seen["future"]["label"] == "bad"
    assert seen["future"]["label"] != seen["nosource"]["label"], "distinct reds, distinct labels"


def test_a_daemon_that_has_not_synced_yet_beats_the_future_mtime_heuristic(tmp_path):
    """FOUND IN REVIEW: the guard fired on `not synced`, so a detected-but-not-yet-synchronised
    chronyd plus restored future directory metadata reported red "implausible" — masking the
    accurate, more useful yellow "time daemon running, not synced yet". The heuristic is only
    meaningful when NOTHING is steering the clock."""
    fake = _time_fake(tmp_path, **_chrony_box(mtimes={str(tmp_path) + "/state": _NOW + 86_400}))
    d = _time_state(tmp_path, fake, {"synced": False, "maxerror_us": 900_000})
    assert (d["state"], d["label"]) == ("red", "none"), d          # not "bad"
    assert "not synced yet" in d["detail"]
    assert d.get("hint_cmd", "") == "", "waiting is the correct action; offer nothing to run"


@pytest.mark.parametrize("kw, kernel", [
    ({"mtimes": {"/var/lib/systemd/timesync/clock": _NOW - 7200}},
     {"synced": False, "maxerror_us": 900_000}),
    ({"files": {"/sys/class/rtc/rtc0/name": "rtc-ds3231\n",
                "/sys/class/rtc/rtc0/hctosys": "1"}},
     {"synced": False, "maxerror_us": 900_000}),
])
def test_a_restore_artifact_also_beats_the_future_mtime_heuristic(tmp_path, kw, kernel):
    """Same rule for the other candidate sources: a restored clock is unverified, not implausible."""
    kw = dict(kw)
    kw.setdefault("mtimes", {})["state"] = None
    kw["mtimes"] = {k: v for k, v in kw["mtimes"].items() if v is not None}
    kw["mtimes"][str(tmp_path) + "/state"] = _NOW + 86_400
    d = _time_state(tmp_path, _time_fake(tmp_path, **kw), kernel)
    assert d["label"] in ("fake", "RTC"), d                        # never "bad"


def test_the_copyable_command_is_a_command_and_nothing_else(tmp_path):
    """The element carrying it is select-all, so it must paste into a shell verbatim. The old
    single string ended "(or install chrony)" — a syntax error. Prose lives in `hint`."""
    d = _time_state(tmp_path, _time_fake(tmp_path), {"synced": False, "maxerror_us": 2_000_000})
    cmd = d["hint_cmd"]
    assert cmd == "sudo timedatectl set-ntp true"
    for junk in ("(", ")", "  or ", "\n"):
        assert junk not in cmd, f"{junk!r} in a select-all command breaks the paste"
    assert d["hint"] and d["hint"] != cmd, "the prose is a separate field"


def test_guidance_is_state_specific_and_never_wrong(tmp_path):
    """"Enable NTP" is wrong advice when a daemon already runs, and wronger when two are
    fighting — the old code showed exactly that string in both cases."""
    conflict = _time_state(tmp_path, _time_fake(
        tmp_path, dirs={"/proc": ["1", "2"]},
        files={"/proc/1/comm": "chronyd\n", "/proc/2/comm": "systemd-timesyn\n"}),
        {"synced": True, "maxerror_us": 100})
    assert conflict["label"] == "dup"
    assert "disable one" in conflict["hint"]
    assert "hint_cmd" not in conflict, "which daemon to disable is the operator's call"
    assert "set-ntp" not in conflict["hint"], "must not tell them to enable yet another daemon"

    waiting = _time_state(tmp_path, _time_fake(
        tmp_path, dirs={"/proc": ["1"]}, files={"/proc/1/comm": "chronyd\n"}),
        {"synced": False, "maxerror_us": 900_000})
    assert "no action" in waiting["hint"] and "hint_cmd" not in waiting


@pytest.mark.parametrize("daemon, comm, shown", [
    ("chrony", "chronyd\n", "chrony"),
    ("systemd-timesyncd", "systemd-timesyn\n", "systemd-timesyncd"),
    ("ntpd", "ntpd\n", "ntpd"),
])
@pytest.mark.parametrize("kernel, why", [
    ({"synced": True, "maxerror_us": 5_000_000}, "synced, but estimated error above tolerance"),
    ({"synced": False, "maxerror_us": 900_000}, "running, first sync still pending"),
])
def test_a_running_daemon_is_never_told_to_enable_another_one(tmp_path, daemon, comm, shown,
                                                              kernel, why):
    """FOUND IN REVIEW: with chronyd active and the error above tolerance, the row advised
    `sudo timedatectl set-ntp true` — enabling a SECOND discipliner, which is both useless and
    the direct cause of the two-daemon conflict this same row flags as a fault. When a real
    daemon is running, the only useful direction is that daemon's own upstream.
    """
    fake = _time_fake(tmp_path, **(_chrony_box() if comm == "chronyd\n" else
                                   {"dirs": {"/proc": ["412"]}, "files": {"/proc/412/comm": comm}}))
    d = _time_state(tmp_path, fake, kernel)
    assert d["state"] in ("yellow", "red"), why
    assert d.get("hint_cmd", "") == "", f"nothing to run while {shown} is active ({why})"
    hint = d["hint"].lower()
    for wrong in ("enable a time daemon", "install chrony", "set-ntp"):
        assert wrong not in hint, f"advised {wrong!r} while {shown} was already running"
    assert "wait" in hint or "no action" in hint, d["hint"]


def test_the_advice_names_the_daemon_that_is_actually_running(tmp_path):
    """Naming it is what makes the advice actionable — "check that daemon's source" is useless
    if the operator cannot see which daemon is meant."""
    fake = _time_fake(tmp_path, dirs={"/proc": ["412"]}, files={"/proc/412/comm": "chronyd\n"})
    d = _time_state(tmp_path, fake, {"synced": True, "maxerror_us": 5_000_000})
    assert "chrony" in d["hint"]
    assert "error above tolerance" in d["detail"]


def test_a_box_with_no_source_and_no_rtc_is_red_not_yellow(tmp_path):
    """The CHANGELOG claimed such a box "sits at yellow, which is its correct steady state".
    It does not: with no daemon, no restore artifact and no RTC, nothing is holding that clock
    up, and the pin rules make it red."""
    fake = _time_fake(tmp_path)                       # no daemons, no artifacts, no RTC
    d = _time_state(tmp_path, fake, {"synced": False, "maxerror_us": 2_000_000})
    assert d["rtc_present"] is False
    assert d["state"] == "red" and d["label"] == "none" and d["detail"] == "no time source"
    assert d["hint_cmd"] == "sudo timedatectl set-ntp true", "here enabling one IS the advice"
