"""The hardware test line's safety guards (testlab/hardware/rf-proof.sh), through its entries: the dry run, the start of a
live run, and the source-only mode (RFPROOF_SOURCE_ONLY=1: the functions, no stdin, lab file, box or peer). No box, no peer,
no radio: a box or PC command runs as a fake on PATH or as a plain shell command in a temporary evidence directory."""
import os
import shutil
import subprocess
import sys
import time
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
    # the TX count (TXC_PY) runs for real: the box's interpreter is this one, on this repo's lhpc (rflog.parse_line)
    (rtbin / "python").write_text(f'#!/bin/bash\ncase "$2" in *rflog.parse_line*) PYTHONPATH="{HW.parents[1]}" exec "{sys.executable}" "$@";; '
                                  '*radios.load*) printf "STATE valid\\nROW r1 ch341-rfm98pw-433 433 True serial=X port= hp=False\\n";; '
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


@pytest.mark.parametrize("mode", ["dry", "live"])
@pytest.mark.parametrize("call", ['X H f-h.txt "true"', 'XIN H f-h.txt "x" "true"'])
def test_a_command_to_an_absent_box_stops_before_anything_runs(tmp_path, mode, call):
    # red-before (8c95b9f9): X and XIN ran a command on a box that was ABSENT (live: an ssh with no password)
    stubs = (f'MODE={mode}; ABSENT[H]="absent: box H"; BOX_H_HOST=box-h; BOX_Z_HOST=box-z; DRYLOG="$EVD/cmd.log"; '
             ': > "$DRYLOG"; sshrun() { : > "$RAN"; }; dryout() { : > "$RAN"; }; dryframes() { :; }; ')
    r, evd = source_only(tmp_path, stubs + call)
    assert r.returncode == 2 and stop_lines(r) == ["STOP: f-h.txt: box H is absent: no command to it"], r.stderr
    assert not (evd / "ran").exists() and (evd / "cmd.log").read_text() == ""   # nothing ran, nothing logged for H
    (tmp_path / "z").mkdir()
    r, evd = source_only(tmp_path / "z", stubs + 'X Z f-z.txt "true" && echo X-DONE')   # a present box: as before
    assert "X-DONE" in r.stdout and (evd / "ran").exists(), r.stdout + r.stderr


@pytest.mark.parametrize("call", ['X H f-h.txt "true"', 'XIN H f-h.txt "x" "true"'])
def test_the_recovery_skips_an_absent_box_records_it_and_goes_on(tmp_path, call):
    # the recovery (RECOVERING=1) must reach its secret scan and RESULT: an absent box is recorded and skipped, never a STOP
    # (after the skip X/XIN return 0 with XRC=255: a `must` around them would still STOP; the recovery's box calls, its
    # undos and verify_set, are X with XNOSTOP=1)
    r, evd = source_only(tmp_path, 'ABSENT[H]="absent: box H"; BOX_H_HOST=box-h; RECOVERING=1; sshrun() { : > "$RAN"; }; '
                         + call + '; echo "GOES-ON XRC=$XRC"')
    assert r.returncode == 0 and "GOES-ON XRC=255" in r.stdout and not stop_lines(r), r.stdout + r.stderr
    assert not (evd / "ran").exists()
    assert (evd / "UNRESOLVED.txt").read_text() == "f-h.txt: box H is absent: no command to it (skipped in the recovery)\n"


def test_r0_takes_a_box_that_does_not_answer_out_of_the_journal_before_the_next_probe(tmp_path):
    # red-before (8c95b9f9): BOXES was narrowed and journalled only after every probe; a STOP or a signal during box Z's
    # probe ran the recovery with box H (absent) still in BOXES
    _box, setup, env = box_stand_in(tmp_path)
    probe = ('sshrun() { [ "$1" = H ] && return 255; [ -e "$RAN.boxes" ] '        # the journal as Z's probe (the first Z call) sees it
             '|| grep "^BOXES=" "$OUT/state.env" | tail -n 1 > "$RAN.boxes"; return 0; }; ')
    r, evd = source_only(tmp_path, setup + 'BOXES="H Z"; PW_Z=pw-z-Q72; BOX_Z_HOST=box-z; ' + probe
                         + 'EVD="$EVD/run"; OUT=$EVD; row_R0', **env)
    assert (evd / "ran.boxes").read_text() == "BOXES=Z\n", r.stdout + r.stderr


def test_the_end_check_runs_the_box_status_and_finds_the_recorded_set(tmp_path):
    # red-before (f256f271): verify_set (RS and the recovery) sent a literal "{LH} status": rc 127, "status/key read failed"
    box, setup, env = box_stand_in(tmp_path)
    r, evd = source_only(tmp_path, setup + 'keep RUN_H kiss@r1; verify_set && echo VS-DONE', **env)
    assert "ok: H: the recorded running instances run again" in r.stdout and "VS-DONE" in r.stdout, r.stdout + r.stderr
    assert (evd / "VS-H.txt").read_text() == STATUS
    assert f"{box}/rt/venv/lhpc/bin/lhpc status" in (box / "box.log").read_text()


R3 = 'keep MODEL_H_r3 ch341-rfm98pw-433; : > "$EVD/undo.list"; echo 0 > "$EVD/undo.seq"; '


@pytest.mark.parametrize("cmd, want", [
    ("echo CAP=$(getk CAP_H_ch341-rfm98pw-433)", "CAP=14"),                    # R0 records the lab's cap, not the model's 17
    ("jmodel H r3 15 || echo NO-TX", "NO-TX"),                                   # an as-is 15 (dpower, spower): no TX
    ("jmodel H r3 14", "14"),
])
def test_the_rfm98pw_stick_is_held_to_setting_14(tmp_path, cmd, want):
    # red-before (54ddaa4a): R0 kept the model cap the box reports (the stand-in's 17) as r3's cap: an as-is 15..17 passed
    _box, setup, env = box_stand_in(tmp_path)
    r, _ = source_only(tmp_path, setup + 'EVD="$EVD/run"; OUT=$EVD; row_R0 > /dev/null || exit 7; ' + R3 + cmd, **env)
    assert r.stdout.strip().splitlines()[-1:] == [want], r.stdout + r.stderr


def test_the_rfm98pw_stick_is_never_set_above_14(tmp_path):
    # red-before (54ddaa4a): pset H r3 15 sent --set POWER=15 (the cap was 17)
    box, setup, env = box_stand_in(tmp_path)
    r, _ = source_only(tmp_path, setup + 'EVD="$EVD/run"; OUT=$EVD; row_R0 > /dev/null || exit 7; ' + R3
                       + 'pset H s14.txt r3 14; pset H s15.txt r3 15', **env)
    assert stop_lines(r) == ["STOP: s15.txt: power 15 above the cap 14 dBm"], r.stdout + r.stderr
    log = (box / "box.log").read_text()
    assert "r3 --set POWER=14 --yes" in log and "POWER=15" not in log     # 14 is sent (a control), 15 never


def test_a_pre_state_above_the_sticks_cap_stops_before_any_change(tmp_path):
    # red-before (54ddaa4a): jpower journalled 15 and pushed its undo: row 7's restore would set r3 back to 15
    _box, setup, env = box_stand_in(tmp_path)
    r, evd = source_only(tmp_path, setup + 'EVD="$EVD/run"; OUT=$EVD; row_R0 > /dev/null || exit 7; ' + R3 + 'jpower H r3 15', **env)
    assert stop_lines(r) == ["STOP: H:r3: pre-state power 15 above 14 dBm (min(17, ch341-rfm98pw-433's power_default_dbm, KITCAP)): "
                             "nothing changed; set it to its cap or below by hand first"], r.stdout + r.stderr
    run = evd / "run"
    assert (run / "undo.list").read_text() == "" and "PRE_H_r3" not in (run / "state.env").read_text()


@pytest.mark.parametrize("action, value, want", [
    ("mt-power", "0", "STOP: f.txt: power 0 outside 0..17 dBm (Meshtastic 1..17): never set"),      # Meshtastic's 0 = its maximum
    ("mt-send", "5", "STOP: f.txt: 'mt-send' is not a peer power action"),
])
def test_the_setter_refuses_a_peer_value_or_action_before_the_peer_is_called(tmp_path, action, value, want):
    # pins the setter's own checks (they held at 54ddaa4a; no caller reaches them today): pdom, the peer power actions
    marker = tmp_path / "peer-args"
    fake = tmp_path / "peer.py"
    fake.write_text(f"import sys\nopen({str(marker)!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\n")
    r, _ = source_only(tmp_path, f'cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; VMT=python3; pset PC f.txt {action} {value}',
                       FAKE=str(fake))
    assert stop_lines(r) == [want] and not marker.exists(), r.stdout + r.stderr


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


@pytest.mark.parametrize("ctx", ["set", "read", "restore"])
def test_under_the_power_setter_or_reader_its_own_command_runs_only_without_stdin(tmp_path, ctx):
    # pins the empty stdin of PAUTH's rule: the authorised command with a payload (no power token in it) is still a STOP
    r, evd = source_only(tmp_path, f"f() {{ local PCTX={ctx} PAUTH='touch \"$RAN\"'; XIN PC p.txt 'echo hi' 'touch \"$RAN\"'; }}; f")
    assert stop_lines(r) == ["STOP: p.txt: under the power setter or reader only its own command runs: never run"], r.stdout + r.stderr
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


# ---- RS counts each radio's physical frames of the run once, against TXMAX; a count it cannot prove complete fails ---------

NOW = int(time.time())   # wallclock: taken at import; _rs pins the box stand-in's clock (T0) to it, so the records sit around T0
PRE, RUN = NOW - 3600, NOW + 300      # a record from before R0's clock (T0), and one of the run


def _clock(t):
    """The box's clock (date -u +%s, R0's T0) pinned to t: a serial suite reaches these tests minutes after import."""
    return f'date() {{ [ "$*" = "-u +%s" ] && echo {t} || command date "$@"; }}\n'


def _l(ts, d="TX", out="ok"):
    rs = "rssi=- snr=-" if d == "TX" else "rssi=-50.0 snr=9.0"
    return f'{time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))}.045Z {d} {rs} len=2 outcome={out} hex=aabb ascii=".."\n'


def _mt(ts, d="TX"):
    rx = ',"rssi":-50' if d == "RX" else ""
    return f'{{"timestamp":{ts},"from":0,"to":1,"size":2,"bytes":"AABB"{rx}}}\n'


DMN, MCOM, MT, RNS = "logs/r1/rf-daemon.log", "logs/r1/rf-meshcom.log", "logs/r1/rf-meshtastic.log", "logs/r1/rf-reticulum.log"
RLOG, PREV = "logs/start-loraham-daemon-433-r1.log", "logs/start-loraham-daemon-433-r1.prev.log"
BANNER = "loraham_daemon 1.4.1 r1 433: started\n" * 3
ERR = "[433] transmit ERROR: -5\n"
BASE = {DMN: _l(PRE), MCOM: _l(PRE), RNS: _l(PRE), RLOG: BANNER + ERR}   # one ERROR from before the run


@pytest.mark.parametrize("after, rns, want", [
    ({DMN: _l(PRE) + _l(RUN) * 2, MCOM: _l(PRE) + _l(RUN)}, "", "r1 2|"),                    # run 1C, Zero r1: 2, not 3
    ({DMN: _l(PRE) + _l(RUN) * 6, MCOM: _l(PRE) + _l(RUN) * 6}, "", "r1 6|"),                # 6 frames, not 12 (a false FAIL)
    ({DMN: _l(PRE) + _l(RUN) * 3, MCOM: _l(PRE) + _l(RUN) * 3 + _l(RUN, out="unconfirmed") * 2}, "", "r1 5|"),  # the mirror stands
    ({DMN: _l(PRE) + _l(RUN) * 10}, "", "r1 10|"),
    ({DMN: _l(PRE) + _l(RUN) * 11}, "", "FAIL r1 11|"),
    ({DMN: _l(PRE) + _l(RUN) * 10, RLOG: BANNER + ERR * 2}, "", "FAIL r1 11|"),               # an ERROR may have aired: counted
    ({DMN: _l(PRE) + _l(RUN) * 9, RLOG: BANNER + ERR * 2}, "", "r1 10|"),
    ({DMN: _l(PRE) + _l(RUN) * 10, RLOG: BANNER + ERR}, "", "r1 10|"),                     # the ERROR from before the run: not this run's
    ({MT: _mt(PRE) + _mt(RUN, "RX") + _mt(RUN) * 11}, "", "FAIL r1 11|"),                    # meshtastic's JSON trace (row 3)
    ({MT: _mt(PRE) + _mt(RUN) * 3}, "", "r1 3|"),
    ({DMN: _l(PRE) + _l(RUN) * 2, RNS: _l(PRE) + _l(RUN) * 4 + _l(RUN, "RX")}, "r1=spi", "r1 6|"),     # the radio's own Reticulum log, spi (row 5)
    # the daemon backend (rows 12, 13): Reticulum's frames are the daemon's again, a mirror
    # red-before (82dc98ad): r1 8 (the Reticulum log added on top of the daemon's frames)
    ({DMN: _l(PRE) + _l(RUN) * 4, RNS: _l(PRE) + _l(RUN) * 4}, "r1=daemon", "r1 4|"),
    ({DMN: _l(PRE) + _l(RUN) * 4, RNS: _l(PRE) + _l(RUN) * 4}, "", "r1 8|"),                  # no backend read by the kit: on top (safe side)
    ({RNS: _l(PRE) + _l(RUN)}, "", "r1 1|"),                                                  # a radio's own Reticulum log counts on it
    ({RNS + ".1": _l(RUN) * 2, RNS: _l(RUN)}, "r1=spi", "FAIL r1 UNREADABLE|"),                # Reticulum's lines lost: its radio
    ({DMN + ".1": _l(PRE) + _l(RUN) * 2, DMN: _l(RUN) * 3}, "", "r1 5|"),                    # a roll in the run: both parts
    ({DMN + ".1": _l(PRE) + _l(RUN) * 6, DMN: _l(RUN) * 5}, "", "FAIL r1 11|"),
    ({DMN + ".1": _l(RUN) * 2, DMN: _l(RUN)}, "", "FAIL r1 UNREADABLE|"),                    # two rolls: lines lost
    ({DMN: ""}, "", "FAIL r1 UNREADABLE|"),                                                   # a Clear in the run
    ({DMN: _l(PRE) + "garbled TX line\n"}, "", "r1 1|"),                                     # a line that does not parse: TX
    ({RLOG: "", PREV: BANNER}, "", "FAIL r1 UNREADABLE|"),                                    # a cut
    ({RLOG: "x" * 5000, PREV: BANNER}, "", "FAIL r1 UNREADABLE|"),                            # a cut, then regrown past R0
    ({RLOG: BANNER + ERR, PREV: BANNER + ERR * 2}, "", "FAIL r1 UNREADABLE|"),                          # a cut + restart: the same head
    ({RLOG: None}, "", "FAIL r1 UNREADABLE|"),
    ({DMN: None}, "", "FAIL r1 UNREADABLE|"),                                                 # an RF log with records at R0 gone                                                # the run log gone
])
def test_rs_counts_each_physical_frame_of_the_run_once(tmp_path, after, rns, want):
    # red-before (54ddaa4a): RS summed ' TX ' lines over rN/rf-*.log as RS minus R0: a daemon frame and its stack's line
    # counted twice (run 1C), meshtastic's JSON and logs/rf-reticulum.log counted 0, a roll or a Clear hid frames, and the
    # daemon's transmit ERRORs (which may have aired) were not counted
    _rs(tmp_path, BASE, after, rns, want)


def _rs(tmp_path, before, after, rns, want, box_env="", extra=""):
    """R0 on the box stand-in with `before` in its runtime, then `after` laid over it (None = deleted), then RS."""
    box, setup, env = box_stand_in(tmp_path)
    rt = box / "rt"
    with open(box / "env.sh", "a") as f:
        f.write(_clock(NOW) + box_env)            # a box_env clock, defined later, wins
    for f, c in before.items():
        (rt / f).parent.mkdir(parents=True, exist_ok=True)
        (rt / f).write_text(c)
    ovl = tmp_path / "ovl"
    gone = [str(rt / f) for f, c in after.items() if c is None]
    for f, c in after.items():
        if c is not None:
            (ovl / f).parent.mkdir(parents=True, exist_ok=True)
            (ovl / f).write_text(c)
    rnsk = "keep RNS_H_{} {}; ".format(*rns.split("=")) if rns else ""   # the backend a Reticulum row read on that radio
    r, _ = source_only(tmp_path, setup + f'EVD="$EVD/run"; OUT=$EVD; row_R0 > /dev/null || exit 7; cp -r "{ovl}"/. "{rt}"/; rm -f {" ".join(gone) or "/dev/null/x"}; '
                                         f'{rnsk}{extra}: > "$EVD/undo.list"; : > "$EVD/undo.done"; usbguard() {{ :; }}; row_RS && echo RS-DONE',
                       **env)
    if want.startswith("STOP-FAIL "):           # the judge itself failed: no count line at all
        assert r.returncode == 1 and "RS-DONE" not in r.stdout and want.removeprefix("STOP-FAIL ") in r.stderr, r.stdout + r.stderr
        return
    assert f"data: RS H: TX frames of this run per radio: {want.removeprefix('FAIL ')}" in r.stdout, r.stdout + r.stderr
    if want.startswith("FAIL"):
        assert r.returncode == 1 and "RS-DONE" not in r.stdout and "RS H: a radio sent more than 10" in r.stderr, r.stdout + r.stderr
    else:
        assert "RS-DONE" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("after, grew", [
    ("RX 4\\nTX 8\\n", True),     # its RX, the announce's 2 frames and the exchange's proof or send
    ("RX 4\\nTX 7\\n", False),    # two TX: MeshChat's announce alone (LXMF + audio call), the exchange went over the owner
    ("RX 4\\nTX 6\\n", False),    # one TX
    ("RX 3\\nTX 9\\n", False),    # no RX
    ("RX 4\\nTX x\\n", False),    # a count not a number
])
def test_row_13_member_leg_needs_an_rx_and_a_tx_beyond_the_announce_on_the_members_own_log(tmp_path, after, grew):
    # red-before (da24e83c): no memgrew (the leg's judge was rfgrew ... 2, met by the start's 2 announces alone)
    r, _ = source_only(tmp_path, f'printf "RX 3\\nTX 5\\n" > "$EVD/a.txt"; printf "{after}" > "$EVD/b.txt"; '
                                 'if memgrew a.txt b.txt; then echo GREW; else echo NOT; fi')
    assert r.stdout.strip() == ("GREW" if grew else "NOT"), r.stdout + r.stderr


@pytest.mark.parametrize("legacy, want", [
    pytest.param(_l(RUN), "FAIL r1 UNREADABLE|", id="tx-in-run"),    # a TX of the run in a pre-85fa9df5 node-wide log
    pytest.param(_l(PRE), "r1 0|", id="tx-before-run"),               # its lines from before the run: not this run's
])
def test_a_tx_in_the_node_wide_reticulum_log_makes_the_box_unreadable(tmp_path, legacy, want):
    # red-before (82730d2d): the node-wide log was not read: r1 0 for both
    _rs(tmp_path, BASE, {"logs/rf-reticulum.log": legacy}, "", want)


BIG = "".join(f"daemon line {i:05d}\n" for i in range(400))      # 7200 bytes: a run log over 4 KiB at R0


@pytest.mark.parametrize("before, after", [
    (BASE, {RLOG: "y" * 5000 + ERR}),                      # replaced by a new file (not the cap: no cut file), regrown past R0
    ({**BASE, RLOG: BIG}, {RLOG: BIG[:4096] + "z\n"}),     # truncated outside the cap, regrown: the same first 4 KiB, shorter
])
def test_a_run_log_not_continuous_since_r0_is_unreadable(tmp_path, before, after):
    # pins the head (a new file) and the size (a shorter file with the same head) checks of the daemon run log
    _rs(tmp_path, before, after, "", "FAIL r1 UNREADABLE|")


def test_a_frame_in_the_second_of_t0_counts(tmp_path):
    # the box clock at R0 is T0 (whole seconds): a frame within that second counts (the safe side), one before it does not
    t0 = NOW - 600
    _rs(tmp_path, BASE, {DMN: _l(PRE) + _l(t0 - 1) + _l(t0)}, "", "r1 1|",
        box_env=_clock(t0))


@pytest.mark.parametrize("row, be", [("5", "spi"), ("12", "daemon")])
def test_a_reticulum_row_records_its_radio_and_backend_for_the_count(tmp_path, row, be):
    # a Reticulum row keeps RNS_<box>_<rid>=<backend> before Reticulum can send (rnsrids reads it at RS); the row stops at its
    # power read here. red-before (82dc98ad): row 5 kept RNS_H=r2 (one radio per box, no backend); row_12 was not found
    r, evd = source_only(tmp_path, 'MODE=dry; SCRATCH=$EVD; needs() { return 0; }; must() { :; }; undo_push() { :; }; spower() { exit 0; }; '
                                     f'keep KZ r2; row_{row} H:KZ')
    assert f"RNS_H_r2={be}" in (evd / "state.env").read_text().splitlines(), r.stdout + r.stderr


@pytest.mark.parametrize("before, after, extra, want", [
    ({RLOG: BANNER}, {}, "keep TXR_H_r1 2; ", "FAIL r1 UNREADABLE|"),                         # no RF log at R0 or RS, the kit sent
    ({RLOG: BANNER}, {}, "", "FAIL r1 UNREADABLE|"),                                           # no RF log, an instance ran on r1 at R0
    ({DMN: "", RLOG: BANNER}, {DMN: None}, "keep TXR_H_r1 2; ", "FAIL r1 UNREADABLE|"),       # an empty R0 log gone after the TX
    ({DMN: "", RLOG: BANNER}, {DMN: None}, "", "FAIL r1 UNREADABLE|"),
    (BASE, {}, "keep TXR_H_r1 2; ", "FAIL r1 UNREADABLE|"),                                    # the kit sent on r1, nothing counted
    (BASE, {DMN: _l(PRE) + _l(RUN) * 2}, "keep TXR_H_r1 2; ", "r1 2|"),                        # a control: the kit's frames counted
    (BASE, {}, 'rm "$EVD/R0-H-tx.txt"; ', "STOP-FAIL RS H: the TX count judge failed (rc 2)"),  # the judge fails: never a pass
])
def test_rs_never_reads_missing_tx_evidence_as_zero(tmp_path, before, after, extra, want):
    # red-before (badc0a7b, G2-HWKIT-D B1/B2): each of these passed as r1 0 (no RF log; an empty R0 log removed; the kit's own
    # frames not in any log), and a failing judge left an empty count that the TXMAX check accepted
    _rs(tmp_path, before, after, "", want, extra=extra)


@pytest.mark.parametrize("extra, want", [
    ("keep TXR_H_r1 1; keep TXREF_H_r1 1; ", "r1 0|"),                # its one frame refused by the daemon: 0 is its count
    ("keep TXR_H_r1 2; keep TXREF_H_r1 2; ", "r1 0|"),
    ("keep TXR_H_r1 2; keep TXREF_H_r1 1; ", "FAIL r1 UNREADABLE|"),  # one frame not refused and not counted: unreadable
    ("keep TXR_H_r1 1; keep TXREF_H_r1 1; keep TXR_H_r1 2; ", "FAIL r1 UNREADABLE|"),   # a later frame sent: the last values
])
def test_rs_reads_0_for_a_radio_whose_every_kit_frame_its_daemon_refused(tmp_path, extra, want):
    # red-before (daa840f9, run 20261124T080716Z: r4 refused CHANNEL_BUSY, then RS "r4 UNREADABLE" stopped the run)
    _rs(tmp_path, BASE, {}, "", want, extra=extra)


@pytest.mark.parametrize("lines, ok", [("r1 3\nr2 0\n", True), ("r1 3\nr2 0\nreticulum UNREADABLE\n", False), ("", False),
                                       ("r1 3\n", False), ("r1 3\nr1 3\nr2 0\n", False), ("r1 3\nr2 0\nr9 0\n", False),
                                       ("r1 3\nr2 -1\n", False), ("r1 3\nr2 x\n", False)])
def test_the_tx_count_has_exactly_one_valid_line_per_radio(tmp_path, lines, ok):
    # red-before (badc0a7b, G2-HWKIT-D B2): an empty or short count passed the TXMAX check; "reticulum UNREADABLE" is no
    # longer a judge line (each radio counts its own Reticulum log). red-before (82dc98ad): that line passed (OK)
    r, _ = source_only(tmp_path, f'printf "{lines}" > "$EVD/c"; txlines "$EVD/c" "r1 r2" && echo OK')
    assert ("OK" in r.stdout) == ok, r.stdout + r.stderr


def test_each_radio_of_the_node_counts_its_own_reticulum_log_by_its_backend(tmp_path):
    # Reticulum's RF log per radio (product 85fa9df5): r2 (daemon backend) and r10 (spi) count their own files, no sum; r2's
    # Reticulum frames are its daemon's again. red-before (82dc98ad): ["r10 0", "r2 3"]: a radio's own Reticulum log never counted
    a = (f"F r2 daemon rf-daemon.log 1 {PRE} 0\\nF r2 rns rf-reticulum.log 1 {PRE} 0\\nF r10 rns rf-reticulum.log 1 {PRE} 0\\n")
    b = (f"F r2 daemon rf-daemon.log 4 {PRE} 3\\nF r2 rns rf-reticulum.log 4 {PRE} 3\\nF r10 rns rf-reticulum.log 3 {PRE} 2\\n")
    r, _ = source_only(tmp_path, f'printf "{a}" > "$EVD/a"; printf "{b}" > "$EVD/b"; txjudge "$EVD/a" "$EVD/b" {NOW} "r2=daemon r10=spi"')
    assert r.stdout.splitlines() == ["r10 2", "r2 3"], r.stdout + r.stderr


def test_a_radio_that_could_not_send_and_has_no_log_counts_zero(tmp_path):
    # no RF log, no kit frames, no instance on it at R0: nothing of that radio could send (an idle radio of the list)
    r, _ = source_only(tmp_path, 'printf "L r1 0 e3b0c44298fc1c14 0 absent\\n" > "$EVD/a"; cp "$EVD/a" "$EVD/b"; txjudge "$EVD/a" "$EVD/b" 100 "" "" ""')
    assert r.stdout.splitlines() == ["r1 0"], r.stdout + r.stderr


def test_a_radio_the_kit_started_an_instance_on_could_send(tmp_path):
    # G2-D delta M1: the undo journal is empty by RS, so the start itself is kept: a stack sends at its start (meshtastic's
    # nodeinfo) even when its row ends before the first tx; with no RF log and no reserved frame that radio is UNREADABLE
    r, _ = source_only(tmp_path, 'undo_push() { :; }; must() { :; }; started H 3-start.txt meshtastic@r3; '
                                   'printf "kiss@r1\\n" > "$EVD/R0-H-runset.txt"; echo "[$(runrids H)]"; '
                                   ': > "$EVD/a"; : > "$EVD/b"; txjudge "$EVD/a" "$EVD/b" 100 "" "" "$(runrids H)"')
    assert r.stdout.splitlines() == ["[r1 r3 ]", "r1 UNREADABLE", "r3 UNREADABLE"], r.stdout + r.stderr


def test_a_radio_of_r0_without_its_facts_at_rs_is_unreadable(tmp_path):
    r, _ = source_only(tmp_path, 'printf "L r1 9 aa 0 absent\\nL r2 9 bb 0 absent\\n" > "$EVD/a"; printf "L r1 9 aa 0 absent\\n" > "$EVD/b"; '
                                     'txjudge "$EVD/a" "$EVD/b" 100 ""')
    assert r.stdout.splitlines() == ["r1 0", "r2 UNREADABLE"], r.stdout + r.stderr


def test_reticulum_backends_are_kept_per_radio_of_the_box(tmp_path):
    # every radio a Reticulum row ran the node on, once each, with the backend it read; another box's radios are not this box's
    # red-before (82dc98ad): one empty line, nothing for either box (only RNS_<box>, one radio per box, and R0's running set)
    r, _ = source_only(tmp_path, 'keep RNS_H_r2 daemon; keep RNS_H_r10 spi; keep RNS_Z_r2 spi; keep RNS_H_r2 daemon; rnsrids H; echo; rnsrids Z')
    assert r.stdout.splitlines() == ["r10=spi r2=daemon ", "r2=spi "], r.stdout + r.stderr


# ---- row 6 never runs a command line from the box's output: it builds the chat call from plain paths ----------------------

CHAT_OK = ("cd /home/lhpc/rt/files/r1/ && /home/lhpc/rt/LoRaHAM_Daemon/loraham_chat --data-socket /run/loraham/lora-r1.sock "
           "--conf-socket /run/loraham/loraconf-r1.sock; stty sane 2>/dev/null")


@pytest.mark.parametrize("line", [CHAT_OK.replace("/files/r1/", "/$(touch>chat-marker)/"),
                                  CHAT_OK.replace("/files/r1/", "/`touch chat-marker`/"),
                                  CHAT_OK.replace("/run/loraham/lora-r1", "/run/$(id)/lora-r1"),
                                  CHAT_OK.replace("lora-r1.sock --conf", "lora-r1.sock $(id) --conf"),
                                  CHAT_OK.replace("cd /home", "cd home")])
def test_the_chat_call_is_refused_when_a_path_from_the_box_is_not_plain(tmp_path, line):
    # red-before (cd825a69): the judge passed $(...) and `...` inside the paths, then the line ran in the box's tmux pane
    r, _ = source_only(tmp_path, 'printf "%s\\n" "run it yourself in a terminal:" "$LINE" > "$EVD/s.txt"; '
                                   'chatcmd s.txt r1 > /dev/null && echo ACCEPTED', LINE=line)
    assert "ACCEPTED" not in r.stdout and not list(tmp_path.rglob("chat-marker")), r.stderr


def test_the_chat_call_is_built_from_the_parsed_paths(tmp_path):
    r, _ = source_only(tmp_path, 'printf "%s\\n" x "run it yourself in a terminal:" "  $LINE" > "$EVD/s.txt"; chatcmd s.txt r1',
                       LINE=CHAT_OK)
    assert r.returncode == 0 and r.stdout.strip() == CHAT_OK, r.stdout + r.stderr   # a control: a plain line passes as is


# ---- the scratch root is a plain path; an interrupt is a STOP; the dry gate check sees a TX without its command -----------

def test_a_scratch_root_that_is_not_a_plain_path_stops_the_start(tmp_path):
    # red-before (cd825a69): the dry run went on with TMPDIR spliced into shell text (the self-check ran its $(...))
    bad = tmp_path / "s$(touch scratch-marker)"
    bad.mkdir()
    r = subprocess.run(["bash", str(HW / "rf-proof.sh"), "dry"], env={**_env(tmp_path), "TMPDIR": str(bad)}, stdin=subprocess.DEVNULL,
                       cwd=tmp_path, capture_output=True, text=True, timeout=300, check=False)
    assert r.returncode == 2 and not list(tmp_path.rglob("scratch-marker")), r.stdout[-2000:] + r.stderr
    assert stop_lines(r) == [f"STOP: TMPDIR '{bad}' is not a plain absolute path ([A-Za-z0-9._/-]): the scratch root is spliced "
                             "into commands"], r.stderr


def test_an_interrupt_is_a_stop(tmp_path):
    # red-before (cd825a69): SIGTERM ended the run with rc 130, outside the documented 0/1/2
    p = subprocess.Popen(["bash", str(HW / "rf-proof.sh"), "dry"], env=_env(tmp_path), stdin=subprocess.DEVNULL, cwd=tmp_path,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.startswith("== ROW R1"):
            break
    p.terminate()
    _, err = p.communicate(timeout=120)
    assert p.returncode == 2 and "STOP: interrupted (signal): the recovery runs" in err, err


# ---- the run's supervision: the main shell only waits; a signal ends the worker's whole group before the recovery -------
# bash 5.2 loses a signal that lands while the shell expands a $(...) (CI testlab 45082540356: "trap: line 2: unexpected EOF",
# rc 0): the rows run in a worker in its own process group, and the shell that traps the signals only waits.

SUP = ('OPERATOR_CALL=N0CALL; RUN=; BOXES=; export EVD; SCRATCH=$EVD/scratch; mkdir -p "$SCRATCH"; '
       # stop as the product's, plus which of the run's processes (pids, one per line) are still alive at that moment
       # (a zombie is an ended one: an init that never reaps, as a container's, keeps it, and `kill -0` counts it)
       'stop() { local p s; while read -r p; do { read -r s < "/proc/$p/stat"; } 2>/dev/null || continue; s=${s##*) }; '
       '[ "${s%% *}" = Z ] || echo "ALIVE $p" >&2; done < "$EVD/pids"; '
       'echo "STOP: $*" >&2; exit 2; }; : > "$EVD/pids"; runtraps\n')


def supervised(tmp_path, script, name="evd", **popen):
    """`script` after sourcing rf-proof.sh (as source_only does) and the run's traps (runtraps), as a running process."""
    evd = tmp_path / name
    evd.mkdir()
    prelude = (f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"; '
               'export RAN="$EVD/ran"\n')
    popen.setdefault("stdout", subprocess.PIPE)
    popen.setdefault("stderr", subprocess.PIPE)
    p = subprocess.Popen(["bash", "-c", prelude + SUP + script], env=_env(tmp_path), stdin=subprocess.DEVNULL, cwd=evd,
                         text=True, **popen)
    return p, evd


def until(cond, what, timeout=60.0):
    """An event: `cond()` turns true (a file the process writes), bounded."""
    deadline = time.monotonic() + timeout  # wallclock: the bound of a wait for a file the process writes (an event)
    while not cond():
        assert time.monotonic() < deadline, f"never happened: {what}"  # wallclock: that bound, never a sync by time
        time.sleep(0.02)  # wallclock: the poll interval of that event wait


def lines_of(path, n):
    return lambda: path.exists() and len(path.read_text().split()) >= n


def cleanup(p, evd):
    """After an assertion failed too: the process and every child it listed end, so none outlives the test."""
    if p.poll() is None:
        p.kill()
        p.wait(timeout=30)
    for f in ("pids", "wpid"):
        for x in ((evd / f).read_text().split() if (evd / f).exists() else ()):
            if alive(int(x)):
                os.kill(int(x), 9)


def alive(pid):
    """The process runs: it exists and is no zombie (an init that never reaps, as a container's, keeps one)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


# the worker: one background and one FOREGROUND child (each lives until killed), their pids in $EVD/pids
TWO_CHILDREN = ('w() { tail -f /dev/null & echo $! >> "$EVD/pids"; sh -c \'echo $$ >> "$EVD/pids"; exec tail -f /dev/null\'; }; supervise w\n')


def test_the_rows_run_in_their_own_process_group(tmp_path):
    # red-before (ff8712b5): the rows ran in the main shell itself, the shell whose INT/TERM trap bash 5.2 can lose
    p = subprocess.Popen(["bash", str(HW / "rf-proof.sh"), "dry"], env=_env(tmp_path), stdin=subprocess.DEVNULL, cwd=tmp_path,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.startswith("== ROW R1"):
            break
    leaders = []
    for d in Path("/proc").iterdir():
        try:
            f = (d / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if d.name.isdigit() and int(f[1]) == p.pid and int(f[2]) == int(d.name):   # a child of the main shell leading its group
            leaders.append(int(d.name))
    p.terminate()
    _, err = p.communicate(timeout=120)
    assert leaders, "no child of the main shell leads its own process group: the rows run in the main shell"
    assert p.returncode == 2 and "STOP: interrupted (signal): the recovery runs" in err, err


@pytest.mark.parametrize("how", ["TERM", "INT to the group", "HUP", "QUIT"])
def test_a_signal_ends_the_workers_whole_group_before_the_stop(tmp_path, how):
    """TERM (a kill), a terminal's Ctrl-C (SIGINT to the main shell's process group), a hangup (a dropped ssh connection) and a
    Ctrl-\\ are each a STOP, and the run's foreground and background commands have ended BEFORE it (and its recovery)."""
    p, evd = supervised(tmp_path, TWO_CHILDREN, start_new_session=True)
    try:
        until(lines_of(evd / "pids", 2), "the worker's two children")
        if how == "INT to the group":
            os.killpg(p.pid, 2)
        else:
            p.send_signal({"TERM": 15, "HUP": 1, "QUIT": 3}[how])
        _, err = p.communicate(timeout=120)
    finally:
        cleanup(p, evd)
    assert p.returncode == 2 and "STOP: interrupted (signal): the recovery runs" in err, err
    assert "ALIVE" not in err, err
    # the group's end is seen at once, also where PID 1 never reaps (the CI lane): a zombie member is no running one
    assert "did not end" not in ((evd / "UNRESOLVED.txt").read_text() if (evd / "UNRESOLVED.txt").exists() else ""), err


def test_a_command_that_ignores_term_is_killed_before_the_stop(tmp_path):
    p, evd = supervised(tmp_path, 'w() { bash -c \'trap "" TERM; echo $$ >> "$EVD/pids"; exec tail -f /dev/null\'; }; supervise w\n')
    try:
        until(lines_of(evd / "pids", 1), "the worker's child")
        p.terminate()
        _, err = p.communicate(timeout=120)
    finally:
        cleanup(p, evd)
    assert p.returncode == 2 and "STOP: interrupted (signal): the recovery runs" in err and "ALIVE" not in err, err


JOURNAL = ': > "$EVD/undo.done"; printf "u1\\tPC\\ttouch \\"\\$RAN\\"\\n" > "$EVD/undo.list"; echo 1 > "$EVD/undo.seq"; '


def test_a_fail_in_the_worker_is_the_runs_fail_and_the_recovery_runs(tmp_path):
    # G1 row 214 F1: R0, M, RS and RE call fail()/eq() in the run itself; that is exit 1 + the recovery, never a STOP
    p, evd = supervised(tmp_path, JOURNAL + 'w() { fail "a row-level FAIL"; }; supervise w\n')
    _, err = p.communicate(timeout=120)
    assert p.returncode == 1 and "FAIL: a row-level FAIL" in err and "STOP:" not in err, err
    assert (evd / "ran").exists() and (evd / "undo.done").read_text() == "u1\n", err
    assert (evd / "RESULT.md").exists(), err


def test_a_worker_killed_alone_leaves_no_command_running_into_the_recovery(tmp_path):
    # G1 row 214 F2: the worker's foreground child outlives a kill of the worker alone
    p, evd = supervised(tmp_path, 'w() { echo $BASHPID > "$EVD/wpid"; sh -c \'echo $$ >> "$EVD/pids"; exec tail -f /dev/null\'; }; supervise w\n')
    try:
        until(lines_of(evd / "pids", 1), "the worker's foreground child")
        os.kill(int((evd / "wpid").read_text()), 15)
        _, err = p.communicate(timeout=120)
    finally:
        cleanup(p, evd)
    assert p.returncode == 2 and "STOP: the run's worker ended rc 143" in err and "ALIVE" not in err, err


def test_the_workers_end_is_the_runs(tmp_path):
    p, evd = supervised(tmp_path, 'w() { tail -f /dev/null & echo $! >> "$EVD/pids"; exit 2; }; supervise w\n')
    try:
        _, err = p.communicate(timeout=120)
        left = [int(x) for x in (evd / "pids").read_text().split()]
    finally:
        cleanup(p, evd)
    assert p.returncode == 2 and "STOP:" not in err and not [x for x in left if alive(x)], err   # its group ended (n1)
    p, _ = supervised(tmp_path, 'w() { return 0; }; supervise w; echo AFTER\n', name="ok")
    out, err = p.communicate(timeout=120)
    assert p.returncode == 0 and out.strip().endswith("AFTER"), err
    p, _ = supervised(tmp_path, 'w() { exit 3; }; supervise w\n', name="rc3")
    _, err = p.communicate(timeout=120)
    assert p.returncode == 2 and "STOP: the run's worker ended rc 3" in err, err


@pytest.mark.wallclock("the worker's rows are paced (0.1 s) so the KILL lands between two rowgates; its end is its pid")
def test_a_killed_main_shell_ends_the_worker_at_its_next_row(tmp_path):
    # a KILL of the main shell (no trap, no recovery): today the row in flight ended and nothing started after it
    p, evd = supervised(tmp_path, 'w() { local i; for ((i = 0; i < 600; i++)); do rowgate; echo $BASHPID > "$EVD/wpid"; '
                                  'sleep 0.1; done; }; supervise w\n')
    try:
        until(lines_of(evd / "wpid", 1), "the worker's first row")
        worker = int((evd / "wpid").read_text())
        p.kill()                       # not reaped yet: a zombie main shell is a gone one too
        until(lambda: not alive(worker), "the worker ending at its next rowgate", timeout=20)
    finally:
        cleanup(p, evd)


# runall with stand-in rows: RS marks its start and waits for the test's release file; RE marks that it was entered
RUNALL = ('row_R0() { :; }; row_R1() { :; }; row_M() { :; }; coverage() { :; }; restable() { :; }; '
          'row_RE() { : > "$EVD/re-entered"; }; '
          'row_RS() { echo $BASHPID > "$EVD/wpid"; until [ -e "$EVD/go" ]; do sleep 0.05; done; }; supervise runall\n')  # wallclock: RS polls (0.05 s) for the test's release file (an event wait)


def test_a_main_shell_killed_during_rs_never_enters_re(tmp_path):
    # red-before (ca275287): runall had no rowgate between RS and RE, so a worker whose main shell was KILLed during RS ran RE
    p, evd = supervised(tmp_path, RUNALL)
    try:
        until(lines_of(evd / "wpid", 1), "the run in RS")
        worker = int((evd / "wpid").read_text())
        p.kill()                       # not reaped yet: a zombie main shell is a gone one too
        (evd / "go").touch()
        until(lambda: not alive(worker), "the worker ending after RS", timeout=20)
    finally:
        cleanup(p, evd)
    assert not (evd / "re-entered").exists(), "RE ran after the main shell was killed"
    p, evd = supervised(tmp_path, RUNALL, name="control")   # the same run, not killed: RE is entered
    until(lines_of(evd / "wpid", 1), "the control run in RS")
    (evd / "go").touch()
    _, err = p.communicate(timeout=120)
    assert p.returncode == 0 and (evd / "re-entered").exists(), err


def test_the_recovery_reads_the_boxes_r0_kept(tmp_path):
    r, _ = source_only(tmp_path, 'OPERATOR_CALL=N0CALL; RUN=; keep BOXES H; BOXES="H Z"; : > "$EVD/undo.list"; '
                                 'verify_set() { echo "VERIFY $BOXES"; }; recover')
    assert "VERIFY H" in r.stdout and "VERIFY H Z" not in r.stdout, r.stdout + r.stderr


def test_the_recovery_runs_to_its_end_on_a_console_that_went_away(tmp_path):
    # OPEN-1: `... 2>&1 | tee <log>` and a Ctrl-C that also ends tee: the next write raised SIGPIPE and ended the shell
    # before its recovery (rc 141 through rf-proof.sh dry on ff8712b5); stdout AND stderr here are a pipe nobody reads
    r_fd, w_fd = os.pipe()
    os.close(r_fd)
    try:
        p, evd = supervised(tmp_path, JOURNAL + 'w() { exit 2; }; supervise w\n', stdout=w_fd, stderr=w_fd)
    finally:
        os.close(w_fd)
    p.wait(timeout=120)
    assert p.returncode == 2
    assert (evd / "ran").exists() and (evd / "undo.done").read_text() == "u1\n" and (evd / "RESULT.md").exists()


def test_the_rows_keep_the_default_sigpipe(tmp_path):
    p, _ = supervised(tmp_path, 'w() { yes | head -n 1 >/dev/null; echo "YES-RC ${PIPESTATUS[0]}"; }; supervise w\n')
    out, err = p.communicate(timeout=120)
    assert "YES-RC 141" in out, out + err


@pytest.mark.wallclock("the recovery's first undo polls (0.1 s) for the release file the test writes after the signals")
@pytest.mark.parametrize("end, rc", [("exit 2", 2), ("exit 1", 1)], ids=["after-a-stop", "after-a-fail"])
def test_a_hangup_or_ctrl_backslash_does_not_cut_a_started_recovery(tmp_path, end, rc):
    # G1 row 215 F3: HUP/QUIT stayed caught during the recovery: an ssh drop there skipped the remaining undos and RESULT.md
    journal = (': > "$EVD/undo.done"; echo 2 > "$EVD/undo.seq"; '
               'printf "u1\\tPC\\ttouch \\"\\$RAN.done\\"\\n" > "$EVD/undo.list"; '
               'printf "u2\\tPC\\ttouch \\"\\$RAN.started\\"; i=0; until [ -e \\"\\$RAN.release\\" ] || [ \\$i -ge 600 ]; '
               'do sleep 0.1; i=\\$((i + 1)); done\\n" >> "$EVD/undo.list"; ')
    p, evd = supervised(tmp_path, journal + f'w() {{ {end}; }}; supervise w\n')
    try:
        until((evd / "ran.started").exists, "the recovery's first undo")   # the recovery has started ...
        p.send_signal(1)                                                    # ... then the hangup and the Ctrl-\
        p.send_signal(3)
        (evd / "ran.release").touch()                                       # ... then its undo may finish
        _, err = p.communicate(timeout=120)
    finally:
        (evd / "ran.release").touch()                                       # never leave the undo waiting
        cleanup(p, evd)
    assert p.returncode == rc, err
    assert (evd / "ran.done").exists() and sorted((evd / "undo.done").read_text().split()) == ["u1", "u2"], err
    assert (evd / "RESULT.md").exists(), err

def test_the_dry_gate_check_flags_a_tx_without_its_command_at_the_end(tmp_path):
    # red-before (cd825a69): a TX as the last log line passed "every-send-gated" (no END check)
    r, _ = source_only(tmp_path, f'. "{HW}/zstatus.sh"; MODE=dry; SCRATCH=$EVD; DRYLOG=$EVD/drylog; OPERATOR_CALL=N0CALL; '
                                   'TRACKER_CALL=; printf "TX PC:r1 1 nonce send\\n" > "$DRYLOG"; selfcheck')
    assert "dry self-check OK every-send-gated" not in r.stdout
    assert "TX on line 1 is not followed by its command" in r.stdout + r.stderr, r.stdout + r.stderr


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


@pytest.mark.parametrize("name", ["mt_pget", "rt_pget", "rt_dpget", "mt_cap", "radios", "caps"])
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


# ---- a frame row's FAIL in a direction is that direction's: a later one did not run ------------------------------------------

FRAME_STAND_IN = (  # runrow and frame_row as they are; the box calls, the power steps and the RX check stubbed out
    'rowf() { case $2 in kind) echo frame;; a) echo H:KA;; b) echo "$BOXB:KB";; power) echo as,as;; flags) echo "$FLAGS";; *) echo -;; esac; }; '
    'needs() { return 0; }; undo_mark() { echo 0; }; undo_to() { return 0; }; usbguard() { :; }; bgend() { return 0; }; '
    'started() { :; }; dpower() { :; }; txron() { :; }; usbcheck() { :; }; boxrx() { return 0; }; '
    'RADIO_KA="H r4 868 port=1-1"; RADIO_KB="$BOXB r2 868 -"; keep KA r4; keep KB r2; '
    'for q in R9-H-r4 R9-$BOXB-r2; do echo "STATUS FREQ=869.525 SF=12" > "$EVD/PW-$q.txt"; done; '
    'txframe() { echo x >> "$EVD/sends"; [ "$(wc -l < "$EVD/sends")" != "$FAILAT" ] || fail "TX refused: x"; }; ')


NR = "NOT RUN: after A->B FAIL: TX refused: x"


@pytest.mark.parametrize("flags, fail_at, want", [
    ("-", 1, ["9|A->B|FAIL: TX refused: x", f"9|B->A|{NR}"]),
    ("-", 2, ["9|A->B|PASS", "9|B->A|FAIL: TX refused: x"]),
    ("same-box", 1, ["9|A->B|FAIL: TX refused: x", f"9|B->A|{NR}", f"9|stop-isolation|{NR}"]),   # one box, two radios
])
def test_a_frame_rows_fail_is_its_directions_and_a_later_one_is_not_run(tmp_path, flags, fail_at, want):
    # red-before (daa840f9, run 20261124T080716Z row 9): a refused A->B frame was also B->A's FAIL, which never sent
    box_b = "H" if flags == "same-box" else "Z"
    r, evd = source_only(tmp_path, f'FLAGS={flags}; BOXB={box_b}; FAILAT={fail_at}; ' + FRAME_STAND_IN + 'runrow 9; echo "AFTER rc=$?"')
    assert "AFTER rc=0" in r.stdout, r.stdout + r.stderr
    assert (evd / "results.txt").read_text().splitlines() == want
    assert (evd / "sends").read_text().count("x") == fail_at   # nothing is sent after the FAIL


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
             'keep "P_$3" "$p"; }; tx() { exit 7; }; txron() { :; }; txframe() { exit 7; }; frame_row 7 H:H433 H:HSTK min,min same-box')


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
    assert stop_lines(r) == ["STOP: form not confirmed: dset dtxr rx_rf stop tx_text (FORMS_OK must hold every form of the chosen rows)"]
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


def test_row_5s_worst_case_is_five_frames_on_the_box_and_five_on_the_rnode(tmp_path):
    # mutant K5: row 5 counted one frame per end (the box: MeshChat's announce of 2 frames, its delivery proof, own send and
    # the one retry a late cancel can let slip; the RNode's four (announce, second announce, message, path request) + its
    # proof of the box's message). red-before (da24e83c): Z:r2 3 (one frame per announce); (80068d70): Z:r2 4, PC:rnode 3
    # (the box's send counted as never sent); (8e52a31e): PC:rnode 4, one PC announce
    r = dry(tmp_path, LANE="full", ROWS_ONLY="5", FAKE_PEER=_null_peer(tmp_path))
    assert r.returncode == 0, r.stderr
    r0 = r.stdout.split("== ROW R0", 1)[1].split("== ROW R1", 1)[0]
    assert [line for line in r0.splitlines() if line.split(" | ")[0] == "BUDGET"] == ["BUDGET | PC:rnode | 5 | 10", "BUDGET | Z:r2 | 5 | 10"]


def r0_budget(r):
    r0 = r.stdout.split("== ROW R0", 1)[1].split("== ROW R1", 1)[0]
    return [line for line in r0.splitlines() if line.split(" | ")[0] == "BUDGET"]


@pytest.mark.parametrize("row, want", [
    ("12", ["BUDGET | H:r5 | 7 | 10", "BUDGET | PC:rnode | 5 | 10"]),                       # row 5's + the announce call (2)
    # the owner: MeshChat started once (2) + the member leg's announce call (2) + the owner and leave proofs + the member leg's
    # send, slip and proof of the Zero's message; the RNode (owner, leave): see the end; the member: the announce call (2) + send,
    # slip, proof; the Zero: its MeshChat start's announce (2) + send, slip, proof of H's message; the RNode 4 x 2
    ("13", ["BUDGET | H:r2 | 9 | 10", "BUDGET | H:r5 | 5 | 10", "BUDGET | PC:rnode | 8 | 10", "BUDGET | Z:r2 | 5 | 10"]),
])
def test_a_reticulum_rows_worst_case_is_reserved_per_endpoint(tmp_path, row, want):
    # red-before (82dc98ad): "STOP: ROWS_ONLY: '12' is not a row id" (and '13'), rc 2; (da24e83c): one frame per announce
    r = dry(tmp_path, LANE="full", ROWS_ONLY=row, FAKE_PEER=_null_peer(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r0_budget(r) == want


def test_row_13s_member_is_resolved_granted_and_budgeted_in_r0(tmp_path):
    # the member (flags member=H:HMS) is a third endpoint: R0 resolves it like A and B (r0grants then reads its grant)
    # red-before (82dc98ad): "STOP: LANE=fast|full", rc 2
    r = dry(tmp_path, LANE="rns")
    assert r.returncode == 0, r.stderr
    assert {"data: R0: row 13: H868 = H:r2 (loraham)", "data: R0: row 13: HMS = H:r5 (meshstick-1262)"} <= set(r.stdout.splitlines())
    r = dry(tmp_path, LANE="rns", DRY_RADIOS_H=RADIOS_H.replace("r5 meshstick-1262 868 True", "r5 meshstick-1262 868 False"))
    assert r.returncode == 0, r.stderr
    assert "BUDGET" not in r.stdout and "13-" not in r.stdout               # nothing of the row runs, nothing is reserved
    legs = ["owner both ways", "member joins", "member both ways", "member stops", "member joins again", "owner stops"]
    assert [line for line in r.stdout.splitlines() if line.startswith("NOT RUN: ")] == [
        f"NOT RUN: row 13 reticulum: two radios, one node {leg}: absent: radio HMS" for leg in legs]


def test_a_member_with_a_high_power_grant_stops_r0(tmp_path):
    # red-before (82dc98ad): "STOP: LANE=fast|full", rc 2 (no R0 of row 13 at all)
    r = dry(tmp_path, LANE="rns", DRY_RADIOS_H=RADIOS_H.replace("serial=MSTICK01 port=1-3 hp=False", "serial=MSTICK01 port=1-3 hp=True"))
    assert r.returncode == 2 and stop_lines(r) == ["STOP: R0: row 13, HMS = r5: the high-power grant of r5 is 'True' (want None or "
                                                   "False): nothing starts"], r.stderr


def test_row_13_renders_the_join_form_and_the_rehearsal_checks_it(tmp_path):
    # red-before (82dc98ad): "STOP: ROWS_ONLY: '13' is not a row id", rc 2
    r = dry(tmp_path, LANE="full", ROWS_ONLY="13")
    assert r.returncode == 0, r.stderr
    assert "dry self-check OK all-forms-printed" in r.stdout
    joins = [line for line in r.stdout.splitlines() if "stack start reticulum@r5 --join --yes" in line]
    assert [line.split(": ", 1)[0] for line in joins] == ["  [dry] H > 13-join.txt", "  [dry] H > 13-rejoin.txt"]
    # a row 13 that no longer renders its join is named by the rehearsal (the form is needed, never printed)
    hw = tmp_path / "r" / "testlab" / "hardware"                      # a copy beside this checkout's package: its REPO is a repo
    shutil.copytree(HW, hw, ignore=shutil.ignore_patterns("*.local.env", "__pycache__"))
    (tmp_path / "r" / "lhpc").symlink_to(HW.parents[1] / "lhpc")
    s = (hw / "rf-proof.sh").read_text()
    planted = s.replace('"$(form rt_join rid="$rm")"', '"$(form start inst="reticulum@$rm")"')
    assert planted.count("form rt_join") == 0 and planted != s
    (hw / "rf-proof.sh").write_text(planted)
    r = dry(tmp_path, hw=hw, LANE="rns")
    assert r.returncode == 2 and "STOP: dry self-check all-forms-printed: needed but never printed: rt_join" in r.stderr, r.stderr[-1500:]


def test_a_form_a_row_prints_but_does_not_need_stops_the_dry_run(tmp_path):
    # a form outside its row's RFORMS would run live unconfirmed (FORMS_OK checks only the needed ones)
    # red-before (2ce2c4ca): no such self-check, rc 0
    hw = tmp_path / "r" / "testlab" / "hardware"
    shutil.copytree(HW, hw, ignore=shutil.ignore_patterns("*.local.env", "__pycache__"))
    (tmp_path / "r" / "lhpc").symlink_to(HW.parents[1] / "lhpc")
    s = (hw / "rf-proof.sh").read_text()
    planted = s.replace(' rt_zfreq rt_announce rt_send")', ' rt_zfreq rt_send")')
    assert planted != s
    (hw / "rf-proof.sh").write_text(planted)
    r = dry(tmp_path, hw=hw, LANE="rns")
    assert r.returncode == 2 and "STOP: dry self-check every-form-needed: printed but not needed by its row: 13 rt_announce" in r.stderr, r.stderr[-1500:]


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


def test_coverage_takes_each_rows_own_result_not_a_row_whose_id_ends_alike(tmp_path):
    # red-before (a21's kit run on 8e52a31e): covres matched "2|<direction>|" anywhere in the line, so row 2's COVERAGE
    # showed row 12's result (and row 1's row 11's); here row 12's box -> peer is NO-PATH, row 2's is rehearsed
    r = dry(tmp_path, LANE="full", DRY_NOPATH="12-box-send")
    assert r.returncode == 0, r.stderr[-1500:]
    cov = [ln.split(" | ") for ln in r.stdout.splitlines() if ln.startswith("COVERAGE | ")]
    row = {i: [c for c in cov if c[3] == i] for i in ("2", "12")}
    assert row["2"] and row["12"], cov
    assert all(c[5] == "rehearsed" for c in row["2"]), row["2"]
    assert [c[5:] for c in row["12"] if c[4] == "box -> peer"] == [["NOT RUN", "the node had no path to the PC, nothing sent"]]


def test_coverage_takes_a_directions_own_result_not_one_whose_text_extends_it(tmp_path):
    # row 13's "member joins" is the start of its "member joins again": a FAIL after the join leaves the join rehearsed and
    # the rejoin NOT RUN, and each COVERAGE line shows its own (a planted FAIL right after the join's REPORT)
    hw = tmp_path / "r" / "testlab" / "hardware"
    shutil.copytree(HW, hw, ignore=shutil.ignore_patterns("*.local.env", "__pycache__"))
    (tmp_path / "r" / "lhpc").symlink_to(HW.parents[1] / "lhpc")
    s = (hw / "rf-proof.sh").read_text()
    anchor = '"$(dirtext join)" "the node\'s pid unchanged, LoRa $rm up, MeshChat answers" — PASS\n'
    assert s.count(anchor) == 1
    (hw / "rf-proof.sh").write_text(s.replace(anchor, anchor + "  fail planted-after-the-join\n"))
    r = dry(tmp_path, hw=hw, LANE="rns", ROWS_ONLY="13")
    assert "planted-after-the-join" in r.stdout + r.stderr, r.stderr[-1500:]
    cov = [ln.split(" | ") for ln in r.stdout.splitlines() if ln.startswith("COVERAGE | ") and ln.split(" | ")[3] == "13"]
    join = [c[5] for c in cov if c[4] == "member joins"]
    again = [c[5] for c in cov if c[4] == "member joins again"]
    assert join and set(join) == {"rehearsed"}, cov
    assert again and set(again) == {"NOT RUN"}, cov

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


@pytest.mark.parametrize("name", ["radios", "caps", "mt_pget", "rt_pget", "rt_dpget", "mt_cap"])
def test_the_readers_own_forms_pass_its_check(tmp_path, name):
    r, evd = source_only(tmp_path, f'sshrun() {{ touch "$EVD/ran"; }}; pread H f.txt {name} r1')
    assert r.returncode == 0, r.stderr
    assert (evd / "ran").exists()


# ---- row 1 peer -> box (his word 2026-11-17: the T-Deck sends at its own stored power, read first, never changed) ---------

def _tdeck_power(tmp_path, power):
    """Row 1's start in live mode, a fake peer.py answering the T-Deck's power read with `power`; a box start leaves a marker."""
    (tmp_path / "peer.py").write_text(f"print('POWER {power}')\n")
    return source_only(tmp_path, f'PEER="{tmp_path}/peer.py"; LOCK="{tmp_path}/lock"; VMT=python3; '
                       'FORMS_OK="start stop tx_mcom rx_mcom rx_mcom_app"; '
                       ': > "$EVD/undo.list"; CURROW=1; started() { touch "$EVD/box-start"; exit 0; }; row_1 H:H433')


@pytest.mark.parametrize("power", ["22", "18", "", "x"])
def test_row_1_stops_before_any_change_when_the_tdecks_power_is_outside_the_domain(tmp_path, power):
    # red-before (16c98e22): row 1 read no T-Deck power at all; now the T-Deck sends at its own power, so 22 dBm (its 11-10
    # value) or an unreadable one STOPs before the row starts anything, naming the manual step
    r, evd = _tdeck_power(tmp_path, power)
    assert r.returncode == 2 and not (evd / "box-start").exists(), r.stdout + r.stderr
    assert "set its stored power to 17 or less by hand first" in r.stdout + r.stderr


@pytest.mark.parametrize("power", ["17", "2"])
def test_row_1_goes_on_with_the_tdecks_own_power_inside_the_domain(tmp_path, power):
    r, evd = _tdeck_power(tmp_path, power)
    assert r.returncode == 0 and (evd / "box-start").exists(), r.stdout + r.stderr
    assert f"row 1: the T-Deck at its own {power} dBm, not changed" in r.stdout


def test_row_1_runs_peer_to_box_through_tx_in_dry(tmp_path):
    # red-before (16c98e22): row 1 peer -> box was NOT RUN ("it only receives", DEC-W409bh, lifted 2026-11-17)
    r = dry(tmp_path, LANE="full", ROWS_ONLY="1")
    assert r.returncode == 0 and stop_lines(r) == [], r.stdout + r.stderr
    results = {line.split(" | ")[6]: line.split(" | ")[-1] for line in r.stdout.splitlines() if line.split(" | ")[:2] == ["REPORT", "1"]}
    assert results == {"box -> peer": "DRY-PASS", "peer -> box": "DRY-PASS"}, r.stdout
    # each direction names both its pieces of evidence: inside MeshCom on the receiver and the box's RF log
    seen = {line.split(" | ")[6]: line.split(" | ")[7] for line in r.stdout.splitlines() if line.split(" | ")[:2] == ["REPORT", "1"]}
    assert seen == {"box -> peer": "yes (in MeshCom on the T-Deck + TX on r1)", "peer -> box": "yes (in MeshCom on r1 + RX on r1)"}, seen
    tx = [line for line in r.stdout.splitlines() if line.startswith("TX | PC:tdeck | 1 | ")]
    assert len(tx) == 1 and "peer 1-peer-send.txt" in tx[0], r.stdout
    assert any(line.startswith("BUDGET | PC:tdeck | 1 |") for line in r.stdout.splitlines()), r.stdout
    assert "dry self-check OK rx-mcom-judge" in r.stdout


# ---- row 1: inside MeshCom on the receiver, both ways (plan PLAN-HWKIT-RECEIVE-PROOF-v2, design DESIGN-HWKIT-ROW1-v1) ------

def _row1_dry(tmp_path):
    r = dry(tmp_path, LANE="full", ROWS_ONLY="1")
    assert r.returncode == 0 and stop_lines(r) == [], r.stdout + r.stderr
    return r.stdout.splitlines()


def test_row_1_runs_both_box_checks_and_both_rf_judges_in_dry(tmp_path):
    # red-before (cfe79165): peer -> box read r's RF log only; box -> peer had no RF-log evidence at all
    out = _row1_dry(tmp_path)
    files = [line.split(" > ", 1)[1].split(":", 1)[0] for line in out
             if line.startswith("  [dry] H > 1-box-") and not line.startswith("  [dry] H > 1-box-send-")]
    assert files == ["1-box-tx.txt", "1-box-app.txt", "1-box-rx.txt"], files
    assert "dry self-check OK rx-mcom-judge" in out and "dry self-check OK rx-mcom-app-judge" in out


def test_row_1_budget_counts_the_tdecks_relays_only(tmp_path):
    # O1: R0's worst case for the T-Deck is its one send + its mesh's relays of the box's three attempts (4); the kit still
    # dispatches ONE T-Deck send (the end's count 1); the box endpoint stays 3. red-before (cfe79165): R0 said 1
    out = _row1_dry(tmp_path)
    budget = [line for line in out if line.startswith("BUDGET | PC:tdeck | ")]
    assert [line.split(" | ")[2] for line in budget] == ["4", "1"], budget
    assert [line.split(" | ")[2] for line in out if line.startswith("BUDGET | H:")][:1] == ["3"], out
    assert len([line for line in out if line.startswith("TX | PC:tdeck | 1 | ")]) == 1, out


def test_row_1_the_tdecks_nonce_is_sent_only_by_the_tdeck(tmp_path):
    # C2: the peer -> box nonce is given to the T-Deck's send alone; on the box it appears only in the read judges
    out = _row1_dry(tmp_path)
    n2 = next(line.split(" | ")[3] for line in out if line.startswith("TX | PC:tdeck | 1 | "))
    cmds = [line for line in out if line.startswith("  [dry] ") and n2 in line.replace("\\ ", " ")]   # %q escapes the space
    pc = [c for c in cmds if c.startswith("  [dry] PC > ")]
    assert len(pc) == 1 and "meshcom-send" in pc[0], cmds
    box = [c for c in cmds if not c.startswith("  [dry] PC > ")]
    assert box and all(c.startswith("  [dry] H > 1-box-") and "grep -hE" in c and "python3" not in c and "::" not in c
                       for c in box), box


def test_row_1_journals_setinfo_off_before_it_sends_setinfo_on(tmp_path):
    # O2: the off is in the journal before the on reaches the box node
    out = _row1_dry(tmp_path)
    off = [i for i, line in enumerate(out) if line.startswith("  [dry] undo += H: python3 -I -c ") and "--setinfo\\ off" in line]
    on = [i for i, line in enumerate(out) if line.startswith("  [dry] H > 1-setinfo-on.txt: ") and "--setinfo\\ on" in line]
    assert len(off) == 1 and len(on) == 1 and off[0] < on[0], (off, on)


def _row1_live(tmp_path, fail_file="", stop_at=""):
    """Row 1 through runrow in live mode, every box and peer call a fake: X/XIN record their command; `fail_file` is a
    receive judge that does not see its nonce (a row FAIL); `stop_at` is a tx key whose send STOPs (as in main, the EXIT
    trap runs the recovery)."""
    script = ('trap \'rc=$?; [ "$rc" = 0 ] || recover\' EXIT; '
              'FORMS_OK="start stop tx_mcom rx_mcom rx_mcom_app"; : > "$EVD/undo.list"; keep H433 r1; BOXES=; '
              'X() { printf "%s\\t%s\\n" "$2" "${*:3}" >> "$EVD/xlog"; XRC=0; }; XIN() { X "$1" "$2" "${@:4}"; }; '
              'pread() { printf "POWER 17\\n" > "$OUT/$2"; XRC=0; }; started() { :; }; dpower() { :; }; '
              'usbguard() { :; }; peer() { XRC=0; SPAWN=; }; reap() { :; }; verify_set() { :; }; '
              'result() { [ "$1" != "FAILF" ]; }; '
              'tx() { [ "$1" != "STOPK" ] || stop "test: the send of $1"; shift 4; "$@"; }; RUN=1; runrow 1')
    return source_only(tmp_path, script.replace("FAILF", fail_file).replace("STOPK", stop_at))


def _undone(evd):
    lines = (evd / "xlog").read_text().splitlines()
    on = [i for i, line in enumerate(lines) if line.startswith("1-setinfo-on.txt\t") and "--setinfo\\ on" in line]
    off = [i for i, line in enumerate(lines) if line.startswith("UNDO-") and "--setinfo\\ off" in line]
    return on, off


def test_row_1_a_fail_in_peer_to_box_restores_setinfo_through_the_rows_undo(tmp_path):
    # O2 / C6: a FAIL after the on (MeshCom on the box never printed the T-Deck's text) runs runrow's undo_to: the off
    r, evd = _row1_live(tmp_path, fail_file="1-box-app.txt")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not in MeshCom on r1" in (evd / "FAILED.txt").read_text()
    on, off = _undone(evd)
    assert len(on) == 1 and len(off) == 1 and on[0] < off[0], (evd / "xlog").read_text()


def test_row_1_a_stop_after_setinfo_on_restores_it_through_the_recovery(tmp_path):
    # O2 / C6: a STOP after the on (the T-Deck's send) ends the run; the recovery runs the journalled off
    r, evd = _row1_live(tmp_path, stop_at="R1-tdeck")
    assert r.returncode == 2 and "STOP: test: the send of R1-tdeck" in r.stderr, r.stdout + r.stderr
    on, off = _undone(evd)
    assert len(on) == 1 and len(off) == 1 and on[0] < off[0], (evd / "xlog").read_text()


@pytest.mark.parametrize("fail_file, why", [("1-peer-read.txt", "box -> peer: not in MeshCom on the T-Deck"),
                                            ("1-box-tx.txt", "box -> peer: in MeshCom on the T-Deck, but no TX"),
                                            ("1-box-app.txt", "peer -> box: not in MeshCom on r1"),
                                            ("1-box-rx.txt", "peer -> box: in MeshCom on r1, but no RX")])
def test_row_1_each_direction_fails_when_either_of_its_checks_is_missing(tmp_path, fail_file, why):
    r, evd = _row1_live(tmp_path, fail_file=fail_file)
    assert r.returncode == 0, r.stdout + r.stderr
    assert why in (evd / "FAILED.txt").read_text(), (evd / "FAILED.txt").read_text()
    # the failing direction is never a PASS (runrow writes its FAIL; a later direction does not run)
    results = dict(line.split("|", 2)[1:] for line in (evd / "results.txt").read_text().splitlines())
    assert results[why.split(":")[0]].startswith("FAIL: "), results


def test_row_1_a_failed_tdeck_send_stops_the_run_and_restores_setinfo(tmp_path):
    # a24 M1 (dc8b6bf0): the T-Deck's send rc != 0 is a STOP (an unknown TX state), never a later FAIL of the box's checks
    script = ('trap \'rc=$?; [ "$rc" = 0 ] || recover\' EXIT; '
              'FORMS_OK="start stop tx_mcom rx_mcom rx_mcom_app"; : > "$EVD/undo.list"; keep H433 r1; BOXES=; '
              'X() { printf "%s\\t%s\\n" "$2" "${*:3}" >> "$EVD/xlog"; XRC=0; }; XIN() { X "$1" "$2" "${@:4}"; }; '
              'pread() { printf "POWER 17\\n" > "$OUT/$2"; XRC=0; }; started() { :; }; dpower() { :; }; '
              'usbguard() { :; }; peer() { XRC=0; SPAWN=; [ "$3" != meshcom-send ] || XRC=1; }; reap() { :; }; '
              'verify_set() { :; }; result() { :; }; tx() { shift 4; "$@"; }; RUN=1; runrow 1')
    r, evd = source_only(tmp_path, script)
    assert r.returncode == 2 and "STOP: row 1: the T-Deck send failed (rc 1)" in r.stderr, r.stdout + r.stderr
    assert not (evd / "FAILED.txt").exists(), (evd / "FAILED.txt").read_text()   # a STOP, not a row FAIL
    lines = (evd / "xlog").read_text().splitlines()
    assert not any(line.startswith(("1-box-app.txt", "1-box-rx.txt")) for line in lines), lines   # no check after the STOP
    on, off = _undone(evd)
    assert len(on) == 1 and len(off) == 1 and on[0] < off[0], lines


def _console(tmp_path, arg):
    """`setinfo <arg> r1` as rendered, run against a fake node console on a free local port: banner, then it echoes each
    line it gets; returns the run and every byte the console received."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(20)
    got = []

    def serve():
        try:
            c, _ = srv.accept()
        except OSError:
            return
        with c:
            c.sendall(b"MeshCom console\r\nType --help for commands\r\n")
            c.settimeout(5)
            try:
                while (d := c.recv(4096)):
                    got.append(d)
                    c.sendall(d)
            except OSError:
                pass
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    r, _ = source_only(tmp_path, f'rport() {{ echo {srv.getsockname()[1]}; }}; eval "$(setinfo {arg} r1)"; echo "RC=$?"')
    srv.close()
    t.join(10)
    return r, b"".join(got)


def test_the_box_consoles_setinfo_line_is_only_setinfo_on_or_off(tmp_path):
    # a24 M2 (dc8b6bf0): MCINFO_PY refuses any other line before it connects: the console never gets a sending command
    r, got = _console(tmp_path, "on")
    assert "ECHOED" in r.stdout and "RC=0" in r.stdout and got == b"--setinfo on\n", (r.stdout, r.stderr, got)
    (tmp_path / "x").mkdir()
    r, got = _console(tmp_path / "x", "'on; ::rfkit 0a1b2c3d'")
    assert "AssertionError" in r.stderr and "RC=1" in r.stdout and got == b"", (r.stdout, r.stderr, got)


def test_row_1_names_its_peer_to_box_leg_before_that_legs_first_change(tmp_path):
    # the leg file says "peer -> box" from the setinfo on to the row's last check; before it, every check is box -> peer
    script = ('FORMS_OK="start stop tx_mcom rx_mcom rx_mcom_app"; : > "$EVD/undo.list"; keep H433 r1; BOXES=; '
              'X() { printf "%s\\t%s\\n" "$2" "$(cat "$OUT/rowleg.txt" 2>/dev/null)" >> "$EVD/legs"; XRC=0; }; '
              'XIN() { X "$1" "$2" "${@:4}"; }; pread() { printf "POWER 17\\n" > "$OUT/$2"; XRC=0; }; started() { :; }; '
              'dpower() { :; }; usbguard() { :; }; peer() { XRC=0; SPAWN=; }; reap() { :; }; verify_set() { :; }; '
              'result() { printf "%s\\t%s\\n" "$1" "$(cat "$OUT/rowleg.txt")" >> "$EVD/legs"; }; '
              'tx() { shift 4; "$@"; }; RUN=1; runrow 1')
    r, evd = source_only(tmp_path, script)
    assert r.returncode == 0, r.stdout + r.stderr
    legs = dict(line.split("\t", 1) for line in (evd / "legs").read_text().splitlines())
    assert legs["1-box-tx.txt"] == "box -> peer", legs
    for f in ("1-setinfo-on.txt", "1-box-app.txt", "1-box-rx.txt"):
        assert legs[f] == "peer -> box", (f, legs)


def test_row_1_needs_its_three_meshcom_forms_confirmed(tmp_path):
    # mutant M11 (6f09f3cb): row 1's forms back to "start stop" - its new judges ran unconfirmed. A live row 1 whose
    # FORMS_OK holds only start and stop stops before any box, naming the three
    r = subprocess.run(["bash", str(HW / "rf-proof.sh"), "live"], input="\n\n",
                       env=_env(tmp_path, LAB_ENV=str(HW / "lab.example.env"), LANE="full", ROWS_ONLY="1",
                                FORMS_OK="start stop"),
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 2
    assert stop_lines(r) == ["STOP: form not confirmed: rx_mcom rx_mcom_app tx_mcom (FORMS_OK must hold every form of "
                             "the chosen rows)"]
    assert "== ROW" not in r.stdout
