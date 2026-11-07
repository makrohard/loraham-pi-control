"""Week item 13 (field test F2): a GPS feed without a fix reads "no fix yet", and the doctor names the trap that kept
the clock from following the GPS.

The feed counts "connected, no fix" as healthy (a cold receiver takes minutes), so the stack read RUNNING long before
the clock could follow a fix. The component now carries the typed state `gps.fix` ("fix" | "no-fix" | "" when the
marker does not say), the stack page shows "no fix yet" and `lhpc status` adds the line. The doctor names a gpsd that
runs while chrony has no GPS sample (a gpsd started by hand cannot write chrony's root-only SHM). The start gate is
unchanged: `connected` still starts.
"""
from __future__ import annotations

import json
import os
import random
import time

import pytest
from htmlq import parse

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import StatusProber

CHRONY = ("chronyc", "-n", "-c", "sources")
GPSD = ("systemctl", "is-active", "gpsd.service")
PGREP = ("pgrep", "-x", "gpsd")


def _svc(tmp_path, commands=None):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem(commands=commands or {}).system, paths=Paths(runtime_root=tmp_path))


def _marker(tmp_path, payload):
    d = tmp_path / "state" / "gps" / "meshtastic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "readiness.json").write_text(payload if isinstance(payload, str) else json.dumps(payload))


def _feed(state, **kw):
    return {"state": state, "detail": "", "sentences": 3, "fixes": 0, "updated": int(time.time()), "pid": os.getpid(),
            **kw}


def _fix(tmp_path):
    svc = _svc(tmp_path)
    comp = next(c for c in svc.stack("meshtastic").components if c.id == "meshtastic-gps")
    return StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path)).assess_component(comp).evidence


@pytest.mark.parametrize("state,word", [("connected", "no-fix"), ("ready", "fix")])
def test_r1_the_feed_state_is_typed_fix_or_no_fix(tmp_path, state, word):
    """RED before: no `gps.fix` at all; "connected" read like any running feed."""
    _marker(tmp_path, _feed(state, fixes=0 if state == "connected" else 4))
    assert _fix(tmp_path).get("gps.fix") == word


@pytest.mark.parametrize("seed", range(20))
def test_r2_an_unreadable_stale_or_foreign_marker_never_reads_fix_or_no_fix(tmp_path, seed):
    """Tri-state: what the marker does not say is "", never a fix and never "no fix". RED before for the key."""
    rng = random.Random(seed)  # noqa: S311 - a seeded, repeatable test input, no secret
    bad = rng.choice([
        "[" * 40, "", "null", json.dumps(["connected"]), json.dumps({"state": 7}),
        _feed(rng.choice(["connected", "ready"]), updated=int(time.time()) - 10_000),     # stale
        _feed(rng.choice(["connected", "ready"]), pid=rng.choice([None, "x", -1, True])),      # no usable owner
        _feed(rng.choice(["source-lost", "starting", "stale", "", "READY"])),
    ])
    _marker(tmp_path, bad)
    assert _fix(tmp_path).get("gps.fix") == ""


def _snapshot_with(svc, evidence):
    from lhpc.core.model import ComponentStatus, RunState
    from lhpc.core.status import Snapshot, StackStatus
    st = svc.stack("meshtastic")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=st)
    for comp in st.components:
        ss.components[comp.id] = ComponentStatus(component_id=comp.id, run_state=RunState.RUNNING,
                                                 evidence=dict(evidence) if comp.id == "meshtastic-gps" else {})
    snap.stacks.append(ss)
    return snap


@pytest.mark.parametrize("fix,shown", [("no-fix", True), ("fix", False), ("", False)],
                         ids=["no-fix", "control-fix", "control-unknown"])
def test_r3_the_stack_page_shows_no_fix_yet(web, tmp_path, monkeypatch, fix, shown):
    """RED before for "no-fix": the row said running and nothing else. (The snapshot is stood in for; the typed
    state itself is r1's.)"""
    svc = _svc(tmp_path)
    snap = _snapshot_with(svc, {"gps.fix": fix, "gps.feed": "source reachable, waiting for a fix"})
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *a, **k: snap)
    doc = parse(web(service_factory=lambda: svc).get("/stacks/meshtastic/body").get_data(as_text=True))
    row = doc.within(doc.by_id("comp-meshtastic-gps"))
    assert bool(row.find("span", **{"data-state": "no-fix"})) is shown


@pytest.mark.parametrize("fix,shown", [("no-fix", True), ("fix", False)], ids=["no-fix", "control-fix"])
def test_r4_lhpc_status_adds_the_no_fix_line(tmp_path, monkeypatch, fix, shown):
    """RED before for "no-fix". Through the service's status (what `lhpc status` prints)."""
    svc = _svc(tmp_path)
    snap = _snapshot_with(svc, {"gps.fix": fix})
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *a, **k: snap)
    res = svc.status("meshtastic")
    lines = [d.strip() for d in res.details]
    i = next(n for n, d in enumerate(lines) if d.split()[:1] == ["meshtastic-gps"])
    assert (lines[i + 1] == "no fix yet (the receiver is reachable)") is shown


def _src(reach):
    return CR(0, f"#,?,GPS,0,2,{reach},20,0.001,0.001,0.0005\n^,*,192.0.2.1,2,6,377,30,0.0,0.0,0.01\n", "")


ACTIVE, INACTIVE = CR(0, "active\n", ""), CR(3, "inactive\n", "")
PROC, NOPROC = CR(0, "4242\n", ""), CR(1, "", "")


@pytest.mark.parametrize("chrony,proc,unit,want", [
    (_src("0"), PROC, INACTIVE, "started by hand"),          # the 11-02 boot: gpsd by hand, the unit stopped. RED before
    (_src("0"), PROC, ACTIVE, "no GPS samples yet"),        # a root gpsd without a fix. RED before
    (_src("377"), PROC, ACTIVE, None),                      # samples arrive
    (_src("377"), PROC, INACTIVE, None),                    # samples arrive (whoever runs gpsd)
    (_src("0"), NOPROC, INACTIVE, None),                    # no gpsd: other lines own that
    (CR(0, "^,?,192.0.2.1,2,6,0,30,0.0,0.0,0.01\n#,?,PPS,0,4,0,0,0.0,0.0,0.0\n", ""), PROC, INACTIVE, None),  # no GPS row
    (CR(1, "", "506 Cannot talk to daemon"), PROC, ACTIVE, "not known"),       # chronyc fails. RED before
    (CR(127, "", "chronyc: not found"), PROC, ACTIVE, None),                   # no chrony on this box
], ids=["hand-started", "service-no-fix", "control-samples", "control-samples-hand", "control-no-gpsd",
        "control-no-gps-row", "chronyc-unreadable", "control-no-chronyc"])
def test_r5_the_doctor_names_a_gpsd_that_feeds_chrony_nothing(tmp_path, chrony, proc, unit, want):
    svc = _svc(tmp_path, commands={CHRONY: chrony, PGREP: proc, GPSD: unit})
    gps = [d for d in svc.doctor().details if d.strip().split(":")[0] == "gps time"]
    assert (gps[0] if gps else None) is None if want is None else want in gps[0]


def test_r5b_a_missing_chronyc_binary_says_nothing(tmp_path, monkeypatch):
    """The runner raising FileNotFoundError (no chronyc installed) is not "not known"."""
    from lhpc.core import clock

    class Runner:
        def run(self, argv, timeout=None):
            raise FileNotFoundError(argv[0])
    assert clock.gps_time_line(Runner()) == []


def test_r6_control_a_feed_reachable_without_a_fix_still_starts(tmp_path):
    svc = _svc(tmp_path)
    comp = next(c for c in svc.stack("meshtastic").components if c.id == "meshtastic-gps")
    _marker(tmp_path, _feed("connected"))
    assert svc._gps_feed_ready(comp)[0] is True
