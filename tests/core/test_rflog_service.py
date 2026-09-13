"""The RF-log service behind the log page and `lhpc rflog`: the band-less switch (one stack's
and every stack's at once) and its restart marker, the band row and stack row, the two-segment
tail, the scoped Clear and Clear all, and the meshtastic retention exception. Everything is
authorized by the registry, never by a file name."""

from __future__ import annotations

import os

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import rflog
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _store(svc, sid, band=""):
    return cfgmod.load_stack_config(svc._paths, sid, band)


def _live(monkeypatch, svc, sid, band):
    monkeypatch.setattr(type(svc), "stack_running", lambda self, s: s == sid)
    monkeypatch.setattr(type(svc), "running_band", lambda self, s, d="": band if s == sid else d)


def _logs(tmp_path, name, text=""):
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text)
    return d / name


# ---- the band-less switch ---------------------------------------------------------------

def test_a_banded_save_changes_only_the_bandless_store(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"rx_only": "on"}, band="433").ok
    banded = cfgmod._stack_config_path(svc._paths, "kiss", "433").read_text()
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="433").ok
    assert _store(svc, "kiss")["rf_log"] == "off"
    assert cfgmod._stack_config_path(svc._paths, "kiss", "433").read_text() == banded


def test_cli_and_rflog_paths_store_the_same_value_in_the_same_file(tmp_path):
    a, b = _svc(tmp_path / "a"), _svc(tmp_path / "b")
    assert a.save_config_bundle("kiss", values={"rf_log": "off"}).ok       # lhpc config kiss rf_log off
    assert b.set_rflog("graywolf", "off").ok                                # the graywolf log page
    pa, pb = cfgmod._stack_config_path(a._paths, "kiss", ""), cfgmod._stack_config_path(b._paths, "kiss", "")
    assert pa.read_text() == pb.read_text() and _store(b, "kiss")["rf_log"] == "off"
    assert "rf_log" not in _store(b, "graywolf")                            # never the proxy's store
    assert not cfgmod._stack_config_path(b._paths, "graywolf", "").exists()


def test_the_switch_survives_a_band_change(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="433").ok
    assert svc.stack_config("kiss", "868")["rf_log"] == "off"
    # The resolver is what spawn reads per band; there is no public per-band accessor for a
    # single param, and the line above already proves the store through the public view.
    assert svc._resolved_param_value("kiss", "run", "loraham-kiss-tnc", "rf_log", "868") == "off"
    assert svc.rflog_switch("graywolf")["value"] == "off"


def test_on_is_the_default_and_clears_the_key(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}).ok
    assert svc.save_config_bundle("kiss", values={"rf_log": "on"}).ok
    assert "rf_log" not in _store(svc, "kiss")                              # no third state
    assert svc.rflog_switch("graywolf")["value"] == "on"


def test_a_file_param_owner_stores_its_file_key_bandless(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("meshcore", values={"file_rf_log": "off"}).ok
    assert _store(svc, "meshcore")["file_rf_log"] == "off"
    assert svc.file_config_values("meshcore")["rf_log"] == "off"
    assert svc.rflog_switch("meshcore")["value"] == "off"


def test_the_daemon_switch_is_read_per_band_at_spawn(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("daemon", values={"rf_log": "off"}).ok
    for band in ("433", "868"):
        assert svc._resolved_param_value("daemon", "run", "loraham-daemon", "rf_log", band) == "off"


def test_a_bad_value_is_refused(tmp_path):
    svc = _svc(tmp_path)
    assert not svc.set_rflog("graywolf", "maybe").ok
    assert not svc.set_rflog("chat", "off").ok
    assert "rf_log" not in _store(svc, "kiss") and "rf_log" not in _store(svc, "graywolf")


# ---- the restart marker -----------------------------------------------------------------

def test_a_cross_band_edit_of_the_switch_marks_the_live_band(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "868")
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="433").ok
    m = svc.restart_required("kiss")
    assert m is not None and m["params"] == ["rf_log"] and m["band"] == "868"
    assert svc.rflog_switch("graywolf")["restart_required"] is True


def test_a_mixed_cross_band_save_names_only_the_bandless_param(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "868")
    assert svc.save_config_bundle("kiss", values={"rf_log": "off", "rx_only": "on"}, band="433").ok
    m = svc.restart_required("kiss")
    assert m is not None and m["params"] == ["rf_log"] and m["band"] == "868"


def test_a_same_band_save_marks_every_changed_restart_param(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "868")
    assert svc.save_config_bundle("kiss", values={"rf_log": "off", "rx_only": "on"}, band="868").ok
    m = svc.restart_required("kiss")
    assert m is not None and m["params"] == ["rf_log", "rx_only"] and m["band"] == "868"


def test_a_cross_band_edit_of_a_banded_param_alone_marks_nothing(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "868")
    assert svc.save_config_bundle("kiss", values={"rx_only": "on"}, band="433").ok
    assert svc.restart_required("kiss") is None


# ---- the viewer --------------------------------------------------------------------------

def test_the_viewer_tails_both_segments_as_one(tmp_path):
    svc = _svc(tmp_path)
    _logs(tmp_path, "rf-kiss.log.1", "".join(f"old {i}\n" for i in range(200)))
    _logs(tmp_path, "rf-kiss.log", "".join(f"new {i}\n" for i in range(220)))
    path, lines = svc.log_tail("loraham-kiss-tnc", 300, job="rf-kiss.log")
    assert path.endswith("/logs/rf-kiss.log") and len(lines) == 300
    assert lines[:80] == [f"old {i}" for i in range(120, 200)]
    assert lines[80:] == [f"new {i}" for i in range(220)]
    os.unlink(tmp_path / "logs" / "rf-kiss.log.1")
    assert svc.log_tail("loraham-kiss-tnc", 300, job="rf-kiss.log")[1] == [f"new {i}" for i in range(220)]


def _declared(svc, surface):
    return {b for c in svc.stack(surface).components for b in (c.bands or ([c.band] if c.band else []))}


@pytest.mark.parametrize("band", ControllerService.RADIO_BANDS)
def test_the_stack_row_is_the_registry_for_that_band(tmp_path, band):
    """Every band in the band row; in the stack row the daemon's file for the band plus every
    registered stack whose manifest components declare it, in registry order — nothing
    hand-typed here, the registry and the manifest are the expectation."""
    svc = _svc(tmp_path)
    sw = svc.rflog_switcher("rf-kiss.log", band)
    assert sw["band"] == band
    assert [(b["band"], b["current"]) for b in sw["bands"]] == [(b, b == band) for b in ControllerService.RADIO_BANDS]
    expected = [(e.writer, e.job(band) if e.banded else e.job(), e.surface) for e in rflog.REGISTRY
                if e.banded or band in _declared(svc, e.surface)]
    assert [(s["target"], s["job"], s["surface"]) for s in sw["stacks"]] == expected
    assert [s["surface"] for s in sw["stacks"] if s["current"]] == ["graywolf"]
    assert [s["label"] for s in sw["stacks"] if s["surface"] == "daemon"] == [f"daemon {band}"]
    assert svc.rflog_switch("chat") is None
    assert svc.rflog_job("loraham-kiss-tnc", "rf-kiss.log")["surface"] == "graywolf"
    assert svc.rflog_job("loraham-daemon", "rf-kiss.log") is None        # not this writer's
    assert svc.rflog_job("loraham-kiss-tnc", "rf-made-up.log") is None


def test_the_shown_band_is_the_arg_else_the_files_band_else_the_stacks_own(tmp_path):
    svc = _svc(tmp_path)
    assert svc.rflog_switcher("rf-daemon-868.log")["band"] == "868"            # the file's band
    assert svc.rflog_switcher("rf-daemon-868.log", "433")["band"] == "433"     # the arg wins
    assert svc.rflog_switcher("rf-daemon-868.log", "900")["band"] == "868"     # an invalid arg is ignored
    primary = next(c.band for c in svc.stack("meshtastic").components if c.band)
    assert svc.rflog_switcher("rf-meshtastic.log")["band"] == primary          # the stack's own band
    assert [s["current"] for s in svc.rflog_switcher("rf-daemon-868.log", "433")["stacks"]] == \
        [False] * len(svc.rflog_switcher("rf-daemon-868.log", "433")["stacks"])   # the 868 file is not on 433


# ---- every stack at once -----------------------------------------------------------------

def test_set_rflog_all_writes_every_owner_once_and_marks_each_running_one(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "868")
    saved = []
    real = ControllerService.save_config_bundle
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, target, **kw: saved.append(target) or real(self, target, **kw))
    res = svc.set_rflog_all("off")
    owners = list(dict.fromkeys(e.owner for e in rflog.REGISTRY))
    assert res.ok and saved == owners and len(res.details) == len(owners)
    for e in rflog.REGISTRY:
        assert _store(svc, e.owner)[e.key] == "off"
    assert svc.restart_required("kiss")["params"] == ["rf_log"]
    assert svc.restart_required("daemon") is None                              # not running: no marker
    bad = svc.set_rflog_all("maybe")
    assert not bad.ok and bad.data["reason"] == "invalid-choice" and saved == owners


def test_logging_state_is_on_off_or_mixed(tmp_path):
    svc = _svc(tmp_path)
    assert svc.rflog_logging_state() == "on"                                    # every default
    assert svc.set_rflog_all("off").ok and svc.rflog_logging_state() == "off"
    assert svc.set_rflog("graywolf", "on").ok and svc.rflog_logging_state() == "mixed"
    assert svc.set_rflog_all("on").ok and svc.rflog_logging_state() == "on"


# ---- Clear -------------------------------------------------------------------------------

def test_clear_is_scoped_to_one_job(tmp_path):
    svc = _svc(tmp_path)
    a = _logs(tmp_path, "rf-kiss.log", "a\n")
    a1 = _logs(tmp_path, "rf-kiss.log.1", "old\n")
    b = _logs(tmp_path, "rf-meshcom.log", "b\n")
    inode = os.stat(a).st_ino
    assert svc.rflog_clear("loraham-kiss-tnc", "rf-kiss.log").ok
    assert a.read_text() == "" and os.stat(a).st_ino == inode          # truncated in place
    assert not a1.exists() and b.read_text() == "b\n"


@pytest.mark.parametrize("target,job", [
    ("loraham-kiss-tnc", "rf-made-up.log"),        # not in the registry — no prefix semantics
    ("loraham-daemon", "rf-kiss.log"),             # a registered job, but not this writer's
    ("loraham-kiss-tnc", "../rf-kiss.log"),
    ("loraham-kiss-tnc", "rf-kiss.txt"),
    ("loraham-kiss-tnc", ""),
])
def test_clear_refuses_anything_outside_the_registry(tmp_path, target, job):
    svc = _svc(tmp_path)
    f = _logs(tmp_path, "rf-kiss.log", "keep\n")
    made_up = _logs(tmp_path, "rf-made-up.log", "keep\n")
    assert not svc.rflog_clear(target, job).ok
    assert f.read_text() == "keep\n" and made_up.read_text() == "keep\n"


def test_clear_all_clears_every_registry_job_and_nothing_else(tmp_path):
    svc = _svc(tmp_path)
    jobs = [j for e in rflog.REGISTRY for _b, j in e.jobs]
    live = [_logs(tmp_path, j, "x\n") for j in jobs]
    prev = [_logs(tmp_path, j + ".1", "old\n") for j in jobs]
    keep = [_logs(tmp_path, n, "keep\n") for n in ("rf-made-up.log", "start-loraham-daemon-433.log")]
    inodes = [os.stat(p).st_ino for p in live]
    res = svc.rflog_clear_all()
    assert res.ok and len(res.details) == len(jobs)
    assert all(p.read_text() == "" for p in live) and [os.stat(p).st_ino for p in live] == inodes
    assert not any(p.exists() for p in prev) and all(p.read_text() == "keep\n" for p in keep)


def test_clear_all_reports_the_one_job_it_could_not_clear(tmp_path):
    svc = _svc(tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_text("precious\n")
    kiss = _logs(tmp_path, "rf-kiss.log", "x\n")
    os.symlink(outside, tmp_path / "logs" / "rf-meshcom.log")
    res = svc.rflog_clear_all()
    assert not res.ok and outside.read_text() == "precious\n" and kiss.read_text() == ""


def test_clear_never_truncates_through_a_symlink(tmp_path):
    svc = _svc(tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_text("precious\n")
    (tmp_path / "logs").mkdir()
    os.symlink(outside, tmp_path / "logs" / "rf-kiss.log")
    _logs(tmp_path, "rf-kiss.log.1", "old\n")
    assert not svc.rflog_clear("loraham-kiss-tnc", "rf-kiss.log").ok
    assert outside.read_text() == "precious\n"
    assert (tmp_path / "logs" / "rf-kiss.log.1").exists()              # nothing deleted either


# ---- meshtastic: the native trace is rolled opportunistically ---------------------------------

def test_meshtastic_trace_is_rolled_on_read_keeping_the_last_5mb(tmp_path):
    svc = _svc(tmp_path)
    line = ("{" + "x" * 98 + "}\n").encode()                          # 100 bytes
    p = _logs(tmp_path, "rf-meshtastic.log")
    with open(p, "wb") as f:
        for i in range(70_000):                                         # 7 MB
            f.write(b"%06d" % i + line[6:])
    inode = os.stat(p).st_ino
    path, lines = svc.log_tail("meshtastic", 300, job="rf-meshtastic.log")
    prev = tmp_path / "logs" / "rf-meshtastic.log.1"
    assert os.stat(p).st_size == 0 and os.stat(p).st_ino == inode       # same inode: the node keeps appending
    assert 0 < prev.stat().st_size <= rflog.MAX_BYTES
    text = prev.read_bytes()
    assert text.endswith(b"069999" + line[6:]) and text.startswith(b"0")  # whole lines, the tail
    assert len(lines) == 300 and lines[-1].startswith("069999")          # history still shown


def test_meshtastic_clear_only_empties(tmp_path):
    svc = _svc(tmp_path)
    p = _logs(tmp_path, "rf-meshtastic.log", "x" * (rflog.MAX_BYTES + 10))
    _logs(tmp_path, "rf-meshtastic.log.1", "old\n")
    assert svc.rflog_clear("meshtastic", "rf-meshtastic.log").ok
    assert p.stat().st_size == 0 and not (tmp_path / "logs" / "rf-meshtastic.log.1").exists()


# ---- the CLI's resolver --------------------------------------------------------------------

def test_cli_resolver_requires_the_band_only_for_the_daemon(tmp_path):
    svc = _svc(tmp_path)
    _logs(tmp_path, "rf-kiss.log", "k\n")
    with pytest.raises(ValueError, match="--band"):
        svc.rflog_tail("daemon")
    with pytest.raises(ValueError, match="--band"):
        svc.rflog_tail("graywolf", "433")
    with pytest.raises(ValueError):
        svc.rflog_tail("chat")
    assert svc.rflog_tail("graywolf") == (str(tmp_path / "logs" / "rf-kiss.log"), ["k"])
    assert svc.rflog_tail("daemon", "868") == ("", [])                  # no file yet
    _logs(tmp_path, "rf-daemon-868.log", "d\n")
    assert svc.rflog_tail("daemon", "868")[0].endswith("rf-daemon-868.log")


# ---- persistence and the roll lock -----------------------------------------------------------

def test_generic_log_pruning_never_touches_a_registered_rf_log(tmp_path):
    """RF logs are persistent by contract: their writer keeps the descriptor open and they must
    outlive every job log. The generic prune used to count them in its budget and delete the
    oldest — the writer kept writing to an unlinked inode while the viewer showed nothing."""
    svc = _svc(tmp_path)
    svc.LOG_RETENTION = 5
    rf = _logs(tmp_path, "rf-kiss.log", "old frame\n")
    prev = _logs(tmp_path, "rf-kiss.log.1", "older\n")
    os.utime(rf, (1, 1))                                   # the oldest file of all
    for i in range(40):
        (tmp_path / "logs" / f"build-x-{i:02d}.log").write_text("x")
    removed = svc.prune_logs()
    assert removed > 0
    assert rf.read_text() == "old frame\n" and prev.read_text() == "older\n"
    remaining = sorted(p.name for p in (tmp_path / "logs").glob("build-x-*.log"))
    assert len(remaining) <= 5                             # the budget still applies to job logs
    assert (tmp_path / "logs" / "rf-made-up.log").exists() is False


def test_concurrent_native_rolls_keep_the_retained_segment(tmp_path):
    """Two controller processes (or workers) can both pass the size pre-check. Without one lock
    and a re-check under it, the second copies the just-truncated live file over `.1` and the
    5 MB of history are gone. Two racing rollers must leave `.1` holding the tail."""
    import threading
    svc = _svc(tmp_path)
    p = _logs(tmp_path, "rf-meshtastic.log")
    with open(p, "wb") as f:
        for i in range(70_000):
            f.write(b"%06d" % i + b"x" * 93 + b"\n")            # 7 MB
    errors = []

    def roll():
        try:
            svc._rflog_roll_native(svc._rflog_path("rf-meshtastic.log"))
        except Exception as exc:                                # pragma: no cover
            errors.append(exc)
    a, b = threading.Thread(target=roll), threading.Thread(target=roll)
    a.start(); b.start(); a.join(); b.join()
    assert errors == []
    prev = tmp_path / "logs" / "rf-meshtastic.log.1"
    assert 0 < prev.stat().st_size <= rflog.MAX_BYTES
    assert prev.read_bytes().endswith(b"069999" + b"x" * 93 + b"\n")
    assert p.stat().st_size == 0


def test_clear_and_roll_share_the_lock(tmp_path):
    """Clear must never interleave with a roll: with the lock held by a roll in flight, the
    Clear waits and finds the rolled state — it empties the live file and removes `.1`."""
    import fcntl
    import threading
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    p = _logs(tmp_path, "rf-meshcore.log", "a\n")
    _logs(tmp_path, "rf-meshcore.log.1", "old\n")
    # The lock leaf is a cross-process contract (a console worker and the CLI share it by
    # name), so it is spelled here on purpose: a rename must fail this test.
    fh = runtime_fs.open_lock(svc._paths, svc._paths.under("state", "locks", "rflog-rf-meshcore.log.lock"))
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    done = threading.Event()
    result = {}

    def clear():
        result["r"] = svc.rflog_clear("meshcore-node", "rf-meshcore.log")
        done.set()
    threading.Thread(target=clear).start()
    assert not done.wait(0.3)                              # blocked behind the held lock
    assert p.read_text() == "a\n"                          # nothing touched meanwhile
    fh.close()                                             # release
    assert done.wait(5) and result["r"].ok
    assert p.read_text() == "" and not (tmp_path / "logs" / "rf-meshcore.log.1").exists()
