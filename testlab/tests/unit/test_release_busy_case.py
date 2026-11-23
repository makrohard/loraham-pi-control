"""release-verify's busy-channel check of the Reticulum case: `busy_case_wrong` reads the fake daemon's tx.jsonl,
the interface's RF log and the node's start log (from recorded marks), and `set_lab_flags` arms the lab's fake
daemon through scenario.json. Recorded text only: what is under test is the reading, not a running stack."""
from __future__ import annotations

import json

import lhpc_testlab.release as rel

L = 41                                            # one framed path request
REFUSED = {"band": "868", "len": L, "result": "CHANNEL_BUSY"}
SENT = {"band": "868", "len": L, "result": "OK"}
RF_OK = f'2026-11-23T12:00:00.000Z TX rssi=- snr=- len={L} outcome=ok-cad-timeout hex=00 ascii="."'
NODE = ("[2026-11-23 13:00:00] [Notice] LoRaDaemonInterface[LoRa r2] sent 40 bytes after 1 refused tries"
        " (channel-busy 1, busy 0, not-ready 0; 0.4 s)")


def test_one_refused_try_logged_as_the_interface_must_is_right():
    assert rel.busy_case_wrong([REFUSED, SENT], RF_OK + "\n", NODE + "\n") == ""


def test_another_band_and_an_earlier_unrelated_tx_do_not_count():
    other = {"band": "433", "len": 9, "result": "CHANNEL_BUSY"}
    assert rel.busy_case_wrong([other, SENT, REFUSED, SENT], RF_OK, NODE) == ""


def test_a_tx_line_for_the_refused_try_is_wrong():
    spurious = f'2026-11-23T12:00:00.000Z TX rssi=- snr=- len={L} outcome=channel-busy hex=00 ascii="."'
    assert rel.busy_case_wrong([REFUSED, SENT], f"{spurious}\n{RF_OK}", NODE).startswith(
        "RF line: TX line(s) with outcome ['channel-busy']")


def test_a_plain_ok_or_two_lines_of_the_packet_are_wrong():
    plain = RF_OK.replace("ok-cad-timeout", "ok")
    assert rel.busy_case_wrong([REFUSED, SENT], plain, NODE).startswith("RF line: the TX lines of len 41 are ['ok']")
    assert rel.busy_case_wrong([REFUSED, SENT], f"{RF_OK}\n{RF_OK}", NODE).startswith("RF line:")


def test_a_node_line_with_another_count_or_none_is_wrong():
    for node in (NODE.replace("channel-busy 1, busy 0, not-ready 0", "channel-busy 0, busy 0, not-ready 1"),
                 NODE.replace("after 1 refused tries (channel-busy 1", "after 2 refused tries (channel-busy 2"),
                 "", f"{NODE}\n{NODE}"):
        assert rel.busy_case_wrong([REFUSED, SENT], RF_OK, node).startswith("node line:"), node


def test_no_refusal_two_refusals_or_no_sent_retry_are_wrong():
    assert rel.busy_case_wrong([SENT], RF_OK, NODE).startswith("tx.jsonl pair: the fake daemon refused 0")
    assert rel.busy_case_wrong([REFUSED, REFUSED, SENT], RF_OK, NODE).startswith("tx.jsonl pair: the fake daemon refused 2")
    assert rel.busy_case_wrong([REFUSED], RF_OK, NODE).startswith("tx.jsonl pair: no OK of len 41")
    assert rel.busy_case_wrong([REFUSED, dict(SENT, len=L + 1)], RF_OK, NODE).startswith("tx.jsonl pair: no OK")


def test_logs_are_read_from_their_marks(tmp_path):
    log = tmp_path / "rf.log"
    assert rel.file_mark(log) == 0 and rel.read_since(log, 0) == ""
    log.write_text("before\n")
    mark = rel.file_mark(log)
    with open(log, "a") as fh:
        fh.write("after\n")
    assert rel.read_since(log, mark) == "after\n"


def test_set_lab_flags_merges_into_the_scenario_and_waits_for_the_re_read(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(rel.time, "sleep", waits.append)
    scn = tmp_path / "state" / "testlab" / "scenario.json"
    scn.parent.mkdir(parents=True)
    scn.write_text(json.dumps({"name": "t", "applied_boottime": 0, "auto_revert_s": None,
                               "flags": {"radio_868": "READY"}}))
    rel.set_lab_flags({"LHPC_RUNTIME_ROOT": str(tmp_path)}, txbusy_868=1, txcadtimeout_868=True)
    assert json.loads(scn.read_text())["flags"] == {"radio_868": "READY", "txbusy_868": 1, "txcadtimeout_868": True}
    assert waits == [rel.SCENARIO_REREAD_S] and rel.SCENARIO_REREAD_S > 0.8
