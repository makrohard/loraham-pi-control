"""testlab/hardware/rf-proof.sh in dry mode against the example file's simulated bench, read through its output lines only
(docs/live-tests/hardware-proof.md): both lanes pass
their self-checks with the expected coverage; absent or ambiguous hardware turns exactly its rows NOT RUN and changes nothing
else; an incompatible radio is a FAIL; every frame on air is covered by its own `tx` call; the lhpc calls parse at this head;
and no secret reaches the evidence."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field

import pytest

KIT = pathlib.Path(__file__).resolve().parents[2] / "hardware"
RUNNER = KIT / "rf-proof.sh"


def fields(line):
    return [f.strip() for f in line.split("|")[1:]]


@dataclass
class Dry:
    rc: int
    out: str
    err: str
    lines: list = field(default_factory=list)

    def rows(self, kind):
        """The `<kind> | ...` lines (REPORT, COVERAGE, TX, BUDGET, FRAME) as field lists, in order."""
        return [fields(line) for line in self.lines if line.startswith(f"{kind} |")]

    def report(self):
        """{(row id, direction): result} of the REPORT lines (fields: id stack box band peer direction seen power result);
        a row and direction reported twice is an error, never a silent overwrite."""
        rows = [((r[0], r[5]), r[8]) for r in self.rows("REPORT")]
        assert len(rows) == len(dict(rows)), f"a row and direction reported twice: {rows}"
        return dict(rows)


def table_rows(script=RUNNER):
    """The ROWS table of rf-proof.sh: (id, lane, kind, A, B, power, flags) per line, in table order."""
    text = pathlib.Path(script).read_text()
    body = re.search(r"^ROWS=\((.*?)^\)", text, re.MULTILINE | re.DOTALL).group(1)
    return [tuple(m.split()) for m in re.findall(r'"([^"]+)"', body)]


def run_dry(tmp, lane, script, env):
    """One dry run of `script` with the example file's inventory, `env` overriding its keys; TMPDIR and the working
    directory are the empty `tmp`, which must be empty again afterwards (the release file's scratch is removed)."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("DRY_", "BOX_", "RADIO_", "PEER_", "LANE", "ROWS_ONLY"))}
    e.update(TMPDIR=str(tmp), LANE=lane, **env)
    p = subprocess.run(["bash", str(script), "dry"], capture_output=True, text=True, stdin=subprocess.DEVNULL, env=e,
                       cwd=tmp, check=False, timeout=300)
    assert list(tmp.iterdir()) == [], "dry left files behind"
    return Dry(p.returncode, p.stdout, p.stderr, p.stdout.splitlines())


@pytest.fixture
def dry(tmp_path):
    """dry(lane, script=RUNNER, **env): run_dry in a fresh directory of this test."""
    def run(lane="full", script=RUNNER, **env):
        tmp = tmp_path / f"dry-{len(list(tmp_path.glob('dry-*')))}"
        tmp.mkdir()
        return run_dry(tmp, lane, script, env)
    return run


@pytest.fixture
def runner_copy(tmp_path):
    """runner_copy(name, edit) -> rf-proof.sh of a copy of the kit whose file `name` is changed by `edit(text) -> text` (a
    planted defect; `edit=None` leaves the copy unchanged). The copy sits at `<tmp>/r/testlab/hardware` beside a link
    `<tmp>/r/lhpc` to this checkout's package, so its REPO is a repo and its dry run reaches the rows."""
    def make(name, edit):
        root = tmp_path / f"r{len(list(tmp_path.glob('r*')))}"
        kit = root / "testlab" / "hardware"
        shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns("*.local.env", "__pycache__"))
        (root / "lhpc").symlink_to(KIT.parents[1] / "lhpc")
        if edit is not None:
            text = (kit / name).read_text()
            changed = edit(text)
            assert changed != text, "the planted edit changed nothing"
            (kit / name).write_text(changed)
        return kit / "rf-proof.sh"
    return make


SELF_CHECKS = ("no-high-power", "no-call-in-commands", "peer-calls-locked", "every-send-gated", "all-forms-printed",
               "chat-judge", "provisioning-wait", "meshcore-power-read", "rx-mc-judge", "row3-port-judge", "state-helpers",
               "power-guards", "tx-dispatch")
MODELS_IN_LAB = {"loraham", "meshstick-1262", "pinedio-usb-1262", "ch341-rfm98pw-433"}
MODELS_NOT_IN_LAB = {"uputronics", "waveshare-sx1262"}
TXMAX = 10
TX_CALL = re.compile(r"\btx \S+ \d+ \S+ -- ")
# The send forms as the runner uses them: a forms-table send, a helper on a box's stdin, a peer action, the chat keys and the
# announce. Inside a `case` pattern list (the dry executor's own detector) or a quoted assignment that expands nothing (a
# pattern held as data) a form is text, not a send.
SEND = re.compile(r"\bform (?:tx_text|meshcom_send|mt_send|mc_send|rt_send)\b|\bXIN\b.*\$\{?(?:MCSEND_PY|GW_PY)\b"
                  r"|\b(?:meshcom|mt|mc)-send\b|\brns-exchange\b|\btmux send-keys\b|\bcurl\b.*announce")
CASE_PATTERNS = re.compile(r"^\s*case \S+ in ")
# Data: a single-quoted value, or a double-quoted one with no $, backtick or backslash (nothing in it expands or runs). The
# runner's runtime guard still STOPs such a value when it is executed outside tx (test below).
QUOTED_ASSIGNMENT = re.compile(r"""^\s*[A-Za-z_]\w*=(?:'[^']*'|"[^"$`\\]*")\s*$""")


def lane_rows(lane):
    return [r for r in table_rows() if lane == "full" or r[1] == "fast"]


def rows_using(lane, endpoint):
    """The ids of the lane's rows with the endpoint `endpoint` (`H:HMS`, `peer:tdpro`), or with any endpoint on a box (`Z:`)."""
    on_box = endpoint.endswith(":")
    return {r[0] for r in lane_rows(lane) for e in r[3:5] if e == endpoint or (on_box and e.startswith(endpoint))}


def uncovered(lines):
    """The FRAME lines no earlier TX line covers: each TX gives <frames> credits to its radio and class, each FRAME takes one
    from the oldest TX of its radio and class that has one left. A FRAME of `<box>:*` (no rid in its command) takes one from
    any radio of that box."""
    credit, bad = [], []
    for line in lines:
        f = [x.strip() for x in line.split("|")[1:]]
        if line.startswith("TX |"):
            credit.append([f[0], f[2], int(f[1])])
        elif line.startswith("FRAME |"):
            box, _, rid = f[0].partition(":")
            same = [c for c in credit if c[0] == f[0] or (rid == "*" and c[0].startswith(box + ":"))]
            c = next((c for c in same if c[1] == f[1] and c[2] > 0), None)
            if c is None:
                bad.append(line)
            else:
                c[2] -= 1
    return bad


def unsent_tx(lines):
    """The TX lines with an executor that are not directly followed by that executor's `[dry]` command line."""
    return [a for a, b in zip(lines, [*lines[1:], ""], strict=True)
            if a.startswith("TX |") and not a.rstrip().endswith("| reserve") and not b.lstrip().startswith("[dry] ")]


def bypasses(text):
    """The lines of rf-proof.sh that use a send form outside the arguments of a `tx` call (comments aside)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#") or CASE_PATTERNS.match(line) or QUOTED_ASSIGNMENT.match(line):
            continue
        for m in SEND.finditer(line):
            if not TX_CALL.search(line[:m.start()]):
                out.append(f"{n}: {line.strip()}")
    return out


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """The full lane on the whole example bench, once for the module."""
    return run_dry(tmp_path_factory.mktemp("base"), "full", RUNNER, {})


# ---- the dry run, both lanes ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lane", ["fast", "full"])
def test_a_dry_lane_passes_every_self_check(dry, lane):
    d = dry(lane)
    assert d.rc == 0, d.err
    ok = [line.split()[-1] for line in d.lines if line.startswith("dry self-check OK ")]
    assert sorted(ok) == sorted(SELF_CHECKS) and f"dry self-check: all {len(SELF_CHECKS)} OK" in d.lines


@pytest.mark.parametrize("lane", ["fast", "full"])
def test_a_dry_lane_runs_its_rows_and_covers_every_model(dry, lane):
    d = dry(lane)
    ran = {r[0] for r in d.rows("REPORT")}
    assert ran == {r[0] for r in lane_rows(lane)}
    # dry never says PASS or proven: a dry output is never mistaken for an on-air proof
    assert {r for r in d.report().values() if r.partition(":")[0] != "NOT RUN"} == {"DRY-PASS"}
    assert "DRY-PASS" in d.report().values()
    models = {r[0]: r[4] for r in d.rows("COVERAGE") if r[1] == "model"}
    assert {m for m, res in models.items() if res == "rehearsed"} == MODELS_IN_LAB
    assert {m for m, res in models.items() if res == "not present"} == MODELS_NOT_IN_LAB
    if lane == "full":
        # per stack, row and direction: rehearsed where the row ran, NOT RUN where its REPORT says so
        stacks = {(r[2], r[3]): r[4] for r in d.rows("COVERAGE") if r[1] == "stack"}
        report = d.report()
        assert stacks and stacks == {k: "rehearsed" if report[k] == "DRY-PASS" else "NOT RUN" for k in stacks}
        assert ("stop-isolation", "rehearsed") in {(r[0], r[4]) for r in d.rows("COVERAGE") if r[1] == "check"}


def test_the_whole_bench_has_no_absent_or_ambiguous_row(base):
    assert base.rc == 0, base.err
    assert not [r for r in base.report().values() if "absent:" in r or "ambiguous:" in r]


# ---- hardware that is not there ------------------------------------------------------------------------------------------

def no_stick_no_ble(env):
    radios = "\n".join(line for line in env["DRY_RADIOS_H"].splitlines() if "meshstick-1262" not in line)
    return {"DRY_RADIOS_H": radios, "DRY_BLE": ""}


def example(key):
    text = (KIT / "lab.example.env").read_text()
    m = re.search(rf"^{key}='(.*?)'", text, re.MULTILINE | re.DOTALL) or re.search(rf'^{key}="(.*?)"', text, re.MULTILINE)
    return m.group(1)


@pytest.mark.parametrize("case", ["a stick and the BLE peer removed", "box Z removed", "two radios match one key"])
def test_absent_hardware_turns_exactly_its_rows_not_run(dry, base, case):
    if case == "a stick and the BLE peer removed":
        env = no_stick_no_ble({"DRY_RADIOS_H": example("DRY_RADIOS_H")})
        want = dict.fromkeys(rows_using("full", "H:HMS"), "absent: radio HMS")
        want |= dict.fromkeys(rows_using("full", "peer:tdpro"), "absent: peer tdpro")
    elif case == "box Z removed":
        env = {"BOX_Z_HOST": ""}
        want = dict.fromkeys(rows_using("full", "Z:"), "absent: box Z")
    else:
        env = {"DRY_RADIOS_H": example("DRY_RADIOS_H") + "\nROW r6 loraham 433 True serial= port= hp=None"}
        want = dict.fromkeys(rows_using("full", "H:H433"), "ambiguous: radio H433 (2 match)")
    d = dry("full", **env)
    assert d.rc == 0, d.err
    got, before = d.report(), base.report()
    assert want and set(got) == set(before)                     # every row and direction is still reported
    assert {k: r for k, r in got.items() if k[0] in want} == {k: f"NOT RUN: {want[k[0]]}" for k in got if k[0] in want}
    assert {k: r for k, r in got.items() if k[0] not in want} == {k: r for k, r in before.items() if k[0] not in want}


def test_a_frequency_mismatch_on_a_frame_row_is_a_fail(dry, base):
    row = next(r for r in lane_rows("fast") if r[3] == "H:H433" and r[4] == "Z:Z433")[0]
    d = dry("fast", DRY_STATUS_Z_r1="STATUS ID=r1 FREQ=434.500000 SF=11 POWER=10 HIGHPOWER=0")
    assert d.rc == 0, d.err                                     # dry exits 0 or 2 only; the row says FAIL
    results = {k: r for k, r in d.report().items() if k[0] == row}
    assert results and all(re.fullmatch(r"FAIL: incompatible: FREQ \S+ vs \S+", r) for r in results.values())
    assert {k: r for k, r in d.report().items() if k[0] != row} == {k: r for k, r in base.report().items()
                                                                    if k[0] != row and k[0] in {x[0] for x in lane_rows("fast")}}


# ---- every frame on air goes through tx ----------------------------------------------------------------------------------

def test_every_frame_of_the_dry_run_is_covered_by_its_own_tx(base):
    frames = [line for line in base.lines if line.startswith("FRAME |")]
    assert uncovered(base.lines) == [] and unsent_tx(base.lines) == []
    kinds = Counter((f.split("|")[1].strip(), f.split("|")[2].strip() == "control") for f in frames)
    assert kinds[("PC:rnode", True)] == 2 and kinds[("PC:rnode", False)] == 1       # the announce, the path request; the message
    assert kinds[("PC:tracker", True)] == 1                                          # the tracker's ACK
    assert any(r.partition(":")[0] == "Z" and c for r, c in kinds)                  # the box's LXMF delivery proof


def test_every_radio_stays_within_its_budget(base):
    budget = {}
    for r in base.rows("BUDGET"):
        budget.setdefault(r[0], (int(r[1]), int(r[2])))
    used = Counter()
    for r in base.rows("TX"):
        used[r[0]] += int(r[1])
    assert used and all(r in budget for r in used)
    assert all(used[r] <= budget[r][0] <= budget[r][1] == TXMAX for r in used)


@pytest.mark.parametrize("lines,bad", [
    (["TX | H:r1 | 1 | n1 | must x.txt", "FRAME | H:r1 | n1"], 0),
    (["TX | H:r1 | 1 | n1 | must x.txt", "FRAME | H:r1 | n1", "FRAME | H:r1 | n1"], 1),     # two frames under a 1-frame tx
    (["FRAME | H:r1 | n1", "TX | H:r1 | 1 | n1 | must x.txt"], 1),                         # the frame before its tx
    (["TX | H:r1 | 1 | n1 | must x.txt", "FRAME | Z:r1 | n1"], 1),                         # another radio
    (["TX | PC:rnode | 1 | control | reserve", "FRAME | PC:rnode | n1"], 1),               # a message under a reservation
    (["TX | PC:rnode | 2 | control | peer 5.txt", "FRAME | PC:rnode | control", "FRAME | PC:rnode | control"], 0),
    (["TX | Z:r2 | 1 | n1 | must x.txt", "FRAME | Z:* | n1"], 0),                          # no rid in the command: the box
    (["TX | Z:r2 | 1 | n1 | must x.txt", "FRAME | H:* | n1"], 1),
])
def test_the_coverage_check_counts_frames_per_radio_and_class(lines, bad):
    assert len(uncovered(lines)) == bad


def test_a_peer_that_sends_twice_under_one_tx_fails_the_dispatch_check(dry, tmp_path):
    planted = tmp_path / "fake_peer.py"
    shutil.copy(KIT / "peer.py", tmp_path / "peer.py")           # the fake reads its action table beside it
    text = (KIT / "fake_peer.py").read_text()
    planted.write_text(text.replace("        frame(peer, a[1])\n", "        frame(peer, a[1])\n        frame(peer, a[1])\n", 1))
    assert planted.read_text() != text
    d = dry("full", FAKE_PEER=str(planted))
    # both detectors fire: the runner's own self-check STOPs (rc 2), and this test's credit count finds the frame
    assert d.rc == 2 and "without a TX credit" in d.err
    assert [f for f in uncovered(d.lines) if "PC:" in f]


def test_a_send_moved_out_of_its_tx_fails_the_dispatch_check_and_the_lint(dry, runner_copy):
    assert dry("full", script=runner_copy("rf-proof.sh", None)).rc == 0, "the unchanged copy must run through"
    script = runner_copy("rf-proof.sh", lambda t: TX_CALL.sub("", t, count=1))
    assert bypasses(script.read_text())
    d = dry("full", script=script)
    assert d.rc == 2 and "a send outside tx: never run" in d.err


@pytest.mark.parametrize("line,hit", [("PAT='tmux send-keys'", False), ("PAT='$(tmux send-keys -t x n1)'", False),
                                      ("PAT=\"tmux send-keys -t x n1\"", False), ("PAT=\"tmux send-keys $n\"", True),
                                      ("c=\"$(tmux send-keys -t x n1)\"", True), ("c=\"`tmux send-keys -t x n1`\"", True),
                                      ("    tx H:H433 1 n1 -- must H f.txt \"tmux send-keys -t x n1\"", False)])
def test_the_lint_reads_a_quoted_pattern_as_data_but_a_command_substitution_as_a_send(line, hit):
    assert bool(bypasses(line)) is hit


def test_no_send_form_is_used_outside_a_tx_call():
    assert bypasses(RUNNER.read_text()) == []


# ---- the lhpc calls parse at this head -----------------------------------------------------------------------------------

# Forms dry prints although this head's CLI has no verb for them (live reports their direction NOT RUN, "form not
# confirmed"): rt_send, the MeshChat send of row 5's box -> peer.
KNOWN_ABSENT = {"rt_send": re.compile(r"lhpc meshchat@r\d+ send ")}


def test_every_lhpc_call_of_the_dry_run_parses_at_this_head(base, tmp_path):
    out = tmp_path / "full.dry.txt"
    out.write_text(base.out)
    repo = KIT.parents[1]
    p = subprocess.run([sys.executable, "-I", str(KIT / "cliforms.py"), str(out)], capture_output=True, text=True,
                       cwd=repo, check=True, timeout=120)
    rows = [line.split("\t") for line in p.stdout.splitlines()[1:]]
    known = [r for r in rows if any(c.match(r[5]) for c in KNOWN_ABSENT.values())]
    assert rows and [r for r in rows if r[4] != "OK" and r not in known] == []
    # a known-absent form must still be absent: once the CLI has the verb, drop the entry and confirm the form
    assert known and {r[4] for r in known} == {"UNKNOWN"}


# ---- no secret in the evidence -------------------------------------------------------------------------------------------

SECRETS = {"PW_H": "pw-h-Wq81", "PW_Z": "pw-z-Kx27", "MC_PIN": "482913"}
CALL = "XX9TST"
FAKE_ECHO = f"""#!/bin/bash
echo "argv $*"; echo "env $SSHPASS"; cat 2>/dev/null; echo "{CALL}-7 {CALL.lower()}-12 {CALL} {SECRETS['PW_H']} {SECRETS['PW_Z']}"
"""
FAKE_PEER = f"""import sys
print("stdin", sys.stdin.read().strip(), "{CALL}-9", "{CALL.lower()}-15", "{SECRETS['PW_H']}")
print("SEEN n1 2026-01-01T00:00:00Z")
"""


def test_no_password_pin_or_call_reaches_the_evidence(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("ssh", "sshpass"):
        (bin_ / name).write_text(FAKE_ECHO)
        (bin_ / name).chmod(0o755)
    (tmp_path / "peer.py").write_text(FAKE_PEER)
    evd = tmp_path / "evd"
    evd.mkdir()
    script = f"""set -u
RFPROOF_SOURCE_ONLY=1 . "{RUNNER}"
MODE=live; EVD="{evd}"; PW_H="{SECRETS['PW_H']}"; PW_Z="{SECRETS['PW_Z']}"; MC_PIN="{SECRETS['MC_PIN']}"
OPERATOR_CALL="{CALL}"; PEER="{tmp_path / 'peer.py'}"
X H x.txt "echo hello"
XIN Z xin.txt "{SECRETS['PW_Z']}" "cat"
peer p.txt "{sys.executable}" meshcom-read n1 5
peerpin pin.txt mc-read n1 5
"""
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
                       env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}, timeout=60)
    files = sorted(f for f in evd.rglob("*") if f.is_file())
    assert {f.name for f in files} >= {"x.txt", "xin.txt", "p.txt", "pin.txt"}, p.stderr
    text = "\n".join(f.read_text(errors="replace") for f in files)
    assert "hello" in text and "SEEN n1" in text                         # the captures ran, so the greps below mean something
    leaks = [s for s in SECRETS.values() if s in text] + re.findall(CALL, text, re.IGNORECASE)
    assert leaks == []


@pytest.mark.parametrize("call", ['X H p.txt "lhpc config meshtastic@r2 tx_power 10"',
                                  'XIN Z p.txt "10" "lhpc daemon@r1 settx"',
                                  'peer p.txt python3 mt-power 1'])
def test_a_raw_power_command_stops_before_anything_runs(tmp_path, call):
    # Power is written only by pset and read only by pread: a raw power command from a test (or a row) is a STOP, and
    # the fake ssh and the peer never run.
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("ssh", "sshpass"):
        (bin_ / name).write_text(f"#!/bin/bash\ntouch {tmp_path}/ran\n")
        (bin_ / name).chmod(0o755)
    evd = tmp_path / "evd"
    evd.mkdir()
    script = (f'set -u\nRFPROOF_SOURCE_ONLY=1 . "{RUNNER}"\nMODE=live; EVD="{evd}"; PW_H=pw-h; PW_Z=pw-z; MC_PIN=1; '
              f'OPERATOR_CALL=XX9TST; PEER="{tmp_path}/peer.py"\n{call}\necho RAN-ON')
    (tmp_path / "peer.py").write_text(f"open('{tmp_path}/ran', 'w')\n")
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
                       env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}, timeout=60)
    assert p.returncode == 2 and "power token" in p.stderr and "RAN-ON" not in p.stdout
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("call", ['X H s.txt "$PAT"', 'eval "XIN Z s.txt - \\"$PAT\\""'])
def test_a_send_held_as_data_still_stops_when_it_runs_outside_tx(tmp_path, call):
    # The lint reads PAT="tmux send-keys ..." as data only because the runtime guard backs it: run outside tx, it STOPs
    # before the fake ssh runs.
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("ssh", "sshpass"):
        (bin_ / name).write_text(f"#!/bin/bash\ntouch {tmp_path}/ran\n")
        (bin_ / name).chmod(0o755)
    evd = tmp_path / "evd"
    evd.mkdir()
    script = (f'set -u\nRFPROOF_SOURCE_ONLY=1 . "{RUNNER}"\nMODE=live; EVD="{evd}"; PW_H=pw-h; PW_Z=pw-z; MC_PIN=1; '
              f'OPERATOR_CALL=XX9TST\nPAT="tmux send-keys -t rfchat n1"\n{call}\necho RAN-ON')
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
                       env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}, timeout=60)
    assert p.returncode == 2 and "a send outside tx" in p.stderr and "RAN-ON" not in p.stdout
    assert not (tmp_path / "ran").exists()

