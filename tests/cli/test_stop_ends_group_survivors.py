"""C1: `lhpc stack stop <stack>@<rid>` ends the members of the stopped component's own process group that outlive its
main process (PLAN-C1-STOP-v2). The real tree here behaves as RNS 1.6.1's stamp workers do: the main process exits on
SIGTERM through a Python handler, its workers were forked from a NON-main thread and stay alive after the group's SIGTERM
while they compute, with the main process's argv. Whether the inherited handler runs in such a worker depends on the
CPython version (3.14: never while it computes; 3.11: it runs and the worker exits), so the forking thread blocks SIGTERM
before the fork: the workers outlive the SIGTERM on every version (RNS 1.6.1's did on CPython 3.14.10, PROOF-RNS161)."""
import os
import select
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from lhpc.core import radios
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

R433 = radios.Radio("r3", "b", "uputronics", "433", ce="ce1")

TREE = r"""
import multiprocessing, os, signal, threading, time
signal.signal(signal.SIGTERM, lambda s, f: os._exit(0))
def work():
    while True: sum(range(1000))        # busy, as the stamp search: a blocking call would run the handler on EINTR
def spawn():
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})   # inherited by the fork: the workers outlive the SIGTERM
    for _ in range(2): multiprocessing.get_context("fork").Process(target=work, daemon=True).start()
    print("ready", flush=True)
threading.Thread(target=spawn).start()
while True: time.sleep(0.2)
"""


def _members(pgid):
    out = []
    for n in os.listdir("/proc"):
        if not n.isdigit():
            continue
        try:
            stat = Path(f"/proc/{n}/stat").read_text()
        except OSError:
            continue
        rest = stat[stat.rindex(")") + 2:].split()
        if int(rest[2]) == pgid and rest[0] != "Z":
            out.append(int(n))
    return sorted(out)


def _alive(pid):
    try:
        return open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1][0] != "Z"
    except OSError:
        return False


def _gone(pid, bound=5.0):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        select.select([fd], [], [], bound)
    finally:
        os.close(fd)
    return not _alive(pid)


@pytest.mark.needs_session
def test_a_stop_sigkills_the_workers_that_outlive_its_sigterm_and_names_them(box, tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(Lifecycle, "GROUP_GRACE_S", 0.5, raising=False)
    script = tmp_path / "loraham-kiss-tnc"                      # the component's spec: exec_name loraham-kiss-tnc
    script.write_text(TREE)
    table = {1: ["init"]}                                       # the box's process table, filled below; it shows
    monkeypatch.setattr(FakeSystem, "cmdlines",                 # only the live ones, as /proc does
                        lambda self: {p: a for p, a in self.cmdlines_data.items() if p == 1 or _alive(p)})
    run = box((R433,), procs=table)
    p = subprocess.Popen([sys.executable, str(script)], start_new_session=True, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    workers = []
    try:
        assert p.stdout.readline().strip() == b"ready"
        workers = [w for w in _members(p.pid) if w != p.pid]
        assert len(workers) == 2, workers
        for pid in (p.pid, *workers):
            table[pid] = [sys.executable, str(script)]
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("kiss")
        assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), p.pid, band="433",
                                              extra={"rid": "r3"})
        rc, out, _ = run("stop", "kiss@r3")
        assert rc == 0, out
        assert svc._lifecycle().owned_records("loraham-kiss-tnc", role=None) == [], out   # the record went with the stop
        assert all(_gone(w) for w in workers), out              # 0.14.2: alive -> "no instance claims it"
        assert "no instance claims it" not in out, out
        assert "SIGKILLed " + " ".join(map(str, sorted(workers))) in out, out   # the stop names what it killed
    finally:
        for w in workers:
            try:
                fd = os.pidfd_open(w)
            except ProcessLookupError:
                continue
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                os.close(fd)
        p.kill()
        p.wait(timeout=5)
