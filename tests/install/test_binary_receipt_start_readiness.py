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
    why = svc.install_blocker(svc.stack("daemon").component(DAEMON))
    assert svc.start_readiness("daemon")[0] == [(c, why) for c in svc.binary_spec("daemon").covers   # each by the receipt
                                                if c != "libch341"]      # not launched, no build provider (RELP5b)


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
    assert why == (f"daemon: the installed binary predates {DAEMON}: lhpc update daemon when a published binary "
                   "covers it, else install it from source"), why


def test_a_valid_receipt_recording_the_component_starts(short_tmp_path, monkeypatch, binary_receipt):
    """The anchor of the three refusals above: the same box with an intact receipt reaches the spawn."""
    svc, spawned = _daemon_box(short_tmp_path, monkeypatch, binary_receipt)
    with pytest.raises(AssertionError, match="spawned"):
        svc.start("daemon", apply=True)
    assert spawned == [DAEMON]
    assert svc.start_readiness("daemon") == ([], [])


def test_an_uncovered_component_of_a_binary_stack_is_judged_on_its_own_source(tmp_path, monkeypatch, binary_receipt):
    """Mixed coverage: on a valid meshcom receipt, a component the spec does not cover has no
    artifact behind it; without its own checkout it is refused, not passed on the receipt — by the
    web start gate too."""
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
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    assert svc.set_operator_identity(callsign="XX0XXA").ok        # meshcom's identity gate comes first
    monkeypatch.setattr(svc, "_ensure_daemon", lambda *a, **k: ([], True, ""))   # the daemon up (its own path)
    r = svc.start("meshcom", apply=True)
    res = {x.component: x for x in r.results}
    assert not r.ok and res["meshcom-bridge"].outcome.value == "blocked", r.details     # through the start too
    assert res["meshcom-bridge"].summary == "not installed — run: lhpc install meshcom", r.details


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
def test_a_receipt_that_is_not_valid_is_never_read_as_ready(short_tmp_path, monkeypatch, binary_receipt, state, why,
                                                           expected):
    """Through `start daemon` too: the daemon's own spawn path refuses by the same reason."""
    svc, spawned = _daemon_box(short_tmp_path, monkeypatch, binary_receipt)   # records the daemon
    daemon = svc.stack("daemon").component(DAEMON)
    rec = svc.binary_receipt_state("daemon")[1]
    monkeypatch.setattr(ControllerService, "binary_receipt_state", lambda self, sid: (state, rec, why))
    monkeypatch.setattr(ControllerService, "_build_complete", lambda self, comp: False)   # a source build would say so
    assert svc.install_blocker(daemon) == expected
    assert _refused(svc, spawned) == f"daemon: {expected}"


def _meshtastic_box(tmp_path, monkeypatch, binary_receipt, *, clone):
    """A meshtastic artifact installed (receipt valid, recording the component), the hardware and
    node config saved, the host's packages and the spawn stubbed: (svc, spawned)."""
    from lhpc.core.lifecycle import StartLaunch
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshtastic", values={"node_name": "LHPC test", "node_short": "LHPT"}).ok
    comp = svc.stack("meshtastic").component("meshtastic")
    if clone:
        (tmp_path / comp.source.path).mkdir(parents=True, exist_ok=True)
    binary_receipt(svc, "meshtastic")
    monkeypatch.setattr(ControllerService, "start_blocking_requirements", lambda self, comp: [])
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start", lambda self, stack, comp, *a, **k: spawned.append(
        comp.id) or StartLaunch(False, "", "stubbed spawn"))
    return svc, spawned


def test_a_valid_receipt_starts_without_a_clone(tmp_path, monkeypatch, binary_receipt):
    """G1-UPD-U8-CODE B2: the meshtastic artifact's proof files live outside its checkout, so a binary
    install needs no clone. The generic start asks the receipt, not the absent source directory."""
    svc, spawned = _meshtastic_box(tmp_path, monkeypatch, binary_receipt, clone=False)
    comp = svc.stack("meshtastic").component("meshtastic")
    assert svc.on_binary_channel("meshtastic") and not svc._source_present(comp)
    r = svc.start("meshtastic", apply=True)
    assert spawned == ["meshtastic"], r.details                     # past every readiness gate
    assert "meshtastic" not in dict(svc.start_readiness("meshtastic")[0])   # (its web client is a separate fetch)


def test_a_valid_receipt_outranks_a_source_build_marker(tmp_path, monkeypatch, binary_receipt):
    """G1-UPD-U8-CODE B1: a covered component that also declares `build_requires` and has no source-build
    marker (none of today's covered components has both: the shape is made here) starts on its valid
    receipt; the source-build marker check belongs to the source branch."""
    svc, spawned = _meshtastic_box(tmp_path, monkeypatch, binary_receipt, clone=True)
    svc._stacks = [dataclasses.replace(s, components=tuple(
        dataclasses.replace(c, build_requires=("meshtastic-web",)) if c.id == "meshtastic" else c
        for c in s.components)) if s.id == "meshtastic" else s for s in svc.stacks()]
    comp = svc.stack("meshtastic").component("meshtastic")
    assert comp.build_marker and comp.build_requires and not svc._build_complete(comp)
    r = svc.start("meshtastic", apply=True)
    assert spawned == ["meshtastic"], r.details
    assert "sources changed since the last build" not in r.details


def test_an_unsafe_receipt_without_its_checkout_refuses_by_its_reason(short_tmp_path, monkeypatch, binary_receipt):
    """G1-UPD-U8-CODE B2: the daemon's recorded files live in its checkout; with the checkout removed
    the receipt is unsafe. `start daemon` and the page name that reason, never "not installed"."""
    svc, spawned = _daemon_box(short_tmp_path, monkeypatch, binary_receipt)
    daemon = svc.stack("daemon").component(DAEMON)
    import shutil
    shutil.rmtree(short_tmp_path / daemon.source.path)
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "unsafe" and not svc._source_present(daemon)
    why = _refused(svc, spawned)
    assert "is gone" in why and "not installed" not in why, why
    assert svc.start_readiness("daemon")[0] == [(c, svc.install_blocker(daemon)) for c in svc.binary_spec("daemon").covers
                                                if c != "libch341"]      # not launched, no build provider (RELP5b)
