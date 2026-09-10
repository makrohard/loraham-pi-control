"""The release lane's pytest HARNESS, importable as `lhpc_testlab.release_lane` — the two
things that keep a failure attributable across cases, kept out of the lane's `conftest.py` so
they can be exercised on their own (a test may not import a sibling test module).

`lhpc_testlab.release` next door holds the per-assertion helpers. This module holds what the
lane needs *between* cases: releasing what a failing case took, and owning the graphical session
so one case can run without one.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from lhpc_testlab.release import holding_stacks, stop


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Publish each phase's report on the item, so a fixture teardown can see whether the case
    FAILED. pytest offers no other way to make cleanup conditional on the outcome."""
    outcome = yield
    setattr(item, f"rep_{outcome.get_result().when}", outcome.get_result())


def case_failed(item) -> bool:
    return any(getattr(getattr(item, f"rep_{when}", None), "failed", False)
               for when in ("setup", "call"))


@pytest.fixture(autouse=True)
def release_what_a_failing_case_took(request):
    """Stop the stacks THIS case started — but only when it FAILED — and make a stop that could
    NOT stop something a visible teardown error.

    The lane chains on purpose (`test_release_kiss` leaves the KISS chain up for
    `test_release_graywolf`), so a case that PASSES must leave its stacks exactly as they are.
    A case that ABORTS is the opposite problem: its trailing `stop()` never runs, its stack keeps
    the lab's one radio pair, and the NEXT case then fails at its own MARKED readiness assertion
    — so an automated release freezes a stack that was never given a chance to start.

    On failure it releases EVERYTHING that is holding, not only what this case started. The
    difference is not academic: `test_release_reticulum` deliberately leaves Reticulum up for
    `test_release_reticulum_nomadnet`, so when nomadnet failed it had started nothing, the
    before-and-after diff was empty, nothing was stopped, and Reticulum kept the 868 band until
    `test_release_meshtastic` was refused it — a MARKED failure naming meshtastic for a fault two
    cases earlier. Observed exactly that way in run 34410321870.

    A passing case is still left alone, which is what keeps the chaining intact. Which stacks are
    holding is READ FROM LHPC, never bookkept, so it cannot drift out of step with the cases.

    **A failed stop RAISES here**, after every stack has been tried. It used to be swallowed
    (`require=False`), and that is unsafe in the one direction that matters: the band stayed
    held, the next case hit it and earned its OWN valid marker, and a release then froze that
    innocent stack with nothing anywhere saying the lab had failed to clean up. pytest reports a
    raising teardown as a separate, unmarked testcase error, and the consumer refuses to
    attribute anything from a run that carries an unmarked failure — which is the honest outcome
    for a lab that could not release a radio. The automated lane also runs with `-x`
    (`.github/workflows/testlab.yml`), so nothing runs after the first failure anyway; the raise
    is what makes a broken cleanup say so instead of ending the run quietly.
    """
    if "env" not in request.fixturenames:      # the lane-hygiene case touches no box
        yield
        return
    env = request.getfixturevalue("env")
    yield
    if not case_failed(request.node):
        return
    stuck = []
    for sid in sorted(holding_stacks(env)):
        print(f"[cleanup] {request.node.name} failed with {sid} still up — stopping it so the "
              f"next case is not judged on a band this one kept")
        try:
            stop(env, sid)
        except AssertionError as exc:
            stuck.append(str(exc))
    assert not stuck, ("teardown could not release what this case held, so the lab still holds a "
                       "radio band and NOTHING in this run may be attributed to a stack:\n"
                       + "\n".join(stuck))


class LabDisplay:
    """The lane's OWN X server — so one case can prove what LHPC does when there is NONE.

    LHPC chooses Voice's variant from `display_available()`, which globs for a live compositor
    socket. That is deliberately REAL host evidence, outside the injected System and with no
    environment override, and every production path agrees with it: on a box with a display the
    GTK app is what `lhpc stack start voice` starts, and the terminal variant is not offered at
    all — its launcher symlink is never materialised and a direct start of it is refused. So the
    only way to see BOTH variants start through production paths is to run one case on a box
    that genuinely has no graphical session.

    The lane therefore owns the server it runs under (the workflow used to start it beside
    pytest) and can take it down for exactly one case. `down()` REFUSES rather than pretends
    when a compositor this lane did not start is running: the terminal variant is then not
    proved here, and saying so is the honest outcome.
    """

    SCREEN = "1280x800x24"

    def __init__(self, display: str):
        self.display = display
        self.proc: subprocess.Popen | None = None

    @staticmethod
    def available() -> bool:
        """LHPC's own predicate, never re-derived here."""
        from lhpc.core.services import ControllerService
        return ControllerService.display_available()

    def _wait(self, want: bool, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.available() is want:
                return True
            time.sleep(0.25)
        return self.available() is want

    def up(self, timeout: float = 30.0) -> None:
        if self.available():
            return                                  # already serving: ours, or someone else's
        self.proc = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", self.SCREEN],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert self._wait(True, timeout), (
            f"Xvfb {self.display} did not open a compositor socket within {timeout} s — this "
            f"lane's GUI deliverables (the Voice GTK app, Sideband) cannot be proved without "
            f"one")

    def down(self, timeout: float = 30.0) -> None:
        assert self.proc is not None, (
            "a graphical session this lane did not start is running: it cannot be taken down "
            "from here, and Voice's terminal variant is only offered on a box without one. Run "
            "the lane in the lab container, where it owns its own Xvfb.")
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.proc = None
        # An X server unlinks its socket and lock on a clean exit; ours are ours to remove if it
        # did not, and either one left behind would keep `display_available()` true (or block the
        # restart) for a reason that has nothing to do with what the case measures.
        n = self.display.lstrip(":").split(".")[0]
        for leftover in (Path(f"/tmp/.X11-unix/X{n}"), Path(f"/tmp/.X{n}-lock")):
            with contextlib.suppress(OSError):
                leftover.unlink()
        assert self._wait(False, timeout), (
            "a compositor socket is still present after this lane's X server exited — something "
            "else on this box is serving a display, so Voice's terminal variant cannot be "
            "proved here")


@pytest.fixture(scope="session")
def display():
    """The graphical session the lane runs under, owned here so one case can remove it.

    NOT autouse: the lane's own `conftest.py` makes it so for the release lane only. Anything
    else that loads this module for the cleanup fixture must not start an X server it never
    asked for."""
    d = LabDisplay(os.environ.get("DISPLAY") or ":99")
    d.up()
    yield d
    if d.proc is not None:
        d.down()


@pytest.fixture
def headless_box(display):
    """This case runs on a box with NO graphical session; the next one gets it back."""
    display.down()
    try:
        yield
    finally:
        display.up()
