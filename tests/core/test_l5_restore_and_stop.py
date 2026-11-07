"""Two stage-L5 rows the commit's own tests leave open (B4-TABLES-v2 D15; B4-WIRING-TABLES-v1 W1-11).

D15: a restore item whose radio is gone is refused with its reason, and the restore **continues with the others**.
W1-11: a per-band stop of a band two radios share is refused naming them **and signals nothing**.
Each test builds its own records, hardware list and service (the topic adds files only; the helpers of
test_boot_restore.py and tests/radios_fixture.py are not imported). radios.py is new: each test imports it itself, so
the file collects on a release before L5 and each test fails there on its own.
"""
from __future__ import annotations

import json
import os

import pytest

from lhpc.core import service_boot_restore as sbr
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session


def _record(tmp_path, *, stack, comp, band, rid, pid, nonce):
    """A schema-v1 ownership record of a previous boot, written where the restore reads it."""
    rec = {"launch_id": f"{comp}__{band}__{pid}__{nonce}", "stack": stack, "component": comp, "band": band,
           "pid": pid, "role": "", "launched_at": 1000, "version": 1, "requested_target": stack,
           "start_scope": "stack", "boot_id": "OLDBOOT", "starttime": "123", "pgid": pid, "sid": pid, "rid": rid}
    d = tmp_path / "state" / "owned"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
    return rec


def _hardware_list(tmp_path, rows):
    from lhpc.core import radios
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render(tuple(rows)))
    assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID


def _radio(rid, name, model, band, **kw):
    from lhpc.core import radios
    return radios.Radio(rid, name, model, band, **kw)


def _restore_service(tmp_path, monkeypatch, starts):
    _hardware_list(tmp_path, [_radio("r1", "low", "loraham", "433"), _radio("r2", "high", "loraham", "868")])
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))

    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
              _before_start_locked=None, _operator=True, position=None, position_note="", inst=None):
        starts.append((target, band))
        if _before_start_locked is not None:
            refusal = _before_start_locked()
            if refusal is not None:
                return refusal
        return ActionResult(True, "started")
    monkeypatch.setattr(ControllerService, "start", start)
    return svc


def test_d15_an_item_on_a_gone_radio_is_refused_and_the_restore_continues_with_the_others(tmp_path, monkeypatch):
    starts = []
    svc = _restore_service(tmp_path, monkeypatch, starts)
    _record(tmp_path, stack="kiss", comp="loraham-kiss-tnc", band="868", rid="r2", pid=999991, nonce="ab" * 16)
    gone = _record(tmp_path, stack="chat", comp="loraham-chat", band="433", rid="r9", pid=999992, nonce="cd" * 16)
    assert svc.boot_restore_run().ok
    assert starts == [("kiss", "868")]                                # the other item still runs
    j = json.loads((tmp_path / "state" / "boot-restore.json").read_text())
    assert [(s["stack"], s["reason"]) for s in j["skipped"]] == [("chat", "radio r9 no longer exists")]
    assert j["skipped"][0]["evidence_ids"] == [gone["launch_id"]]
    assert [(i["target"], i["rid"]) for i in j["items"]] == [("kiss", "r2")]


@pytest.mark.parametrize("op", ["stop", "restart"])
def test_w1_11_the_refused_per_band_stop_of_a_shared_band_signals_nothing(tmp_path, monkeypatch, op):
    _hardware_list(tmp_path, [_radio("r1", "a", "uputronics", "868", ce="ce0"), _radio("r2", "b", "uputronics", "868", ce="ce1")])
    svc = ControllerService(system=FakeSystem(cmdlines_data={100: ["loraham_daemon", "--radio", "868", "--id", "r1"],
                                                             101: ["loraham_daemon", "--radio", "868", "--id", "r2"]}
                                              ).system, paths=Paths(runtime_root=tmp_path))
    signals = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append(("kill", pid, sig)))
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append(("killpg", pgid, sig)))
    res = getattr(svc, op)("daemon", apply=True, band="868")
    assert not res.ok and "r1" in res.summary and "r2" in res.summary, res.summary
    assert signals == []
