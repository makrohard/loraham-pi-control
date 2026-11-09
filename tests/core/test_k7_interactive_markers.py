"""K7 (13.14): an interactive app's "run" marker is its instance's own, state/interactive/<sid>@<rid>.show, so two
instances of one stack (chat on r1 and on r2, both 433) are marked, read and dismissed independently."""
import radios_fixture

from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))          # r1 and r2, both on 433
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _markers(tmp_path):
    d = tmp_path / "state" / "interactive"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _seed(tmp_path, **bands):
    d = tmp_path / "state" / "interactive"
    d.mkdir(parents=True, exist_ok=True)
    for rid, band in bands.items():
        (d / f"chat@{rid}.show").write_text(f"{band}\n")


def test_k7_a_stop_of_one_instance_dismisses_its_own_marker_only(tmp_path):
    svc = _svc(tmp_path)
    _seed(tmp_path, r1="433", r2="433")
    assert svc.stop("chat", apply=True, inst=Instance("r1", band="433")).ok
    assert _markers(tmp_path) == ["chat@r2.show"]
    assert svc.interactive_band("chat", Instance("r2", band="433")) == "433"
    assert svc.interactive_band("chat", Instance("r1", band="433")) is None


def test_k7_each_instance_is_marked_in_its_own_file(tmp_path):
    svc = _svc(tmp_path)
    assert svc.mark_interactive("chat", "433", Instance("r1", band="433"))
    assert svc.mark_interactive("chat", "433", Instance("r2", band="433"))
    assert _markers(tmp_path) == ["chat@r1.show", "chat@r2.show"]
    svc.dismiss_interactive("chat", Instance("r1", band="433"))
    assert _markers(tmp_path) == ["chat@r2.show"]
    assert svc.interactive_band("chat") == "433"                   # without an instance: any instance's


def test_k7_without_an_instance_disagreeing_markers_give_no_band(tmp_path):
    svc = _svc(tmp_path)
    _seed(tmp_path, r1="433", r2="868")
    assert svc.interactive_band("chat") == ""                      # marked, band unknown (never a guess)
    svc.dismiss_interactive("chat")                                 # a bare dismiss: every instance's
    assert _markers(tmp_path) == [] and svc.interactive_band("chat") is None


def test_k7_a_running_apps_instance_markers_are_not_stale(tmp_path, monkeypatch):
    # before, the sweep read "chat@r1" as a stack id, found none and cleared a running chat's marker
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _seed(tmp_path, r1="433", r2="433")
    snap = svc.build_snapshot()
    snap.stack("chat").components[svc.stack("chat").main].run_state = RunState.RUNNING
    monkeypatch.setattr(svc, "build_snapshot", lambda *a, **k: snap)
    assert svc.clear_stale_interactive() == [] and _markers(tmp_path) == ["chat@r1.show", "chat@r2.show"]


def test_k7_a_stale_instance_marker_is_cleared_by_its_file(tmp_path):
    svc = _svc(tmp_path)
    _seed(tmp_path, r1="433", r2="433")
    assert sorted(svc.clear_stale_interactive()) == ["chat@r1", "chat@r2"]     # chat is not running
    assert _markers(tmp_path) == []


def test_g1_k7a_2_the_sweep_judges_each_instance_by_its_own_run(tmp_path, monkeypatch):
    # chat@r2 runs, chat@r1 stopped: r1's stale marker goes, r2's stays; before, both were judged by chat's one
    # component state (both kept); and a start of chat@r2 keeps only its own marker, not every chat marker
    from lhpc.core import instances
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _seed(tmp_path, r1="433", r2="433")
    snap = svc.build_snapshot()
    snap.stack("chat").components[svc.stack("chat").main].run_state = RunState.RUNNING
    monkeypatch.setattr(svc, "build_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(instances, "running_keys", lambda _s, sid: ["chat@r2"] if sid == "chat" else [])
    assert svc.clear_stale_interactive(keep="kiss@r1") == ["chat@r1"] and _markers(tmp_path) == ["chat@r2.show"]
    _seed(tmp_path, r1="433")
    assert svc.clear_stale_interactive(keep="chat@r2") == ["chat@r1"]


def test_g1_k7a_3_a_left_over_bare_marker_is_no_instances(tmp_path):
    # a pre-K7 state/interactive/chat.show with a hardware list: chat@r1 stopped is not shown, nor is the stack
    svc = _svc(tmp_path)
    d = tmp_path / "state" / "interactive"
    d.mkdir(parents=True)
    (d / "chat.show").write_text("433\n")
    assert svc.interactive_band("chat", Instance("r1", band="433")) is None
    assert svc.interactive_band("chat") is None
    svc.dismiss_interactive("chat")                                  # a bare dismiss still removes it
    assert _markers(tmp_path) == []
