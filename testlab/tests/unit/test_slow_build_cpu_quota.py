"""The slow-build lane's per-run CPU quota (testlab/slowbuild/cpu_quota.py, flake F13): SLOW_CPUS =
min(0.25, floor_2dp(0.85 x W1 / Z_cpu)), W1 the calibration's cpu build at --cpus 1 on this runner, Z_cpu the newest
zero2w cpu_s of the current workload. The timed build itself runs on CI (an arm runner with docker); here it is
replaced by its measured seconds, or `docker` by a stand-in that fails or hangs."""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
import sys
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
    (19.0, 99.3, 0.16),        # the faster runner of testlab 43626338096: 75.9 s at 0.25 CPUs is ~19 s of CPU
    (28.9, 99.7, 0.24),        # the runner the fixed 0.25 was sized on (57.8 s at 0.5 CPUs)
    (27.6, 99.3, 0.23),        # testlab 43852504845's runner: 0.236 at 3 places, enforced as 0.24
    (40.0, 99.3, 0.25),        # a slower runner: the ceiling holds, the throttle never loosens
    (11.68, 99.3, 0.09),       # floored to 2 places, never rounded up
])
def test_the_quota_keeps_the_margin_under_the_ceiling(w1, z_cpu, want):
    assert cpu_quota.quota(w1, z_cpu) == want


def test_the_quota_is_whole_hundredths_as_the_engine_enforces_it():
    """--cpus is enforced in hundredths (testlab 43852504845: 0.236 became cpu.max 24000 100000 and the lane's check
    refused it): every quota the step prints is a whole number of hundredths, so cpu.max is the quota as given."""
    for tenth_s in range(50, 400):
        q = cpu_quota.quota(tenth_s / 10, 99.3)
        assert re.fullmatch(r"0\.\d{1,2}", str(q)), q


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


def _docker(tmp_path, monkeypatch, body: str):
    """A stand-in `docker` first on PATH: it records its argv, then runs `body`."""
    exe = tmp_path / "bin" / "docker"
    exe.parent.mkdir()
    exe.write_text(f"#!{sys.executable}\nimport sys\nopen({str(tmp_path / 'argv')!r}, 'w').write('\\0'.join(sys.argv[1:]))\n"
                   f"{body}")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{exe.parent}:{__import__('os').environ['PATH']}")
    return lambda: (tmp_path / "argv").read_text().split("\0")


def test_the_timed_build_runs_as_root_and_a_failed_make_ends_the_script(tmp_path, monkeypatch):
    """The lane's container runs as root; the image's default user could not enter the 0700 work dir, and a make that
    failed there still printed a time (W1 0.01 s with a non-root image). Now root, and `set -e` before the timing."""
    argv = _docker(tmp_path, monkeypatch, "print('100.0 127.5')\n")
    assert cpu_quota.measure_w1("img") == 27.5
    got = argv()
    assert got[got.index("--user") + 1] == "root" and got.index("--user") < got.index("img")
    assert got[-1].split("; ")[0] == "set -e"


def test_a_failed_timed_build_is_a_stop_naming_the_command_and_its_output(tmp_path, monkeypatch):
    _docker(tmp_path, monkeypatch, "print('partial', flush=True)\n"
                                   "sys.stderr.write('make: *** /w: Permission denied.  Stop.\\n')\nsys.exit(2)\n")
    with pytest.raises(SystemExit) as stop:
        cpu_quota.measure_w1("img")
    why = str(stop.value)
    assert "the timed build failed (rc 2): docker run " in why and " img bash -c " in why
    assert "stdout: partial" in why and "Permission denied" in why


@pytest.mark.wallclock("a real stand-in hangs past the step's bound (0.5 s here)")
def test_a_timed_build_that_hangs_is_a_stop_after_its_bound(tmp_path, monkeypatch):
    _docker(tmp_path, monkeypatch, "import time\nprint('started', flush=True)\ntime.sleep(60)\n")
    with pytest.raises(SystemExit) as stop:
        cpu_quota.measure_w1("img", timeout=0.5)
    why = str(stop.value)
    assert "the timed build did not end within 0.5 s: docker run " in why and "stdout: started" in why
