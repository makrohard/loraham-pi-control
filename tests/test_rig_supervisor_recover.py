"""rig_supervisor safety contracts: no false SUCCESS, no unsafe recovery, decisive results acted on.

P1 regressions covered:
  * a modern-CLI `--recover` refusal / a failed orphan-confirm / an OLD CLI all STOP the rig
    (RECOVER_REFUSED) — there is no file move-aside anywhere;
  * a failed/empty/unrecognized `lhpc status` is INCONCLUSIVE (stop rc 4), never "goal reached";
  * SUCCESS additionally requires the authoritative auto-install completion banner (status rows
    cannot distinguish an interrupted build from a finished one);
  * a failed tmux start stops instead of polling a run that never began.
"""

import importlib.util
import pathlib

_RS_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "rig_supervisor.py"
_spec = importlib.util.spec_from_file_location("rig_supervisor_under_test", _RS_PATH)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

_STATUS_OK = ("[daemon] LoRaHAM daemon  (stopped)\n"
              "  loraham-daemon           stopped        [service] band -  tx disabled  src match\n")
_BANNER = ("==== auto-install run completed: 7/8 stack(s) successful, 0 blocked, 0 failed, "
           "1 skipped (GUI deps absent). ====\n")


def _clean_state(run_alive=False, old_cli=False, status_rc=0, status_text=_STATUS_OK,
                 run_log_tail=""):
    st = rs.RigState()
    st.old_cli = old_cli
    st.status_rc = status_rc
    st.status_text = status_text
    st.run_log_tail = run_log_tail
    st.live_procs = "some-proc" if run_alive else ""     # drives run_alive
    st.runtime_root = "/run/user/1000/lhpc"
    return st


# --- do_recover: every decisive refusal STOPS ------------------------------------------------

def test_modern_recover_refusal_returns_sentinel(monkeypatch):
    calls = []

    def fake_ssh(cmd, timeout=120):
        calls.append(cmd)
        # the modern verb DECLINES for a non-orphan reason (e.g. not the lease owner)
        return 1, "refusing: run state is owned by another lease holder"

    monkeypatch.setattr(rs, "ssh", fake_ssh)
    record = rs.do_recover(lambda _m: None, _clean_state())
    assert record.startswith(rs.RECOVER_REFUSED)                     # refusal sentinel returned
    assert calls == [f"{rs.LHPC} auto-install --recover 2>&1"]       # one attempt, no fallback
    assert not any("--confirm-orphan" in c for c in calls)


def test_old_cli_recovery_stops_no_move_aside(monkeypatch):
    # The old-CLI automatic file move-aside is GONE: liveness cannot be identity-proven, so an
    # old CLI stops for explicit operator recovery. No ssh mutation of any kind is attempted.
    calls = []
    monkeypatch.setattr(rs, "ssh", lambda cmd, timeout=120: calls.append(cmd) or (0, ""))
    record = rs.do_recover(lambda _m: None, _clean_state(old_cli=True))
    assert record.startswith(rs.RECOVER_REFUSED) and "OLD" in record
    assert calls == []                                               # nothing touched on the rig
    assert not hasattr(rs, "_recover_move_aside")                    # the mechanism is deleted


def test_orphan_confirm_failure_is_a_refusal_not_a_record(monkeypatch):
    # rc2 != 0 from `--recover --confirm-orphan` is DECISIVE: sentinel, not a sailed-past record.
    def fake_ssh(cmd, timeout=120):
        if "--confirm-orphan" in cmd:
            return 1, "refusing: lease identity mismatch"
        return 1, "ORPHAN RISK: leftover state from pid 123"
    monkeypatch.setattr(rs, "ssh", fake_ssh)
    record = rs.do_recover(lambda _m: None, _clean_state())
    assert record.startswith(rs.RECOVER_REFUSED) and "confirm-orphan" in record


def test_orphan_confirm_success_is_a_recover_record(monkeypatch):
    def fake_ssh(cmd, timeout=120):
        if "--confirm-orphan" in cmd:
            return 0, "recovered"
        return 1, "ORPHAN RISK: leftover state from pid 123"
    monkeypatch.setattr(rs, "ssh", fake_ssh)
    record = rs.do_recover(lambda _m: None, _clean_state())
    assert not record.startswith(rs.RECOVER_REFUSED) and "OK" in record


# --- supervise: inconclusive evidence never becomes a verdict --------------------------------

def _wire(monkeypatch, tmp_path, st, *, banner=False):
    started = {"run": False}
    monkeypatch.setattr(rs, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(rs, "wait_until_reachable", lambda log: None)
    monkeypatch.setattr(rs, "take_stock", lambda log: st)
    monkeypatch.setattr(rs, "start_run", lambda log: started.__setitem__("run", True) or True)
    if banner:
        st.run_log_tail = _BANNER
    return started


def test_supervise_success_requires_the_authoritative_banner(monkeypatch, tmp_path):
    # Status shows nothing outstanding BUT no completion banner -> NOT success; a run is started
    # (an interrupted build shows 'stopped' exactly like a finished one).
    st = _clean_state()                                              # all rows present-state
    started = _wire(monkeypatch, tmp_path, st)
    rc = rs.supervise(once=True)
    assert rc == 0 and started["run"] is True                        # ran auto-install, no SUCCESS claim


def test_supervise_success_with_banner(monkeypatch, tmp_path):
    st = _clean_state()
    started = _wire(monkeypatch, tmp_path, st, banner=True)
    rc = rs.supervise(once=True)
    assert rc == 0 and started["run"] is False                       # authoritative SUCCESS, no run


def test_supervise_nonzero_status_rc_is_inconclusive(monkeypatch, tmp_path):
    st = _clean_state(status_rc=1)
    started = _wire(monkeypatch, tmp_path, st)
    assert rs.supervise(once=True) == 4
    assert started["run"] is False


def test_supervise_empty_status_is_inconclusive(monkeypatch, tmp_path):
    st = _clean_state(status_text="")
    started = _wire(monkeypatch, tmp_path, st)
    assert rs.supervise(once=True) == 4
    assert started["run"] is False


def test_supervise_unrecognized_status_is_inconclusive(monkeypatch, tmp_path):
    # rc 0 + non-empty output but ZERO recognizable component rows (garbled/foreign output).
    st = _clean_state(status_text="something went sideways\nbut exit code lied\n")
    started = _wire(monkeypatch, tmp_path, st)
    assert rs.supervise(once=True) == 4
    assert started["run"] is False


def test_supervise_tmux_start_failure_stops(monkeypatch, tmp_path):
    st = _clean_state()                                              # no banner -> wants to run
    _wire(monkeypatch, tmp_path, st)
    monkeypatch.setattr(rs, "start_run", lambda log: False)          # tmux failed
    assert rs.supervise(once=True) == 4


def test_supervise_stops_on_recover_refusal_without_starting_a_run(monkeypatch, tmp_path):
    # The caller must stop the rig (return 3) on the refusal sentinel and NEVER start a new run.
    st = _clean_state()
    started = _wire(monkeypatch, tmp_path, st)
    monkeypatch.setattr(rs, "do_recover", lambda log, s: f"{rs.RECOVER_REFUSED}: declined (rc=1)")
    monkeypatch.setattr(rs.RigState, "recovery_needed", property(lambda self: True))
    rc = rs.supervise(once=True)
    assert rc == 3
    assert started["run"] is False                                   # never looped into a run


# --- assess_goal / authoritative_success units -----------------------------------------------

def test_assess_goal_reports_recognized_rows():
    reached, outstanding, rows = rs.assess_goal(_STATUS_OK)
    assert reached and outstanding == [] and rows >= 1
    reached2, _, rows2 = rs.assess_goal("garbage\n")
    assert rows2 == 0                                                # caller must treat as inconclusive


def test_authoritative_success_banner_matrix():
    assert rs.authoritative_success(_BANNER) is True
    assert rs.authoritative_success("") is False
    assert rs.authoritative_success(
        "==== auto-install run completed-with-failures: 7/8 stack(s) successful, 1 blocked, "
        "0 failed. ====") is False
    assert rs.authoritative_success(
        "==== auto-install run completed: 7/8 stack(s) successful, 0 blocked, 2 failed. ====") is False
