"""Helpers for the release lane, importable as `lhpc_testlab.release` — no path hacks, and
no importing a sibling test module (the suite's rule 7).

They drive the REAL `lhpc` executable and read LHPC's own predicates. Nothing here decides what
"correct" means; that is in the test cases, next to the evidence they assert.
"""
from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from lhpc.core import build_regression as br
from lhpc_testlab import data_path
from lhpc_testlab.testing import run_lhpc

REQUIRED_CASES_FILE = "required-release-cases.json"

# The lab box's well-known ports, named once for every lane (what each stack serves when up).
KISS_TCP = 8001
GRAYWOLF_UI = 8080
MESHCORE_COMPANION = 5000
MESHCORE_WEBUI = 8788
MESHCHAT_UI = 8790
REPEATER_DASHBOARD = 8000
MESHCOM_UI = 18083
MESHTASTIC_API = 4403

STACK_REGRESSION_PHASES = ("install", "build", "start", "readiness")
_STACK_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def stack_regression(stack: str, phase: str) -> str:
    r"""The one-line marker that makes a failure MACHINE-ATTRIBUTABLE to a single stack.

    It goes FIRST in a pytest assertion message, on a line of its own::

        assert wait_tcp(KISS_TCP, 120), (
            f"{stack_regression('kiss', 'readiness')}\n"
            "kiss is not serving KISS/TCP on 8001")

    **Grammar — a separate program parses this, so do not reformat it:**

        ``STACK-REGRESSION stack=<stack-id> phase=<install|build|start|readiness>``

    The literal word, then two ``key=value`` fields in this fixed order, one space between
    fields, no space around ``=``, and NOTHING after the phase on that line. ``<stack-id>`` is
    the manifest's STACK id (``[a-z0-9][a-z0-9_-]*``), never a component id, because a stack is
    what a release freezes. What this function returns is exactly::

        ^STACK-REGRESSION stack=([a-z0-9][a-z0-9_-]*) phase=(install|build|start|readiness)$

    A stack or phase outside that grammar raises here, rather than emitting a line the parser
    would silently fail to match.

    **How to parse it out of the JUnit.** SEARCH the `<failure>` text (its `message` attribute
    included) for::

        STACK-REGRESSION stack=([a-z0-9][a-z0-9_-]*) phase=(install|build|start|readiness)(?=\s|$)

    Do not anchor at the start of a line: pytest prefixes explanation lines with `E   ` and the
    `message` attribute with `AssertionError: `, and it may repeat the line in both. The marker
    is the FIRST thing in the assertion message and nothing follows it on its line, so the
    lookahead is the end of the field. Several matches of one failure are the same marker seen
    twice; a failure carrying markers for two different stacks would be a defect in this lane,
    not something to guess about.

    **What its presence means.** At THIS assertion the named stack's own install, build, start
    or readiness failed, so an automated release may freeze that one stack and let the others
    advance. Its absence means only "this case failed" — the conservative answer everywhere the
    failure may belong to something else. Nothing may infer a stack from a CASE NAME:
    `test_release_chat` also stops kiss and starts the lab's fake daemon.

    **Why first.** The `<failure>` element's `message` attribute and pytest's own short summary
    both LEAD with the assertion message, and these messages carry hundreds of lines of command
    output; on the first line the marker is readable without parsing a traceback, and anything
    pytest appends to an explanation lands after it rather than in front of it. A marked
    assertion's message is also a plain string, never a tuple: a tuple message is `repr`'d,
    which folds the marker's newline into a literal ``\n`` and leaves it on a line with other
    text — that is why `_start()` and `start_component()` no longer build one.

    **Marked sites** — each is that stack's own step, and nothing else:

    - `install_build()`'s build (`phase=build`), and ONLY where LHPC itself typed that
      component's build as failed AT A STEP THE RECIPE DECLARES ITS OWN (`attributable = true`
      in the manifest). The install is never marked, and neither is a fetching step: see that
      function.
    - a tool the stack's own install must have produced, missing afterwards (`phase=install`):
      Reticulum's `rnstatus`.
    - the lane's `_start()` (`phase=start`), except where the caller opts out.
    - `start_component()` (`phase=start`) when the caller names the owning stack — the optional
      components a stack start deliberately leaves alone (meshcore-webui, lxmd, sideband).
    - the readiness evidence of the stack under test (`phase=readiness`): its own port
      (`wait_tcp`/`wait_http`), `alive()` on its own component, `lhpc meshtastic --info`, and
      `rnstatus` listing Reticulum's LoRa interface.
    - `pty_readiness()` when the caller names the stack (`phase=readiness`): drew nothing, drew
      something other than what only a working program draws, or exited on its own.

    **Deliberately UNMARKED, and why** — an unattributed failure is reported as an ordinary
    failure and freezes nothing, which is the right outcome for all of these:

    - `stop()`: teardown, and usually of a DIFFERENT stack than the case is named for. A
      failed stop is nobody's regression, and it must never be swallowed either: the automated
      lane runs with `-x` (see `release_lane.release_what_a_failing_case_took`), so the run ends
      at the first failure and a cleanup that could not release a band surfaces as a teardown
      ERROR — an unmarked testcase of its own, which makes the consumer refuse to attribute
      anything from that run. Without `-x`, a retained band lands on the NEXT case's marked
      readiness assertion and freezes an innocent stack.
    - every install failure, and every build failure outside a step the recipe declares its
      own: see `install_build`.
    - `require_prerequisite()`: by construction — it fires exactly when the cause is elsewhere.
    - the fake-daemon start in the chat case (`_start(..., attribute=False)`): the lab's own
      fixture and a prerequisite of a different stack, not chat's regression.
    - `_meshcore_mode()`: writing a config value exercises LHPC's own config path.
    - `_artifact_intact()`: it compares installed files with the hashes recorded AT INSTALL, so
      a mismatch is local corruption of what was installed, not an upstream change.
    - `_web_client_is_the_pinned_one()` and `_artifact_records_the_manifest_inputs()`: these
      fail when a web/CLI pin moved and the artifact was not republished. The remedy is a
      republish; freezing the stack would hide the real cause.
    - the `gui_startable()` assertions (the Voice GTK variant, Sideband) and the lane's
      display-state preconditions: they depend on this box's display and toolkit as well as on
      the stack, so a lab that lost its display would freeze an innocent stack.
    - `pty_readiness()`'s SIGTERM escalation: cleanup, and it can equally be this harness's
      process group rather than the program.
    - the negative assertion in `test_release_meshcore_repeater` ("repeater-only mode is hosting
      a companion"): mode wiring between LHPC's config and the upstream repeater, and not one of
      the four phases.
    - `test_release_identity_matches_candidate_manifest`: ONE assertion over every stack; its
      message names as many stacks as drifted, so it can never mean "freeze this one".
    - `test_release_lane_reports_exactly_the_required_cases` and
      `test_release_gui_predicate_names_only_gui_components`: lane hygiene and a predicate over
      all stacks — neither is a stack's own step.

    **The one known coupling.** Graywolf is started over the RUNNING KISS chain, so a kiss
    regression can also show up as a marked graywolf start or readiness. It stays marked,
    because the assertion is graywolf's own step and graywolf is exactly the kind of stack an
    upstream move breaks. What makes it readable is that kiss has its own earlier case, which
    fails first and carries `stack=kiss`: a run in which both are marked names its own cause,
    where dropping graywolf's marker would leave the stack most likely to regress unattributed.
    Every other prerequisite in the lane is either the same stack (the MeshCore CLI over its
    companion, NomadNet over Reticulum) or unmarked (the fake daemon).
    """
    if phase not in STACK_REGRESSION_PHASES:
        raise ValueError(f"phase {phase!r} is not one of {STACK_REGRESSION_PHASES} — the "
                         "parser matches these four and nothing else")
    if not _STACK_ID_RE.fullmatch(stack):
        raise ValueError(f"stack {stack!r} is not a stack id ({_STACK_ID_RE.pattern})")
    # The grammar itself lives in the controller, so the builder writes the same bytes.
    return br.marker_line(stack, phase)


def required_release_cases() -> tuple[str, ...]:
    """The case names the release lane must report, as stated by the file that IS the list.

    A count is not a contract: the automated release used to accept any fourteen passing cases
    named `test_release_*`, so renaming or replacing one kept the count while the stack it
    proved silently stopped being proved. The list lives in this package's data rather than in
    Python because its other consumer is the release automation in a different repository,
    which reads it out of the checkout without importing anything.
    """
    import json
    doc = json.loads(data_path(REQUIRED_CASES_FILE).read_text())
    return tuple(doc["required_cases"])


def missing_required_cases(reported) -> list[str]:
    """Which required cases `reported` does not contain — the check over the lane's JUnit,
    written here so the lane and the automation cannot disagree about it. Sorted, so a failure
    message reads the same twice."""
    return sorted(set(required_release_cases()) - set(reported))


def stop(env: dict, *stacks: str, require: bool = True) -> None:
    """Release the bands before the next stack claims them.

    A stop that failed is not housekeeping: the next stack will collide with whatever is still
    holding the band, and the failure it then reports will name the wrong thing. `require=False`
    is for stacks that may not be running at all.
    """
    for sid in stacks:
        r = run_lhpc(env, "stack", "stop", sid, "--yes", timeout=300)
        if require and r.returncode != 0:
            raise AssertionError(f"stopping {sid} failed (rc {r.returncode}): "
                                 f"{r.stdout[-800:]}")


# A stack's header line in `lhpc status`: `[kiss] LoRaHAM KISS TNC  (running)`. Merely finding
# the stack's NAME in the output is true of a stopped stack too, and of one never installed.
# The state is the LAST parenthesised word on the line, anchored at the end. Forbidding "(" in
# the middle instead made every stack whose DISPLAY NAME carries parentheses invisible —
# "MeshCore (OpenHop)", "Reticulum (RNS)", "MeshCom (QEMU)" — so three of eight stacks read as
# absent, and a prerequisite check on any of them failed on every run. `[controller] … (up to
# date)` still does not match, because a state has no spaces in it.
_STACK_STATE = re.compile(r"^\[([a-z0-9][a-z0-9_-]*)\].*\(([a-z-]+)\)\s*$", re.MULTILINE)


def stack_states(env: dict) -> dict:
    """Every stack's run state from ONE `lhpc status`: `{stack id: state}`."""
    out = run_lhpc(env, "status", timeout=180).stdout
    return {m.group(1): m.group(2) for m in _STACK_STATE.finditer(out)}


def state_of(env: dict, stack: str) -> str:
    """The stack's own run state as `lhpc status` reports it, e.g. `running` or `stopped`."""
    return stack_states(env).get(stack, "")


def running(env: dict, stack: str) -> bool:
    """Running, and not merely mentioned. `degraded` is not running: it is the state LHPC uses
    when the process is up but an endpoint it promised is not."""
    return state_of(env, stack) == "running"


def holding_stacks(env: dict) -> set:
    """Every stack that is HOLDING something — a process, and with it a radio band.

    NAMED states, not "anything but stopped". A fresh lab root reports every stack as
    `not-installed`, which is not holding anything — with the loose rule the stack a case was
    about was already counted before the case started, the before/after diff came out empty, and
    the cleanup issued no stop at all. That is every first case of every chain.

    `degraded` counts: it is a component that IS up with an endpoint it promised missing, and it
    holds the band exactly like a healthy one. So does `failed`, which can leave a process behind.
    """
    return {sid for sid, state in stack_states(env).items()
            if state in ("running", "degraded", "failed")}


def require_prerequisite(env: dict, *stacks: str, left_by: str) -> None:
    """Fail — UNMARKED, and BEFORE this case's own steps — when a stack this case INHERITS from
    an earlier one is not running.

    The lane chains deliberately: `test_release_kiss` leaves the KISS chain up for
    `test_release_graywolf`, and `test_release_chat` leaves the fake daemon up for Voice. When
    the earlier case aborted (or the per-case cleanup released what it had taken), the later case
    used to fail at its OWN marked start or readiness assertion instead — and an automated
    release then froze a stack that never had a chance to run. Saying so here, with no marker,
    keeps the freeze on the case that actually broke and tells a reader which one that was.
    """
    down = sorted(s for s in stacks if not running(env, s))
    assert not down, (
        f"prerequisite not met: {', '.join(down)} is not running. {left_by} leaves it up for "
        f"this case, so this failure belongs to that case — it is NOT a regression of the stack "
        f"under test here, and nothing about this stack was proved either way.")


# LHPC's OWN typed per-component build failure, as `lhpc build` renders it from the component's
# JobState:  "  [failed] build loraham-kiss-tnc (rc 1, log …/logs/build-loraham-kiss-tnc.log)".
# `succeeded` and `timeout` are the only other states, and every REFUSAL (not installed, GUI
# toolkit absent, binary channel, admission, unresolved journal, lock contention) returns before
# a build step runs and prints no such line at all. The LOG PATH names the step that failed —
# `Lifecycle.build` logs step `i` of a multi-step component as `build-<component>-<i>` — which is
# the consumer half of the attribution contract below. A refusal typed before any step ran
# carries no log path, so this pattern does not match it either.
_TYPED_BUILD_FAILURE = br.TYPED_BUILD_FAILURE


def _own_recipe_step_logs(svc, stack: str) -> set:
    """The log names of the build steps this stack's recipe declares as its OWN.

    The rule itself lives in `lhpc.core.build_regression`, because the binary builder applies the
    same one to the same `lhpc build` output inside its container. Two copies of it would drift,
    and the direction they drift in is "freeze an upstream pin over a broken package index".
    """
    return br.own_step_logs(svc.stack(stack))


def install_build(env, svc, stack: str, *, build: bool = True,
                  timeout: float = 1800.0) -> None:
    """Install on the DEFAULT channel (binary where published, else the pin) and build.

    A binary install has no source tree, so `lhpc build` is deliberately not run there —
    the binary channel refuses it, and a refusal is not evidence of anything.

    **What is marked here, and the rule.** A nonzero exit is NOT by itself this stack's
    regression, and a marker freezes the stack's pins until someone lifts it. So the only marked
    failure is one LHPC itself typed as a component's own build failing AT A STEP THE RECIPE
    DECLARES ITS OWN — `attributable = true`, which a manifest step carries only when it
    compiles, patches or checks what earlier steps already fetched. That is a producer/consumer
    contract, not a diagnosis of the error text, and it is what the typed line alone cannot
    give: `lhpc build meshcore` fails exactly the same way when PyPI is unreachable during one
    of the recipe's `pip install` steps as when the patch no longer applies, and freezing four
    MeshCore pins over a package mirror is not recoverable in a week. A declared step also runs
    only AFTER its prerequisites succeeded, so reaching it already rules the earlier fetches out.

    Everything else stays UNMARKED and is reported as an ordinary failure — which already means
    "no freeze", the right outcome for all of these:

    - EVERY install failure, without exception. An install clones and downloads, and
      `lhpc install` reports it as free-form plan-action text (`[failed] clone … — <what git
      said>`), in which "DNS did not resolve", "the clone timed out", "the disk is full" and
      "the pinned commit is gone from the remote" are the same shape of line. Nothing there
      separates the environment from the stack, and a missed freeze is recoverable where a
      wrong one is not.
    - every FETCHING build step: pip, git, `pio pkg install`, a release download. They fail
      typed exactly like a compile does, and the cause is as often the network as the recipe.
      A stack whose whole build is a fetch (graywolf's `.deb`, Reticulum's wheels) therefore
      has nothing marked at all — deliberately, and the audit's own instruction: prefer marking
      less.
    - a build that TIMED OUT (`[timeout] build …`). The per-component ceiling is measured for
      the slowest supported board, so crossing it says more about this runner than about the
      recipe.
    - every build REFUSAL, and a cancelled run. LHPC executed no build step, or stopped one
      mid-way on request, so nothing about the recipe was proved in either direction. Both are
      typed before a step's log exists, and the pattern above needs that log to name a step.
    - an unknown shape: anything this lane cannot resolve to a declared step is not attributed.
    """
    r = run_lhpc(env, "install", stack, "--yes", timeout=timeout)
    assert r.returncode == 0, (
        f"installing {stack} failed (rc {r.returncode}) — UNMARKED: an install clones and "
        f"downloads, so this is as likely to be the network as the stack: "
        f"{r.stdout[-2000:]}\n{r.stderr[-800:]}")
    if build and not on_binary(env, stack):
        r = run_lhpc(env, "build", stack, "--yes", timeout=timeout)
        own = _own_recipe_step_logs(svc, stack)
        typed = next((m for m in _TYPED_BUILD_FAILURE.finditer(r.stdout)
                      if Path(m.group(2)).name in own), None)
        mark = f"{stack_regression(stack, 'build')}\n" if typed else ""
        why = (f"LHPC typed {typed.group(1)}'s own build as failed at {Path(typed.group(2)).name}"
               f", a step this recipe declares its own" if typed else
               "UNMARKED: no step this recipe declares its own (`attributable`) failed, so the "
               "cause is not provably the recipe — a fetch, a refusal or a timeout is not")
        assert r.returncode == 0, (
            f"{mark}building {stack} failed (rc {r.returncode}) — {why}: "
            f"{r.stdout[-2000:]}\n{r.stderr[-800:]}")


def on_binary(env: dict, stack: str) -> bool:
    """Installed from an artifact? The receipt is the authority — the same file
    `lhpc status --versions` renders `binary@…` from."""
    root = Path(env["LHPC_RUNTIME_ROOT"])
    return (root / "state" / "binary" / f"{stack}.json").is_file()


def wait_tcp(port: int, timeout: float, host: str = "127.0.0.1") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection((host, port), timeout=5).close()
            return True
        except OSError:
            time.sleep(1.0)
    return False


def wait_for(predicate, timeout: float, every: float = 5.0) -> bool:
    """Poll `predicate()` until it is true (True) or `timeout` seconds pass (False)."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(every)


def wait_http(url: str, timeout: float, accept=(200,)) -> int:
    """Last status seen; 0 when the endpoint never answered."""
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                last = r.status
                if r.status in accept:
                    return r.status
        except urllib.error.HTTPError as exc:
            last = exc.code
            if exc.code in accept:
                return exc.code
        except OSError:
            pass
        time.sleep(2.0)
    return last


def _matches(drawn: bytes, expect: str) -> bool:
    """Has the program drawn what only a working one draws? Matched over EVERYTHING read so
    far, because a TUI paints in several writes and the pattern can straddle two of them."""
    return bool(re.search(expect, drawn.decode("utf-8", "replace"), re.IGNORECASE | re.DOTALL))


def pty_readiness(command: str, env: dict, expect: str, *, ready_timeout: float = 60.0,
                  hold: float = 5.0, stack: str | None = None) -> bytes:
    """Run an INTERACTIVE component the way an operator does — on a real terminal — and prove
    it: it must draw its OWN screen, still be running afterwards, and exit CLEANLY when asked.
    Returns what it drew.

    `expect` is a regular expression the drawn output must match, and it is REQUIRED rather
    than defaulted: every caller once omitted it, so "wrote something and is still up" passed a
    program that printed an error message and slept. Pass something only the working program
    draws — a menu bar, its own status line — never a word an error also contains.

    Cleanup is part of the proof, not tidying: a component that has to be killed did not exit,
    and a lane that silently escalates to SIGKILL would hide a hung application.

    The controller never starts these itself, so the command comes from the production renderer
    (`manual_start_command`), not from a literal here.

    A real terminal means more than a pty pair: a curses program needs `TERM` and a non-zero
    window size, and exits immediately without them. CI has neither, so both are supplied here —
    otherwise this would measure the runner's environment instead of the build.

    `stack` names the stack this component belongs to, and marks the three READINESS assertions
    — drew nothing, drew the wrong thing, exited on its own — with `stack_regression`. It is
    validated before the program is started, so a typo fails immediately instead of an hour in.
    The SIGTERM escalation below stays unmarked on purpose: see `stack_regression`.
    """
    import fcntl
    import pty
    import struct
    import termios

    mark = f"{stack_regression(stack, 'readiness')}\n" if stack else ""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    env = dict(env)
    # Present-but-empty is the CI case, and ncurses reports it as TERM="unknown" and exits.
    if env.get("TERM", "") in ("", "unknown", "dumb"):
        env["TERM"] = "xterm-256color"
    env["LINES"], env["COLUMNS"] = "40", "120"
    # `manual_start_command` renders what the operator PASTES INTO A SHELL — it carries `cd`,
    # `&&` and a trailing `stty sane`. Run it through a shell on a real terminal, exactly as the
    # documentation tells the operator to; splitting it into argv would execute `cd` as a program.
    proc = subprocess.Popen(["/bin/bash", "-lc", command], stdin=slave, stdout=slave,
                            stderr=slave, env=env, start_new_session=True, close_fds=True)
    os.close(slave)
    drawn = b""
    try:
        import select
        deadline = time.monotonic() + ready_timeout
        # Read until what we are waiting for is on the screen, not until some byte count: a
        # full-screen TUI paints in several writes and the wanted line can be the last of them,
        # so stopping at the first chunk would fail a healthy program.
        while time.monotonic() < deadline and not _matches(drawn, expect):
            r, _, _ = select.select([master], [], [], 1.0)
            if r:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                drawn += chunk
            if proc.poll() is not None:
                break
        tail = drawn[-600:].decode("utf-8", "replace")
        assert drawn, (f"{mark}{command!r} drew nothing on its terminal within "
                       f"{ready_timeout} s")
        assert _matches(drawn, expect), (
            f"{mark}{command!r} did not draw anything matching {expect!r} within "
            f"{ready_timeout} s. It drew: {tail!r}")
        time.sleep(hold)
        assert proc.poll() is None, (
            f"{mark}{command!r} exited on its own (rc={proc.returncode}) — not a running app. "
            f"It drew: {tail!r}")
        killed = False
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            killed = True
        os.close(master)
    assert not killed, (f"{command!r} ignored SIGTERM and had to be killed — it does not exit "
                        f"cleanly")
    return drawn


def start_component(env: dict, component: str, timeout: float = 900.0, *,
                    stack: str | None = None) -> None:
    """Start ONE component by name.

    An `optional` component is not started by `lhpc stack start <stack>` — Sideband, LXMD and
    the MeshCore Web UI among them. Naming it is how an operator starts it, and it is the only
    way this lane can claim it starts at all.

    `stack` is the stack that OWNS the component; naming it marks the failure with
    `stack_regression` (`phase=start`), because an optional component that stops starting is
    that stack's own regression. The marker names the stack, never the component id.
    """
    mark = f"{stack_regression(stack, 'start')}\n" if stack else ""
    r = run_lhpc(env, "stack", "start", component, "--yes", timeout=timeout)
    assert r.returncode == 0, (f"{mark}starting {component} failed (rc {r.returncode}): "
                               f"{r.stdout[-2000:]}\n{r.stderr[-500:]}")


def alive(env: dict, component: str) -> bool:
    """Is this COMPONENT running, as LHPC's own status reports it?"""
    out = run_lhpc(env, "status", timeout=120).stdout
    m = re.search(rf"^\s+{re.escape(component)}\s+([a-z-]+)", out, re.MULTILINE)
    return bool(m) and m.group(1) == "running"


def gui_startable(svc, stack_id: str, comp_id: str) -> bool:
    """LHPC's OWN predicate for 'can this GUI component run here'. Never re-derived: the
    build skip, the start gate and the image's composition check all read this one."""
    st = svc.stack(stack_id)
    comp = next((c for c in st.components if c.id == comp_id), None)
    if comp is None:
        return False
    if comp_id in svc.gui_unavailable_components(st):
        return False
    return not (svc.needs_display(comp) and not svc.display_available())
