"""W389 (1): a lab's fakes stop with the lab. `lhpc-testlab init`/`reset` spawn the fake gpsd (and the APRS-IS sink)
detached, each the leader of its own process group; `LabServer.stop()` stopped only `lhpc web`, so every lab root left its fake gpsd
running (~24 MB each, about three per test run). Told by the root: a fake carries its lab's LHPC_RUNTIME_ROOT."""
from __future__ import annotations

import os
import signal
from pathlib import Path

from lhpc_testlab.testing import LabServer


def _fakes_of(root: Path) -> list:
    """The live fake processes (`lhpc_testlab _gpsd`, the APRS-IS sink) whose lab root is `root`."""
    out = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ")
            with open(f"/proc/{p}/environ", "rb") as fh:
                env = fh.read().split(b"\0")
        except OSError:
            continue
        if (b"lhpc_testlab _gpsd" in cmd or b"aprs_sink.py" in cmd) and f"LHPC_RUNTIME_ROOT={root}".encode() in env:
            out.append(int(p))
    return out


def test_a_stopped_lab_leaves_no_fake_of_its_root(tmp_path):
    root = tmp_path / "runtime"
    server = LabServer(root)
    try:
        server.init_and_reset()
        assert _fakes_of(root), "the lab's init/reset spawned its fake gpsd"          # the precondition
        server.stop()                                                                   # no web was started
        assert _fakes_of(root) == [], "a fake of this lab outlived its stop()"
    finally:
        for pid in _fakes_of(root):                     # never leak, even when the assertion fails
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_fake_that_ignores_term_is_killed_with_its_whole_group(tmp_path):
    """The stop reaches the fake's whole process group (it leads a group of its own) and kills what ignores
    SIGTERM after the grace: a stand-in fake that ignores TERM, with a child in its group, both gone."""
    import subprocess
    import sys
    from types import SimpleNamespace

    from lhpc_testlab import gpsd, ops

    from lhpc.core.paths import Paths
    prog = ("import signal, subprocess, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "c = subprocess.Popen([sys.executable, '-c', 'import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "signal.pause()']); print(c.pid, flush=True); signal.pause()")
    fake = subprocess.Popen([sys.executable, "-c", prog, "lhpc_testlab", "_gpsd"], stdout=subprocess.PIPE,
                            start_new_session=True, text=True)
    child = int(fake.stdout.readline())
    paths = Paths(runtime_root=tmp_path)
    try:
        pid_file = gpsd.pid_path(paths)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{fake.pid}\n")
        assert ops.stop_fakes(SimpleNamespace(_paths=paths), grace_s=0.5) == [fake.pid]
        assert fake.wait(timeout=5) == -signal.SIGKILL               # it ignored TERM: killed after the grace
        state = ""
        if os.path.exists(f"/proc/{child}"):
            with open(f"/proc/{child}/stat") as fh:
                state = fh.read().split(") ")[1][0]
        assert state in ("", "Z", "X"), state                         # the child of its group is gone too
    finally:
        for pid in (fake.pid, child):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_lab_reset_refuses_while_a_foreign_daemon_serves_its_radio_sockets(tmp_path, monkeypatch):
    """W389 (2): the daemon's sockets are the host's (/run/loraham or /tmp, LORAHAM_SOCKET_DIR, the clients' paths
    too), so two lab roots on one host collide on loraconf-r1.sock. A reset (the lab's start) refuses while a live one
    exists that its own stopped stacks no longer explain, naming it; a stale socket file does not refuse."""
    import socket
    import tempfile

    from lhpc_testlab import ops
    from lhpc_testlab.testing import make_lab_root, stop_lab_fakes

    from lhpc.core.services import ControllerService
    sockdir = Path(tempfile.mkdtemp(prefix="lab-", dir="/tmp"))     # short: an AF_UNIX path has a limit
    monkeypatch.setattr(ops, "RADIO_SOCKET_DIRS", (str(sockdir),), raising=False)
    live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        live.bind(str(sockdir / "loraconf-r1.sock"))
        live.listen(1)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(sockdir / "loraconf-r2.sock"))                 # bound, never listening: refuses a connect
        stale.close()
        paths = make_lab_root(tmp_path, monkeypatch)
        svc = ControllerService(paths=paths)
        r = ops.reset(svc)
        assert not r.ok and str(sockdir / "loraconf-r1.sock") in r.summary, r.summary
        assert "loraconf-r2.sock" not in r.summary                   # a stale file is no foreign daemon
    finally:
        live.close()
        stop_lab_fakes(tmp_path)
        for f in sockdir.iterdir():
            f.unlink()
        sockdir.rmdir()
