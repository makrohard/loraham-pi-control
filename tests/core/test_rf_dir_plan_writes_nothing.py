"""Package item 26: the radio's RF log directory `logs/<rid>/` is created by the daemon start, not by its plan.

`radio_start.start_plan` is a planning function, yet it created the directory (ensure_rf_dir, LEG-D32), so a
caller that only wants a plan wrote to disk. The creation is now `launch_plan`'s, which the daemon start calls
right before the launch, only for a plan with no refusal. The start's own behaviour (created before the launch,
an unusable directory refused with nothing launched) is held by tests/core/test_rf_dir_at_start.py, unchanged."""
import radios_fixture

from lhpc.core import radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ONE = (radios.Radio("r1", "low", "loraham", "433"),)


def _svc(tmp_path, rows=ONE):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_plan_alone_writes_nothing(tmp_path):
    params, refusal, _notes = radio_start.start_plan(_svc(tmp_path), "r1")
    assert params is not None and refusal == ""
    assert not (tmp_path / "logs").exists()


def test_the_launch_plan_creates_the_radios_directory(tmp_path):
    params, refusal, _notes = radio_start.launch_plan(_svc(tmp_path), "r1")
    d = tmp_path / "logs" / "r1"
    assert params is not None and refusal == ""
    assert d.is_dir() and not d.is_symlink()


def test_a_refused_plan_creates_nothing(tmp_path):
    svc = _svc(tmp_path, (radios.Radio("r1", "low", "loraham", "433", enabled=False),))
    assert radio_start.launch_plan(svc, "r1")[:2] == (None, "radio r1 is disabled")
    assert not (tmp_path / "logs").exists()


def test_a_plan_with_params_but_a_refusal_creates_nothing(tmp_path, monkeypatch):
    # e.g. a USB stick that is not there: the plan has its params and refuses; nothing is created for it.
    svc = _svc(tmp_path)
    monkeypatch.setattr(radio_start, "usb_precheck", lambda radio, model: "the stick is not plugged in")
    params, refusal, _notes = radio_start.launch_plan(svc, "r1")
    assert params is not None and refusal == "the stick is not plugged in"
    assert not (tmp_path / "logs").exists()
