"""Fixtures for the stack suites.

`fake_gpsd` lives here rather than in the root conftest because only the GPS stack tests need it,
and a fixture is easier to understand next to its subject. It is a fixture rather than a shared
helper module so no test file has to import another.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest


class _FakeGpsd:
    """A minimal gpsd: answers ?DEVICES with a device list and streams NMEA after ?WATCH.

    `json_lines` serves a JSON `?WATCH` response instead of NMEA, to each connection in turn (read
    when it connects, so a test may change it between two polls):
    pass raw byte chunks, so a test can split one TPV across recv boundaries, send junk, or
    send a TPV with no fix. `silent=True` accepts the connection and says nothing, for the
    timeout path.
    """

    def __init__(self, devices=(), sentences=(), json_lines=None, silent=False):
        self.devices = list(devices)
        self.sentences = list(sentences)
        self.json_lines = list(json_lines) if json_lines is not None else None
        self.silent = silent
        self.connections = 0
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                self._srv.settimeout(0.3)
                conn, _ = self._srv.accept()
            except (TimeoutError, OSError):
                continue
            self.connections += 1
            with conn:
                conn.settimeout(1.0)
                try:
                    req = conn.recv(4096)
                except (TimeoutError, OSError):
                    req = b""
                if b"?DEVICES" in req:
                    conn.sendall(json.dumps(
                        {"class": "DEVICES",
                         "devices": [{"path": p} for p in self.devices]}).encode() + b"\n")
                    continue
                if self.silent:
                    # Connected but mute — the caller must hit its own total deadline.
                    while not self._stop.wait(0.05):
                        pass
                    return
                if self.json_lines is not None:
                    for chunk in self.json_lines:
                        if isinstance(chunk, float):     # a pause in the stream, in seconds
                            time.sleep(chunk)    # wallclock: a scripted pause, the scenario's own timing
                            continue
                        try:
                            conn.sendall(chunk)
                        except OSError:
                            return
                        time.sleep(0.01)    # wallclock: the fake's line pacing, as gpsd streams
                    self._hold_until_closed(conn)    # then serve the next connection
                    continue
                while not self._stop.is_set():
                    for s in self.sentences:
                        try:
                            conn.sendall(s.encode() + b"\r\n")
                        except OSError:
                            return
                    time.sleep(0.05)    # wallclock: the fake's NMEA pacing between rounds

    def _hold_until_closed(self, conn):
        """Keep the connection open, as gpsd does, until the client closes it or the fake stops."""
        while not self._stop.is_set():
            try:
                if not conn.recv(4096):
                    return
            except TimeoutError:
                continue
            except OSError:
                return

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


class _PreloadedGpsd:
    """gpsd as a transport that already holds its whole answer. `gps._gpsd_connect` is replaced
    by `connect`, which hands out one end of a socketpair whose other end has the script written
    before the read starts: the first recv returns data at once, whatever the scheduler does, and
    the read still ends at its budget, as in production. Connection i gets scripts[i] (the last one
    for any later connection); `connects` counts them, and a SKY wait is a second connection. The
    far ends stay open until the test ends, as gpsd keeps a watch open."""

    port = 2947

    def __init__(self, scripts):
        self.scripts = scripts
        self.connects = 0
        self._far = []

    def connect(self, host, port, deadline):
        script = self.scripts[min(self.connects, len(self.scripts) - 1)]
        self.connects += 1
        near, far = socket.socketpair()
        far.sendall(b"".join(script))
        self._far.append(far)
        return near, ""

    def close(self):
        for far in self._far:
            far.close()


@pytest.fixture
def preloaded_gpsd(monkeypatch):
    """Factory for a `_PreloadedGpsd`: `preloaded_gpsd(script, ...)` patches the snapshot's
    transport for the rest of the test; every far end is closed at teardown."""
    from lhpc.core import gps
    made: list = []

    def _make(*scripts):
        srv = _PreloadedGpsd(scripts)
        made.append(srv)
        monkeypatch.setattr(gps, "_gpsd_connect", srv.connect)
        return srv

    yield _make
    for srv in made:
        srv.close()


@pytest.fixture(autouse=True)
def _no_satellite_memo(monkeypatch):
    """Isolation: `gps.gpsd_snapshot` remembers each gpsd device's last satellite list in the
    process; every test starts and ends without one. Its wait for a satellite SKY is off here, so
    a read ends at its budget; the tests of that wait set `SKY_WAIT_S` themselves."""
    from lhpc.core import gps
    monkeypatch.setattr(gps, "SKY_WAIT_S", 0.0)
    gps._SKY_MEMO.clear()
    yield
    gps._SKY_MEMO.clear()


@pytest.fixture
def fake_gpsd():
    """Factory for a minimal in-process gpsd. Every server it hands out is closed at teardown,
    so a failing assertion cannot leak the accept thread into the rest of the session."""
    made: list = []

    def _make(**kw):
        srv = _FakeGpsd(**kw)
        made.append(srv)
        return srv

    yield _make
    for srv in made:
        srv.close()
