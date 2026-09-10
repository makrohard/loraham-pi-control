"""Was a failed build this stack's OWN regression, or the environment's?

One rule, two callers. The release-verification lane asks it about a lane build; the binary
builder asks it about the same `lhpc build` run inside its container, and an automated release
freezes a pin on the answer. Written down once because two implementations of this would drift,
and the direction they drift in is "freeze an upstream pin over a broken package index".

The contract has a producer half and a consumer half:

* **Producer** — a manifest build step may declare `attributable = true`. It means: a failure
  here is this component's own build regression. It compiles, patches or checks what earlier
  steps already fetched, and reaches no network itself. Fetching steps never carry it.
* **Consumer** — `Lifecycle.build` types a failed step as `[failed] build <component> (rc N, log
  <path>)`, and logs step `i` of a multi-step component as `build-<component>-<i>`. So the LOG
  NAME says which step failed, and only a name a declared step owns may attribute.

Everything else stays unattributed on purpose: a refusal typed before any step ran, a timeout, a
cancellation, and a completion-marker write that failed after every command succeeded — that one
carries no log path at all, precisely so it cannot be read as evidence against a pin.
"""
from __future__ import annotations

import re

MARKER = "STACK-REGRESSION"

# `[failed]` only. A timeout is `[timeout]`, and a refusal typed before any step ran carries no
# log path, so neither shape matches here.
TYPED_BUILD_FAILURE = re.compile(r"\[failed\] build (\S+) \(rc \d+, log (\S+)\)")


def marker_line(stack: str, phase: str) -> str:
    """The one line an automated release parses to know which stack to hold."""
    return f"{MARKER} stack={stack} phase={phase}"


def own_step_logs(stack) -> set:
    """Log names of the build steps this stack's recipe declares as its own."""
    names = set()
    for comp in stack.components:
        n = len(comp.build_steps)
        for i, step in enumerate(comp.build_steps):
            if step.get("attributable"):
                names.add(f"build-{comp.id}-{i}.log" if n > 1 else f"build-{comp.id}.log")
    return names


def attributed_step(stack, output: str):
    """The typed failure naming a step this recipe owns, or `None`.

    `stack` is a parsed `Stack`; `output` is whatever `lhpc build` printed. Returns the regex
    match so a caller can name the component and the log in its own message.
    """
    from pathlib import PurePosixPath
    own = own_step_logs(stack)
    return next((m for m in TYPED_BUILD_FAILURE.finditer(output or "")
                 if PurePosixPath(m.group(2)).name in own), None)
