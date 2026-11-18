"""The kit carries what the 0.14.0 RF sheet proved (testlab/hardware/rf-proof.sh, hardware-proof.md): row 5 proves the shipped
Reticulum defaults (rnode_framing is READ and must be yes; MeshChat's own start announce is heard and counted, never asked
for), rows 8 and 9 drive both ends at the minimum with an undo from the recorded value only, and the pre-step M (mcset.py)
copies each stack row's instance settings, keeps them, and names every job that is not a PASS."""
import os
import re
import subprocess

import pytest
from test_rf_proof import run_dry, table_rows
from test_rf_proof_guards import HW, source_only, stop_lines


def dry(tmp_path, lane="full", **env):
    d = tmp_path / f"dry-{len(list(tmp_path.glob('dry-*')))}"
    d.mkdir()
    return run_dry(d, lane, HW / "rf-proof.sh", env)


def block(out, row):
    """The dry output of one row: from its '== ROW <row>' line to the next '== ROW'."""
    m = re.search(rf"^== ROW {row}\n(.*?)(?=^== ROW )", out, re.MULTILINE | re.DOTALL)
    assert m, f"no ROW {row} in the dry output"
    return m.group(1)


# ---- row 5: the shipped defaults -----------------------------------------------------------------------------------------

ROW5 = ('MODE=live; SCRATCH=$EVD; needs() { return 0; }; undo_push() { :; }; undo_mark() { echo 0; }; nonce() { echo rfkit 0a1b2c3d; }; '
        'must() { case $2 in 5-framing0.txt) printf "%s\\n" "$FR" > "$OUT/$2";; *) echo "MUST $2" >> "$EVD/ran.txt";; esac; XRC=0; }; '
        'spower() { echo REACHED; exit 0; }; keep KZ r2; row_5 Z:KZ')


@pytest.mark.parametrize("fr", ["no", "", "yes no", "maybe"])
def test_row_5_stops_unless_rnode_framing_reads_yes_and_sets_nothing(tmp_path, fr):
    # red-before (fe33ead8): a 'no' was set to yes by the kit (undo + rt_set), so the product default was never proven
    r, evd = source_only(tmp_path, ROW5, FR=fr)
    assert r.returncode == 2, r.stdout + r.stderr
    assert stop_lines(r) == [f"STOP: row 5: rnode_framing reads '{fr}', not yes (the product default): "
                             "the kit sets nothing, no TX"]
    assert not (evd / "ran.txt").exists()                  # nothing after the read: no set, no start


def test_row_5_goes_on_when_rnode_framing_reads_yes(tmp_path):
    r, evd = source_only(tmp_path, ROW5, FR="yes")
    assert r.returncode == 0 and "REACHED" in r.stdout, r.stdout + r.stderr
    assert not (evd / "ran.txt").exists()


def test_row_5_listens_before_the_meshchat_start_and_counts_its_announce(tmp_path):
    # red-before (fe33ead8): MeshChat started before the PC listened, outside tx, and the kit asked for an announce (curl)
    d = dry(tmp_path)
    assert d.rc == 0, d.err[-2000:]
    b = block(d.out, 5)
    lines = b.splitlines()
    ex = next(i for i, x in enumerate(lines) if "rns-exchange" in x and "[dry] PC > 5-peer.txt" in x)
    st = next(i for i, x in enumerate(lines) if x.startswith("  [dry] Z > 5-start.txt:"))
    assert ex < st, "the PC does not listen before the MeshChat start"
    assert lines[st - 1].startswith("TX | Z:r2 | 1 | control | must 5-start.txt"), lines[st - 1]
    assert lines[st + 1] == "FRAME | Z:r2 | control"
    assert " 420 " in lines[ex]
    assert "/api/v1/announce" not in b and "rnode_framing yes" not in b and "rnode_framing no" not in b
    assert sum(1 for x in lines if x.startswith("  [dry] Z > 5-start.txt:")) == 1


def test_row_5_journals_the_meshchat_stop_before_its_start(tmp_path):
    # a24 M2 (G3): a STOP after the start must stop MeshChat: its guarded stop is journalled before the start runs
    lines = block(dry(tmp_path).out, 5).splitlines()
    st = next(i for i, x in enumerate(lines) if x.startswith("  [dry] Z > 5-start.txt:"))
    un = [i for i, x in enumerate(lines) if x.startswith("  [dry] undo += Z: ") and "stack stop meshchat@r2 --yes" in x]
    assert un and un[-1] < st, "no MeshChat stop journalled before its start"


def test_row_5_needs_no_set_form():
    text = (HW / "rf-proof.sh").read_text()
    assert re.search(r'\[5\]="start stop rt_get rt_pget rx_mchat"', text)
    assert "[rt_set]=" not in text


# ---- rows 8 and 9: both ends at the minimum ------------------------------------------------------------------------------

def test_rows_8_and_9_drive_both_ends_at_the_minimum():
    rows = {r[0]: r for r in table_rows()}
    assert rows["8"][5] == "min,min" and rows["9"][5] == "min,min"
    doc = (HW / "hardware-proof.md").read_text()
    for r in ("8 | daemon text frames | box H, Meshstick 868", "9 | daemon text frames | box H, PineDio 868"):
        assert f"| {r} | the Zero, board 868 | minimum, restored / minimum, restored |" in doc


@pytest.mark.parametrize("row", ["8", "9"])
def test_rows_8_and_9_set_the_zero_to_the_minimum_with_the_recorded_undo(tmp_path, row):
    # red-before (fe33ead8): the Zero's end ran as-is (no set, no undo)
    b = block(dry(tmp_path, "fast").out, row)
    assert f"  [dry] Z > {row}-set-b.txt: " in b and "daemon r2 --set POWER=2 --yes" in b
    assert "  [dry] undo += Z: @pset Z r2 10" in b


NOREC = ('keep HMS r5; keep Z868 r2; needs() { :; }; undo_mark() { echo 0; }; started() { :; }; '
         'jpower() { echo "JPOWER $*" >> "$EVD/ran.txt"; }; pset() { XRC=0; SET=1; }; '
         'dpower() { local p=10; [ -z "${SET:-}" ] || p=2; printf "STATUS FREQ=868.1 SF=9 POWER=%s\\n" "$p" > "$OUT/PW-$3.txt"; '
         '[ -n "${REC:-}" ] && keep "P_$3" "$p"; return 0; }; '
         'tx() { exit 7; }; RADIO_HMS="H meshstick-1262 868 serial=S"; frame_row 8 H:HMS Z:Z868 min,min -')


@pytest.mark.parametrize("rec, rc", [("1", 7), ("", 2)])
def test_a_min_endpoint_without_a_recorded_power_stops_before_any_change(tmp_path, rec, rc):
    # red-before (fe33ead8): an unrecorded power fell back to 10 and that became the undo's value
    r, evd = source_only(tmp_path, NOREC, REC=rec)
    assert r.returncode == rc, r.stdout + r.stderr
    if rc == 2:
        assert stop_lines(r) == ["STOP: row 8: no recorded power of H:r5: no undo, no set, no TX"]
        assert not (evd / "ran.txt").exists()
    else:
        assert (evd / "ran.txt").read_text().splitlines() == ["JPOWER H r5 10", "JPOWER Z r2 10"]


# ---- the pre-step M ------------------------------------------------------------------------------------------------------

def test_m_runs_one_job_per_stack_row_after_r1_in_the_full_lane(tmp_path):
    # red-before (fe33ead8): there was no pre-step
    d = dry(tmp_path)
    assert d.rc == 0, d.err[-2000:]
    assert d.out.index("== ROW R1") < d.out.index("== ROW M") < d.out.index("== ROW 7")
    b = block(d.out, "M")
    assert "M J1 meshcom@r1: NOT RUN: no per-radio callsign given (MCSET_J1)" in b
    want = [("H", "M-J2-graywolf@r1.txt", "J2 graywolf graywolf@433.toml r1 call 8443"),
            ("Z", "M-J3-meshtastic@r2.txt", "J3 meshtastic meshtastic@868.toml r2 node_name,node_short 8443"),
            ("Z", "M-J4-meshcore@r2.txt", "J4 meshcore meshcore.toml r2 file_node_name 8443")]
    got = [x for x in b.splitlines() if x.startswith("  [dry] ")]
    assert len(got) == 3, got
    for (box, f, args), line in zip(want, got, strict=True):
        assert line.startswith(f"  [dry] {box} > {f}: (stdin) cd ") and f"/venv/lhpc/bin/python - {args} " in line, line


def test_m_is_off_in_the_fast_lane_and_by_choice(tmp_path):
    assert "M: MCSET=off: the pre-step is not run" in block(dry(tmp_path, "fast").out, "M")
    assert "M: MCSET=off: the pre-step is not run" in block(dry(tmp_path, "full", MCSET="off").out, "M")
    b = block(dry(tmp_path, "fast", MCSET="on").out, "M")                                   # on, but no stack row runs
    assert "[dry]" not in b and "MCSET=off" not in b


@pytest.mark.parametrize("env, msg", [({"MCSET": "yes"}, "STOP: MCSET=on|off"),
                                      ({"MCSET_J1": "n0call"}, "STOP: MCSET_J1 is not a callsign[-SSID] or 'global'"),
                                      ({"MCSET_J1": "N0CALL-123"}, "STOP: MCSET_J1 is not a callsign[-SSID] or 'global'")])
def test_a_bad_mcset_input_stops_at_the_start(tmp_path, env, msg):
    d = dry(tmp_path, **env)
    assert d.rc == 2 and msg in d.err, d.err[-500:]
    assert "== ROW" not in d.out


def test_the_j1_value_goes_on_stdin_only(tmp_path):
    d = dry(tmp_path, MCSET_J1="N0CALL-1")
    assert d.rc == 0, d.err[-2000:]
    assert "N0CALL" not in d.out + d.err
    assert "[dry] H > M-J1-meshcom@r1.txt: (stdin) cd " in block(d.out, "M")


def test_the_redactor_and_the_end_scan_cover_the_j1_value(tmp_path):
    r, _ = source_only(tmp_path, 'MCSET_J1=N0CALL-7; OPERATOR_CALL=ZZ9ZZZ; printf "a N0CALL-7 b\\n" | redact; '
                                   'printf "N0CALL-7\\n" > "$EVD/x.txt"; secret_scan; echo AFTER')
    assert r.stdout.splitlines()[0] == "a <REDACTED> b", r.stdout
    assert "FAIL: RE: the J1 callsign in the evidence" in r.stderr and "AFTER" not in r.stdout


MJOB_LIVE = ('MCSET=on; MCSET_J1=; MCSET_PY=x; RUN="2"; WEBPORT=8443; RADIO_H433="H loraham 433"; keep H433 r1; '
             'printf "| job | box | instance | verdict |\\n" > "$EVD/MCSET.md"; '
             'XIN() { printf "%s\\n" "$V" > "$OUT/$2"; XRC=${XR:-0}; }; row_M; echo AFTER')


@pytest.mark.parametrize("v, xr, md, notrun, unres", [
    ("JOB J2 graywolf@r1 PASS: set call", "0", "| J2 | H | graywolf@r1 | PASS: set call |", False, False),
    ("JOB J2 graywolf@r1 NOOP: no source file graywolf@433.toml", "0", "| J2 | H | graywolf@r1 | NOOP: no source file graywolf@433.toml |", False, False),
    ("JOB J2 graywolf@r1 STOP: M1: x", "0", "| J2 | H | graywolf@r1 | STOP: M1: x |", True, False),
    ("JOB J2 graywolf@r1 UNRESOLVED: M3: y", "0", "| J2 | H | graywolf@r1 | UNRESOLVED: M3: y |", True, True),
    ("", "1", "| J2 | H | graywolf@r1 | UNKNOWN: helper rc 1 |", True, True),
    # a24 M3 (G13): a helper that ended rc != 0 after printing a verdict: its state is unknown, never that verdict
    ("JOB J2 graywolf@r1 PASS: set call", "1", "| J2 | H | graywolf@r1 | UNKNOWN: helper rc 1 |", True, True),
])
def test_m_records_each_jobs_verdict_and_names_what_is_not_a_pass(tmp_path, v, xr, md, notrun, unres):
    r, evd = source_only(tmp_path, MJOB_LIVE, V=v, XR=xr)
    assert r.returncode == 0 and "AFTER" in r.stdout, r.stdout + r.stderr          # a job never stops the run
    assert (evd / "MCSET.md").read_text().splitlines()[-1] == md
    assert (evd / "NOT-RUN.txt").exists() == notrun
    assert (evd / "UNRESOLVED.txt").exists() == unres


def test_m_skips_a_row_that_does_not_run(tmp_path):
    r, evd = source_only(tmp_path, MJOB_LIVE.replace("keep H433 r1; ", "keep H433 r1; keep SKIP_2 'absent: radio H433'; "), V="x")
    assert r.returncode == 0, r.stderr
    assert (evd / "MCSET.md").read_text().splitlines()[-1] == "| J2 | - | graywolf | NOT RUN: row 2: absent: radio H433 |"


def _helper(tmp_path, override, files=None):
    rt = tmp_path / "rt"
    (rt / "config" / "stacks").mkdir(parents=True)
    (rt / "venv" / "lhpc" / "bin").mkdir(parents=True)
    lh = rt / "venv" / "lhpc" / "bin" / "lhpc"
    lh.write_text("#!/bin/sh\necho refused; exit 3\n")
    lh.chmod(0o755)
    for n, t in (files or {}).items():
        (rt / "config" / "stacks" / n).write_text(t)
    text = f"OVERRIDE = '{override}'\n" + (HW / "mcset.py").read_text()
    return subprocess.run(["python3", "-I", "-", "J1", "meshcom", "meshcom.toml", "r1", "mc_callsign", "8443", str(rt)],
                          input=text, capture_output=True, text=True, timeout=60, check=False, env={"PATH": os.environ["PATH"]})


def test_the_helper_is_a_noop_without_its_source_file(tmp_path):
    p = _helper(tmp_path, "-")
    assert p.returncode == 0 and p.stdout.strip() == "JOB J1 meshcom@r1 NOOP: no source file meshcom.toml", p.stdout + p.stderr


def test_the_helper_takes_the_override_from_its_stdin_prelude(tmp_path):
    # the source has no mc_callsign: only the override gives the job a value, so it reaches M0 (the refused list: STOP)
    p = _helper(tmp_path, "N0CALL-2", {"meshcom.toml": 'other = "x"\n'})
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines()[-1].startswith("JOB J1 meshcom@r1 STOP: M0: the instance list is refused"), p.stdout
    assert "M0 source=meshcom.toml keys=mc_callsign " in p.stdout
    q = _helper(tmp_path / "b", "-", {"meshcom.toml": 'other = "x"\n'})
    assert q.stdout.strip() == "JOB J1 meshcom@r1 NOOP: none of the job's keys in the source", q.stdout


# ---- row 4 runs only after J4 checked MeshCore's mode --------------------------------------------------------------------

ROW4_M = ('MCSET=${MC:-on}; MCSET_J1=; MCSET_PY=x; RUN="${RR:-4}"; WEBPORT=8443; RADIO_Z868="Z loraham 868"; keep Z868 r2; [ -z "${PRE:-}" ] || keep SKIP_4 "absent: radio Z868"; '
          'printf "| job | box | instance | verdict |\\n" > "$EVD/MCSET.md"; '
          'XIN() { printf "%s\\n" "$V" > "$OUT/$2"; XRC=0; }; row_M; echo "SKIP4=$(getk SKIP_4)"')
MODE_STOP = ("JOB J4 meshcore@r2 STOP: M1: the node's mode reads 'repeater', not chat: row 4's node would relay frames the kit "
             "never counts; nothing set")


@pytest.mark.parametrize("env, want", [
    ({"V": MODE_STOP}, "SKIP4=J4: " + MODE_STOP.split("meshcore@r2 ", 1)[1]),
    ({"MC": "off", "V": "x"}, "SKIP4=MCSET=off: J4 did not check MeshCore's mode"),
    ({"V": "JOB J4 meshcore@r2 PASS: set node_name"}, "SKIP4="),                  # mode checked: row 4 runs
    # fail closed (handler DEC): only a J4 PASS lets row 4 run
    ({"V": "JOB J4 meshcore@r2 STOP: M1: 'x' maps to 0 parameters of meshcore"}, "SKIP4=J4: STOP: M1: 'x' maps to 0 parameters of meshcore"),
    ({"V": "JOB J4 meshcore@r2 NOOP: no source file meshcore.toml"}, "SKIP4=J4: NOOP: no source file meshcore.toml"),
    ({"V": ""}, "SKIP4=J4: UNKNOWN: helper rc 0"),
    ({"MC": "off", "RR": "3", "V": "x"}, "SKIP4="),                                # row 4 not in this run: nothing kept
    ({"MC": "off", "V": "x", "PRE": "1"}, "SKIP4=absent: radio Z868"),             # an earlier reason stays
])
def test_row_4_is_not_run_unless_j4_checked_the_mode(tmp_path, env, want):
    # red-before (5ef04ac0, a24 N2): a J4 mode STOP or MCSET=off left row 4 running in a repeater mode
    r, _ = source_only(tmp_path, ROW4_M, **env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines()[-1] == want


def test_row_4_with_mcset_off_is_not_run_by_name(tmp_path):
    d = dry(tmp_path, MCSET="off")
    assert d.rc == 0, d.err[-2000:]
    got = [ln for ln in d.out.splitlines() if re.fullmatch(r"NOT RUN: row 4 meshcore (box -> peer|peer -> box): MCSET=off: "
                                                           r"J4 did not check MeshCore's mode", ln)]
    assert len(got) == 2, d.out[-3000:]
    assert not re.search(r"^  \[dry\] Z > 4-", d.out, re.MULTILINE)        # nothing of row 4 runs


# ---- Meshtastic's air count (row 3): packets to the node itself are local; the kit's own sends come from its ledger ----------

from test_rf_proof_guards import BASE, MT, PRE, RUN, _rs  # noqa: E402


def _mj(ts, frm, to, **kw):
    import json as _json
    return _json.dumps({"timestamp": ts, "from": frm, "to": to, "size": 5, "bytes": "AABB", **kw}) + "\n"


SELF = _mj(PRE, 7, 7) + _mj(RUN, 7, 7) * 7 + _mj(RUN, 0, 7, want_ack=True) * 6   # GO-F row 3: 13 local API packets
AIR = _mj(RUN, 7, 4294967295, type="position")                                    # its one broadcast


@pytest.mark.parametrize("log, extra, want", [
    (SELF + AIR, "", "r1 1|"),                              # red-before (81c0f431): 14, FAIL (the GO-F row 3 run)
    (SELF + AIR, "keep MTX_H_r1 1; ", "r1 2|"),            # + the kit's own text send (never in this log)
    (SELF + AIR, "keep MTX_H_r1 1; keep MTX_H_r1 2; ", "r1 3|"),   # the ledger's last value counts
    (_mj(PRE, 0, 1) + _mj(RUN, 0, 1) * 11, "", "FAIL r1 11|"),     # no self-addressed packet: the node's number unknown, all count
], ids=["self-local", "ledger", "ledger-last", "unknown-node"])
def test_meshtastic_counts_air_frames_and_the_kits_own_sends(tmp_path, log, extra, want):
    _rs(tmp_path, BASE, {MT: log}, "", want, extra=extra)


def test_row_3_records_its_own_send_before_it_runs():
    text = (HW / "rf-proof.sh").read_text()
    body = text[text.index("row_3() {"):text.index("row_4() {")]
    assert body.index('keep "MTX_${b}_$r"') < body.index('form mt_send rid="$r" n="$n2"')


# ---- row 2: the tracker as-is (his word 2026-11-18) ------------------------------------------------------------------------

ROW2 = ('MODE=live; needs() { return 0; }; undo_mark() { echo 0; }; nonce() { echo rfkit 0a1b2c3d; }; keep H433 r1; '
        'started() { echo REACHED; exit 0; }; row_2 H:H433')


@pytest.mark.parametrize("env, out", [
    ({"TRACKER_CALL": "N0CALL-7", "TRACKER_DBM": "", "TRACKER_ASIS": "1"}, "row 2: the tracker as-is (his word 11-18: run as-is without power check)"),
    ({"TRACKER_CALL": "N0CALL-7", "TRACKER_DBM": "", "TRACKER_ASIS": ""}, "NOT RUN: row 2 kiss + graywolf box -> peer: no tracker power given at GO"),
    ({"TRACKER_CALL": "N0CALL-7", "TRACKER_DBM": "9", "TRACKER_ASIS": ""}, "REACHED"),
])
def test_row_2_runs_the_tracker_as_is_only_with_tracker_asis(tmp_path, env, out):
    # red-before (81c0f431): without TRACKER_DBM row 2 was always NOT RUN (no as-is input)
    r, _ = source_only(tmp_path, ROW2, **env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(ln.startswith(out) for ln in r.stdout.splitlines()), r.stdout


def test_a_bad_tracker_asis_stops_at_the_start(tmp_path):
    d = dry(tmp_path, TRACKER_ASIS="yes")
    assert d.rc == 2 and "STOP: TRACKER_ASIS is '' or 1" in d.err, d.err[-500:]


# ---- row 5 tells the box's announce by its LXMF address (handler DEC 11-18; GO-F row 5 failed on the display name) -------

def test_row_5_reads_the_boxs_lxmf_address_before_the_exchange_and_passes_it(tmp_path):
    # red-before (81c0f431): the exchange was given the fixed name "LHPC"; the box announced "Anonymous Peer"
    d = dry(tmp_path, DRY_MCHASH="6326b7e6a36b7912ba02bfc8260bfc77")
    assert d.rc == 0, d.err[-2000:]
    lines = block(d.out, 5).splitlines()
    rd = next(i for i, x in enumerate(lines) if x.startswith("  [dry] Z > 5-lxmf.txt: "))
    ex = next(i for i, x in enumerate(lines) if "rns-exchange" in x and "[dry] PC > 5-peer.txt" in x)
    assert rd < ex and "state/meshchat/identity" in lines[rd]
    assert " rns-exchange rfkit\\ " in lines[ex] and " 6326b7e6a36b7912ba02bfc8260bfc77 420 " in lines[ex], lines[ex]
    assert " LHPC " not in lines[ex]


ROW5H = ('MODE=live; SCRATCH=$EVD; needs() { return 0; }; undo_push() { :; }; undo_mark() { echo 0; }; nonce() { echo rfkit 0a1b2c3d; }; '
         'must() { case $2 in 5-framing0.txt) echo yes > "$OUT/$2";; 5-lxmf.txt) printf "%s\\n" "$H" > "$OUT/$2";; esac; XRC=0; }; '
         'spower() { :; }; hpower() { :; }; tx() { echo "TX-REACHED $*"; exit 0; }; keep KZ r2; row_5 Z:KZ')


@pytest.mark.parametrize("h, stop", [("LXMF 6326b7e6a36b7912ba02bfc8260bfc77", False), ("", True), ("LXMF 6326b7e6", True),
                                     ("LXMF 6326B7E6A36B7912BA02BFC8260BFC77", True), ("Traceback (most recent call last):", True)])
def test_row_5_without_a_valid_lxmf_address_stops_before_any_tx(tmp_path, h, stop):
    r, _ = source_only(tmp_path, ROW5H, H=h)
    if stop:
        assert r.returncode == 2 and "TX-REACHED" not in r.stdout, r.stdout + r.stderr
        assert stop_lines(r) == ["STOP: row 5: no LXMF address of the box's MeshChat (5-lxmf.txt): the PC cannot tell its announce"]
    else:
        assert r.returncode == 0 and "TX-REACHED R5-rnode 1 control -- peer 5-peer.txt" in r.stdout, r.stdout + r.stderr
        assert " rns-exchange rfkit 0a1b2c3d 6326b7e6a36b7912ba02bfc8260bfc77 420 " in r.stdout
