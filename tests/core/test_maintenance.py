"""`lhpc.core.maintenance.run`: what the pass does when a task does not simply succeed or fail.
The task's service method is replaced by a fake with its real signature."""

import pytest

from lhpc.core import maintenance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.bootstrap(apply=True).ok
    return svc


def test_ctrl_c_in_a_task_stops_the_pass(tmp_path, monkeypatch):
    """Only an `Exception` is a task's failure: a KeyboardInterrupt propagates, no later task
    runs and nothing is recorded."""
    svc = _svc(tmp_path)
    later = []

    def interrupted(self):
        raise KeyboardInterrupt
    monkeypatch.setattr(ControllerService, "crl_refresh_if_expired", interrupted)
    monkeypatch.setattr(ControllerService, "rflog_roll_native_all",
                        lambda self: later.append("rf-logs"))
    with pytest.raises(KeyboardInterrupt):
        maintenance.run(svc)
    assert later == [] and maintenance.read(svc._paths) == ("absent", {})


def test_a_record_that_cannot_be_written_is_the_passs_failure(tmp_path, monkeypatch):
    """Every task still ran; the pass returns, and says the record was not written."""
    svc = _svc(tmp_path)
    (tmp_path / "state" / "maintenance.json").mkdir()          # the record's place is taken
    out = maintenance.run(svc)
    assert [n for n in out if n != "record"] == [n for n, _ in maintenance.TASKS]
    outcome, message = out["record"]
    assert outcome is maintenance.Outcome.FAILED and message.startswith("maintenance.json not written: ")


def test_a_multi_line_error_is_recorded_as_one_line(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "cap_controller_logs",
                        lambda self: {"lhpc-web.log": "error: OSError\n  at\tline 2"})
    out = maintenance.run(svc)
    assert out["controller-logs"] == (maintenance.Outcome.FAILED,
                                      "lhpc-web.log: error: OSError at line 2")
    assert maintenance.failing(svc._paths)[0][2] == "lhpc-web.log: error: OSError at line 2"
