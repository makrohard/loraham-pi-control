"""Release lane: every stack this release may repin is installed at the SELECTED commit,
built, started and verified on a fresh lab root — then its installed identity is proved
against the candidate manifest.

Opt-in (`LHPC_RELEASE_VERIFY=1`), because it installs and builds every stack. The lane exists
so an automated pin release has evidence that the pins it moves actually run; the box test
matrix stays the minor release's proof.

Case names are the contract: `test_release_<stack>[_<variant>]`, and the required set is stated
in `lhpc_testlab/data/required-release-cases.json`. A release automation maps a moved component
to its stack through the manifest and requires that stack's case to have PASSED — a skip is not
proof, and a count is not proof either: fourteen renamed cases satisfy a count while the stack
they proved goes unproved. `test_release_lane_reports_exactly_the_required_cases` binds the list
to this module.

A case name is not evidence of WHICH stack broke: a case also stops the previous stack, starts
the lab's fake daemon and shares one radio pair. So a failure at a genuine per-stack step —
that stack's own install, build, start or readiness — additionally carries the one-line
`STACK-REGRESSION` marker documented in `lhpc_testlab.release.stack_regression`, and every
other failure deliberately carries none: an automated release freezes a stack only on a marked
failure, and reports an unmarked one as an ordinary failure.

RUN IT WITH `-x` (the CI job does): the cases chain over one radio pair, so after the first
failure nothing later is judged in a state that means anything, and a release reads this lane's
JUnit to decide what to freeze. A stopped run may still carry the attribution for its first
regression; it can never satisfy the publication gate, which needs every required case to pass.

Two fixtures here keep that attribution honest across cases. `release_what_a_failing_case_took`
stops the stacks a FAILING case started, so its band never lands on the next case's marked
readiness assertion, and RAISES when a stop could not — a visible teardown error, on which the
consumer refuses to attribute anything; passing cases are untouched, because the lane chains on
purpose. `display` owns the lane's X server, so the one case that must run on a box WITHOUT a
graphical session can have exactly that.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# The lane's between-case harness lives in the package, so it can be tested without importing a
# test module: importing the hook and the fixtures HERE is what registers them for this lane.
from lhpc_testlab.release_lane import (  # noqa: F401
    display,
    headless_box,
    pytest_runtest_makereport,
    release_what_a_failing_case_took,
)
from lhpc_testlab.testing import LabServer, lab_env, run_lhpc

from lhpc.core.manifest import default_manifest_path, load_manifest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("LHPC_RELEASE_VERIFY") == "1":
        return
    skip = pytest.mark.skip(reason="release lane is opt-in: set LHPC_RELEASE_VERIFY=1")
    for item in items:
        if str(item.fspath).replace("\\", "/").find("tests/release") != -1:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def lab(tmp_path_factory):
    """A FRESH lab root: no operator known-working records, so `pinned` resolves to the
    candidate manifest pin and nothing carries over from a previous run."""
    root = Path(tmp_path_factory.mktemp("release") / "runtime")
    server = LabServer(root)
    server.init_and_reset()
    yield server
    # Every stack the packaged manifest declares, never a hand-kept list; the fake daemon last,
    # because it is the provider the others run over.
    stacks = [s.id for s in load_manifest(default_manifest_path())]
    for sid in sorted(stacks, key=lambda sid: sid == "daemon"):
        run_lhpc(server.env, "stack", "stop", sid, "--yes", timeout=300)


@pytest.fixture(scope="session", autouse=True)
def lane_display(request):
    """The whole lane runs under the graphical session it owns — the GTK Voice app and Sideband
    are release deliverables and need one. `headless_box` removes it for the single case that
    must run without."""
    return request.getfixturevalue("display")   # by name: `display` is imported, not shadowed


@pytest.fixture(scope="session")
def env(lab):
    return lab_env(lab.root)


@pytest.fixture(scope="session")
def svc(lab):
    """An in-process ControllerService over the SAME lab root — used to read the manifest,
    render an interactive component's own start command and run the production identity
    verifiers. Never to mutate: every lifecycle step goes through the real executable.

    The two env keys are set for the WHOLE lane on purpose (this is a session fixture in an
    opt-in lane that owns its process): the service latches lab mode at construction and every
    later read goes through the same latched instance. They are restored at teardown."""
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LHPC_SYSTEM_PROVIDER", "lhpc_testlab.provider:build")
        mp.setenv("LHPC_TESTLAB", "1")
        yield ControllerService(paths=Paths(runtime_root=lab.root))

