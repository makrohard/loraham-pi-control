"""Simulated GPS receiver for the demo's Monitor: a few seconds of NMEA from a receiver with a 3D
fix. The demo feeds these sentences through the product's own parser (`gps.NmeaSnapshot`), so the
Monitor's shape can never drift from what a real receiver produces — the same approach as the
simulated /proc text behind the System box (system_sim)."""
from __future__ import annotations

import time

# A fixed position (Greenwich) and a plausible sky: (prn, elevation, azimuth, SNR, used in the fix).
LAT, LON, ALT_M = 51.477812, -0.001545, 45.0
SATS = ((2, 67, 45, 42, True), (5, 41, 120, 38, True), (12, 23, 200, 33, True),
        (15, 55, 300, 40, True), (18, 12, 80, 25, False), (24, 34, 250, 36, True),
        (25, 70, 170, 44, True), (29, 8, 10, None, False), (31, 48, 330, 39, True))


def _sentence(body: str) -> bytes:
    cs = 0
    for ch in body.encode("ascii"):
        cs ^= ch
    return f"${body}*{cs:02X}".encode("ascii")


def _coord(value: float, is_lat: bool) -> tuple[str, str]:
    hemi = ("N" if value >= 0 else "S") if is_lat else ("E" if value >= 0 else "W")
    value = abs(value)
    deg = int(value)
    minutes = (value - deg) * 60
    return (f"{deg:02d}{minutes:07.4f}" if is_lat else f"{deg:03d}{minutes:07.4f}"), hemi


def sentences(now: float | None = None) -> list[bytes]:
    """One epoch of NMEA for the simulated receiver at `now` (UTC)."""
    t = time.gmtime(time.time() if now is None else now)
    hms, dmy = time.strftime("%H%M%S", t), time.strftime("%d%m%y", t)
    lat, ns = _coord(LAT, True)
    lon, ew = _coord(LON, False)
    used = [s[0] for s in SATS if s[4]]
    out = [_sentence(f"GPRMC,{hms}.00,A,{lat},{ns},{lon},{ew},0.0,0.0,{dmy},,,A"),
           _sentence(f"GPGGA,{hms}.00,{lat},{ns},{lon},{ew},1,{len(used):02d},0.9,{ALT_M:.1f},M,47.0,M,,"),
           _sentence("GPGSA,A,3," + ",".join([f"{p:02d}" for p in used] + [""] * (12 - len(used)))
                     + ",1.6,0.9,1.3")]
    groups = [SATS[i:i + 4] for i in range(0, len(SATS), 4)]
    for n, grp in enumerate(groups, 1):
        fields = []
        for prn, el, az, snr, _used in grp:
            fields += [f"{prn:02d}", f"{el:02d}", f"{az:03d}", "" if snr is None else f"{snr:02d}"]
        out.append(_sentence(f"GPGSV,{len(groups)},{n},{len(SATS):02d}," + ",".join(fields)))
    return out
