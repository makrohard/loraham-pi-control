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


def proof(tmp_path, body, path_bin=None):
    """Run `body` in bash after sourcing fw-boxproof.sh (source-only), as a live run with evidence in tmp_path/evd."""
    evd = tmp_path / "evd"
    evd.mkdir(exist_ok=True)
    script = (f'FWPROOF_SOURCE_ONLY=1; . "{PROOF}"\n'
              f'MODE=live; EVD="{evd}"; PW="{PW}"; PW_H="{PW}"; OPERATOR_CALL="{CALL}"\n{body}\n')
    env = {**os.environ, "TMPDIR": str(tmp_path)}
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
  *) printf 'ok\\n';; esac > "$EVD/$f"; [ "$XRC" != 255 ] || stop "$f: ssh transport rc 255"; }}
sshx() {{ local n r; n=$(( $(cat "$EVD/../stops" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$EVD/../stops"   # a file: capture runs it in a pipe
  r={stop1}; [ "$n" = 1 ] || r={stop2}; echo "stop rc $r"; return "$r"; }}
{body}; echo RC=$?"""


def canned(tmp_path, count="4", power="10", start="0", read="0", stop1="0", stop2="0", body="mtstart F6-r2", mp="10",
           size0="500", size1="600"):
    return proof(tmp_path, CANNED.format(count=count, power=power, start=start, read=read, stop1=stop1, stop2=stop2,
                                         body=body, mp=mp, size0=size0, size1=size1))


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


def test_a_password_inside_the_login_name_is_refused_before_any_box_call(tmp_path):
    # The redactor masks the password, so a password that is part of the login name would mask the login in every
    # capture: the live run refuses it at once, with no ssh call.
    root = tmp_path / "r"
    kit = root / "testlab" / "hardware"
    shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns("*.local.env", "__pycache__"))
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=60)
    (kit / "lab.local.env").write_text(f"BOX_H_HOST=192.0.2.10\nBOX_H_USER=u{PW}x\nEVD_ROOT={tmp_path}/evd\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("ssh", "sshpass"):
        (bin_ / name).write_text(f"#!/bin/bash\ntouch {tmp_path}/called\n")
        (bin_ / name).chmod(0o755)
    p = subprocess.run(["bash", str(kit / "fw-boxproof.sh"), "live"], input=f"{PW}\n", capture_output=True, text=True,
                       cwd=tmp_path, env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TMPDIR": str(tmp_path)},
                       check=False, timeout=60)
    assert p.returncode == 2 and "STOP: the box password is part of the login name" in p.stdout, p.stdout + p.stderr
    assert not (tmp_path / "called").exists()
