"""The RF-log service behind the log page and `lhpc rflog`: the band-less switch (one stack's
and every stack's at once) and its restart marker, the band row and stack row, the two-segment
tail, the scoped Clear and Clear all, and the meshtastic retention exception. Everything is
authorized by the registry, never by a file name."""

from __future__ import annotations

import os

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import rflog
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _store(svc, sid, rid=""):
    # K6: an instance's own file; no rid: the global <sid>.toml
    return cfgmod.load_stack_config(svc._paths, sid, inst=Instance(rid) if rid else None)


def _live(monkeypatch, svc, sid, rid):
    # the instance `sid@rid` runs (its own process; K6: a save of an instance asks about that instance)
    from lhpc.core import instances
    real = instances.instance_runs
    monkeypatch.setattr(instances, "instance_runs",
                        lambda s, c, i, b="": (getattr(i, "rid", "") == rid and s.stack_of(c.id) == sid) or (
                            real(s, c, i, b) if getattr(i, "rid", "") != rid else False))


def _logs(tmp_path, name, text=""):
    d = tmp_path / "logs"
    (d / name).parent.mkdir(parents=True, exist_ok=True)              # a radio's `<rid>/` file too
    (d / name).write_text(text)
    return d / name


# ---- the switch, per instance (K6, DEC-W384k) ---------------------------------------------

def test_a_band_callers_switch_save_is_its_instances_own(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"rx_only": "on"}, band="433").ok
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="433").ok
    assert _store(svc, "kiss", "r1") == {"rx_only": "on", "rf_log": "off"}          # kiss@r1's own file, both
    assert "rf_log" not in _store(svc, "kiss", "r2") and "rf_log" not in _store(svc, "kiss")


def test_cli_and_rflog_paths_set_every_instance_in_the_same_files(tmp_path):
    a, b = _svc(tmp_path / "a"), _svc(tmp_path / "b")
    assert a.set_rflog("graywolf", "off").ok                                 # lhpc rflog graywolf off (kiss's store)
    assert b.set_rflog("graywolf", "off").ok                                 # the graywolf log page
    for rid in ("r1", "r2"):
        pa = cfgmod._stack_config_path(a._paths, "kiss", Instance(rid))
        pb = cfgmod._stack_config_path(b._paths, "kiss", Instance(rid))
        assert pa.read_text() == pb.read_text() and _store(b, "kiss", rid)["rf_log"] == "off"
    assert all("rf_log" not in _store(b, "graywolf", rid) for rid in ("", "r1", "r2"))   # never the proxy's store


def test_each_instance_has_its_own_switch(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="433").ok    # kiss@r1 alone
    assert svc.stack_config("kiss", "433")["rf_log"] == "off" and svc.stack_config("kiss", "868")["rf_log"] == "on"
    assert svc._resolved_param_value("kiss", "run", "loraham-kiss-tnc", "rf_log", "868") == "on"
    assert svc.rflog_switch("graywolf")["value"] == "mixed"
    assert svc.set_rflog("graywolf", "off").ok and svc.rflog_switch("graywolf")["value"] == "off"


def test_on_is_the_default_and_clears_the_key(tmp_path):
    svc = _svc(tmp_path)
    assert svc.set_rflog("graywolf", "off").ok
    assert svc.set_rflog("graywolf", "on").ok
    assert all("rf_log" not in _store(svc, "kiss", rid) for rid in ("r1", "r2"))   # no third state
    assert svc.rflog_switch("graywolf")["value"] == "on"


def test_a_file_param_owner_stores_its_file_key_in_its_instances_file(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("meshcore", values={"file_rf_log": "off"}).ok
    assert _store(svc, "meshcore", "r2")["file_rf_log"] == "off"                 # meshcore@r2 (868)
    assert svc.file_config_values("meshcore")["rf_log"] == "off"
    assert svc.rflog_switch("meshcore")["value"] == "off"


def test_the_daemon_switch_is_read_per_radio_at_spawn(tmp_path):
    svc = _svc(tmp_path)
    assert svc.set_rflog("daemon", "off").ok                                     # every radio's daemon
    for band in ("433", "868"):
        assert svc._resolved_param_value("daemon", "run", "loraham-daemon", "rf_log", band) == "off"


def test_a_bad_value_is_refused(tmp_path):
    svc = _svc(tmp_path)
    assert not svc.set_rflog("graywolf", "maybe").ok
    assert not svc.set_rflog("chat", "off").ok
    assert all("rf_log" not in _store(svc, s, rid) for s in ("kiss", "graywolf") for rid in ("", "r1", "r2"))


# ---- the restart marker, per instance ------------------------------------------------------

def test_a_live_instances_switch_edit_marks_that_instance(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "r2")
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="868").ok    # kiss@r2's own switch
    m = svc.restart_required("kiss@r2")
    assert m is not None and m["params"] == ["rf_log"] and m["band"] == "868"
    assert svc.rflog_switch("graywolf")["restart_required"] is True


def test_another_instances_edit_marks_nothing(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "r2")
    assert svc.save_config_bundle("kiss", values={"rf_log": "off", "rx_only": "on"}, band="433").ok   # kiss@r1
    assert svc.restart_required("kiss") is None


def test_a_live_instances_save_marks_every_changed_restart_param(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "r2")
    assert svc.save_config_bundle("kiss", values={"rf_log": "off", "rx_only": "on"}, band="868").ok
    m = svc.restart_required("kiss@r2")
    assert m is not None and m["params"] == ["rf_log", "rx_only"] and m["band"] == "868"


def test_switching_every_log_off_and_on_again_leaves_no_marker(tmp_path, monkeypatch):
    # F-M1 (hardware matrix 2026-09-25): `lhpc rflog --all off` then `on` left the running
    # stacks flagged although they run with exactly the saved value again.
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "r2")
    before = svc.rflog_switch("graywolf")["value"]                              # what kiss launched with
    other = "off" if before == "on" else "on"
    assert svc.set_rflog_all(other).ok
    assert svc.restart_required("kiss@r2")["params"] == ["rf_log"]
    assert svc.set_rflog_all(before).ok
    assert svc.restart_required("kiss") is None
    assert svc.rflog_switch("graywolf")["restart_required"] is False


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


@pytest.mark.parametrize("rid, band", [("r1", "433"), ("r2", "868")])
def test_the_stack_row_is_the_registry_for_that_radio(tmp_path, rid, band):
    """W4: every enabled radio in the radio row; in the stack row the radio's daemon file plus every registered stack
    whose manifest components declare the radio's band (its band-less file, then that radio's own `<rid>/<file>`), in
    registry order — nothing hand-typed here, the registry and the manifest are the expectation."""
    svc = _svc(tmp_path)
    sw = svc.rflog_switcher("rf-kiss.log", rid)
    assert sw["radio"] == rid
    assert [(r["rid"], r["band"], r["current"]) for r in sw["radios"]] == [("r1", "433", rid == "r1"),
                                                                            ("r2", "868", rid == "r2")]
    expected = []
    for e in rflog.REGISTRY:
        if e.banded:
            expected.append((e.writer, e.radio_job(rid), e.surface))
        elif band in _declared(svc, e.surface):          # the stack's row, then its instance's on this radio (K4b)
            expected.append((e.writer, e.job(), e.surface))
            expected += [(e.writer, f"{rid}/{e.job()}", e.surface)] if rflog.by_job(f"{rid}/{e.job()}") else []
    assert [(s["target"], s["job"], s["surface"]) for s in sw["stacks"]] == expected
    assert [s["surface"] for s in sw["stacks"] if s["current"]] == ["graywolf"]
    assert [s["label"] for s in sw["stacks"] if s["surface"] == "daemon"] == [f"daemon {rid}"]
    assert svc.rflog_switch("chat") is None
    assert svc.rflog_job("loraham-kiss-tnc", "rf-kiss.log")["surface"] == "graywolf"
    assert svc.rflog_job("loraham-daemon", "rf-kiss.log") is None        # not this writer's
    assert svc.rflog_job("loraham-kiss-tnc", "rf-made-up.log") is None


def test_the_shown_radio_is_the_arg_else_the_files_radio_else_one_on_the_stacks_bands(tmp_path):
    svc = _svc(tmp_path)
    assert svc.rflog_switcher("r2/rf-daemon.log")["radio"] == "r2"             # the file's radio
    assert svc.rflog_switcher("r2/rf-daemon.log", "r1")["radio"] == "r1"       # the arg wins
    assert svc.rflog_switcher("r2/rf-daemon.log", "r9")["radio"] == "r2"       # a rid of no enabled radio is ignored
    assert svc.rflog_switcher("rf-meshcore.log")["radio"] == "r2"              # nothing runs: the first on its 868
    assert svc.rflog_switcher("rf-daemon-868.log")["radio"] == "r1"            # a 0.13.x band file: the first radio
    assert not [s for s in svc.rflog_switcher("r2/rf-daemon.log", "r1")["stacks"] if s["current"]]   # not on r1


# ---- every stack at once -----------------------------------------------------------------

def test_set_rflog_all_writes_every_owner_once_and_marks_each_running_one(tmp_path, monkeypatch):
    # K6 (DEC-W384k): every owner once per instance (its own file), one result line per owner
    from lhpc.core import instances
    from lhpc.core.commands import stack_instances
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "r2")
    saved = []
    real = ControllerService.save_config_bundle
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, target, **kw: saved.append((target, kw["inst"].rid)) or real(self, target, **kw))
    res = svc.set_rflog_all("off")
    owners = list(dict.fromkeys(e.owner for e in rflog.REGISTRY))
    every = [(o, i.rid) for o in owners for i in instances.saved_insts(svc, o)]     # every radio of its bands (N3)
    assert res.ok and saved == every and len(res.details) == len(owners)
    for e in rflog.REGISTRY:
        assert all(_store(svc, e.owner, i.rid)[e.key] == "off" for i in stack_instances(svc.stack(e.owner), svc._paths))
    assert svc.restart_required("kiss@r2")["params"] == ["rf_log"]
    assert svc.restart_required("kiss@r1") is None and svc.restart_required("daemon") is None   # not running
    bad = svc.set_rflog_all("maybe")
    assert not bad.ok and bad.data["reason"] == "invalid-choice" and saved == every


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
    assert jobs                                     # an empty registry would make this vacuous
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

def test_k8_the_cli_resolver_names_a_radio_never_a_band(tmp_path):
    # K8 (W308/W310): the daemon's and an instance's RF log is the radio's `<rid>/<file>`; a one-node stack has one file
    svc = _svc(tmp_path)
    _logs(tmp_path, "r1/rf-kiss.log", "k\n")
    with pytest.raises(ValueError, match="logs per radio: name one, lhpc rflog daemon@<rid>"):
        svc.rflog_tail("daemon")
    with pytest.raises(ValueError, match="logs per radio: name one, lhpc rflog graywolf@<rid>"):
        svc.rflog_tail("graywolf")
    with pytest.raises(ValueError, match="'reticulum@r1' has no RF log"):
        svc.rflog_tail("reticulum", "r1")
    with pytest.raises(ValueError):
        svc.rflog_tail("chat")
    assert svc.rflog_tail("graywolf", "r1") == (str(tmp_path / "logs" / "r1" / "rf-kiss.log"), ["k"])
    assert svc.rflog_tail("daemon", "r2") == ("", [])                     # no file yet
    _logs(tmp_path, "r2/rf-daemon.log", "d\n")
    assert svc.rflog_tail("daemon", "r2")[0].endswith("logs/r2/rf-daemon.log")


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


# --- W7-8a (B4-D23, B4-D24): the daemon's RF log per radio ---------------------------------------------

def _one_radio(tmp_path):
    from lhpc.core import radios
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((radios.Radio("r1", "low", "loraham", "868"),)))


def test_a_radios_rf_log_is_read_from_its_own_directory(tmp_path):
    _one_radio(tmp_path)
    svc = _svc(tmp_path)
    d = tmp_path / "logs" / "r1"
    d.mkdir(parents=True)
    (d / "rf-daemon.log").write_text("hello\n")
    path, raw = svc.log_tail("loraham-daemon", 10, job="r1/rf-daemon.log")
    assert path == str(d / "rf-daemon.log") and "hello" in raw


def test_a_symlinked_radio_directory_is_never_read(tmp_path):
    # W7-8a: the leaf read is O_NOFOLLOW; a symlinked logs/r1 (the level above it) is refused as well.
    _one_radio(tmp_path)
    svc = _svc(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "rf-daemon.log").write_text("secret\n")
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "r1").symlink_to(real, target_is_directory=True)
    path, raw = svc.log_tail("loraham-daemon", 10, job="r1/rf-daemon.log")
    assert (path, raw) == ("", [])                       # neither the content nor the path is surfaced


@pytest.mark.parametrize("job", ["r0/rf-daemon.log", "r1/../x", "../rf-daemon.log", "r1/rf-daemon.log.1/x",
                                 "R1/rf-daemon.log", "r1/rf-daemon.log "])
def test_only_the_radio_form_is_an_rf_log(job):
    assert rflog.by_job(job) is None


def test_the_listing_has_one_entry_per_enabled_radio_and_the_old_band_files_read_only():
    from lhpc.core import radios
    rows = (radios.Radio("r12", "c", "loraham", "868"), radios.Radio("r2", "b", "loraham", "433"),
            radios.Radio("r3", "d", "loraham", "868", enabled=False))
    jobs = rflog.list_jobs(rows, ["rf-daemon-868.log", "other.log"])
    assert [(j.job, j.readonly) for j in jobs] == [("r2/rf-daemon.log", False), ("r12/rf-daemon.log", False),
                                                    ("rf-daemon-868.log", True)]
    assert jobs[0].label == "daemon r2 (433)"


def test_a_prune_never_takes_an_rf_log_of_either_form():
    names = ["build-x.log", "rf-daemon-868.log", "r1/rf-daemon.log", "keep.log"]
    assert rflog.prune_targets(names, lambda n: n == "keep.log") == ["build-x.log"]


def test_the_job_selector_takes_no_other_nested_name(tmp_path):
    # W7-8a: only a radio's exact `<rid>/rf-daemon.log` may name a subdirectory; any other nested job is refused.
    _one_radio(tmp_path)
    svc = _svc(tmp_path)
    for sub, name in (("sub", "a.log"), ("r1", "other.log"), ("r1", "rf-daemon.log.1")):
        (tmp_path / "logs" / sub).mkdir(parents=True, exist_ok=True)
        (tmp_path / "logs" / sub / name).write_text("hidden\n")
        assert svc.log_tail("loraham-daemon", 10, job=f"{sub}/{name}") == ("", [])


@pytest.mark.parametrize("make,state", [
    (lambda root: (root / "logs" / "r1").mkdir(parents=True), "present"),
    (lambda root: (root / "logs").mkdir(), "absent"),
    (lambda root: None, "absent"),                                                    # no logs/ at all
    (lambda root: ((root / "logs").mkdir(), (root / "logs" / "r1").write_text("x")), "unknown"),   # a file
    (lambda root: ((root / "logs").mkdir(), (root / "real").mkdir(),
                   (root / "logs" / "r1").symlink_to(root / "real", target_is_directory=True)), "unknown"),
    (lambda root: ((root / "logs").mkdir(), (root / "logs" / "r1").symlink_to(root / "gone")), "unknown"),
    (lambda root: (root / "logs").write_text("x"), "unknown"),                        # logs/ is a file: ENOTDIR
])
def test_a_radio_log_directory_is_present_absent_or_unknown(tmp_path, make, state):
    # B4-D34 n9: absent, unreadable and wrong are three answers; only PRESENT is listed or read.
    make(tmp_path)
    paths = Paths(runtime_root=tmp_path)
    assert rflog.radio_dir_state(paths, "r1/rf-daemon.log") == state
    assert rflog.radio_dir_ok(paths, "r1/rf-daemon.log") is (state == "present")
    assert rflog.radio_dir_state(paths, "rf-kiss.log") == "present"                   # no radio level to check


def test_an_outside_logs_tree_is_unknown(tmp_path):
    root, outside = tmp_path / "rt", tmp_path / "outside"
    (outside / "r1").mkdir(parents=True)
    root.mkdir()
    (root / "logs").symlink_to(outside, target_is_directory=True)
    assert rflog.radio_dir_state(Paths(runtime_root=root), "r1/rf-daemon.log") == "unknown"


@pytest.mark.parametrize("make,state", [
    # B5-D21: an intermediate logs/ link is judged before anything under it: escaping or dangling is UNKNOWN
    (lambda root, out: ((out / "elsewhere").mkdir(parents=True),
                        (root / "logs").symlink_to(out / "elsewhere", target_is_directory=True)), "unknown"),
    (lambda root, out: (root / "logs").symlink_to(root / "nowhere"), "unknown"),                  # dangling inside
    (lambda root, out: (root / "logs").symlink_to(out / "nowhere"), "unknown"),                   # dangling outside
    (lambda root, out: ((root / "real" / "r1").mkdir(parents=True),
                        (root / "logs").symlink_to(root / "real", target_is_directory=True)), "present"),
    (lambda root, out: ((root / "real").mkdir(),
                        (root / "logs").symlink_to(root / "real", target_is_directory=True)), "absent"),
])
def test_an_intermediate_logs_link_is_judged_before_the_radio_level(tmp_path, make, state):
    root, out = tmp_path / "rt", tmp_path / "out"
    root.mkdir()
    make(root, out)
    paths = Paths(runtime_root=root)
    assert rflog.radio_dir_state(paths, "r1/rf-daemon.log") == state
    assert rflog.radio_dir_ok(paths, "r1/rf-daemon.log") is (state == "present")


def test_g1_k6c_n3_the_stack_wide_switch_reaches_a_disabled_radios_own_file(tmp_path):
    # r1 disabled while `lhpc rflog --all off` runs: kiss@r1's own file gets it, so r1 reads it when it returns
    import dataclasses

    import radios_fixture
    rows = list(radios_fixture.RADIOS)
    radios_fixture.write(tmp_path, [dataclasses.replace(rows[0], enabled=False), *rows[1:]])
    svc = _svc(tmp_path)
    assert svc.set_rflog("graywolf", "off").ok
    assert _store(svc, "kiss", "r1")["rf_log"] == "off" and "rf_log" not in _store(svc, "kiss")
    radios_fixture.write(tmp_path, rows)                                        # r1 back
    svc = _svc(tmp_path)
    assert svc.stack_config("kiss", "433")["rf_log"] == "off"


def test_g1_k6c_n4_a_partly_saved_switch_names_the_saved_and_the_failed_instances(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    real = ControllerService.save_config_bundle

    def save(self, target, **kw):
        from lhpc.core.service_base import ActionResult
        if kw.get("inst") is not None and kw["inst"].rid == "r2":
            return ActionResult(False, "Config not saved for 'kiss'.", ["disk full"])
        return real(self, target, **kw)
    monkeypatch.setattr(ControllerService, "save_config_bundle", save)
    r = svc.set_rflog("graywolf", "off")
    assert not r.ok and r.summary == "RF log not saved on every instance: saved kiss@r1; not saved kiss@r2"
    assert "disk full" in r.details


def _only_868(tmp_path):
    import radios_fixture
    radios_fixture.write(tmp_path, (radios_fixture.R3,))          # one 868 radio: meshcom (433 only) has none
    return _svc(tmp_path)


def test_g1_k6c_re_3_a_stack_without_a_radio_refuses_the_switch_by_name(tmp_path):
    # before, saved_insts fell back to [None]: the switch landed in meshcom.toml, which no meshcom@rN reads
    svc = _only_868(tmp_path)
    r = svc.set_rflog("meshcom", "on")
    assert not r.ok and r.summary == "RF log not saved: no radio for 'meshcom' in the hardware list"
    assert "rf_log" not in _store(svc, "meshcom")
    assert svc.rflog_switch("meshcom")["value"] == ""


def test_g1_k6c_re_3_the_stack_wide_switch_skips_a_stack_without_a_radio(tmp_path):
    svc = _only_868(tmp_path)
    r = svc.set_rflog_all("off")
    assert r.ok and "meshcom: RF log not saved: no radio for 'meshcom' in the hardware list" in r.details
    assert _store(svc, "kiss", "r3")["rf_log"] == "off" and "rf_log" not in _store(svc, "meshcom")
    assert svc.rflog_logging_state() == "off"                        # meshcom is left out, not "mixed"


def test_dec_w384ci_an_instance_form_names_the_stacks_one_switch(tmp_path):
    # DEC-W384ci: `daemon@r1` is no switch of its own; the refusal says whose switch it is, nothing is saved.
    # red on a5a047ce: "'daemon@r1' has no RF log."
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.set_rflog("daemon@r1", "off")
    assert (res.ok, res.summary) == (False, "Cannot switch the RF log of daemon@r1: the switch is daemon's, one for all "
                                            "its radios (switch 'daemon').")
    assert not (tmp_path / "config" / "stacks").exists() or not list((tmp_path / "config" / "stacks").iterdir())
