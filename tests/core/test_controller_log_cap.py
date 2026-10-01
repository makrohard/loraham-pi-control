"""A1 part B: the controller's own long-lived logs (the four unit logs and nginx's two) are capped
like a start log from the console's pass, the Meshtastic trace gets the missing unattended roll, and
the pruner never deletes a controller log. Real files; small limits patched in; fake content only."""
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from lhpc.core import rflog, runtime_fs, updater_units, webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

import repo_paths

LINE = b"2026-09-29 10:00:00 INFO filler line for the controller-log cap tests\n"
MAX, KEEP = 5000, 1000


def _svc(tmp_path) -> ControllerService:
    (tmp_path / "logs").mkdir(exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _write(tmp_path, name, n=200) -> Path:
    p = tmp_path / "logs" / name
    p.write_bytes(b"".join(b"%05d " % i + LINE for i in range(n)))
    return p


@pytest.fixture
def small_cap(monkeypatch):
    real = runtime_fs.cap_start_log
    monkeypatch.setattr(runtime_fs, "cap_start_log",
                        lambda paths, path, **k: real(paths, path, max_bytes=MAX, keep_bytes=KEEP))


def test_cap_controller_logs_caps_each_named_log(tmp_path, small_cap):
    svc = _svc(tmp_path)
    for name in updater_units.CONTROLLER_LOGS:
        _write(tmp_path, name)
    out = svc.cap_controller_logs()
    assert out == {name: "capped" for name in updater_units.CONTROLLER_LOGS}
    for name in updater_units.CONTROLLER_LOGS:
        live = tmp_path / "logs" / name
        prev = live.with_name(runtime_fs.prev_log_name(name))
        assert live.stat().st_size == 0
        assert 0 < prev.stat().st_size <= KEEP and prev.read_bytes().endswith(b"00199 " + LINE)


def test_cap_controller_logs_touches_nothing_else(tmp_path, small_cap):
    svc = _svc(tmp_path)
    others = [_write(tmp_path, n) for n in ("rf-kiss.log", "build-x.log", "start-y.log")]
    before = {p: p.read_bytes() for p in others}
    svc.cap_controller_logs()
    assert {p: p.read_bytes() for p in others} == before
    assert sorted(os.listdir(tmp_path / "logs")) == sorted(p.name for p in others)


def test_cap_controller_logs_missing_symlink_and_non_regular(tmp_path, small_cap):
    svc = _svc(tmp_path)
    logs = tmp_path / "logs"
    target = _write(tmp_path, "elsewhere.log")
    (logs / "lhpc-web.log").symlink_to(target)
    os.mkfifo(logs / "nginx-error.log")
    out = svc.cap_controller_logs()
    assert out["lhpc-selfupdate.log"] == "absent"                 # a missing one is skipped
    assert out["lhpc-web.log"].startswith("error")               # a symlink is refused
    assert out["nginx-error.log"].startswith("error")            # a FIFO is refused
    assert target.stat().st_size > MAX                           # nothing followed the link


def test_controller_logs_tuple_matches_the_units():
    """Every `append:` log of the rendered units (updater_units.render) and of deploy/*.service is
    in CONTROLLER_LOGS, and every entry is such a log or one of nginx's two: none can be forgotten."""
    root = Path(repo_paths.REPO)
    pat = re.compile(r"append:\S*/logs/([\w.-]+\.log)")
    canonical = updater_units.deployment_paths("/rt")
    units = set()
    for kind in updater_units.ALL_UNITS:
        units |= set(pat.findall(updater_units.render(kind, *canonical)))
    deploy = set()
    for f in (root / "deploy").glob("*.service"):
        deploy |= set(pat.findall(f.read_text()))
    nginx = {webserver._ERR_LOG[-1], webserver._ACC_LOG[-1]}
    assert units == deploy and units                              # the two sources agree
    assert set(updater_units.CONTROLLER_LOGS) == units | nginx
    assert len(updater_units.CONTROLLER_LOGS) == len(set(updater_units.CONTROLLER_LOGS))


_WRITER = textwrap.dedent("""
    import os, sys
    for i in range(int(sys.argv[1])):
        os.write(1, b"w-%08d\\n" % i)
    sys.stdin.readline()                       # the test says when the cap is done
    os.write(1, b"after-the-cap\\n")
""")


def test_cap_controller_log_with_a_live_o_append_writer(tmp_path, small_cap):
    svc = _svc(tmp_path)
    live = tmp_path / "logs" / "lhpc-web.log"
    with open(live, "ab") as fh:                                  # the unit's append: descriptor
        proc = subprocess.Popen([sys.executable, "-c", _WRITER, "1000"], stdout=fh,
                                stdin=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while live.stat().st_size < 11 * 1000:
            assert proc.poll() is None and time.monotonic() < deadline, "the writer never filled the log"
            time.sleep(0.01)
        assert svc.cap_controller_logs()["lhpc-web.log"] == "capped"
        proc.stdin.write(b"go\n")
    finally:
        proc.stdin.close()                                        # EOF also releases the writer
        proc.wait(timeout=30)
    data = live.read_bytes()
    assert data == b"after-the-cap\n"                             # the next write lands at offset 0
    assert b"\0" not in data                                      # no hole


def test_rflog_roll_native_all_rolls_the_trace_over_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(rflog, "MAX_BYTES", MAX)
    svc = _svc(tmp_path)
    trace = _write(tmp_path, "rf-meshtastic.log")
    svc.rflog_roll_native_all()
    one = trace.with_name(rflog.previous(trace.name))
    assert trace.stat().st_size == 0
    assert 0 < one.stat().st_size <= MAX and one.read_bytes().endswith(b"00199 " + LINE)


def test_rflog_roll_native_all_leaves_it_below_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(rflog, "MAX_BYTES", MAX)
    svc = _svc(tmp_path)
    trace = _write(tmp_path, "rf-meshtastic.log", n=10)
    before = trace.read_bytes()
    svc.rflog_roll_native_all()
    assert trace.read_bytes() == before
    assert not trace.with_name(rflog.previous(trace.name)).exists()


def test_rflog_roll_native_all_ignores_non_native_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(rflog, "MAX_BYTES", MAX)
    svc = _svc(tmp_path)
    kiss = _write(tmp_path, "rf-kiss.log")                        # oversized, NOT native
    one = kiss.with_name(rflog.previous(kiss.name))
    one.write_bytes(b"the writer's own older segment\n")
    before = (kiss.read_bytes(), one.read_bytes())
    svc.rflog_roll_native_all()
    assert (kiss.read_bytes(), one.read_bytes()) == before


def test_prune_logs_never_deletes_a_controller_log(tmp_path):
    svc = _svc(tmp_path)
    logs = tmp_path / "logs"
    old = 1_000_000_000
    for name in updater_units.CONTROLLER_LOGS:                    # the OLDEST files
        _write(tmp_path, name, n=2)
        os.utime(logs / name, (old, old))
    prev = _write(tmp_path, "lhpc-web.prev.log", n=2)             # a capped half: an ordinary log
    os.utime(prev, (old, old))
    for i in range(svc.LOG_RETENTION + 5):                        # budget pressure by count
        p = _write(tmp_path, f"build-{i:04d}.log", n=1)
        os.utime(p, (old + 1000 + i, old + 1000 + i))
    svc.prune_logs()
    for name in updater_units.CONTROLLER_LOGS:
        assert (logs / name).exists(), name
    assert not prev.exists()                                      # its .prev.log ages out


def test_controller_log_tail_after_a_cap_shows_the_new_content(tmp_path):
    svc = _svc(tmp_path)
    live = _write(tmp_path, "lhpc-web.log")
    assert runtime_fs.cap_start_log(Paths(runtime_root=tmp_path), live,
                                    max_bytes=MAX, keep_bytes=KEEP) == "capped"   # part A's cut
    with open(live, "ab") as fh:
        fh.write(b"a line after the cap\n")
    _path, lines = svc.controller_log_tail("web", 10)
    assert [ln.rstrip("\n") for ln in lines] == ["a line after the cap"]


def test_webserver_log_tail_after_a_cap_shows_the_new_content(tmp_path):
    svc = _svc(tmp_path)
    live = _write(tmp_path, "nginx-access.log")
    assert runtime_fs.cap_start_log(Paths(runtime_root=tmp_path), live,
                                    max_bytes=MAX, keep_bytes=KEEP) == "capped"   # part A's cut
    with open(live, "ab") as fh:
        fh.write(b"a request after the cap\n")
    _path, lines = svc.webserver_log_tail("access", 10)
    assert [ln.rstrip("\n") for ln in lines] == ["a request after the cap"]
