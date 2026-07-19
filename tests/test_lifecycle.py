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
                  logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path))
    assert res.ok and res.tail == ["hi"]
    assert (tmp_path / "logs" / "t.log").read_text() == "hi\n"


def test_run_job_failure_state(tmp_path):
    argv = ["false"]
    fake = FakeSystem(commands={tuple(argv): CommandResult(1, "", "boom")})
    res = run_job(fake.system.runner, name="t", argv=argv, cwd=None,
                  logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path))
    assert not res.ok and res.returncode == 1


def test_run_job_announces_log_path_at_creation(tmp_path):
    # Item 7: on_log_open fires the MOMENT the log file exists (before the run) with the EXACT path the
    # job writes to — so a long, silent build can be tailed from another terminal.
    from pathlib import Path
    argv = ["echo", "hi"]
    fake = FakeSystem(commands={tuple(argv): CommandResult(0, "hi\n", "")})
    seen: list = []
    res = run_job(fake.system.runner, name="build-x", argv=argv, cwd=None,
                  logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path),
                  on_log_open=lambda name, path: seen.append((name, path)))
    assert seen == [("build-x", str(tmp_path / "logs" / "build-x.log"))]
    assert seen[0][1] == res.log_path and Path(res.log_path).exists()   # announced == the file it wrote


def test_logs_resolves_newest_build_log_over_stale_unsuffixed(tmp_path):
    # Item 7: `lhpc logs <comp>` (band-less) resolves to the NEWEST job log across start/build/test,
    # so it agrees with what a just-finished build wrote — never a stale unsuffixed sibling.
    import os
    comp = Component(id="widget", name="Widget", kind=ComponentKind.SERVICE)
    life = _life(FakeSystem().system, tmp_path)
    logs = life.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    stale = logs / "build-widget.log"
    fresh = logs / "build-widget-1.log"
    stale.write_text("OLD single-step build\n")
    fresh.write_text("NEW multi-step build step 1\n")
    old = time.time() - 100
    os.utime(stale, (old, old))                                         # stale is older
    path, tail = life.logs(comp)
    assert path == str(fresh) and any("NEW multi-step" in ln for ln in tail)


def test_logs_band_scoped_still_uses_start_log(tmp_path):
    # Item 7: a band-scoped caller (RX/TX feed) still gets the exact band's START log, unchanged.
    comp = Component(id="widget", name="Widget", kind=ComponentKind.SERVICE)
    life = _life(FakeSystem().system, tmp_path)
    logs = life.logs_dir(); logs.mkdir(parents=True, exist_ok=True)
    (logs / "start-widget-868.log").write_text("run log 868\n")
    (logs / "build-widget.log").write_text("build log\n")
    path, _ = life.logs(comp, band="868")
    assert path == str(logs / "start-widget-868.log")


def test_log_announcer_records_details_and_emits_live_and_dedups(tmp_path):
    # Item 7: the service announcer records one copy-pasteable line per new log into `details` AND
    # emits it live via _progress (the CLI printer), deduping repeat opens of the same file.
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    emitted: list = []
    svc._progress = emitted.append
    details: list = []
    p = "/home/u/loraham-pi-control/logs/build-meshcom-qemu-3.log"
    cb = svc._log_announcer("meshcom-qemu", details)
    cb("build-meshcom-qemu-3", p)
    cb("build-meshcom-qemu-3", p)                                       # same file -> not repeated
    assert details == [f"  [log] meshcom-qemu -> tail -f {p}"]
    assert emitted == [f"[log] meshcom-qemu -> tail -f {p}"]


def test_run_job_output_unverified_alone_is_unsafe(tmp_path):
    # P1: an escaped descendant holding the output pipe open (output_unverified) makes the job UNSAFE even
    # when the DIRECT child exited 0 — a SUCCEEDED direct process does NOT prove a descendant stopped.
    class _Runner:
        def run_streaming(self, argv, timeout, log_fh, cwd=None, env=None,
                          redactor=None, should_cancel=None):
            log_fh.write("built ok\n")
            return CommandResult(0, "", "", output_unverified=True)   # clean exit, pipe NOT proven drained

    res = run_job(_Runner(), name="build-x", argv=["true"], cwd=None,
                  logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path))
    assert res.returncode == 0                                        # the direct child succeeded
    assert res.unsafe is True and res.unsafe_scope == "escaped-or-output-unverified"


def test_run_job_log_write_failure_is_not_success(tmp_path):
    # P2: a clean-exiting, fully-drained build whose LOG could not be persisted must NOT be SUCCEEDED — the
    # recorded evidence is incomplete (but it is not `unsafe`: cessation/draining were proven).
    class _Runner:
        def run_streaming(self, argv, timeout, log_fh, cwd=None, env=None,
                          redactor=None, should_cancel=None):
            return CommandResult(0, "", "", log_write_failed=True)

    res = run_job(_Runner(), name="build-x", argv=["true"], cwd=None,
                  logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path))
    assert not res.ok and res.returncode == 0 and res.log_write_failed is True
    assert not res.unsafe                                             # proven stop/drain -> not unsafe


def test_stop_without_process_identity(tmp_path):
    # No ownership record and no process identity -> nothing owned to stop.
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE)
    res = _life(FakeSystem().system, tmp_path).stop(comp)
    assert res.outcome.value == "already_stopped" and res.ok


def test_stop_no_matching_process(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     process=ProcessSpec(exec_name="nope"))
    res = _life(FakeSystem().system, tmp_path).stop(comp)
    assert res.outcome.value == "already_stopped" and res.ok


def test_logs_tails_component_log(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("\n".join(f"line{i}" for i in range(10)) + "\n")
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     log_paths=(str(log),))
    path, tail = _life(FakeSystem().system, tmp_path).logs(comp, lines=3)
    assert path == str(log) and tail == ["line7", "line8", "line9"]


def _start_log_comp():
    return Component(id="c", name="c", kind=ComponentKind.SERVICE)


def test_start_writes_a_band_suffixed_log_only_when_banded(tmp_path):
    # The `no run_argv` early return surfaces the resolved log path without spawning anything.
    from lhpc.core.model import Stack
    life = _life(FakeSystem().system, tmp_path)
    comp = _start_log_comp()
    stack = Stack(id="s", name="s", components=(comp,))
    banded = life.start(stack, comp, band="868")
    assert not banded.ok and banded.log_path.endswith("logs/start-c-868.log")
    plain = life.start(stack, comp)                            # band-agnostic -> legacy name
    assert not plain.ok and plain.log_path.endswith("logs/start-c.log")


def test_start_log_resolves_exact_band_then_newest_then_legacy(tmp_path):
    # The daemon runs one instance PER BAND at once, so each band gets its own captured log.
    # A band-less reader (`lhpc logs`, the GUI "logs" link) has no band to offer and must still
    # find something — the newest band's log — before falling back to the pre-rename name.
    import os
    life = _life(FakeSystem().system, tmp_path)
    comp = _start_log_comp()
    d = tmp_path / "logs"
    d.mkdir(parents=True)
    assert life.start_log(comp) is None                       # nothing at all

    (d / "start-c.log").write_text("legacy\n")
    assert life.start_log(comp) == d / "start-c.log"          # 3. legacy fallback

    (d / "start-c-433.log").write_text("433\n")
    (d / "start-c-868.log").write_text("868\n")
    os.utime(d / "start-c-433.log", (1000, 1000))             # make 868 the newest
    os.utime(d / "start-c-868.log", (2000, 2000))
    assert life.start_log(comp) == d / "start-c-868.log"      # 2. newest band, not the legacy
    assert life.start_log(comp, "433") == d / "start-c-433.log"   # 1. exact band wins


def test_start_log_skips_symlinked_band_entries(tmp_path):
    life = _life(FakeSystem().system, tmp_path)
    comp = _start_log_comp()
    d = tmp_path / "logs"
    d.mkdir(parents=True)
    (d / "start-c.log").write_text("legacy\n")
    (d / "start-c-868.log").symlink_to("start-c.log")
    assert life.start_log(comp) == d / "start-c.log"          # symlinked band entry ignored


def test_logs_reads_the_band_suffixed_start_log(tmp_path):
    life = _life(FakeSystem().system, tmp_path)
    comp = _start_log_comp()
    d = tmp_path / "logs"
    d.mkdir(parents=True)
    (d / "start-c-868.log").write_text("a\nb\n")
    path, tail = life.logs(comp, lines=5, band="868")
    assert path == str(d / "start-c-868.log") and tail == ["a", "b"]
    # still finds a legacy band-less log when that is all there is (pre-upgrade process)
    (d / "start-c-868.log").unlink()
    (d / "start-c.log").write_text("legacy\n")
    path, tail = life.logs(comp, lines=5)
    assert path == str(d / "start-c.log") and tail == ["legacy"]


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
