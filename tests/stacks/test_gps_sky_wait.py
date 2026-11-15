"""The GPS Monitor's satellite list against gpsd's real cadence (field row D2).

gpsd sends a SKY every second but carries the satellite list in only every fifth one (5.0 s,
measured on the field box; the others are DOP-only). The console reads 2.5 s, then polls again 3 s
after the answer; that cycle drifts against the 5 s period and missed the list for runs of up to
~27 s, past the 20 s the memo then covered (5 of 20 polls empty on the box). A read without a list
now waits for the next one, bounded, and a list is remembered for 30 s.
"""

import json
import socket
import threading
import time
import types

import pytest

from lhpc.core import gps as _gps

VERSION = b'{"class":"VERSION","release":"3.25","proto_major":3,"proto_minor":15}\n'
DEV_A = b'{"class":"DEVICES","devices":[{"path":"/dev/ttyACM0","driver":"u-blox"}]}\n'


def _line(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


A, B = "/dev/ttyACM0", "/dev/ttyACM1"
DEV_AB = _line({"class": "DEVICES", "devices": [{"path": A}, {"path": B}]})


def _tpv(device=A) -> bytes:
    return _line({"class": "TPV", "device": device, "mode": 3, "lat": 51.5, "lon": -0.12})


def _dop_sky(device=A) -> bytes:
    return _line({"class": "SKY", "device": device, "hdop": 0.9, "uSat": 27})


def _sky(*prns, device=A) -> bytes:
    return _line({"class": "SKY", "device": device, "nSat": len(prns), "uSat": len(prns),
                  "satellites": [{"PRN": p, "el": 30, "az": 90, "ss": 30, "used": True}
                                 for p in prns]})


# --- the bounded wait ----------------------------------------------------------------------------
# The wait is a second connection to gpsd. `preloaded_gpsd` hands each connection its script already
# written, so the budget's read never races a fake's thread; whether a read waited is its connection
# count: one connection is no wait, two are the budget's read and the wait.

def test_a_list_arriving_after_the_budget_is_waited_for(preloaded_gpsd, monkeypatch):
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
    srv = preloaded_gpsd([VERSION, DEV_A, _tpv(), _dop_sky()],
                         [VERSION, DEV_A, _tpv(), _dop_sky(), _sky(3, 7), _tpv()])
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert r["state"] == "3d" and [s["prn"] for s in r["satellites"]] == [3, 7]
    assert r["sats_seen"] == 2 and srv.connects == 2


def test_the_wait_takes_the_whole_burst_of_a_list(preloaded_gpsd, monkeypatch):
    # gpsd sends one list as several SKYs in a row, each with more satellites (16, 26, 29, 46, 50
    # on the field box); the wait ends with the message after the burst, not at its first SKY.
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
    srv = preloaded_gpsd([VERSION, DEV_A, _tpv(), _dop_sky()],
                         [VERSION, DEV_A, _tpv(), _dop_sky(),
                          _sky(3), _sky(3, 7), _sky(3, 7, 9), _tpv()])
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert [s["prn"] for s in r["satellites"]] == [3, 7, 9] and r["sats_seen"] == 3
    assert srv.connects == 2


@pytest.mark.wallclock("the wait's real end at SKY_WAIT_S is what is measured")
def test_without_a_list_in_the_wait_the_read_ends_at_the_bound(preloaded_gpsd, monkeypatch):
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 0.8)
    srv = preloaded_gpsd([VERSION, DEV_A, _tpv(), _dop_sky()])
    t0 = time.monotonic()
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    took = time.monotonic() - t0
    assert r["state"] == "3d" and r["satellites"] == [] and r["sats_seen"] is None
    assert srv.connects == 2 and 0.7 <= took < 1.5


def test_a_remembered_list_ends_the_read_at_its_budget(preloaded_gpsd, monkeypatch):
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 3.0)
    srv = preloaded_gpsd([VERSION, DEV_A, _tpv(), _sky(5)],
                         [VERSION, DEV_A, _tpv(), _dop_sky()])
    _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert srv.connects == 1
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert srv.connects == 2 and [s["prn"] for s in r["satellites"]] == [5]      # no wait


# Two receivers on one gpsd: only the device whose position the snapshot shows decides the wait.

@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
def test_a_receiver_without_satellite_reports_waits_once_per_memo_window(preloaded_gpsd,
                                                                         fake_clock, monkeypatch):
    # NMEA without GSV: positions and DOP-only SKYs, never a list. Waiting on every read would
    # slow every poll by the whole wait; one wait per memo window finds out, then reads are short.
    # The memo's age is on the module's clock, here the fake one; the reads still end at their
    # budgets in real time.
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 0.8)
    monkeypatch.setattr(_gps, "SKY_MEMO_S", 2.5)
    monkeypatch.setattr(_gps, "time", types.SimpleNamespace(monotonic=fake_clock.monotonic))
    srv = preloaded_gpsd([VERSION, DEV_A, _tpv(), _dop_sky()])

    def read():
        before = srv.connects
        r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
        assert r["state"] == "3d" and r["satellites"] == [] and r["sats_seen"] is None
        return srv.connects - before

    assert read() == 2                              # the one wait
    assert read() == 1                              # stamped: no wait
    fake_clock.advance(2.6)
    assert read() == 2                              # the stamp expired: one wait again


def test_another_devices_fresh_list_does_not_skip_the_wait(preloaded_gpsd, monkeypatch):
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
    srv = preloaded_gpsd([VERSION, DEV_AB, _tpv(A), _sky(3, 7, device=A), _tpv(A)],   # A remembered
                         [VERSION, DEV_AB, _tpv(B), _dop_sky(B)],
                         [VERSION, DEV_AB, _tpv(B), _dop_sky(B), _sky(5, device=B), _tpv(B)])
    first = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert first["device"] == A and [s["prn"] for s in first["satellites"]] == [3, 7]
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert r["device"] == B and [s["prn"] for s in r["satellites"]] == [5]
    assert srv.connects == 3


def test_another_devices_list_does_not_end_the_wait(preloaded_gpsd, monkeypatch):
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
    srv = preloaded_gpsd([VERSION, DEV_AB, _tpv(B), _dop_sky(B)],
                         [VERSION, DEV_AB, _tpv(B), _sky(3, device=A), _tpv(B),
                          _sky(5, device=B), _tpv(B)])
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert r["device"] == B and [s["prn"] for s in r["satellites"]] == [5]
    assert srv.connects == 2


def test_no_wait_without_a_position(preloaded_gpsd, monkeypatch):
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 3.0)
    srv = preloaded_gpsd([VERSION, DEV_A, _dop_sky()])
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
    assert r["state"] == "gpsd-no-data" and srv.connects == 1


def test_the_memo_covers_the_measured_worst_miss(monkeypatch):
    # ~27 s measured (a 5.5 s read cycle against the 5.0 s list period); the wait covers a period.
    monkeypatch.undo()              # the shipped values, not the isolation fixture's
    assert _gps.SKY_MEMO_S >= 30.0 and _gps.SKY_WAIT_S >= 6.0


# --- the field cadence, scaled -------------------------------------------------------------------

class _CadenceGpsd:
    """gpsd at the measured cadence on one shared clock: every `period` a TPV and a DOP-only SKY,
    every fifth period the satellite SKY. The clock runs independently of client connections, as
    gpsd's receiver does."""

    def __init__(self, period):
        self.period = period
        self.t0 = time.monotonic()    # wallclock: the receiver's own clock, as gpsd's runs
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop.is_set():
            try:
                self._srv.settimeout(0.2)
                conn, _ = self._srv.accept()
            except OSError:
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            try:
                conn.settimeout(1.0)
                conn.recv(4096)
                conn.sendall(VERSION + DEV_A)
                k = int((time.monotonic() - self.t0) / self.period) + 1    # wallclock: the cadence
                while not self._stop.is_set():
                    delay = self.t0 + k * self.period - time.monotonic()    # wallclock: the cadence
                    if delay > 0:
                        time.sleep(delay)    # wallclock: the cadence's next period
                    conn.sendall(_tpv() + _dop_sky() + (_sky(1, 2, 3) if k % 5 == 0 else b""))
                    k += 1
            except OSError:
                return

    def close(self):
        self._stop.set()
        self._srv.close()


@pytest.mark.slow                   # ~25 s of real time: the field cadence at a fifth of its speed
@pytest.mark.wallclock("the field cadence on a real clock: the poll cycle drifts against the list period")
def test_the_console_poll_never_shows_an_empty_sky_at_the_field_cadence(monkeypatch):
    scale = 0.2                     # every duration of the field row, times this
    monkeypatch.setattr(_gps, "SKY_WAIT_S", 6.0 * scale)
    monkeypatch.setattr(_gps, "SKY_MEMO_S", 30.0 * scale)
    srv = _CadenceGpsd(period=1.0 * scale)
    try:
        counts = []
        for _ in range(20):        # the row: 20 polls, each 3 s after the previous answer
            r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=2.5 * scale)
            counts.append(len(r["satellites"]))
            time.sleep(3.0 * scale)
    finally:
        srv.close()
    assert counts.count(0) == 0, counts
