"""The release lane's attribution contract, proved where the automation reads it: the required
case list, the `STACK-REGRESSION` marker grammar, JUnit attribution through the REAL release
helpers with the executable stubbed, the between-case cleanup fixture and its clean-lab proof, the run-all invocation, and
the one rule the lane shares with the binary builder."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# --- the release lane's required cases --------------------------------------------------


def test_a_missing_required_release_case_is_detected():
    """The automated release requires NAMES, not a count. It used to accept any fourteen
    passing cases called `test_release_*`, so dropping or renaming one — the MeshCom case here
    — kept the count intact while that stack stopped being proved."""
    from lhpc_testlab.release import missing_required_cases, required_release_cases

    required = list(required_release_cases())
    assert "test_release_meshcom" in required, "the list no longer names the MeshCom case"
    assert missing_required_cases(required) == []
    reported = [n for n in required if n != "test_release_meshcom"]
    assert missing_required_cases([*reported, "test_release_meshcom_renamed"]) == [
        "test_release_meshcom"]


def test_the_required_cases_are_the_names_the_release_lane_defines():
    """The list and the lane are one thing.

    The lane's own first case asserts this same equality, but only when the lane RUNS — which
    is during a release, an hour of installs later. This is its cheap twin, so a rename is
    caught in CI instead. It reads the file rather than importing it because a test module may
    not import a sibling test module, and the names are the contract the release automation
    consumes.
    """
    import ast

    from lhpc_testlab.release import required_release_cases

    lane = Path(__file__).resolve().parents[1] / "release" / "test_release_verify.py"
    tree = ast.parse(lane.read_text())
    defined = {n.name for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("test_release_")}
    assert defined == set(required_release_cases())


# --- the stack-regression marker ---------------------------------------------------------

# The grammar exactly as `lhpc_testlab.release.stack_regression` documents it, written out here
# the way the release automation's own parser has to write it — a program in another repository
# reads this line out of the lane's JUnit and freezes the stack it names.
STACK_REGRESSION_LINE = re.compile(
    r"^STACK-REGRESSION stack=([a-z0-9][a-z0-9_-]*) phase=(install|build|start|readiness)$")
# The same grammar as the automation must SEARCH for it in a JUnit `<failure>`: pytest puts
# `E   ` in front of an explanation line and `AssertionError: ` in front of the message, so the
# start of the line is not the start of the marker. Nothing follows the phase on its line.
STACK_REGRESSION_IN_TEXT = re.compile(
    r"STACK-REGRESSION stack=([a-z0-9][a-z0-9_-]*) "
    r"phase=(install|build|start|readiness)(?=\s|$)")


def test_the_stack_regression_marker_is_the_documented_line():
    """One line, three fields, parseable by the published regex — the marker IS the contract."""
    from lhpc_testlab.release import stack_regression

    assert stack_regression("kiss", "readiness") == \
        "STACK-REGRESSION stack=kiss phase=readiness"
    m = STACK_REGRESSION_LINE.match(stack_regression("meshcore", "build"))
    assert m and m.group(1) == "meshcore" and m.group(2) == "build"


@pytest.mark.parametrize("stack,phase", [
    ("kiss", "shutdown"),        # a phase the parser does not know
    ("kiss", "Install"),         # the phases are lower-case
    ("MeshCore", "start"),       # a stack id is lower-case
    ("meshcore cli", "start"),   # a space would end the field early
    ("", "start"),
])
def test_the_marker_refuses_what_the_grammar_does_not_cover(stack, phase):
    """It raises instead of emitting a line the automation would silently fail to match — a
    marker that does not parse is worse than none, because the failure looks attributed."""
    from lhpc_testlab.release import stack_regression

    with pytest.raises(ValueError):
        stack_regression(stack, phase)


def _junit_entries(tmp_path, lane: str, *extra_args) -> list:
    """Run `lane` as its own pytest process and return every reported entry as
    `(case name, "failure"|"error", text)` — message attribute and body joined, exactly what the
    release automation searches. A case that FAILS and whose teardown then ERRORS is two entries
    under one name, which is what pytest hands the automation."""
    import xml.etree.ElementTree as ET

    (tmp_path / "test_marker_lane.py").write_text(lane)
    xml = tmp_path / "junit.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    *extra_args, "test_marker_lane.py", f"--junitxml={xml}"],
                   cwd=tmp_path, capture_output=True, text=True, timeout=300, check=False)
    out = []
    # Untrusted only in the abstract: this XML is the file pytest just wrote here.
    for case in ET.parse(xml).getroot().iter("testcase"):  # noqa: S314
        for kind in ("failure", "error"):
            for f in case.iter(kind):
                out.append((case.get("name"), kind,
                            (f.get("message") or "") + "\n" + (f.text or "")))
    return out


def _junit_failures(tmp_path, lane: str, *extra_args) -> dict:
    """{case name: its JUnit failure text}, for lanes that report one entry per case."""
    return {n: text for n, kind, text in _junit_entries(tmp_path, lane, *extra_args)
            if kind == "failure"}


# Each stubbed `lhpc` run is as long as the real thing's output, so a marker is read out of a
# failure the size the automation actually meets. The MeshCore cases run against the REAL recipe:
# its steps, its declarations, its log names.
_MARKER_LANE = """
import lhpc_testlab.release as rel
from lhpc.core.manifest import default_manifest_path, load_manifest

class _R:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout, self.stderr = rc, out + "boom\\n" * 400, "boom\\n" * 400

STACKS = {s.id: s for s in load_manifest(default_manifest_path())}

class SVC:
    def stack(self, sid):
        return STACKS[sid]

# A REAL MeshCore recipe carrying both shapes: a step that reaches the network (pip) and a step
# the recipe declares its own (`attributable`). LHPC types both failures identically, and the log
# name is what tells them apart.
#
# The component is RESOLVED, not named: meshcore-node used to be it and stopped being one when
# LHPC dropped its openhop_core patch, which was the node's only attributable step. Picking
# whichever MeshCore component has both shapes today keeps the RULE under test, not the example.
NODE = next(c for c in STACKS["meshcore"].components
            if c.build_steps
            and any(s.get("attributable") for s in c.build_steps)
            and any(str(s["argv"][0]).endswith("pip") and "install" in s["argv"]
                    for s in c.build_steps))
NET_STEP = next(i for i, s in enumerate(NODE.build_steps)
                if str(s["argv"][0]).endswith("pip") and "install" in s["argv"])
OWN_STEP = next(i for i, s in enumerate(NODE.build_steps) if s.get("attributable"))

def typed(step):
    return (f"  [failed] build {NODE.id} (rc 1, log /x/logs/build-{NODE.id}-{step}.log)"
            "\\n")

# What pip prints when the package index is unreachable — the failure this rule exists to keep
# off a stack's pins.
PIP_DNS = ("WARNING: Retrying (Retry(total=0)) after connection broken by "
           "'NewConnectionError(...: Temporary failure in name resolution)': /simple/pynacl/\\n"
           "ERROR: Could not find a version that satisfies the requirement pynacl\\n")

# What `lhpc build` prints when LHPC ITSELF typed a component's build as failed, and the two
# shapes that are NOT that: a build the runner ran out of time on, and a refusal that executed
# no build step at all.
TYPED = "  [failed] build loraham-kiss-tnc (rc 1, log /x/logs/build-loraham-kiss-tnc.log)\\n"
TIMED_OUT = "  [timeout] build loraham-kiss-tnc (rc 124, log /x/logs/build-x.log)\\n"
REFUSED = "ERR   Refusing to build 'kiss': loraham-kiss-tnc is not installed.\\n"
CLONE = "  [failed] clone loraham-kiss-tnc — fatal: unable to access: Could not resolve host\\n"
# Every command succeeded and the COMPLETION MARKER write failed — a full disk, not the recipe.
# LHPC types it as a failed build with NO log identity, precisely so it cannot be attributed.
MARKER_IO = "  [failed] build loraham-kiss-tnc (rc 1, log )\\n"

def _script(*results):
    it = iter(results)
    rel.run_lhpc = lambda env, *a, **kw: next(it, results[-1])   # a repeat sees the last again

import pytest

@pytest.fixture(autouse=True)
def _one_retry_no_wait(monkeypatch):
    monkeypatch.setattr(rel, "TRANSFER_WAITS", (0,))
    monkeypatch.setattr(rel, "TRANSFER_TOTAL", 0)

ENV = {'LHPC_RUNTIME_ROOT': '/nonexistent'}

def test_a_build_lhpc_typed_as_the_components_own():
    _script(_R(0), _R(1, TYPED))
    rel.install_build(ENV, SVC(), 'kiss')

def test_a_build_that_ran_out_of_time():
    _script(_R(0), _R(1, TIMED_OUT))
    rel.install_build(ENV, SVC(), 'kiss')

def test_a_build_refused_before_a_step_ran():
    _script(_R(0), _R(1, REFUSED))
    rel.install_build(ENV, SVC(), 'kiss')

def test_an_install_that_could_not_reach_the_remote():
    _script(_R(1, CLONE))
    rel.install_build(ENV, SVC(), 'kiss')

def test_a_build_whose_only_failure_was_writing_the_completion_marker():
    _script(_R(0), _R(1, MARKER_IO))
    rel.install_build(ENV, SVC(), 'kiss')

def test_a_package_network_failure_during_a_build_step():
    _script(_R(0), _R(1, PIP_DNS + typed(NET_STEP)))
    rel.install_build(ENV, SVC(), 'meshcore')

def test_a_build_step_the_recipe_declares_its_own():
    _script(_R(0), _R(1, "error: patch does not apply\\n" + typed(OWN_STEP)))
    rel.install_build(ENV, SVC(), 'meshcore')

def test_a_prerequisite_another_case_should_have_left_running():
    _script(_R(0, "[kiss] LoRaHAM KISS TNC  (stopped)\\n"))
    rel.require_prerequisite(ENV, 'kiss', left_by='test_release_kiss')

def test_stopping_another_stack():
    _script(_R(1))
    rel.stop(ENV, 'graywolf')
"""


@pytest.fixture(scope="module")
def marker_lane_failures(tmp_path_factory) -> dict:
    """The marker lane run ONCE for this module: {case name: its JUnit failure text}."""
    return _junit_failures(tmp_path_factory.mktemp("marker-lane"), _MARKER_LANE)


def test_only_a_build_step_the_recipe_declares_its_own_is_attributable(marker_lane_failures):
    """THE attribution rule, proved where the automation reads it: the lane's JUnit.

    Every case here fails through the REAL release helpers with the executable stubbed out. Only
    one of them is this stack's own regression — a build LHPC typed as failed AT A STEP THE
    RECIPE DECLARES ITS OWN — and only that one may carry a marker, because a marker freezes the
    stack's pins until someone lifts it.

    The MeshCore pair is the counterexample that made the rule: its real recipe installs openHop's
    dependency closure with pip, so an unreachable package index produces exactly the same typed
    `[failed] build …` line as a patch that no longer applies. Attributing on the typed line alone
    froze all four MeshCore pins over a name-resolution failure. An install that could not reach
    the remote, a build the runner ran out of time on, a refusal that executed no build step, a
    prerequisite an earlier case should have left running and a stop of ANOTHER stack are the same
    kind of "may equally be the environment" failure, and carry none either: an unattributed
    failure is reported as an ordinary failure and freezes nothing.
    """
    failures = marker_lane_failures
    attributable = {"test_a_build_lhpc_typed_as_the_components_own",
                    "test_a_build_step_the_recipe_declares_its_own"}
    assert set(failures) == attributable | {
        "test_a_build_that_ran_out_of_time",
        "test_a_build_refused_before_a_step_ran",
        "test_an_install_that_could_not_reach_the_remote",
        "test_a_package_network_failure_during_a_build_step",
        "test_a_build_whose_only_failure_was_writing_the_completion_marker",
        "test_a_prerequisite_another_case_should_have_left_running",
        "test_stopping_another_stack"}, failures

    for name, stack in (("test_a_build_lhpc_typed_as_the_components_own", "kiss"),
                        ("test_a_build_step_the_recipe_declares_its_own", "meshcore")):
        marked = failures[name]
        hits = STACK_REGRESSION_IN_TEXT.findall(marked)
        assert set(hits) == {(stack, "build")}, f"unattributable JUnit failure:\n{marked}"
        # Every occurrence parsed: a marker the automation cannot read is worse than none.
        assert marked.count("STACK-REGRESSION") == len(hits)

    for name, text in failures.items():
        if name in attributable:
            continue
        assert "STACK-REGRESSION" not in text, f"{name} must not be attributable to a stack"


def test_the_prerequisite_failure_says_it_is_not_this_stacks_regression(marker_lane_failures):
    """A reader — and the maintainer triaging the run — has to see WHY it failed. An unmarked
    failure that merely said "graywolf's web UI never answered" looked exactly like graywolf
    breaking, when kiss had simply never come up."""
    text = marker_lane_failures["test_a_prerequisite_another_case_should_have_left_running"]
    assert "prerequisite not met: kiss is not running" in text
    assert "test_release_kiss" in text


# --- the lane releases what a FAILING case took -----------------------------------------

_CLEANUP_LANE = """
import json, pathlib
import lhpc_testlab.release as rel
import pytest

LOG = pathlib.Path("calls.json")
# What a FRESH lab root actually reports, and with the display names the product ships.
# Both details were wrong here before and each hid a defect: "stopped" hid a cleanup that fired
# for nothing, and a name with no parentheses hid a status parser that could not see three of
# the eight stacks at all.
STATE = {"kiss": "not-installed", "graywolf": "stopped",
         "reticulum": "not-installed", "meshcore": "not-installed"}
NAMES = {"kiss": "KISS TNC", "graywolf": "Graywolf",
         "reticulum": "Reticulum (RNS)", "meshcore": "MeshCore (OpenHop)"}

def _run(env, *a, **kw):
    if a[:1] == ("status",):
        out = "".join(f"[{s}] {NAMES[s]}  ({st})\\n" for s, st in STATE.items())
        out += "[controller] loraham-pi-control  (up to date)\\n"
    else:
        assert a[:2] == ("stack", "stop"), a
        STATE[a[2].partition("@")[0]] = "stopped"           # K3: the stop names its instance
        LOG.write_text(json.dumps(json.loads(LOG.read_text() or "[]") + [a[2].partition("@")[0]]))
        out = ""
    return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()

rel.run_lhpc = _run
LOG.write_text("[]")

@pytest.fixture
def env():
    return {}

def test_a_case_that_fails_after_starting_its_stack(env):
    STATE["kiss"] = "running"
    assert False, "the case aborted before its trailing stop()"

def test_a_passing_case_keeps_what_it_started_for_the_next_one(env):
    STATE["graywolf"] = "running"

def test_the_next_case_sees_what_was_left(env):
    assert STATE["kiss"] == "stopped", STATE
    assert STATE["graywolf"] == "running", STATE
"""


def test_a_failing_case_releases_its_stack_and_a_passing_one_does_not(tmp_path):
    """A case that aborts before its trailing `stop()` used to leave its stack holding the lab's
    one radio pair, so the NEXT case failed at its own MARKED readiness assertion and an
    automated release froze an innocent stack. A FAILING case now releases everything holding,
    because once it has failed the chain is broken and there is nothing left to preserve. A
    PASSING case is still left exactly as it is, which is what keeps the chaining intact.
    """
    import json

    failures = _junit_failures(tmp_path, _CLEANUP_LANE, "-p", "lhpc_testlab.release_lane")
    assert set(failures) == {"test_a_case_that_fails_after_starting_its_stack"}, failures
    assert json.loads((tmp_path / "calls.json").read_text()) == ["kiss"]


# A stop that cannot stop anything. One radio pair, two stacks that both want it.
_FAILFAST_LANE = """
import lhpc_testlab.release as rel
import pytest

STATE = {"kiss": "running", "reticulum": "stopped"}
NAMES = {"kiss": "KISS TNC", "reticulum": "Reticulum (RNS)"}

def _run(env, *a, **kw):
    if a[:1] == ("status",):
        out = "".join(f"[{s}] {NAMES[s]}  ({st})\\n" for s, st in STATE.items())
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    assert a[:2] == ("stack", "stop"), a
    # The stop FAILS: the process would not go away, so the band stays held.
    return type("R", (), {"returncode": 1, "stdout": "could not stop the process",
                          "stderr": ""})()

rel.run_lhpc = _run

@pytest.fixture
def env():
    return {}

def test_release_kiss(env):
    assert False, rel.stack_regression("kiss", "readiness") + "\\nkiss never served KISS/TCP"

def test_release_reticulum(env):
    STATE["reticulum"] = "running"
    assert False, rel.stack_regression("reticulum", "readiness") + "\\nrnstatus listed nothing"
"""


def _junit_outcomes(tmp_path, lane: str, *extra_args) -> dict:
    """{case name: [(outcome, text), ...]} — "failure", "error", "skipped" or "passed" — for a lane
    run as its own pytest process. A case that FAILS and whose teardown then ERRORS has both."""
    import xml.etree.ElementTree as ET

    (tmp_path / "test_marker_lane.py").write_text(lane)
    xml = tmp_path / "junit.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args,
                    "test_marker_lane.py", f"--junitxml={xml}"],
                   cwd=tmp_path, capture_output=True, text=True, timeout=300, check=False)
    out: dict = {}
    # Untrusted only in the abstract: this XML is the file pytest just wrote here.
    for case in ET.parse(xml).getroot().iter("testcase"):  # noqa: S314
        got = [(e.tag, (e.get("message") or "") + "\n" + (e.text or "")) for e in case
               if e.tag in ("failure", "error", "skipped")]
        out.setdefault(case.get("name"), []).extend(got or [("passed", "")])
    return out


def _unmarked(outcomes: dict) -> list:
    """The failing entries the consumer reads as UNATTRIBUTED (no marker): any one of them makes it
    refuse every hold in the run."""
    return [(n, k) for n, entries in outcomes.items() for k, t in entries
            if k in ("failure", "error") and not STACK_REGRESSION_IN_TEXT.search(t)]


def test_a_failed_cleanup_makes_the_whole_run_unattributable(tmp_path):
    """The lane runs every case now, so a cleanup that could not stop what the failing case held
    must make the RUN say so: the band stays held and the next stack's own MARKED failure would
    otherwise freeze an innocent stack. The failed stop is a teardown ERROR with no marker on the
    failing case, and the consumer attributes nothing in a run that carries one."""
    out = _junit_outcomes(tmp_path, _FAILFAST_LANE, "-p", "lhpc_testlab.release_lane")
    kiss = dict(out["test_release_kiss"])
    assert set(STACK_REGRESSION_IN_TEXT.findall(kiss["failure"])) == {("kiss", "readiness")}
    assert "STACK-REGRESSION" not in kiss["error"] and "could not release" in kiss["error"], kiss
    assert ("test_release_kiss", "error") in _unmarked(out)
    assert "test_release_reticulum" in out                # judged: the lane no longer stops


def test_the_release_verify_job_runs_the_lane_without_x():
    """Every case runs and every failure is reported; the attribution `-x` used to protect is now
    kept by `inherits` and the clean-lab proof, so a re-added `-x` would hide every failure after
    the first again."""
    import shlex

    wf = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "testlab.yml")
    # Backslash-continued commands are one invocation: join them before reading the argv, so a
    # reflow of the workflow's shell moves the flag between lines without moving it out of
    # the command.
    joined = wf.read_text().replace("\\\n", " ")
    invocations = [ln for ln in joined.splitlines() if "pytest testlab/tests/release" in ln]
    assert invocations, "the release-verify job no longer runs the release lane"
    for ln in invocations:
        argv = shlex.split(ln, comments=True)
        assert not {"-x", "--exitfirst"} & set(argv), f"the release lane must run every case: {ln.strip()}"
        assert "--maxfail" not in {a.partition("=")[0] for a in argv}, ln.strip()



def test_the_lane_gates_every_case_that_inherits_or_judges_the_whole_install():
    """The gates live in the lane's case bodies, which only the aarch64 release job runs — so the
    calls are pinned here: a case that builds on another case's stack opens with its gate, and the
    identity case opens with `require_all_passed()`."""
    import ast

    lane = Path(__file__).resolve().parents[1] / "release" / "test_release_verify.py"
    bodies = {f.name: f.body for f in ast.parse(lane.read_text()).body if isinstance(f, ast.FunctionDef)}

    def first_call(case):
        stmt = next(s for s in bodies[case]
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)))   # past the docstring
        return ast.unparse(stmt)
    assert first_call("test_release_graywolf") == "require_prerequisite(env, 'kiss', left_by='test_release_kiss')"
    assert first_call("test_release_voice_gtk") == "require_prerequisite(env, 'daemon', left_by='test_release_chat')"
    assert first_call("test_release_voice_terminal") == "inherits('test_release_voice_gtk')"
    assert first_call("test_release_reticulum_nomadnet") == \
        "require_prerequisite(env, 'reticulum', left_by='test_release_reticulum')"
    assert first_call("test_release_identity_matches_candidate_manifest") == "require_all_passed()"

# A lane as a release now runs it: no -x. The stub is the cleanup lane's: status from STATE, a stop
# that stops. `rel.lab_endpoints` is the clean-lab proof's endpoint source, replaced by plain
# module assignment (it exists on both sides of the change).
_RUN_ALL_STUB = """
import socket, subprocess
import lhpc_testlab.release as rel
import pytest

STATE = {"kiss": "stopped", "graywolf": "stopped", "daemon": "stopped", "chat": "stopped",
         "voice": "stopped", "reticulum": "stopped"}

def _run(env, *a, **kw):
    if a[:1] == ("status",):
        out = "".join(f"[{s}] {s.title()}  ({st})\\n" for s, st in STATE.items())
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    assert a[:2] == ("stack", "stop"), a
    STATE[a[2].partition("@")[0]] = "stopped"
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

rel.run_lhpc = _run
rel.lab_endpoints = lambda env: []

@pytest.fixture
def env():
    return {}
"""

_PREREQUISITE_LANE = _RUN_ALL_STUB + """
def test_release_kiss(env):
    STATE["kiss"] = "running"
    assert False, rel.stack_regression("kiss", "readiness") + "\\nkiss never served KISS/TCP"

def test_release_graywolf(env):
    rel.require_prerequisite(env, "kiss", left_by="test_release_kiss")
    assert False, rel.stack_regression("graywolf", "start") + "\\nmust never be reached"

def test_release_reticulum(env):
    STATE["reticulum"] = "running"
"""


def test_a_case_inheriting_from_a_failed_one_is_skipped_and_an_independent_one_runs(tmp_path):
    """kiss fails MARKED; graywolf, which builds on the KISS chain kiss leaves up, is SKIPPED
    naming kiss (before, it failed UNMARKED in `require_prerequisite`, and that unattributed
    failure made the consumer drop kiss's genuine freeze); reticulum, independent, runs."""
    out = _junit_outcomes(tmp_path, _PREREQUISITE_LANE, "-p", "lhpc_testlab.release_lane")
    assert [k for k, _ in out["test_release_kiss"]] == ["failure"], out
    (kind, why), = out["test_release_graywolf"]
    assert kind == "skipped" and "not run: test_release_kiss did not pass" in why, out
    assert out["test_release_reticulum"] == [("passed", "")], out
    assert _unmarked(out) == []                            # kiss's marker is the run's only verdict


_CHAIN_LANE = _RUN_ALL_STUB + """
def test_release_chat(env):
    STATE["daemon"] = "running"
    assert False, rel.stack_regression("chat", "readiness") + "\\nchat drew nothing"

def test_release_voice_gtk(env):
    rel.require_prerequisite(env, "daemon", left_by="test_release_chat")
    assert False, rel.stack_regression("voice", "start") + "\\nmust never be reached"

def test_release_voice_terminal(env):
    rel.inherits("test_release_voice_gtk")
    assert False, rel.stack_regression("voice", "readiness") + "\\nmust never be reached"

def test_release_identity_matches_candidate_manifest(env):
    rel.require_all_passed()
    assert False, "mandatory components are not installed: voice"
"""


def test_a_not_run_walks_down_the_chain_and_only_the_broken_stack_is_named(tmp_path):
    """chat fails → voice_gtk (which needs the daemon chat leaves up) is SKIPPED → voice_terminal
    (which runs voice_gtk's install) is SKIPPED too, and the identity case is not judged: the only
    stack the JUnit names is chat. Ungated, voice_terminal failed MARKED voice."""
    out = _junit_outcomes(tmp_path, _CHAIN_LANE, "-p", "lhpc_testlab.release_lane")
    assert [k for k, _ in out["test_release_chat"]] == ["failure"], out
    for case, after in (("test_release_voice_gtk", "test_release_chat"),
                        ("test_release_voice_terminal", "test_release_voice_gtk")):
        (kind, why), = out[case]
        assert kind == "skipped" and f"not run: {after} did not pass" in why, out
    (kind, why), = out["test_release_identity_matches_candidate_manifest"]
    assert kind == "skipped" and "not judged: test_release_chat, test_release_voice_gtk" in why, out
    named = {m for entries in out.values() for k, t in entries if k in ("failure", "error")
             for m in STACK_REGRESSION_IN_TEXT.findall(t)}
    assert named == {("chat", "readiness")} and _unmarked(out) == [], out


def test_the_identity_case_runs_when_every_box_case_passed(tmp_path):
    lane = _RUN_ALL_STUB + """
def test_release_kiss(env):
    pass

def test_release_identity_matches_candidate_manifest(env):
    rel.require_all_passed()
"""
    out = _junit_outcomes(tmp_path, lane, "-p", "lhpc_testlab.release_lane")
    assert out["test_release_identity_matches_candidate_manifest"] == [("passed", "")], out


# The verdict's case (G1 row 210 B1): kiss fails and leaves a listener LHPC status does not show,
# then an independent, otherwise healthy stack fails to start on it and earns its own marker.
_ORPHAN_LANE = _RUN_ALL_STUB.replace("rel.lab_endpoints = lambda env: []\n", """
ORPHAN = socket.socket()
ORPHAN.bind(("127.0.0.1", 0))
ADDRESS = "127.0.0.1:%d" % ORPHAN.getsockname()[1]
rel.lab_endpoints = lambda env: [("kiss", "loraham-kiss-tnc", "tcp", ADDRESS)]
""") + """
def test_release_kiss(env):
    STATE["kiss"] = "running"
    ORPHAN.listen()
    if CLOSE:
        ORPHAN.close()
    assert False, rel.stack_regression("kiss", "readiness") + "\\nkiss never served KISS/TCP"

def test_release_reticulum(env):
    STATE["reticulum"] = "running"
    assert False, rel.stack_regression("reticulum", "readiness") + "\\nrnsd gone: its port was taken"
"""


def test_an_orphan_lhpc_cannot_see_makes_the_run_unattributable(tmp_path):
    """After kiss's stops succeed, the clean-lab proof finds the endpoint still accepting and turns
    kiss's teardown into an UNMARKED error naming it — so reticulum's marker, earned on what kiss
    left, cannot become a freeze: the consumer refuses every hold in a run with an unmarked entry."""
    out = _junit_outcomes(tmp_path, _ORPHAN_LANE.replace("STATE = {", "CLOSE = False\nSTATE = {"),
                          "-p", "lhpc_testlab.release_lane")
    kiss = dict(out["test_release_kiss"])
    assert "STACK-REGRESSION" not in kiss["error"], kiss
    assert re.search(r"kiss/loraham-kiss-tnc tcp 127\.0\.0\.1:\d+ still accepts connections", kiss["error"]), kiss
    assert "NOTHING in this run may be attributed" in kiss["error"], kiss
    assert dict(out["test_release_reticulum"])["failure"]          # the independent case was judged
    assert ("test_release_kiss", "error") in _unmarked(out)


def test_a_lab_shown_clean_keeps_every_marker(tmp_path):
    """The same lane with the listener closed before kiss fails: no teardown error, kiss's marker
    stands, and the independent reticulum is judged and attributed on its own."""
    out = _junit_outcomes(tmp_path, _ORPHAN_LANE.replace("STATE = {", "CLOSE = True\nSTATE = {"),
                          "-p", "lhpc_testlab.release_lane")
    assert [k for k, _ in out["test_release_kiss"]] == ["failure"], out
    assert [k for k, _ in out["test_release_reticulum"]] == ["failure"], out
    assert _unmarked(out) == []


def test_a_stack_still_holding_after_a_stop_that_succeeded_is_a_leftover(tmp_path):
    """The stop answers rc 0 but LHPC still reports the stack running: the second status read names it."""
    lane = _RUN_ALL_STUB.replace('    STATE[a[2].partition("@")[0]] = "stopped"\n', "") + """
def test_release_kiss(env):
    STATE["kiss"] = "running"
    assert False, rel.stack_regression("kiss", "readiness") + "\\nkiss never served KISS/TCP"
"""
    out = _junit_outcomes(tmp_path, lane, "-p", "lhpc_testlab.release_lane")
    kiss = dict(out["test_release_kiss"])
    assert "kiss is still running after its stop" in kiss["error"] and "STACK-REGRESSION" not in kiss["error"]


# --- lab_leftovers itself: fail closed on every answer that cannot show the lab clean ------------------


def _leftovers(monkeypatch, status, endpoints=(), timeout=0.0):
    import lhpc_testlab.release as rel

    def run(env, *args, timeout=300.0, check=False):
        if isinstance(status, BaseException):
            raise status
        return status
    monkeypatch.setattr(rel, "run_lhpc", run)
    monkeypatch.setattr(rel, "lab_endpoints", lambda env: list(endpoints))
    return rel.lab_leftovers({}, timeout=timeout)


_CLEAN = subprocess.CompletedProcess(["lhpc", "status"], 0, "[kiss] KISS TNC  (stopped)\n", "")


@pytest.mark.parametrize("status, why", [
    (subprocess.TimeoutExpired(["lhpc", "status"], 180), "`lhpc status` timed out"),
    (subprocess.CompletedProcess(["lhpc", "status"], 1, "[kiss] KISS TNC  (stopped)\n", ""), "failed (rc 1)"),
], ids=["timeout", "rc1"])
def test_a_status_that_cannot_be_read_is_a_leftover(monkeypatch, status, why):
    left = _leftovers(monkeypatch, status)
    assert len(left) == 1 and why in left[0] and "cannot be shown clean" in left[0], left


@pytest.mark.parametrize("state", ["running", "degraded", "failed"])
def test_a_stack_lhpc_still_reports_holding_is_a_leftover(monkeypatch, state):
    status = subprocess.CompletedProcess(["lhpc", "status"], 0, f"[kiss] KISS TNC  ({state})\n", "")
    assert _leftovers(monkeypatch, status) == [f"kiss is still {state} after its stop"]


def test_a_refused_port_and_a_missing_socket_are_free(monkeypatch, tmp_path):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert _leftovers(monkeypatch, _CLEAN, [("kiss", "k", "tcp", f"127.0.0.1:{port}"),
                                            ("daemon", "d", "unix", str(tmp_path / "gone.sock"))]) == []


def test_a_listening_port_and_a_listening_socket_are_leftovers(monkeypatch, tmp_path):
    import socket

    with socket.socket() as t, socket.socket(socket.AF_UNIX) as u:
        t.bind(("127.0.0.1", 0))
        t.listen()
        u.bind(str(tmp_path / "live.sock"))
        u.listen()
        addr = f"127.0.0.1:{t.getsockname()[1]}"
        left = _leftovers(monkeypatch, _CLEAN, [("kiss", "k", "tcp", addr),
                                                ("daemon", "d", "unix", str(tmp_path / "live.sock"))])
    assert left == [f"kiss/k tcp {addr} still accepts connections",
                    f"daemon/d unix {tmp_path / 'live.sock'} still accepts connections"]


def test_an_answer_that_cannot_rule_out_a_holder_is_a_leftover(monkeypatch):
    import lhpc_testlab.release as rel

    def denied(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(rel.socket, "create_connection", denied)
    left = _leftovers(monkeypatch, _CLEAN, [("kiss", "k", "tcp", "127.0.0.1:8001")])
    assert left == ["kiss/k tcp 127.0.0.1:8001 gave PermissionError: [Errno 13] Permission denied, "
                    "so it cannot be shown free"]


def test_an_unrendered_address_is_a_leftover(monkeypatch):
    left = _leftovers(monkeypatch, _CLEAN, [("daemon", "d", "unix", "/tmp/loraconf-{rid}.sock")])
    assert left == ["daemon/d unix /tmp/loraconf-{rid}.sock is not rendered, so it cannot be checked"]


def test_an_endpoint_still_closing_is_polled_until_free(monkeypatch):
    """Counted answers, not time: the endpoint is held for two rounds, then free."""
    import lhpc_testlab.release as rel

    answers = iter(["still accepts connections", "still accepts connections"])
    monkeypatch.setattr(rel, "_endpoint_held", lambda kind, address: next(answers, ""))
    assert _leftovers(monkeypatch, _CLEAN, [("kiss", "k", "tcp", "127.0.0.1:8001")], timeout=60) == []


# --- T-SET: the endpoints the proof checks are the ones the lane itself waits on ---------------------------


def test_the_clean_lab_proof_checks_every_rendered_endpoint_the_lane_waits_on(tmp_path):
    """On the lab's own baseline hardware list (`setup_radios(paths, "loraham")`, what the lab reset
    writes: lhpc_testlab/ops.py `set_hardware_setup("loraham")`), every address the lane's own
    `port_of` resolves is in `lab_endpoints`, rendered per instance: meshcore runs on r2, where its
    ports are NOT the base ones; meshtastic's r2 instance is there beside r1; the daemon's socket is
    there for each radio; and nothing is left as a template. Containment, not equality: the proof may
    check more than the lane waits on."""
    from lhpc_testlab import release as rel
    from lhpc_testlab.testing import port_of

    from lhpc.core import radio_start
    from lhpc.core.paths import Paths

    ok, why = radio_start.setup_radios(Paths(runtime_root=tmp_path), "loraham")[:2]
    assert ok, why
    env = {"LHPC_RUNTIME_ROOT": str(tmp_path)}
    eps = rel.lab_endpoints(env)
    tcp = {a for _s, _c, k, a in eps if k == "tcp"}
    unix = {a for _s, _c, k, a in eps if k == "unix"}
    assert port_of(env, "meshcore", rel.MESHCORE_COMPANION) == 5133      # rendered != base (measured)
    for stack, base in (("kiss", rel.KISS_TCP), ("graywolf", rel.GRAYWOLF_UI), ("meshtastic", rel.MESHTASTIC_API),
                        ("meshtastic", rel.MESHTASTIC_WEB), ("meshcom", rel.MESHCOM_UI),
                        ("meshcore", rel.MESHCORE_COMPANION), ("meshcore", rel.REPEATER_DASHBOARD),
                        ("meshcore", rel.MESHCORE_WEBUI)):
        assert f"127.0.0.1:{port_of(env, stack, base)}" in tcp, (stack, base, sorted(tcp))
    assert f"127.0.0.1:{rel.MESHCHAT_UI}" in tcp                       # the lane waits on it unrendered
    assert {"127.0.0.1:5133", "127.0.0.1:8133", "127.0.0.1:8921", "127.0.0.1:4536"} <= tcp, sorted(tcp)
    assert {"/tmp/loraconf-r1.sock", "/tmp/loraconf-r2.sock"} <= unix, sorted(unix)
    assert not [a for _s, _c, _k, a in eps if "{" in a], eps


def test_the_lane_refuses_to_fake_a_box_without_a_display(tmp_path):
    """The terminal Voice variant is only offered where LHPC's own predicate sees no graphical
    session, so the lane takes ITS OWN X server down for that case. Asked to remove a session it
    did not start, it must refuse and say so — proving that variant needs a box without a
    display, not a pretence that there is none."""
    from lhpc_testlab.release_lane import LabDisplay

    d = LabDisplay(":98")
    assert d.proc is None
    with pytest.raises(AssertionError, match="did not start"):
        d.down()


def test_a_stack_whose_name_carries_parentheses_is_still_seen(monkeypatch):
    """`MeshCore (OpenHop)`, `Reticulum (RNS)` and `MeshCom (QEMU)` are three of the eight. A
    parser that forbade `(` before the state read them as absent, so a prerequisite check on any
    of them failed on every single run — and the lane's own controller line must still not be
    mistaken for a stack."""
    import lhpc_testlab.release as rel

    text = ("[daemon] LoRaHAM daemon  (stopped)\n"
            "[meshcore] MeshCore (OpenHop)  (running)\n"
            "[reticulum] Reticulum (RNS)  (degraded)\n"
            "[meshcom] MeshCom (QEMU)  (not-installed)\n"
            "[controller] loraham-pi-control  (up to date)\n"
            "   loraham-chat  (running)\n")
    # The executable is the parser's one collaborator: stubbed so this text IS `lhpc status`.
    monkeypatch.setattr(rel, "run_lhpc",
                        lambda env, *a, **k: subprocess.CompletedProcess(a, 0, text, ""))
    assert rel.stack_states({}) == {"daemon": "stopped", "meshcore": "running",
                                    "reticulum": "degraded", "meshcom": "not-installed"}


def test_only_a_stack_that_holds_something_counts_as_holding(monkeypatch):
    """A fresh lab root reports every stack `not-installed`. Counting that as "holding" put the
    stack a case was about into the before-set, so the diff came out empty and the cleanup
    stopped nothing — for every first case of every chain."""
    import lhpc_testlab.release as rel

    states = {"a": "not-installed", "b": "stopped", "c": "running",
              "d": "degraded", "e": "failed", "f": "not-applicable"}
    monkeypatch.setattr(rel, "stack_states", lambda env: states)
    assert rel.holding_stacks({}) == {"c", "d", "e"}


def test_a_real_marker_write_failure_crosses_into_the_lane_unattributed(tmp_path, monkeypatch):
    """The producer and the consumer, joined — not two halves asserted apart.

    A REAL `Lifecycle.build` runs with every command succeeding and the completion-marker write
    failing, the typed line is rendered exactly as `service_lifecycle_ops` renders it from that
    JobResult, and the lane's own consumer is asked what it makes of it. Nothing may be
    attributed: every command succeeded, so there is no evidence against any upstream pin.
    """
    import lhpc_testlab.release as rel

    from lhpc.core import build_regression as br
    from lhpc.core import lifecycle as lifecycle_mod
    from lhpc.core.jobs import JobResult, JobState
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    # The component RESOLVED by shape, not by name: whichever MeshCore component declares a step
    # as its own. Naming one is how this test broke when LHPC dropped its openhop_core patch.
    comp = next(c for s in svc.stacks() if s.id == "meshcore" for c in s.components
                if c.build_steps and any(x.get("attributable") for x in c.build_steps))
    (svc._lifecycle().source_dir(comp) / ".venv" / "bin").mkdir(parents=True, exist_ok=True)

    # The log of the step this recipe DECLARES ITS OWN — the one name that would attribute.
    # Derived from the recipe, not typed in: a fake name would make this test pass by accident.
    own = br.own_step_logs(svc.stack("meshcore"))
    owned_log = next(n for n in own if n.startswith(f"build-{comp.id}"))

    monkeypatch.setattr(lifecycle_mod, "run_job",
                        lambda runner, **kw: JobResult(name="b", state=JobState.SUCCEEDED,
                                                       returncode=0,
                                                       log_path=f"/x/logs/{owned_log}",
                                                       tail=[]))
    monkeypatch.setattr(lifecycle_mod.runtime_fs, "atomic_write",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left")))

    res = svc._lifecycle().build(comp, marker_extra=svc._consumed_source_lines(comp))
    assert not res.ok

    # Rendered the way service_lifecycle_ops renders a build result into the run's details, and
    # judged by the lane's OWN consumer: `lhpc install` succeeds, `lhpc build` fails with exactly
    # this line in its output.
    typed = f"  [{res.state.value}] build {comp.id} (rc {res.returncode}, log {res.log_path})"
    runs = iter([subprocess.CompletedProcess((), 0, "", ""),
                 subprocess.CompletedProcess((), 1, typed + "\n", "")])
    monkeypatch.setattr(rel, "run_lhpc", lambda env, *a, **k: next(runs))
    with pytest.raises(AssertionError) as failed:
        rel.install_build({"LHPC_RUNTIME_ROOT": str(tmp_path)}, svc, "meshcore")
    assert "STACK-REGRESSION" not in str(failed.value), (
        f"a local write failure was attributed to an owned step: {typed!r}")


def test_the_lane_and_the_binary_builder_share_one_attribution_rule(tmp_path):
    """Not two implementations that happen to agree today.

    The builder cannot import this package, so it runs `tools/build_regression.py` against the
    controller it was told to build. Both paths must be the SAME rule: if they ever diverged, a
    pin could be frozen by one and not the other, and the divergence would show up as a hold
    nobody could explain.
    """
    import lhpc_testlab.release as rel

    from lhpc.core import build_regression as br
    assert rel._TYPED_BUILD_FAILURE is br.TYPED_BUILD_FAILURE
    assert rel.stack_regression("meshcore", "build") == br.marker_line("meshcore", "build")

    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert rel._own_recipe_step_logs(svc, "meshcore") == br.own_step_logs(svc.stack("meshcore"))


# --- a wait-based readiness check: marked only when LHPC reports the component gone ------------

_STATUS = """OK    Status collected (read-only; no network, no changes).
[kiss] LoRaHAM KISS TNC  ({stack})
  loraham-kiss-tnc         {state:<14} [service] band 433  tx disabled  src ok
  loraham-kiss-serial      stopped        [service] band 433  tx disabled  src ok
"""


def _readiness_message(monkeypatch, tmp_path, status):
    """`readiness_failed` for kiss with `lhpc status` answering `status` (a CompletedProcess,
    or an exception to raise). The lane's executable is stubbed: the case is the reading of
    its answer, not the process table."""
    import lhpc_testlab.release as rel

    def run(env, *args, timeout=300.0, check=False):
        if isinstance(status, BaseException):
            raise status
        return status
    monkeypatch.setattr(rel, "run_lhpc", run)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "start-loraham-kiss-tnc-433.log").write_text("KISS-RUN-LOG-LAST\n")
    return rel.readiness_failed({"LHPC_RUNTIME_ROOT": str(tmp_path)}, "kiss",
                                "loraham-kiss-tnc", "kiss is not serving KISS/TCP on 8001")


def _done(stdout, rc=0):
    return subprocess.CompletedProcess(["lhpc", "status"], rc, stdout, "")


@pytest.mark.parametrize("state", ["running", "degraded", "unknown"])
def test_a_window_that_passed_while_the_component_runs_blames_no_stack(monkeypatch, tmp_path,
                                                                      state):
    msg = _readiness_message(monkeypatch, tmp_path,
                             _done(_STATUS.format(stack=state, state=state)))
    assert not re.search(r"STACK-REGRESSION stack=", msg)
    assert f"loraham-kiss-tnc: {state}" in msg and "KISS-RUN-LOG-LAST" in msg


@pytest.mark.parametrize("state", ["stopped", "failed"])
def test_a_component_lhpc_reports_gone_is_marked(monkeypatch, tmp_path, state):
    msg = _readiness_message(monkeypatch, tmp_path,
                             _done(_STATUS.format(stack=state, state=state)))
    assert msg.split("\n", 1)[0] == "STACK-REGRESSION stack=kiss phase=readiness" and "\n" in msg
    assert "KISS-RUN-LOG-LAST" in msg


@pytest.mark.parametrize("status", [
    _done("ERR   status failed\n", rc=1),                                # the call failed
    _done(_STATUS.format(stack="stopped", state="stopped"), rc=2),     # failed, whatever it printed
    _done("OK    Status collected.\n"),                                 # the component not listed
    subprocess.TimeoutExpired(["lhpc", "status"], 120),               # the call timed out
], ids=["rc1", "rc2-with-stopped-text", "not-listed", "timeout"])
def test_a_status_that_cannot_be_read_blames_no_stack(monkeypatch, tmp_path, status):
    msg = _readiness_message(monkeypatch, tmp_path, status)
    assert not re.search(r"STACK-REGRESSION stack=", msg)
    assert "loraham-kiss-tnc: unreadable" in msg


# --- an interactive program's readiness: only evidence of breaking blames the stack -------------

def _readiness_failure(command, tmp_path, *, expect="NEVER-DRAWN", logs=(), on_timeout=None):
    import os

    from lhpc_testlab.release import pty_readiness
    extra = {"on_timeout": on_timeout} if on_timeout else {}
    with pytest.raises(AssertionError) as exc:
        pty_readiness(command, dict(os.environ), expect, ready_timeout=1.5, hold=0.2,
                      stack="reticulum", logs=logs, **extra)
    return str(exc.value)


@pytest.mark.parametrize("command", [
    "sleep 30",                                         # still running, drew nothing
    "printf 'loading the node list'; sleep 30",         # still running, drew something else
    "printf '[Error] interface not ready\\n'; sleep 30",  # an app's own log tag, not a crash
])
@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
@pytest.mark.slow
def test_a_readiness_timeout_without_evidence_blames_no_stack(tmp_path, command):
    msg = _readiness_failure(command, tmp_path)
    assert "STACK-REGRESSION" not in msg


@pytest.mark.parametrize("command", [
    "true",                                                                  # exited, drew nothing
    pytest.param("printf 'Traceback (most recent call last):\\n'; sleep 30", marks=pytest.mark.slow),  # drew a traceback
    pytest.param("printf 'ModuleNotFoundError: no module named x\\n'; sleep 30", marks=pytest.mark.slow),  # an exception line
])
@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
def test_a_readiness_failure_with_evidence_of_breaking_is_marked(tmp_path, command):
    msg = _readiness_failure(command, tmp_path)
    assert "STACK-REGRESSION stack=reticulum phase=readiness" in msg


@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
@pytest.mark.slow
def test_a_readiness_failure_carries_the_tails_of_the_named_logs(tmp_path):
    app, daemon = tmp_path / "logfile", tmp_path / "absent-logfile"
    app.write_text("old line\n" + "x" * 2000 + "APP-LOG-LAST-LINE\n")
    msg = _readiness_failure("sleep 30", tmp_path, logs=(app, daemon))
    assert f"--- {app} (tail) ---" in msg and "APP-LOG-LAST-LINE" in msg
    assert "old line" not in msg                                      # a tail, not the whole log
    assert f"--- {daemon} (tail) ---" in msg and "not readable" in msg


# --- a readiness timeout: diagnosed (its stack, and what it waits on), never retried -------------

def _counted(tmp_path, first, then):
    """A shell command that appends one line per start to a counter file and runs `first` on
    the first start, `then` on every later one. Returns (command, counter)."""
    counter = tmp_path / "starts"
    return (f"echo start >> {counter}; "
            f"if [ $(wc -l < {counter}) -eq 1 ]; then {first}; else {then}; fi"), counter


def _hang_in_python():
    import sys
    return f"exec {sys.executable} -c 'import time; time.sleep(60)'"


@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
@pytest.mark.slow
def test_a_start_that_times_out_is_not_retried_and_carries_its_stack(tmp_path):
    """A test body is never re-run because it failed: a hung first start fails, unmarked, with
    the thread dump of where it hung — even when a second start would have drawn."""
    command, counter = _counted(tmp_path, _hang_in_python(), "printf 'READY'; sleep 30")
    msg = _readiness_failure(command, tmp_path, expect="READY")
    assert counter.read_text().count("start") == 1
    assert "STACK-REGRESSION" not in msg
    assert "Fatal Python error" in msg and "time.sleep" in msg


@pytest.mark.slow
def test_the_timeout_diagnosis_runs_while_the_program_still_hangs(tmp_path):
    """`on_timeout` runs BEFORE the abort: what a hung program waits on (another process, a
    socket) is only visible while it still waits."""
    import os

    pidfile = tmp_path / "pid"
    seen = []

    def diagnose():
        pid = int(pidfile.read_text())
        os.kill(pid, 0)                            # raises if it were already gone
        seen.append(pid)
        return "\n--- DIAGNOSIS-TEXT ---"
    msg = _readiness_failure(f"echo $$ > {pidfile}; {_hang_in_python()}", tmp_path,
                             on_timeout=diagnose)
    assert len(seen) == 1
    assert msg.index("DIAGNOSIS-TEXT") < msg.index("Fatal Python error")
    assert "STACK-REGRESSION" not in msg


@pytest.mark.slow
def test_the_reticulum_snapshot_aborts_only_recorded_reticulum_processes(tmp_path):
    """The snapshot SIGABRTs the process group of every Reticulum process LHPC owns, and returns
    their faulthandler dumps from their run logs. A process of another stack, and a record whose
    pid now belongs to a different process (a stale record after pid reuse), are left alone."""
    import json
    import os

    from lhpc_testlab.release import reticulum_peers_snapshot

    from lhpc.core.procident import proc_identity
    (tmp_path / "logs").mkdir()
    owned = tmp_path / "state" / "owned"
    owned.mkdir(parents=True)
    env = dict(os.environ, PYTHONFAULTHANDLER="1")
    procs = {}
    for comp, band in (("rns", "868"), ("loraham-kiss-tnc", "433"), ("lxmd", "868")):
        # It says `ready` once its interpreter (and with it faulthandler) is up; the dump goes to
        # its run log on stderr, as an LHPC-started component's does.
        with open(tmp_path / "logs" / f"start-{comp}-{band}.log", "ab") as log:
            procs[comp] = subprocess.Popen(
                [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
                stdout=subprocess.PIPE, stderr=log, env=env, start_new_session=True, text=True)
        rec = {"pid": procs[comp].pid, **proc_identity(procs[comp].pid)}
        if comp == "lxmd":                          # stale: the pid's process started later
            rec["starttime"] = int(rec["starttime"]) - 1
        (owned / f"{comp}__{band}__{procs[comp].pid}__n1.json").write_text(json.dumps(rec))
    try:
        for p in procs.values():
            assert p.stdout.readline() == "ready\n"
        out = reticulum_peers_snapshot({"LHPC_RUNTIME_ROOT": str(tmp_path)}, drain=2.0)
        assert procs["rns"].wait(timeout=10) != 0          # aborted
        assert procs["loraham-kiss-tnc"].poll() is None    # another stack: untouched
        assert procs["lxmd"].poll() is None                # a stale record: untouched
        assert "aborted for their stacks: rns; skipped: lxmd (not the recorded process) ---" in out
        assert "Fatal Python error: Aborted" in out and 'File "<string>", line 1' in out
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)


@pytest.mark.parametrize("first", [
    "exit 3",                                                           # exited
    pytest.param("printf 'Traceback (most recent call last):\\n'; sleep 30", marks=pytest.mark.slow),  # drew a traceback
])
@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
def test_a_start_that_shows_it_is_broken_is_never_retried(tmp_path, first):
    command, counter = _counted(tmp_path, first, "printf 'READY'; sleep 30")
    msg = _readiness_failure(command, tmp_path, expect="READY")
    assert counter.read_text().count("start") == 1
    assert "STACK-REGRESSION stack=reticulum phase=readiness" in msg


@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
def test_the_program_runs_without_core_dumps(tmp_path):
    import os

    from lhpc_testlab.release import pty_readiness
    limit = tmp_path / "core-limit"
    pty_readiness(f"ulimit -c > {limit}; printf 'READY'; sleep 30", dict(os.environ), "READY",
                  ready_timeout=5, hold=0.2)
    assert limit.read_text().strip() == "0"


@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
def test_a_start_whose_terminal_closed_before_it_was_reaped_is_an_exit(tmp_path, monkeypatch):
    """The race CI hit: the program's terminal closes BEFORE its exit can be reaped, so
    `poll()` still says "running" right after an exit. That is an exit — marked, never retried
    as a pure timeout. Modelled with a process whose `poll()` answers "running" until waited."""
    from lhpc_testlab import release
    real_launch = release._pty_launch

    class NotYetReaped:
        def __init__(self, proc):
            self._proc, self._waited = proc, False

        def poll(self):
            return self._proc.poll() if self._waited else None

        def wait(self, timeout=None):
            self._waited = True
            return self._proc.wait(timeout)

        def __getattr__(self, name):
            return getattr(self._proc, name)

    def launch(command, env):
        proc, master = real_launch(command, env)
        return NotYetReaped(proc), master
    monkeypatch.setattr(release, "_pty_launch", launch)
    command, counter = _counted(tmp_path, "exit 3", "printf 'READY'; sleep 30")
    msg = _readiness_failure(command, tmp_path, expect="READY")
    assert counter.read_text().count("start") == 1
    assert "STACK-REGRESSION stack=reticulum phase=readiness" in msg


@pytest.mark.wallclock("a real child on a terminal against the readiness wait")
def test_a_read_error_other_than_a_closed_terminal_propagates_unmarked(tmp_path, monkeypatch):
    """Only EOF and EIO mean the program's terminal closed. Any other read error (EBADF here)
    is this harness's own fault: it propagates, and never reads as a marked exit."""
    import errno
    import os
    import types

    from lhpc_testlab import release
    fake_os = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})

    def read(fd, n):
        raise OSError(errno.EBADF, "injected")
    fake_os.read = read
    monkeypatch.setattr(release, "os", fake_os)
    with pytest.raises(OSError) as exc:
        release.pty_readiness("printf 'READY'; sleep 30", dict(os.environ), "READY",
                              ready_timeout=3, hold=0.2, stack="reticulum")
    assert exc.value.errno == errno.EBADF
    assert "STACK-REGRESSION" not in str(exc.value)


# --- a failed transfer: the EXTERNAL-UNAVAILABLE line, the decisive step, the transfer backoff ----

# The bot's search (the agreed contract): never anchored, so pytest's prefixes cannot hide it.
_EXTERNAL = re.compile(r"EXTERNAL-UNAVAILABLE what=([a-z0-9][a-z0-9_.:-]*)(?=\s|$)")


class _Done:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def _lhpc(monkeypatch, **by_verb):
    """`lhpc <verb> …` answers from `by_verb[verb]`, a list consumed in order (the last one
    repeats); returns the per-verb call counts. One retry with no wait: the schedule itself is
    pinned in test_workflow_shell.py's twin and below."""
    import lhpc_testlab.release as rel
    calls = dict.fromkeys(by_verb, 0)

    def run(env, verb, *a, **kw):
        calls[verb] += 1
        seq = by_verb[verb]
        return seq[min(calls[verb], len(seq)) - 1]
    monkeypatch.setattr(rel, "run_lhpc", run)
    monkeypatch.setattr(rel, "on_binary", lambda env, stack: False)   # stub: no receipt read
    monkeypatch.setattr(rel, "TRANSFER_WAITS", (0, 0), raising=False)
    monkeypatch.setattr(rel, "TRANSFER_TOTAL", 0, raising=False)
    return calls


def _install_build_message(stack="kiss", build=True):
    import warnings

    import lhpc_testlab.release as rel

    from lhpc.core.manifest import default_manifest_path, load_manifest

    class Svc:
        def stack(self, sid):
            return next(s for s in load_manifest(default_manifest_path()) if s.id == sid)
    with pytest.raises(AssertionError) as exc, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rel.install_build({"LHPC_RUNTIME_ROOT": "/nonexistent"}, Svc(), stack, build=build)
    return str(exc.value)


CLONE_DNS = ("  [failed] clone loraham-kiss-tnc — fatal: unable to access "
             "'https://h.invalid/k.git/': Could not resolve host: h.invalid\n")


def test_an_install_that_failed_on_a_transfer_is_external_and_never_marked(monkeypatch):
    calls = _lhpc(monkeypatch, install=[_Done(1, CLONE_DNS)])
    msg = _install_build_message(build=False)
    assert msg.splitlines()[0] == "EXTERNAL-UNAVAILABLE what=install:kiss"
    assert "Could not resolve host" in msg.splitlines()[1]
    assert "STACK-REGRESSION" not in msg and calls["install"] == 3      # 1 + 2 retries


def test_a_transfer_that_recovers_passes_after_the_backoff(monkeypatch):
    import warnings

    import lhpc_testlab.release as rel
    calls = _lhpc(monkeypatch, install=[_Done(1, CLONE_DNS), _Done(1, CLONE_DNS), _Done(0)])
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        rel.install_build({"LHPC_RUNTIME_ROOT": "/nonexistent"}, None, "kiss", build=False)
    assert calls["install"] == 3
    assert sum("retrying in 0 s" in str(w.message) for w in seen) == 2


def test_a_failure_that_is_not_a_transfer_is_never_repeated(monkeypatch):
    calls = _lhpc(monkeypatch, install=[
        _Done(1, "  [failed] clone loraham-kiss-tnc — fatal: repository not found\n")])
    msg = _install_build_message(build=False)
    assert not _EXTERNAL.search(msg) and calls["install"] == 1


def _meshcore_steps():
    from lhpc.core.manifest import default_manifest_path, load_manifest
    st = next(s for s in load_manifest(default_manifest_path()) if s.id == "meshcore")
    node = next(c for c in st.components if c.build_steps
                and any(s.get("attributable") for s in c.build_steps)
                and any(str(s["argv"][0]).endswith("pip") and "install" in s["argv"]
                        for s in c.build_steps))
    net = next(i for i, s in enumerate(node.build_steps)
               if str(s["argv"][0]).endswith("pip") and "install" in s["argv"])
    own = next(i for i, s in enumerate(node.build_steps) if s.get("attributable"))
    def typed(step):
        return f"  [failed] build {node.id} (rc 1, log /nonexistent/logs/build-{node.id}-{step}.log)\n"
    return typed(net), typed(own)


@pytest.mark.parametrize("step_output, expect", [
    # the decisive (last) error line of a fetching step is a transport failure → EXTERNAL
    ("ERROR: Could not install packages due to an OSError: HTTPSConnectionPool(host='pypi.org',"
     " port=443): Read timed out.\n", "external"),
    # pip's outage shape: retries exhausted on name resolution, then "no version satisfies"
    ("WARNING: Retrying (Retry(total=0, connect=None)) after connection broken by "
     "'NewConnectionError(...: Temporary failure in name resolution)': /simple/pynacl/\n"
     "ERROR: Could not find a version that satisfies the requirement pynacl\n", "external"),
    # SAME-STEP TWIN: a transport hiccup pip recovered from, then a genuine resolution failure
    ("WARNING: Retrying (Retry(total=4, connect=None)) after connection broken by "
     "'ConnectionError(...)': /simple/pynacl/\n"
     "ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/\n", "unmarked"),
    # a version that does not exist: no exhausted retry, so not a transfer
    ("ERROR: Could not find a version that satisfies the requirement pynacl==99\n", "unmarked"),
    ("something nobody classified\n", "unmarked"),
])
def test_a_fetching_build_step_is_judged_by_its_decisive_line(monkeypatch, step_output, expect):
    net_typed, _ = _meshcore_steps()
    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(1, step_output + net_typed)])
    msg = _install_build_message("meshcore")
    assert "STACK-REGRESSION" not in msg
    if expect == "external":
        assert msg.splitlines()[0] == "EXTERNAL-UNAVAILABLE what=build:meshcore"
        assert calls["build"] == 3
    else:
        assert not _EXTERNAL.search(msg) and calls["build"] == 1


def test_an_attributable_step_is_marked_whatever_transport_text_the_output_carries(monkeypatch):
    """The MIXED-LOG TWIN: the decisive step is the recipe's own, so it is the stack's
    regression, even though an earlier step's output carries a transport error."""
    _, own_typed = _meshcore_steps()
    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(
        1, "ERROR: Could not install packages: Read timed out.\n"
           "error: patch does not apply\n" + own_typed)])
    msg = _install_build_message("meshcore")
    assert msg.splitlines()[0] == "STACK-REGRESSION stack=meshcore phase=build"
    assert not _EXTERNAL.search(msg) and calls["build"] == 1


# A real failing install's output (release-verify run 37403409245, 2026-10-06): the summary is
# the `ERR` line, then numbered advice and `Next:` — a refusal, not a transfer.
REAL_INSTALL_REFUSAL = (
    "ERR   Binary install of 'meshcom' refused: the published binary was built from different "
    "commits than this lhpc pins (meshcom-firmware: binary e1e2acea6, pin 62095658c)\n"
    "  1. Update LHPC first, then install again (about a minute): lhpc self-update --apply\n"
    "  2. Or build from source: lhpc install meshcom --source pinned --yes\n"
    "  Staying on the binary keeps meshcom exactly as it is.\n\nNext:\n  lhpc self-update --apply\n")


@pytest.mark.parametrize("output", [
    REAL_INSTALL_REFUSAL,
    # an HTTP 4xx is not the network: a binary not yet published, a repository that is gone
    "ERR   Binary install of 'meshcom' refused: download failed: HTTP Error 404: Not Found\n",
    "  [failed] clone loraham-kiss-tnc — fatal: unable to access 'https://h.invalid/k.git/': "
    "The requested URL returned error: 403\n",
], ids=["real-refusal", "binary-404", "git-403"])
def test_an_install_that_failed_for_a_reason_other_than_the_network_is_not_repeated(
        monkeypatch, output):
    calls = _lhpc(monkeypatch, install=[_Done(1, output)])
    msg = _install_build_message(build=False)
    assert not _EXTERNAL.search(msg) and calls["install"] == 1


@pytest.mark.parametrize("log_tail, stdout_extra, expect", [
    # the step's own log ends on a read timeout → EXTERNAL, though `lhpc build` printed nothing
    ("Collecting pynacl\nERROR: Could not install packages due to an OSError: "
     "HTTPSConnectionPool(host='pypi.org', port=443): Read timed out.\n", "", "external"),
    # the log ends on a genuine resolution failure; a timeout in `lhpc build`'s own output
    # (another step, a recovered hiccup) does not decide
    ("ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/\n",
     "WARNING: Retrying after connection broken by ReadTimeoutError\n", "unmarked"),
])
def test_the_failed_steps_readable_log_decides(monkeypatch, tmp_path, log_tail, stdout_extra,
                                               expect):
    net_typed, _ = _meshcore_steps()
    log = tmp_path / net_typed.split("log ", 1)[1].rstrip(")\n").rsplit("/", 1)[1]
    log.write_text(log_tail)
    typed = net_typed.replace(net_typed.split("log ", 1)[1].rstrip(")\n"), str(log))
    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(1, stdout_extra + typed)])
    msg = _install_build_message("meshcore")
    assert "STACK-REGRESSION" not in msg
    if expect == "external":
        assert msg.splitlines()[0] == "EXTERNAL-UNAVAILABLE what=build:meshcore"
        assert calls["build"] == 3
    else:
        assert not _EXTERNAL.search(msg) and calls["build"] == 1


# The end of the failed step's own log in lhpc-binaries run 34133564060 (2026-09-07), verbatim:
# meshtastic's library fetch (`pio pkg install`, build-meshtastic-2.log) met a gateway timeout.
# PlatformIO's last word is a `PackageException:` line naming the HTTP status.
PIO_504_TAIL = (
    "Library Manager: Installing https://github.com/thingsboard/pubsubclient/archive/refs/tags/"
    "v2.12.1.zip\n"
    "Downloading 0% 10% 20% 30% 40% 50% 60%\n"
    "Unpacking 0% 10% 20% 30% 40% 50% 60% 70% 80% 90% 100%\n"
    "Library Manager: TBPubSubClient@2.12.1 has been installed!\n"
    "Library Manager: Installing https://github.com/arduino-libraries/NTPClient/archive/refs/tags/"
    "3.2.1.zip\n"
    "PackageException: Got the unrecognized status code '504' when downloaded "
    "https://github.com/arduino-libraries/NTPClient/archive/refs/tags/3.2.1.zip\n")


def _meshtastic_fetch_step_failed(tmp_path, log_tail):
    """`lhpc build meshtastic`'s typed failure at its PlatformIO fetch step, that step's log
    holding `log_tail`. The step is found in the manifest, and is not the recipe's own."""
    from lhpc.core.manifest import default_manifest_path, load_manifest
    st = next(s for s in load_manifest(default_manifest_path()) if s.id == "meshtastic")
    comp = next(c for c in st.components if c.id == "meshtastic")
    i = next(i for i, s in enumerate(comp.build_steps)
             if str(s["argv"][0]).endswith("/pio") and s["argv"][1:3] == ["pkg", "install"])
    assert not comp.build_steps[i].get("attributable")
    log = tmp_path / f"build-{comp.id}-{i}.log"
    log.write_text(log_tail)
    return f"  [failed] build {comp.id} (rc 1, log {log})\n"


@pytest.mark.parametrize("log_tail, expect", [
    (PIO_504_TAIL, "external"),
    # the same shape with a 4xx: the archive is gone, which no wait brings back
    (PIO_504_TAIL.replace("'504'", "'404'"), "unmarked"),
], ids=["platformio-504", "platformio-404"])
def test_a_platformio_fetch_that_met_a_5xx_is_a_transfer(monkeypatch, tmp_path, log_tail, expect):
    typed = _meshtastic_fetch_step_failed(tmp_path, log_tail)
    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(1, typed)])
    msg = _install_build_message("meshtastic")
    assert "STACK-REGRESSION" not in msg
    if expect == "external":
        assert msg.splitlines()[:2] == [
            "EXTERNAL-UNAVAILABLE what=build:meshtastic",
            "PackageException: Got the unrecognized status code '504' when downloaded "
            "https://github.com/arduino-libraries/NTPClient/archive/refs/tags/3.2.1.zip"]
        assert calls["build"] == 3                                    # 1 + 2 retries
    else:
        assert not _EXTERNAL.search(msg) and calls["build"] == 1


_DNS = "fatal: unable to access 'https://github.com/x/y.git/': Could not resolve host: github.com\n"


@pytest.mark.parametrize("log_tail", [
    # the step's LAST error line decides, as for every other shape: a later PackageException that
    # names no 5xx wins over an earlier transfer line (intended: the step ended on the package)
    _DNS + "PackageException: Could not find the package with 'foo @ 1.0' requirements for your "
           "system 'linux_x86_64'\n",
    # a 5xx PackageException the step went on past: its last error line is a compile error
    "PackageException: Got the unrecognized status code '503' when downloaded https://x/y.zip\n"
    "error: compilation terminated.\n",
    # "status code" with a 5xx in a line that is not PlatformIO's is not a transfer
    "ERROR: test_server: expected status code '200', got status code '500'\n",
    # a PackageException inside a traceback line, after the last error line, is no error line
    "error: compilation terminated.\n"
    "  platformio.package.exception.PackageException: Got the unrecognized status code '504' "
    "when downloaded https://x/y.zip\n",
], ids=["later-packageexception-decides", "5xx-not-last", "non-platformio-status-500",
        "traceback-packageexception"])
def test_only_platformios_own_last_line_makes_a_5xx_a_transfer(monkeypatch, tmp_path, log_tail):
    typed = _meshtastic_fetch_step_failed(tmp_path, log_tail)
    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(1, typed)])
    msg = _install_build_message("meshtastic")
    assert "STACK-REGRESSION" not in msg
    assert not _EXTERNAL.search(msg) and calls["build"] == 1


# --- a failed start carries the start logs it wrote ---------------------------------------------
import ast as _ast  # noqa: E402
import os as _os  # noqa: E402
import types  # noqa: E402

import lhpc_testlab.release as rel  # noqa: E402

_RV = Path(__file__).resolve().parents[1] / "release" / "test_release_verify.py"


def _rv_function(name: str, lane: Path = _RV, **namespace):
    """One top-level function of the release lane, compiled from its source with `namespace` as its
    globals: the lane is read, not imported (a test module may not import a sibling test module)."""
    fn = next(f for f in _ast.parse(lane.read_text()).body
              if isinstance(f, _ast.FunctionDef) and f.name == name)
    glb = {"__builtins__": __builtins__, **namespace}
    exec(compile(_ast.Module(body=[fn], type_ignores=[]), str(lane), "exec"), glb)  # noqa: S102 - the lane's own source
    return glb[name]


def _start_failure_text(root: Path, lane: Path = _RV) -> str:
    """The AssertionError text of the lane's `_start` for a reticulum start that fails the way the
    RETI-SOCKET defect did: lhpc says only "ready endpoint(s) never came up", the cause is in the
    component's start log, which the start writes. An older log of another component is left alone.
    The lane's clock reads 1000 until the start ran and 2000 after it, so `since` must be taken before."""
    logs = root / "logs"
    logs.mkdir(parents=True)
    old = logs / "start-kiss-433-r1.log"
    old.write_text("an earlier case's start\n")
    _os.utime(old, (940.0, 940.0))
    clock = {"now": 1000.0}

    def run_lhpc(env, *args, timeout):
        (logs / "start-rns-433-r1.log").write_text(
            "[Error]    An unhandled <class 'loraham_rns.section.SectionError'> exception occurred: "
            "conf_socket: is required\n"
            "[Critical] interface 'LoRa r1' was not registered\n")
        _os.utime(logs / "start-rns-433-r1.log", (1000.5, 1000.5))
        clock["now"] = 2000.0
        return subprocess.CompletedProcess(args, 1, "ERR   Run FAILED for 'reticulum': rns did not start/verify.\n"
                                           "  [unverified] rns: ready endpoint(s) never came up\n", "")

    start = _rv_function("_start", lane, run_lhpc=run_lhpc, named=lambda env, s: f"{s}@r1",
                         stack_regression=rel.stack_regression,
                         time=types.SimpleNamespace(time=lambda: clock["now"]),
                         start_log_tails=getattr(rel, "start_log_tails", None))
    with pytest.raises(AssertionError) as exc:
        start({"LHPC_RUNTIME_ROOT": str(root)}, "reticulum")
    return str(exc.value)


def test_a_failed_start_shows_the_cause_its_component_logged(tmp_path):
    text = _start_failure_text(tmp_path)
    assert text.splitlines()[0] == rel.stack_regression("reticulum", "start")
    assert "rns: ready endpoint(s) never came up" in text
    assert "start-rns-433-r1.log (tail)" in text
    assert "SectionError'> exception occurred: conf_socket: is required" in text
    assert "start-kiss-433-r1.log" not in text                      # written before this start


def test_the_start_log_tails_are_the_logs_written_since(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    env, since = {"LHPC_RUNTIME_ROOT": str(tmp_path)}, 1_000_000.0
    assert rel.start_log_tails(env, since) == ""
    ages = {"start-rns-433-r1.log": 0.0,                            # at `since`
            "start-loraham-daemon-433-r1.log": 5.0,                 # after it
            "start-meshchat-r1.log": -(rel.START_LOG_SLACK - 0.5),  # within the coarse-clock slack
            "start-lxmd-r1.log": -(rel.START_LOG_SLACK + 1.0),      # before it
            "build-rns-0.log": 5.0}                                 # no start log
    for name, age in ages.items():
        (logs / name).write_text(f"tail of {name}\n")
        _os.utime(logs / name, (since + age, since + age))
    text = rel.start_log_tails(env, since)
    shown = re.findall(r"--- .*/(\S+) \(tail\) ---\ntail of \1\n", text)
    assert shown == ["start-loraham-daemon-433-r1.log", "start-meshchat-r1.log", "start-rns-433-r1.log"]
