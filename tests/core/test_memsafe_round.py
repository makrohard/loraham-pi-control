"""MEMSAFE: a console build round on a small box runs its jobs one at a time.

`spawn_web_job` reads /proc/meminfo once, before the round's first job: below
`build_plan.ROUND_SERIAL_BELOW_KB` every job of the round (the primary included) is rendered with one
round lock, and each detached launcher waits for that lock, unbounded, before it takes any other lock.
At or above the threshold the round runs in parallel, as before. The request returns at once either
way: the jobs are spawned detached and wait in their own launchers."""

import fcntl
import os
import subprocess
import sys

import pytest

from lhpc.core import build_plan, commands, reslock
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService

T = 256 * 1024               # build_plan.ROUND_SERIAL_BELOW_KB, pinned: a change of T is a decision
LOCK = "build-round"         # build_plan.ROUND_LOCK_KEY, pinned


def _meminfo(available_kb):
    return (f"MemTotal:         425000 kB\nMemFree:           60000 kB\nMemAvailable:   {available_kb} kB\n"
            "SwapTotal:       1200000 kB\nSwapFree:         700000 kB\n")


@pytest.mark.parametrize("available_kb, jobs, serial", [
    (216_000, 3, True),          # box Z's idle reading (the incident's 211 MB after relief)
    (T - 1, 2, True),
    (T, 2, False),
    (3_000_000, 3, False),       # box H
    (None, 2, True),             # unreadable: one at a time
    (100_000, 1, False),         # one job is no round to serialize
])
def test_the_round_is_serial_below_the_threshold(available_kb, jobs, serial):
    # T-MS1: a pure function of (MemAvailable, jobs).
    assert build_plan.serial_round(available_kb, jobs) is serial


@pytest.mark.parametrize("text, lock", [(_meminfo(216_000), True), (_meminfo(3_000_000), False),
                                        ("", True), ("MemTotal: 1 kB\n", True)],
                         ids=["box-z", "box-h", "unreadable", "no-available-line"])
def test_the_round_lock_is_read_from_meminfo(tmp_path, text, lock):
    paths = Paths(runtime_root=tmp_path)
    fs = FakeSystem(files={"/proc/meminfo": text}).system.fs
    expected = str(reslock.lock_file_path(paths, LOCK)) if lock else ""
    assert build_plan.round_lock(paths, 3, fs) == expected


class _Commands(dict):
    """The box's command table: a `systemctl` probe answers inactive, every other command succeeds."""

    def get(self, argv, default=None):
        return CommandResult(3, "inactive\n", "") if argv[0] == "systemctl" else CommandResult(0, "ok\n", "")


def _round(tmp_path, monkeypatch, available_kb):
    """The meshcore round from the console on a box whose /proc/meminfo reads `available_kb`: each
    rendered job's (target, round lock). The detached spawn is stubbed by a live process."""
    root = tmp_path / "rt"
    svc = ControllerService(system=FakeSystem(commands=_Commands(),
                                              files={"/proc/meminfo": _meminfo(available_kb)}).system,
                            paths=Paths(runtime_root=root))
    assert svc.bootstrap(apply=True).ok
    for sp in ("src/openhop-core", "src/openhop-repeater", "src/meshcore-webui", "src/meshcore-cli"):
        (root / sp).mkdir(parents=True)
    seen, real = [], commands.render_build_launcher

    def render(*a, **kw):
        seen.append((kw["target"], kw.get("round_lock_path", "")))
        return real(*a, **kw)
    monkeypatch.setattr(commands, "render_build_launcher", render)
    child = subprocess.Popen(["sleep", "30"])
    monkeypatch.setattr(Lifecycle, "spawn_job", lambda self, name, argv, cwd, env=None: (f"{name}.log", child.pid))
    try:
        assert svc.spawn_web_job("build", "meshcore")[:2] == ("build-meshcore-node.log", "pending")
    finally:
        child.kill()
        child.wait()
    return seen, str(reslock.lock_file_path(svc._paths, LOCK))


def test_a_round_below_the_threshold_gives_every_job_the_round_lock(tmp_path, monkeypatch):
    # T-MS2 (spawn side): every job of the round, the primary first, waits for the one round lock.
    seen, lock = _round(tmp_path, monkeypatch, 216_000)
    assert [t for t, _ in seen] == ["meshcore-node", "meshcore-webui", "meshcore-cli"]
    assert {rl for _, rl in seen} == {lock}


def test_a_round_at_or_above_the_threshold_runs_in_parallel_as_before(tmp_path, monkeypatch):
    # T-MS3: unchanged behaviour on a large box.
    seen, _lock = _round(tmp_path, monkeypatch, T)
    assert [t for t, _ in seen] == ["meshcore-node", "meshcore-webui", "meshcore-cli"]
    assert {rl for _, rl in seen} == {""}


def test_a_job_of_a_serial_round_runs_only_once_the_running_one_released_the_lock(tmp_path):
    # T-MS2 (launcher side): one at a time. A job holds the round lock (here: the test); the next
    # job's launcher says it waits and runs no step until the lock is released. Its own line is
    # the event the check waits for, so no step can have run before it.
    root = tmp_path / "rt"
    paths = Paths(runtime_root=root)
    lock = reslock.lock_file_path(paths, LOCK)
    lock.parent.mkdir(parents=True)
    lock.touch()
    marker = tmp_path / "stepped"
    spec = {"steps": [{"argv": ["touch", str(marker)], "env_items": []}], "cwd": str(tmp_path),
            "runtime_root": str(root), "lock_names": [], "shared_lock_names": [],
            "index_lock_name": "", "round_lock_name": lock.name}
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)                  # the round's running job
    p = subprocess.Popen([sys.executable, "-c", "import sys; from lhpc.core import build_launcher_runtime as b; "
                          "b.run(eval(sys.argv[1]))", repr(spec)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         cwd=os.getcwd(), env={**os.environ, "PYTHONPATH": os.getcwd()})
    try:
        assert p.stdout.readline() == "waiting for memory: this box builds one job of the round at a time\n"
        assert not marker.exists() and p.poll() is None
        fcntl.flock(fd, fcntl.LOCK_UN)              # the running job ends
        assert p.wait(timeout=60) == 0, p.stdout.read()
        assert marker.exists()
    finally:
        os.close(fd)
        if p.poll() is None:
            p.kill()
            p.wait()
