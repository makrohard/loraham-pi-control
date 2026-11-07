"""The reader input matrix (L24, READER-INPUT-MATRIX-v1) as a regression test: every numeric field the matrix
measured, fed each input class through the reader's own public function, gives the safe answer — refused, not
fresh, not ceased, not running, unsafe or unchecked — and never raises out of a reader that must not raise.
The per-defect tests (test_l24_*.py) hold the edges; this file holds the whole matrix in one place, so a new
regression in any measured cell fails here."""
import json
import os
import time

import pytest

from lhpc.core import boot_restore as br
from lhpc.core import gps, power, procident, restart_required, selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_MISSING = object()
# The matrix's input classes (values as JSON or Python carries them).
CLASSES = {"missing": _MISSING, "null": None, "bool": True, "digit-string": "5", "string-nan": "nan",
           "nan": float("nan"), "inf": float("inf"), "-inf": float("-inf"), "401-digits": 10 ** 400,
           "negative": -5, "list": [1], "object": {"a": 1}}
IDS = list(CLASSES)
VALUES = list(CLASSES.values())


def _with(base: dict, key: str, v) -> dict:
    d = dict(base)
    if v is _MISSING:
        d.pop(key, None)
    else:
        d[key] = v
    return d


@pytest.mark.parametrize("v", VALUES, ids=IDS)
def test_gps_marker_freshness(v):
    assert gps.marker_is_fresh(_with({}, "updated", v), time.time()) is False


@pytest.mark.parametrize("v", VALUES, ids=IDS)
def test_gps_marker_owner_pid(v):
    assert gps.marker_owner_pid(_with({}, "pid", v)) is None


@pytest.mark.parametrize("v", [v for v in VALUES if v is not _MISSING], ids=[i for i in IDS if i != "missing"])
def test_a_live_process_with_an_uncomparable_start_time_is_not_ceased(v):
    assert procident.recorded_ceased(os.getpid(), v) is False


# TOML literals of the classes a job marker can hold.
TOML = {"missing": None, "bool": "true", "digit-string": '"5"', "nan": "nan", "inf": "inf", "-inf": "-inf",
        "1e400": "1e400", "401-digits": "1" + "0" * 400, "negative": "-5", "list": "[1]", "float": "5.7"}


@pytest.mark.parametrize("lit", list(TOML.values()), ids=list(TOML))
def test_job_marker_pid(tmp_path, lit):
    jobs = tmp_path / "state" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "probe.job").write_text(("" if lit is None else f"pid = {lit}\n") + "starttime = 5\n")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.log_running("x", job="probe") is False
    assert svc.active_jobs(cleanup=False) == []
    assert [j.get("unsafe") for j in svc.active_jobs(include_unsafe=True)] == [True]


@pytest.mark.parametrize("field", ["process_start_time", "pid"])
@pytest.mark.parametrize("v", VALUES, ids=IDS)
def test_boot_journal_writer_identity(field, v):
    base = br.new_journal(boot_id="B", pid=42, process_start_time=7, items=[])
    assert br.validate_journal(json.loads(json.dumps(_with(base, field, v)))) != ""


@pytest.mark.parametrize("v", VALUES, ids=IDS)
def test_restart_marker_time(tmp_path, v):
    paths = Paths(runtime_root=tmp_path)
    p = restart_required.marker_path(paths, "chat")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_with({"version": 1, "stack": "chat", "mode": "restart", "params": [],
                                   "created_at": 1.0}, "created_at", v)))
    assert restart_required.read_marker(paths, "chat").get("unsafe") is True


@pytest.mark.parametrize("v", [v for v in VALUES if v is not _MISSING], ids=[i for i in IDS if i != "missing"])
def test_self_update_cache_stamp(tmp_path, v):
    paths = Paths(runtime_root=tmp_path)
    selfupdate.write_cache(paths, {"schema_version": selfupdate.CACHE_SCHEMA_VERSION, "checked_at": v})
    assert selfupdate.read_cache(paths) == {}


@pytest.mark.parametrize("v", VALUES, ids=IDS)
def test_power_pending_uptime_is_refused_by_raising(v):
    d = _with({"kind": "poweroff", "boot_id": "boot-1", "requested_uptime": 100.0}, "requested_uptime", v)
    with pytest.raises(Exception):               # by design: the caller catches it and refuses
        power.parse_pending_marker(json.dumps(d))
