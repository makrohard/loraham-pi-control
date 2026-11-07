"""Item 7e (DESIGN-7E 8d3e823f, DEC-W49): boot restore per instance key, through the service's boot-restore run.

Slot 0's key is `<stack>`; a listed instance's is `<stack>@<rid>`. A record's rid is a listed instance's when the
stack lists it (the rule 7c2b's plain stop uses). A slot-0 item is kept only while slot 0's radio on its band
(DEC-W16) is still its rid; this replaces G2-0.14.0-1 item 3's "the band's enabled radios are exactly [rid]". A
slot-k item is kept while its radio exists, is enabled and is on its band. A stop of `<stack>@<rid>` holds back
that instance alone (L55)."""
from __future__ import annotations

import json

import pytest

from lhpc.core import radios
from lhpc.core import service_boot_restore as sbr
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session

HAT433 = radios.Radio("r1", "low", "loraham", "433")
HAT868 = radios.Radio("r2", "high", "loraham", "868")
USB433 = radios.Radio("r3", "rfm", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433")
STICK868 = radios.Radio("r4", "stick", "meshstick-1262", "868", usb_serial="MS123456")
TNC = "loraham-kiss-tnc"


_NO_SLOT = object()


def _record(tmp_path, *, rid, band, pid, at=1000, stack="kiss", comp=TNC, slot=_NO_SLOT):
    """A previous boot's ownership record of a full-stack start, where the restore reads it; without `slot` it is a
    record from before 7e."""
    nonce = f"{pid:032x}"
    rec = {"launch_id": f"{comp}__{band}__{pid}__{nonce}", "stack": stack, "component": comp, "band": band,
           "pid": pid, "role": "", "launched_at": at, "version": 1, "requested_target": stack,
           "start_scope": "stack", "boot_id": "OLDBOOT", "starttime": "123", "pgid": pid, "sid": pid, "rid": rid,
           **({} if slot is _NO_SLOT else {"slot": slot})}
    d = tmp_path / "state" / "owned"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
    return rec


def _box(tmp_path, rows, listed):
    """The hardware list and the instance list on disk; the service over them."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render(tuple(rows)))
    assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID
    if listed:
        (tmp_path / "config" / "instances.toml").write_text(listed)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _service(tmp_path, monkeypatch, rows, listed, starts):
    """A boot-restore-ready service whose stack starts are recorded as (target, band, (slot, rid) or None)."""
    svc = _box(tmp_path, rows, listed)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))

    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
              _before_start_locked=None, _operator=True, position=None, position_note="", inst=None):
        starts.append((target, band, (inst.slot, inst.rid) if inst is not None else None))
        if _before_start_locked is not None:
            refusal = _before_start_locked()
            if refusal is not None:
                return refusal
        return ActionResult(True, "started")
    monkeypatch.setattr(ControllerService, "start", start)
    return svc


def _journal(tmp_path):
    return json.loads((tmp_path / "state" / "boot-restore.json").read_text())


def _skips(tmp_path):
    return [(s["stack"], s["reason"]) for s in _journal(tmp_path)["skipped"]]


def test_slot0_and_a_listed_instance_on_another_band_are_both_restored(tmp_path, monkeypatch):
    # kiss ran as slot 0 on r2 (868) and as kiss@r3 (433), each band with one radio. Slot 0's markers name 868;
    # kiss@r3 writes none, so its band is its record's.
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, USB433], 'kiss = ["r3"]\n', starts)
    _record(tmp_path, rid="r2", band="868", pid=999901, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999902, at=1001)
    (tmp_path / "state" / "running").mkdir(parents=True)
    (tmp_path / "state" / "running" / "kiss.band").write_text("868\n")
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", None), ("kiss", "433", (1, "r3"))]
    assert [(i["target"], i["rid"]) for i in _journal(tmp_path)["items"]] == [("kiss", "r2"), ("kiss@r3", "r3")]
    assert _skips(tmp_path) == []


def test_slot0_on_r1_and_kiss_at_r3_on_the_same_band_are_both_restored(tmp_path, monkeypatch):
    # DEC-W49 (2): today's rule refuses both ("433 MHz has 2 enabled radios now"). Slot 0 starts on r1 (the band's
    # first unlisted radio, DEC-W16), so its item is kept; kiss@r3 starts on r3.
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, USB433], 'kiss = ["r3"]\n', starts)
    _record(tmp_path, rid="r1", band="433", pid=999911, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999912, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "433", None), ("kiss", "433", (1, "r3"))]
    assert _skips(tmp_path) == []


@pytest.mark.parametrize("rows, listed, why", [
    # G2-0.14.0-1 item 3's own case: a second enabled radio with a lower rid joined the band, so slot 0 would now
    # start on r1, not on the r3 it ran on.
    ([HAT433, USB433], "", "slot 0 of kiss on 433 MHz starts on r1 now, not r3"),
    ([USB433, HAT433], "", "slot 0 of kiss on 433 MHz starts on r1 now, not r3"),     # by rid, not by file order
    # every radio of the band holds a listed instance: slot 0 has no radio there
    ([HAT433, USB433], 'kiss = ["r1", "r3"]\n', "slot 0 of kiss has no radio on 433 MHz now"),
])
def test_a_slot0_item_whose_radio_is_no_longer_slot0s_is_refused(tmp_path, monkeypatch, rows, listed, why):
    starts = []
    svc = _service(tmp_path, monkeypatch, rows, listed, starts)
    rec = _record(tmp_path, rid="r3", band="433", pid=999921)
    if listed:                       # r3 is listed now: its record reads as kiss@r3, which still resolves
        assert svc.boot_restore_run().ok
        assert starts == [("kiss", "433", (2, "r3"))]
        return
    svc.boot_restore_run()
    assert starts == []
    assert _skips(tmp_path) == [("kiss", why)]
    assert _journal(tmp_path)["skipped"][0]["evidence_ids"] == [rec["launch_id"]]


@pytest.mark.parametrize("rows, why", [
    ([HAT868], "radio r3 no longer exists"),
    ([HAT868, radios.Radio("r3", "rfm", "ch341-rfm98pw-433", "433", enabled=False, usb_serial="CHE1W433")],
     "radio r3 is disabled"),
    ([HAT868, radios.Radio("r3", "ms", "meshstick-1262", "868", usb_serial="MS654321")],
     "radio r3 is on 868 MHz now, not 433 MHz"),
])
def test_a_listed_instance_whose_radio_is_gone_or_disabled_is_refused_and_slot0_still_runs(tmp_path, monkeypatch,
                                                                                         rows, why):
    starts = []
    svc = _service(tmp_path, monkeypatch, rows, 'kiss = ["r3"]\n', starts)
    _record(tmp_path, rid="r2", band="868", pid=999931, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999932, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", None)]
    assert _skips(tmp_path) == [("kiss@r3", why)]


def test_stopping_an_instance_holds_back_that_instance_alone(tmp_path, monkeypatch):
    # L55: the operator stopped kiss@r3 (nothing ran, so the stop succeeds and records the intent); the next boot
    # restores slot 0 and not kiss@r3.
    from lhpc.core import instances
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, USB433], 'kiss = ["r3"]\n', starts)
    sid, inst, band, why = instances.resolve(svc, "kiss@r3")
    assert why == "" and svc.stop(sid, apply=True, band=band, inst=inst, _operator=True).ok
    _record(tmp_path, rid="r2", band="868", pid=999941, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999942, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", None)]
    assert _skips(tmp_path) == [("kiss@r3", sbr.BootRestoreOpsMixin.INTENT_SKIP_REASON)]


def test_starting_an_instance_ends_its_own_stop_intent_and_leaves_slot0s(tmp_path, monkeypatch):
    # L55, the start side: the applied start of kiss@r3 removes kiss@r3's intent only; slot 0's stays. The start's
    # body is stubbed: only its wrapper's intent hook is under test (as in test_boot_restore.py's slot-0 test).
    from lhpc.core import instances
    svc = _box(tmp_path, [HAT868, USB433], 'kiss = ["r3"]\n')
    intents = tmp_path / "state" / "stop-intent"
    sid, inst, band, _why = instances.resolve(svc, "kiss@r3")
    assert svc.stop("kiss", apply=True).ok and svc.stop(sid, apply=True, band=band, inst=inst).ok
    assert sorted(p.name for p in intents.iterdir()) == ["kiss.json", "kiss@r3.json"]
    monkeypatch.setattr(ControllerService, "_start_impl", lambda self, target, **kw: ActionResult(True, "stubbed"))
    assert svc.start(sid, apply=True, band=band, inst=inst).ok
    assert sorted(p.name for p in intents.iterdir()) == ["kiss.json"]


def test_an_instance_list_that_does_not_read_back_refuses_the_items_of_stacks_with_instances(tmp_path, monkeypatch):
    # Without the list a record's radio cannot be told slot 0's from an instance's: the item is refused, naming why.
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, USB433], 'kiss = ["r3"\n', starts)
    _record(tmp_path, rid="r3", band="433", pid=999951)
    svc.boot_restore_run()
    assert starts == []
    [(stack, why)] = _skips(tmp_path)
    assert stack == "kiss" and why.partition(": ")[0] == "instances.toml does not read back"


def test_an_instance_the_list_no_longer_resolves_at_its_start_fails_and_never_starts_slot0(tmp_path, monkeypatch):
    # The list changed between the plan and the item's start: the item fails with the reason; nothing starts in its
    # place as slot 0.
    from lhpc.core import instances
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, USB433], 'kiss = ["r3"]\n', starts)
    _record(tmp_path, rid="r2", band="868", pid=999961, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999962, at=1001)
    real = instances.resolve
    monkeypatch.setattr(instances, "resolve", lambda svc, t: ("kiss", None, "", "kiss@r3 is not a listed instance")
                        if t == "kiss@r3" else real(svc, t))
    svc.boot_restore_run()
    assert starts == [("kiss", "868", None)]
    item = next(i for i in _journal(tmp_path)["items"] if i["target"] == "kiss@r3")
    assert (item["state"], item["result"]["summary"]) == ("failed", "kiss@r3 is not a listed instance")


def test_an_instances_own_part_is_restored_on_the_instance(tmp_path, monkeypatch):
    # meshcore@r4's web UI, started on its own, comes back after its node, on the same instance.
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, STICK868], 'meshcore = ["r4"]\n', starts)
    _record(tmp_path, rid="r4", band="868", pid=999971, stack="meshcore", comp="meshcore-node")
    rec = _record(tmp_path, rid="r4", band="868", pid=999972, stack="meshcore", comp="meshcore-webui")
    rec.update(start_scope="component", requested_target="meshcore-webui")
    (tmp_path / "state" / "owned" / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
    assert svc.boot_restore_run().ok
    assert starts == [("meshcore", "868", (1, "r4")), ("meshcore-webui", "868", (1, "r4"))]


def test_a_removed_instances_record_is_refused_and_never_starts_slot0_on_its_radio(tmp_path, monkeypatch):
    # kiss@r3 ran (slot 1); it was removed from the list before the restore. r3 is now 433's only radio, so read by
    # the list its record would start slot 0 there, which never ran. The record's slot names the instance instead.
    starts = []
    svc = _service(tmp_path, monkeypatch, [USB433], 'kiss = [""]\n', starts)
    _record(tmp_path, rid="r3", band="433", pid=999981, slot=1)
    svc.boot_restore_run()
    assert starts == []
    [item] = _journal(tmp_path)["items"]
    assert (item["target"], item["state"]) == ("kiss@r3", "failed")
    assert item["result"]["summary"] == "kiss@r3 is not a listed instance (lhpc stack add-instance kiss r3)"


def test_a_slot0_record_on_a_radio_listed_since_is_slot0s_and_refused(tmp_path, monkeypatch):
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, USB433], 'kiss = ["r3"]\n', starts)
    _record(tmp_path, rid="r3", band="433", pid=999982, slot=0)
    svc.boot_restore_run()
    assert starts == []
    assert _skips(tmp_path) == [("kiss", "slot 0 of kiss on 433 MHz starts on r1 now, not r3")]


@pytest.mark.parametrize("slot", [_NO_SLOT, True, False, "1", 1.0, -1, 8, None, [1]])
def test_a_record_without_a_valid_slot_reads_by_the_instance_list(tmp_path, monkeypatch, slot):
    # A record from before 7e, or one whose slot is not a plain int 0..7: the list decides, as before.
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, USB433], 'kiss = ["r3"]\n', starts)
    _record(tmp_path, rid="r3", band="433", pid=999983, slot=slot)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "433", (1, "r3"))]


@pytest.mark.parametrize("observe", [0.0, 1.0], ids=["recorded-directly", "recorded-once-observed"])
@pytest.mark.parametrize("slot, rid", [(0, "r1"), (1, "r3"), (7, "r2")])
def test_a_launch_records_its_instances_slot_beside_its_radio(tmp_path, monkeypatch, slot, rid, observe):
    from lhpc.core import commands
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind, Stack
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), FakeSystem().system,
                     spawn=lambda argv, log, cwd=None, env=None: 4321)
    life.OBSERVE_TIMEOUT_S = observe
    # The observed path: the process is alive and its command line confirms the launch at the first look.
    monkeypatch.setattr(life, "_proc_alive", lambda pid: True)
    monkeypatch.setattr(life, "_argv_confirms", lambda cmd, argv: True)
    monkeypatch.setattr(life, "_capture_identity", lambda pid: None)
    extras = []
    monkeypatch.setattr(life, "record_launch", lambda *a, **k: extras.append(k.get("extra")) or True)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("true",))
    assert life.start(Stack(id="s", name="s", main="c"), comp, inst=commands.Instance(slot, rid)).ok
    assert extras == [{"rid": rid, "slot": slot}]


def test_an_instance_whose_radio_moved_band_after_the_plan_is_not_started_there(tmp_path, monkeypatch):
    # G1-C6: the list changed between the plan (r3 on 433) and the item's start (r3 on 868 now): the item fails with
    # the reason, and nothing starts on 868; the operator starts it by hand.
    from lhpc.core import instances
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, USB433], 'kiss = ["r3"]\n', starts)
    _record(tmp_path, rid="r3", band="433", pid=999991, slot=1)
    real = instances.resolve

    def resolve(svc, target):
        sid, inst, band, why = real(svc, target)
        return (sid, inst, "868", why) if target == "kiss@r3" else (sid, inst, band, why)
    monkeypatch.setattr(instances, "resolve", resolve)
    svc.boot_restore_run()
    assert starts == []
    [item] = _journal(tmp_path)["items"]
    assert (item["state"], item["result"]["summary"]) == (
        "failed", "kiss@r3: r3 is on 868 MHz now, not 433 MHz: start it by hand")
