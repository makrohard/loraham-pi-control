"""The one stack-copy factory `instances.copy_of` (CLEANUP-DESIGN-v2 sections 1 and 2): a copy's band is its radio
row's and a contradicting band is refused; slot 0 is a copy too, on DEC-W16's radio of its operation band. K4ac: the
hardware list gives the copies (a band's further radios, instances.copy_rids). `instances.resolve` answers through it."""

import pytest
import radios_fixture
from lhpc.core import commands, instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
R1_OFF = radios.Radio("r1", "a", "uputronics", "433", ce="ce0", enabled=False)


def _svc(tmp_path, rows=ROWS):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_copy_carries_its_stack_its_radio_and_its_radios_band(tmp_path):
    copy, why = instances.copy_of(_svc(tmp_path), "kiss", "r3")
    assert why == ""
    assert (copy.rid, copy.band) == ("r3", "433")
    assert instances.key("kiss", copy) == "kiss@r3"


def test_a_copys_band_is_its_radios_and_a_contradicting_band_is_refused(tmp_path):
    svc = _svc(tmp_path)
    assert instances.copy_of(svc, "kiss", "r3", "433")[0].band == "433"
    copy, why = instances.copy_of(svc, "kiss", "r3", "868")
    assert copy is None and why == "kiss@r3: r3 is on 433 MHz, not 868 MHz"


def test_the_bands_first_radio_is_an_instance_and_an_unknown_radio_or_stack_is_refused(tmp_path):
    svc = _svc(tmp_path)
    inst, why = instances.copy_of(svc, "kiss", "r1")                        # K4b: every radio of the band, r1 too
    assert (inst.rid, inst.band, why) == ("r1", "433", "")
    assert instances.copy_of(svc, "nope", "r3") == (None, "unknown stack 'nope'")
    svc = _svc(tmp_path, rows=ROWS[:1])
    assert instances.copy_of(svc, "kiss", "r3") == (None, "kiss@r3: r3 is not in the hardware list")


def test_a_disabled_radios_copy_comes_back_with_its_why(tmp_path):
    rows = (ROWS[0], radios.Radio("r3", "b", "uputronics", "433", ce="ce1", enabled=False))
    copy, why = instances.copy_of(_svc(tmp_path, rows=rows), "kiss", "r3")
    assert instances.key("kiss", copy) == "kiss@r3" and why == "kiss@r3: r3 is disabled"


def test_slot0_is_a_copy_on_dec_w16s_radio_of_its_band(tmp_path):
    copy, why = instances.copy_of(_svc(tmp_path), "kiss", band="433")
    assert why == ""
    assert (copy.rid, copy.band) == ("r1", "433")                 # a band caller means the band's first radio
    assert instances.key("kiss", copy) == "kiss@r1"


def test_slot0s_band_is_the_operation_band(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    asked = []
    monkeypatch.setattr(svc, "operation_band", lambda sid, band="": asked.append((sid, band)) or "433")
    assert instances.copy_of(svc, "kiss")[0].band == "433" and asked == [("kiss", "")]


def test_resolve_answers_through_the_factory(tmp_path):
    svc = _svc(tmp_path)
    sid, copy, band, why = instances.resolve(svc, "kiss@r3")
    assert (sid, instances.key(sid, copy), band, why) == ("kiss", "kiss@r3", "433", "")
    assert instances.resolve(svc, "kiss") == ("kiss", None, "", "")
    assert instances.resolve(svc, "kiss@")[1:3] == (None, "")


def test_a_template_only_instance_keeps_todays_values():
    inst = commands.Instance("r4")
    assert (inst.band, inst.subst("{instance_dir}x")) == ("", "r4/x")
    assert commands.TEMPLATE.rid == "" and commands.TEMPLATE.subst("{instance_dir}x") == "x"


def test_a_fixed_band_stacks_slot0_runs_on_its_one_band(tmp_path):
    rows = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"),)
    copy, why = instances.copy_of(_svc(tmp_path, rows=rows), "meshcore")
    assert (copy.rid, copy.band, why) == ("r2", "868", "")


def test_a_switchable_stacks_slot0_without_served_bands_has_no_band(tmp_path):
    copy, why = instances.copy_of(_svc(tmp_path, rows=()), "kiss")    # S2: a list without radios serves none
    assert (copy.rid, copy.band, why) == ("", "", "")


def test_slot0_with_its_radio_renders_a_peer_port_as_today():
    # CLEANUP-DESIGN-v5 section 12 (C6): slot 0 is a real copy with its DEC-W16 radio; `{port_of:}` then names the
    # peer stack's slot 0, as without a radio. K4ac (DEC-W348): a copy's is its radio's, base + 133 x (n - 1).
    assert commands.Instance("r1").subst("{port_of:kiss:8001}") == "8001"
    assert commands.Instance("r2").subst("{port_of:kiss:8001}") == "8134"


def test_an_applied_slot0_start_resolves_its_copy_under_the_locks(tmp_path, monkeypatch):
    # G1-C45: r1 is disabled while the start waits for admission; the admitted start acts on slot 0's radio as the
    # locks see it (r3, DEC-W16's first enabled), never on the r1 resolved before them.
    import contextlib
    from lhpc.core.service_base import ActionResult
    svc = _svc(tmp_path)
    real, seen = svc._admit_and_acquire_guards, []

    @contextlib.contextmanager
    def admitted(*a, **k):
        radios_fixture.write(tmp_path, (R1_OFF, ROWS[1]))                          # meanwhile
        with real(*a, **k) as got:
            yield got
    monkeypatch.setattr(svc, "_admit_and_acquire_guards", admitted)
    monkeypatch.setattr(svc, "_start_outer_refusal", lambda *a: (seen.append(a[-1]), (ActionResult(False, "x"), None, ""))[1])
    svc.start("kiss", apply=True, band="433")
    assert [c.rid for c in seen] == ["r3"]


def test_an_applied_slot0_stop_resolves_its_copy_under_the_lock(tmp_path, monkeypatch):
    # G1-C47: as for the start (G1-C45), r1 is disabled while the stop waits for its lock; the locked stop acts on
    # slot 0's radio as the lock sees it (r3), never on the r1 resolved before.
    import contextlib
    from lhpc.core.service_base import ActionResult
    svc = _svc(tmp_path)
    real, seen = svc._lifecycle_guard, []

    @contextlib.contextmanager
    def locked(*a, **k):
        radios_fixture.write(tmp_path, (R1_OFF, ROWS[1]))                          # meanwhile
        with real(*a, **k) as got:
            yield got
    monkeypatch.setattr(svc, "_lifecycle_guard", locked)
    monkeypatch.setattr(svc, "_stop_impl", lambda *a, **k: seen.append(k["inst"]) or ActionResult(False, "x"))
    svc.stop("kiss", apply=True, band="433")
    assert [c.rid for c in seen] == ["r3"]


def test_a_hand_started_process_no_instance_claims_keeps_the_snapshots_state(tmp_path):
    # G1-C48 fail-safe (K4b): a hand-started kiss-serial (no instance token, no record) keeps the snapshot's state for
    # the instance's start, so the start judges it and never launches a second one.
    from lhpc.core.model import RunState
    radios_fixture.write(tmp_path, ROWS[:1])
    comp = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack("kiss").component(
        "loraham-kiss-serial")
    bare = [comp.process.exec_name, *comp.process.all_args]
    svc = ControllerService(system=FakeSystem(cmdlines_data={77: bare}).system, paths=Paths(runtime_root=tmp_path))
    copy = instances.copy_of(svc, "kiss", band="433")[0]
    assert instances.start_state(svc, "kiss", comp, copy, "433", RunState.RUNNING) is RunState.RUNNING


def test_g1_b1_a_band_caller_keeps_the_running_instances_radio_when_a_lower_radio_joins(tmp_path):
    # 13.16 (G1-B1): kiss@r3 runs, 433's only radio, by its verified launch record; r1 then joins 433. A band caller
    # (the internal starts and stops) still means the instance that runs, on r3, never the new first radio.
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        svc = _svc(tmp_path, rows=ROWS[1:])
        stk = svc.stack("kiss")
        assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), p.pid, band="433",
                                              extra={"rid": "r3"})
        radios_fixture.write(tmp_path, ROWS)                                      # r1 joins 433, below r3
        assert instances.band_radio(svc._paths, "433") == "r1"
        by_band = instances.copy_of(svc, "kiss", band="433")[0]
        assert [c.rid for c in (by_band, instances.copy_of(svc, "kiss", "r3")[0])] == ["r3", "r3"]
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_b1_re_the_parse_carries_one_record_snapshot_into_its_copy(tmp_path, monkeypatch):
    # G1-B1-RE: kiss@r3 runs by its record; r1 is disabled. The record vanishes between two reads: the parse and its
    # copy_of share ONE snapshot, so kiss@r3 is never refused or re-keyed half-way (Lifecycle.stop re-verifies
    # ownership before it acts).
    rows = (R1_OFF, ROWS[1])
    svc = _svc(tmp_path, rows=rows)
    reads = iter([{"r3"}])
    monkeypatch.setattr(instances, "recorded", lambda svc, sid: next(reads, set()))
    sid, copy, band, why = instances.parse_target(svc, "kiss@r3", "stop")
    assert why == "" and copy is not None, why
    assert (sid, copy.rid, band) == ("kiss", "r3", "433")


@pytest.mark.parametrize("verb", ["start", "restart", "stop"])
def test_k7_an_empty_radio_id_is_refused_never_the_band_callers_instance(tmp_path, verb):
    # `kiss@` (G1-C54): before, copy_of(rid="") took the band-caller path and `restart kiss@` ran on r1
    svc = _svc(tmp_path)
    assert instances.parse_target(svc, "kiss@", verb) == ("kiss", None, "", "kiss@: name a radio (kiss@<rid>)")
