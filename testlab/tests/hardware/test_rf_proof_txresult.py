"""The frame rows' send (testlab/hardware/rf-proof.sh: tx_text, txframe, txron): the send waits for the daemon's TX_RESULT, so a
frame the daemon refused or never answered is no exit 0; the daemon's STATUS and STATS are read before the verdict; TXRESULT
is set only on the run's own daemon, journalled first and read back. No box, no radio: a fake framed socket in the test's
directory, and the source-only mode with a fake ssh."""
import socket
import struct
import subprocess
import threading

import pytest
from test_rf_proof_guards import TX_SETUP, source_only, stop_lines

RID = "r97"   # no /run/loraham/loraf-r97.sock on a test host: the form takes its second path, moved into the test's directory


def rendered_send(tmp_path, text="rfkit 0000aaaa", wait=3):
    r, _ = source_only(tmp_path, f'printf "%s" "$(form tx_text rid={RID} n="{text}" to={wait})"')
    assert r.returncode == 0, r.stderr
    cmd = r.stdout
    assert f'"/tmp/loraf-{RID}.sock"' in cmd
    return cmd.replace(f'"/tmp/loraf-{RID}.sock"', f'"loraf-{RID}.sock"')   # relative: AF_UNIX paths are short


def frame(kind, payload):
    return bytes([kind]) + struct.pack("<H", len(payload)) + payload


def tx_result(status, flags=1, seq=7):
    return frame(4, bytes([status, flags]) + struct.pack("<H", seq))


class FakeDaemon:
    """The framed socket of one daemon: reads the client's TX_PACKET, then answers with `chunks` (bytes, sent one by one)."""

    def __init__(self, path, chunks, close=False):
        self.got = b""
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen(1)
        self.chunks, self.close = chunks, close
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        c, _ = self.srv.accept()
        c.settimeout(10)
        while len(self.got) < 3 or len(self.got) < 3 + struct.unpack("<H", self.got[1:3])[0]:
            b = c.recv(4096)
            if not b:
                break
            self.got += b
        for ch in self.chunks:
            c.sendall(ch)
        if self.close:
            c.close()
            return
        self.conn = c   # kept open: a silent daemon


CASES = [  # (answer chunks, close after, exit status, last output line)
    ([frame(1, b"\x00rx"), tx_result(0)], False, 0, "TX_RESULT OK flags=1 seq=7"),
    ([tx_result(0)[:2], tx_result(0)[2:]], False, 0, "TX_RESULT OK flags=1 seq=7"),
    ([tx_result(2)], False, 2, "TX_RESULT CHANNEL_BUSY flags=1 seq=7"),
    ([tx_result(4)], False, 2, "TX_RESULT RADIO_ERROR flags=1 seq=7"),
    ([frame(3, b"frame error")], False, 3, "ERROR frame error"),
    ([], False, 4, "NO_TX_RESULT within 2 s"),
    ([], True, 4, "NO_TX_RESULT: the socket closed first"),
    ([tx_result(2)[:4]], True, 4, "NO_TX_RESULT: the socket closed first"),               # truncated: EOF inside a frame
    ([b"\x04\xff\xff" + b"x" * 8], True, 4, "NO_TX_RESULT: the socket closed first"),  # a length the stream never fills
]


@pytest.mark.parametrize("chunks, close, rc, last", CASES)
def test_the_frame_send_exits_by_the_daemons_answer(tmp_path, monkeypatch, chunks, close, rc, last):
    # red-before (d03d312b): the send closed after writing and exited 0 whatever the daemon did with the frame
    cmd = rendered_send(tmp_path, wait=2)
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.chdir(run)
    fake = FakeDaemon(f"loraf-{RID}.sock", chunks, close)
    p = subprocess.run(["bash", "-c", cmd], cwd=run, capture_output=True, text=True, timeout=30, check=False,
                       stdin=subprocess.DEVNULL)
    assert (p.returncode, p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "") == (rc, last), p.stderr
    assert fake.got == frame(2, b"rfkit 0000aaaa")


# ---- txframe: the verdict per exit status, kept across the reads; the counters before and after ----------------------------

def _txframe(tmp_path, send_rc, stat_rc=0, before="TXRESULT=1", after="TXRESULT=1", up=(5, 9), line="TX_RESULT CHANNEL_BUSY flags=1 seq=0"):
    # the first STATUS/STATS read (before the send) answers `before`; the second (after it) `after`, with exit status stat_rc
    n = tmp_path / "reads"
    fake = (f'sshrun() {{ case "$3" in *loraf-*) echo "{line}"; return {send_rc};; '
            f'*STATS*) echo x >> "{n}"; if [ "$(wc -l < "{n}")" = 1 ]; then echo "STATUS {before}"; echo "STATS UPTIME={up[0]}"; '
            f'return 0; fi; echo "STATUS {after} TXQLAST=CHANNEL_BUSY"; echo "STATS UPTIME={up[1]} TXBUSY=1"; return {stat_rc};; esac; }}; ')
    return source_only(tmp_path, TX_SETUP + fake + 'echo "STATUS CADWAIT=1500" > "$EVD/PW-K.txt"; '
                       'txframe 9 K H r1 "rfkit 0000aaaa"; echo "AFTER rc=$?"')


W = "the daemon's STATUS and STATS before/after in 9-txstat0-H-r1.txt, 9-txstat-H-r1.txt"
LINE = "TX_RESULT CHANNEL_BUSY flags=1 seq=0"


@pytest.mark.parametrize("send_rc, stat_rc, msg", [
    (2, 0, f"TX refused: {LINE} ({W})"),
    (3, 0, f"TX frame error: {LINE} ({W})"),
    (4, 0, f"TX unconfirmed: {LINE}, cause unknown ({W})"),
    (2, 1, f"TX refused: {LINE} ({W}; the read after the send failed, rc 1)"),   # a failed read never turns it into a send
])
def test_a_refused_frame_is_the_rows_fail_after_the_counters_are_read(tmp_path, send_rc, stat_rc, msg):
    # red-before (d03d312b): the send ran under must (a refusal could not reach a verdict) and nothing was read after it
    r, evd = _txframe(tmp_path, send_rc, stat_rc)
    assert r.returncode == 1, r.stderr   # fail(): the row's FAIL (its undos run), not a STOP of the run
    assert f"FAIL: {msg}" in r.stderr.splitlines()
    assert (evd / "9-txstat-H-r1.txt").read_text().splitlines()[-2:] == ["STATUS TXRESULT=1 TXQLAST=CHANNEL_BUSY",
                                                                          "STATS UPTIME=9 TXBUSY=1"]
    assert (evd / "9-txstat0-H-r1.txt").read_text().splitlines()[-1] == "STATS UPTIME=5"
    assert "TXR_PC_g2=1" in (evd / "state.env").read_text().splitlines()   # the refused frame counts in the budget
    # only a TX_RESULT refusal proves the daemon sent nothing (RS may then count 0); a frame error or no answer does not
    assert ("TXREF_PC_g2=1" in (evd / "state.env").read_text().splitlines()) == (send_rc == 2)


@pytest.mark.parametrize("status, kept", [("BUSY", True), ("CHANNEL_BUSY", True), ("RADIO_NOT_READY", True),
                                          ("INVALID_PACKET", True), ("INVALID_BAND", True),
                                          ("RADIO_ERROR", False),   # also a failed transmit after keying: it may have radiated
                                          ("9", False)])            # a status the kit does not know
def test_only_a_refusal_before_keying_is_kept_as_a_frame_the_daemon_never_sent(tmp_path, status, kept):
    r, evd = _txframe(tmp_path, 2, line=f"TX_RESULT {status} flags=0 seq=0")
    assert r.returncode == 1 and f"FAIL: TX refused: TX_RESULT {status} flags=0 seq=0 ({W})" in r.stderr.splitlines(), r.stderr
    assert ("TXREF_PC_g2=1" in (evd / "state.env").read_text().splitlines()) == kept


def test_each_refused_frame_of_a_radio_adds_one_to_its_refused_count(tmp_path):
    # RS compares the last reserved count with the last refused one: two refused frames are TXREF 2, never 1 (a constant
    # would leave the radio's 0 frames UNREADABLE at RS: the safe side, but not the count)
    r, evd = source_only(tmp_path, TX_SETUP + 'sshrun() { case "$3" in *loraf-*) echo "TX_RESULT CHANNEL_BUSY flags=1 seq=0"; '
                         'return 2;; *STATS*) echo "STATUS TXRESULT=1"; echo "STATS UPTIME=5"; return 0;; esac; }; '
                         'echo "STATUS CADWAIT=1500" > "$EVD/PW-K.txt"; '
                         'for i in 1 2; do ( txframe 9 K H r1 "rfkit 0000aaaa" ); echo "SEND rc=$?"; done')
    assert [x for x in r.stdout.splitlines() if x.startswith("SEND")] == ["SEND rc=1"] * 2, r.stdout + r.stderr   # each a FAIL
    state = (evd / "state.env").read_text().splitlines()
    assert [x for x in state if x.startswith(("TXR_", "TXREF_"))] == ["TXR_PC_g2=1", "TXREF_PC_g2=1", "TXR_PC_g2=2", "TXREF_PC_g2=2"]


@pytest.mark.parametrize("after, up, state", [("TXRESULT=0", (5, 9), "TXRESULT=0, UPTIME 5 -> 9"),
                                              ("TXRESULT=1", (50, 2), "TXRESULT=1, UPTIME 50 -> 2")])
def test_a_daemon_restart_during_the_send_is_named_never_a_cause(tmp_path, after, up, state):
    r, _ = _txframe(tmp_path, 4, after=after, up=up)
    assert f"FAIL: TX unconfirmed: {LINE}, cause unknown; daemon state after the send: {state} ({W})" in r.stderr.splitlines()


def test_a_sent_frame_goes_on(tmp_path):
    r, evd = _txframe(tmp_path, 0)
    assert r.returncode == 0, r.stderr
    assert "AFTER rc=0" in r.stdout
    assert (evd / "9-txstat-H-r1.txt").exists()


@pytest.mark.parametrize("send_rc, stat_rc, why", [(1, 0, "9-tx-H-r1.txt: exit status 1"),
                                                    (0, 1, "9-txstat-H-r1.txt: exit status 1")])
def test_a_broken_send_or_unread_counters_after_a_sent_frame_stop_the_run(tmp_path, send_rc, stat_rc, why):
    r, _ = _txframe(tmp_path, send_rc, stat_rc)
    assert r.returncode == 2
    assert [s.split(" (")[0] for s in stop_lines(r)] == [f"STOP: {why}"]


def test_txresult_lost_before_the_send_is_a_stop_without_tx(tmp_path):
    r, evd = _txframe(tmp_path, 0, before="TXRESULT=0")
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: row 9: H:r1 reads TXRESULT '0' before the send (a daemon restart resets it): no TX"]
    assert not (evd / "9-tx-H-r1.txt").exists()


def test_the_wait_is_the_senders_cadwait_plus_ten_seconds(tmp_path):
    r, _ = source_only(tmp_path, 'echo "STATUS CADWAIT=2501 X=1" > "$EVD/PW-K.txt"; txwait K; '
                       'echo "STATUS X=1" > "$EVD/PW-K.txt"; txwait K || echo "NO rc=$?"')
    assert r.stdout.splitlines() == ["13", "0 CADWAIT field(s) in the STATUS (want 1)", "NO rc=1"], r.stderr


# ---- txron: TXRESULT=1 only on the run's own daemon, journalled first, read back --------------------------------------------

def _txron(tmp_path, started=True, found="TXRESULT=0", after="TXRESULT=1", tail=""):
    log = tmp_path / "cmds"
    fake = (f'sshrun() {{ echo "$3" >> "{log}"; case "$3" in *--set=TXRESULT=1*) grep -c TXRESULT=0 "$EVD/undo.list";; '
            f'*"GET STATUS"*) echo "STATUS {after} CADWAIT=1500";; esac; }}; ')
    pre = ('keep STARTED_H_r5 1; ' if started else '') + f'echo "STATUS {found} CADWAIT=1500" > "$EVD/PW-K.txt"; '
    r, evd = source_only(tmp_path, fake + pre + 'txron 8 a H r5 K; echo "AFTER rc=$?"' + tail)
    return r, evd, (log.read_text() if log.exists() else "")


def test_txresult_is_journalled_before_the_set_and_read_back(tmp_path):
    r, evd, cmds = _txron(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "AFTER rc=0" in r.stdout
    assert (evd / "8-txr-a.txt").read_text().strip().splitlines()[-1] == "1"   # the undo was in the journal when the set ran
    assert "daemon r5 --set=TXRESULT=0 --yes" in (evd / "undo.list").read_text()
    assert "daemon r5 --set=TXRESULT=1 --yes" in cmds


def test_recovery_restores_txresult_while_the_daemon_runs_then_stops_it(tmp_path):
    # the row's order: started (its stop journalled) -> txron (the restore journalled after it) -> recovery newest first
    log = tmp_path / "cmds"
    fake = (f'sshrun() {{ echo "$3" >> "{log}"; case "$3" in *"GET STATUS"*) echo "STATUS TXRESULT=1";; esac; }}; ')
    r, _ = source_only(tmp_path, fake + ': > "$EVD/undo.list"; : > "$EVD/undo.done"; echo "STATUS TXRESULT=0 CADWAIT=1500" > "$EVD/PW-K.txt"; m=$(undo_mark); '
                       'started H 8-start-a.txt daemon@r5; txron 8 a H r5 K; : > "' + str(log) + '"; undo_to "$m"; echo "UNDO rc=$?"')
    assert "UNDO rc=0" in r.stdout, r.stderr
    cmds = log.read_text().splitlines()
    restore = [i for i, c in enumerate(cmds) if "--set=TXRESULT=0" in c]
    stop = [i for i, c in enumerate(cmds) if "stack stop daemon@r5" in c]
    assert len(restore) == 1 and len(stop) == 1 and restore[0] < stop[0], cmds


def test_txresult_is_never_set_on_a_daemon_the_run_did_not_start(tmp_path):
    r, _, cmds = _txron(tmp_path, started=False)
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: row 8: H:r5 was not started by this run: TXRESULT is set only on its own daemon: no TX"]
    assert "TXRESULT=1" not in cmds


@pytest.mark.parametrize("found, after, why", [
    ("TXRESULT=0", "TXRESULT=0", "STOP: row 8: H:r5 reads TXRESULT '0' after the set: no TX"),
    ("TXRESULT=0", "RADIO=READY", "STOP: row 8: H:r5 reads TXRESULT '0 TXRESULT field(s) in the STATUS (want 1)' after the set: no TX"),
    ("RADIO=READY", "TXRESULT=1", "STOP: row 8: H:r5: 0 TXRESULT field(s) in the STATUS (want 1): no TX"),   # an older daemon
])
def test_no_txresult_or_no_readback_is_a_stop_before_any_tx(tmp_path, found, after, why):
    r, _, cmds = _txron(tmp_path, found=found, after=after)
    assert r.returncode == 2
    assert stop_lines(r) == [why]
    assert "loraf-" not in cmds


def test_the_txresult_set_passes_the_power_guard_and_is_no_send(tmp_path):
    # the setter runs outside the one power setter (PCTX): its rendered text carries no power token and is no send form
    r, _ = source_only(tmp_path, 't=$(form dtxr rid=r5 v=1); ptoken "$t" && echo PTOKEN; issend "$t" && echo SEND; '
                       't=$(form tx_text rid=r5 n="rfkit 0000aaaa" to=12); ptoken "$t" && echo PTOKEN2; issend "$t" && echo SEND2')
    assert r.stdout.split() == ["SEND2"], r.stderr
