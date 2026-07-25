"""System box backend: pure procfs/sysfs parsers + the stateless `system_stats()` contract.

Behaviour under test: raw counters + monotonic ts (browser computes rates), fail-soft omission of
absent sources, truthful Power reporting (unknown is never synthesized), and the no-subprocess
guarantee of the GET path.
"""

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_system import (
    parse_dt_model,
    parse_loadavg,
    parse_meminfo,
    parse_net_dev,
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
            "/": {"total_b": 31000000000, "free_b": 21000000000, "dev": 100},
            str(tmp_path): {"total_b": 31000000000, "free_b": 21000000000, "dev": 100},
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
    assert d["disk"]["root"] == {"total_b": 31000000000, "free_b": 21000000000}
    assert "runtime" not in d["disk"]           # same st_dev as / -> no duplicate row
    assert d["temp_mc"] == 48312
    assert d["power"] == {"source": "hwmon-alarm", "undervolt_alarm": False}
    assert d["uptime_s"] == 12345.67
    assert d["info"]["model"] == "Raspberry Pi 5 Model B Rev 1.0"
    assert d["info"]["os"] == "Raspberry Pi OS Lite"


def test_system_stats_runtime_disk_on_other_device(tmp_path):
    fake = _full_fake(tmp_path)
    fake.statvfs_data[str(tmp_path)] = {"total_b": 500, "free_b": 400, "dev": 200}
    d = _svc(tmp_path, fake).system_stats()
    assert d["disk"]["runtime"] == {"path": str(tmp_path), "total_b": 500, "free_b": 400}


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
