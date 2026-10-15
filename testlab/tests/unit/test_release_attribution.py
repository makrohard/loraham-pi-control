"""The release lane's attribution contract, proved where the automation reads it: the required
case list, the `STACK-REGRESSION` marker grammar, JUnit attribution through the REAL release
helpers with the executable stubbed, the between-case cleanup fixture, the `-x` invocation, and
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
        STATE[a[2]] = "stopped"
        LOG.write_text(json.dumps(json.loads(LOG.read_text() or "[]") + [a[2]]))
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


# The lane as a RELEASE runs it: `-x`, and a stop that cannot stop anything. One radio pair,
# two stacks that both want it.
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


def test_a_stopped_run_reports_its_failed_cleanup_and_never_reaches_the_next_stack(tmp_path):
    """The two sequences that made continuing after a failure unsafe in BOTH directions.

    A cleanup that SUCCEEDS used to be followed by the next case failing UNMARKED on its
    prerequisite, and the consumer — right to refuse attribution when a run also carries an
    unexplained failure — then dropped the genuine freeze the first case had earned. A cleanup
    that FAILS was worse: the band stayed held, the next case hit it, earned its OWN valid
    marker, and an innocent stack was frozen with nothing in the JUnit saying the lab had not
    cleaned up.

    `-x` and a raising teardown answer both. The run stops at the first genuine regression, which
    is the only one this lane can still judge; the failed stop is reported as a teardown ERROR on
    that same case, with no marker on it — so a run whose lab is broken cannot attribute anything
    at all. Nothing about the second stack is claimed either way, and a stopped run can never
    satisfy the publication gate, which requires every required case to have passed.
    """
    entries = _junit_entries(tmp_path, _FAILFAST_LANE, "-x", "-p", "lhpc_testlab.release_lane")
    assert [(n, k) for n, k, _ in entries] == [("test_release_kiss", "failure"),
                                               ("test_release_kiss", "error")], entries
    failure, error = entries[0][2], entries[1][2]
    assert set(STACK_REGRESSION_IN_TEXT.findall(failure)) == {("kiss", "readiness")}
    assert "STACK-REGRESSION" not in error, error
    assert "could not release" in error, error


def test_the_release_verify_job_runs_the_lane_with_x():
    """`-x` IS the correction above, and it lives in the invocation rather than in the lane's
    code — so nothing in the lane notices when it is dropped. The automated release dispatches
    exactly this job and reads exactly this JUnit, which makes the flag part of the attribution
    contract and not a preference."""
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
        assert "-x" in argv, f"the release lane must stop at the first failure: {ln.strip()}"


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


def test_the_log_names_the_rule_expects_are_the_ones_the_builder_writes(tmp_path, monkeypatch):
    """The naming convention lives in TWO places and must not drift.

    `Lifecycle.build` names step `i` of a multi-step component `build-<component>-<i>`, and
    `build_regression.own_step_logs` reconstructs that name to decide what may attribute. Nothing
    tied them together: renaming the log would leave attribution matching nothing, no automatic
    freeze would ever fire again, and every run would look exactly as green as before. So this
    asks the real `build()` what it names the attributable step, and the rule what it expects.
    """
    from pathlib import PurePosixPath

    from lhpc.core import build_regression as br
    from lhpc.core import lifecycle as lifecycle_mod
    from lhpc.core.jobs import JobResult, JobState
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    stack = svc.stack("meshcore")
    comp = next(c for c in stack.components
                if c.build_steps and any(s.get("attributable") for s in c.build_steps))
    owned_index = next(i for i, s in enumerate(comp.build_steps) if s.get("attributable"))
    (svc._lifecycle().source_dir(comp) / ".venv" / "bin").mkdir(parents=True, exist_ok=True)

    seen = []

    def record(runner, **kw):
        seen.append(kw["name"])
        state = JobState.FAILED if len(seen) - 1 == owned_index else JobState.SUCCEEDED
        return JobResult(name=kw["name"], state=state,
                         returncode=1 if state is JobState.FAILED else 0,
                         log_path=f"/x/logs/{kw['name']}.log", tail=[])

    monkeypatch.setattr(lifecycle_mod, "run_job", record)
    res = svc._lifecycle().build(comp, marker_extra=svc._consumed_source_lines(comp))

    assert not res.ok and res.log_path, "the attributable step must fail with a log identity"
    assert PurePosixPath(res.log_path).name in br.own_step_logs(stack), (
        f"build() named the failed step {PurePosixPath(res.log_path).name!r}, but the "
        f"attribution rule looks for one of {sorted(br.own_step_logs(stack))} — a rename here "
        f"silently disables every automatic freeze")


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
    assert msg.startswith("STACK-REGRESSION stack=kiss phase=readiness\n")
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
def test_a_readiness_timeout_without_evidence_blames_no_stack(tmp_path, command):
    msg = _readiness_failure(command, tmp_path)
    assert "STACK-REGRESSION" not in msg


@pytest.mark.parametrize("command", [
    "true",                                                                  # exited, drew nothing
    "printf 'Traceback (most recent call last):\\n'; sleep 30",             # drew a traceback
    "printf 'ModuleNotFoundError: no module named x\\n'; sleep 30",         # drew an exception line
])
def test_a_readiness_failure_with_evidence_of_breaking_is_marked(tmp_path, command):
    msg = _readiness_failure(command, tmp_path)
    assert "STACK-REGRESSION stack=reticulum phase=readiness" in msg


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


def test_a_start_that_times_out_is_not_retried_and_carries_its_stack(tmp_path):
    """A test body is never re-run because it failed: a hung first start fails, unmarked, with
    the thread dump of where it hung — even when a second start would have drawn."""
    command, counter = _counted(tmp_path, _hang_in_python(), "printf 'READY'; sleep 30")
    msg = _readiness_failure(command, tmp_path, expect="READY")
    assert counter.read_text().count("start") == 1
    assert "STACK-REGRESSION" not in msg
    assert "Fatal Python error" in msg and "time.sleep" in msg


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
    "printf 'Traceback (most recent call last):\\n'; sleep 30",         # drew a traceback
])
def test_a_start_that_shows_it_is_broken_is_never_retried(tmp_path, first):
    command, counter = _counted(tmp_path, first, "printf 'READY'; sleep 30")
    msg = _readiness_failure(command, tmp_path, expect="READY")
    assert counter.read_text().count("start") == 1
    assert "STACK-REGRESSION stack=reticulum phase=readiness" in msg


def test_the_program_runs_without_core_dumps(tmp_path):
    import os

    from lhpc_testlab.release import pty_readiness
    limit = tmp_path / "core-limit"
    pty_readiness(f"ulimit -c > {limit}; printf 'READY'; sleep 30", dict(os.environ), "READY",
                  ready_timeout=5, hold=0.2)
    assert limit.read_text().strip() == "0"


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
