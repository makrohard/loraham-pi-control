"""Test helpers for the lab, importable as `lhpc_testlab.testing` — no path hacks.

Builds a lab runtime root through the REAL `lhpc` executable, starts the REAL waitress server
on a free loopback port, and tears both down. Shared by the acceptance and browser lanes.
`make_lab_root` is the in-process equivalent for tests that need only a latched root."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from lhpc.core.paths import Paths


def lab_bin() -> Path:
    exe = Path(sys.executable).parent / "lhpc-testlab"
    if not exe.exists():
        raise RuntimeError(f"no lhpc-testlab console script at {exe} — install the lab "
                           "(pip install -e ./testlab)")
    return exe


def lhpc_bin() -> Path:
    exe = Path(sys.executable).parent / "lhpc"
    if not exe.exists():
        raise RuntimeError(f"no installed lhpc console script at {exe} — the "
                           "acceptance lane needs an editable install "
                           "(pip install -e .[dev])")
    return exe


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def lab_env(root: Path) -> dict:
    env = dict(os.environ)
    env["LHPC_RUNTIME_ROOT"] = str(root)
    env["LHPC_TESTLAB"] = "1"
    env["LHPC_SYSTEM_PROVIDER"] = "lhpc_testlab.provider:build"
    env["LHPC_BOOT_ID_FILE"] = str(root / "state" / "testlab" / "host" / "boot_id")
    env["LHPC_FW_PATH_PREFIX"] = str(root / "state" / "testlab" / "host")
    env.pop("INVOCATION_ID", None)
    return env


def run_lab(env: dict, *args: str, timeout: float = 300.0, check: bool = False):
    r = subprocess.run([str(lab_bin()), *args], env=env, capture_output=True,
                       text=True, timeout=timeout, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"lhpc-testlab {' '.join(args)} rc={r.returncode}\n"
                           f"stdout: {r.stdout[-2000:]}\nstderr: {r.stderr[-2000:]}")
    return r


def named(env: dict, target: str, band: str = "") -> str:
    """`<target>@<rid>` on the lab root's own hardware list (W323): every stack verb of a lane names its instance.
    lhpc's own suggestion (instances.named) when it has one candidate; with several, the lab picks the first (the
    band's first enabled radio, else the first running instance, else the first radio the stack can run on), a
    test's choice that lhpc itself never makes (G1-C60-RE)."""
    from lhpc.core import instances, radios
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService
    if "LHPC_RUNTIME_ROOT" not in env:                  # a unit lane's stubbed env: no lab root to read
        return f"{target}@<rid>"
    svc = ControllerService(paths=Paths(runtime_root=Path(env["LHPC_RUNTIME_ROOT"])))
    if not (name := instances.named(svc, target, band=band)).endswith("@<rid>"):
        return name
    sid = target if svc.stack(target) is not None else (svc.stack_of(target) or "")
    if not sid:
        return name
    rids = ([r.id for r in radios.load(svc._paths).radios if r.enabled and r.band == band] if band else
            [k.partition("@")[2] for k in instances.running_keys(svc, sid)] or instances.can_run_on(svc, svc.stack(sid)))
    return f"{target}@{rids[0]}" if rids else name


def running(env: dict, sid: str) -> list:
    """Every running instance of `sid` on the lab root (instances.running_keys), else its `named` one: a teardown
    stops each (a bare stop no longer stops every radio's daemon, W323)."""
    from lhpc.core import instances
    from lhpc.core.services import ControllerService
    if "LHPC_RUNTIME_ROOT" not in env:                  # a unit lane's stubbed env
        return [named(env, sid)]
    svc = ControllerService(paths=Paths(runtime_root=Path(env["LHPC_RUNTIME_ROOT"])))
    return instances.running_keys(svc, sid) or [named(env, sid)]


def port_of(env: dict, stack: str, base: int) -> int:
    """The TCP port `stack`'s `named` instance serves where the manifest says `base`: its endpoint as lhpc renders it
    for that instance (DEC-W348's per-radio ports), so a lane waits where the instance listens; `base` when no
    endpoint of the stack is on it, or for a unit lane's stubbed env."""
    from lhpc.core import instances
    from lhpc.core.services import ControllerService
    if "LHPC_RUNTIME_ROOT" not in env:
        return base
    svc = ControllerService(paths=Paths(runtime_root=Path(env["LHPC_RUNTIME_ROOT"])))
    _sid, inst, _band, _why = instances.resolve(svc, named(env, stack))
    for comp in (svc.stack(stack).components if inst is not None else ()):
        for tpl, ep in zip(comp.endpoints, instances.endpoints_for(comp.endpoints, inst), strict=True):
            if tpl.kind == "tcp" and tpl.address.endswith(f":{base}"):
                return int(ep.address.rsplit(":", 1)[1])
    return base


def run_lhpc(env: dict, *args: str, timeout: float = 300.0,
             check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run([str(lhpc_bin()), *args], env=env, capture_output=True,
                       text=True, timeout=timeout, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"lhpc {' '.join(args)} rc={r.returncode}\n"
                           f"stdout: {r.stdout[-2000:]}\nstderr: {r.stderr[-2000:]}")
    return r


# W389: a loraham_daemon on this host is every lab root's daemon to the product (the radio sockets are the host's, and
# the run state matches the exec name host-wide), so ONE holder per host may run one: a LabServer from its init/reset or
# start until its stop, a test that resets a lab or runs a fake daemon (`lab_serial`, testlab/tests/conftest.py) for its
# own run. Holders of one process share the lock (the run-wide lab fixture, a case's own root); a holder of another
# process (another worker, another run) is waited for up to `wait_s`, then the lab refuses, naming it.
LAB_LOCK_PATH = os.environ.get("LHPC_TESTLAB_LOCK", "/tmp/lhpc-testlab-radio.lock")   # the env: a test's own lock
LAB_LOCK_WAIT_S = 120.0          # a LabServer rides out a test's short hold; another lab's lifetime it refuses
_LAB_LOCKS: dict = {}              # path -> [fd, holders] of this process (a test may point LAB_LOCK_PATH elsewhere)


def _hold_lab_lock(root, wait_s: float | None = None) -> str:
    """Hold the host's lab lock (LAB_LOCK_PATH as it is now) for `root`; returns that path, for _release_lab_lock."""
    import fcntl
    path = LAB_LOCK_PATH
    if path not in _LAB_LOCKS:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)    # never a planted symlink
        deadline = time.monotonic() + (LAB_LOCK_WAIT_S if wait_s is None else wait_s)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() < deadline:
                    time.sleep(0.2)
                    continue
                holder = os.read(fd, 512).decode("utf-8", "replace").strip()
                os.close(fd)
                raise RuntimeError(f"another lab holds this host's radio sockets ({holder or 'holder unknown'}, lock "
                                   f"{path}): one lab per host, stop it first") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"pid {os.getpid()} root {root}\n".encode())
        _LAB_LOCKS[path] = [fd, 0]
    _LAB_LOCKS[path][1] += 1
    return path


def serial_held_by_ancestor() -> bool:
    """The lab_serial turn lock (testlab/tests/conftest.py) is held by an ancestor of this process: a pytest that the
    holding test runs itself. LHPC_LAB_SERIAL_HELD names the holder's pid; any other value is not honoured."""
    try:
        pid = int(os.environ.get("LHPC_LAB_SERIAL_HELD", ""))
    except ValueError:
        return False
    p = os.getppid()
    while p > 1:
        if p == pid:
            return True
        try:
            with open(f"/proc/{p}/stat") as fh:
                p = int(fh.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            return False
    return False


def _release_lab_lock(path: str) -> None:
    held = _LAB_LOCKS[path]
    held[1] -= 1
    if held[1] == 0:
        os.close(held[0])
        del _LAB_LOCKS[path]


class LabServer:
    """A real `lhpc web` process on a free loopback port over a lab root."""

    def __init__(self, root: Path):
        self.root = root
        self.env = lab_env(root)
        self.port = 0
        self.proc: subprocess.Popen | None = None
        self._locked = ""                              # the lock path this lab holds, "" when none

    def _lock(self) -> None:
        if not self._locked:
            self._locked = _hold_lab_lock(self.root)

    def _unlock(self) -> None:
        if self._locked:
            path, self._locked = self._locked, ""
            _release_lab_lock(path)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def init_and_reset(self) -> None:
        self._lock()
        try:
            run_lab(self.env, "init", check=True)
            run_lab(self.env, "reset", check=True)
        except BaseException:
            self._unlock()                             # a lab that did not come up holds nothing
            raise

    def start(self, timeout: float = 30.0) -> None:
        self._lock()
        self.port = free_port()
        log = open(self.root / "weblog.txt", "ab")  # noqa: SIM115 (held by Popen)
        self.proc = subprocess.Popen(
            [str(lab_bin()), "web", "--port", str(self.port)],
            env=self.env, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base}/healthz", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        raise RuntimeError("lhpc web did not come up: "
                           + (self.root / "weblog.txt").read_text()[-2000:])

    def stop(self) -> None:
        """Stop `lhpc web` (when started) and the fakes the lab root's init/reset spawned (W389: they outlived it)."""
        if self.proc is not None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self.proc = None
        stop_lab_fakes(self.root)
        self._unlock()


def stop_lab_fakes(root) -> list:
    """The fakes of the lab root `root` stopped (ops.stop_fakes): for a lab brought up with `run_lab` alone."""
    from . import ops
    return ops.stop_fakes(Paths(runtime_root=Path(root)))


def make_lab_root(tmp_path, monkeypatch) -> Paths:
    """A latched lab root: env key set, marker present, state skeleton created."""
    monkeypatch.setenv("LHPC_TESTLAB", "1")
    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "lhpc_testlab.provider:build")
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "testlab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "state" / "testlab" / "enabled").write_text("lab\n")
    return paths
