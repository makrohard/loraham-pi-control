"""W389 (1): a lab's fakes stop with the lab. `lhpc-testlab init`/`reset` spawn the fake gpsd (and the APRS-IS sink)
detached, each the leader of its own process group; `LabServer.stop()` stopped only `lhpc web`, so every lab root left
its fake gpsd running. In this serial lane, not the unit lane: a real reset reads every daemon on the host as its own
(a parallel unit test's fake daemon, another checkout's live lab), so it holds the host's lab lock like every lab here."""
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


def test_a_stopped_lab_leaves_no_fake_of_its_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("stoproot") / "runtime"
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
