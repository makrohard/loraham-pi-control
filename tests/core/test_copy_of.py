"""The one stack-copy factory `instances.copy_of` (CLEANUP-DESIGN-v2 sections 1 and 2): a listed copy's band is
its radio row's and a contradicting band is refused; slot 0 is a copy too, on DEC-W16's radio of its operation band.
`instances.resolve` answers through it."""
import pytest

import radios_fixture
from lhpc.core import commands, instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _svc(tmp_path, listed='kiss = ["r3"]\n', rows=ROWS):
    radios_fixture.write(tmp_path, rows)
    (tmp_path / "config" / "instances.toml").write_text(listed)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_listed_copy_carries_its_stack_its_radio_and_its_radios_band(tmp_path):
    copy, why = instances.copy_of(_svc(tmp_path), "kiss", "r3")
    assert why == ""
    assert (copy.sid, copy.slot, copy.rid, copy.band) == ("kiss", 1, "r3", "433")
    assert (copy.key, copy.primary) == ("kiss@r3", False)


def test_a_listed_copys_band_is_its_radios_and_a_contradicting_band_is_refused(tmp_path):
    svc = _svc(tmp_path)
    assert instances.copy_of(svc, "kiss", "r3", "433")[0].band == "433"
    copy, why = instances.copy_of(svc, "kiss", "r3", "868")
    assert copy is None and why == "kiss@r3: r3 is on 433 MHz, not 868 MHz"


def test_an_unlisted_radio_an_unknown_radio_and_an_unknown_stack_are_refused(tmp_path):
    svc = _svc(tmp_path)
    assert instances.copy_of(svc, "kiss", "r1") == (
        None, "kiss@r1 is not a listed instance (lhpc stack add-instance kiss r1)")
    assert instances.copy_of(svc, "nope", "r3") == (None, "unknown stack 'nope'")
    svc = _svc(tmp_path, rows=ROWS[:1])
    assert instances.copy_of(svc, "kiss", "r3") == (None, "kiss@r3: r3 is not in the hardware list")


def test_a_disabled_radios_copy_comes_back_with_its_why(tmp_path):
    rows = (ROWS[0], radios.Radio("r3", "b", "uputronics", "433", ce="ce1", enabled=False))
    copy, why = instances.copy_of(_svc(tmp_path, rows=rows), "kiss", "r3")
    assert copy.key == "kiss@r3" and why == "kiss@r3: r3 is disabled"


def test_slot0_is_a_copy_on_dec_w16s_radio_of_its_band(tmp_path):
    copy, why = instances.copy_of(_svc(tmp_path), "kiss", band="433")
    assert why == ""
    assert (copy.sid, copy.slot, copy.rid, copy.band) == ("kiss", 0, "r1", "433")   # r3 holds kiss@r3
    assert (copy.key, copy.primary) == ("kiss", True)


def test_slot0_without_a_free_radio_on_its_band_comes_back_with_dec_w16s_why(tmp_path):
    copy, why = instances.copy_of(_svc(tmp_path, listed='kiss = ["r3", "r1"]\n'), "kiss", band="433")
    assert (copy.slot, copy.rid, copy.band) == (0, "", "433")
    assert why == ("every enabled radio of 433 MHz holds a listed instance of kiss (r1, r3): "
                   "start one of them as kiss@<rid>")


def test_slot0s_band_is_the_operation_band(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    asked = []
    monkeypatch.setattr(svc, "operation_band", lambda sid, band="": asked.append((sid, band)) or "433")
    assert instances.copy_of(svc, "kiss")[0].band == "433" and asked == [("kiss", "")]


def test_resolve_answers_through_the_factory(tmp_path):
    svc = _svc(tmp_path)
    sid, copy, band, why = instances.resolve(svc, "kiss@r3")
    assert (sid, copy.key, band, why) == ("kiss", "kiss@r3", "433", "")
    assert instances.resolve(svc, "kiss") == ("kiss", None, "", "")
    assert instances.resolve(svc, "kiss@")[1:3] == (None, "")


def test_a_template_only_instance_keeps_todays_values():
    inst = commands.Instance(2, "r4")
    assert (inst.sid, inst.band, inst.primary, inst.subst("{instance_dir}x")) == ("", "", False, "r4/x")
    assert commands.SLOT0.primary and commands.SLOT0.subst("{instance_dir}x") == "x"


def test_a_fixed_band_stacks_slot0_runs_on_its_one_band(tmp_path):
    rows = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"),)
    copy, why = instances.copy_of(_svc(tmp_path, listed="", rows=rows), "meshcore")
    assert (copy.rid, copy.band, why) == ("r2", "868", "")


@pytest.mark.no_default_hardware
def test_a_switchable_stacks_slot0_without_served_bands_has_no_band(tmp_path):
    copy, why = instances.copy_of(_svc(tmp_path, listed=""), "kiss")
    assert (copy.rid, copy.band, why) == ("", "", "")


def test_slot0_with_its_radio_renders_a_peer_port_as_today():
    # CLEANUP-DESIGN-v5 section 12 (C6): slot 0 is a real copy with its DEC-W16 radio; `{port_of:}` then names the
    # peer stack's slot 0, as without a radio. A listed copy still needs its peer on its radio.
    assert commands.Instance(0, "r1", sid="graywolf").subst("{port_of:kiss:8001}") == "8001"
    with pytest.raises(commands.CommandError):
        commands.Instance(1, "r2", sid="graywolf").subst("{port_of:kiss:8001}")


def test_an_applied_slot0_start_resolves_its_copy_under_the_locks(tmp_path, monkeypatch):
    # G1-C45: r1 becomes a listed copy while the start waits for admission; the admitted start acts on slot 0's
    # radio as the locks see it (r3, DEC-W16's first unlisted), never on the r1 resolved before them.
    import contextlib
    from lhpc.core.service_base import ActionResult
    svc = _svc(tmp_path, listed="")
    real, seen = svc._admit_and_acquire_guards, []

    @contextlib.contextmanager
    def admitted(*a, **k):
        (tmp_path / "config" / "instances.toml").write_text('kiss = ["r1"]\n')     # meanwhile
        with real(*a, **k) as got:
            yield got
    monkeypatch.setattr(svc, "_admit_and_acquire_guards", admitted)
    monkeypatch.setattr(svc, "_start_outer_refusal", lambda *a: (seen.append(a[-1]), (ActionResult(False, "x"), None, ""))[1])
    svc.start("kiss", apply=True, band="433")
    assert [(c.slot, c.rid) for c in seen] == [(0, "r3")]


def test_an_applied_slot0_stop_resolves_its_copy_under_the_lock(tmp_path, monkeypatch):
    # G1-C47: as for the start (G1-C45), r1 becomes a listed copy while the stop waits for its lock; the locked stop
    # acts on slot 0's radio as the lock sees it (r3), never on the r1 resolved before.
    import contextlib
    from lhpc.core.service_base import ActionResult
    svc = _svc(tmp_path, listed="")
    real, seen = svc._lifecycle_guard, []

    @contextlib.contextmanager
    def locked(*a, **k):
        (tmp_path / "config" / "instances.toml").write_text('kiss = ["r1"]\n')     # meanwhile
        with real(*a, **k) as got:
            yield got
    monkeypatch.setattr(svc, "_lifecycle_guard", locked)
    monkeypatch.setattr(svc, "_stop_impl", lambda *a, **k: seen.append(k["inst"]) or ActionResult(False, "x"))
    svc.stop("kiss", apply=True, band="433")
    assert [(c.slot, c.rid) for c in seen] == [(0, "r3")]
