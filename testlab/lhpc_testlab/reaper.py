"""The keeper in front of a lab command (`testing.run_lab`): a child subreaper (PR_SET_CHILD_SUBREAPER), so all the
command starts stays a descendant of this process while the command runs. That includes a grandchild whose parent
has exited and one started with setsid. run_lab's timeout can therefore find all of it and kill it. The
keeper runs the command without core dumps and passes SIGABRT on to it (the stack dump). On SIGTERM, SIGINT or SIGHUP
(an outer kill) it kills all it keeps and ends. When the command ends, the keeper ends the same way (its exit status,
or its signal). What the command left running on purpose (a lab daemon) lives on, except after a SIGABRT (an abort):
then the command's end, too, kills all it keeps first. Killing all it keeps (`clean`) repeats until no live descendant
is left (one forked after a pass is found by the next), with a deadline.

Run as `python -I -S reaper.py PROGRAM ARG...` (-I: PYTHONFAULTHANDLER and the rest are the command's, not its own)."""
from __future__ import annotations

import ctypes
import os
import resource
import signal
import sys
import time

PR_SET_CHILD_SUBREAPER = 36


def descendants(pid: int) -> list[tuple[int, int]]:
    """(pid, start time) of each descendant of `pid`, read from /proc."""
    kids: dict[int, list[tuple[int, int]]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as fh:
                f = fh.read().rsplit(")", 1)[1].split()      # after "(comm)": state ppid pgrp ...
        except (OSError, IndexError):
            continue                                         # ended while read
        kids.setdefault(int(f[1]), []).append((int(name), int(f[19])))   # f[19]: start time
    found, todo = [], list(kids.get(pid, []))
    while todo:
        p = todo.pop()
        found.append(p)
        todo += kids.get(p[0], [])
    return found


def _start_time(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return int(fh.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def pin(listed: list[tuple[int, int]]) -> list[int]:
    """A pidfd for each listed process that is still the one listed (same start time once the pidfd is open): a
    later kill through it reaches that process or nothing, never a process that got its pid since."""
    fds = []
    for pid, start in listed:
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        if _start_time(pid) == start:
            fds.append(fd)
        else:
            os.close(fd)                                     # the pid is someone else's now
    return fds


def kill(fds: list[int]) -> None:
    """SIGKILL through each pidfd, then close it."""
    for fd in fds:
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass                                             # already ended
        finally:
            os.close(fd)


def _live(listed: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The listed processes that are not zombies (a zombie is reaped by its parent or, orphaned, by this keeper)."""
    out = []
    for pid, start in listed:
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] != "Z":
                    out.append((pid, start))
        except (OSError, IndexError):
            pass                                             # ended
    return out


def clean(deadline: float = 30.0) -> bool:
    """Kill every descendant of this process and reap the orphans it keeps, pass after pass, until none is live
    (False: still one at the deadline, e.g. in uninterruptible sleep)."""
    end = time.monotonic() + deadline
    while True:
        try:
            while os.waitpid(-1, os.WNOHANG)[0] > 0:
                pass
        except ChildProcessError:
            pass
        live = _live(descendants(os.getpid()))
        if not live:
            return True
        if time.monotonic() > end:
            os.write(2, f"reaper: {len(live)} descendant(s) still live after {deadline:g} s\n".encode())
            return False
        kill(pin(live))
        time.sleep(0.01)


def main(argv: list[str]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        sys.exit(f"reaper: prctl(PR_SET_CHILD_SUBREAPER): {os.strerror(ctypes.get_errno())}")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))         # no core dumps, for it and the command
    child = 0
    aborted = False

    def forward(sig, _frame):
        nonlocal aborted
        aborted = True                                       # the command's end kills all it keeps (clean)
        if child:
            try:
                os.kill(child, sig)
            except ProcessLookupError:
                pass

    def end(sig, _frame):
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(s, signal.SIG_IGN)                 # one clean: a second signal does not restart it
        clean()
        os._exit(128 + sig)

    signal.signal(signal.SIGABRT, forward)
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, end)
    child = os.fork()
    if child == 0:
        try:
            os.execv(argv[0], argv)  # noqa: S606 (deliberate: exec the lab command directly, no shell)
        except OSError as exc:
            os.write(2, f"reaper: cannot run {argv[0]}: {exc}\n".encode())
        os._exit(127)
    while True:
        pid, status = os.waitpid(-1, 0)                      # reaps the orphans it keeps, too
        if pid != child:
            continue
        if aborted:
            clean()
        if os.WIFSIGNALED(status):                           # end as the command ended
            sig = os.WTERMSIG(status)
            if sig not in (signal.SIGKILL, signal.SIGSTOP):  # those two have no handler to reset
                signal.signal(sig, signal.SIG_DFL)
            os.kill(os.getpid(), sig)
        os._exit(os.waitstatus_to_exitcode(status) & 0xFF)


if __name__ == "__main__":
    main(sys.argv[1:])
