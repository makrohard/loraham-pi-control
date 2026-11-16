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


# ---- a box password inside a login name stops the start; the redactor masks the literal password -------------------------

def test_a_password_inside_a_login_name_stops_the_start(tmp_path):
    # red-before (42768cef): 'lhp' for box H ran on past the start (it stopped later, at the empty FORMS_OK)
    r = subprocess.run(["bash", str(HW / "rf-proof.sh"), "live"], input="lhp\n\n", env=_env(tmp_path, LAB_ENV=str(HW / "lab.example.env")),
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: box H's password is part of a login name ('lhpc' or BOX_<B>_USER): it cannot be masked "
                             "in the evidence; set another password"]
    assert "== ROW" not in r.stdout


def test_the_redactor_masks_a_password_whatever_the_login_names(tmp_path):
    # red-before (42768cef): with BOX_H_USER=lhpc the redactor left 'auth lhp' unmasked
    r, _ = source_only(tmp_path, "PW_H=lhp; BOX_H_USER=lhpc; printf 'auth lhp\\n' | redact")
    assert r.stdout == "auth <REDACTED>\n"


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


# ---- a STOP inside R0's radio and peer checks ends the run; a malformed peer key is a STOP ---------------------------------

UNKNOWN_KIND = "STOP: PEER_g2: unknown kind 'bogus' (want meshcom, aprs, meshtastic, meshcore-ble or rnode)"


@pytest.mark.parametrize("env, want", [
    ({"RADIO_HSTK": "H ch341-rfm98pw-433 433 bogus=1", "LANE": "fast"}, "STOP: RADIO_HSTK: 'bogus=1' (want serial=<s> or port=<p>)"),
    ({"PEER_g2": "bogus usb-x hubB", "LANE": "full"}, UNKNOWN_KIND),
    ({"PEER_g2": "bogus", "LANE": "full"}, UNKNOWN_KIND),
    ({"PEER_g2": "meshtastic", "LANE": "full"},
     "STOP: PEER_g2: kind 'meshtastic' without its by-id name or BLE address (an empty or unset key = the peer is absent)"),
])
def test_a_malformed_radio_or_peer_key_stops_r0(tmp_path, env, want):
    # red-before: the selector STOP was swallowed and the run went on (42768cef); 'bogus usb-x' read as an absent serial
    # peer (42768cef); 'bogus' without an id read as absent before the kind check (f797c66b)
    r = dry(tmp_path, **env)
    assert r.returncode == 2
    assert stop_lines(r) == [want], r.stderr


def test_an_empty_peer_key_is_an_absent_peer(tmp_path):
    r = dry(tmp_path, LANE="full", ROWS_ONLY="3", PEER_g2="")
    assert r.returncode == 0, r.stderr
    assert "NOT RUN: row 3 meshtastic box -> peer: absent: peer g2" in r.stdout


# ---- the PC's USB snapshot is redacted; an evidence write that fails is a STOP ---------------------------------------------

def test_the_pcs_by_id_list_and_kernel_log_are_redacted(tmp_path):
    # red-before (42768cef): both were written raw, the call in them
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "journalctl").write_text("#!/bin/sh\necho 'kernel: usb N0CALL-7 n0call'\n")
    (fake / "ls").write_text("#!/bin/sh\necho total 0\necho 'lrwxrwxrwx 1 u u 13 x y z usb-N0CALL-9-if00 -> ../../ttyUSB0'\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    r, evd = source_only(tmp_path, "OPERATOR_CALL=N0CALL; T0=0; usbsnap S", PATH=f"{fake}:{os.environ['PATH']}")
    assert r.returncode == 0, r.stderr
    assert (evd / "S-kern.txt").read_text() == "kernel: usb <CALL> <CALL>\n"
    assert (evd / "S-byid.txt").read_text() == "usb-<CALL>-if00 ../../ttyUSB0\n"


@pytest.mark.parametrize("script", [
    'mkdir "$EVD/a.txt.rc"; capture a.txt true',
    'mkdir "$EVD/results.txt"; REPORT 7 daemon b 433 p A-\\>B yes 2 PASS',
    'mkdir "$EVD/REPORT.md"; REPORT 7 daemon b 433 p A-\\>B yes 2 PASS',
])
def test_an_evidence_write_that_fails_is_a_stop(tmp_path, script):
    # red-before (42768cef): each returned normally
    r, _ = source_only(tmp_path, script)
    assert r.returncode == 2
