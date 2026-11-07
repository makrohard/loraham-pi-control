"""W217 (found by the maintainer on the Hardware page): two overlapping writes of the hardware list. Two /hardware/add
requests for two distinct sticks both read r1/r2, both chose r3, the last save won, and the messages did not match
the file. Every read-modify-write of config/radios.toml (add, bind, `lhpc hardware <setup>`) now runs whole under the
interprocess config lock, a busy lock is refused with nothing changed, and each save writes a temp file of its own.

No timer decides an order: a writer is held after its read until the other has read too or was refused the config
flock, and a holder PROCESS keeps the lock until the test releases it. Every wait is bounded (DEADLINE); a bound only
ends a broken run, and the holder is killed on any failure."""
import contextlib
import dataclasses
import functools
import os
import select
import subprocess
import sys
import threading

import pytest
import radios_fixture
import repo_paths

from lhpc.core import config, radio_start, radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Field, Probe


@pytest.fixture(autouse=True)
def _chip_found(monkeypatch):
    """DEC-W21: the add checks the stick's chip first; these cases are about the list, so the check answers "found"
    (tests/web/test_w21_add_checks_the_chip.py drives the check itself)."""
    from lhpc.core.radio_detect import run
    monkeypatch.setattr(run, "detect_stick", lambda svc, model, port: run.Detected("found", f"{model}: found", "", ""))

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"     # the Meshstick on 1-1.3, no serial
REAL_SCAN = usb_access.scan
SPI = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))
STICK3 = radios.Radio("r3", "stick", "meshstick-1262", "868", usb_port="1-1.9")
SPARE = radios.Radio("r4", "spare", "meshstick-1262", "868", usb_serial="SPARE0001")
DEADLINE = 20.0
BUSY = "the hardware list is busy (config is busy — a long-running operation holds it; try again shortly); nothing was " \
       "changed — try again"


def _two_sticks(monkeypatch):
    base = REAL_SCAN(STICKS)
    d0 = next(d for d in base.devices if d.port == "1-1.3")
    second = dataclasses.replace(d0, port="1-1.4", serial=Field(Probe.PRESENT, "MSTICK0002"))
    both = dataclasses.replace(base, devices=(*base.devices, second))
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: both)
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


class _Seen:
    """What the writers did, under one condition: the reads of the list, and whether a writer of this process was
    refused the config flock (it is waiting for the lock)."""

    def __init__(self):
        self.cond, self.reads, self.refused = threading.Condition(), 0, False

    def note(self, attr, value):
        with self.cond:
            setattr(self, attr, value)
            self.cond.notify_all()

    def wait(self, pred, what):
        with self.cond:
            assert self.cond.wait_for(lambda: pred(self), timeout=DEADLINE), what


def _watch_flock(monkeypatch):
    """A `_Seen` whose `refused` turns True at the first refused config flock in this process."""
    import fcntl
    seen, real = _Seen(), fcntl.flock

    def flock(fd, op):
        try:
            return real(fd, op)
        except OSError:
            seen.note("refused", True)
            raise
    monkeypatch.setattr(config.fcntl, "flock", flock)
    return seen


def _hold_after_read(monkeypatch):
    """Each of the two writers, after its read of the list, waits until the other has read too (no lock: both read
    the same list) or the other was refused the config lock (the lock: it waits, and reads after this one's save)."""
    seen, real = _watch_flock(monkeypatch), radios.load

    def load(paths):
        state = real(paths)
        with seen.cond:
            seen.reads += 1
            seen.cond.notify_all()
        seen.wait(lambda s: s.reads >= 2 or s.refused, "neither a second read nor a writer waiting for the lock")
        return state
    monkeypatch.setattr(radios, "load", load)
    return real


def _run_all(fns):
    """Start each callable in a thread, join each within DEADLINE; a thread still alive fails the test."""
    ts = [threading.Thread(target=f, daemon=True) for f in fns]
    [t.start() for t in ts]
    [t.join(DEADLINE) for t in ts]
    assert not [t for t in ts if t.is_alive()], "a writer did not finish"


@contextlib.contextmanager
def _holder(tmp_path, left=None):
    """Another PROCESS (as `lhpc hardware` is) holding the config lock until `release()` is called; on release it
    leaves `left`'s list in place first (when given). Its start and its end are bounded; it is killed on failure."""
    code = ("import shutil, sys; from pathlib import Path; from lhpc.core import config; from lhpc.core.paths import "
            "Paths\nwith config.config_lock(Paths(runtime_root=Path(sys.argv[1]))):\n"
            "    print('held', flush=True); sys.stdin.readline()\n"
            "    if sys.argv[2]: shutil.copyfile(sys.argv[2], Path(sys.argv[1]) / 'config' / 'radios.toml')")
    holder = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(left or "")], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, cwd=str(repo_paths.REPO))

    def release():
        if holder.poll() is None and not holder.stdin.closed:
            holder.stdin.write("go\n")
            holder.stdin.close()
        holder.wait(DEADLINE)
    try:
        ready, _w, _x = select.select([holder.stdout], [], [], DEADLINE)
        assert ready and holder.stdout.readline().strip() == "held", "the holder never took the lock"
        yield release
        release()
        assert holder.returncode == 0
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(DEADLINE)
        for pipe in (holder.stdin, holder.stdout):
            if not pipe.closed:
                pipe.close()


def _contended(tmp_path, monkeypatch, call, left=None):
    """`call` run against the holder: it must be refused the lock and still be waiting while the lock is held, then
    finish once the holder has released it. Returns its result."""
    out = {}
    with _holder(tmp_path, left) as release:
        seen = _watch_flock(monkeypatch)
        t = threading.Thread(target=lambda: out.__setitem__("r", call()), daemon=True)
        t.start()
        seen.wait(lambda s: s.refused, "the writer never met the held lock")
        assert "r" not in out, out                                      # still waiting while the lock is held
        release()
        t.join(DEADLINE)
        assert not t.is_alive(), "the writer did not finish after the release"
    return out["r"]


def _left(tmp_path, rows):
    """A list file the holder leaves in place: `rows`, written under `tmp_path/left`."""
    radios_fixture.write(tmp_path / "left", rows)
    return tmp_path / "left" / "config" / "radios.toml"


def _rows(tmp_path, load=radios.load):
    return [(r.id, r.usb_port, r.usb_serial) for r in load(Paths(runtime_root=tmp_path)).radios]


def _flash(page):
    import html
    import re
    return [html.unescape(m).strip() for m in re.findall(r'class="flash[^"]*"[^>]*>(.*?)<', page, re.S)]


# ---- two writers of this process ------------------------------------------------------------------------

def test_two_overlapping_adds_of_distinct_sticks_keep_both_rows_and_true_messages(web, tmp_path, monkeypatch, csrf):
    radios_fixture.write(tmp_path, SPI)
    _two_sticks(monkeypatch)
    clients = [web(), web()]
    tokens = [csrf(c, "/hardware") for c in clients]
    load = _hold_after_read(monkeypatch)
    pages = {}

    def add(i, port):
        c = clients[i]
        c.post("/hardware/add", data={"_csrf": tokens[i], "model": "meshstick-1262", "port": port})
        pages[port] = c.get("/hardware").get_data(as_text=True)
    _run_all([lambda: add(0, "1-1.3"), lambda: add(1, "1-1.4")])
    rows = _rows(tmp_path, load)
    assert [r[0] for r in rows] == ["r1", "r2", "r3", "r4"], rows                 # no row lost
    by_stick = {(r[1] or r[2]): r[0] for r in rows[2:]}
    assert set(by_stick) == {"1-1.3", "MSTICK0002"}, rows                         # both sticks, one row each
    # each page's message is exactly its own stick's, with the row that stick holds in the file
    assert _flash(pages["1-1.3"]) == [f"{by_stick['1-1.3']} added: meshstick-1262 on 868 MHz, port 1-1.3 (no serial)"]
    assert _flash(pages["1-1.4"]) == [f"{by_stick['MSTICK0002']} added: meshstick-1262 on 868 MHz, serial MSTICK0002"]


def test_an_add_overlapping_a_bind_keeps_both_changes(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, (*SPI, STICK3))
    _two_sticks(monkeypatch)
    svc = _svc(tmp_path)
    load = _hold_after_read(monkeypatch)
    out = {}
    _run_all([lambda: out.__setitem__("bind", radio_start.bind_usb(svc, "r3", "1-1.3")),
              lambda: out.__setitem__("add", radio_start.add_usb(svc, "meshstick-1262", "1-1.4"))])
    assert out["bind"][0] and out["add"][0], out
    rows = {r[0]: r[1:] for r in _rows(tmp_path, load)}
    assert list(rows) == ["r1", "r2", "r3", "r4"], rows                           # the add's row is not lost
    assert rows["r3"] == ("1-1.3", "") and rows["r4"] == ("", "MSTICK0002"), rows


# ---- each writer against another process holding the lock ------------------------------------------------

def test_an_add_waits_for_another_process_and_adds_to_the_list_it_left(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, SPI)
    _two_sticks(monkeypatch)
    svc = _svc(tmp_path)
    res = _contended(tmp_path, monkeypatch, lambda: radio_start.add_usb(svc, "meshstick-1262", "1-1.3"),
                     _left(tmp_path, (*SPI, SPARE)))
    assert res == (True, "r3 added: meshstick-1262 on 868 MHz, port 1-1.3 (no serial)", False), res   # lowest free id
    rows = dict((r[0], r[1:]) for r in _rows(tmp_path))
    assert rows == {"r1": ("", ""), "r2": ("", ""), "r3": ("1-1.3", ""), "r4": ("", "SPARE0001")}, rows   # r4 kept


def test_a_bind_waits_for_another_process_and_binds_in_the_list_it_left(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, (*SPI, STICK3))
    _two_sticks(monkeypatch)
    svc = _svc(tmp_path)
    res = _contended(tmp_path, monkeypatch, lambda: radio_start.bind_usb(svc, "r3", "1-1.3"),
                     _left(tmp_path, (*SPI, STICK3, SPARE)))
    assert res[0], res
    assert _rows(tmp_path) == [("r1", "", ""), ("r2", "", ""), ("r3", "1-1.3", ""), ("r4", "", "SPARE0001")]


def test_the_cli_setup_waits_for_another_process_and_keeps_the_list_it_left(tmp_path, monkeypatch):
    # `lhpc hardware <setup>` writes the list only when there is none. There is none when it starts, but the holder
    # leaves one while the setup waits for the lock: the setup must read after the wait and keep that list.
    (tmp_path / "config").mkdir()
    left = _left(tmp_path, (*SPI, SPARE))
    res = _contended(tmp_path, monkeypatch, lambda: radio_start.setup_radios(Paths(runtime_root=tmp_path), "loraham"),
                     left)
    assert res[0], res
    assert (tmp_path / "config" / "radios.toml").read_bytes() == left.read_bytes()


# ---- a busy lock is refused, nothing changed -------------------------------------------------------------

def _busy_lock(monkeypatch):
    """The list lock with no wait: a held lock is refused at once (ConfigLockBusy), not after its 15 s bound."""
    monkeypatch.setattr(radio_start.cfgmod, "config_lock", functools.partial(config.config_lock, timeout=0.0))


def test_a_busy_lock_refuses_an_add_a_bind_and_the_setup_and_changes_nothing(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, (*SPI, STICK3))
    _two_sticks(monkeypatch)
    svc, paths = _svc(tmp_path), Paths(runtime_root=tmp_path)
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    _busy_lock(monkeypatch)
    with _holder(tmp_path):
        assert radio_start.add_usb(svc, "meshstick-1262", "1-1.3") == (False, BUSY, False)
        assert radio_start.bind_usb(svc, "r3", "1-1.3") == (False, f"r3: {BUSY}", False)
        assert radio_start.setup_radios(paths, "loraham") == (False, f"hardware list: not written: {BUSY}")
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before
    assert sorted(p.name for p in (tmp_path / "config").iterdir()) == [".lock", "radios.toml"]


# ---- the save itself -------------------------------------------------------------------------------------

def test_each_save_writes_a_temp_file_of_its_own(tmp_path, monkeypatch):
    seen = []
    real_replace = radios.os.replace
    monkeypatch.setattr(radios.os, "replace", lambda a, b: (seen.append(str(a)), real_replace(a, b))[1])
    radios_fixture.write(tmp_path, SPI)
    for _ in range(2):
        assert radios.save(Paths(runtime_root=tmp_path), SPI) == []
    assert len(set(seen)) == 2 and all(s.endswith(".tmp") and "radios.toml." in s for s in seen), seen
    assert not [p for p in (tmp_path / "config").iterdir() if p.name.endswith(".tmp")]


def test_the_saved_list_is_0644_whatever_the_umask(tmp_path):
    # The mode is set on the held fd (fchmod), as runtime_fs's writers do ("umask-proof"); before W217 the list was
    # created 0644 under the umask (0600 under umask 077). Pinned here so the policy is explicit.
    radios_fixture.write(tmp_path, SPI)
    p = tmp_path / "config" / "radios.toml"
    for mask in (0o077, 0o000):
        old = os.umask(mask)
        try:
            assert radios.save(Paths(runtime_root=tmp_path), SPI) == []
        finally:
            os.umask(old)
        assert oct(os.stat(p).st_mode & 0o777) == oct(0o644), (oct(mask), oct(os.stat(p).st_mode))
