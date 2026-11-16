"""The hardware test line's safety guards (testlab/hardware/rf-proof.sh), through its entries: the dry run, the start of a
live run, and the source-only mode (RFPROOF_SOURCE_ONLY=1: the functions, no stdin, lab file, box or peer). No box, no peer,
no radio: a box or PC command runs as a fake on PATH or as a plain shell command in a temporary evidence directory."""
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
    # RAN: the marker a command creates when it runs; commands name it as "$RAN" (expanded only when they run), so the
    # guard never sees the temporary path (it carries the test's name, and a name like "power" would be refused)
    prelude = (f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"; '
               'export RAN="$EVD/ran"\n')
    r = subprocess.run(["bash", "-c", prelude + script], env=_env(tmp_path, **extra), stdin=subprocess.DEVNULL, cwd=evd,
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


# ---- power is set only by the one setter and read only by the one reader; sends run only inside tx -----------------------

TX_SETUP = ('BOXES=; CURROW=9; printf "%s\\n" "rfkit 0000aaaa" > "$EVD/nonces"; usbsnap() { :; }; '
            ': > "$EVD/R0-usb-byid.txt"; : > "$EVD/TXG-usb-byid.txt"; : > "$EVD/TXG-usb-kern.txt"; '
            'keep RAD_K PC:g2; keep PWROW_PC_g2 9; ')
# every spelling G1 found, and their class: a power token anywhere in a command, however it is quoted, escaped or spaced
BYPASS = ["--power 20", "--tx-power=20", "tx_power: 18", "txpower = 20", "--set POWER=18", "--high-power", "--highpower",
          "-High-Power", "--power=020", "POWER=00018", "set tx 018", "--power='20'", '--power "20"', "--power x",
          "--power\t20", '--po"wer"=20', "--po\\wer=20", "--power=9223372036854775809", "--set HIGHPOWER=1", "--set POWER=2", "--txp 20"]


@pytest.mark.parametrize("form", BYPASS)
@pytest.mark.parametrize("executor", ["X PC f.txt", "XIN H f.txt -", "tx K 1 control -- X PC f.txt"])
def test_a_power_token_outside_the_setter_and_reader_is_a_stop(tmp_path, form, executor):
    # red-before (06eeb15a): the TAB, the quote fragment, the backslash and the 20-digit value ran (and every power token
    # outside tx ran before f797c66b)
    r, evd = source_only(tmp_path, TX_SETUP + f'sshrun() {{ touch "$EVD/ran"; }}; {executor} "touch \\"\\$RAN\\"; : $FORM"', FORM=form)
    assert r.returncode == 2
    assert not (evd / "ran").exists()


def test_a_power_token_in_the_stdin_payload_is_a_stop(tmp_path):
    # red-before (06eeb15a): XIN checked its command, not its stdin: 'radio --power=20' ran as bash -s input
    r, evd = source_only(tmp_path, 'sshrun() { touch "$EVD/ran"; }; XIN H f.txt "radio --power=20" "bash -s"')
    assert r.returncode == 2
    assert not (evd / "ran").exists()


def test_the_box_password_on_stdin_is_not_a_command(tmp_path):
    r, evd = source_only(tmp_path, 'PW_H=power99; sshrun() { touch "$EVD/ran"; }; XIN H f.txt "$PW_H" "sudo -S true"')
    assert r.returncode == 0, r.stderr
    assert (evd / "ran").exists()


@pytest.mark.parametrize("value, sent", [("2", "2"), ("17", "17"), ("017", "17"), ("07", "7"), ("0", "0")])
def test_the_setter_sets_a_low_power(tmp_path, value, sent):
    r, evd = source_only(tmp_path, f'keep MODEL_H_r1 loraham; keep CAP_H_loraham 17; '
                                   f'sshrun() {{ printf "%s\\n" "$3" > "$EVD/sent"; }}; pset H set.txt r1 {value}')
    assert r.returncode == 0, r.stderr
    assert (evd / "sent").read_text().split()[1:] == ["daemon", "r1", "--set", f"POWER={sent}", "--yes"]   # after the lhpc binary


@pytest.mark.parametrize("value", ["18", "20", "100", "0018", "9223372036854775809", "x", "-1", "+5", "", "1 7"])
def test_the_setter_refuses_a_high_or_malformed_power(tmp_path, value):
    r, evd = source_only(tmp_path, f'keep MODEL_H_r1 loraham; keep CAP_H_loraham 17; sshrun() {{ touch "$EVD/ran"; }}; '
                                   f'pset H set.txt r1 "{value}"')
    assert r.returncode == 2
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("value", ["100", "9223372036854775809"])
def test_the_setter_names_a_value_of_more_than_two_digits(tmp_path, value):
    r, _ = source_only(tmp_path, f'keep MODEL_H_r1 loraham; keep CAP_H_loraham 17; pset H set.txt r1 {value}')
    assert r.returncode == 2
    assert stop_lines(r) == [f"STOP: set.txt: power value '{value}' has more than 2 digits"]


def test_the_setter_keeps_a_models_lower_cap(tmp_path):
    r, evd = source_only(tmp_path, 'keep MODEL_H_r4 pinedio-usb-1262; keep CAP_H_pinedio-usb-1262 14; '
                                   'sshrun() { touch "$EVD/ran"; }; pset H set.txt r4 15')
    assert r.returncode == 2
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("name", ["mt_pget", "rt_pget", "mt_cap", "radios", "caps"])
def test_the_reader_runs_its_fixed_read_forms(tmp_path, name):
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; pread H read.txt {name} r2')
    assert r.returncode == 0, r.stderr
    assert (evd / "ran").exists()


def test_the_reader_refuses_any_other_name(tmp_path):
    r, evd = source_only(tmp_path, 'sshrun() { touch "$EVD/ran"; }; pread H read.txt dset r2')
    assert r.returncode == 2
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("args, runs", [("mt-power 20", False), ("mt-power 1", False), ("mt-power", False)])
def test_the_peer_process_refuses_a_power_action_outside_the_setter_and_reader(tmp_path, args, runs):
    # red-before (f797c66b): the peer process ran 'mt-power 20'
    marker = tmp_path / "ran"
    fake = tmp_path / "peer.py"
    fake.write_text(f"open({str(marker)!r}, 'w').close()\nprint('POWER 1')\n")
    r, _ = source_only(tmp_path, 'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; ' + f'peer p.txt python3 {args}', FAKE=str(fake))
    assert (r.returncode, marker.exists()) == ((0, True) if runs else (2, False)), r.stderr


def test_the_setter_and_reader_run_the_peers_power_actions(tmp_path):
    marker = tmp_path / "ran"
    fake = tmp_path / "peer.py"
    fake.write_text(f"import sys\nopen({str(marker)!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\nprint('POWER 1')\n")
    r, _ = source_only(tmp_path, 'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; VMT=python3; '
                       'pread PC r.txt mt-power; pset PC s.txt mt-power 1', FAKE=str(fake))
    assert r.returncode == 0, r.stderr
    assert marker.read_text() == "mt-power\nmt-power 1\n"


SENDS = [("X H f.txt", None),   # None: the forms table's own tx_text (the frame send), rendered by the script
         ("X H f.txt", "tmux send-keys -t rfchat-r1 -- 'rfkit 0000aaaa' Enter"),
         ("X H f.txt", "~/x/lhpc meshtastic@r1 --sendtext \"rfkit 0000aaaa\" --ack"),
         ("XIN H f.txt \"Type --help for commands\"", "python3 - 'rfkit 0000aaaa' 12323")]


@pytest.mark.parametrize("executor, cmd", SENDS)
def test_a_send_outside_tx_is_a_stop_in_every_executor(tmp_path, executor, cmd):
    # red-before (06eeb15a): an executor ran a send form outside tx
    send = '"$(form tx_text rid=r1 n="rfkit 0000aaaa")"' if cmd is None else '"$CMD"'
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; {executor} {send}', CMD=cmd or "")
    assert r.returncode == 2
    assert not (evd / "ran").exists()


def test_a_peer_send_outside_tx_is_a_stop(tmp_path):
    marker = tmp_path / "ran"
    fake = tmp_path / "peer.py"
    fake.write_text(f"open({str(marker)!r}, 'w').close()\n")
    r, _ = source_only(tmp_path, 'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; '
                       'peer p.txt python3 mt-send "rfkit 0000aaaa"', FAKE=str(fake))
    assert (r.returncode, marker.exists()) == (2, False)


@pytest.mark.parametrize("chain", ["&&", ";", "|"])
def test_a_send_chained_after_a_tx_on_the_same_line_is_a_stop(tmp_path, chain):
    # red-before (06eeb15a): the static scanner passed a line with a tx and a chained second send
    r, evd = source_only(tmp_path, TX_SETUP + f'sshrun() {{ touch "$EVD/ran"; }}; '
                         f'tx K 1 control -- X PC a.txt true {chain} X H b.txt "tmux send-keys -t rfchat-r1 -- x Enter"')
    assert r.returncode == 2
    assert not (evd / "ran").exists()


def test_a_send_inside_tx_runs(tmp_path):
    r, evd = source_only(tmp_path, TX_SETUP + 'tx K 1 "rfkit 0000aaaa" -- X PC f.txt "touch \\"\\$RAN\\"; : tmux send-keys -t rfchat-r1 -- \'rfkit 0000aaaa\'"')
    assert r.returncode == 0, r.stderr
    assert (evd / "ran").exists()


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
