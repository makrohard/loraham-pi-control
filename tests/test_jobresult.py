"""The web-job attempt/result marker state machine: compare-before-replace, no absent-create,
structural validation, and GET-safe reads."""

import json

from lhpc.core import jobresult, runtime_fs
from lhpc.core.paths import Paths

_LOG = "build-meshcom-qemu.log"
_A = "a" * 32
_B = "b" * 32


def _p(tmp_path):
    return Paths(runtime_root=tmp_path)


def _reserve(paths, attempt=_A, log=_LOG):
    return jobresult.reserve(paths, log, attempt, "build", "meshcom-qemu", "meshcom",
                             ["src:/home/x/meshcom"])


def test_reserve_then_advance_roundtrip(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p)
    d = jobresult._read_raw(p, _LOG)
    assert d["state"] == "starting" and d["startup_unverified"] is True and d["attempt_id"] == _A
    assert jobresult.mark_gate_passed(p, _LOG, _A)
    assert jobresult._read_raw(p, _LOG)["startup_unverified"] is False
    assert jobresult.mark_running(p, _LOG, _A)
    assert jobresult._read_raw(p, _LOG)["state"] == "running"
    assert jobresult.terminalize(p, _LOG, _A, "done")
    d = jobresult._read_raw(p, _LOG)
    assert d["state"] == "done" and d["finished_at"] and "driver_ident" not in d


def test_absent_marker_is_never_created(tmp_path):
    # No reserve() first → every mutating op is a no-op returning False (a stale child cannot resurrect).
    p = _p(tmp_path)
    assert not jobresult.mark_gate_passed(p, _LOG, _A)
    assert not jobresult.mark_running(p, _LOG, _A)
    assert not jobresult.terminalize(p, _LOG, _A, "failed")
    assert not jobresult.remove(p, _LOG, _A)
    assert jobresult._read_raw(p, _LOG) is None


def test_mismatched_attempt_is_a_noop(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p, attempt=_A)
    # a stale child (attempt B) can neither advance nor clobber attempt A
    assert not jobresult.mark_running(p, _LOG, _B)
    assert not jobresult.terminalize(p, _LOG, _B, "done")
    assert not jobresult.remove(p, _LOG, _B)
    assert jobresult._read_raw(p, _LOG)["state"] == "starting"


def test_terminalize_unsafe_stores_only_safe_driver_ident(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p)
    ident = {"pid": 5, "starttime": 9, "pgid": 5, "sid": 5, "session_ident": "SECRET", "exec": "x"}
    assert jobresult.terminalize(p, _LOG, _A, "unsafe", detail="orphan", driver_ident=ident)
    d = jobresult._read_raw(p, _LOG)
    assert d["state"] == "unsafe" and d["driver_ident"] == {"pid": 5, "starttime": 9, "pgid": 5, "sid": 5}
    assert "session_ident" not in json.dumps(d)


def test_recover_only_rewrites_matching_unsafe(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p)
    assert not jobresult.recover(p, _LOG, _A)              # not unsafe yet
    assert jobresult.terminalize(p, _LOG, _A, "unsafe", driver_ident={"pid": 1, "starttime": 1,
                                                                      "pgid": 1, "sid": 1})
    assert not jobresult.recover(p, _LOG, _B)              # wrong attempt
    assert jobresult.recover(p, _LOG, _A)
    d = jobresult._read_raw(p, _LOG)
    assert d["state"] == "failed" and "driver_ident" not in d


def test_read_results_skips_untrusted(tmp_path):
    p = _p(tmp_path)
    runtime_fs.ensure_dir(p, p.under("state", "jobresults"))
    assert _reserve(p)                                     # one valid marker
    d = p.under("state", "jobresults")
    (d / "not-a-result.json").write_text("{}")             # wrong suffix (no .log.json)
    (d / "bad.log.json").write_text("{not json")           # malformed
    (d / "wrongop.log.json").write_text(json.dumps({"op": "nope", "state": "done", "log": "wrongop.log",
                                                     "attempt_id": _A, "finished_at": "2026-01-01T00:00:00Z"}))
    got = {log for log, _ in jobresult.read_results(p)}
    assert got == {_LOG}                                   # only the valid one


def test_prune_done_removes_old_done_retains_failed(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p, attempt=_A, log="build-a.log")
    assert jobresult.terminalize(p, "build-a.log", _A, "done")
    assert _reserve(p, attempt=_B, log="build-b.log")
    assert jobresult.terminalize(p, "build-b.log", _B, "failed")
    fin = jobresult.parse_epoch(jobresult._read_raw(p, "build-a.log")["finished_at"])
    assert jobresult.prune_done(p, fin + 61, 60) == 1      # done aged out
    assert jobresult._read_raw(p, "build-a.log") is None
    assert jobresult._read_raw(p, "build-b.log")["state"] == "failed"   # failed retained


def test_read_results_empty_on_missing_dir(tmp_path):
    assert jobresult.read_results(_p(tmp_path)) == []      # never raises


def test_reserve_refuses_a_live_attempt_but_replaces_terminal(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p, attempt=_A)                          # starting (live)
    assert not jobresult.reserve(p, _LOG, _B, "build", "meshcom-qemu", "meshcom", [])   # refused
    assert jobresult._read_raw(p, _LOG)["attempt_id"] == _A
    assert jobresult.mark_gate_passed(p, _LOG, _A) and jobresult.mark_running(p, _LOG, _A)
    assert not jobresult.reserve(p, _LOG, _B, "build", "meshcom-qemu", "meshcom", [])   # running → refused
    assert jobresult.terminalize(p, _LOG, _A, "done")
    assert jobresult.reserve(p, _LOG, _B, "build", "meshcom-qemu", "meshcom", [])        # done → replaceable
    assert jobresult._read_raw(p, _LOG)["attempt_id"] == _B


def test_admitted_flag_lifecycle_and_bool_validation(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p)
    assert jobresult._read_raw(p, _LOG)["admitted"] is False           # reserve → False
    jobresult.mark_gate_passed(p, _LOG, _A)
    assert jobresult.mark_running(p, _LOG, _A)
    assert jobresult._read_raw(p, _LOG)["admitted"] is True            # mark_running → True
    assert jobresult.terminalize(p, _LOG, _A, "failed")
    assert jobresult._read_raw(p, _LOG)["admitted"] is True            # preserved through terminal
    # a malformed non-bool `admitted` makes the marker untrusted (never read as truthy)
    runtime_fs.ensure_dir(p, p.under("state", "jobresults"))
    (p.under("state", "jobresults") / "bad.log.json").write_text(json.dumps(
        {"op": "build", "state": "failed", "log": "bad.log", "attempt_id": _A,
         "finished_at": "2026-01-01T00:00:00Z", "admitted": "false"}))
    assert "bad.log" not in {log for log, _ in jobresult.read_results(p)}


def test_prune_done_does_not_delete_a_newer_reserved_attempt(tmp_path, monkeypatch):
    # RACE: prune's lock-free snapshot still shows the old done A, but a concurrent reserve() has replaced it
    # with a NEW starting B on disk. prune_done must re-read under the lock and keep B.
    p = _p(tmp_path)
    assert _reserve(p, attempt=_A)
    assert jobresult.terminalize(p, _LOG, _A, "done")
    snap = {**jobresult._read_raw(p, _LOG), "finished_at": "2000-01-01T00:00:00Z"}   # ancient A
    monkeypatch.setattr(jobresult, "read_results", lambda paths: [(_LOG, snap)])
    assert jobresult.reserve(p, _LOG, _B, "build", "meshcom-qemu", "meshcom", [])     # B replaces A (done)
    assert jobresult.prune_done(p, 9_999_999_999, 60) == 0                            # B not deleted
    assert jobresult._read_raw(p, _LOG)["attempt_id"] == _B and jobresult._read_raw(p, _LOG)["state"] == "starting"


def test_prune_done_still_removes_a_genuinely_expired_done(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p, attempt=_A)
    assert jobresult.terminalize(p, _LOG, _A, "done")
    fin = jobresult.parse_epoch(jobresult._read_raw(p, _LOG)["finished_at"])
    assert jobresult.prune_done(p, fin + 61, 60) == 1
    assert jobresult._read_raw(p, _LOG) is None


def test_stale_terminalize_after_new_reserve_is_a_noop(tmp_path):
    p = _p(tmp_path)
    assert _reserve(p, attempt=_A)
    assert jobresult.terminalize(p, _LOG, _A, "done")       # A terminal → replaceable
    assert jobresult.reserve(p, _LOG, _B, "build", "meshcom-qemu", "meshcom", [])       # B owns it now
    assert not jobresult.terminalize(p, _LOG, _A, "failed") # stale A cannot clobber B
    assert not jobresult.remove(p, _LOG, _A)                # stale dismiss cannot unlink B
    d = jobresult._read_raw(p, _LOG)
    assert d["attempt_id"] == _B and d["state"] == "starting"
