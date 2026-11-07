"""L24 F2: a recorded process whose start time cannot be compared is not proven ceased. The input matrix measured
service_base._proc_ceased reading a LIVE process as ceased for every start_time that is not the exact int
(absent, null, true, a string, a float, NaN, ±inf, 401 digits, negative, zero, a list, an object). Through the
public recovery: an in-flight update record naming this live test process must keep "still running", and the
record stays. A pid that no longer exists is ceased whatever the start time says."""
import json
import os

import pytest

from lhpc.core import procident
from lhpc.core.services import _proc_start_time

ME = os.getpid()
_MISSING = object()
BAD_STARTS = [_MISSING, None, True, "123", float(_proc_start_time(ME)), float("nan"), float("inf"),
              float("-inf"), 10 ** 400, -5, 0, [1], {"a": 1}]


def _inflight(tmp_path, start):
    rec = {"mode": "normal", "pid": ME}
    if start is not _MISSING:
        rec["start_time"] = start
    (tmp_path / "state" / "selfupdate.inflight").write_text(json.dumps(rec))


IDS = ["missing", "null", "bool", "digit-string", "float-of-the-start", "nan", "inf", "-inf", "401-digits",
       "negative", "zero", "list", "object"]


@pytest.mark.parametrize("start", BAD_STARTS, ids=IDS)
def test_a_live_helper_with_an_uncomparable_start_time_is_still_running(tmp_path, op_svc, start):
    svc, _fake = op_svc(units=True, invocation=True)
    _inflight(tmp_path, start)
    assert svc.classify_request() == "in_flight"
    res = svc.self_update_recover_request()
    assert not res.ok and res.data.get("state") == "running", (res.summary, res.data)
    assert (tmp_path / "state" / "selfupdate.inflight").exists()


def test_an_unreadable_stat_of_a_live_process_is_not_ceased(tmp_path, op_svc, monkeypatch):
    svc, _fake = op_svc(units=True, invocation=True)
    _inflight(tmp_path, _proc_start_time(ME))
    monkeypatch.setattr(procident, "stat_start_time", lambda pid: 0)   # /proc/<pid>/stat unreadable
    res = svc.self_update_recover_request()
    assert not res.ok and res.data.get("state") == "running", (res.summary, res.data)


def test_a_pid_that_no_longer_exists_is_ceased_whatever_the_start_time(tmp_path, op_svc):
    svc, _fake = op_svc(units=True, invocation=True)
    (tmp_path / "state" / "selfupdate.inflight").write_text(json.dumps({"mode": "normal", "pid": 4194303}))
    assert not os.path.exists("/proc/4194303")
    res = svc.self_update_recover_request()
    assert res.ok and res.data.get("cleared") == "in_flight", (res.summary, res.data)
