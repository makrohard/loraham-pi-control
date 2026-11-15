"""U8's start readiness for a binary built from other commits than today's pins (moved here from the removed
test_binary_pin_override.py, U10a): a moved pin is an update, not a broken install, so a covered component off its
pin reaches the spawn."""
import pytest
import radios_fixture

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

A = "a" * 40


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_lagging_covered_library_does_not_block_the_start(tmp_path, monkeypatch, binary_receipt):
    """The daemon's artifact also covers RadioLib, a library that never runs: its lag does not block a start (the
    maintainer's decision after 0.11.5)."""
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path, monkeypatch)
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "high", "uputronics", "868", ce="ce1")))   # stage L5
    pins = svc._binary_pins("daemon")
    lib = "radiolib"
    assert svc.stack("daemon").component(lib).kind.value == "library"
    daemon = svc.stack("daemon").component("loraham-daemon")
    (tmp_path / daemon.source.path).mkdir(parents=True, exist_ok=True)
    binary_receipt(svc, commits={**pins, lib: A}, probe="loraham_daemon 0.9.0")
    assert svc.install_blocker(daemon) == ""
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start",
                        lambda self, *a, **k: spawned.append(a) or (_ for _ in ()).throw(
                            AssertionError("spawned")))
    try:
        svc.start("daemon", apply=True)
    except AssertionError:
        pass
    assert spawned                                                     # reached the spawn


@pytest.mark.parametrize(("lagging", "kind"), [("meshcom-bridge", "service"),
                                               ("meshcom-firmware", "firmware")])
def test_a_covered_component_off_its_pin_does_not_block_the_start(short_tmp_path, monkeypatch, binary_receipt,
                                                                  lagging, kind):
    """`start meshcom` with one covered component off its pin (U8: a moved pin is an update, not
    a broken install): a lagging bridge (a service) or firmware image, the bridge and QEMU both
    reach the spawn."""
    from lhpc.core import config as cfgmod
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT, StartLaunch
    svc = _svc(short_tmp_path, monkeypatch)
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    # stage L5: a dual-band box's hardware list; the bridge is a 433 client, so it runs on r1 (REL14-D28)
    radios_fixture.write(short_tmp_path, (radios.Radio("r1", "low", "uputronics", "433", ce="ce0"),
                                          radios.Radio("r2", "high", "uputronics", "868", ce="ce1")))
    svc._invalidate_config()
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    pins = svc._binary_pins("meshcom")
    stack = svc.stack("meshcom")
    for c in stack.components:
        if c.source is not None:
            (short_tmp_path / c.source.path).mkdir(parents=True, exist_ok=True)
        if c.source is not None and c.build_marker:          # QEMU built from today's inputs
            marker = svc._lifecycle().source_dir(c) / c.build_marker
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(c))
            svc.build_inputs_path(c).parent.mkdir(parents=True, exist_ok=True)
            svc.build_inputs_path(c).write_text(svc.build_inputs_text(c))
    binary_receipt(svc, "meshcom", commits={**pins, lagging: A})
    assert stack.component(lagging).kind.value == kind
    assert svc.install_blocker(stack.component("meshcom-qemu")) == ""
    # Everything outside the gate is stubbed: the daemon is ready, the host has the packages,
    # a launched bridge is ready; the spawn records the component and QEMU's launch "fails".
    monkeypatch.setattr(type(svc), "_ensure_daemon", lambda self, *a, **k: ([], True, ""))
    monkeypatch.setattr(type(svc), "start_blocking_requirements", lambda self, comp: [])
    monkeypatch.setattr(type(svc), "_ready_endpoints_present", lambda self, comp, inst=None: (True, []))
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start",
                        lambda self, stack, comp, *a, **k: spawned.append(comp.id) or StartLaunch(
                            comp.id != "meshcom-qemu", "", "stubbed spawn"))
    r = svc.start("meshcom", apply=True)
    res = {x.component: x for x in r.results}
    assert spawned == ["meshcom-bridge", "meshcom-qemu"], r.summary    # both reached the spawn
    assert res["meshcom-qemu"].summary == "start failed: stubbed spawn (log )"
