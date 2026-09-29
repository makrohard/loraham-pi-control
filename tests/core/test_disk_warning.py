"""The disk-space warning: ONE classifier (bytes AND inodes), read by doctor, status, the console
notice, the System row and the watchdog's one log line per change."""
import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_system import disk_level, worst_disk_row
from lhpc.core.services import ControllerService

GIB = 1024 ** 3
MIB = 1024 ** 2


# ---- the classifier's boundaries: below → the level, at/above → the next better one ----------
@pytest.mark.parametrize("total, free, expected", [
    # 8 GiB: the FLOORS decide (10 % = 0.8 GiB < 1.5 GiB; 5 % = 409.6 MiB < 500 MiB)
    (8 * GIB, int(1.5 * GIB) - 1, "low"), (8 * GIB, int(1.5 * GIB), "ok"), (8 * GIB, int(1.5 * GIB) + 1, "ok"),
    (8 * GIB, 500 * MIB - 1, "critical"), (8 * GIB, 500 * MIB, "low"), (8 * GIB, 500 * MIB + 1, "low"),
    # 100 GiB: the SHARES decide (10 % = 10 GiB; 5 % = 5 GiB). 100, not 128: 10 % of 128 GiB is not a
    # whole number of bytes, so "exactly at" would not exist there.
    (100 * GIB, 10 * GIB - 1, "low"), (100 * GIB, 10 * GIB, "ok"), (100 * GIB, 10 * GIB + 1, "ok"),
    (100 * GIB, 5 * GIB - 1, "critical"), (100 * GIB, 5 * GIB, "low"), (100 * GIB, 5 * GIB + 1, "low"),
])
def test_disk_health_levels_bytes(total, free, expected):
    assert disk_level(total, free)[0] == expected


@pytest.mark.parametrize("free_inodes, expected", [
    (9999, "low"), (10000, "ok"), (10001, "ok"),        # 10 % of 100000
    (4999, "critical"), (5000, "low"), (5001, "low"),   # 5 %
])
def test_disk_health_levels_inodes(free_inodes, expected):
    level, reason = disk_level(128 * GIB, 100 * GIB, 100000, free_inodes)
    assert level == expected
    assert reason == ("" if expected == "ok" else "inodes")


def test_a_dynamic_inode_filesystem_never_goes_low_on_inodes():
    assert disk_level(128 * GIB, 100 * GIB, 0, 0) == ("ok", "")


@pytest.mark.parametrize("free_b, free_inodes, expected", [
    (int(1.4 * GIB), 4000, ("critical", "inodes")),     # bytes low + inodes critical
    (400 * MIB, 9000, ("critical", "bytes")),           # bytes critical + inodes low
    (int(1.4 * GIB), 9000, ("low", "bytes")),           # equal severity: bytes is the tie-break
])
def test_the_worse_metric_sets_level_and_reason(free_b, free_inodes, expected):
    assert disk_level(8 * GIB, free_b, 100000, free_inodes) == expected


def test_the_worst_row_wins_and_row_order_breaks_a_tie():
    rows = [{"path": "/", "level": "low"}, {"path": "/rt", "level": "critical"}]
    assert worst_disk_row(rows)["path"] == "/rt"
    rows = [{"path": "/", "level": "low"}, {"path": "/rt", "level": "low"}]
    assert worst_disk_row(rows)["path"] == "/"


# ---- the surfaces -----------------------------------------------------------------------------
def _svc(tmp_path, root, runtime=None):
    data = {"/": {"dev": 1, **root}}
    data[str(tmp_path)] = {"dev": 2, **runtime} if runtime else {"dev": 1, **root}
    return ControllerService(system=FakeSystem(statvfs_data=data).system,
                             paths=Paths(runtime_root=tmp_path))


def _status_disk(svc):
    return [d for d in svc.status().details if d.startswith("disk:")]


def _doctor_disk(svc):
    res = svc.doctor()
    return res.ok, [d for d in res.details if d.strip().startswith("disk ")]


def test_status_and_doctor_are_silent_about_a_healthy_disk(tmp_path):
    svc = _svc(tmp_path, {"total_b": 32 * GIB, "free_b": 20 * GIB,
                          "total_inodes": 100000, "free_inodes": 90000})
    assert _status_disk(svc) == []
    ok, lines = _doctor_disk(svc)
    assert ok and lines == ["  disk /: 20.0 GiB free of 32 GiB, 10 % inodes used: ok"]


def test_an_inode_driven_level_says_inodes_on_every_surface(tmp_path):
    svc = _svc(tmp_path, {"total_b": 32 * GIB, "free_b": 20 * GIB,
                          "total_inodes": 100000, "free_inodes": 4000})
    assert _status_disk(svc) == ["disk: CRITICAL (/: 4 % inodes free)"]
    ok, lines = _doctor_disk(svc)
    assert not ok and "(/: 4 % inodes free)" in lines[0]
    row = svc.system_stats()["disk"]["root"]
    assert (row["level"], row["reason"]) == ("critical", "inodes")


@pytest.mark.parametrize("free_b, free_inodes, status, level_reason", [
    (int(1.4 * GIB), 4000, "disk: CRITICAL (/: 4 % inodes free)", ("critical", "inodes")),  # bytes low
    (400 * MIB, 9000, "disk: CRITICAL (/: 0.4 GiB free)", ("critical", "bytes")),         # inodes low
])
def test_the_worse_metric_decides_on_every_surface(tmp_path, free_b, free_inodes, status, level_reason):
    svc = _svc(tmp_path, {"total_b": 8 * GIB, "free_b": free_b,
                          "total_inodes": 100000, "free_inodes": free_inodes})
    assert _status_disk(svc) == [status]
    row = svc.system_stats()["disk"]["root"]
    assert (row["level"], row["reason"]) == level_reason


def test_a_low_disk_is_informational_for_doctor(tmp_path):
    svc = _svc(tmp_path, {"total_b": 8 * GIB, "free_b": GIB})
    assert _status_disk(svc) == ["disk: LOW (/: 1.0 GiB free)"]
    ok, lines = _doctor_disk(svc)
    assert ok and lines[0].endswith(": low (/: 1.0 GiB free)")


@pytest.mark.parametrize("root", [
    {"total_b": 8 * GIB, "free_b": GIB},                # / low
    {"total_b": 32 * GIB, "free_b": 20 * GIB},          # / ok
])
def test_two_filesystems_the_worst_decides(tmp_path, root):
    svc = _svc(tmp_path, root, runtime={"total_b": 8 * GIB, "free_b": 100 * MIB})
    assert _status_disk(svc) == [f"disk: CRITICAL ({tmp_path}: 0.1 GiB free)"]
    ok, lines = _doctor_disk(svc)
    assert not ok and len(lines) == 2
    disk = svc.system_stats()["disk"]
    assert set(disk) == {"root", "runtime"} and disk["runtime"]["level"] == "critical"
