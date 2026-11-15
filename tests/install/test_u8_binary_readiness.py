"""U8 (PLAN-UPDATE-v6): the start readiness of a component on an installed binary. The receipt is the
evidence: it must be valid and record the component (no hashing at start). An unsafe receipt or an
interrupted transaction refuses, never a source fallback; a component the spec does not cover is
judged on its own source. Through `start daemon` (the daemon's own spawn path) and the readiness the
web gate and the stack page read."""
import dataclasses

import pytest

from lhpc.core import binary_install as bi
from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

DAEMON = "loraham-daemon"


def _daemon_box(tmp_path, monkeypatch, binary_receipt, **receipt):
    """A box with the daemon artifact installed (its checkout overlaid), the hardware configured and
    the spawn recorded: (svc, spawned)."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    (tmp_path / svc.stack("daemon").component(DAEMON).source.path).mkdir(parents=True, exist_ok=True)
    binary_receipt(svc, probe="loraham_daemon 0.9.0", **receipt)
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start", lambda self, stack, comp, *a, **k: spawned.append(
        comp.id) or (_ for _ in ()).throw(AssertionError("spawned")))
    return svc, spawned


def _refused(svc, spawned):
    """`start daemon`: the daemon's typed result summary; the spawn was never reached."""
    r = svc.start("daemon", apply=True)
    dres = [x for x in r.results if x.component == DAEMON]
    assert not r.ok and dres and spawned == [], (r.summary, r.details)
    return dres[0].summary


def test_a_receipt_whose_recorded_file_is_gone_refuses_the_start(short_tmp_path, monkeypatch, binary_receipt):
    svc, spawned = _daemon_box(short_tmp_path, monkeypatch, binary_receipt)
    rel = svc.binary_spec("daemon").proof_paths[0]
    (short_tmp_path / rel).unlink()
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "unsafe"
    why = _refused(svc, spawned)
    assert f"binary artifact file {rel!r} is gone" in why, why          # its reason, not a source build
    assert "lhpc build" not in why
    assert svc.start_readiness("daemon")[0] == [(DAEMON, svc.install_blocker(svc.stack("daemon").component(DAEMON)))]


def test_an_interrupted_binary_transaction_refuses_the_start(short_tmp_path, monkeypatch, binary_receipt):
    svc, spawned = _daemon_box(short_tmp_path, monkeypatch, binary_receipt)
    monkeypatch.setattr(bi, "read_journal", lambda paths: ({"stack": "daemon", "state": "prepared"}, "valid"))
    svc.invalidate_snapshot()
    why = _refused(svc, spawned)
    assert "a binary install of 'daemon' is in progress or was interrupted" in why, why


def test_an_old_receipt_refuses_a_component_the_widened_spec_now_covers(short_tmp_path, monkeypatch, binary_receipt):
    """The receipt was written before the spec covered the daemon: it records no commit for it, so the
    installed artifact cannot be the one that provides it."""
    svc0 = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=short_tmp_path))
    pins = {c: v for c, v in svc0._binary_pins("daemon").items() if c != DAEMON}
    svc, spawned = _daemon_box(short_tmp_path, monkeypatch, binary_receipt, commits=pins)
    assert svc.on_binary_channel("daemon")
    why = _refused(svc, spawned)
    assert why == (f"daemon: the installed binary predates {DAEMON}: lhpc update daemon "
                   "or install it from source"), why


def test_a_valid_receipt_recording_the_component_starts(short_tmp_path, monkeypatch, binary_receipt):
    """The anchor of the three refusals above: the same box with an intact receipt reaches the spawn."""
    svc, spawned = _daemon_box(short_tmp_path, monkeypatch, binary_receipt)
    with pytest.raises(AssertionError, match="spawned"):
        svc.start("daemon", apply=True)
    assert spawned == [DAEMON]
    assert svc.start_readiness("daemon") == ([], [])


def test_an_uncovered_component_of_a_binary_stack_is_judged_on_its_own_source(tmp_path, monkeypatch, binary_receipt):
    """Mixed coverage: on a valid meshcom receipt, a component the spec does not cover has no
    artifact behind it; without its own checkout it is refused, not passed on the receipt."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    spec = svc.binary_spec("meshcom")
    bridge_dir = svc.stack("meshcom").component("meshcom-bridge").source.path + "/"
    narrowed = dataclasses.replace(spec, covers=tuple(c for c in spec.covers if c != "meshcom-bridge"),
                                   proof_paths=tuple(p for p in spec.proof_paths if not p.startswith(bridge_dir)))
    real = ControllerService.binary_spec
    monkeypatch.setattr(ControllerService, "binary_spec",
                        lambda self, sid: narrowed if sid == "meshcom" else real(self, sid))
    binary_receipt(svc, "meshcom")
    bridge = svc.stack("meshcom").component("meshcom-bridge")
    qemu = svc.stack("meshcom").component("meshcom-qemu")
    assert svc.on_binary_channel("meshcom") and not svc._source_present(bridge)
    assert svc.install_blocker(bridge) == "not installed — run: lhpc install meshcom"
    assert svc.install_blocker(qemu) == ""                     # the covered one: the receipt


def test_an_unsafe_receipt_refuses_the_generic_start_by_its_reason(tmp_path, monkeypatch, binary_receipt):
    """The generic start loop (not the daemon's own path): a meshtastic artifact whose recorded file is
    gone refuses by that reason, never by a source build's "not built"."""
    from lhpc.core.lifecycle import StartLaunch
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshtastic", values={"node_name": "LHPC test", "node_short": "LHPT"}).ok
    comp = svc.stack("meshtastic").component("meshtastic")
    (tmp_path / comp.source.path).mkdir(parents=True, exist_ok=True)
    binary_receipt(svc, "meshtastic")
    rel = svc.binary_spec("meshtastic").proof_paths[0]
    (tmp_path / rel).unlink()
    svc.invalidate_snapshot()
    monkeypatch.setattr(ControllerService, "start_blocking_requirements", lambda self, comp: [])
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start", lambda self, stack, comp, *a, **k: spawned.append(
        comp.id) or StartLaunch(True, ""))
    r = svc.start("meshtastic", apply=True)
    res = {x.component: x for x in r.results}
    assert not r.ok and spawned == [] and res["meshtastic"].outcome.value == "blocked", r.details
    assert f"binary artifact file {rel!r} is gone" in res["meshtastic"].summary, res["meshtastic"].summary


@pytest.mark.parametrize(("state", "why", "expected"), [
    ("unsafe", "", "the binary install of 'daemon' is unsafe — resolve it first"),   # never "" = ready
    ("superseded", "adopted", "not built — run: lhpc build daemon"),               # the source branch
])
def test_a_receipt_that_is_not_valid_is_never_read_as_ready(tmp_path, monkeypatch, binary_receipt, state, why,
                                                           expected):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    daemon = svc.stack("daemon").component(DAEMON)
    (tmp_path / daemon.source.path).mkdir(parents=True, exist_ok=True)
    rec = binary_receipt(svc)                                    # records the daemon
    monkeypatch.setattr(ControllerService, "binary_receipt_state", lambda self, sid: (state, rec, why))
    monkeypatch.setattr(ControllerService, "_build_complete", lambda self, comp: False)   # a source build would say so
    assert svc.install_blocker(daemon) == expected
