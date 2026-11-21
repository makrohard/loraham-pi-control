"""Q6c (i), his word "Both checks use the per-radio rule": the check inside the run (_running_conflicts) takes the
pre-start rule (run_blockers, instances.elsewhere): a holder told on ANOTHER radio of the band does not block, the
same radio does, and a holder no record tells still holds the whole band. Box H: r1 loraham 433 (SPI), r2 loraham
868 (SPI), r3 CH341-RFM98 433 and r4 PineDio 868 (USB). Holders run as real sleep processes under the argv their
spec matches, each with its verified launch record unless said otherwise."""
from __future__ import annotations

import contextlib
import subprocess
import sys

import radios_fixture

from lhpc.core import instances
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

DAEMON = ("daemon", "loraham-daemon")
RNS = ("reticulum", "rns")
MESHTASTIC = ("meshtastic", "meshtastic")
ARGV = {"loraham-daemon": lambda rid, band: ["loraham_daemon", "--radio", band, "--id", rid],
        "rns": lambda rid, band: ["loraham-rns-node"],
        "meshtastic": lambda rid, band: ["meshtasticd"]}


@contextlib.contextmanager
def _box(tmp_path, *holders):
    """A Box H service with `holders` running: (stack, component, rid, band[, record=True[, argv]])."""
    radios_fixture.write(tmp_path, radios_fixture.BOX_H)
    procs = [subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
             for _ in holders]
    try:
        table = {p.pid: (h[5] if len(h) > 5 else ARGV[h[1]](h[2], h[3])) for p, h in zip(procs, holders)}
        svc = ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))
        assert svc.set_operator_identity(callsign="XX0XXA").ok
        for p, (sid, cid, rid, band, *rest) in zip(procs, holders):
            if not rest or rest[0]:
                stk = svc.stack(sid)
                assert svc._lifecycle().record_launch(stk, stk.component(cid), p.pid, band=band, extra={"rid": rid})
        yield svc
    finally:
        for p in procs:
            p.kill()
            p.wait(timeout=5)


def _conflicts(svc, who, rid, band="868"):
    """The in-run check's verdict on starting `who` (stack, component) as its instance on `rid` (None: no instance)."""
    sid, cid = who
    inst = instances.copy_of(svc, sid, rid)[0] if rid else None
    return svc._running_conflicts(svc.stack(sid).component(cid), band, inst)


def _vs_daemon(rid):
    return (f"loraham-daemon (provider) vs rns (exclusive) on loraham.radio.868 (radios r2, r4) — both running; "
            f"stop daemon@{rid} or use another radio")


def test_t1_rns_on_r2_starts_beside_the_daemon_on_r4_through_the_public_start(tmp_path, monkeypatch):
    # red-before (a63276de): rns BLOCKED "resource conflict: loraham-daemon (provider) vs rns ...", while the
    # pre-start check (run_blockers) let it through. The gates BEFORE the resource gate are stubbed (driver framing,
    # build state, requirements, install), so the run reaches it; past it the spawn fails on the absent checkout.
    with _box(tmp_path, (*DAEMON, "r4", "868")) as svc:
        assert svc.run_blockers("reticulum", "868", "", instances.copy_of(svc, "reticulum", "r2")[0]) == []
        for name, stub in (("_rns_driver_has_framing", lambda self: True), ("_build_complete", lambda self, c: True),
                           ("start_blocking_requirements", lambda self, c: []), ("install_blocker", lambda self, c: "")):
            monkeypatch.setattr(type(svc), name, stub)
        res = svc.start("reticulum", apply=True, inst=instances.copy_of(svc, "reticulum", "r2")[0])
        rns = next(r for r in res.results if r.component == "rns")
        assert rns.outcome is not Outcome.BLOCKED, rns.summary
        assert _conflicts(svc, RNS, "r2") == []


def test_t2_the_same_radio_still_blocks(tmp_path):
    with _box(tmp_path, (*DAEMON, "r2", "868")) as svc:
        assert _conflicts(svc, RNS, "r2") == [_vs_daemon("r2")]


def test_t3_a_daemon_no_record_and_no_id_tells_still_holds_the_whole_band(tmp_path):
    with _box(tmp_path, (*DAEMON, "r4", "868", False, ["loraham_daemon", "--radio", "868"])) as svc:
        assert _conflicts(svc, RNS, "r2") == [_vs_daemon("<rid>")]


def test_t4_a_start_without_an_instance_is_never_relaxed(tmp_path):
    with _box(tmp_path, (*DAEMON, "r4", "868")) as svc:
        assert _conflicts(svc, RNS, "") == [_vs_daemon("r4")]


def test_t5_a_holder_on_another_radio_keeps_its_non_radio_claims(tmp_path):
    # a real shared claim: meshtasticd and the Reticulum node both drive SPI without the daemon's lock
    # (spi.bus.0.unlocked, exclusive); meshtastic@r1 (433) is told on r1, rns@r2 is still blocked by it
    with _box(tmp_path, (*MESHTASTIC, "r1", "433")) as svc:
        assert _conflicts(svc, RNS, "r2") == [
            "meshtastic (exclusive) vs rns (exclusive) on spi.bus.0.unlocked — both running; stop meshtastic@r1"]


def test_t6_meshtastic_on_r2_beside_the_daemon_on_r4(tmp_path):
    # red-before (a63276de): blocked on loraham.radio.868 like rns
    with _box(tmp_path, (*DAEMON, "r4", "868")) as svc:
        assert _conflicts(svc, MESHTASTIC, "r2") == []


def test_n2_the_daemon_on_r4_starts_beside_a_running_rns_on_r2(tmp_path):
    # the reverse direction: rns is told by its launch record (instance_processes), not by a daemon argv.
    # red-before (a63276de): blocked on loraham.radio.868
    with _box(tmp_path, (*RNS, "r2", "868")) as svc:
        assert _conflicts(svc, DAEMON, "r4") == []


def test_n2_the_daemon_on_the_same_radio_as_a_running_rns_is_still_blocked(tmp_path):
    with _box(tmp_path, (*RNS, "r2", "868")) as svc:
        assert _conflicts(svc, DAEMON, "r2") == [
            "rns (exclusive) vs loraham-daemon (provider) on loraham.radio.868 (radios r2, r4) — both running; "
            "stop reticulum@r2 or use another radio"]
