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


NOTE_H = "NOTE: box H password equals its login name (lab box); not masked"


def live_start(tmp_path, stdin, **extra):
    return subprocess.run(["bash", str(HW / "rf-proof.sh"), "live"], input=stdin,
                          env=_env(tmp_path, LAB_ENV=str(HW / "lab.example.env"), **extra),
                          capture_output=True, text=True, timeout=60, check=False)


def test_a_password_equal_to_its_login_name_passes_the_start_with_the_note(tmp_path):
    # red-before (849fb082): 'lhpc' for box H (BOX_H_USER=lhpc) was refused as part of a login name
    r = live_start(tmp_path, "lhpc\n\n")
    assert NOTE_H in r.stdout, r.stdout + r.stderr
    assert stop_lines(r) == ["STOP: FORMS_OK is empty: confirm the forms at INTHEAD first"], r.stderr


@pytest.mark.parametrize("pw", ["lhp", "hpc", "lhpc "])
def test_a_password_only_part_of_the_login_name_is_still_refused(tmp_path, pw):
    # only the EXACT login name is allowed: a part of it (a trailing blank too) stays a STOP before any row
    r = live_start(tmp_path, f"{pw}\n\n")
    assert stop_lines(r) == ["STOP: box H's password is part of a login name ('lhpc' or BOX_<B>_USER): it cannot be masked "
                             "in the evidence; set another password"], r.stderr
    assert NOTE_H not in r.stdout and "== ROW" not in r.stdout


def test_another_password_passes_the_start_without_the_note(tmp_path):
    r = live_start(tmp_path, "LHPC\n\n")      # not the login name (case counts), not part of it: masked as before
    assert NOTE_H not in r.stdout and stop_lines(r) == ["STOP: FORMS_OK is empty: confirm the forms at INTHEAD first"], r.stderr


def test_a_password_equal_to_the_other_boxs_login_name_is_still_refused(tmp_path):
    # equal to box Z's login, not its own (box H logs in as lhpc): masking it would garble box Z's login, so STOP
    r = live_start(tmp_path, "zed\n\n", BOX_Z_USER="zed")
    assert stop_lines(r)[:1] == ["STOP: box H's password is part of a login name ('lhpc' or BOX_<B>_USER): it cannot be "
                                 "masked in the evidence; set another password"], r.stderr


def test_a_password_equal_to_its_login_name_is_not_masked_and_another_still_is(tmp_path):
    r, _ = source_only(tmp_path, "PW_H=lhpc; BOX_H_USER=lhpc; PW_Z=zz-Q71; printf 'login lhpc auth zz-Q71\\n' | redact")
    assert r.stdout == "login lhpc auth <REDACTED>\n", r.stderr


def test_the_report_header_carries_the_note_only_for_an_equal_password(tmp_path):
    r, _ = source_only(tmp_path, "PW_H=lhpc; BOX_H_USER=lhpc; PW_Z=zz-Q71; BOX_Z_USER=lhpc; pwnote; echo --; "
                                 "PW_Z=lhpc; pwnote; echo --; PW_H=x9; PW_Z=; pwnote")
    assert r.stdout == (NOTE_H + "\n--\n" + NOTE_H + "\nNOTE: box Z password equals its login name (lab box); not masked\n--\n"), r.stderr


@pytest.mark.parametrize("pw,head", [("lhpc", [NOTE_H, ""]), ("x9", [])])
def test_row_r0_writes_the_note_above_the_report_table(tmp_path, pw, head):
    # R0 opens REPORT.md, then stops (INTHEAD unset) with the header already written
    r, evd = source_only(tmp_path, f'EVD="$EVD/run"; OUT=$EVD; PW_H={pw}; BOX_H_USER=lhpc; row_R0')
    lines = (evd / "run" / "REPORT.md").read_text().splitlines()
    assert lines[:len(head) + 1] == [*head, "| row | stack | box | band | peer | direction | nonce seen | power used | result |"], r.stderr


# ---- R0 and the end check run `{LH} status` on a box: through a stand-in box that runs each command in a real bash --------

HEAD40 = "a" * 40
STATUS = "1 stacks, 1 components: 1 running\n[controller] lhpc controller  (running)\n[kiss] KISS TNC  (running)\n"


def box_stand_in(tmp_path):
    """sshpass on PATH as a box: it runs the command the box's login shell gets ('bash -lc <cmd>') in a real bash, so a
    placeholder left in a command fails there as on a box (rc 127); the box's runtime holds a fake lhpc and python, and
    the host-bound commands (git, ss, the journal test) answer as a box at INTHEAD would. Each command lands in box.log."""
    box = tmp_path / "box"
    rtbin = box / "rt" / "venv" / "lhpc" / "bin"
    rtbin.mkdir(parents=True)
    (rtbin / "lhpc").write_text(f'#!/bin/bash\n[ "$*" = status ] || exit 3\nprintf %s "{STATUS}"\n')
    (rtbin / "python").write_text('#!/bin/bash\ncase "$2" in *radios.load*) echo "STATE valid";; '
                                  '*radio_models*) echo "CAP ch341-rfm98pw-433 17";; *ControllerService*) echo kiss@r1;; '
                                  '*) exit 3;; esac\n')
    (box / "env.sh").write_text(f'git() {{ echo {HEAD40}; }}; ss() {{ echo "ESTAB 0 0 192.0.2.2:22 192.0.2.1:50000"; }}\n'
                                'test() { [ "$*" = "-d /var/log/journal" ] || builtin test "$@"; }\n')
    pc = tmp_path / "bin"
    pc.mkdir()
    (pc / "sshpass").write_text(f'#!/bin/bash\nfor a; do last=$a; done; eval "set -- $last"\n'
                                f'[ "$1 $2" = "bash -lc" ] || exit 99; printf "%s\\n" "$3" >> "{box}/box.log"\n'
                                f'BASH_ENV="{box}/env.sh" exec bash -c "$3"\n')
    (pc / "gh").write_text(f'#!/bin/sh\necho "{HEAD40} success"\n')
    for f in [*rtbin.iterdir(), *pc.iterdir()]:
        f.chmod(0o755)
    setup = (f'. "{HW}/zstatus.sh"; declare -A ABSENT=(); BOXES=H; RUN=; LANE=fast; PW_H=pw-h-Q71; PW_Z=; BOX_H_HOST=box-h; '
             f'BOX_H_RUNTIME="{box}/rt"; INTHEAD={HEAD40}; INTCI=1; FORMS_HEAD=$INTHEAD; '
             'usbsnap() { :; }; boxkern() { :; }; ')   # the PC's and the box's kernel logs (journalctl, sudo): another path
    return box, setup, {"PATH": f"{pc}:{os.environ['PATH']}"}


def test_r0_runs_the_box_status_and_key_read_and_records_the_running_set(tmp_path):
    # red-before (f256f271): R0 sent a literal "{LH} status" to the box: rc 127, STOP (the kit run 1B stop at R0)
    box, setup, env = box_stand_in(tmp_path)
    r, evd = source_only(tmp_path, setup + 'EVD="$EVD/run"; OUT=$EVD; row_R0 && echo R0-DONE', **env)
    assert "R0-DONE" in r.stdout, r.stdout + r.stderr
    run = evd / "run"
    assert (run / "R0-H-status.txt").read_text() == STATUS and (run / "R0-H-status.txt.rc").read_text() == "0\n"
    assert "data: R0 H: running instances: 'kiss@r1'" in r.stdout
    log = (box / "box.log").read_text()
    assert f"{box}/rt/venv/lhpc/bin/lhpc status" in log and "{" + "LH}" not in log and "{" + "RT}" not in log


def test_the_end_check_runs_the_box_status_and_finds_the_recorded_set(tmp_path):
    # red-before (f256f271): verify_set (RS and the recovery) sent a literal "{LH} status": rc 127, "status/key read failed"
    box, setup, env = box_stand_in(tmp_path)
    r, evd = source_only(tmp_path, setup + 'keep RUN_H kiss@r1; verify_set && echo VS-DONE', **env)
    assert "ok: H: the recorded running instances run again" in r.stdout and "VS-DONE" in r.stdout, r.stdout + r.stderr
    assert (evd / "VS-H.txt").read_text() == STATUS
    assert f"{box}/rt/venv/lhpc/bin/lhpc status" in (box / "box.log").read_text()


def test_a_pc_command_naming_the_box_lhpc_is_a_stop(tmp_path):
    r, evd = source_only(tmp_path, 'X PC f.txt "{LH} status; touch \\"$RAN\\""')
    assert stop_lines(r) == ["STOP: a PC command names a box path ({RT} or {LH})"], r.stderr
    assert not (evd / "ran").exists()


# ---- the power boundary: no power set on stdin in any context; the setter and the reader run only their own command -------

@pytest.mark.parametrize("ctx", ["set", "read", "restore"])
def test_a_power_set_on_stdin_stops_in_every_power_context(tmp_path, ctx):
    # red-before (cd825a69): under PCTX=set|read|restore the stdin payload was not checked: the box ran it
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; '
                                   f'f() {{ local PCTX={ctx}; XIN H p.txt "lhpc daemon r1 --set POWER=20 --yes" "bash -s"; }}; f')
    assert stop_lines(r) == ["STOP: p.txt: a power token in the stdin payload: never run"], r.stderr
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("ctx", ["set", "read", "restore"])
@pytest.mark.parametrize("cmd", ["lhpc daemon r1 --set POWER=\\$(printf 20) --yes", "touch \\\"\\$RAN\\\""])
def test_under_the_power_setter_or_reader_only_its_own_command_runs(tmp_path, ctx, cmd):
    # red-before (cd825a69): the context flag alone let any command through: both ran under set and restore, the plain
    # command under read (the reader's check already refused the set form there); POWER=$(printf 20) has no digit after the
    # token: a value that is not a literal number now counts as over
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; f() {{ local PCTX={ctx}; X H p.txt "{cmd}"; }}; f')
    want = ("the reader's command carries a power set" if ctx == "read" else "the setter's command carries a power above") \
        if "POWER" in cmd else "under the power setter or reader only its own command runs: never run"
    assert len(stop_lines(r)) == 1 and want in stop_lines(r)[0], r.stderr
    assert not (evd / "ran").exists()


def test_the_setter_runs_only_its_fixed_form_with_a_literal_value(tmp_path):
    # red-before (cd825a69): a setter form with a shell expansion before its value (POWER=$(printf 2)0 = 20 on the box) ran
    r, evd = source_only(tmp_path, "keep MODEL_H_r1 loraham; keep CAP_H_loraham 17; sshrun() { touch \"$EVD/ran\"; }; "
                                   "F[dset]='{LH} daemon {rid} --set POWER=$(printf 2){p} --yes'; pset H s.txt r1 0")
    assert r.returncode == 2 and not (evd / "ran").exists(), r.stderr


@pytest.mark.parametrize("value", ["$(printf 20)", "`printf 20`", "$P", "${P}"])
def test_a_power_value_that_is_not_a_literal_number_counts_as_over(tmp_path, value):
    # red-before (cd825a69): pover saw no digits after the token, so the expansion passed the setter's check
    r, _ = source_only(tmp_path, f"pover 'lhpc daemon r1 --set POWER={value} --yes' && echo OVER")
    assert r.stdout.strip() == "OVER", r.stderr


def test_the_evidence_file_is_opened_before_its_command_runs(tmp_path):
    # red-before (cd825a69): the command ran while its evidence file could not be opened (then the run STOPped)
    r, evd = source_only(tmp_path, 'mkdir "$EVD/x.txt"; X PC x.txt "touch \\"\\$RAN\\""')
    assert r.returncode == 2, r.stderr
    assert not (evd / "ran").exists()


def test_the_end_scan_skips_an_equal_password_and_still_scans_the_other(tmp_path):
    r, _ = source_only(tmp_path, 'PW_H=lhpc; BOX_H_USER=lhpc; PW_Z=zz-Q71; OPERATOR_CALL=XX9TST; '
                                   'printf "login lhpc\\nzz-Q71\\n" > "$EVD/x.txt"; secret_scan')
    assert "ok: RE: box H password equals its login name (lab box): not scanned" in r.stdout, r.stdout + r.stderr
    assert "RE: box password in the evidence" in r.stdout + r.stderr, r.stdout + r.stderr


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
         ("XIN H f.txt \"Type --help for commands\"", "python3 - 'rfkit 0000aaaa' 12323"),
         ("X H f.txt", "LHPC_PROOF=Z:meshchat@r2 printf '%s\\n' 'rfkit 0000aaaa' > ~/x/5-tx2.go")]   # row 5's release


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


# ---- the guard's context flags come only from tx, pset and pread, never from the environment or the lab file ---------------

CTX_STOP = "STOP: PCTX/TXCTX are set in the environment: the guard's context comes only from tx, pset and pread"


@pytest.mark.parametrize("flag", [{"PCTX": "read"}, {"PCTX": "set"}, {"TXCTX": "1"}])
def test_an_inherited_context_flag_stops_the_start(tmp_path, flag):
    # red-before (3416e029): the dry run went on with the flag in the environment (and the guard open)
    r = dry(tmp_path, LANE="fast", **flag)
    assert r.returncode == 2
    assert stop_lines(r) == [CTX_STOP]
    assert "== ROW" not in r.stdout


@pytest.mark.parametrize("line", ["PCTX=read", "TXCTX=1"])
def test_a_context_flag_in_the_lab_file_stops_the_start(tmp_path, line):
    # red-before (3416e029): a lab file that set the flag opened the guard for the whole run
    lab, n = _lab_with(tmp_path, line)
    r = dry(tmp_path, LANE="fast", LAB_ENV=str(lab))
    assert r.returncode == 2
    assert stop_lines(r) == [f"STOP: {lab}:{n}: '{line.split('=')[0]}' is not a lab key"]


# ---- the lab file is data: KEY=VALUE entries of the lab keys only, never run as code -------------------------------------

def _lab_with(tmp_path, line):
    """The example lab file with one more line; returns the file and that line's number."""
    text = (HW / "lab.example.env").read_text()
    lab = tmp_path / "lab.env"
    lab.write_text(text + line + "\n")
    return lab, text.count("\n") + 1


KV = "not a KEY=VALUE line (the lab file is data, never run)"
UQ = "an unquoted value is one word of [A-Za-z0-9_.,:/@+=-] (quote it)"


@pytest.mark.parametrize("line, why", [
    ("F[dset]='{LH} daemon {rid} --set POWER=20 --yes'", KV),   # a form (the second read's P1a: the dry run ended "all 13 OK")
    ("TXMAX=50", "'TXMAX' is not a lab key"), ("PCAP=20", "'PCAP' is not a lab key"),   # the script's constants
    ("STICK_MIN=20", "'STICK_MIN' is not a lab key"),
    ("SSHPASS=x", "'SSHPASS' is not a lab key"), ("BOXPW=x", "'BOXPW' is not a lab key"),   # secret-like names the name
    ("MCPIN=x", "'MCPIN' is not a lab key"),                                                # rule does not catch
    ("pset() { :; }", KV), ("export RADIO_H433='H loraham 433'", KV), ("touch /nonexistent/ran", KV),
    ('RADIO_H433="H loraham $(touch $TMPDIR/ran)"', 'RADIO_H433: a "..." value is one line without $ ` \\ or "'),
    ("RADIO_H433=`touch $TMPDIR/ran`", "RADIO_H433: " + UQ),
    ("RADIO_H433='H loraham 433'; touch $TMPDIR/ran", "RADIO_H433: text after its closing quote"),
    ("RADIO_H433=~/x", "RADIO_H433: " + UQ), ("RADIO_H433='H loraham 433", "the quote of RADIO_H433 is not closed"),
    ("BOX_H_HOST=a b", "BOX_H_HOST: " + UQ),
])
def test_a_lab_file_line_that_is_not_a_lab_key_value_stops_the_start(tmp_path, line, why):
    # red-before (7bed19a0): the lab file was sourced: the form, the constants, the names and the commands were taken
    lab, n = _lab_with(tmp_path, line)
    r = dry(tmp_path, LANE="fast", LAB_ENV=str(lab))
    assert r.returncode == 2
    assert stop_lines(r) == [f"STOP: {lab}:{n}: {why}"], r.stderr
    assert "== ROW" not in r.stdout
    assert not (tmp_path / "ran").exists()


def test_the_lab_reader_gives_the_values_the_example_file_means(tmp_path):
    keys = 'for k in $(compgen -v); do [[ $k =~ $LABKEYS ]] && printf "%s=%q\\n" "$k" "${!k}"; done; true'
    r, _ = source_only(tmp_path, f'labread "{HW}/lab.example.env"; {keys}')
    assert r.returncode == 0, r.stderr
    # what bash makes of the same file (sourced in a separate shell, only to compare)
    want = subprocess.run(["bash", "-c", f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; . "{HW}/lab.example.env"; {keys}'],
                          env=_env(tmp_path, RFPROOF_SOURCE_ONLY="1"), capture_output=True, text=True, check=True)
    assert sorted(r.stdout.splitlines()) == sorted(want.stdout.splitlines())
    assert "DRY_RADIOS_Z=$'STATE valid\\nROW r1 loraham 433" in r.stdout


def test_a_key_in_the_environment_wins_over_the_lab_file(tmp_path):
    r, _ = source_only(tmp_path, f'labread "{HW}/lab.example.env"; echo "$OPERATOR_CALL $BOX_H_HOST"', OPERATOR_CALL="DL0ENV")
    assert r.stdout == "DL0ENV box-h.example\n", r.stderr


# ---- the setter's own command: no power above the cap and no high-power flag, whatever its form says ---------------------

@pytest.mark.parametrize("form", ["{LH} daemon {rid} --set POWER=20 --yes", "{LH} daemon {rid} --set POWER={p} --high-power --yes",
                                  "{LH} daemon {rid} --set POWER={p} --set TXPOWER=018 --yes",
                                  "{LH} daemon {rid} --set POWER={p} --set high_power=True --yes"])
def test_a_setter_form_with_a_power_above_the_cap_is_a_stop(tmp_path, form):
    # red-before (7bed19a0): under the setter's context the guard checked no power: POWER=20 reached the box
    r, evd = source_only(tmp_path, f"F[dset]='{form}'; keep MODEL_H_r1 loraham; keep CAP_H_loraham 17; "
                                   'sshrun() { touch "$EVD/ran"; }; pset H set.txt r1 10')
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: set.txt: the setter's command carries a power above 17 dBm or a high-power flag: never run"]
    assert not (evd / "ran").exists()


def test_the_dry_self_check_fails_on_a_setter_command_above_the_cap(tmp_path):
    # red-before (7bed19a0): the no-high-power self-check scanned only the commands outside the setter and reader
    r, _ = source_only(tmp_path, 'DRYLOG=$EVD/dry.log; printf "%s\\n" "CMD[set] H: lhpc daemon r1 --set POWER=20 --yes" '
                                 '"CMD[set] H: lhpc daemon r2 --set POWER=10 --yes" > "$DRYLOG"; selfcheck')
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: dry self-check no-high-power: 1 setter command(s) carry a high-power flag or a power above 17 dBm"]


def test_the_power_cap_is_a_constant(tmp_path):
    r, _ = source_only(tmp_path, 'PCAP=20')
    assert r.returncode != 0 and "readonly" in r.stderr


@pytest.mark.parametrize("cmd", ["X PC f.txt 'touch \"$RAN\"; : --set POWER=20'", "X PC f.txt 'touch \"$RAN\"; : --high-power'",
                                 "X H f.txt 'tmux send-keys -t rfchat-r1 -- x Enter'"])
@pytest.mark.parametrize("flag", [{"PCTX": "set"}, {"TXCTX": "1"}])
def test_an_inherited_context_flag_opens_nothing_even_when_sourced(tmp_path, cmd, flag):
    # red-before (3416e029): with PCTX exported the power commands ran, with TXCTX exported the send ran (the probe's P2, P3, P5)
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$RAN"; }}; {cmd}', **flag)
    assert r.returncode == 2
    assert stop_lines(r) == [CTX_STOP]
    assert not (evd / "ran").exists()


def test_the_recovery_runs_with_the_context_cleared(tmp_path):
    # red-before (3416e029): an exit inside pread/pset/tx left its local flag visible, so the recovery's undos ran with the
    # guard open (here: a power command in the journal ran)
    r, evd = source_only(tmp_path, ': > "$EVD/undo.done"; printf "u1\\tPC\\ttouch \\"\\$RAN\\"; : POWER=20\\n" > "$EVD/undo.list"; '
                                   'f() { local PCTX=set; recover; }; f')
    assert not (evd / "ran").exists(), r.stderr


# ---- a power restore sets exactly the journalled pre-state; a refused or failed undo leaves the others running -------------

def _peer_fake(tmp_path):
    marker = tmp_path / "peer-args"
    fake = tmp_path / "peer.py"
    fake.write_text(f"import sys\nopen({str(marker)!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\nprint('POWER 1')\n")
    return marker, fake


def test_a_peer_restore_above_17_is_refused_reported_and_never_set(tmp_path):
    # supersedes 94d8b1b7 (a peer found above 17 was set back there): that restore would itself break 'never 20 dBm'.
    # One power domain 0..17 dBm for read, set and restore; a pre-state outside it STOPs the row before any change (below).
    # red-before (cd825a69): the replay set mt-power 30
    marker, fake = _peer_fake(tmp_path)
    r, evd = source_only(tmp_path, 'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; VMT=python3; keep PRE_PC_mt-power 30; '
                                   ': > "$EVD/undo.done"; printf "u1\\tPC\\t@pset PC mt-power 30\\n" > "$EVD/undo.list"; undo_to 0',
                         FAKE=str(fake))
    assert r.returncode == 1, r.stderr
    assert not marker.exists()
    assert "PC: @pset PC mt-power 30 (rc 2)" in (evd / "UNRESOLVED.txt").read_text().splitlines()


@pytest.mark.parametrize("pre", ["-3", "0", "18", "30"])
def test_a_peer_pre_state_outside_the_power_domain_stops_the_row_before_any_change(tmp_path, pre):
    # red-before (cd825a69): ppower journalled the pre-state and set the peer low; a -3 restore was refused later (the
    # peer stayed changed), a 30 restore set 30 again; Meshtastic's 0 is its firmware's maximum
    marker = tmp_path / "peer-args"
    fake = tmp_path / "peer.py"
    fake.write_text(f"import sys\nopen({str(marker)!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\nprint('POWER {pre}')\n")
    r, evd = source_only(tmp_path, 'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; VMT=python3; : > "$EVD/undo.list"; '
                                   'echo 0 > "$EVD/undo.seq"; CURROW=3; ppower R3-g2 g2 mt-power 1', FAKE=str(fake))
    assert stop_lines(r) == [f"STOP: PC:mt-power: pre-state power '{pre}' outside 0..17 dBm (Meshtastic 1..17): "
                             "nothing changed; set it into the domain by hand first"], r.stderr
    assert marker.read_text() == "mt-power\n"                      # the read only, never a set
    assert (evd / "undo.list").read_text() == ""


@pytest.mark.parametrize("judge, ok", [('jstatus "STATUS HIGHPOWER=0 POWER=-3"', False), ('jstatus "STATUS HIGHPOWER=0 POWER=-9"', False),
                                       ('jstatus "STATUS HIGHPOWER=0 POWER=0"', True), ('jstatus "STATUS HIGHPOWER=0 POWER=17"', True),
                                       ("jspower rt_pget -3", False), ("jspower rt_pget 0", True), ("jspower mt_pget 0", False),
                                       ("jspower mt_pget 1", True), ("pdom 0", True), ("pdom 17", True), ("pdom 18", False),
                                       ("pdom -1", False), ("pdom 0 mt-power", False), ("pdom ''", False), ("pdom 1x", False)])
def test_every_power_judge_has_the_one_domain(tmp_path, judge, ok):
    # red-before (cd825a69): the box STATUS and a stack's value accepted -9..-1 (no restore can set them back); no pdom
    r, _ = source_only(tmp_path, judge + " >/dev/null && echo IN || echo OUT")
    assert r.stdout.strip() == ("IN" if ok else "OUT"), r.stderr


def test_a_box_restore_sets_its_journalled_pre_state(tmp_path):
    r, evd = source_only(tmp_path, 'keep PRE_H_r1 12; sshrun() { printf "%s\\n" "$3" > "$EVD/sent"; }; : > "$EVD/undo.done"; '
                                   'printf "u1\\tH\\t@pset H r1 12\\n" > "$EVD/undo.list"; undo_to 0')
    assert r.returncode == 0, r.stderr
    assert (evd / "sent").read_text().split()[1:] == ["daemon", "r1", "--set", "POWER=12", "--yes"]   # after the lhpc binary


@pytest.mark.parametrize("pre", ["20", ""])
def test_a_restore_of_another_value_is_refused_reported_and_the_older_undos_still_run(tmp_path, pre):
    # red-before (d20b8c41): the refused replay STOPped undo_to: the older undo (u1) never ran and nothing was reported
    marker, fake = _peer_fake(tmp_path)
    keep = f"keep PRE_PC_mt-power {pre}; " if pre else ""
    r, evd = source_only(tmp_path, 'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; VMT=python3; ' + keep +
                         ': > "$EVD/undo.done"; printf "u1\\tPC\\ttouch \\"\\$RAN\\"\\nu2\\tPC\\t@pset PC mt-power 30\\n" > "$EVD/undo.list"; '
                         'undo_to 0', FAKE=str(fake))
    assert r.returncode == 1
    assert not marker.exists()                                    # the peer never got mt-power 30
    assert (evd / "ran").exists()                                 # u1 ran after u2 was refused
    assert "PC: @pset PC mt-power 30 (rc 2)" in (evd / "UNRESOLVED.txt").read_text().splitlines()
    assert (evd / "undo.list").read_text() == "u2\tPC\t@pset PC mt-power 30\n"   # kept for the recovery


def test_a_peer_power_change_journals_its_pre_state_and_its_restore(tmp_path):
    # the pre-state the restore may set is the value read before the row set the peer low
    marker, fake = _peer_fake(tmp_path)
    r, evd = source_only(tmp_path, 'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; VMT=python3; : > "$EVD/undo.list"; '
                                   'echo 0 > "$EVD/undo.seq"; CURROW=3; ppower R3-g2 g2 mt-power 1', FAKE=str(fake))
    assert r.returncode == 0, r.stderr
    assert "PRE_PC_mt-power=1" in (evd / "state.env").read_text().splitlines()
    assert (evd / "undo.list").read_text() == "u1\tPC\t@pset PC mt-power 1\n"
    assert marker.read_text() == "mt-power\nmt-power 1\n"


# ---- the rnode peer names its RNS shared instance; a missing or malformed field is a STOP at R0 ----------------------------

@pytest.mark.parametrize("fields, want", [
    ("", "instance= missing or malformed ('')"),
    ("instance=a/b port=1 control=2", "instance= missing or malformed ('a/b')"),
    ("instance=lhpc-rf-host control=37439", "port= missing or not 1..65535 ('')"),
    ("instance=lhpc-rf-host port=0 control=37439", "port= missing or not 1..65535 ('0')"),
    ("instance=lhpc-rf-host port=37438", "control= missing or not 1..65535 ('')"),
    ("instance=lhpc-rf-host port=37438 control=70000", "control= missing or not 1..65535 ('70000')"),
    ("instance=lhpc-rf-host port=37438 control=x", "control= missing or not 1..65535 ('x')"),
    ("instance=lhpc-rf-host port=37438 control=37438", "port= and control= are the same (37438)"),
])
def test_a_missing_or_malformed_rns_instance_field_stops_r0(tmp_path, fields, want):
    # red-before (d7f61ebe): the fields were not read; R0 went on into the rows
    r = dry(tmp_path, LANE="full", ROWS_ONLY="5", PEER_rnode=f"rnode usb-Example_RNode_CP2102-if00-port0 hubA {fields}".strip())
    assert r.returncode == 2
    assert stop_lines(r) == [f"STOP: PEER_rnode: {want}"]
    assert "== ROW R1" not in r.stdout


def test_a_valid_rns_instance_passes_r0(tmp_path):
    r = dry(tmp_path, LANE="full", ROWS_ONLY="5")       # the example's instance=lhpc-rf-host port=37438 control=37439
    assert "== ROW R1" in r.stdout, r.stderr
    assert not [line for line in stop_lines(r) if "PEER_rnode" in line]


# ---- a dry FAIL is rc 0: a FAILed row's later forms are not "needed but never printed" -------------------------------------

STATUS_868 = ("STATUS RADIO=READY TX=0 TXMODE=MANAGED TXQUEUE=1 RXREADY=1 HIGHPOWER=0 CHIPFAMILY=SX127x OCP=100 USBWAIT=- "
              "BOUND=- ID=r2 FREQ=868.000 SF=11 POWER=10")


@pytest.mark.parametrize("row, key, freqs", [("11", "DRY_STATUS_Z_r2", "869.525000 vs 868.000"),
                                             ("10", "DRY_STATUS_Z_r1", "433.775000 vs 433.775")])
def test_a_single_frame_row_with_a_freq_mismatch_fails_with_rc_0_in_dry(tmp_path, row, key, freqs):
    # red-before (7bed19a0): the all-forms-printed self-check found the FAILed row's later forms missing: STOP, rc 2
    status = STATUS_868 if key.endswith("r2") else STATUS_868.replace("ID=r2 FREQ=868.000", "ID=r1 FREQ=433.775")
    r = dry(tmp_path, LANE="fast", ROWS_ONLY=row, **{key: status})
    assert r.returncode == 0, r.stderr
    assert stop_lines(r) == []
    results = [line.split(" | ")[-1] for line in r.stdout.splitlines() if line.split(" | ")[:2] == ["REPORT", row]]
    assert results == [f"FAIL: incompatible: FREQ {freqs}"] * 2, r.stdout   # both directions
    assert "dry self-check OK all-forms-printed" in r.stdout


# ---- the dispatcher's checks after the power read: boot id, the PC's by-id list, the sending box's USB list ----------------

TXH = ('BOXES=H; CURROW=9; printf "%s\\n" "rfkit 0000aaaa" > "$EVD/nonces"; keep RAD_K H:r1; keep PWROW_H_r1 9; keep BOOT_H B1; '
       'printf "usb-a\\n" > "$EVD/R0-H-usb.txt"; printf "by-a\\n" > "$EVD/R0-usb-byid.txt"; '
       'usbsnap() { printf "%s\\n" "${BYID:-by-a}" > "$EVD/$1-byid.txt"; : > "$EVD/$1-kern.txt"; }; '
       'sshrun() { case "$3" in *boot_id*) echo "${BOOT:-B1}";; *) echo "${USB:-usb-a}";; esac; }; '
       'tx K 1 "rfkit 0000aaaa" -- X PC f.txt "touch \\"\\$RAN\\"; : rfkit 0000aaaa"')


def test_a_send_with_the_box_and_the_pc_unchanged_runs(tmp_path):
    r, evd = source_only(tmp_path, TXH)
    assert r.returncode == 0, r.stderr
    assert (evd / "ran").exists()


@pytest.mark.parametrize("env, want", [
    ({"BOOT": "B2"}, "STOP: tx K: box H rebooted (boot id changed)"),
    ({"USB": "usb-b"}, "STOP: tx K: box H's USB device list changed since R0"),
    ({"BYID": "by-b"}, "STOP: tx K: a PC peer re-enumerated or vanished"),
])
def test_a_changed_boot_id_or_usb_list_stops_the_send(tmp_path, env, want):
    # mutants K7, K8, K9 of the second read: each check replaced by `true`, and the send ran
    r, evd = source_only(tmp_path, TXH, **env)
    assert r.returncode == 2
    assert stop_lines(r) == [want]
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("sel, answer, rc", [("serial=S1", "1", 0), ("serial=S1", "0", 2), ("port=1-2", "1", 0), ("port=1-2", "0", 2),
                                            ("serial=S1", "2", 2)])
def test_a_usb_radio_gone_after_a_frame_is_a_stop(tmp_path, sel, answer, rc):
    # mutant K10: the check after each frame never stopped
    r, _ = source_only(tmp_path, f'RADIO_HSTK="H ch341-rfm98pw-433 433 {sel}"; sshrun() {{ echo {answer}; }}; usbcheck f.txt HSTK')
    assert r.returncode == rc, r.stderr
    assert stop_lines(r) == ([] if rc == 0 else ["STOP: HSTK (H) is gone after a frame (brown-out / re-enumeration)"])


FRAME_MIN = ('keep H433 r1; keep HSTK r3; needs() { :; }; undo_mark() { echo 0; }; started() { :; }; jpower() { :; }; '
             'pset() { XRC=0; SET=1; }; '
             'dpower() { local p=10; [ -z "${SET:-}" ] || p=$RB; printf "STATUS FREQ=433.1 SF=11 POWER=%s\\n" "$p" > "$OUT/PW-$3.txt"; '
             'keep "P_$3" "$p"; }; tx() { exit 7; }; frame_row 7 H:H433 H:HSTK min,min same-box')


@pytest.mark.parametrize("rb, rc", [("2", 7), ("5", 2), ("", 2)])
def test_a_min_endpoint_that_does_not_read_back_the_minimum_stops_before_the_send(tmp_path, rb, rc):
    # mutant K25: the readback after the set was not checked (rc 7 = the stub tx: the row reached its send)
    r, _ = source_only(tmp_path, FRAME_MIN, RB=rb)
    assert r.returncode == rc, r.stderr
    if rc == 2:
        assert stop_lines(r) == [f"STOP: row 7: H:r1 reads back '{rb}' dBm after POWER=2: no TX"]


# ---- R0's refusals: a disabled radio, a high-power grant, an invalid hardware list, the wrong box, a missing serial peer ----

RADIOS_H = ("STATE valid\nROW r1 loraham 433 True serial= port= hp=None\nROW r2 loraham 868 True serial= port= hp=None\n"
            "ROW r3 ch341-rfm98pw-433 433 True serial=STICK433A port=1-2 hp=False\n"
            "ROW r4 pinedio-usb-1262 868 True serial= port=1-1 hp=False\nROW r5 meshstick-1262 868 True serial=MSTICK01 port=1-3 hp=False")


def _null_peer(tmp_path):
    fake = tmp_path / "nullpeer.py"
    fake.write_text("import sys\n")
    return str(fake)


def test_a_disabled_radio_is_absent(tmp_path):
    # mutant K2: a disabled radio (ROW ... False) still resolved
    r = dry(tmp_path, LANE="fast", DRY_RADIOS_H=RADIOS_H.replace("r1 loraham 433 True", "r1 loraham 433 False"))
    assert r.returncode == 0, r.stderr
    assert "NOT RUN: row 7 daemon A->B: absent: radio H433" in r.stdout, r.stdout


def test_a_high_power_grant_at_r0_stops_before_anything_starts(tmp_path):
    # mutant K32: the grant was not a STOP (in dry this is the only grant check)
    r = dry(tmp_path, LANE="fast", DRY_RADIOS_H=RADIOS_H.replace("r1 loraham 433 True serial= port= hp=None",
                                                                 "r1 loraham 433 True serial= port= hp=True"))
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: R0: row 7, H433 = r1: the high-power grant of r1 is 'True' (want None or False): nothing starts"]
    assert "== ROW R1" not in r.stdout


@pytest.mark.parametrize("radios, first", [("STATE invalid\nROW r1 loraham 433 True serial= port= hp=None", "STATE invalid"),
                                           (RADIOS_H.replace("STATE valid", "STATE valid\nSTATE valid"), "STATE valid"),
                                           (RADIOS_H + "\nROW r9 loraham 433 maybe serial= port= hp=None", "STATE valid")])
def test_an_invalid_hardware_list_stops_r0(tmp_path, radios, first):
    # mutant K33: the list's state was not checked (the fix of 6f484fc8 had no kept test)
    r = dry(tmp_path, LANE="fast", DRY_RADIOS_H=radios)
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: R0 H: the hardware list is not valid (want exactly one 'STATE valid' and well-formed ROW lines): "
                             + first], r.stderr


def test_an_endpoint_on_the_other_box_stops_r0(tmp_path):
    # mutant K34: RADIO_H433 naming box Z was taken for row 7's box-H endpoint
    r = dry(tmp_path, LANE="fast", RADIO_H433="Z loraham 433")
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: row 7: H433 is on box Z, the table says H"]


def test_a_serial_peer_missing_from_the_pcs_by_id_list_is_absent(tmp_path):
    # mutant K35: the by-id check was skipped, the peer read as present
    r = dry(tmp_path, LANE="full", ROWS_ONLY="3", FAKE_PEER=_null_peer(tmp_path), DRY_BYID="usb-Example_TDeck_MeshCom-if00")
    assert r.returncode == 0, r.stderr
    assert "NOT RUN: row 3 meshtastic box -> peer: absent: peer g2" in r.stdout


# ---- rows and the end scan: an ACK from another call, row 5's control files, the operator's call ---------------------------

@pytest.mark.parametrize("lines, want", [
    ("ACKED t msg 1\nCALL-DIFF", "row 2 peer -> box: the ACK frame's source differs from the given call (not the tracker's ACK)"),
    ("ACKED t msg 1\nNO-ACK-FRAME", "row 2 peer -> box: Graywolf has 'n1' acked, but no ACK frame in the rf log"),
    ("ACKED t msg 1\nCALL-MATCH", None),
])
def test_an_ack_from_another_call_is_a_row_fail(tmp_path, lines, want):
    # mutant K36: CALL-DIFF was not a FAIL (the fix of 3e2fa79c had no kept test)
    r, evd = source_only(tmp_path, f'printf "%s\\n" "{lines}" > "$EVD/2-box-send.txt"; ackjudge 2-box-send.txt n1')
    assert r.returncode == (0 if want is None else 1), r.stderr
    if want:
        assert (evd / "rowfail.txt").read_text() == want + "\n"


def test_row_5s_control_files_stay_in_the_private_scratch(tmp_path):
    # mutant K37: the release and destination files were in the evidence directory again (the second half of c033d2e6)
    r = dry(tmp_path, LANE="full", ROWS_ONLY="5", FAKE_PEER=_null_peer(tmp_path))
    assert r.returncode == 0, r.stderr
    paths = [w for line in r.stdout.splitlines() for w in line.split() if w.endswith(("/5-tx2.go", "/5-pcdest.txt"))]
    assert {Path(w).name for w in paths} == {"5-tx2.go", "5-pcdest.txt"}, r.stdout
    assert all(Path(w).parent.parent == tmp_path for w in paths), paths   # the scratch (TMPDIR/rf-proof.*), not its out/


def test_the_end_scan_finds_the_operators_call(tmp_path):
    # mutant K12: RE no longer scanned for the call
    r, evd = source_only(tmp_path, 'OPERATOR_CALL=N0CALL; TRACKER_CALL=; printf "de n0call-7 k\\n" > "$EVD/x.txt"; secret_scan')
    assert r.returncode == 1
    assert (evd / "rowfail.txt").read_text() == "RE: the operator's call (any SSID) in the evidence\n"


def test_the_end_scan_passes_clean_evidence(tmp_path):
    r, _ = source_only(tmp_path, 'OPERATOR_CALL=N0CALL; TRACKER_CALL=; printf "nothing here\\n" > "$EVD/x.txt"; secret_scan')
    assert r.returncode == 0, r.stderr


# ---- the start's refusals --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("env, want", [
    ({"OPERATOR_CALL": "N0CALL-7"}, "STOP: OPERATOR_CALL is not a callsign without an SSID (3..8 of A-Z 0-9)"),
    ({"OPERATOR_CALL": "n0call"}, "STOP: OPERATOR_CALL is not a callsign without an SSID (3..8 of A-Z 0-9)"),
    ({"EVD_ROOT": "/tmp/rf"}, "STOP: EVD_ROOT is under /tmp: the evidence lives under /home"),
    ({"EVD_ROOT": "/tmp"}, "STOP: EVD_ROOT is under /tmp: the evidence lives under /home"),
    ({"EVD_ROOT": "rf"}, "STOP: EVD_ROOT is not an absolute path"),
])
def test_a_bad_call_or_evidence_root_stops_the_start(tmp_path, env, want):
    # mutants K13, K16: neither was refused
    r = dry(tmp_path, LANE="fast", **env)
    assert r.returncode == 2
    assert stop_lines(r) == [want]
    assert "== ROW" not in r.stdout


@pytest.mark.parametrize("line", ["RADIO_PW=x", "PEER_pin=1234", "DRY_SECRET=x", "BOX_H_PASSWORD=x"])
def test_a_secret_name_in_the_lab_file_stops_the_start(tmp_path, line):
    # mutant K15: the lab file's secret names were not refused
    lab, _ = _lab_with(tmp_path, line)
    r = dry(tmp_path, LANE="fast", LAB_ENV=str(lab))
    assert r.returncode == 2
    assert stop_lines(r) == [f"STOP: {lab} sets a *PASS*/*PIN*/*SECRET* variable: secrets come on stdin only"]


def test_an_incomplete_forms_ok_stops_a_live_start(tmp_path):
    # mutant K17: the missing forms were printed and the run went on; both password lines empty, so no box is reached
    r = subprocess.run(["bash", str(HW / "rf-proof.sh"), "live"], input="\n\n",
                       env=_env(tmp_path, LAB_ENV=str(HW / "lab.example.env"), LANE="fast", FORMS_OK="start"),
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: form not confirmed: dset rx_rf stop tx_text (FORMS_OK must hold every form of the chosen rows)"]
    assert "== ROW" not in r.stdout


def test_row_2_alone_needs_only_the_forms_it_renders(tmp_path):
    # red-before (7f58f02c): row 2 listed rx_rf, which it never renders (its Graywolf helper reads the RF log itself):
    # ROWS_ONLY=2 in dry ended "needed but never printed: rx_rf", rc 2
    r = dry(tmp_path, LANE="full", ROWS_ONLY="2", FAKE_PEER=_null_peer(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "dry self-check OK all-forms-printed" in r.stdout


# ---- what H2's dry tests pinned alone: an ambiguous key, row 5's worst case, the tracker's ACK, COVERAGE, stop-isolation ---

def test_a_key_matching_two_radios_is_ambiguous_not_the_first(tmp_path):
    # mutant K1: the first match was taken
    r = dry(tmp_path, LANE="fast", DRY_RADIOS_Z="STATE valid\nROW r1 loraham 433 True serial= port= hp=None\n"
                                                "ROW r2 loraham 868 True serial= port= hp=None\nROW r3 loraham 868 True serial= port= hp=None")
    assert r.returncode == 0, r.stderr
    assert [line for line in r.stdout.splitlines() if line.split(": ", 1)[0] == "NOT RUN"] == [
        f"NOT RUN: row {n} daemon {d}: ambiguous: radio Z868 (2 match)" for n in (8, 9, 11) for d in ("A->B", "B->A")]


def test_row_5s_worst_case_is_three_frames_each_end(tmp_path):
    # mutant K5: row 5 counted one frame per end (its announce, delivery proof and own send; the RNode's three)
    r = dry(tmp_path, LANE="full", ROWS_ONLY="5", FAKE_PEER=_null_peer(tmp_path))
    assert r.returncode == 0, r.stderr
    r0 = r.stdout.split("== ROW R0", 1)[1].split("== ROW R1", 1)[0]
    assert [line for line in r0.splitlines() if line.split(" | ")[0] == "BUDGET"] == ["BUDGET | PC:rnode | 3 | 10", "BUDGET | Z:r2 | 3 | 10"]


def test_the_fake_box_frames_the_trackers_ack(tmp_path):
    # mutant K14: the dry box printed no FRAME for the tracker's ACK, so its reservation was never exercised
    r = dry(tmp_path, LANE="full", ROWS_ONLY="2", FAKE_PEER=_null_peer(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "FRAME | PC:tracker | control" in r.stdout.splitlines()


def test_coverage_names_the_models_no_chosen_row_carries(tmp_path):
    # mutant K21: the "not present" lines were dropped
    r = dry(tmp_path, LANE="fast", ROWS_ONLY="11")
    assert r.returncode == 0, r.stderr
    assert "COVERAGE | meshstick-1262 | model | - | - | not present | no row carries this model" in r.stdout.splitlines()


def test_the_same_box_frame_row_proves_stop_isolation(tmp_path):
    # mutant K22: row 7's stop-isolation direction was gone from its directions (COVERAGE, and the FAIL marking)
    r = dry(tmp_path, LANE="fast", ROWS_ONLY="7")
    assert r.returncode == 0, r.stderr
    assert "COVERAGE | stop-isolation | check | 7 | - | rehearsed | -" in r.stdout.splitlines()
    assert ("REPORT | 7 | daemon | H (r1, r3) | 433 | — | stop-isolation | stop r3: r1 runs on; stop r1: r3 runs on | — | DRY-PASS"
            in r.stdout.splitlines())


# ---- a lab value is words of [A-Za-z0-9._/@:+=-], checked per key at the start; every value spliced into a shell is quoted ---

def _q(value):
    return subprocess.run(["bash", "-c", 'printf %q "$1"', "_", value], capture_output=True, text=True, check=True).stdout


GRAMMAR = "a lab value is [A-Za-z0-9._/@:+=-] words only"
INJECT = ["~/x; lhpc daemon r1 --set POWER=20 --yes #", "~/x y", "~/x$(touch $TMPDIR/ran)", "~/x\nlhpc daemon r1 --set POWER=20 --yes",
          "~/x; lhpc daemon r1 --set POW$(printf E)R=20 --yes; cd ~/loraham-pi-control",   # the delta read's V1
          "~/x\n~/y"]   # two lines, each a path on its own


@pytest.mark.parametrize("value", INJECT)
@pytest.mark.parametrize("key", ["VENV_MT", "PEER_LOCK", "RNS_CONFIG", "BOX_H_RUNTIME", "VENV_RNS", "EVD_ROOT"])
def test_a_path_value_that_is_not_one_word_stops_the_start(tmp_path, key, value):
    # red-before (146a815a): '…' took any text, and the path reached peerrun's bash -c, hpower's read and every box command
    lab, _ = _lab_with(tmp_path, f"{key}='{value}'")
    r = dry(tmp_path, LANE="fast", LAB_ENV=str(lab))
    assert r.returncode == 2
    assert stop_lines(r) == [f"STOP: {key}: {_q(value)} is not a path (or empty): {GRAMMAR}"], r.stderr
    assert "== ROW" not in r.stdout
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("key, value, kind", [
    ("VENV_MT", "~/x; touch ran", "a path (or empty)"), ("BOX_H_RUNTIME", "-x", "a path (or empty)"),
    ("BOX_H_HOST", "-oProxyCommand=x", "a host (or empty)"), ("BOX_H_USER", "u;x", "a login (or empty)"),
    ("RADIO_HSTK", "H ch341-rfm98pw-433 433 serial=S'1", "fields (or empty)"), ("PEER_g2", "meshtastic usb-x hubB;id", "fields (or empty)"),
    ("DRY_RADIOS_H", "STATE valid\nROW r1 loraham 433 True serial=$(id) port= hp=None", "fields (or empty)"),
])
def test_a_lab_key_from_the_environment_meets_the_same_grammar(tmp_path, key, value, kind):
    # red-before (146a815a): a key set in the environment won over the file unchecked
    r = dry(tmp_path, LANE="fast", **{key: value})
    assert r.returncode == 2
    assert stop_lines(r) == [f"STOP: {key}: {_q(value)} is not {kind}: {GRAMMAR}"], r.stderr


def test_the_peer_process_command_quotes_its_lock_python_and_script(tmp_path):
    # red-before (146a815a): `flock -n -E 75 $LOCK $py -I $PEER` split the lock path at the blank and ran `touch ran2`
    r, evd = source_only(tmp_path, 'ln -s "$(command -v python3)" "$EVD/py thon"; LOCK="$EVD/l ock;touch ran2"; '
                                   'PEER="$EVD/p eer.py"; printf "import sys\\nprint(\\"PEER-RAN\\", sys.argv[1:])\\n" > "$PEER"; '
                                   'peer f.txt "$EVD/py thon" aprs-read x; cat "$EVD/f.txt"')
    assert r.returncode == 0, r.stderr
    assert r.stdout == "PEER-RAN ['aprs-read', 'x']\n"
    assert not (evd / "ran2").exists()


def test_the_rns_read_quotes_its_paths(tmp_path):
    # red-before (146a815a): `python3 -I hpower.py $RNSCFG $(dirname $VRNS)/rnstatus` split the paths and ran `touch ran2`
    r, evd = source_only(tmp_path, 'SCRATCH="$EVD"; printf "import sys\\nprint(sys.argv[1:])\\n" > "$EVD/hpower.py"; '
                                   'RNSCFG="$EVD/rns cfg;touch ran2"; VRNS="$EVD/v env/bin/python"; pread PC f.txt hpower; cat "$EVD/f.txt"')
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"['{evd}/rns cfg;touch ran2', '{evd}/v env/bin/rnstatus']\n"
    assert not (evd / "ran2").exists()


def test_the_box_runtime_path_is_one_word_in_a_command_and_as_is_in_a_script(tmp_path):
    # red-before (146a815a): {RT} went into the box command as it was: `cd ~/rt dir && true`
    r, evd = source_only(tmp_path, 'BOX_H_RUNTIME="~/rt dir"; sshrun() { printf "%s\\n" "$3" >> "$EVD/cmds"; printf "%s\\n" "$2" >> "$EVD/ins"; }; '
                                   'X H a.txt "cd {RT} && true"; XIN H b.txt "x = \'{RT}\'" "cat"')
    assert r.returncode == 0, r.stderr
    assert (evd / "cmds").read_text() == 'cd "$HOME"/rt\\ dir && true\ncat\n'
    assert (evd / "ins").read_text() == "-\nx = '~/rt dir'\n"   # a python text on stdin: the path as is


# ---- the power check under the one reader: a read form carries no power set ----------------------------------------------

@pytest.mark.parametrize("cmd", ["lhpc daemon r1 --set POWER=20 --yes", "lhpc daemon r1 --set POWER=10 --yes", "x --high-power",
                                 "lhpc meshtastic@r1 --set lora.tx_power 1", "meshcom set tx 5", "x --set HIGHPOWER=1"])
def test_a_power_set_under_the_readers_context_is_a_stop(tmp_path, cmd):
    # red-before (146a815a): under PCTX=read the guard checked nothing: a set form reached the box
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; f() {{ local PCTX=read; X H f.txt "{cmd}"; }}; f')
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: f.txt: the reader's command carries a power set, a high-power flag or a power above 17 dBm: never run"]
    assert not (evd / "ran").exists()


@pytest.mark.parametrize("name", ["radios", "caps", "mt_pget", "rt_pget", "mt_cap"])
def test_the_readers_own_forms_pass_its_check(tmp_path, name):
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; pread H f.txt {name} r1')
    assert r.returncode == 0, r.stderr
    assert (evd / "ran").exists()
