"""Tests for Unix-socket existence and the bounded daemon GET STATUS probe.

The fake daemon CONF server is modelled by FakeSystem.unix_replies /
unix_errors, exercising valid status, connection failure (timeout), malformed
output and oversize output.
"""

from __future__ import annotations

from lhpc.core.probes.backends import FakeSystem
from lhpc.core.probes.unixsock import probe_daemon_status, probe_socket

_VALID = (
    b"STATUS RADIO=READY TX=0 CAD=0 GETRSSI=0 TXRESULT=0 TXMODE=DIRECT TXQUEUE=1 "
    b"CADWAIT=1500 CADIDLE=250 CADPOLL=50 CADTXAFTERTIMEOUT=0 CADMONITOR=0 CADRSSI=-90\n"
)


def test_socket_existence_and_type():
    fake = FakeSystem(sockets={"/tmp/loraconf433.sock"}, paths={"/tmp/plain"})
    assert probe_socket(fake.system, "/tmp/loraconf433.sock").is_socket
    # exists but is not a socket
    p = probe_socket(fake.system, "/tmp/plain")
    assert p.exists and not p.is_socket
    # absent
    assert not probe_socket(fake.system, "/tmp/none").exists


def test_daemon_status_valid_ready():
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _VALID})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert ds.reachable and ds.ready
    assert ds.radio == "READY" and ds.tx_mode == "DIRECT"
    assert ds.fields["CADWAIT"] == "1500"


def test_daemon_status_radio_failed_is_not_ready():
    reply = b"STATUS RADIO=FAILED TX=0 TXMODE=MANAGED\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf868.sock": reply})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf868.sock")
    assert ds.reachable and not ds.ready and ds.radio == "FAILED"


def test_daemon_status_connection_failure():
    fake = FakeSystem(unix_errors={"/tmp/loraconf433.sock": "timed out"})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert not ds.reachable and not ds.ready
    assert "timed out" in ds.evidence["error"]


def test_daemon_status_malformed_response():
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": b"garbage not a status\n"})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert not ds.reachable and not ds.ready
    assert "malformed" in ds.evidence["error"]


def test_daemon_status_empty_response():
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": b""})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert not ds.reachable and "empty" in ds.evidence["error"]


def test_daemon_status_oversize_is_bounded_and_parsed_or_safe():
    # An oversize first line (no newline within the cap) must not hang or crash;
    # it is read up to the cap and parsed defensively.
    huge = b"STATUS RADIO=READY " + b"PAD=x " * 5000 + b"\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": huge})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    # We only require: no exception, reachable parse, and RADIO captured.
    assert ds.reachable and ds.radio == "READY"
