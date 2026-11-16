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


# ---- every high-power form is refused, by the dispatcher and by every executor ------------------------------------------

HIGH = ["--power 20", "--tx-power=20", "tx_power: 18", "txpower = 20", "--set POWER=18", "--high-power", "--highpower",
        "-High-Power", "--power=020", "--tx-power=018", "POWER=00018", "set tx 018", "--power='20'", '--power "20"',
        "--power x", "POWER=17.5", "TXPOWER=100"]
TX_SETUP = ('BOXES=; CURROW=9; printf "%s\\n" "rfkit 0000aaaa" > "$EVD/nonces"; usbsnap() { :; }; '
            ': > "$EVD/R0-usb-byid.txt"; : > "$EVD/TXG-usb-byid.txt"; : > "$EVD/TXG-usb-kern.txt"; '
            'keep RAD_K PC:g2; keep PWROW_PC_g2 9; ')


@pytest.mark.parametrize("form", HIGH)
def test_the_dispatcher_refuses_every_high_power_form(tmp_path, form):
    # red-before: '--power 20', '--tx-power=20', 'txpower = 20', '--highpower' ran on 42768cef; the zero-padded and
    # quoted values and a non-numeric value ran on f797c66b
    r, evd = source_only(tmp_path, TX_SETUP + 'tx K 1 control -- X PC f.txt "touch $EVD/ran; : $FORM"', FORM=form)
    assert r.returncode == 2
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("form", ["--power=020", "--highpower", "POWER=18"])
@pytest.mark.parametrize("executor", ["X PC f.txt", "XIN H f.txt -"])
def test_every_executor_refuses_a_high_power_command_outside_tx(tmp_path, form, executor):
    # red-before (f797c66b): an executor called without tx ran the command
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; {executor} "touch $EVD/ran; : $FORM"', FORM=form)
    assert r.returncode == 2
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("form", ["--set POWER=17 HIGHPOWER=0", "max_power | sed -n x", "txpower | sed", "--get lora.tx_power",
                                  "mt-power 1", "POWER=-9", "--power='2'"])
def test_a_low_power_or_a_power_read_runs(tmp_path, form):
    r, evd = source_only(tmp_path, TX_SETUP + 'tx K 1 control -- X PC f.txt "touch $EVD/ran; : $FORM"', FORM=form)
    assert r.returncode == 0, r.stderr
    assert (evd / "ran").exists()


@pytest.mark.parametrize("args, runs", [("mt-power 20", False), ("mt-power 018", False), ("mt-power 1", True)])
def test_the_peer_process_refuses_a_high_power_action(tmp_path, args, runs):
    # red-before (f797c66b): the peer process ran 'mt-power 20'
    marker = tmp_path / "ran"
    fake = tmp_path / "peer.py"
    fake.write_text(f"open({str(marker)!r}, 'w').close()\nprint('POWER 1')\n")
    r, _ = source_only(tmp_path, f'LOCK="$EVD/lock"; peer p.txt python3 {args}', PEER=str(fake))
    assert (r.returncode, marker.exists()) == ((0, True) if runs else (2, False)), r.stderr


def test_a_send_outside_tx_fails_the_dry_self_check(tmp_path):
    # the static twin of the dispatch check: a planted send form outside tx (in a function the dry run never calls)
    hw = tmp_path / "repo" / "testlab" / "hardware"
    hw.mkdir(parents=True)
    (tmp_path / "repo" / "lhpc").symlink_to(REPO / "lhpc")
    for f in ("zstatus.sh", "lab.example.env"):
        shutil.copy(HW / f, hw / f)
    text = (HW / "rf-proof.sh").read_text()
    anchor = '[ "${RFPROOF_SOURCE_ONLY:-0}" = 1 ] && return 0'
    assert text.count(anchor) == 1
    (hw / "rf-proof.sh").write_text(text.replace(anchor, 'planted() { must H p.txt "$(form tx_text rid=r1 n=x)"; }\n' + anchor))
    r = dry(tmp_path, hw=hw, LANE="fast")
    assert r.returncode == 2
    assert stop_lines(r) == ['STOP: dry self-check sends-only-in-tx: a send outside tx: planted: must H p.txt "$(form tx_text rid=r1 n=x)"']
    assert "dry self-check OK every-send-gated" in r.stdout          # anchor: the dynamic check ran and passed
