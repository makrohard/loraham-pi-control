"""testlab/hardware/fw-boxproof.sh, the firewall box proof, through its own functions in source-only mode
(FWPROOF_SOURCE_ONLY=1) with a fake ssh: what it writes as evidence, and how it judges a page and a power line."""
import os
import pathlib
import re
import shutil
import subprocess

import pytest

KIT = pathlib.Path(__file__).resolve().parents[2] / "hardware"
PROOF = KIT / "fw-boxproof.sh"
PW = "pw-h-Qz71"
CALL = "XX9TST"
ECHO = f"""#!/bin/bash
echo "argv $*"; echo "env $SSHPASS"; cat 2>/dev/null; echo "{PW} {CALL}-7 {CALL.lower()}-12"; echo "to stderr {PW}" >&2
"""


def proof(tmp_path, body, path_bin=None, env=None):
    """Run `body` in bash after sourcing fw-boxproof.sh (source-only), as a live run with evidence in tmp_path/evd."""
    evd = tmp_path / "evd"
    evd.mkdir(exist_ok=True)
    script = (f'FWPROOF_SOURCE_ONLY=1; . "{PROOF}"\n'
              f'MODE=live; EVD="{evd}"; PW="{PW}"; PW_H="{PW}"; OPERATOR_CALL="{CALL}"\n{body}\n')
    env = {**os.environ, "TMPDIR": str(tmp_path), **(env or {})}
    if path_bin:
        env["PATH"] = f"{path_bin}:{os.environ['PATH']}"
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=tmp_path,
                       env=env, check=False, timeout=60)
    return p, evd


def fake_ssh(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("ssh", "sshpass"):
        (bin_ / name).write_text(ECHO)
        (bin_ / name).chmod(0o755)
    return bin_


def test_no_password_or_call_reaches_the_evidence(tmp_path):
    p, evd = proof(tmp_path, f'X x.txt "echo hello"\nXIN xin.txt "{PW}" "cat"', fake_ssh(tmp_path))
    files = sorted(f for f in evd.rglob("*") if f.is_file())
    assert {f.name for f in files} >= {"x.txt", "x.txt.rc", "xin.txt", "xin.txt.rc"}, p.stderr
    text = "\n".join(f.read_text(errors="replace") for f in files)
    assert "argv" in text and "to stderr" in text                  # the captures ran, stderr included
    assert [s for s in (PW,) if s in text] + re.findall(CALL, text, re.IGNORECASE) == []


def test_an_inherited_out_does_not_steer_the_captures(tmp_path):
    # rf-proof.sh's capture writes to ${OUT:-$EVD}: an OUT from the environment would put the evidence outside the run's
    # directory (and outside the end scan); the proof unsets it.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    p, evd = proof(tmp_path, 'X x.txt "echo hello"', fake_ssh(tmp_path), env={"OUT": str(elsewhere)})
    assert (evd / "x.txt").exists() and list(elsewhere.iterdir()) == [], p.stderr


def test_a_stdin_payload_of_a_dash_is_delivered(tmp_path):
    p, evd = proof(tmp_path, 'XIN dash.txt "-" "cat"', fake_ssh(tmp_path))
    assert "-" in (evd / "dash.txt").read_text().splitlines(), p.stderr


@pytest.mark.parametrize("code,ok", [("200", True), ("302", True), ("000", False), ("403", False), ("404", False),
                                     ("500", False), ("", False), ("2000", False)])
def test_a_page_counts_as_reachable_only_when_it_answers(tmp_path, code, ok):
    # A claim of reachability (the console, an exposed page) needs a 2xx or 3xx answer; 000, 4xx and 5xx are FAIL.
    p, _ = proof(tmp_path, f'page_ok "{code}" && echo REACHABLE || echo NOT')
    assert p.stdout.split() == (["REACHABLE"] if ok else ["NOT"]), p.stderr


CANNED = """X() {{ local f=$1; shift; XRC=0; case $f in
  *-maxpower.txt) printf '  max_power = {mp}\\n';; *-ftx0.txt) printf 'SIZE {size0}\\n3\\n';; *-start.txt) printf 'start\\n'; XRC={start};;
  *-txpower.txt) printf 'SIZE {size1}\\nCOUNT {count}\\n... Final Tx power: {power} dBm\\n'; XRC={read};;
  *-radios.txt) printf '{radios}';; *-caps.txt) printf '{caps}';; *-runset.txt) printf '{runset}';;
  F6-r4.txt) printf 'refused: re-apply with firewall-apply.sh\\n'; XRC={r4rc};;
  *) printf 'ok\\n';; esac > "$EVD/$f"; [ "$XRC" != 255 ] || stop "$f: ssh transport rc 255"; }}
sshx() {{ local n r; n=$(( $(cat "$EVD/../stops" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$EVD/../stops"   # a file: capture runs it in a pipe
  r={stop1}; [ "$n" = 1 ] || r={stop2}; echo "stop rc $r"; return "$r"; }}
{body}; echo RC=$?"""


# radios.py's rows and the model caps of box H (r2 a loraham board, r4 the PineDio: 14 dBm), as RADIOS_PY and CAPS_PY print them
RADIOS = "ROW r2 loraham 868 True serial= port=1-1 hp=None\\nROW r4 pinedio-usb-1262 868 True serial= port=1-2 hp=None\\n"
CAPS = "CAP loraham 17\\nCAP pinedio-usb-1262 14\\nCAP meshstick-1262 22\\n"


def canned(tmp_path, count="4", power="10", start="0", read="0", stop1="0", stop2="0", body="mtstart F6-r2", mp="10",
           size0="500", size1="600", radios=RADIOS, caps=CAPS, runset="meshtastic@r2\\n", r4rc="1"):
    return proof(tmp_path, CANNED.format(count=count, power=power, start=start, read=read, stop1=stop1, stop2=stop2,
                                         body=body, mp=mp, size0=size0, size1=size1, radios=radios, caps=caps,
                                         runset=runset, r4rc=r4rc))


@pytest.mark.parametrize("mp,why", [("17", None), ("18", "max_power 18 above 17: no start"), ("20", "above 17"),
                                    ("", "no max_power value")])
def test_the_stack_starts_only_with_a_max_power_of_at_most_17(tmp_path, mp, why):
    # The power gate before the start: the stack's max_power is read first; above 17, or no value, is a STOP, and no
    # start command is sent.
    p, evd = canned(tmp_path, mp=mp)
    if why is None:
        assert "RC=0" in p.stdout and (evd / "F6-r2-start.txt").exists(), p.stdout + p.stderr
    else:
        assert p.returncode == 2 and why in p.stdout and "RC=" not in p.stdout, p.stdout + p.stderr
        assert not (evd / "F6-r2-start.txt").exists() and not list(evd.glob("SAFESTOP-*.txt"))


@pytest.mark.parametrize("count,started", [("4", True), ("3", False), ("", False)])
def test_a_power_line_counts_only_when_this_start_wrote_it(tmp_path, count, started):
    # Before the start the log holds 3 Final Tx power lines; the start must add one, else the last line is an older
    # start's (stale) and the run stops, the stack stopped first.
    p, _ = canned(tmp_path, count=count)
    if started:
        assert "RC=0" in p.stdout and "Final Tx power 10 dBm" in p.stdout, p.stderr
    else:
        assert p.returncode == 2 and "no Final Tx power line of this start" in p.stdout
        assert "safety stop: meshtastic@r2 stopped" in p.stdout and "RC=" not in p.stdout


@pytest.mark.parametrize("size1,count,started", [("100", "1", True), ("9000000", "1", False), ("500", "1", False),
                                                 ("100", "0", False), ("", "1", False)])
def test_a_start_log_the_start_capped_counts_from_0(tmp_path, size1, count, started):
    # The start caps its log (over 8 MiB the product truncates it): a log smaller than before the start (500 bytes, 3
    # lines) holds only this start's lines, so 1 line is this start's; a log of the same size or larger counts from 3,
    # and a size that cannot be read counts from 3 (a STOP, never a false credit).
    p, _ = canned(tmp_path, size1=size1, count=count)
    if started:
        assert "RC=0" in p.stdout and "Final Tx power 10 dBm" in p.stdout, p.stdout + p.stderr
    else:
        assert p.returncode == 2 and "no Final Tx power line of this start" in p.stdout, p.stdout + p.stderr


@pytest.mark.parametrize("size0", ["", "x"])
def test_no_start_without_the_start_logs_size_before_it(tmp_path, size0):
    p, evd = canned(tmp_path, size0=size0)
    assert p.returncode == 2 and "no count or size of the start log before the start" in p.stdout, p.stdout + p.stderr
    assert not (evd / "F6-r2-start.txt").exists()


CAUSES = {"stale": {"count": "3"}, "too high": {"power": "20"}, "no power value": {"power": "x"},
          "the start failed": {"start": "1"}, "the read failed": {"read": "1"}, "the read had no ssh": {"read": "255"}}


@pytest.mark.parametrize("cause", sorted(CAUSES))
@pytest.mark.parametrize("stop1,stop2,rc", [("0", "0", 2), ("1", "0", 2), ("1", "1", 3), ("255", "0", 2),
                                            ("255", "255", 3)])   # 255: ssh had no connection, also a failed stop
def test_every_stop_after_a_start_stops_the_stack_first(tmp_path, cause, stop1, stop2, rc):
    # Once meshtastic@r2 was started, any STOP (a power check, a failed start or read, ssh 255) exits through the trap,
    # which stops it: checked, one retry; a stop that fails twice makes the exit 3 and names the unsafe state.
    p, evd = canned(tmp_path, stop1=stop1, stop2=stop2, **CAUSES[cause])
    assert p.returncode == rc and "RC=" not in p.stdout, p.stdout + p.stderr
    stops = sorted(f.name for f in evd.glob("SAFESTOP-*.txt"))
    assert stops == ["SAFESTOP-meshtastic@r2-1.txt"] + (["SAFESTOP-meshtastic@r2-2.txt"] if stop1 != "0" else [])
    if rc == 3:
        assert "may still be running, TX power unverified" in p.stdout and "UNSAFE" in (evd / "STOP.txt").read_text()
    else:
        assert "safety stop: meshtastic@r2 stopped" in p.stdout


def test_a_signal_after_a_start_stops_the_stack(tmp_path):
    p, _ = canned(tmp_path, body="started meshtastic@r2; kill -TERM $$; echo AFTER")   # bash runs the trap before the next command
    assert p.returncode == 130 and "AFTER" not in p.stdout and "safety stop: meshtastic@r2 stopped" in p.stdout


def test_a_stack_stopped_by_its_row_is_not_stopped_again(tmp_path):
    p, evd = canned(tmp_path, body="started meshtastic@r2; stopped meshtastic@r2; stop 'a later STOP'")
    assert p.returncode == 2 and not list(evd.glob("SAFESTOP-*.txt"))


def test_the_end_scan_passes_redacted_evidence(tmp_path):
    p, _ = proof(tmp_path, 'X x.txt "echo hello"; endscan; echo CLEAN', fake_ssh(tmp_path))
    assert p.returncode == 0 and "CLEAN" in p.stdout, p.stdout + p.stderr


def test_the_end_scan_stops_on_a_password_in_the_evidence(tmp_path):
    # RE: a file that holds the password (a capture that missed the redactor) stops the run before the SHA256SUMS.
    p, _ = proof(tmp_path, f'X x.txt "echo hello"; printf "%s\\n" "{PW}" > "$EVD/planted.txt"; endscan; echo CLEAN',
                 fake_ssh(tmp_path))
    assert p.returncode == 2 and "the box password is in 1 evidence file(s)" in p.stdout and "CLEAN" not in p.stdout


LAB = {"BOX_H_HOST": "192.0.2.10", "BOX_H_USER": "lhpc", "BOX_H_RUNTIME": "~/loraham-pi-control", "EVD_ROOT": "{tmp}/evd"}
INPUTS = {"OPERATOR_CALL": CALL, "FWSHA": "a" * 40, "FWFROM": "b" * 40, "FWCI": "123", "FWREF": "cons/x",
          "LANCIDR": "192.0.2.0/24", "LABNAME": "LabH2", "LABSHORT": "LH2"}
PROBE = "; lhpc daemon r1 --set POWER=20 --yes #"


def live_copy(tmp_path, lab=None, env=None, stdin=f"{PW}\n", extra=""):
    """A live run of a kit copy (a git repo, so the proof finds its REPO) with `lab` as its lab.local.env ('...' values)
    and the lines `extra` after them, `env` added, ssh and sshpass faked: (the process, whether any ssh ran)."""
    root = tmp_path / "r"
    kit = root / "testlab" / "hardware"
    shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns("*.local.env", "__pycache__"))
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=60)
    lab = {k: v.replace("{tmp}", str(tmp_path)) for k, v in {**LAB, **(lab or {})}.items()}
    (kit / "lab.local.env").write_text("".join(f"{k}='{v}'\n" for k, v in lab.items()) + extra)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("ssh", "sshpass"):
        (bin_ / name).write_text(f"#!/bin/bash\ntouch {tmp_path}/called\n")
        (bin_ / name).chmod(0o755)
    e = {k: v for k, v in os.environ.items() if k not in LAB and k not in INPUTS}
    e.update(PATH=f"{bin_}:{os.environ['PATH']}", TMPDIR=str(tmp_path), **(env or {}))
    p = subprocess.run(["bash", str(kit / "fw-boxproof.sh"), "live"], input=stdin, capture_output=True, text=True,
                       cwd=tmp_path, env=e, check=False, timeout=60)
    return p, (tmp_path / "called").exists()


@pytest.mark.parametrize("key,where", [(k, "lab") for k in sorted(LAB)] + [("BOX_H_RUNTIME", "env")]
                         + [(k, "env") for k in sorted(INPUTS)])
def test_a_value_that_would_run_a_command_stops_before_any_box_call(tmp_path, key, where):
    # Every value the proof puts into a box command comes through a grammar first: a valid value with a command after
    # it is a STOP that names the key, before the password is read or ssh runs.
    value = {**LAB, **INPUTS}[key].replace("{tmp}", str(tmp_path)) + PROBE
    p, ssh = live_copy(tmp_path, lab={key: value} if where == "lab" else None,
                       env={key: value} if where == "env" else None)
    assert p.returncode == 2 and f"STOP: {key}: " in p.stdout + p.stderr and not ssh, p.stdout + p.stderr


RT = '"$HOME"/loraham-pi-control'           # the default runtime path as one shell word (shq)
RT_FORMS = (RT + "/venv/lhpc/bin/lhpc", "cd " + RT + " && ./venv/lhpc/bin/python")


@pytest.mark.parametrize("ref", ["--all", "--upload-pack=touch {tmp}/ran"])   # --all: only its leading - is wrong
def test_a_ref_that_is_an_option_stops_before_any_box_call(tmp_path, ref):
    # FWREF reaches git fetch: a value starting with - would be an option there (--upload-pack runs a command).
    p, ssh = live_copy(tmp_path, env={"FWREF": ref.replace("{tmp}", str(tmp_path))})
    assert p.returncode == 2 and "STOP: FWREF: " in p.stderr and not ssh and not (tmp_path / "ran").exists(), p.stderr


def test_the_runtime_path_enters_each_box_command_as_one_word(tmp_path):
    p, _ = proof(tmp_path, 'printf "%s\\n" "$LH" "$PY" "$FTX"')
    lh, py, ftx = p.stdout.splitlines()
    assert (lh, py) == RT_FORMS and f" {RT}/logs/" in ftx, p.stderr


def test_the_lab_file_is_never_run(tmp_path):
    p, ssh = live_copy(tmp_path, extra=f"touch {tmp_path}/ran\n")
    assert p.returncode == 2 and "not a KEY=VALUE line" in p.stderr and not (tmp_path / "ran").exists() and not ssh, p.stderr


def test_a_password_inside_the_login_name_is_refused_before_any_box_call(tmp_path):
    # The redactor masks the password, so a password that is part of the login name would mask the login in every
    # capture: the live run refuses it at once, with no ssh call.
    p, ssh = live_copy(tmp_path, lab={"BOX_H_USER": f"u{PW}x"})
    assert p.returncode == 2 and "STOP: the box password is part of the login name" in p.stdout, p.stdout + p.stderr
    assert not ssh


NOTE = "NOTE: box H password equals its login name (lab box); not masked"


def test_a_password_equal_to_the_login_name_runs_on_with_the_note_in_every_header(tmp_path):
    # red-before (849fb082): 'lhpc' with BOX_H_USER=lhpc stopped as "part of the login name", before any box call
    p, _ = live_copy(tmp_path, stdin="lhpc\n")      # it runs on to the PC prep (FWSHA unset there: a STOP, no box call)
    assert "part of the login name" not in p.stdout + p.stderr and "STOP: FWSHA is not a 40-hex sha" in p.stdout, p.stdout
    (evd,) = (tmp_path / "evd").glob("fw-boxproof-*")
    assert (evd / "REPORT.md").read_text().splitlines()[:2] == [NOTE, ""]
    assert (evd / "run.log").read_text().splitlines()[0].endswith(" " + NOTE)


@pytest.mark.parametrize("user", ["lhpcx", "xlhpc"])
def test_a_password_only_part_of_the_login_name_is_still_refused(tmp_path, user):
    p, ssh = live_copy(tmp_path, lab={"BOX_H_USER": user}, stdin="lhpc\n")   # lhpc is part of the login, not equal to it
    assert p.returncode == 2 and "STOP: the box password is part of the login name" in p.stdout and not ssh, p.stdout + p.stderr


def test_another_password_has_no_note(tmp_path):
    p, _ = live_copy(tmp_path)
    (evd,) = (tmp_path / "evd").glob("fw-boxproof-*")
    assert "STOP: FWSHA is not a 40-hex sha" in p.stdout and NOTE not in (evd / "run.log").read_text(), p.stdout
    assert not (evd / "REPORT.md").exists()          # no header written before the first row


def test_an_equal_password_is_not_masked_and_the_end_scan_notes_it(tmp_path):
    p, evd = proof(tmp_path, 'PW=lhpc; PW_H=lhpc; BOX_H_USER=lhpc; X x.txt "echo hello"; endscan; echo CLEAN',
                   fake_ssh(tmp_path))
    assert p.returncode == 0 and "CLEAN" in p.stdout, p.stdout + p.stderr
    assert "ok: RE: box H password equals its login name (lab box): not scanned" in p.stdout
    assert "lhpc" in (evd / "x.txt").read_text()         # the fake prints the password: it stays as the login name


# ---- G2-HWKITC B3: the power checks come BEFORE any start; r4's refusal counts only when r4 does not run ------------------

@pytest.mark.parametrize("radios,caps,mp,why", [
    (RADIOS.replace("hp=None\\nROW r4", "hp=True\\nROW r4"), CAPS, "10", "the high-power grant of r2 is 'True'"),
    (RADIOS.replace("ROW r2 loraham", "ROW r2 pinedio-usb-1262"), CAPS, "17", "max_power 17 above 14 dBm"),
    (RADIOS, CAPS.replace("CAP loraham 17\\n", ""), "10", "no single power cap of r2's model ('loraham')"),
    (RADIOS + "ROW r2 loraham 868 True serial= port=1-3 hp=None\\n", CAPS, "10", "2 hardware row(s) of r2"),
    (RADIOS.replace("ROW r2 ", "ROW r9 "), CAPS, "10", "0 hardware row(s) of r2")])
def test_no_start_before_the_radios_grant_and_model_cap_are_read(tmp_path, radios, caps, mp, why):
    # red-before (44b611ca): only max_power <= 17 was read before the start; a radio whose model tops out at 14 dBm, or one
    # with a high-power grant, still started
    p, evd = canned(tmp_path, radios=radios, caps=caps, mp=mp)
    assert p.returncode == 2 and why in p.stdout and "no start" in p.stdout and "RC=" not in p.stdout, p.stdout + p.stderr
    assert not (evd / "F6-r2-start.txt").exists() and not list(evd.glob("SAFESTOP-*.txt"))


def test_a_max_power_within_its_models_cap_starts(tmp_path):
    p, evd = canned(tmp_path, radios=RADIOS.replace("ROW r2 loraham", "ROW r2 pinedio-usb-1262"), mp="14")
    assert "RC=0" in p.stdout and (evd / "F6-r2-start.txt").exists(), p.stdout + p.stderr


@pytest.mark.parametrize("runset,r4rc,passed", [("meshtastic@r2\\n", "1", True), ("meshtastic@r2\\nmeshtastic@r4\\n", "1", False),
                                                ("meshtastic@r2\\n", "0", False)])
def test_r4s_refusal_counts_only_when_r4_does_not_run(tmp_path, runset, r4rc, passed):
    # red-before (44b611ca): a start that returned non-zero naming firewall-apply.sh was a PASS and r4 left the safety stop's
    # list, although a read of the running set showed meshtastic@r4 running
    p, evd = canned(tmp_path, body="r4refused; echo STARTED=${STARTED[*]}", runset=runset, r4rc=r4rc)
    assert "RC=0" in p.stdout and (evd / "F6-r4-runset.txt").exists(), p.stdout + p.stderr
    if passed:
        assert "1b | meshtastic@r4 (changed radio) refused, naming the apply, not running | PASS" in p.stdout
        assert "STARTED=\n" in p.stdout and not (evd / "F6-r4-stop.txt").exists()
    else:
        assert "FAIL: FW6: meshtastic@r4" in p.stdout and (evd / "F6-r4-stop.txt").exists()


def test_r4_is_never_started_above_its_models_cap(tmp_path):
    # red-before (44b611ca): meshtastic@r4 went to gstart with no power read at all; the PineDio tops out at 14 dBm
    p, evd = canned(tmp_path, body="r4refused", mp="15")
    assert p.returncode == 2 and "F6-r4: max_power 15 above 14 dBm" in p.stdout, p.stdout + p.stderr
    assert not (evd / "F6-r4.txt").exists() and not list(evd.glob("SAFESTOP-*.txt"))


# ---- G2-HWKITC B4: a STOP or a signal replays the journalled undos, last first; one that fails is UNRESOLVED ------------

REPLAY = """sshx() {{ shift; echo "$*" >> "$EVD/../replayed"; case "$*" in {fails}) return 1;; esac
  case "$*" in *U2*) {during};; esac; return 0; }}
undo "echo U1"; undo "echo U2"; {body}; echo RC=$?"""


def replay(tmp_path, body="stop 'a later STOP'", fails="NONE", during=":"):
    p, evd = proof(tmp_path, REPLAY.format(body=body, fails=fails, during=during))
    f = tmp_path / "replayed"
    return p, evd, f.read_text().splitlines() if f.exists() else []


def test_a_stop_replays_every_undo_last_first(tmp_path):
    # red-before (44b611ca): the EXIT trap stopped started stacks only; r4 disabled, the node identity and the stopped
    # stacks stayed changed after a STOP
    p, evd, ran = replay(tmp_path)
    assert p.returncode == 2 and ran == ["echo U2", "echo U1"], p.stdout + p.stderr
    assert (evd / "UNDO-1.txt").exists() and (evd / "UNDO-0.txt").exists() and "UNRESOLVED" not in p.stdout
    assert [line.split(" ", 1)[1] for line in (evd / "UNDO.txt").read_text().splitlines()] == ["echo U1", "echo U2"]


def test_an_undo_that_fails_is_named_and_the_rest_still_replays(tmp_path):
    p, evd, ran = replay(tmp_path, fails="*U2*")
    assert p.returncode == 4 and ran == ["echo U2", "echo U1"], p.stdout + p.stderr
    assert "UNRESOLVED: undo 1 (rc 1): echo U2" in (evd / "STOP.txt").read_text()


def test_a_failed_safety_stop_keeps_exit_3_over_an_unresolved_undo(tmp_path):
    p, _, ran = replay(tmp_path, body="started meshtastic@r2; stop 'x'", fails="*")
    assert p.returncode == 3 and "UNSAFE: meshtastic@r2" in p.stdout and "UNRESOLVED" in p.stdout, p.stdout + p.stderr
    assert ran[-2:] == ["echo U2", "echo U1"]          # after the two safety stop attempts, both undos were still tried


def test_a_signal_replays_the_undos_too(tmp_path):
    p, _, ran = replay(tmp_path, body="kill -TERM $$; echo AFTER")
    assert p.returncode == 130 and "AFTER" not in p.stdout and ran == ["echo U2", "echo U1"], p.stdout + p.stderr


def test_a_signal_during_the_replay_does_not_cut_it_short(tmp_path):
    p, _, ran = replay(tmp_path, during="kill -INT $$")
    assert p.returncode == 2 and ran == ["echo U2", "echo U1"], p.stdout + p.stderr


def test_after_fe_ran_the_undos_an_exit_replays_none(tmp_path):
    p, evd, ran = replay(tmp_path, body="undone; stop 'a STOP after FE'")
    assert p.returncode == 2 and ran == [] and not list(evd.glob("UNDO-*.txt")), p.stdout + p.stderr


# ---- G2-HWKITC B5: the lab identity is never the operator's call ---------------------------------------------------------

@pytest.mark.parametrize("call,key,value", [(CALL, "LABNAME", CALL), (CALL, "LABNAME", CALL.lower() + "-7"),
                                            ("K1AB", "LABSHORT", "k1ab"), (CALL + "-9", "LABNAME", CALL)])
def test_a_lab_identity_equal_to_the_operators_call_stops_before_any_box_call(tmp_path, call, key, value):
    # red-before (44b611ca): LABNAME=<the call> passed its grammar and reached `lhpc config ... node_name <call>` over ssh
    p, ssh = live_copy(tmp_path, env={"OPERATOR_CALL": call, key: value})
    assert p.returncode == 2 and f"STOP: {key} is the operator's call" in p.stdout and not ssh, p.stdout + p.stderr
    assert call.split("-")[0].lower() not in (p.stdout + p.stderr).lower()


def test_a_lab_identity_that_only_contains_the_call_runs_on(tmp_path):
    p, _ = live_copy(tmp_path, env={"OPERATOR_CALL": "K1AB", "LABNAME": "K1ABlab", "LABSHORT": "K1A"})
    assert "operator's call" not in p.stdout and "STOP: FWSHA is not a 40-hex sha" in p.stdout, p.stdout + p.stderr
