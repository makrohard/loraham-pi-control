"""W217 (found by the maintainer on the Hardware page): two overlapping writes of the hardware list. Two /hardware/add
requests for two distinct sticks both read r1/r2, both chose r3, the last save won, and the messages did not match
the file. Every read-modify-write of config/radios.toml now runs whole under the interprocess config lock, and each
save writes a temp file of its own. Each writer here is held after its read until the other has read too or was
refused the config flock, so without the lock both read the same list; with it, the second waits and reads the first
one's row. No timer decides the order."""
import pytest
import dataclasses
import threading

import radios_fixture
import repo_paths

from lhpc.core import radio_start, radios, usb_access
from lhpc.core.paths import Paths
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


def _two_sticks(monkeypatch):
    base = REAL_SCAN(STICKS)
    d0 = next(d for d in base.devices if d.port == "1-1.3")
    second = dataclasses.replace(d0, port="1-1.4", serial=Field(Probe.PRESENT, "MSTICK0002"))
    both = dataclasses.replace(base, devices=(*base.devices, second))
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: both)
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)


class _Seen:
    """What the writers did, under one condition: the reads of the list, and whether a writer of this process was
    refused the config flock (it is waiting for the lock). No timer decides; the 10 s bound only ends a broken run."""

    def __init__(self):
        self.cond, self.reads, self.refused = threading.Condition(), 0, False

    def note(self, attr, value):
        with self.cond:
            setattr(self, attr, value)
            self.cond.notify_all()

    def wait(self, pred, what):
        with self.cond:
            assert self.cond.wait_for(lambda: pred(self), timeout=10), what


def _watch_flock(monkeypatch):
    """A `_Seen` whose `refused` turns True at the first refused config flock in this process."""
    import fcntl

    from lhpc.core import config
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


def _rows(tmp_path, load):
    return [(r.id, r.usb_port, r.usb_serial) for r in load(Paths(runtime_root=tmp_path)).radios]


def _flash(page):
    import html
    import re
    return [html.unescape(m) for m in re.findall(r'class="flash[^"]*"[^>]*>(.*?)<', page, re.S)]


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
        pages[port] = " ".join(c.get("/hardware").get_data(as_text=True).split())
    ts = [threading.Thread(target=add, args=(0, "1-1.3")), threading.Thread(target=add, args=(1, "1-1.4"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rows = _rows(tmp_path, load)
    assert [r[0] for r in rows] == ["r1", "r2", "r3", "r4"], rows                 # no row lost
    assert sorted(rows) == [("r1", "", ""), ("r2", "", ""), ("r3", *rows[2][1:]), ("r4", *rows[3][1:])]
    assert {r[1] or r[2] for r in rows[2:]} == {"1-1.3", "MSTICK0002"}            # both sticks, one row each
    for r in rows[2:]:                                                             # each message names its own row
        port = r[1] or "1-1.4"
        assert f"{r[0]} added" in pages[port] and "unchanged" not in pages[port], pages[port][:400]


def test_an_add_overlapping_a_bind_keeps_both_changes(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    radios_fixture.write(tmp_path, (*SPI, radios.Radio("r3", "stick", "meshstick-1262", "868", usb_port="1-1.9")))
    _two_sticks(monkeypatch)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    load = _hold_after_read(monkeypatch)
    out = {}
    ts = [threading.Thread(target=lambda: out.__setitem__("bind", radio_start.bind_usb(svc, "r3", "1-1.3"))),
          threading.Thread(target=lambda: out.__setitem__("add", radio_start.add_usb(svc, "meshstick-1262", "1-1.4")))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out["bind"][0] and out["add"][0], out
    rows = {r[0]: r[1:] for r in _rows(tmp_path, load)}
    assert list(rows) == ["r1", "r2", "r3", "r4"], rows                           # the add's row is not lost
    assert rows["r3"] == ("1-1.3", "") and rows["r4"] == ("", "MSTICK0002"), rows


def test_the_cli_writer_waits_for_a_console_write_and_never_overwrites_it(tmp_path, monkeypatch):
    # `lhpc hardware <setup>` writes the list only when there is none; a console add landing first makes it keep the
    # existing list, never replace it.
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    radios_fixture.write(tmp_path, SPI)
    _two_sticks(monkeypatch)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ok, msg, _box = radio_start.add_usb(svc, "meshstick-1262", "1-1.4")
    assert ok, msg
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    assert radio_start.setup_radios(Paths(runtime_root=tmp_path), "loraham")[0]
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before
    # Another PROCESS (as `lhpc hardware` is) holds the config lock and, while holding it, leaves a list with one row
    # more. The console add is refused the lock (a refused flock, seen in this process), waits, then reads the list
    # that process left and adds its row to it — the interprocess lock, not a thread lock. The test releases the
    # holder; no timer decides the order.
    import subprocess
    import sys
    left = tmp_path / "left"
    radios_fixture.write(left, (*radios.load(Paths(runtime_root=tmp_path)).radios,
                                radios.Radio("r4", "spare", "meshstick-1262", "868", usb_serial="SPARE0001")))
    holder = subprocess.Popen(
        [sys.executable, "-c", "import shutil, sys; from pathlib import Path; from lhpc.core import config; "
         "from lhpc.core.paths import Paths\n"
         "with config.config_lock(Paths(runtime_root=Path(sys.argv[1]))):\n"
         "    print('held', flush=True); sys.stdin.readline()\n"
         "    shutil.copyfile(sys.argv[2], Path(sys.argv[1]) / 'config' / 'radios.toml')",
         str(tmp_path), str(left / "config" / "radios.toml")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=str(repo_paths.REPO))
    out = {}
    try:
        assert holder.stdout.readline().strip() == "held"
        seen = _watch_flock(monkeypatch)
        adder = threading.Thread(target=lambda: out.__setitem__(
            "add", radio_start.add_usb(svc, "meshstick-1262", "1-1.3")))
        adder.start()
        seen.wait(lambda s: s.refused, "the add never met the held lock")
        assert "add" not in out, out                                    # still waiting while the lock is held
    finally:
        holder.stdin.write("go\n")
        holder.stdin.close()
        holder.wait(10)
        holder.stdout.close()
    assert holder.returncode == 0
    adder.join(20)
    assert out["add"] == (True, "r5 added: meshstick-1262 on 868 MHz, port 1-1.3 (no serial)", False), out
    rows = {r.id: (r.usb_port, r.usb_serial) for r in radios.load(Paths(runtime_root=tmp_path)).radios}
    assert list(rows) == ["r1", "r2", "r3", "r4", "r5"], rows                     # the holder's r4 is kept
    assert rows["r4"] == ("", "SPARE0001") and rows["r5"] == ("1-1.3", ""), rows


def test_each_save_writes_a_temp_file_of_its_own(tmp_path, monkeypatch):
    seen = []
    real_replace = radios.os.replace
    monkeypatch.setattr(radios.os, "replace", lambda a, b: (seen.append(str(a)), real_replace(a, b))[1])
    radios_fixture.write(tmp_path, SPI)
    for _ in range(2):
        assert radios.save(Paths(runtime_root=tmp_path), SPI) == []
    assert len(set(seen)) == 2 and all(s.endswith(".tmp") and "radios.toml." in s for s in seen), seen
    assert not [p for p in (tmp_path / "config").iterdir() if p.name.endswith(".tmp")]
