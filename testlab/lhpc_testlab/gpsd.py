"""The fake gpsd: a REAL listener on 127.0.0.1:2947 streaming checksum-valid NMEA, so
the `auto` GPS source's /proc/net/tcp probe and the GPS bridge's `?WATCH` + raw-NMEA
client find an honest peer. Scenario-polled (~1 s): while `gpsd` is off the listener is
CLOSED (the port truly disappears), and it reopens on recovery. Run detached via the
hidden CLI verb `_testlab-gpsd`.
"""
from __future__ import annotations

import os
import select
import socket
import time

from lhpc.core import runtime_fs

from . import scenarios

PORT = 2947
_BASE_LAT, _BASE_LON, _ALT = 48.4303, 11.6682, 482.7


def _nmea(sentence: str) -> bytes:
    csum = 0
    for ch in sentence:
        csum ^= ord(ch)
    return f"${sentence}*{csum:02X}\r\n".encode()


def _fix_lines(tick: int) -> bytes:
    """A slowly circling fix; GGA + RMC per second (the bridge needs navigation
    sentences with populated coordinates, not just an open port)."""
    import math
    lat = _BASE_LAT + 0.0005 * math.sin(tick / 30.0)
    lon = _BASE_LON + 0.0005 * math.cos(tick / 30.0)
    t = time.strftime("%H%M%S", time.gmtime())
    d = time.strftime("%d%m%y", time.gmtime())

    def dm(value: float, width: int) -> str:
        deg = int(abs(value))
        minutes = (abs(value) - deg) * 60.0
        return f"{deg:0{width}d}{minutes:07.4f}"
    lats, lons = dm(lat, 2), dm(lon, 3)
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    gga = (f"GPGGA,{t},{lats},{ns},{lons},{ew},1,08,1.1,{_ALT:.1f},M,47.0,M,,")
    rmc = (f"GPRMC,{t},A,{lats},{ns},{lons},{ew},0.1,157.4,{d},3.4,E,A")
    return _nmea(gga) + _nmea(rmc)


def pid_path(paths):
    return paths.under("state", "testlab", "gpsd.pid")


def main(paths) -> int:
    runtime_fs.atomic_write(paths, pid_path(paths), f"{os.getpid()}\n", 0o600)
    server: socket.socket | None = None
    clients: list[socket.socket] = []
    tick = 0
    while True:
        want = bool(scenarios.effective_state(paths).get("gpsd", True))
        if want and server is None:
            try:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("127.0.0.1", PORT))
                server.listen(4)
                server.setblocking(False)
            except OSError:
                server = None                       # port busy — retry next tick
        if not want and server is not None:
            for c in clients:
                c.close()
            clients.clear()
            server.close()
            server = None
        if server is not None:
            readable, _, _ = select.select([server, *clients], [], [], 1.0)
            for sock in readable:
                if sock is server:
                    try:
                        conn, _addr = server.accept()
                        conn.setblocking(False)
                        clients.append(conn)
                    except OSError:
                        pass
                else:
                    try:
                        if sock.recv(4096) == b"":  # ?WATCH etc. read + ignored
                            raise OSError
                    except BlockingIOError:
                        pass
                    except OSError:
                        clients.remove(sock)
                        sock.close()
            payload = _fix_lines(tick)
            for c in list(clients):
                try:
                    c.sendall(payload)
                except OSError:
                    clients.remove(c)
                    c.close()
        else:
            time.sleep(1.0)
        tick += 1
