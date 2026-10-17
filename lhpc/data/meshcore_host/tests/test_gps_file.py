"""LHPC's position for openHop's own GPS file source."""
from __future__ import annotations

import asyncio
import calendar
import time

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


def test_the_file_is_rewritten_with_the_current_time(tmp_path, monkeypatch):
    monkeypatch.setattr("meshcore_host.gps_file.PERIOD_S", 0.01)
    f = NmeaFile(str(tmp_path / "gps.nmea"))
    f.set_position(50.5, 8.0)
    stamps = iter([1000.0, 1003.0, 1006.0])
    monkeypatch.setattr(time, "time", lambda: next(stamps, 1006.0))

    async def run():
        f.start()
        await asyncio.sleep(0.05)
        await f.stop()
    asyncio.run(run())
    assert f.path.read_text() == ""                 # stop clears the position
