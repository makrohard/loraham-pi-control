"""The hardware test line's safety guards (testlab/hardware/rf-proof.sh), through its entries: the dry run, the start of a
live run, and the source-only mode (RFPROOF_SOURCE_ONLY=1: the functions, no stdin, lab file, box or peer). No box, no peer,
no radio: a box or PC command runs as a fake on PATH or as a plain shell command in a temporary evidence directory."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HW = Path(__file__).resolve().parents[2] / "hardware"
REPO = HW.parents[1]


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RADIO_", "PEER_", "BOX_", "DRY_", "LANE", "ROWS_ONLY"))}
    env.update(TMPDIR=str(tmp_path), **extra)
    return env


def source_only(tmp_path, script, **extra):
    """`script` after sourcing rf-proof.sh in source-only mode, in live mode, with $EVD a fresh evidence directory."""
    evd = tmp_path / "evd"
    evd.mkdir()
    prelude = (f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"\n')
    r = subprocess.run(["bash", "-c", prelude + script], env=_env(tmp_path, **extra), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120, check=False)
    return r, evd


def dry(tmp_path, hw=HW, **extra):
    return subprocess.run(["bash", str(hw / "rf-proof.sh"), "dry"], env=_env(tmp_path, **extra), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=300, check=False)


def stop_lines(r):
    return [line for line in r.stderr.splitlines() if line.startswith("STOP: ")]


# ---- an undo that fails stops the run; an undo is marked done only after it ran ------------------------------------------

def test_a_failed_undo_stays_in_the_journal_unmarked_and_fails(tmp_path):
    # red-before (42768cef): undo_to returned 0, the line was gone and the id marked done
    r, evd = source_only(tmp_path, "printf 'u1\\tPC\\tfalse\\n' > \"$EVD/undo.list\"; : > \"$EVD/undo.done\"; undo_to 0")
    assert r.returncode != 0
    assert (evd / "undo.list").read_text() == "u1\tPC\tfalse\n"
    assert "u1" not in (evd / "undo.done").read_text().split()
    assert "PC: false (rc 1)" in (evd / "UNRESOLVED.txt").read_text()


def test_an_undo_is_marked_done_only_after_it_ran(tmp_path):
    # red-before (f797c66b): the id was marked done BEFORE the undo ran (an interrupt there made the recovery skip it);
    # this undo succeeds only when it is not yet marked
    r, evd = source_only(tmp_path, "printf 'u1\\tPC\\t! grep -qx u1 %s\\n' \"$EVD/undo.done\" > \"$EVD/undo.list\"; "
                                   ": > \"$EVD/undo.done\"; undo_to 0")
    assert r.returncode == 0, r.stderr
    assert (evd / "undo.list").read_text() == ""
    assert (evd / "undo.done").read_text().split() == ["u1"]


def test_rs_stops_when_an_undo_fails(tmp_path):
    # red-before (42768cef): RS with a failing undo ended in a row FAIL (rc 1), not a STOP
    r, _ = source_only(tmp_path, "undo_to() { return 1; }; row_RS")
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: RS: an undo failed (UNRESOLVED.txt): the recovery runs it again"]
