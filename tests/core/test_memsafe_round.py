"""MEMSAFE: on a small box every console build or test job runs one at a time.

`spawn_web_job` reads MemTotal from /proc/meminfo before a Build's first job: below
`build_plan.SERIAL_BELOW_TOTAL_KB` (1 GiB) every job it spawns (a single job, every job of a round,
the primary included) is rendered with one box-wide lock, and each detached launcher waits for that
lock, unbounded, before it takes any other lock. A box at or above it runs its jobs in parallel, as
before. MemTotal does not move while the box runs, so two Builds on one box always decide alike.
The request returns at once either way: the jobs are spawned detached and wait in their launchers."""

import fcntl
import os
import subprocess
import sys
import time

import pytest

from lhpc.core import build_plan, commands, reslock
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService

T = 1024 * 1024              # build_plan.SERIAL_BELOW_TOTAL_KB, pinned: a change of T is a decision
LOCK = "build-round"         # build_plan.ROUND_LOCK_KEY, pinned
ZERO, PI5 = 425_000, 8_245_000   # MemTotal (kB) of a Pi Zero 2 W (415 MiB) and a Pi 5 (8 GiB)


def _meminfo(total_kb):
    return (f"MemTotal:       {total_kb} kB\nMemFree:           60000 kB\nMemAvailable:     216000 kB\n"
            "SwapTotal:       1200000 kB\nSwapFree:         700000 kB\n")


@pytest.mark.parametrize("total_kb, serial", [
    (ZERO, True), (T - 1, True), (T, False), (PI5, False),
    (None, True),                 # unknown: one at a time
])
def test_a_box_below_the_threshold_runs_its_jobs_one_at_a_time(total_kb, serial):
    # T-MS1: a pure function of MemTotal.
    assert build_plan.serial_box(total_kb) is serial


class _Raising:
    def read_text(self, path, max_bytes):
        raise OSError(5, "Input/output error")


@pytest.mark.parametrize("fs, lock", [
    (FakeSystem(files={"/proc/meminfo": _meminfo(ZERO)}).system.fs, True),
    (FakeSystem(files={"/proc/meminfo": _meminfo(PI5)}).system.fs, False),
    (FakeSystem(files={"/proc/meminfo": ""}).system.fs, True),
    (FakeSystem(files={"/proc/meminfo": "MemAvailable: 9000000 kB\n"}).system.fs, True),   # no MemTotal
    (_Raising(), True),           # the read itself fails: unknown, one at a time
], ids=["zero", "pi5", "empty", "no-memtotal", "read-fails"])
def test_the_lock_is_read_from_memtotal_and_an_unknown_box_is_serial(tmp_path, fs, lock):
    paths = Paths(runtime_root=tmp_path)
    expected = str(reslock.lock_file_path(paths, LOCK)) if lock else ""
    assert build_plan.round_lock(paths, fs) == expected


class _Commands(dict):
    """The box's command table: a `systemctl` probe answers inactive, every other command succeeds."""

    def get(self, argv, default=None):
        return CommandResult(3, "inactive\n", "") if argv[0] == "systemctl" else CommandResult(0, "ok\n", "")


def _rendered(tmp_path, monkeypatch, total_kb, target):
    """The console's Build of `target` on a box whose MemTotal is `total_kb`: each rendered job's
    (target, lock). The detached spawn is stubbed by a live process the test ends itself."""
    root = tmp_path / "rt"
    svc = ControllerService(system=FakeSystem(commands=_Commands(),
                                              files={"/proc/meminfo": _meminfo(total_kb)}).system,
                            paths=Paths(runtime_root=root))
    assert svc.bootstrap(apply=True).ok
    for sp in ("src/openhop-core", "src/openhop-repeater", "src/meshcore-webui", "src/meshcore-cli"):
        (root / sp).mkdir(parents=True)
    seen, real = [], commands.render_build_launcher

    def render(*a, **kw):
        seen.append((kw["target"], kw.get("round_lock_path", "")))
        return real(*a, **kw)
    monkeypatch.setattr(commands, "render_build_launcher", render)
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    monkeypatch.setattr(Lifecycle, "spawn_job", lambda self, name, argv, cwd, env=None: (f"{name}.log", child.pid))
    try:
        assert svc.spawn_web_job("build", target)[1] == "pending"
    finally:
        child.stdin.close()               # the stand-in ends when the test says so
        child.wait()
    return seen, str(reslock.lock_file_path(svc._paths, LOCK))


def test_a_small_box_gives_every_job_of_a_round_the_lock(tmp_path, monkeypatch):
    # T-MS2 (spawn side): every job of the round, the primary first, waits for the one lock.
    seen, lock = _rendered(tmp_path, monkeypatch, ZERO, "meshcore")
    assert seen == [("meshcore-node", lock), ("meshcore-webui", lock), ("meshcore-cli", lock)]


def test_a_small_box_gives_a_single_job_the_lock_too(tmp_path, monkeypatch):
    # A one-component Build on a small box waits for a running round as well (rounds serialize
    # across Builds, G1-MEMSAFE-1 F2).
    seen, lock = _rendered(tmp_path, monkeypatch, ZERO, "meshcore-cli")
    assert seen == [("meshcore-cli", lock)]


def test_a_large_box_runs_its_round_in_parallel_as_before(tmp_path, monkeypatch):
    # T-MS3: unchanged behaviour on a large box.
    seen, _lock = _rendered(tmp_path, monkeypatch, T, "meshcore")
    assert seen == [("meshcore-node", ""), ("meshcore-webui", ""), ("meshcore-cli", "")]


# ---- the launcher side: real launcher processes on one real lock ----------------------------------

def _launcher(tmp_path, steps):
    root = tmp_path / "rt"
    spec = {"steps": [{"argv": a, "env_items": []} for a in steps], "cwd": str(tmp_path),
            "runtime_root": str(root), "lock_names": [], "shared_lock_names": [],
            "index_lock_name": "", "round_lock_name": LOCK + ".lock"}
    return subprocess.Popen([sys.executable, "-c", "import sys; from lhpc.core import build_launcher_runtime as b; "
                             "b.run(eval(sys.argv[1]))", repr(spec)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, "PYTHONPATH": os.getcwd()})


def _until(cond, what):
    """Wait for an EVENT; the deadline only ends a hang, it never decides a pass."""
    end = time.monotonic() + 60
    while not cond():
        assert time.monotonic() < end, f"no {what} within 60 s"
        time.sleep(0.02)


def _blocked_on_flock(pid):
    """/proc/locks lists a process blocked in flock as a `->` row with its pid: the kernel's own
    word that the launcher waits in the lock, not merely that it printed it would."""
    with open("/proc/locks") as f:
        return any("->" in ln and "FLOCK" in ln and f" {pid} " in ln for ln in f)


def _log_step(log, word):
    return [sys.executable, "-c", f"open({str(log)!r}, 'a').write({word!r} + '\\n')"]


def _held_step(log, word, release, rc):
    """Log `word`-entry and say so on stdout, block opening the FIFO `release` until the test opens
    it too (the barrier), log `word`-exit, exit rc."""
    return [sys.executable, "-c",
            f"import sys\nopen({str(log)!r}, 'a').write('{word}-entry\\n')\nprint('{word}-entry', flush=True)\n"
            f"open({str(release)!r}).read()\n"
            f"open({str(log)!r}, 'a').write('{word}-exit\\n')\nsys.exit({rc})"]


def _release(fifo):
    """Open A's barrier if A still waits at it (cleanup after a failed assert); ENXIO: nobody waits."""
    try:
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    except OSError:
        pass


@pytest.mark.parametrize("first_rc", [0, 1], ids=["first-succeeds", "first-fails"])
def test_two_launchers_on_the_lock_run_their_steps_one_after_the_other(tmp_path, first_rc):
    # T-MS2 (launcher side): A holds the lock inside its step (the test's barrier); B, started
    # meanwhile, is blocked in flock (the kernel says so) and has run nothing; once A ends,
    # whether it passed or failed, B runs. The one log shows the order.
    log, release = tmp_path / "order.log", tmp_path / "release-a"
    os.mkfifo(release)
    (tmp_path / "rt" / "state" / "locks").mkdir(parents=True)
    a = _launcher(tmp_path, [_held_step(log, "A", release, first_rc)])
    b = None
    try:
        while (line := a.stdout.readline()) != "A-entry\n":          # A is inside its step
            assert line, "A ended before its step"
        b = _launcher(tmp_path, [_log_step(log, "B-entry"), _log_step(log, "B-exit")])
        assert b.stdout.readline() == "waiting for memory: this box runs one build job at a time\n"
        _until(lambda: _blocked_on_flock(b.pid), "B blocked in flock")
        assert log.read_text() == "A-entry\n"
        with open(release, "w"):                                         # A's barrier opens
            pass
        assert a.wait(timeout=60) == (0 if first_rc == 0 else 1)
        assert b.wait(timeout=60) == 0, b.stdout.read()
        assert log.read_text() == "A-entry\nA-exit\nB-entry\nB-exit\n"
    finally:
        _release(release)
        for p in (a, b):
            if p is not None and p.poll() is None:
                p.kill()
                p.wait()


def test_a_round_lock_that_cannot_be_taken_for_another_reason_blocks_the_job(tmp_path, monkeypatch):
    # N1: only EAGAIN/EWOULDBLOCK means "another job holds it"; any other flock error is a blocked
    # job (exit 3), never an endless wait.
    from lhpc.core import build_launcher_runtime as blr
    (tmp_path / "rt" / "state" / "locks").mkdir(parents=True)
    real = fcntl.flock

    def flock(fd, op):
        if op & fcntl.LOCK_NB:
            raise OSError(9, "Bad file descriptor")
        return real(fd, op)
    monkeypatch.setattr(blr.fcntl, "flock", flock)
    spec = {"steps": [{"argv": ["true"], "env_items": []}], "cwd": str(tmp_path),
            "runtime_root": str(tmp_path / "rt"), "lock_names": [], "shared_lock_names": [],
            "index_lock_name": "", "round_lock_name": LOCK + ".lock"}
    with pytest.raises(SystemExit) as e:
        blr.run(spec)
    assert e.value.code == 3
