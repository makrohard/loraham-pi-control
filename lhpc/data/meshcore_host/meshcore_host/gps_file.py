"""LHPC's position handed to openHop's own GPS service, the upstream way: openHop's file source.

With `use_gps` on, openHop's `gps` section is LHPC's: `source: file`, polling this file. One
writer serves every LHPC GPS mode: a live fix from the meshcore-gps bridge, or the fixed
coordinates. While LHPC has a position it rewrites an RMC + GGA pair with the current UTC time every
2 s (upstream ingests only changed content and calls a fix stale after 10 s); with no position the
file is empty, and upstream falls back to `repeater.latitude/longitude`. The file is on the runtime
tmpfs, never the SD card. Time sync stays off: the clock is LHPC's.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("meshcore-host.gps-file")

PERIOD_S = 2.0


def _checksum(payload: str) -> str:
    value = 0
    for char in payload:
        value ^= ord(char)
    return f"{value:02X}"


def _angle(value: float, degree_digits: int, hemis: tuple[str, str]) -> tuple[str, str]:
    hemi = hemis[0] if value >= 0 else hemis[1]
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60.0
    return f"{degrees:0{degree_digits}d}{minutes:07.4f}", hemi


def sentences(lat: float, lon: float, now: Optional[float] = None) -> str:
    t = time.gmtime(time.time() if now is None else now)
    hms = time.strftime("%H%M%S", t) + ".00"
    dmy = time.strftime("%d%m%y", t)
    la, ns = _angle(lat, 2, ("N", "S"))
    lo, ew = _angle(lon, 3, ("E", "W"))
    rmc = f"GPRMC,{hms},A,{la},{ns},{lo},{ew},0.0,0.0,{dmy},,,A"
    gga = f"GPGGA,{hms},{la},{ns},{lo},{ew},1,08,1.0,0.0,M,0.0,M,,"
    return f"${rmc}*{_checksum(rmc)}\n${gga}*{_checksum(gga)}\n"


class NmeaFile:
    def __init__(self, path: str):
        self.path = Path(path)
        self._pos: Optional[tuple[float, float]] = None
        self._task: Optional[asyncio.Task] = None

    def set_position(self, lat: Optional[float], lon: Optional[float]) -> None:
        self._pos = None if lat is None or lon is None else (lat, lon)

    def write(self) -> None:
        text = "" if self._pos is None else sentences(*self._pos)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="ascii")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("GPS file not written (%s)", exc)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="gps file")

    async def _run(self) -> None:
        while True:
            self.write()
            await asyncio.sleep(PERIOD_S)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._pos = None
        self.write()
