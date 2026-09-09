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


def run_lhpc(env: dict, *args: str, timeout: float = 300.0,
             check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run([str(lhpc_bin()), *args], env=env, capture_output=True,
                       text=True, timeout=timeout, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"lhpc {' '.join(args)} rc={r.returncode}\n"
                           f"stdout: {r.stdout[-2000:]}\nstderr: {r.stderr[-2000:]}")
    return r


class LabServer:
    """A real `lhpc web` process on a free loopback port over a lab root."""

    def __init__(self, root: Path):
        self.root = root
        self.env = lab_env(root)
        self.port = 0
        self.proc: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def init_and_reset(self) -> None:
        run_lab(self.env, "init", check=True)
        run_lab(self.env, "reset", check=True)

    def start(self, timeout: float = 30.0) -> None:
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
        if self.proc is None:
            return
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc = None


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
