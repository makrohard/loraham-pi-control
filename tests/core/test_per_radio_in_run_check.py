"""Q6c (i), both start checks use the per-radio rule: the check inside the run (_running_conflicts) takes the
pre-start rule (run_blockers, instances.elsewhere): a holder told on ANOTHER radio of the band does not block, the
same radio does, and a holder no record tells still holds the whole band. Box H: r1 loraham 433 (SPI), r2 loraham
868 (SPI), r3 CH341-RFM98 433 and r4 PineDio 868 (USB). Holders run as real sleep processes under the argv their
spec matches, each with its verified launch record unless said otherwise."""
from __future__ import annotations

import contextlib
import subprocess
import sys

import radios_fixture

from lhpc.core import instances, radios
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
def _box(tmp_path, *holders, rows=radios_fixture.BOX_H):
    """A Box H service (`rows`) with `holders` running: (stack, component, rid, band[, record=True[, argv]]). Reticulum
    runs its direct-SPI node here (`backend = spi` on every radio, B1): the claims these checks judge."""
    radios_fixture.write(tmp_path, rows)
    radios_fixture.reticulum_backend(tmp_path, [r.id for r in rows])
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


# ---- step 2, the status per radio too (Q9): observed_conflicts, every UI's one source ------------------------------

def _radio_conflicts(svc):
    return sorted((c.resource_key, *sorted(c.holders)) for c in svc.observed_conflicts()
                  if c.resource_key.startswith("loraham.radio."))


def test_t7_the_status_shows_no_conflict_for_rns_on_r2_beside_the_daemon_on_r4(tmp_path):
    # red-before (c4ec4b38): a red "loraham.radio.868 (radios r2, r4)" conflict for a state both checks admit
    with _box(tmp_path, (*RNS, "r2", "868"), (*DAEMON, "r4", "868")) as svc:
        assert _radio_conflicts(svc) == []


def test_t7_the_status_still_shows_the_same_radio(tmp_path):
    with _box(tmp_path, (*RNS, "r2", "868"), (*DAEMON, "r2", "868")) as svc:
        assert _radio_conflicts(svc) == [("loraham.radio.868 (radios r2, r4)", "loraham-daemon", "rns")]


def test_t7_the_status_still_shows_a_holder_no_record_and_no_id_tells(tmp_path):
    with _box(tmp_path, (*RNS, "r2", "868"), (*DAEMON, "r4", "868", False, ["loraham_daemon", "--radio", "868"])) as svc:
        assert _radio_conflicts(svc) == [("loraham.radio.868 (radios r2, r4)", "loraham-daemon", "rns")]


def test_t7_the_status_still_shows_a_shared_non_radio_claim_of_holders_on_two_radios(tmp_path):
    with _box(tmp_path, (*MESHTASTIC, "r1", "433"), (*RNS, "r2", "868")) as svc:
        assert sorted((c.resource_key, *sorted(c.holders)) for c in svc.observed_conflicts()
                      if c.resource_key == "spi.bus.0.unlocked") == [("spi.bus.0.unlocked", "meshtastic", "rns")]


def test_a_holder_with_a_process_no_instance_claims_holds_the_whole_band(tmp_path):
    # elsewhere's fail-safe, kept by the shared _told: rns@r2 is told by its record, but a second rns process no record
    # or token claims runs too, so rns is not told on r2 alone and daemon@r4 is blocked (the status reads the same _told)
    with _box(tmp_path, (*RNS, "r2", "868"), (*RNS, "r2", "868", False)) as svc:
        assert _conflicts(svc, DAEMON, "r4") == [
            "rns (exclusive) vs loraham-daemon (provider) on loraham.radio.868 (radios r2, r4) — both running; "
            "stop reticulum@r2 or use another radio"]


# ---- box H as it is (11-21): r1-r5, r5 an ELECROW Meshstick 868 on USB -----------------------------------------------

BOX_H_NOW = (*radios_fixture.BOX_H, radios.Radio("r5", "mstick 868", "meshstick-1262", "868", usb_serial="MSTK0868"))


def test_box_h_meshtastic_on_r2_starts_beside_the_daemons_on_r4_and_r5(tmp_path):
    # box H 11-21 22:18Z: "[blocked] meshtastic: resource conflict" while daemon@r4 (868) ran; red-before (a63276de)
    with _box(tmp_path, (*DAEMON, "r4", "868"), (*DAEMON, "r5", "868"), rows=BOX_H_NOW) as svc:
        assert _conflicts(svc, MESHTASTIC, "r2") == []


def test_box_h_meshtastic_on_r2_beside_a_daemon_on_r2_is_blocked_with_its_way_out(tmp_path):
    with _box(tmp_path, (*DAEMON, "r2", "868"), (*DAEMON, "r4", "868"), (*DAEMON, "r5", "868"), rows=BOX_H_NOW) as svc:
        assert _conflicts(svc, MESHTASTIC, "r2") == [
            "loraham-daemon (provider) vs meshtastic (exclusive) on loraham.radio.868 (radios r2, r4, r5) — both "
            "running; stop daemon@r2 or use another radio"]


def test_a_recorded_daemon_beside_one_without_id_holds_the_whole_band(tmp_path):
    # G2-153 N1: daemon@r4 runs by its record, a hand-started daemon without --id runs too: the daemon is not told on
    # r4 alone, so rns@r2 is blocked and the status shows the conflict. red-before (85f62b1d): admitted, none shown
    hand = (*DAEMON, "r2", "868", False, ["loraham_daemon", "--radio", "868"])
    with _box(tmp_path, (*DAEMON, "r4", "868"), hand) as svc:
        got = [c.split("; ")[0] for c in _conflicts(svc, RNS, "r2")]           # the sentence; its way out is N2's limit
        assert got == ["loraham-daemon (provider) vs rns (exclusive) on loraham.radio.868 (radios r2, r4) — both running"]
    with _box(tmp_path, (*DAEMON, "r4", "868"), hand, (*RNS, "r2", "868")) as svc:
        assert _radio_conflicts(svc) == [("loraham.radio.868 (radios r2, r4)", "loraham-daemon", "rns")]
