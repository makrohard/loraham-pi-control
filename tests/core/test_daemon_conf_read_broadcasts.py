"""The CONF read's stop rule (daemon 1.6.0, plan F1 "a"): a line the daemon pushes on its own (TX=, CAD=, RSSI=,
RADIO=) just before the reply is skipped, so it is never read as the reply.

Through the public reads (read_view, probe_daemon_status, apply_set's ack, read_radio, read_socket_line) on a REAL
AF_UNIX socket, RealUnixClient included. The fake daemon writes each part only after the client has read the one
before (its socket's SIOCOUTQ is 0), so every part is its own recv; it holds each connection until the client closes
it, so a read that does not stop at the reply runs into its timeout. The socket is a short relative path in the
test's own directory (AF_UNIX paths are <= 107 bytes; a long basetemp would not fit).
"""

import contextlib
import dataclasses
import fcntl
import select
import socket
import struct
import termios
import threading

from lhpc.core import daemon_control
from lhpc.core.probes.backends import FakeSystem, RealUnixClient
from lhpc.core.probes.unixsock import probe_daemon_status

SOCK = "d.sock"
STATUS = b"STATUS RADIO=READY TX=0 TXRESULT=1 TXMODE=MANAGED RADIOERR=retry USBFAULT=0 FREQ=- SF=- POWER=-\n"
STATS = b"STATS UPTIME=5 RADIO=READY RX=0 TXOK=1 TXERR=0 TXBUSY=0\n"
RADIO = b"RADIO RADIO=READY MODE=LORA FREQ=868.0 SF=12 BW=125.0 CR=5 PREAMBLE=8 SYNC=0x12 CRC=1 LDRO=1 POWER=10\n"


def _drained(conn):
    """The client has read every byte sent on conn (SIOCOUTQ 0), checked up to 5000 times."""
    for _ in range(5000):
        left = struct.unpack("i", fcntl.ioctl(conn.fileno(), termios.TIOCOUTQ, struct.pack("i", 0)))[0]
        if left == 0:
            return True
        select.select((), (), (), 0.001)            # the next look at the queue
    return False


def _serve(conn, answers, hold, errs):
    with conn:
        conn.settimeout(5)
        cmd = conn.recv(64)
        parts = next(p for c, p in answers.items() if cmd.startswith(c))
        for i, part in enumerate(parts):
            if i and not _drained(conn):
                errs.append(f"part {i} never read")
                return
            try:
                conn.sendall(part)
            except OSError:
                return                              # the client stopped before this part (it closed)
        while hold:                                # until the client closes it (EOF, or reset with bytes unread)
            try:
                if not conn.recv(64):
                    return
            except ConnectionResetError:
                return


@contextlib.contextmanager
def _daemon(tmp_path, monkeypatch, answers, hold=True, conns=1):
    """A fake daemon CONF socket answering each command (by prefix) with its parts, for `conns` connections in
    turn; yields a System whose unix client is the real one and whose CONF socket for every band is this one."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daemon_control, "conf_socket", lambda band: SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    srv.listen(4)
    srv.settimeout(5)
    errs = []

    def loop():
        for n in range(conns):
            try:
                conn, _ = srv.accept()
            except OSError:
                errs.append(f"connection {n + 1} of {conns} never came")
                return
            _serve(conn, answers, hold, errs)

    t = threading.Thread(target=loop)
    t.start()
    try:
        yield dataclasses.replace(FakeSystem().system, unix=RealUnixClient())
    finally:
        srv.close()
        t.join(10)
    assert not t.is_alive() and errs == []


def test_read_view_skips_a_radio_and_a_tx_line_before_each_reply(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"RADIO=UNPLUGGED\n", STATUS],
                                         b"GET STATS": [b"TX=0\n", STATS]}, conns=2) as system:
        view = daemon_control.read_view(system, "r1")
    assert (view.reachable, view.status.get("RADIO"), view.stats.get("TXOK")) == (True, "READY", "1")


def test_probe_daemon_status_skips_a_radio_line(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"RADIO=UNPLUGGED\n", STATUS]}) as system:
        st = probe_daemon_status(system, SOCK)
    assert (st.reachable, st.ready) == (True, True)


def test_a_broadcast_split_across_reads_is_skipped(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"RAD", b"IO=UNPLUGGED\n", STATUS]}) as system:
        st = probe_daemon_status(system, SOCK)
    assert (st.reachable, st.ready) == (True, True)


def test_a_broadcast_and_the_reply_in_one_write(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"CAD=1\n" + STATUS]}) as system:
        st = probe_daemon_status(system, SOCK)
    assert (st.reachable, st.ready) == (True, True)


def test_a_set_ack_after_a_tx_line_is_acked(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"SET ": [b"TX=1\n", b"OK\n"], b"GET STATUS": [STATUS]},
                 conns=2) as system:
        ok, confirmed, detail = daemon_control.apply_set(system, "r1", "TXRESULT", "1")
    assert (ok, confirmed) == (True, True), detail


def test_read_radio_skips_an_rssi_line(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET RADIO": [b"RSSI=-80.00\n", RADIO]}) as system:
        radio = daemon_control.read_radio(system, "r1", {"RADIOERR": "retry"})
    assert (radio.get("RADIO"), radio.get("FREQ"), radio.get("POWER")) == ("READY", "868.0", "10")


def test_view_socket_shows_the_reply_not_the_broadcast(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"CAD=1\n", STATUS]}) as system:
        line = daemon_control.read_socket_line(system, "r1")
    assert line == STATUS.decode().strip()


def test_any_other_first_line_still_ends_the_read_unreachable(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"FOO=1\n", STATUS]}) as system:
        st = probe_daemon_status(system, SOCK)
    assert (st.reachable, st.evidence.get("sample")) == (False, "FOO=1")


def test_only_broadcasts_then_eof_fails_closed(tmp_path, monkeypatch):
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"TX=0\n", b"RADIO=UNPLUGGED\n"]}, hold=False) as system:
        st = probe_daemon_status(system, SOCK)
    assert st.reachable is False


def test_broadcasts_and_a_reply_ending_exactly_at_the_read_cap_fail_closed(tmp_path, monkeypatch):
    # 800 TX= lines (4000 bytes) + STATUS (96 bytes with its newline) = exactly the 4096-byte cap: every byte read is
    # returned, so the cap check rejects the read, complete STATUS line or not.
    assert len(b"TX=0\n" * 800 + STATUS) == daemon_control._MAX
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [b"TX=0\n" * 800, STATUS]}) as system:
        view = daemon_control.read_view(system, "r1")
    assert (view.reachable, view.error) == (False, "no STATUS response")


def test_a_reply_cut_by_the_read_cap_after_broadcasts_fails_closed(tmp_path, monkeypatch):
    # Broadcasts fill the cap up to "FREQ=86" of a STATUS ending in FREQ=868.125: the read stops there, before the
    # line's newline. The cut line on its own would parse (FREQ=86, a wrong value), so only the cap check, which sees
    # every byte read, keeps it out.
    status = b"STATUS RADIO=READY TX=0 TXRESULT=1 TXMODE=MANAGED RADIOERR=retry USBFAULT=0 FREQ=868.125\n"
    cut = status.index(b"FREQ=86") + len(b"FREQ=86")
    n, rem = divmod(daemon_control._MAX - cut, 5)
    lead = b"TX=" + b"0" * (1 + rem) + b"\n" + b"TX=0\n" * (n - 1)     # 5 * n + rem bytes of broadcasts
    assert (len(lead) + cut, cut < len(status) - 1) == (daemon_control._MAX, True)
    assert daemon_control.parse_conf_reply(status[:cut], "STATUS").get("FREQ") == "86"
    with _daemon(tmp_path, monkeypatch, {b"GET STATUS": [lead, status]}) as system:
        view = daemon_control.read_view(system, "r1")
    assert (view.reachable, view.error) == (False, "no STATUS response")
