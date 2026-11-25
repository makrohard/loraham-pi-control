"""Row 13 and the Reticulum box sends of the kit (testlab/hardware/rf-proof.sh, plan KIT-RT-FORMS v8): the row's four
endpoints, the member's and the Zero's channel journalled and restored through the product, and the ONE bounded send
(`rt_send`, RTSEND_PY): MeshChat's own send, opportunistic, cancelled at t0 + 8 s (t0 = before the POST), LATE past
t0 + 9.5 s, an interval timer ending the python at t0 + 9.5 s; any end but SENT and NO-PATH stops that box's node first.
The send runs against a stub MeshChat (HTTP) and a stub rnpath; the channel forms against this head's product on a
temporary runtime. No box, no radio."""
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HW = Path(__file__).resolve().parents[2] / "hardware"
REPO = HW.parents[1]
sys.path.insert(0, str(REPO / "tests"))
import radios_fixture  # noqa: E402

DEST = "00112233445566778899aabbccddeeff"
NONCE = "rfp 0a1b2c3d"


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RADIO_", "PEER_", "BOX_", "DRY_", "LANE", "ROWS_ONLY"))}
    env.update(TMPDIR=str(tmp_path), **extra)
    return env


def _sourced(tmp_path, script, **extra):
    evd = tmp_path / "evd"
    evd.mkdir(exist_ok=True)
    prelude = f'RFPROOF_SOURCE_ONLY=1 . "{HW}/rf-proof.sh"; EVD="{evd}"; OUT=$EVD; MODE=live; : > "$EVD/state.env"\n'
    return subprocess.run(["bash", "-c", prelude + script], env=_env(tmp_path, **extra), stdin=subprocess.DEVNULL, cwd=evd,
                          capture_output=True, text=True, timeout=120, check=False)


def _runtime(tmp_path, spi=()):
    root = tmp_path / "rt"
    radios_fixture.write(root, radios_fixture.BOX_H)
    radios_fixture.reticulum_backend(root, list(spi))
    (root / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return root


def _box_run(tmp_path, root, cmd):
    """A rendered box form run on the runtime `root` ({LH} this lhpc, {RT} the runtime)."""
    if not (root / "venv" / "lhpc" / "bin").exists():
        from test_rf_proof_rtpower import box_venv
        box_venv(root)
    cmd = cmd.replace("{LH}", f"{root}/venv/lhpc/bin/lhpc").replace("{RT}", str(root))
    return subprocess.run(["bash", "-c", cmd], env=_env(tmp_path, LHPC_RUNTIME_ROOT=str(root),
                                                        PYTHONPATH=f"{REPO}:{REPO}/testlab"),
                          stdin=subprocess.DEVNULL, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False)


def _form(tmp_path, name, **kv):
    r = _sourced(tmp_path, f"form {name} " + " ".join(f"{k}='{v}'" for k, v in kv.items()))
    assert r.returncode == 0 and r.stdout, r.stderr
    return r.stdout


# ---- row 13's four endpoints (G1 row 238 B2) ---------------------------------------------------------------------------

def test_row_13_has_four_endpoints_the_member_and_the_zero_each_their_own(tmp_path):
    # red-before (80068d70): rmember gave "H:HMS,zero=Z:Z868" once the flags held two values, no rzero
    r = _sourced(tmp_path, 'reps 13 | paste -sd " "; rmember 13; rzero 13')
    assert r.returncode == 0 and r.stdout.splitlines() == ["H:H868 peer:rnode H:HMS Z:Z868", "H:HMS", "Z:Z868"], r.stderr


def test_the_dry_r0_resolves_row_13s_four_endpoints_before_its_budget(tmp_path):
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="rns")
    assert r.returncode == 0, r.stderr[-1500:]
    r0 = r.stdout.split("== ROW R0", 1)[1].split("== ROW R1", 1)[0].splitlines()
    res = [ln for ln in r0 if ln.startswith("data: R0: row 13: ")]
    bud = [i for i, ln in enumerate(r0) if ln.startswith("BUDGET | ")]
    assert [ln.split(" (")[0] for ln in res] == ["data: R0: row 13: H868 = H:r2", "data: R0: row 13: HMS = H:r5",
                                                 "data: R0: row 13: Z868 = Z:r2"]
    assert bud and max(r0.index(ln) for ln in res) < min(bud) and len(bud) == 4


def test_row_13s_member_leg_runs_in_its_order_with_its_undos(tmp_path):
    # dry LANE=rns: the member's channel journalled before its save and the join; the Zero's journalled, set, its node's undo
    # newest (so stopped first), its MeshChat started; H's announce call; one send each way, each with its FRAME
    # red-before (80068d70): no channel save, no Zero step: the member leg an RNode exchange
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="rns")
    assert r.returncode == 0, r.stderr[-1500:]
    lines = r.stdout.splitlines()
    want = ["[dry] H > 13-member-freq0.txt: ", "[dry] undo += H: ", "[dry] H > 13-member-freq.txt: ", "[dry] H > 13-join.txt: ",
            "[dry] Z > 13-zero-freq0.txt: ", "[dry] undo += Z: ", "[dry] Z > 13-zero-freq.txt: ", "[dry] undo += Z: ",
            "[dry] undo += Z: ", "[dry] Z > 13-zero-start.txt: ", "[dry] H > 13-member-announce.txt: ",
            "[dry] H > 13-member-send.txt: ", "[dry] Z > 13-zero-send.txt: "]
    at, i = [], 0
    for n, ln in enumerate(lines):
        if i < len(want) and ln.strip().startswith(want[i]) and (i not in (1, 5) or "FREQ=" in ln or "frequency" in ln):
            at.append(n)
            i += 1
    assert i == len(want), (want[i] if i < len(want) else "", [lines[n].strip()[:60] for n in at])
    got = [lines[n].strip() for n in at]
    assert got[1].endswith("--daemon-param FREQ=")                     # the member's own FREQ (none): the undo clears
    assert got[2].endswith("--daemon-param FREQ=868.300")
    assert got[5].endswith('config reticulum@r2 frequency ""') and got[6].endswith('frequency "868300000"')
    assert "meshchat@r2" in got[7] and "reticulum@r2" in got[8]         # pushed last = undone first: the Zero's node
    for tag in ("13-member-send", "13-zero-send"):
        tx = next(ln for ln in lines if ln.startswith("TX | ") and f"X {tag}.txt" in ln)
        assert f"FRAME | {tx.split(' | ')[1].split(':')[0]}:* | {tx.split(' | ')[3]}" in lines, tag


# ---- the channel journal: the member's own dp_FREQ, the Zero's own file_frequency ------------------------------------------

@pytest.mark.parametrize("own", [None, "868.100"])
def test_the_members_own_freq_is_restored_exactly(tmp_path, own):
    root = _runtime(tmp_path)
    toml = root / "config" / "stacks" / "reticulum@r4.toml"
    if own:
        toml.write_text(f'dp_FREQ = "{own}"\n')
    o = _box_run(tmp_path, root, _form(tmp_path, "rt_own", rid="r4", k="dp_FREQ")).stdout
    assert o == f"OWN {own or ''}\n"
    assert _box_run(tmp_path, root, _form(tmp_path, "rt_dfreq", rid="r4", f="868.300")).returncode == 0
    assert 'dp_FREQ = "868.300"' in toml.read_text()
    undo = _form(tmp_path, "rt_dfreq", rid="r4", f=o.split(" ", 1)[1].strip())          # the undo the row pushes
    assert _box_run(tmp_path, root, undo).returncode == 0
    # a25 B1: the OWN value comes back (absent -> cleared), never a rendered one
    assert ("dp_FREQ" in toml.read_text()) is bool(own) and (not own or f'dp_FREQ = "{own}"' in toml.read_text())


def _rendered_frequency(root):
    """The `frequency` of `[[LoRa r2]]` as the node's file renders it on the runtime `root` (what the radio gets)."""
    from lhpc.core import instances
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
    inst = instances.copy_of(svc, "reticulum", "r2")[0]
    svc.write_config_files("rns", inst.band, inst=inst)
    lines = [ln.strip() for ln in (root / "state" / "reticulum" / "config").read_text().splitlines()]
    return next(ln for ln in lines[lines.index("[[LoRa r2]]"):] if ln.startswith("frequency"))


def test_the_zeros_channel_is_set_and_its_rendered_frequency_restored(tmp_path):
    root = _runtime(tmp_path, spi=["r2"])
    before = _rendered_frequency(root)
    o = _box_run(tmp_path, root, _form(tmp_path, "rt_own", rid="r2", k="file_frequency")).stdout
    assert o == "OWN \n"
    assert _box_run(tmp_path, root, _form(tmp_path, "rt_zfreq", rid="r2", hz="868300000")).returncode == 0
    assert _rendered_frequency(root) == "frequency = 868300000"
    # the undo the row pushes: the own value, here none -> a blank (unquoted it was a show and the Zero stayed on 868.3)
    assert _box_run(tmp_path, root, _form(tmp_path, "rt_zfreq", rid="r2", hz="")).returncode == 0
    # the rendered value is back; the toml keeps `file_frequency = ""` and lhpc's header line (measured, plan v8 a25 N1)
    assert before == "frequency = 868500000" and _rendered_frequency(root) == before
    assert 'file_frequency = ""' in (root / "config" / "stacks" / "reticulum@r2.toml").read_text()


# ---- the bounded send ---------------------------------------------------------------------------------------------------

class _MeshChat(http.server.BaseHTTPRequestHandler):
    send_delay, cancel_delay, body_delay, seen = 0.0, 0.0, 0.0, []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        type(self).seen.append((round(time.monotonic(), 2), self.path, body))  # wallclock: when the form's requests came
        time.sleep(self.send_delay if self.path.endswith("/send") else self.cancel_delay)  # wallclock: a slow MeshChat
        out = {"lxmf_message": {"hash": "ab" * 16, "state": "cancelled"}}
        data = json.dumps(out).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.path.endswith("/send"):
                time.sleep(self.body_delay)  # wallclock: the send's headers in time, its body late
            self.wfile.write(data)
        except OSError:
            pass

    def log_message(self, *a):
        pass


@pytest.fixture
def meshchat():
    _MeshChat.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MeshChat)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def _send(tmp_path, srv, path=True, send_delay=0.0, cancel_delay=0.0, patch=(), body_delay=0.0):
    """RTSEND_PY as the kit holds it (its port the stub's; `patch`: (old, new) edits), its node's rnpath a stub: the
    python's (returncode, stdout, seconds) and the stub's requests."""
    root = _runtime(tmp_path)
    from lhpc.core import reticulum_members
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
    rnpath = svc._lifecycle().source_dir(reticulum_members._comp(svc)) / ".venv" / "bin" / "rnpath"
    rnpath.parent.mkdir(parents=True)
    rnpath.write_text(f"#!/bin/sh\necho '<{DEST if path else 'ffff'}> is 1 hop away via <x> on LoRa r5'\n")
    rnpath.chmod(0o755)
    code = _sourced(tmp_path, 'printf "%s" "$RTSEND_PY"').stdout.replace("127.0.0.1:8790", f"127.0.0.1:{srv.server_port}")
    for old, new in patch:
        assert code.count(old) == 1, old
        code = code.replace(old, new)
    _MeshChat.send_delay, _MeshChat.cancel_delay, _MeshChat.body_delay = send_delay, cancel_delay, body_delay
    t = time.monotonic()  # wallclock: how long the form ran (its own clock is under test)
    r = subprocess.run([sys.executable, "-c", code, DEST, NONCE], env=_env(tmp_path, LHPC_RUNTIME_ROOT=str(root),
                       PYTHONPATH=f"{REPO}:{REPO}/testlab"), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=60, check=False)
    return r.returncode, r.stdout, time.monotonic() - t, _MeshChat.seen  # wallclock: as above


@pytest.mark.slow
@pytest.mark.wallclock("the form's own clock (cancel at t0 + 8 s) is what is under test")
def test_the_send_is_opportunistic_and_cancelled_at_t0_plus_8(tmp_path, meshchat):
    rc, out, _secs, seen = _send(tmp_path, meshchat)
    assert rc == 0 and out.startswith(f"SENT {'ab' * 16}\nCANCEL ") and out.rstrip().endswith(" cancelled"), out
    (t_send, p1, body), (t_cancel, p2, _) = seen
    assert p1 == "/api/v1/lxmf-messages/send" and p2 == f"/api/v1/lxmf-messages/{'ab' * 16}/cancel"
    assert body == {"delivery_method": "opportunistic", "lxmf_message": {"destination_hash": DEST, "content": NONCE}}
    assert 7.8 <= t_cancel - t_send <= 8.6


@pytest.mark.slow
@pytest.mark.wallclock("a send answered after 6 s runs into the form's 5 s POST bound")
@pytest.mark.parametrize("delay", [6.0, 30.0])                       # late, and never within the run
def test_a_late_or_unanswered_send_is_late_and_never_cancelled(tmp_path, meshchat, delay):
    # red-before (G1 row 237 B2): the 8 s ran from the POST's return, an unbounded POST held the clock
    rc, out, secs, seen = _send(tmp_path, meshchat, send_delay=delay)
    assert rc == 5 and out.startswith("LATE: no send answer within 5 s") and [p for _, p, _ in seen] == [
        "/api/v1/lxmf-messages/send"] and secs < 7


@pytest.mark.slow
@pytest.mark.wallclock("a send whose headers come at 3 s and its body at 6 s: each read within the 5 s socket timeout")
def test_a_send_answered_after_5_s_in_parts_is_late_and_never_cancelled(tmp_path, meshchat):
    # red-before (d7f86ac8, G2 row 240 B2): no clock read after the send: SENT, CANCEL 8.0 cancelled, exit 0
    rc, out, secs, seen = _send(tmp_path, meshchat, send_delay=3.0, body_delay=3.0)
    assert rc == 5 and out.startswith("LATE: the send answered after 6.") and "SENT" not in out, (rc, out)
    assert [p for _, p, _ in seen] == ["/api/v1/lxmf-messages/send"] and secs < 7.5


@pytest.mark.slow
@pytest.mark.wallclock("only the interval timer can end a hung cancel here: SIGALRM at t0 + 9.5 s")
def test_a_hung_cancel_is_ended_by_the_interval_timer_at_t0_plus_9_5(tmp_path, meshchat):
    # the cancel's own 1.5 s timeout widened to 30 s, so the HTTP timeout cannot end it first (G1 row 238 B1: a
    # signal.alarm(9.5) raised TypeError)
    rc, out, secs, _seen = _send(tmp_path, meshchat, cancel_delay=30.0, patch=[('/cancel", {}, 1.5)', '/cancel", {}, 30)')])
    assert rc == -signal.SIGALRM and out.startswith("SENT ") and "CANCEL" not in out, (rc, out)
    assert 9.3 <= secs <= 11.5


@pytest.mark.slow
@pytest.mark.wallclock("a cancel answered at t0 + 10 s meets the t0 + 9.5 s LATE comparison")
def test_a_cancel_ending_after_t0_plus_9_5_is_late(tmp_path, meshchat):
    # the timer off and the cancel's timeout 3 s: the LATE comparison itself decides
    rc, out, _secs, _seen = _send(tmp_path, meshchat, cancel_delay=2.0,
                                patch=[("signal.setitimer(signal.ITIMER_REAL, 9.5)", "None"),
                                       ('/cancel", {}, 1.5)', '/cancel", {}, 3)')])
    assert rc == 5 and "\nCANCEL 10." in out, out


def test_no_path_sends_nothing(tmp_path, meshchat):
    rc, out, _secs, seen = _send(tmp_path, meshchat, path=False)
    assert rc == 4 and out.startswith("NO-PATH") and seen == []


# ---- any end but SENT and NO-PATH stops the box's node first -------------------------------------------------------------

STUBS = ('tx() { [ "$4" = -- ] && { shift 4; "$@"; }; return 0; }; '
         'X() { echo "X $1 $2"; case "$2" in *-node-stop.txt) echo "  $3"; XRC=0;; *) XRC=$RC;; esac; }; ')


@pytest.mark.parametrize("rc", [5, 6, 124, 142, 255])
def test_every_other_end_of_the_send_stops_its_node_first(tmp_path, rc):
    # red-before (80068d70): no node stop; (v7) only LATE stopped it, an ssh exit 255 ran on
    r = _sourced(tmp_path, STUBS + f'RC={rc}; rtsend 13-zero-send Z r2 {DEST} "{NONCE}" R13-Z-r2', RC=str(rc))
    lines = r.stdout.splitlines()
    assert r.returncode == 2 and lines[0] == "X Z 13-zero-send.txt" and lines[1] == "X Z 13-zero-send-node-stop.txt"
    assert "stack stop reticulum@r2" in lines[2] and "the box's send ended" in r.stderr


@pytest.mark.parametrize("rc, want", [(0, 0), (4, 1)])
def test_sent_and_no_path_leave_the_node_running(tmp_path, rc, want):
    r = _sourced(tmp_path, STUBS + f'RC={rc}; rtsend 13-member-send H r2 {DEST} "{NONCE}" R13-H-r2 R13-H-r5; echo "rc=$?"')
    assert r.stdout.splitlines() == ["X H 13-member-send.txt", f"rc={want}"], r.stdout


# ---- each direction's own outcome: a NO-PATH direction is NOT RUN, never a PASS ------------------------------------------

def _leg(r, row, direction):
    return [ln for ln in r.stdout.splitlines() if ln.startswith(f"REPORT | {row} | ") and f" | {direction} | " in ln]


@pytest.mark.parametrize("nopath, want", [
    ("", None),
    ("13-member-send", "FAIL: row 13 member: heard, no path: H -> Zero: H's node had no path to the Zero, nothing sent; Zero -> H "
                       "delivered and stored"),
    ("13-zero-send", "FAIL: row 13 member: heard, no path: Zero -> H: the Zero's node had no path to H, nothing sent; H -> Zero "
                     "delivered and stored"),
    ("13-member-send 13-zero-send", "FAIL: row 13 member: heard, no path: H -> Zero: H's node had no path to the Zero, nothing "
                                    "sent; Zero -> H: the Zero's node had no path to H, nothing sent")])
def test_row_13s_member_leg_passes_only_with_both_directions_delivered(tmp_path, nopath, want):
    # red-before (d7f86ac8, G2 row 240 B1): a NO-PATH direction still gave the leg's PASS, "both directions ... stored on
    # each side", with the NOT RUN note appended; (b7be347b, PLAN-D172 K-d): with both announces heard it was NOT RUN, not FAIL
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="rns", DRY_NOPATH=nopath)
    assert r.returncode == 0, r.stderr[-1500:]
    (leg,) = _leg(r, 13, "member both ways")
    if want is None:
        assert leg.endswith(" | DRY-PASS") and "NOT RUN" not in leg, leg
    else:
        assert leg.endswith(" | " + want) and "stored on each side" not in leg, leg
        assert not [ln for ln in r.stdout.splitlines() if ln.startswith("NOT RUN: row 13 ") and "member both ways:" in ln]


def _cmds(r):
    """The dry run's box commands, in order: (box, evidence file)."""
    import re
    return [m.groups() for ln in r.stdout.splitlines() if (m := re.match(r"  \[dry\] (\S+) > (\S+): ", ln))]


@pytest.mark.parametrize("unheard, want, called", [
    ("13-zero-heard", "the Zero's start announce not heard on H r5 (13-zero-heard.txt; H's RX/TX lines: 13-zero-heard-win.txt)",
     False),
    ("13-member-heard", "H's announce not heard on the Zero r2 (13-member-heard.txt; the Zero's RX/TX lines: "
                        "13-member-heard-win.txt)", True)])
def test_row_13s_member_leg_fails_on_an_announce_the_other_side_did_not_hear(tmp_path, unheard, want, called):
    # red-before (b7be347b, PLAN-D172 K-a/K-c): no one checked the announces were heard; a lost one became NO-PATH, NOT RUN
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="rns", DRY_UNHEARD=unheard)
    assert r.returncode == 0, r.stderr[-1500:]
    (leg,) = _leg(r, 13, "member both ways")
    assert leg.endswith(" | FAIL: row 13 member: " + want), leg
    files = [f for _, f in _cmds(r)]
    assert f"{unheard}-win.txt" in files and ("13-member-announce.txt" in files) == called, files
    assert "13-member-send.txt" not in files and "13-zero-send.txt" not in files, files     # no retry, no send after a miss


def test_row_13s_member_leg_hears_each_announce_before_the_next_step(tmp_path):
    # red-before (b7be347b, PLAN-D172 K-a..K-c): H called its announce right after the Zero's start, into the Zero's burst
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="rns")
    assert r.returncode == 0, r.stderr[-1500:]
    c = _cmds(r)
    order = [("H", "13-member-rfmark.txt"), ("Z", "13-zero-rfmark.txt"), ("Z", "13-zero-start.txt"), ("H", "13-zero-heard.txt"),
             ("Z", "13-zero-quiet.txt"), ("H", "13-member-announce.txt"), ("Z", "13-member-heard.txt"), ("H", "13-member-send.txt")]
    assert [c.index(x) for x in order] == sorted(c.index(x) for x in order), c
    assert "13-zero-heard-win.txt" not in [f for _, f in c]


def _rflog(tmp_path, lines):
    log = tmp_path / "rt" / "logs" / "r5" / "rf-reticulum.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(ln + "\n" for ln in lines))
    return log


def test_rt_rfseen_takes_only_this_legs_rx_lines_of_the_address(tmp_path):
    # the identities are the same every run: an RX line of the address before the leg's mark is never this leg's
    _rflog(tmp_path, [f"2026-11-25T07:00:00.000Z RX rssi=-50 snr=9 len=236 hex=0071ab{DEST}",
                      f"2026-11-25T07:11:24.220Z TX rssi=- snr=- len=236 outcome=ok hex=0071ab{DEST}",
                      "2026-11-25T07:11:25.646Z RX rssi=-58 snr=11 len=198 hex=0051abcb730d11"])
    def run(frm):
        return _sourced(tmp_path, f'c=$(form rt_rfseen rid=r5 n={DEST} from={frm}); bash -c "${{c//\\{{RT\\}}/{tmp_path}/rt}}"; echo "rc=$?"')
    assert run(1).stdout.splitlines()[-1] == "rc=0" and DEST in run(1).stdout
    assert run(2).stdout.splitlines() == ["rc=1"], run(2).stdout     # a TX of it and an RX of another address: not heard


def test_rnsmark_and_rfline_give_the_first_line_after_the_mark(tmp_path):
    _rflog(tmp_path, ["a RX x", "b TX y"])
    r = _sourced(tmp_path, f'c=$(rnsmark r5); bash -c "${{c//\\{{RT\\}}/{tmp_path}/rt}}" > "$EVD/m.txt"; rfline m.txt; : > "$EVD/e.txt"; rfline e.txt')
    assert r.stdout.splitlines() == ["3", "1"], (r.stdout, r.stderr)


@pytest.mark.wallclock("rnsquiet polls its RF log once a second: 2 s without a new TX line")
def test_rnsquiet_says_quiet_after_two_seconds_without_a_new_tx_line(tmp_path):
    _rflog(tmp_path, ["a TX x"])
    t0 = time.monotonic()
    r = _sourced(tmp_path, f'c=$(rnsquiet r5); bash -c "${{c//\\{{RT\\}}/{tmp_path}/rt}}"')
    assert r.stdout.splitlines() == ["QUIET"] and time.monotonic() - t0 >= 1.9, (r.stdout, r.stderr)   # wallclock: 2 polls


def test_a_no_path_box_send_is_not_run_for_that_reason(tmp_path):
    # red-before (d7f86ac8): row 12's NO-PATH box -> peer was NOT RUN as "form not confirmed: rt_send"; the reason now names
    # the box's own RX of the PC's announce (here not heard: DRY_PCANNMISS)
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="full", DRY_NOPATH="12-box-send", DRY_PCANNMISS="12")
    assert r.returncode == 0, r.stderr[-1500:]
    (leg,) = _leg(r, 12, "box -> peer")
    assert leg.endswith(" | NOT RUN: PC-ANNOUNCE NOT heard by the box (9999-12-31T23:59:59.500Z), so the node had no path to "
                        "the PC, nothing sent"), leg
    assert _leg(r, 5, "box -> peer")[0].endswith(" | DRY-PASS")


# ---- the PC's second announce on air: the box's own RX of it (PCANN_PY, plan ROW12-PC-ANNOUNCE-QUIET v3 F2) --------------

PCHASH = "ab" * 16                                          # the PC's LXMF hash (a test value; the real one is new per run)
TID, BOXD = "11" * 16, "22" * 16                            # stand-ins: the rnsd's transport id, the box's LXMF address
RX = 'rssi=-15.00 snr=12.00 len={n} band=868 hex={h} ascii="..."'


def _frame(head, n):
    """A frame as the box's daemon logs it: `head` (the RNode header byte, flags, hops, addresses) and zeros up to n bytes
    (a21's kit run 00:01Z, box H r5 rf-daemon.log: the real layout and lengths; the payload is not needed)."""
    return head + "00" * (n - len(head) // 2)


def _pcann(tmp_path, lines, t="2026-11-25T00:02:10.500Z"):
    """PCANN_PY as the box runs it (this repo's lhpc, the product's rflog parser) over logs/r5/rf-daemon.log = `lines`."""
    from test_rf_proof_guards import source_only
    r, _ = source_only(tmp_path, 'printf %s "$PCANN_PY"')
    rt = tmp_path / "rt"
    (rt / "logs" / "r5").mkdir(parents=True)
    (rt / "logs" / "r5" / "rf-daemon.log").write_text("".join(ln + "\n" for ln in lines))
    out = subprocess.run([sys.executable, "-c", r.stdout, "r5", t, PCHASH], cwd=rt, env={**os.environ, "PYTHONPATH": str(REPO)},
                         capture_output=True, text=True, timeout=60, check=True).stdout
    assert PCHASH not in out and TID not in out, out                       # the evidence carries no address
    return out.splitlines()


def test_the_aired_check_finds_the_pcs_announce_by_the_header_types_destination(tmp_path):
    # the PC's rnsd re-sends a client's announce as HEADER_2 (flags 0x71: transport id at [3:19], dest at [19:35]), as the
    # box's own announce reads tonight; HEADER_1 (0x01) carries the dest at [3:19]
    h2 = _frame("9071" + "00" + TID + PCHASH, 236)
    h1 = _frame("2001" + "00" + PCHASH, 236)
    out = _pcann(tmp_path, [f"2026-11-25T00:02:10.230Z RX {RX.format(n=236, h=h2)}",
                            f"2026-11-25T00:02:11.000Z RX {RX.format(n=236, h=h1)}"])
    assert out == ["PCANN 2026-11-25T00:02:10.230Z rf-daemon.log len=236 flags=0x71 hops=0 header=2",
                   "PCANN 2026-11-25T00:02:11.000Z rf-daemon.log len=236 flags=0x01 hops=0 header=1",
                   "WINDOW 00:02:08 00:02:22 RX 2"], out


def test_the_aired_check_counts_no_other_frame(tmp_path):
    # tonight's lines in their layout: the PC's message (flags 0x00, LXMF DATA to the box: heard, not an announce), DATA to
    # the PC's hash (not an announce either), the PC's
    # hash at the HEADER_1 place of a HEADER_2 frame (there it is the transport id), another node's announce, the box's own
    # TX of an announce to the PC's hash, and a PC announce outside [t - 2 s, t + 12 s]
    lines = [f"2026-11-25T00:02:11.859Z RX {RX.format(n=228, h=_frame('2000' + '00' + BOXD, 228))}",
             f"2026-11-25T00:02:11.900Z RX {RX.format(n=228, h=_frame('2000' + '00' + PCHASH, 228))}",
             f"2026-11-25T00:02:12.000Z RX {RX.format(n=236, h=_frame('9071' + '00' + PCHASH + BOXD, 236))}",
             f"2026-11-25T00:02:13.000Z RX {RX.format(n=236, h=_frame('9071' + '00' + TID + BOXD, 236))}",
             "2026-11-25T00:02:10.230Z TX rssi=- snr=- len=236 outcome=ok band=868 hex=" + _frame("9071" + "00" + TID + PCHASH, 236)
             + ' ascii="..."',
             f"2026-11-25T00:02:08.400Z RX {RX.format(n=236, h=_frame('9071' + '00' + TID + PCHASH, 236))}",
             f"2026-11-25T00:02:22.600Z RX {RX.format(n=236, h=_frame('9071' + '00' + TID + PCHASH, 236))}"]
    assert _pcann(tmp_path, lines) == ["WINDOW 00:02:08 00:02:22 RX 4"]


def test_a_no_path_box_send_after_the_box_heard_the_pcs_announce_is_a_fail(tmp_path):
    # plan F2: heard by the box and still no path = a path defect (FAIL); not heard = the send could not be tried (NOT RUN,
    # by that reason); a sent message is judged by its delivery as before, the check recorded
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="full", DRY_NOPATH="12-box-send")
    assert r.returncode == 0, r.stderr[-1500:]
    (leg,) = _leg(r, 12, "box -> peer")
    assert leg.endswith(" | FAIL: row 12 box -> peer: PC-ANNOUNCE heard by the box (9999-12-31T23:59:59.500Z), yet the box's "
                        "node had no path to the PC (a path defect)"), leg
    ok = dry(tmp_path, LANE="full")
    assert _leg(ok, 12, "box -> peer")[0].endswith(" | DRY-PASS")
    assert "data: row 12: PC-ANNOUNCE heard by the box (9999-12-31T23:59:59.500Z)" in ok.stdout.splitlines()
    assert "data: row 5: PC-ANNOUNCE heard by the box (9999-12-31T23:59:59.500Z)" in ok.stdout.splitlines()


def test_a_delivered_box_send_passes_whatever_the_aired_check_found(tmp_path):
    # G2 280 B2, the rule (handler: option b): a delivered box -> peer send proves the box's path to the PC by itself; the
    # aired check is the NO-PATH diagnostic only, recorded here as a data line
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE="full", DRY_PCANNMISS="12")
    assert r.returncode == 0, r.stderr[-1500:]
    assert _leg(r, 12, "box -> peer")[0].endswith(" | DRY-PASS")
    assert "data: row 12: PC-ANNOUNCE NOT heard by the box (9999-12-31T23:59:59.500Z)" in r.stdout.splitlines()


# ---- R0's prerequisite of the Reticulum rows: MeshChat's identity on each of their boxes ----------------------------------

NOMCID = "MeshChat has no identity yet (no state/meshchat/identity: it never started there); prerequisite: start MeshChat on it"


@pytest.mark.parametrize("lane, box, off, on", [("rns", "H", [13], []), ("rns", "Z", [13], []), ("full", "H", [12], [5]),
                                                ("full", "Z", [5], [12]), ("rns", "", [], [13])])
def test_r0_marks_a_reticulum_row_not_run_when_its_box_has_no_meshchat_identity(tmp_path, lane, box, off, on):
    # red-before (e14cf5be, R-P6 run 3): no such read; row 13 read H's identity before MeshChat's start and stopped on
    # "no identity" where MeshChat had never run
    from test_rf_proof_guards import dry
    r = dry(tmp_path, LANE=lane, DRY_NOMCID=box)
    assert r.returncode == 0, r.stderr[-1500:]
    lines = r.stdout.splitlines()
    r0 = r.stdout.split("== ROW R0", 1)[1].split("== ROW R1", 1)[0].splitlines()
    reads = sorted(ln.split(":")[0].strip() for ln in r0 if "-mcid.txt: " in ln)
    assert reads == ["[dry] H > R0-H-mcid.txt", "[dry] Z > R0-Z-mcid.txt"], reads  # each Reticulum box once (row 13: H twice)
    for row in off:
        legs = [ln for ln in lines if ln.startswith(f"REPORT | {row} | ")]
        assert legs and all(ln.endswith(f" | NOT RUN: box {box}: {NOMCID} once before the run (the row reads its LXMF "
                                         "address before its start)") for ln in legs), legs
        assert not any(f"> {row}-" in ln for ln in lines), row                     # the row ran no box command
    for row in on:
        assert any(ln.startswith(f"REPORT | {row} | ") and ln.endswith(" | DRY-PASS") for ln in lines), row


def test_the_meshchat_identity_read_needs_a_non_empty_file(tmp_path):
    root = tmp_path / "rt"
    mc = root / "state" / "meshchat"
    mc.mkdir(parents=True)
    cmd = _sourced(tmp_path, 'printf "%s" "$MCID"').stdout.replace("{RT}", str(root))
    got = []
    for content in (None, b"", b"\x01" * 64):
        if content is not None:
            (mc / "identity").write_bytes(content)
        r = subprocess.run(["bash", "-c", cmd], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
        got.append((r.returncode, r.stdout))
    assert got == [(0, "MCID none\n"), (0, "MCID none\n"), (0, "MCID yes\n")]
