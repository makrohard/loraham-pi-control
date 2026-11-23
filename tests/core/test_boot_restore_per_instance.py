"""Item 7e (DESIGN-7E 8d3e823f, DEC-W49; K4b: no slots): boot restore per instance key, through the service's
boot-restore run. Every record's key is `<stack>@<rid>`, its radio's: an item is kept while its radio exists, is
enabled and is on its band, and starts that radio's instance. A record from the slot builds (one with a `slot` field)
is refused at read (13.8), never resolved to a radio. A stop of `<stack>@<rid>` holds back that instance alone (L55)."""
from __future__ import annotations

import json

import pytest
import radios_fixture

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
    """A previous boot's ownership record of a full-stack start, where the restore reads it; with `slot` it is a
    record from the slot builds."""
    nonce = f"{pid:032x}"
    rec = {"launch_id": f"{comp}__{band}__{pid}__{nonce}", "stack": stack, "component": comp, "band": band,
           "pid": pid, "role": "", "launched_at": at, "version": 1, "requested_target": stack,
           "start_scope": "stack", "boot_id": "OLDBOOT", "starttime": "123", "pgid": pid, "sid": pid, "rid": rid,
           **({} if slot is _NO_SLOT else {"slot": slot})}
    d = tmp_path / "state" / "owned"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
    return rec


def _box(tmp_path, rows):
    """The hardware list on disk (it gives the copies); the service over it."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render(tuple(rows)))
    assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _service(tmp_path, monkeypatch, rows, starts):
    """A boot-restore-ready service whose stack starts are recorded as (target, band, the instance's rid or None)."""
    svc = _box(tmp_path, rows)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))

    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
              _before_start_locked=None, _operator=True, position=None, position_note="", inst=None):
        starts.append((target, band, inst.rid if inst is not None else None))
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


def test_two_instances_on_two_bands_are_both_restored(tmp_path, monkeypatch):
    # kiss ran on r2 (868) and on r3 (433): two instances, each on its record's radio and band.
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    _record(tmp_path, rid="r2", band="868", pid=999901, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999902, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", "r2"), ("kiss", "433", "r3")]
    assert [(i["target"], i["rid"]) for i in _journal(tmp_path)["items"]] == [("kiss@r2", "r2"), ("kiss@r3", "r3")]
    assert _skips(tmp_path) == []


@pytest.mark.parametrize("rows", [[HAT433, USB433], [USB433, HAT433]], ids=["rid-order", "file-order"])
def test_two_instances_on_one_band_are_both_restored_whatever_the_list_order(tmp_path, monkeypatch, rows):
    starts = []
    svc = _service(tmp_path, monkeypatch, rows, starts)
    _record(tmp_path, rid="r1", band="433", pid=999911, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999912, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "433", "r1"), ("kiss", "433", "r3")]
    assert _skips(tmp_path) == []


@pytest.mark.parametrize("rows, why", [
    ([HAT868], "radio r3 no longer exists"),
    ([HAT868, radios.Radio("r3", "rfm", "ch341-rfm98pw-433", "433", enabled=False, usb_serial="CHE1W433")],
     "radio r3 is disabled"),
    ([HAT868, radios.Radio("r3", "ms", "meshstick-1262", "868", usb_serial="MS654321")],
     "radio r3 is on 868 MHz now, not 433 MHz"),
])
def test_an_instance_whose_radio_is_gone_disabled_or_moved_is_refused_and_the_other_runs(tmp_path, monkeypatch,
                                                                                         rows, why):
    starts = []
    svc = _service(tmp_path, monkeypatch, rows, starts)
    _record(tmp_path, rid="r2", band="868", pid=999931, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999932, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", "r2")]
    assert _skips(tmp_path) == [("kiss@r3", why)]


def test_stopping_an_instance_holds_back_that_instance_alone(tmp_path, monkeypatch):
    # L55: the operator stopped kiss@r3 (nothing ran, so the stop succeeds and records the intent); the next boot
    # restores kiss@r2 and not kiss@r3.
    from lhpc.core import instances
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    sid, inst, band, why = instances.resolve(svc, "kiss@r3")
    assert why == "" and svc.stop(sid, apply=True, band=band, inst=inst, _operator=True).ok
    _record(tmp_path, rid="r2", band="868", pid=999941, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999942, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", "r2")]
    assert _skips(tmp_path) == [("kiss@r3", sbr.BootRestoreOpsMixin.INTENT_SKIP_REASON)]


def test_starting_an_instance_ends_its_own_stop_intent_and_leaves_the_others(tmp_path, monkeypatch):
    # L55, the start side: the applied start of kiss@r3 removes kiss@r3's intent only; kiss@r1's stays. The start's
    # body is stubbed: only its wrapper's intent hook is under test.
    from lhpc.core import instances
    svc = _box(tmp_path, [HAT433, HAT868, USB433])
    intents = tmp_path / "state" / "stop-intent"
    for t in ("kiss@r1", "kiss@r3"):
        sid, inst, band, _why = instances.resolve(svc, t)
        assert svc.stop(sid, apply=True, band=band, inst=inst).ok
    assert sorted(p.name for p in intents.iterdir()) == ["kiss@r1.json", "kiss@r3.json"]
    monkeypatch.setattr(ControllerService, "_start_impl", lambda self, target, **kw: ActionResult(True, "stubbed"))
    sid, inst, band, _why = instances.resolve(svc, "kiss@r3")
    assert svc.start(sid, apply=True, band=band, inst=inst).ok
    assert sorted(p.name for p in intents.iterdir()) == ["kiss@r1.json"]


def test_an_instance_the_list_no_longer_resolves_at_its_start_fails_and_starts_nothing_else(tmp_path, monkeypatch):
    # The list changed between the plan and the item's start: the item fails with the reason; nothing starts in its
    # place.
    from lhpc.core import instances
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    _record(tmp_path, rid="r2", band="868", pid=999961, at=1000)
    _record(tmp_path, rid="r3", band="433", pid=999962, at=1001)
    real = instances.resolve
    monkeypatch.setattr(instances, "resolve", lambda svc, t: ("kiss", None, "", "kiss@r3: r3 is not in the hardware list")
                        if t == "kiss@r3" else real(svc, t))
    svc.boot_restore_run()
    assert starts == [("kiss", "868", "r2")]
    item = next(i for i in _journal(tmp_path)["items"] if i["target"] == "kiss@r3")
    assert (item["state"], item["result"]["summary"]) == ("failed", "kiss@r3: r3 is not in the hardware list")


def test_an_instances_own_part_is_restored_on_the_instance(tmp_path, monkeypatch):
    # meshcore@r4's web UI, started on its own, comes back after its node, on the same instance.
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, STICK868], starts)
    _record(tmp_path, rid="r4", band="868", pid=999971, stack="meshcore", comp="meshcore-node")
    rec = _record(tmp_path, rid="r4", band="868", pid=999972, stack="meshcore", comp="meshcore-webui")
    rec.update(start_scope="component", requested_target="meshcore-webui")
    (tmp_path / "state" / "owned" / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
    assert svc.boot_restore_run().ok
    assert starts == [("meshcore", "868", "r4"), ("meshcore-webui", "868", "r4")]


@pytest.mark.parametrize("slot", [0, 1, 7, True, None, "1"])
def test_a_record_from_the_slot_builds_is_refused_at_read_and_never_started(tmp_path, monkeypatch, slot):
    # 13.8 (K4b): a record with a `slot` field (any value) is not an instance's record any more; boot restore never
    # resolves it to a radio, and its reason names the remedy.
    from lhpc.core.lifecycle import Lifecycle
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    _record(tmp_path, rid="r3", band="433", pid=999983, slot=slot)
    svc.boot_restore_run()
    assert starts == []
    _valid, issues, _state = Lifecycle(svc._paths, (), None, None).owned_inventory()
    assert [i["reason"] for i in issues] == [
        f"a record from before per-radio instances (slot {slot!r}): stop its process by hand and start it by name "
        "(kiss@<rid>)"]


@pytest.mark.parametrize("observe", [0.0, 1.0], ids=["recorded-directly", "recorded-once-observed"])
@pytest.mark.parametrize("rid", ["r1", "r3", "r12"])
def test_a_launch_records_its_instances_radio_and_no_slot(tmp_path, monkeypatch, rid, observe):
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
    assert life.start(Stack(id="s", name="s", main="c"), comp, inst=commands.Instance(rid)).ok
    assert extras == [{"rid": rid, "bases": {}}]                       # G2-UIU3-R3: its launch ports, {} = defaults


def test_an_instance_whose_radio_moved_band_after_the_plan_is_not_started_there(tmp_path, monkeypatch):
    # G1-C6: the list changed between the plan (r3 on 433) and the item's start (r3 on 868 now): the item fails with
    # the reason, and nothing starts on 868; the operator starts it by hand.
    from lhpc.core import instances
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    _record(tmp_path, rid="r3", band="433", pid=999991)
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


def _owned(tmp_path):
    return sorted(p.name for p in (tmp_path / "state" / "owned").glob("*.json"))


def test_a_skipped_instance_is_not_restored_by_a_later_boot(tmp_path, monkeypatch):
    """A skip retires its evidence. Box H: meshcom@r3's records from boot cb1a1bbf were skipped at the next boot and
    restored by the boot after that, from records two boots old. Here kiss@r3 is skipped (its radio is disabled);
    re-enabled, a later boot must not start it from those records."""
    disabled = radios.Radio("r3", "rfm", "ch341-rfm98pw-433", "433", enabled=False, usb_serial="CHE1W433")
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, disabled], starts)
    _record(tmp_path, rid="r2", band="868", pid=999951, at=1000)
    r3 = _record(tmp_path, rid="r3", band="433", pid=999952, at=1001)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", "r2")] and _skips(tmp_path) == [("kiss@r3", "radio r3 is disabled")]

    (tmp_path / "config" / "radios.toml").write_text(radios.render((HAT868, USB433)))     # r3 enabled again
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "NEXTBOOT")
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868", "r2")], starts                   # nothing more: no kiss@r3 from old records
    assert f"{r3['launch_id']}.json" not in _owned(tmp_path)                # its skip retired the record


def test_an_unreadable_intents_skip_keeps_its_evidence_for_a_later_boot(tmp_path, monkeypatch):
    """The one skip that is retried: with the stop intents unreadable no stack can be shown to be free of a stop, so
    nothing is restored, and a boot that can read them again restores from the same records."""
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    rec = _record(tmp_path, rid="r3", band="433", pid=999961, at=1000)
    monkeypatch.setattr(ControllerService, "_stop_intent_stacks", lambda self: None)
    assert svc.boot_restore_run().ok
    assert starts == [] and _skips(tmp_path) == [("kiss@r3", sbr.BootRestoreOpsMixin.INTENTS_UNREADABLE_REASON)]
    assert f"{rec['launch_id']}.json" in _owned(tmp_path)

    monkeypatch.undo()
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "NEXTBOOT")
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "433", "r3")], starts


def test_a_broken_hardware_list_at_boot_keeps_the_evidence_for_a_boot_with_it_fixed(tmp_path, monkeypatch):
    """An INVALID (broken or unreadable) radios.toml proves no radio gone: every item on a radio is skipped as that,
    its record kept, and the boot after the list is fixed restores it. (An ABSENT list is no radios: lasting.)"""
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    rec = _record(tmp_path, rid="r3", band="433", pid=999971, at=1000)
    daemon = _record(tmp_path, rid="", band="433", pid=999972, at=999, stack="daemon", comp="loraham-daemon")   # no radio
    good = (tmp_path / "config" / "radios.toml").read_text()
    (tmp_path / "config" / "radios.toml").write_text("[[radio]\nnot toml")
    assert svc.boot_restore_run().ok
    assert starts == [] and f"{rec['launch_id']}.json" in _owned(tmp_path)    # skipped, its record kept
    assert f"{daemon['launch_id']}.json" in _owned(tmp_path)                   # an item on no radio is held too
    skips = _skips(tmp_path)                                                   # kiss@r3, and the band daemon item
    assert len(skips) == 2 and ("kiss@r3" in {k for k, _r in skips}) and all("hardware list is not valid" in r
                                                                            for _k, r in skips), skips

    (tmp_path / "config" / "radios.toml").write_text(good)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "NEXTBOOT")
    assert svc.boot_restore_run().ok
    assert sorted(starts, key=str) == [("daemon", "433", None), ("kiss", "433", "r3")], starts   # both, from the kept records


@pytest.mark.no_default_hardware
def test_no_hardware_list_at_boot_is_no_radios_and_retires_the_evidence(tmp_path, monkeypatch):
    """An ABSENT radios.toml is lasting (no hardware list = no radios), unlike a broken one: the item on a radio is
    refused as gone and its record retired. (Without the marker the suite supplies a default list.)"""
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT433, HAT868, USB433], starts)
    rec = _record(tmp_path, rid="r3", band="433", pid=999981, at=1000)
    (tmp_path / "config" / "radios.toml").unlink()
    assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.ABSENT
    assert svc.boot_restore_run().ok
    assert starts == [] and _skips(tmp_path) == [("kiss@r3", "radio r3 no longer exists")]
    assert f"{rec['launch_id']}.json" not in _owned(tmp_path)


def test_a_boot_with_only_a_skipped_item_retires_its_record(tmp_path, monkeypatch):
    """The box case: nothing to restore (the journal's no-plan path), one item skipped; its record is retired too."""
    disabled = radios.Radio("r3", "rfm", "ch341-rfm98pw-433", "433", enabled=False, usb_serial="CHE1W433")
    starts = []
    svc = _service(tmp_path, monkeypatch, [HAT868, disabled], starts)
    rec = _record(tmp_path, rid="r3", band="433", pid=999991, at=1000)
    assert svc.boot_restore_run().ok
    assert _journal(tmp_path)["state"] == "no-plan" and _skips(tmp_path) == [("kiss@r3", "radio r3 is disabled")]
    assert starts == [] and f"{rec['launch_id']}.json" not in _owned(tmp_path)


def test_spi_refusal_a_direct_spi_instance_on_a_usb_radio_is_not_restored_and_says_why(tmp_path, monkeypatch):
    # SPI-REFUSAL T11: reticulum@r4 (a USB stick) fails with the one text (the resolver's refusal: claimed, then
    # failed with its why, as any item the resolver refuses); reticulum@r2 (SPI) is restored.
    # red-before (aa4a88c8): both were started
    starts = []
    svc = _service(tmp_path, monkeypatch, (HAT433, HAT868, STICK868), starts)
    radios_fixture.reticulum_backend(tmp_path, ["r1", "r2", "r4"])     # the direct-SPI node (B1)
    _record(tmp_path, rid="r2", band="868", pid=999941, at=1000, stack="reticulum", comp="rns")
    _record(tmp_path, rid="r4", band="868", pid=999942, at=1001, stack="reticulum", comp="rns")
    assert not svc.boot_restore_run().ok
    assert starts == [("reticulum", "868", "r2")]
    assert [(i["target"], i["state"], i["result"]["summary"]) for i in _journal(tmp_path)["items"]
            if i["target"].endswith("@r4")] == \
        [("reticulum@r4", "failed", "reticulum@r4: reticulum with backend spi drives SPI radios only, r4 is a USB-CH341 radio: use "
                                    "reticulum@r2")]                     # r1 is 433: Reticulum runs on 868 only


def test_t6_an_old_voice_on_868_or_reticulum_on_433_record_is_not_restored_and_says_why(tmp_path, monkeypatch):
    # F34: Voice runs on 433 only and Reticulum on 868 only. A record from before the upgrade of voice@r2 (868) and of
    # reticulum@r1 (433) starts nothing; each is skipped with its why. red-before (C1): both were started
    starts = []
    svc = _service(tmp_path, monkeypatch, (HAT433, HAT868), starts)
    _record(tmp_path, rid="r2", band="868", pid=999951, at=1000, stack="voice", comp="loraham-voice")
    _record(tmp_path, rid="r1", band="433", pid=999952, at=1001, stack="reticulum", comp="rns")
    svc.boot_restore_run()
    assert starts == []
    assert _journal(tmp_path)["items"] == []
    assert sorted(_skips(tmp_path)) == [
        ("reticulum@r1", "band ambiguity: recorded band '433' contradicts manifest band '868'"),
        ("voice@r2", "band ambiguity: recorded band '868' contradicts manifest band '433'")]
