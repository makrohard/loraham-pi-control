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

    `json_lines` serves a JSON `?WATCH` response instead of NMEA:
    pass raw byte chunks, so a test can split one TPV across recv boundaries, send junk, or
    send a TPV with no fix. `silent=True` accepts the connection and says nothing, for the
    timeout path.
    """

    def __init__(self, devices=(), sentences=(), close_after=None,
                 json_lines=None, silent=False):
        self.devices = list(devices)
        self.sentences = list(sentences)
        self.close_after = close_after
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
                        try:
                            conn.sendall(chunk)
                        except OSError:
                            return
                        time.sleep(0.01)
                    while not self._stop.wait(0.05):
                        pass
                    return
                sent = 0
                while not self._stop.is_set():
                    for s in self.sentences:
                        try:
                            conn.sendall(s.encode() + b"\r\n")
                        except OSError:
                            return
                        sent += 1
                        if self.close_after and sent >= self.close_after:
                            return
                    time.sleep(0.05)

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


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
