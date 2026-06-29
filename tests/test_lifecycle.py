"""Tests for the lifecycle layer: build/stop/logs jobs and the bounded TX test.

All fakes — no real processes, no hardware, no RF.
"""

from __future__ import annotations

import time

from lhpc.core.config import Config
from lhpc.core.jobs import run_job
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import (
    Component,
    ComponentKind,
    EndpointSpec,
    ProcessSpec,
    Requirement,
)
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem


def _life(system, tmp_path) -> Lifecycle:
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(values={}), system)


def test_missing_requirement_detected_with_install_hint(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     requires=(Requirement(cmd="nope_xyz", install="sudo apt install nope"),))
    fake = FakeSystem(commands={
        ("/bin/sh", "-c", "command -v nope_xyz"): CommandResult(1, "", ""),
    })
    missing = _life(fake.system, tmp_path).missing_requirements(comp)
    assert len(missing) == 1 and missing[0].install == "sudo apt install nope"


def test_present_requirement_not_flagged(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     requires=(Requirement(cmd="sh"),))
    fake = FakeSystem(commands={
        ("/bin/sh", "-c", "command -v sh"): CommandResult(0, "/bin/sh\n", ""),
    })
    assert _life(fake.system, tmp_path).missing_requirements(comp) == []


def test_run_job_writes_log_and_reports_success(tmp_path):
    argv = ["echo", "hi"]
    fake = FakeSystem(commands={tuple(argv): CommandResult(0, "hi\n", "")})
    res = run_job(fake.system.runner, name="t", argv=argv, cwd=None,
                  logs_dir=tmp_path / "logs")
    assert res.ok and res.tail == ["hi"]
    assert (tmp_path / "logs" / "t.log").read_text() == "hi\n"


def test_run_job_failure_state(tmp_path):
    argv = ["false"]
    fake = FakeSystem(commands={tuple(argv): CommandResult(1, "", "boom")})
    res = run_job(fake.system.runner, name="t", argv=argv, cwd=None,
                  logs_dir=tmp_path / "logs")
    assert not res.ok and res.returncode == 1


def test_stop_without_process_identity(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE)
    killed, note = _life(FakeSystem().system, tmp_path).stop(comp)
    assert killed == [] and "no process" in note


def test_stop_no_matching_process(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     process=ProcessSpec(exec_name="nope"))
    killed, note = _life(FakeSystem().system, tmp_path).stop(comp)
    assert killed == [] and "no matching process" in note


def test_logs_tails_component_log(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("\n".join(f"line{i}" for i in range(10)) + "\n")
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     log_paths=(str(log),))
    path, tail = _life(FakeSystem().system, tmp_path).logs(comp, lines=3)
    assert path == str(log) and tail == ["line7", "line8", "line9"]


def test_tx_test_send_failure_is_handled(tmp_path):
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": b"STATS TXOK=0\n"},
                      unix_errors={"/tmp/lora433.sock": "broken pipe"})
    res = _life(fake.system, tmp_path).run_daemon_tx_test("433", "X")
    assert not res.ok and "send failed" in res.detail


def test_tx_test_confirms_one_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)

    class StatefulUnix:
        def __init__(self):
            self.txok = 0
        def request(self, path, payload, timeout, max_bytes):
            return f"STATS UPTIME=1 RADIO=READY TXOK={self.txok}\n".encode()
        def send(self, path, payload, timeout):
            self.txok += 1   # the raw-socket write transmits one frame

    system = FakeSystem().system
    system.unix = StatefulUnix()
    res = _life(system, tmp_path).run_daemon_tx_test("433", "LHPC TX TEST")
    assert res.ok and res.txok_before == 0 and res.txok_after == 1
