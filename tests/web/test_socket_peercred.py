"""The real Unix client must authenticate the peer of a compatibility /tmp socket via SO_PEERCRED
before sending anything, so a local squatter cannot impersonate the daemon or receive controller
payloads. /run/loraham sockets (dedicated daemon UID + dir/group model) are exempt.

The echo-server helper thread is JOINED (never a leaked daemon thread) and any UNEXPECTED thread
exception is propagated into the test — a socket close/accept race can otherwise surface later as an
`OSError: Bad file descriptor` in an orphaned thread (an unhandled-thread warning). Needs Linux
SO_PEERCRED + AF_UNIX; skipped elsewhere.
"""

import contextlib
import os
import socket
import tempfile
import threading

import pytest

from lhpc.core.probes import backends

pytestmark = pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"),
                                reason="SO_PEERCRED (Linux) required")


@contextlib.contextmanager
def _echo_server():
    """A same-process AF_UNIX echo server under /tmp — its peer UID is our own euid. The serving
    thread is JOINED on exit and any unexpected exception it raised is re-raised here (propagated),
    so no thread is ever leaked and no failure is silently swallowed."""
    d = tempfile.mkdtemp(dir="/tmp")
    path = os.path.join(d, "d.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    srv.settimeout(5)                       # accept can never block the join forever
    errs = []

    def serve():
        try:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(5)
                try:
                    conn.recv(64)
                    conn.sendall(b"OK\n")
                except OSError:
                    pass                    # client may close before our reply (foreign-uid tests)
        except OSError:
            pass                            # accept timed out / srv closed — expected teardown
        except BaseException as exc:        # noqa: BLE001 — anything else is a real bug -> propagate
            errs.append(exc)

    t = threading.Thread(target=serve)      # NOT a daemon — we JOIN it deterministically
    t.start()
    try:
        yield path
    finally:
        srv.close()
        t.join(5)
        assert not t.is_alive(), "echo-server thread did not terminate"
        if errs:
            raise errs[0]


def test_same_uid_tmp_peer_is_accepted(tmp_path):
    with _echo_server() as path:
        reply = backends.RealUnixClient().request(path, b"PING\n", 2.0, 64)
        assert reply == b"OK\n"                          # same euid -> allowed


def test_foreign_uid_tmp_peer_is_refused(tmp_path, monkeypatch):
    with _echo_server() as path:
        # Simulate a FOREIGN peer: the real peer is us, but pretend the controller euid is different,
        # so the peer uid no longer matches -> refused before any payload is sent.
        monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 4242)
        with pytest.raises(OSError):
            backends.RealUnixClient().request(path, b"PING\n", 2.0, 64)


def test_send_path_also_authenticates_tmp_peer(tmp_path, monkeypatch):
    with _echo_server() as path:
        monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 4242)
        with pytest.raises(OSError):
            backends.RealUnixClient().send(path, b"DATA\n", 2.0)   # fire-and-forget is guarded too


def test_run_loraham_socket_is_exempt_from_peercred():
    # A /run/loraham path uses the dedicated-daemon-UID + dir/group model — the peer check is skipped,
    # so no getsockopt is attempted (a dummy object with no getsockopt proves it is never touched).
    class _NoSock:
        def getsockopt(self, *a, **k):
            raise AssertionError("peercred must not be checked for /run/loraham sockets")
    backends._authenticate_tmp_peer(_NoSock(), "/run/loraham/lora868.sock")   # returns, no raise
