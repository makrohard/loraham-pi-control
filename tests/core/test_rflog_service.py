"""The RF-log service behind the RF-Logs submenu, the log page and `lhpc rflog`: the band-less
switch and its restart marker, the two-segment tail, the scoped Clear, and the meshtastic
retention exception. Everything is authorized by the registry, never by a file name."""

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
    assert b.set_rflog("graywolf", "off").ok                                # the graywolf submenu
    pa, pb = cfgmod._stack_config_path(a._paths, "kiss", ""), cfgmod._stack_config_path(b._paths, "kiss", "")
    assert pa.read_text() == pb.read_text() and _store(b, "kiss")["rf_log"] == "off"
    assert "rf_log" not in _store(b, "graywolf")                            # never the proxy's store
    assert not (tmp_path / "b" / "config" / "stacks" / "graywolf.toml").exists()


def test_the_switch_survives_a_band_change(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="433").ok
    assert svc.stack_config("kiss", "868")["rf_log"] == "off"
    assert svc._resolved_param_value("kiss", "run", "loraham-kiss-tnc", "rf_log", "868") == "off"
    assert svc.rflog_view("graywolf")["value"] == "off"


def test_on_is_the_default_and_clears_the_key(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}).ok
    assert svc.save_config_bundle("kiss", values={"rf_log": "on"}).ok
    assert "rf_log" not in _store(svc, "kiss")                              # no third state
    assert svc.rflog_view("graywolf")["value"] == "on"


def test_a_file_param_owner_stores_its_file_key_bandless(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("meshcore", values={"file_rf_log": "off"}).ok
    assert _store(svc, "meshcore")["file_rf_log"] == "off"
    assert svc.file_config_values("meshcore")["rf_log"] == "off"
    assert svc.rflog_view("meshcore")["value"] == "off"


def test_the_daemon_switch_is_read_per_band_at_spawn(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("daemon", values={"rf_log": "off"}).ok
    for band in ("433", "868"):
        assert svc._resolved_param_value("daemon", "run", "loraham-daemon", "rf_log", band) == "off"


def test_a_bad_value_is_refused(tmp_path):
    svc = _svc(tmp_path)
    assert not svc.set_rflog("graywolf", "maybe").ok
    assert not svc.set_rflog("chat", "off").ok
    assert not (tmp_path / "config").exists() or "rf_log" not in _store(svc, "kiss")


# ---- the restart marker -----------------------------------------------------------------

def test_a_cross_band_edit_of_the_switch_marks_the_live_band(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _live(monkeypatch, svc, "kiss", "868")
    assert svc.save_config_bundle("kiss", values={"rf_log": "off"}, band="433").ok
    m = svc.restart_required("kiss")
    assert m is not None and m["params"] == ["rf_log"] and m["band"] == "868"
    assert svc.rflog_view("graywolf")["restart_required"] is True


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


def test_the_view_lists_the_registry_jobs_with_paths_and_sizes(tmp_path):
    svc = _svc(tmp_path)
    _logs(tmp_path, "rf-daemon-433.log", "x" * 2048)
    v = svc.rflog_view("daemon")
    assert [j["job"] for j in v["jobs"]] == ["rf-daemon-433.log", "rf-daemon-868.log"]
    assert v["jobs"][0]["size"] == 2048 and v["jobs"][1]["size"] is None
    assert v["jobs"][0]["path"] == str(tmp_path / "logs" / "rf-daemon-433.log")
    assert v["owner"] == "daemon" and v["writer"] == "loraham-daemon"
    assert svc.rflog_view("chat") is None
    assert [s["job"] for s in svc.rflog_switcher()] == [j for e in rflog.REGISTRY for _b, j in e.jobs]
    assert svc.rflog_job("loraham-kiss-tnc", "rf-kiss.log")["surface"] == "graywolf"
    assert svc.rflog_job("loraham-daemon", "rf-kiss.log") is None        # not this writer's
    assert svc.rflog_job("loraham-kiss-tnc", "rf-made-up.log") is None


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


# ---- second-audit regressions ---------------------------------------------------------------

def test_generic_log_pruning_never_touches_a_registered_rf_log(tmp_path):
    """RF logs are persistent by contract: their writer keeps the descriptor open and they must
    outlive every job log. The generic prune used to count them in its budget and delete the
    oldest — the writer kept writing to an unlinked inode while the viewer showed nothing."""
    import time
    svc = _svc(tmp_path)
    svc.LOG_RETENTION = 5
    rf = _logs(tmp_path, "rf-kiss.log", "old frame\n")
    prev = _logs(tmp_path, "rf-kiss.log.1", "older\n")
    os.utime(rf, (1, 1))                                   # the oldest file of all
    for i in range(40):
        (tmp_path / "logs" / f"build-x-{i:02d}.log").write_text("x")
        time.sleep(0.001)
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


def test_a_binary_artifact_behind_the_manifest_is_refused_before_start(tmp_path, monkeypatch):
    """Publishing a new artifact never updates an installed copy. A copy built from other
    commits than the manifest pins would be launched with argv it does not know (the RF-log
    options were the first case) and exit — so the start refuses it, typed, and names the fix."""
    from lhpc.core import binary_receipt as brx
    from lhpc.core.services import ControllerService
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    spec = svc.binary_spec("meshtastic")
    files = []
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
        files.append(rel)
    comp = svc.stack("meshtastic").component("meshtastic")
    (tmp_path / comp.source.path).mkdir(parents=True)      # the artifact overlays a clone

    def receipt(commit):
        import hashlib
        return brx.BinaryReceipt(
            stack="meshtastic", artifact_sha256="a" * 64, artifact_size=9,
            filename="meshtastic-a.tar.zst", url="https://example.invalid/a.tar.zst",
            components={c: commit for c in spec.covers}, provenance={},
            files=tuple(files),
            file_hashes={r: hashlib.sha256(b"x").hexdigest() for r in files},
            proof_paths=tuple(files), registry_baseline={}, probe="ok")
    assert brx.write_receipt(svc._paths, receipt("b" * 40))     # not the manifest pin
    assert svc.on_binary_channel("meshtastic")
    why = svc.binary_behind(comp)
    assert "behind the manifest" in why and "lhpc update meshtastic" in why
    assert svc.install_blocker(comp) == why
    # Past the identity and hardware gates, the start refuses this component typed.
    from lhpc.core import config as cfgmod
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshtastic", values={"node_name": "LHPC test", "node_short": "LHPT"}).ok
    r = svc.start("meshtastic", apply=True)
    blocked = [x for x in r.results if x.component == "meshtastic"]
    assert not r.ok and blocked and "behind the manifest" in blocked[0].summary, (r.summary, r.details)
    # The matching artifact is not "behind".
    assert brx.write_receipt(svc._paths, receipt(comp.source.pin_commit))
    assert svc.binary_behind(comp) == ""
    # A source-installed component is never judged here (its checkout is the operator's choice).
    assert svc.binary_behind(svc.stack("kiss").component("loraham-kiss-tnc")) == ""


@pytest.mark.parametrize("target", ["daemon", "meshcom"])
def test_a_stale_daemon_binary_is_refused_on_its_own_spawn_path(tmp_path, monkeypatch, target):
    """The daemon is spawned by `_ensure_daemon`, not by the generic start loop, so the
    behind-manifest refusal has to sit on that path too — for a direct `start daemon` and for a
    dependent stack that asks for the band. A stale artifact must never reach the spawn."""
    import hashlib
    from lhpc.core import binary_receipt as brx
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    assert svc.set_operator_identity(callsign="XX0XXA").ok        # meshcom's own identity gate
    spec = svc.binary_spec("daemon")
    files = []
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
        files.append(rel)
    daemon = svc.stack("daemon").component("loraham-daemon")
    (tmp_path / daemon.source.path).mkdir(parents=True, exist_ok=True)
    assert brx.write_receipt(svc._paths, brx.BinaryReceipt(
        stack="daemon", artifact_sha256="a" * 64, artifact_size=3, filename="d.tar.zst",
        url="https://example.invalid/d.tar.zst",
        components={c: "b" * 40 for c in spec.covers}, provenance={},      # not the manifest pins
        files=tuple(files), file_hashes={r: hashlib.sha256(b"ELF").hexdigest() for r in files},
        proof_paths=tuple(files), registry_baseline={}, probe="loraham_daemon 0.9.0"))
    assert svc.on_binary_channel("daemon") and svc.binary_behind(daemon)
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start",
                        lambda self, *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError("spawned")))
    r = svc.start(target, apply=True)
    assert not r.ok
    assert any("behind the manifest" in str(d) for d in r.details), (r.summary, r.details)
    assert spawned == []                                          # never reached the spawn
