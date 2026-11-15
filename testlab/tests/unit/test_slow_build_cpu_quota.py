"""The slow-build lane's per-run CPU quota (testlab/slowbuild/cpu_quota.py, flake F13): SLOW_CPUS =
min(0.25, floor_3dp(0.85 x W1 / Z_cpu)), W1 the calibration's cpu build at --cpus 1 on this runner, Z_cpu the newest
zero2w cpu_s of the current workload. The timed build itself runs on CI (an arm runner with docker); here it is
replaced by its measured seconds."""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "slowbuild" / "cpu_quota.py"
_spec = importlib.util.spec_from_file_location("cpu_quota", _PATH)
cpu_quota = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpu_quota)


def test_the_current_workload_names_a_zero_calibration():
    """The hash is calibrate.sh's: were it computed otherwise, no zero2w row would carry it and the lane's step would
    refuse."""
    rows = tomllib.loads(cpu_quota.BASELINE.read_text())["calibration"]
    assert cpu_quota.workload() in {r["workload"] for r in rows if r.get("source") == "zero2w"}


@pytest.mark.parametrize("w1, z_cpu, want", [
    (19.0, 99.3, 0.162),       # the faster runner of testlab 43626338096: 75.9 s at 0.25 CPUs is ~19 s of CPU
    (28.9, 99.7, 0.246),       # the runner the fixed 0.25 was sized on (57.8 s at 0.5 CPUs)
    (40.0, 99.3, 0.25),        # a slower runner: the ceiling holds, the throttle never loosens
    (11.68, 99.3, 0.099),      # floored to 3 places, never rounded up
])
def test_the_quota_keeps_the_margin_under_the_ceiling(w1, z_cpu, want):
    assert cpu_quota.quota(w1, z_cpu) == want


def test_a_throttled_build_at_the_quota_is_at_least_the_zeros_time():
    """The point of the quota: W1 / q (the throttled build, CPU-time bound) is at or above the Zero's cpu_s."""
    for w1 in (5.0, 11.68, 19.0, 28.9):
        q = cpu_quota.quota(w1, 99.3)
        assert w1 / q >= 99.3 / cpu_quota.MARGIN * 0.999


def test_the_newest_zero_row_of_the_workload_decides():
    rows = [{"source": "zero2w", "workload": "sha256:w", "cpu_s": 98.0, "date": dt.date(2026, 10, 3)},
            {"source": "zero2w", "workload": "sha256:w", "cpu_s": 99.5, "date": dt.date(2026, 10, 4)},
            {"source": "zero2w", "workload": "sha256:other", "cpu_s": 50.0, "date": dt.date(2026, 10, 9)},
            {"source": "throttled-ci", "workload": "sha256:w", "cpu_s": 10.0, "date": dt.date(2026, 10, 9)}]
    assert cpu_quota.zero_cpu_s(rows, "sha256:w") == 99.5


@pytest.mark.parametrize("rows, why", [
    ([], "no zero2w [[calibration]] for sha256:w"),
    ([{"source": "zero2w", "workload": "sha256:w", "cpu_s": "fast", "date": dt.date(2026, 10, 4)}], "no usable cpu_s"),
    ([{"source": "zero2w", "workload": "sha256:w", "cpu_s": 0, "date": dt.date(2026, 10, 4)}], "no usable cpu_s"),
])
def test_without_a_usable_zero_row_the_step_refuses_by_name(rows, why):
    with pytest.raises(SystemExit, match=re.escape(why)):
        cpu_quota.zero_cpu_s(rows, "sha256:w")


def test_the_step_prints_the_quota_for_the_job_env(monkeypatch, capsys):
    monkeypatch.setattr(cpu_quota, "measure_w1", lambda image: 19.0)
    assert cpu_quota.main(["--image", "lhpc-testlab:ci"]) == 0
    out, err = capsys.readouterr()
    z = cpu_quota.zero_cpu_s(tomllib.loads(cpu_quota.BASELINE.read_text())["calibration"], cpu_quota.workload())
    assert out == f"SLOW_CPUS={cpu_quota.quota(19.0, z)}\n"
    assert "W1 19.0 s at --cpus 1" in err and f"Zero cpu_s {z} s" in err
