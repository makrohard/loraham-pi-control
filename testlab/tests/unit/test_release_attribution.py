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
    rel.run_lhpc = lambda env, *a, **kw: next(it)

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
