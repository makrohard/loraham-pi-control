"""LHPC's position for openHop's own GPS file source."""
from __future__ import annotations

import asyncio
import calendar
import time

import pytest

from meshcore_host.gps_file import NmeaFile, sentences


def _xor(body: str) -> str:
    """NMEA's checksum, computed here independently of the module under test."""
    value = 0
    for ch in body:
        value ^= ord(ch)
    return f"{value:02X}"


def _valid(line: str) -> bool:
    body, _, cs = line[1:].partition("*")
    return _xor(body) == cs


def test_rmc_and_gga_carry_the_position_and_valid_checksums():
    now = calendar.timegm((2026, 10, 17, 12, 34, 56, 0, 0, 0))
    rmc, gga = sentences(50.5, -7.25, now).splitlines()
    rmc_body = "GPRMC,123456.00,A,5030.0000,N,00715.0000,W,0.0,0.0,171026,,,A"
    gga_body = "GPGGA,123456.00,5030.0000,N,00715.0000,W,1,08,1.0,0.0,M,0.0,M,,"
    assert (rmc, gga) == (f"${rmc_body}*{_xor(rmc_body)}", f"${gga_body}*{_xor(gga_body)}")
    assert _valid(rmc) and _valid(gga)


def test_no_position_writes_an_empty_file(tmp_path):
    f = NmeaFile(str(tmp_path / "run" / "gps.nmea"))
    f.set_position(50.5, 8.0)
    f.write()
    assert "$GPRMC" in f.path.read_text()
    f.set_position(None, None)
    f.write()
    assert f.path.read_text() == ""


class _Stop(Exception):
    pass


def test_the_file_is_rewritten_every_period_with_the_current_time(tmp_path, monkeypatch):
    """Two writes, observed by an injected wait (no wall clock): each period rewrites the file and
    its sentences carry the time of that write."""
    stamps = iter([calendar.timegm((2026, 10, 17, 12, 0, s, 0, 0, 0)) for s in (0, 2)])
    monkeypatch.setattr(time, "time", lambda: next(stamps))
    seen = []

    async def wait(period):
        seen.append((period, f.path.read_text().split(",")[1]))
        if len(seen) == 2:
            raise _Stop
    f = NmeaFile(str(tmp_path / "gps.nmea"), wait=wait)
    f.set_position(50.5, 8.0)
    with pytest.raises(_Stop):
        asyncio.run(f._run())
    assert seen == [(2.0, "120000.00"), (2.0, "120002.00")]


STEP_S = 5.0                                       # a named bound for the start and for the stop


def test_stop_ends_the_loop_and_empties_the_file(tmp_path):
    """gate 2 r2a F2: the original task is held and checked inside the loop right after stop(), so
    the runner's own cancellation at shutdown cannot stand in for stop()."""
    async def run():
        waiting = asyncio.Event()

        async def wait(_period):
            waiting.set()
            await asyncio.Event().wait()           # held until stop() cancels it
        f = NmeaFile(str(tmp_path / "gps.nmea"), wait=wait)
        f.set_position(50.5, 8.0)
        f.start()
        task = f._task
        try:
            await asyncio.wait_for(waiting.wait(), STEP_S)     # the first write happened (an event)
            assert "$GPRMC" in f.path.read_text()
            await asyncio.wait_for(f.stop(), STEP_S)
            assert task.done() and f._task is None and f.path.read_text() == ""
        finally:
            if not task.done():
                task.cancel()
    asyncio.run(run())
