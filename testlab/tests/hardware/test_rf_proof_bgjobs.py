"""The hardware test line's background peer jobs and shared RNode port (testlab/hardware/rf-proof.sh, G2-RELP4a), in
source-only mode: a row that ends (FAIL, STOP) while a background peer reader still holds the peer lock has that reader's
whole process tree ended before any undo replays, so the peer's power restore gets the lock; R0 lets row 5's RNode port be
held only by this user's rnsd running the lab's RNS config. No radio: the peer is a fake peer.py under the real flock."""
import os
import signal
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
    prelude = f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"\n'
    r = subprocess.run(["bash", "-c", prelude + script], env=_env(tmp_path, **extra), stdin=subprocess.DEVNULL, cwd=evd,
                       capture_output=True, text=True, timeout=120, check=False)
    return r, evd


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


# ---- G2-RELP4a B1: a background reader that outlives its row is ended before the undos ------------------------------------

def _fake_peer(tmp_path):
    """peer.py: each call's arguments to `args`; mt-read writes its pid and sleeps (it holds the peer lock under flock),
    on SIGTERM it writes `off` first (a MeshCom read turns its setinfo off on an interrupt); a power call prints its value."""
    fake = tmp_path / "peer.py"
    fake.write_text(f"""import os, signal, sys, time
a = sys.argv[1:]
open({str(tmp_path / 'args')!r}, 'a').write(' '.join(a) + chr(10))
if a[0] == 'mt-read':
    def off(*_):
        open({str(tmp_path / 'off')!r}, 'w').write('off')
        sys.exit(0)
    signal.signal(signal.SIGTERM, off)
    open({str(tmp_path / 'reader')!r}, 'w').write(str(os.getpid()))
    time.sleep(60)
else:
    print('POWER', a[1] if len(a) > 1 else 10)
""")
    return fake


PEER_SETUP = ('cp "$FAKE" ./peer.py; PEER=./peer.py; LOCK=./lock; VMT=python3; usbguard() { :; }; RUN=3; '
              ': > "$EVD/undo.list"; : > "$EVD/undo.done"; echo 0 > "$EVD/undo.seq"; keep PRE_PC_mt-power 10; ')
# the row: the G2's pre-state journalled (ppower's undo), the box->peer reader started, then the box's send fails
ROW = ('undo_push PC "@pset PC mt-power 10"; BG=1 peer 3-peer-read.txt "$VMT" mt-read rfkit 0a1b2c3d 90; pr=$SPAWN; '
       'for i in $(seq 1 50); do [ -s "$RD" ] && break; sleep 0.1; done; ')   # wallclock: marked tests' text


def _reader_ended(tmp_path):
    pid = int((tmp_path / "reader").read_text())
    try:
        return not _alive(pid)
    finally:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.wallclock("a real background reader under the real flock; bounded at 10 s")
@pytest.mark.parametrize("end", ["fail", "stop", "outside"])
def test_a_row_ended_while_its_reader_holds_the_lock_still_restores_the_peer(tmp_path, end):
    # red-before (13c5b1be): the reader outlived the row under the peer lock, the restore got rc 75: the G2 stayed at 1 dBm
    # and the undo was UNRESOLVED. A FAIL or STOP in the row replays through runrow / recover after runrow's bgend; a STOP
    # outside a row (the reader's shell already gone) only through recover's. Each ends the reader first.
    fake = _fake_peer(tmp_path)
    script = PEER_SETUP + {"fail": f'row_3() {{ {ROW}fail "the G2 send failed"; }}; runrow 3; echo "RR-RC=$?"',
                           "stop": f'row_3() {{ {ROW}stop "row 3: the G2 send failed"; }}; trap recover EXIT; runrow 3',
                           "outside": f'trap recover EXIT; ( {ROW}exit 0 ); stop "interrupted (signal): the recovery runs"'}[end]
    r, evd = source_only(tmp_path, script, FAKE=str(fake), RD=str(tmp_path / "reader"))
    assert _reader_ended(tmp_path), r.stdout + r.stderr
    assert "mt-power 10" in (tmp_path / "args").read_text().splitlines(), r.stdout + r.stderr   # the restore ran
    assert not (evd / "UNRESOLVED.txt").exists() or (evd / "UNRESOLVED.txt").read_text() == "", (evd / "UNRESOLVED.txt").read_text()
    assert (evd / "undo.list").read_text() == ""
    assert (tmp_path / "off").exists()                    # TERM first: the reader ran its own interrupt cleanup


@pytest.mark.wallclock("a real process; bounded at 2 s")
def test_a_listed_job_whose_pid_is_now_another_process_is_left_alone(tmp_path):
    # bg.pids names a pid with its start time: a pid that now belongs to another process (another start time) is not signalled
    other = subprocess.Popen(["sleep", "30"])
    try:
        r, _ = source_only(tmp_path, f'echo "{other.pid} 1" > "$(bgfile)"; bgend; echo "RC=$?"')
        assert "RC=0" in r.stdout and other.poll() is None, r.stdout + r.stderr
    finally:
        other.kill()
        other.wait()


@pytest.mark.wallclock("a real lock holder; bounded at 12 s")
def test_a_peer_lock_still_held_after_the_jobs_ended_is_named_before_any_undo(tmp_path):
    # the jobs listed are gone, but the peer lock is still held (an unlisted holder): bgend fails naming the lock, so the
    # caller STOPs (runrow) or records it (recover) instead of a restore refused with rc 75
    lock = tmp_path / "evd" / "lock"
    lock.parent.mkdir()
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"], stdin=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if subprocess.run(["flock", "-n", str(lock), "true"], check=False).returncode != 0:
                break
            time.sleep(0.05)   # wallclock: the holder's start (marked test)
        r, _ = _source_only_in(tmp_path, 'LOCK=./lock; echo "1 1" > "$(bgfile)"; bgend; echo "RC=$?"')
    finally:
        holder.kill()
        holder.wait()
    assert "RC=1" in r.stdout and "the peer lock is still held" in r.stderr, r.stdout + r.stderr


def _source_only_in(tmp_path, script):
    """source_only with an $EVD that may exist already."""
    evd = tmp_path / "evd"
    prelude = f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"\n'
    r = subprocess.run(["bash", "-c", prelude + script], env=_env(tmp_path), stdin=subprocess.DEVNULL, cwd=evd,
                       capture_output=True, text=True, timeout=120, check=False)
    return r, evd


# ---- G2-RELP4a B2: row 5's RNode port, held only by the lab's rnsd, passes R0 ----------------------------------------------

def _holder(tmp_path, cfg, dev, name="rnsd"):
    """A process named `name` (its comm: the script's file name) with the command line `<name> --config <cfg>`, holding
    `dev` open (from the environment, so the command line stays rnsd's own)."""
    exe = tmp_path / name
    exe.write_text(f"#!{sys.executable}\nimport os, time\nf = open(os.environ['DEV'])\n"
                   f"open({str(tmp_path / (name + '.up'))!r}, 'w').write('up')\ntime.sleep(30)\n")
    exe.chmod(0o755)
    p = subprocess.Popen([str(exe), "--config", str(cfg)], env={**os.environ, "DEV": str(dev)}, stdin=subprocess.DEVNULL)
    for _ in range(100):
        if (tmp_path / (name + ".up")).exists():
            break
        time.sleep(0.05)   # wallclock: the holder's start, bounded at 5 s (marked tests only)
    return p


def _ports(tmp_path, args):
    r, _ = source_only(tmp_path, f'python3 -I -c "$PORTS_PY" {args}')
    return r.stdout.splitlines(), r


@pytest.mark.wallclock("a real holder process; bounded at 5 s")
@pytest.mark.parametrize("name, cfg_of, want", [("rnsd", "lab", "rnsd-shared"), ("rnsd", "other", "rnsd"),
                                                ("screen", "lab", "screen")])
def test_only_an_rnsd_running_the_lab_config_is_the_shared_holder(tmp_path, name, cfg_of, want):
    # red-before (13c5b1be): R0 STOPped on any holder, the rnsd row 5 needs included; a name match alone must not pass
    dev, lab, other = tmp_path / "ttyRNODE", tmp_path / "rns-lab", tmp_path / "rns-other"
    dev.write_text("")
    lab.mkdir()
    other.mkdir()
    p = _holder(tmp_path, lab if cfg_of == "lab" else other, dev, name)
    try:
        lines, r = _ports(tmp_path, f'"--rns={lab}" "{dev}"')
    finally:
        p.kill()
        p.wait()
    assert lines == [f"HELD {dev} {want}"], r.stdout + r.stderr


@pytest.mark.parametrize("held, rdevs, stops", [
    ("HELD /dev/serial/by-id/rn rnsd-shared", " /dev/serial/by-id/rn ", ""),
    ("HELD /dev/serial/by-id/rn rnsd", " /dev/serial/by-id/rn ", "HELD /dev/serial/by-id/rn rnsd"),                # name only
    ("HELD /dev/serial/by-id/rn rnsd-shared,screen", " /dev/serial/by-id/rn ", "HELD /dev/serial/by-id/rn rnsd-shared,screen"),
    ("HELD /dev/serial/by-id/g2 rnsd-shared", " /dev/serial/by-id/rn ", "HELD /dev/serial/by-id/g2 rnsd-shared"),  # not the RNode
    ("HELD /dev/serial/by-id/g2 gpsd\nFREE /dev/serial/by-id/rn", " /dev/serial/by-id/rn ", "HELD /dev/serial/by-id/g2 gpsd"),
])
def test_r0_passes_the_rnodes_port_only_with_the_shared_rnsd_as_its_one_holder(tmp_path, held, rdevs, stops):
    r, _ = source_only(tmp_path, f'printf "%b\\n" {held!r} > "$EVD/p.txt"; echo "STOPS=$(heldports p.txt {rdevs!r})"')
    assert r.stdout.strip() == f"STOPS={stops}", r.stdout + r.stderr
