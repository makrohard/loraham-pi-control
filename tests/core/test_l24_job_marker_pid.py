"""L24 F3: a job marker's pid is a real pid (an int, never a bool, a float or a string, within the kernel's
PID_MAX_LIMIT) or the marker is malformed. The input matrix measured `pid = inf`, `-inf` and `1e400` raising
OverflowError out of log_running and active_jobs, and `pid = [1]` raising TypeError out of active_jobs: one bad
marker broke the log page and the dashboard's job list. Now: log_running says not running, the listing skips the
marker, and the self-update gate's include_unsafe view names it; the marker is kept for diagnosis."""
import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# TOML literals the marker can hold (the probe's classes).
BAD = {"inf": "inf", "-inf": "-inf", "1e400": "1e400", "nan": "nan", "list": "[1]", "bool": "true",
       "digit-string": '"5"', "float": "5.7", "401-digits": "1" + "0" * 400, "zero": "0", "negative": "-5",
       "above-pid-max": str((1 << 22) + 1)}


def _svc(tmp_path, pid_literal):
    jobs = tmp_path / "state" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "probe.job").write_text(f'pid = {pid_literal}\nstarttime = 5\nlog = "x.log"\ntarget = "chat"\n')
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


@pytest.mark.parametrize("lit", list(BAD.values()), ids=list(BAD))
def test_log_running_reads_a_bad_pid_as_not_running(tmp_path, lit):
    assert _svc(tmp_path, lit).log_running("chat", job="probe") is False


@pytest.mark.parametrize("lit", list(BAD.values()), ids=list(BAD))
def test_the_job_listing_skips_a_bad_pid_and_keeps_the_marker(tmp_path, lit):
    svc = _svc(tmp_path, lit)
    assert svc.active_jobs(cleanup=False) == []
    assert (tmp_path / "state" / "jobs" / "probe.job").exists()


@pytest.mark.parametrize("lit", list(BAD.values()), ids=list(BAD))
def test_the_update_gate_sees_a_bad_pid_as_unsafe(tmp_path, lit):
    got = _svc(tmp_path, lit).active_jobs(include_unsafe=True)
    assert [(j.get("unsafe"), j.get("name")) for j in got] == [(True, "probe.job")], got
