"""The hardware test line's receive proof of row 6 and its RESULT table (testlab/hardware/rf-proof.sh), in source-only mode
and dry: a row-6 direction passes only when its word is in the receiving radio's log AND on the other box's chat screen;
after a run one table for people: the verdict on top, one line per row with each direction's PASS / FAIL / NOT RUN. No box,
no radio: the boxes are a shell with a fake tmux whose panes are files."""
import os
import subprocess
from pathlib import Path

import pytest

HW = Path(__file__).resolve().parents[2] / "hardware"


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RADIO_", "PEER_", "BOX_", "DRY_", "LANE", "ROWS_ONLY"))}
    env.update(TMPDIR=str(tmp_path), **extra)
    return env


def source_only(tmp_path, script, **extra):
    """`script` after sourcing rf-proof.sh in source-only mode, in live mode, with $EVD a fresh evidence directory."""
    evd = tmp_path / "evd"
    evd.mkdir()
    prelude = f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"\n'
    r = subprocess.run(["bash", "-c", prelude + script], env=_env(tmp_path, **extra), stdin=subprocess.DEVNULL, cwd=evd,
                       capture_output=True, text=True, timeout=120, check=False)
    return r, evd


# ---- row 6: the word must show on the other box's chat screen; the radio-log check stays ---------------------------------

# Two boxes in one shell: box H's chat pane on radio r1, box Z's on r2, each a file. A line typed into one pane shows there,
# and on the other pane only when that pane is in SHOW (the other box's chat client displayed what its radio got).
FAKE_BOX = r'''
tmux() { local me to
  case "$1" in
    capture-pane) cat "$PANES/$BOX-${@: -1}" 2>/dev/null; return 0;;   # the target is the last argument
    send-keys) me="$BOX-$3"; printf '%s\n' "me: $5" >> "$PANES/$me"
      case "$me" in H-rfchat-r1) to=Z-rfchat-r2;; Z-rfchat-r2) to=H-rfchat-r1;; esac
      case " $SHOW " in *" ${to/rfchat-/} "*) printf '%s\n' "peer: $5" >> "$PANES/$to";; esac; return 0;;
    *) return 0;; esac; }
pgrep() { return 0; }
sleep() { :; }
'''

# The row's own steps run (X, its guard, the evidence files, REPORT, fail); the parts other tests own are stubbed: the forms,
# the power, the undo journal, the TX checks (tx runs its command in the send context), and the radio-log check (HEARD).
ROW6 = r'''
sshrun() { BOX=$1 BASH_ENV="$PANES/../env.sh" bash -c "$3"; }
needs() { return 0; }; form() { echo true; }; chatcmd() { echo true; }; dpower() { :; }
undo_mark() { echo 0; }; undo_push() { :; }; undo_to() { :; }
boxrx() { [ "${HEARD:-yes}" = yes ]; }
tx() { while [ "$1" != -- ]; do shift; done; shift; local TXCTX=1; "$@"; }
printf 'k1=r1\nk2=r2\n' >> "$EVD/state.env"
( CURROW=6; row_6 H:k1 Z:k2 ); echo "ROW-RC=$?"
'''


def row6(tmp_path, show, heard="yes"):
    panes = tmp_path / "box" / "panes"
    panes.mkdir(parents=True)
    (tmp_path / "box" / "env.sh").write_text(FAKE_BOX)
    for p in ("H-rfchat-r1", "Z-rfchat-r2"):
        (panes / p).write_text("DL9XYZ-7: a received line\nLoRaHAM chat   Ctrl-K: Konf\n")   # a third party's line; the footer: ready
    r, evd = source_only(tmp_path, ROW6, PANES=str(panes), SHOW=show, HEARD=heard)
    results = dict(line.split("|", 2)[1:] for line in (evd / "results.txt").read_text().splitlines()) \
        if (evd / "results.txt").exists() else {}
    return r, evd, results


def test_row_6_passes_both_directions_when_each_word_shows_on_the_other_chat_screen(tmp_path):
    r, evd, results = row6(tmp_path, show="Z-r2 H-r1")
    assert "ROW-RC=0" in r.stdout, r.stdout + r.stderr
    assert results == {"box -> peer": "PASS", "peer -> box": "PASS"}
    assert (evd / "6-screen-Z.txt").read_text().startswith("SEEN rfkit ")
    assert (evd / "6-screen-H.txt").read_text().startswith("SEEN rfkit ")


def test_a_word_in_the_radio_log_but_not_on_the_chat_screen_fails_that_direction_only(tmp_path):
    # red-before (54ddaa4a): row 6 passed on the radio log alone, though box Z's chat client never showed H's word
    r, evd, results = row6(tmp_path, show="H-r1")
    assert "ROW-RC=1" in r.stdout, r.stdout + r.stderr
    assert results["box -> peer"].startswith("FAIL: not on the chat screen of Z (r2): rfkit ")
    assert results["peer -> box"] == "PASS"   # the other direction still runs and is judged on its own
    screen = (evd / "6-screen-Z.txt").read_text()
    assert screen == "pane lines=2\nNOT-SEEN\n"   # markers only: a received line (a third party's call) is never written
    assert "DL9XYZ" not in "".join(p.read_text(errors="replace") for p in evd.iterdir() if p.is_file())
    assert (evd / "rowfail.txt").read_text().startswith("not on the chat screen of Z (r2): ")


def test_no_word_on_either_screen_fails_both_directions(tmp_path):
    r, _, results = row6(tmp_path, show="")
    assert "ROW-RC=1" in r.stdout, r.stdout + r.stderr
    assert results["box -> peer"].startswith("FAIL: not on the chat screen of Z (r2): ")
    assert results["peer -> box"].startswith("FAIL: not on the chat screen of H (r1): ")


def test_the_radio_log_check_stays_a_word_on_the_screen_alone_is_not_a_pass(tmp_path):
    r, evd, results = row6(tmp_path, show="Z-r2 H-r1", heard="no")
    assert "ROW-RC=1" in r.stdout, r.stdout + r.stderr
    assert results["box -> peer"].startswith("FAIL: not heard: rfkit ")
    assert results["peer -> box"].startswith("FAIL: not heard: rfkit ")
    assert not (evd / "6-screen-Z.txt").exists()   # the screen is read only after the radio heard the word


# ---- the RESULT table: the verdict on top, one line per row, each direction with its result -----------------------------

def table(tmp_path, run, results, arg="", **extra):
    lines = "".join(f"printf '%s\\n' '{line}' >> \"$EVD/results.txt\"; " for line in results)
    r, evd = source_only(tmp_path, f'RUN="{run}"; {lines}restable {arg}; echo "RT-RC=$?"', **extra)
    return r, evd


def test_the_table_has_the_verdict_on_top_and_one_line_per_row_with_every_direction(tmp_path):
    r, evd = table(tmp_path, "3 6 7", ["3|box -> peer|PASS", "3|peer -> box|NOT RUN: absent: peer g2",
                                       "6|box -> peer|FAIL: not on the chat screen of Z (r1): rfkit 0a1b2c3d",
                                       "6|peer -> box|PASS", "7|A->B|PASS", "7|B->A|PASS", "7|stop-isolation|PASS"])
    assert "RT-RC=0" in r.stdout, r.stdout + r.stderr
    want = ("RESULT: ❌ FAIL: 1 of 7 directions failed, 1 not run\n\n| row | stack | directions |\n|---|---|---|\n"
            "| 3 | meshtastic | box -> peer ✅ PASS · peer -> box ⚪ NOT RUN: absent: peer g2 |\n"
            "| 6 | chat | box -> peer ❌ FAIL: not on the chat screen of Z (r1): rfkit 0a1b2c3d · peer -> box ✅ PASS |\n"
            "| 7 | daemon | A->B ✅ PASS · B->A ✅ PASS · stop-isolation ✅ PASS |\n")
    assert (evd / "RESULT.md").read_text() == want
    assert want in r.stdout


@pytest.mark.parametrize("results, verdict", [
    (["6|box -> peer|PASS", "6|peer -> box|PASS"], "RESULT: ✅ PASS: all 2 directions passed"),
    (["6|box -> peer|PASS", "6|peer -> box|NOT RUN: absent: box Z"], "RESULT: ⚪ NOT COMPLETE: 1 of 2 directions not run"),
    (["6|box -> peer|PASS"], "RESULT: ⚪ NOT COMPLETE: 1 of 2 directions not run"),   # a direction without a result
    (["6|box -> peer|FAIL: not heard: rfkit 0a1b2c3d", "6|peer -> box|FAIL: not heard: rfkit 0a1b2c3d"],
     "RESULT: ❌ FAIL: 2 of 2 directions failed"),
])
def test_the_verdict_is_pass_only_when_every_direction_passed(tmp_path, results, verdict):
    r, evd = table(tmp_path, "6", results)
    assert (evd / "RESULT.md").read_text().splitlines()[0] == verdict, r.stdout + r.stderr


def test_a_result_is_matched_by_its_exact_row_id(tmp_path):
    # row 1's direction is not taken from row 11's line (a substring match would)
    r, evd = table(tmp_path, "1", ["11|box -> peer|PASS", "1|box -> peer|FAIL: not heard: rfkit 0a1b2c3d"])
    assert "| 1 | meshcom | box -> peer ❌ FAIL: not heard: rfkit 0a1b2c3d · peer -> box ⚪ NOT RUN: no result |" \
        in (evd / "RESULT.md").read_text(), r.stdout + r.stderr


def test_a_pipe_in_a_reason_does_not_break_the_table(tmp_path):
    r, evd = table(tmp_path, "6", ["6|box -> peer|FAIL: a|b", "6|peer -> box|PASS"])
    assert "| 6 | chat | box -> peer ❌ FAIL: a/b · peer -> box ✅ PASS |" in (evd / "RESULT.md").read_text(), r.stdout


def test_a_stop_prints_the_table_with_the_rows_not_reached(tmp_path):
    # the recovery after a STOP: the verdict says the run stopped; a direction without a result was not reached
    r, evd = source_only(tmp_path, 'RUN="6 3"; BOXES=; secret_scan() { :; }; : > "$EVD/undo.list"; '
                                   "printf '%s\\n' '6|box -> peer|PASS' >> \"$EVD/results.txt\"; recover; echo REC-DONE")
    assert "REC-DONE" in r.stdout, r.stdout + r.stderr
    text = (evd / "RESULT.md").read_text()
    assert text.splitlines()[0] == "RESULT: ⛔ STOPPED: the run ended early (1 passed, 0 failed, 3 not run)"
    assert "| 6 | chat | box -> peer ✅ PASS · peer -> box ⚪ NOT RUN: not reached (the run stopped) |" in text
    assert "== RESULT" in r.stdout and text in r.stdout


def test_a_stop_after_the_table_was_written_turns_its_verdict_to_stopped(tmp_path):
    # a STOP in RE comes after the table said PASS: the recovery writes it again, STOPPED, with every row's result
    r, evd = source_only(tmp_path, 'RUN=6; BOXES=; secret_scan() { :; }; : > "$EVD/undo.list"; '
                                   "printf '%s\\n' '6|box -> peer|PASS' '6|peer -> box|PASS' >> \"$EVD/results.txt\"; "
                                   'restable > /dev/null; recover; echo REC-DONE')
    assert "REC-DONE" in r.stdout, r.stdout + r.stderr
    text = (evd / "RESULT.md").read_text()
    assert text.splitlines()[0] == "RESULT: ⛔ STOPPED: the run ended early (2 passed, 0 failed, 0 not run)"
    assert "| 6 | chat | box -> peer ✅ PASS · peer -> box ✅ PASS |" in text


def test_a_dry_run_ends_with_the_table_of_a_rehearsal(tmp_path):
    r = subprocess.run(["bash", str(HW / "rf-proof.sh"), "dry"], env=_env(tmp_path, LANE="full", ROWS_ONLY="6"),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300, check=False)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr
    assert ("== RESULT\nRESULT: ⚪ DRY RUN: a rehearsal, nothing sent: 2 of 2 directions rehearsed\n\n"
            "| row | stack | directions |\n|---|---|---|\n"
            "| 6 | chat | box -> peer ⚪ DRY (rehearsed) · peer -> box ⚪ DRY (rehearsed) |\n") in r.stdout
    assert "6-screen-Z.txt: for i in $(seq 1 30); do tmux capture-pane -p -J -t rfchat-" in r.stdout   # the screen check is run


# ---- a stack row stops at its FAIL: its running leg FAILs, the other direction is NOT RUN, naming that FAIL ---------------

def stack_fail(tmp_path, body):
    """runrow 3 with a fake row_3 (`body`), then the table; tx only counts its calls."""
    return source_only(tmp_path, ': > "$EVD/undo.list"; usbguard() { :; }; RUN=3; tx() { echo TX >> "$EVD/tx.calls"; }; '
                                 f'row_3() {{ {body}; }}; runrow 3; echo "RR-RC=$?"; restable')


@pytest.mark.parametrize("body, failed, unrun", [
    # red-before (2742524f): the second direction, never sent, was reported FAIL with the first one's reason
    ('leg "box->peer"; tx K 1 x; fail "not heard: rfkit 0a1b2c3d"', "box -> peer", "peer -> box"),
    # red-before (835e1033): rows 3-5 run peer -> box first; their first leg's FAIL was booked on box -> peer
    ('leg "peer->box"; tx K 1 x; fail "not heard: rfkit 0a1b2c3d"', "peer -> box", "box -> peer"),
    # a FAIL before any leg (the row's setup): the first direction FAILs
    ('tx K 1 x; fail "not heard: rfkit 0a1b2c3d"', "box -> peer", "peer -> box"),
])
def test_a_stack_rows_fail_is_its_running_legs_and_the_other_direction_is_not_run(tmp_path, body, failed, unrun):
    r, evd = stack_fail(tmp_path, body)
    assert "RR-RC=0" in r.stdout, r.stdout + r.stderr          # a FAIL is recorded; the run goes on
    assert (evd / "tx.calls").read_text() == "TX\n"            # the direction after the FAIL sent nothing
    assert sorted((evd / "results.txt").read_text().splitlines()) == sorted([
        f"3|{failed}|FAIL: not heard: rfkit 0a1b2c3d",
        f"3|{unrun}|NOT RUN: after {failed} FAIL: not heard: rfkit 0a1b2c3d"])
    assert (evd / "FAILED.txt").read_text() == "row 3: not heard: rfkit 0a1b2c3d\n"   # the row's verdict stays FAIL
    text = (evd / "RESULT.md").read_text()
    assert text.splitlines()[0] == "RESULT: ❌ FAIL: 1 of 2 directions failed, 1 not run"
    cells = {"box -> peer": "", "peer -> box": ""}
    cells[failed] = f"{failed} ❌ FAIL: not heard: rfkit 0a1b2c3d"
    cells[unrun] = f"{unrun} ⚪ NOT RUN: after {failed} FAIL: not heard: rfkit 0a1b2c3d"
    assert f"| 3 | meshtastic | {cells['box -> peer']} · {cells['peer -> box']} |" in text


def test_a_fail_after_a_reported_leg_is_the_next_directions(tmp_path):
    # the leg named last already has its result (PASS): the FAIL belongs to the direction still without one
    r, evd = stack_fail(tmp_path, 'leg "peer->box"; REPORT 3 meshtastic — — — "peer -> box" yes — PASS; fail "not heard: x"')
    assert (evd / "results.txt").read_text().splitlines() == ["3|peer -> box|PASS", "3|box -> peer|FAIL: not heard: x"], \
        r.stdout + r.stderr
