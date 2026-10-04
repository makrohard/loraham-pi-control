"""The console's maintenance pass reports each task's outcome: a task that fails is shown, with its
time, by `lhpc doctor` and on the dashboard, the other tasks still run, a later success replaces
the failure, and the pass keeps its cadence. Driven through the web's real pass
(`network_watch_pass`) on a real service over a fake box; the failing task is the service method
replaced by a fake with its real signature."""

import re

import pytest
from htmlq import parse

from lhpc.adapters.web.app import network_watch_pass
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# task (as doctor and the dashboard name it) -> the service method that runs it
TASKS = {
    "deferred-apply": "webserver_apply_complete_pending",
    "crl-refresh": "crl_refresh_if_expired",
    "clock-normalise": "pki_clock_normalise",
    "start-logs": "cap_start_logs",
    "controller-logs": "cap_controller_logs",
    "rf-logs": "rflog_roll_native_all",
}
UTC = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.bootstrap(apply=True).ok
    return svc


def _ran(monkeypatch, skip):
    """Record each task method's call, delegating to the real one (all but `skip`)."""
    calls = []
    for name, meth in TASKS.items():
        if name == skip:
            continue
        real = getattr(ControllerService, meth)

        def rec(self, _real=real, _name=name):
            calls.append(_name)
            return _real(self)
        monkeypatch.setattr(ControllerService, meth, rec)
    return calls


def _notice(web, svc):
    page = web(service_factory=lambda: svc).get("/")
    assert page.status_code == 200
    el = parse(page.get_data(as_text=True)).by_id("maintenance-notice")
    return el.text if el is not None else None


@pytest.mark.parametrize("task", list(TASKS))
def test_a_failing_task_is_shown_with_its_time_until_it_succeeds(tmp_path, monkeypatch, web, task):
    svc = _svc(tmp_path)
    others = _ran(monkeypatch, skip=task)
    real = getattr(ControllerService, TASKS[task])

    def broken(self):
        raise RuntimeError(f"{task} broke\nsecond line")
    monkeypatch.setattr(ControllerService, TASKS[task], broken)
    assert network_watch_pass(svc) == 300.0                     # the cadence: a non-AP box
    assert others == [n for n in TASKS if n != task]            # the others still ran, in order

    failed = [d for d in svc.doctor().details if d.startswith(" !maintenance")]
    assert len(failed) == 1
    assert re.fullmatch(rf" !maintenance {task}: FAILED at {UTC} — RuntimeError: {task} broke "
                        r"second line", failed[0])
    notice = _notice(web, svc)
    assert notice is not None and re.search(rf"{task} at {UTC} — RuntimeError: {task} broke",
                                            notice)

    monkeypatch.setattr(ControllerService, TASKS[task], real)  # the next pass succeeds
    assert network_watch_pass(svc) == 300.0
    details = svc.doctor().details
    assert not [d for d in details if d.startswith(" !maintenance")]
    ok = [d for d in details if d.startswith(f"  maintenance {task}: ok at ")]
    assert len(ok) == 1 and re.fullmatch(
        rf"  maintenance {task}: ok at {UTC} \(last failure {UTC}: RuntimeError: {task} "
        r"broke second line\)", ok[0])
    assert _notice(web, svc) is None


def test_a_box_whose_pass_never_ran_says_so(tmp_path, web):
    """An upgraded box (no record yet) reads "never run" — not ok, not failed — until the console's
    first pass; the dashboard shows no failure."""
    svc = _svc(tmp_path)
    lines = [d for d in svc.doctor().details if "maintenance" in d]
    assert len(lines) == 1 and lines[0].startswith("  maintenance: never run")
    assert _notice(web, svc) is None


@pytest.mark.parametrize("record", [
    "{ not json",
    '{"version": 1, "tasks": {"crl-refresh": {"last": "ok", "last_success": "yesterday"}}}'],
    ids=["not-json", "hand-edited-entry"])
def test_an_unreadable_record_is_never_healthy(tmp_path, web, record):
    svc = _svc(tmp_path)
    (tmp_path / "state" / "maintenance.json").write_text(record)
    assert [d for d in svc.doctor().details if d.startswith(" !maintenance record: FAILED")]
    assert "record" in (_notice(web, svc) or "")


@pytest.mark.parametrize("ap_box, apply_pending, normalise_pending, expected", [
    (False, False, False, 300.0), (True, False, False, 60.0),
    (False, True, False, 60.0), (False, False, True, 60.0)])
def test_the_cadence_is_unchanged_when_a_task_fails(tmp_path, monkeypatch, ap_box, apply_pending,
                                                    normalise_pending, expected):
    """A guard of unchanged behaviour (green before and after): the seconds the watch loop sleeps
    after a pass, with a maintenance task failing."""
    svc = _svc(tmp_path)

    def broken(self):
        raise RuntimeError("broke")
    monkeypatch.setattr(ControllerService, "crl_refresh_if_expired", broken)
    monkeypatch.setattr(ControllerService, "network_supported", lambda self: ap_box)
    monkeypatch.setattr(ControllerService, "webserver_apply_pending", lambda self: apply_pending)
    monkeypatch.setattr(ControllerService, "pki_normalise_pending",
                        lambda self: normalise_pending)
    monkeypatch.setattr(ControllerService, "_network_watch_tick",
                        lambda self, force=False: (True, ""))
    assert network_watch_pass(svc) == expected


def test_a_record_that_stops_being_written_is_shown_failing(tmp_path, monkeypatch, web, caplog):
    """A pass whose task fails and whose record cannot be written leaves the last record on disk;
    once that record is older than three pass intervals, doctor and the dashboard say it is no
    longer current, from the same row, instead of showing its old success."""
    import json
    import time

    from lhpc.core import maintenance, runtime_fs
    svc = _svc(tmp_path)
    network_watch_pass(svc)                                       # a pass that succeeds
    rec = tmp_path / "state" / "maintenance.json"
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() - 1200))          # 20 min > 3 × 300 s
    data = json.loads(rec.read_text())
    for e in data["tasks"].values():
        for k in ("last_success", "last_failure"):
            if k in e:
                e[k]["at"] = old                                  # ...written that long ago
    rec.write_text(json.dumps(data))

    def refused(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(runtime_fs, "atomic_write", refused)      # no later pass can write
    monkeypatch.setattr(ControllerService, "crl_refresh_if_expired",
                        lambda self: (_ for _ in ()).throw(RuntimeError("crl")))
    with caplog.at_level("WARNING"):
        network_watch_pass(svc)
    assert any(m.startswith("maintenance record failed: maintenance.json not written: ")
               for m in caplog.messages), caplog.messages     # the record write was reached
    rows = [r for r in maintenance.failing(svc._paths) if r[0] == "record"]
    assert rows == [("record", "", rows[0][2])] and rows[0][2].startswith(
        f"stale — last written {old}, not updated since"), rows    # the time is the last pass
    assert f" !maintenance record: FAILED — stale — last written {old}, not updated since" in \
        "\n".join(svc.doctor().details)
    assert f"record — stale — last written {old}" in " ".join((_notice(web, svc) or "").split())
