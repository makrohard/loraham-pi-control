"""The console Build's components: one round of the build plan, after the same GUI decision `lhpc build` takes.

W205: `lhpc build voice` skipped voice's GTK app on a box without GTK (its GUI preflight), but the console's
Build, a detached job per component, never asked and ran the GTK app's step, which failed in pkg-config.
A component whose GUI-only requirement is absent here (`gui_unavailable_components`) cannot be built: an
`optional` or `gui_optional` one is skipped and named, a mandatory one refuses the build.
"""
from __future__ import annotations

from . import build_plan

HINT = "needs a GUI toolkit that is not installed (opt-in: re-run bootstrap-deps.sh with --with-gui)"


def split(svc, comps) -> tuple[list, list, list]:
    """(keep, dropped, refused) of `comps`, in their order: refused = GUI-unavailable and mandatory, dropped =
    GUI-unavailable and optional or gui_optional, keep = the rest. The predicate `lhpc build` uses."""
    unavailable: set = set()
    for sid in dict.fromkeys(svc.stack_of(c.id) or c.id for c in comps):
        stack = svc.stack(sid)
        if stack is not None:
            unavailable |= set(svc.gui_unavailable_components(stack))
    keep, dropped, refused = [], [], []
    for c in comps:
        if c.id not in unavailable:
            keep.append(c)
        elif c.optional or c.gui_optional:
            dropped.append(c)
        else:
            refused.append(c)
    return keep, dropped, refused


def note(dropped) -> str:
    return f"Skipped {', '.join(c.id for c in dropped)}: {HINT}." if dropped else ""


def round_for(svc, requested, stack) -> tuple[list, str, str]:
    """(components, note, refusal) of one console Build of `requested` (`stack` for a stack target, else None).

    The build plan's selection and order; one round (a provider and its consumer never in the same round: the
    consumer would race the provider's artifact; while a provider is not built this round builds it and the
    operator builds again). A refusal ("" = none) means nothing is spawned."""
    comps, dropped, refused = split(svc, build_plan.select(requested, stack))
    if refused:
        return [], "", f"Refusing to build {', '.join(c.id for c in refused)}: {HINT}."
    if not comps:                       # every one skipped: say why; nothing at all: the caller's own refusal
        return [], "", note(dropped)
    comps, again = build_plan.web_round(build_plan.order(comps), svc.is_built)
    notes = [f"Building the build dependency first ({', '.join(c.name for c in comps)}) — run Build again "
             "to build the rest once it finishes."] if again else []
    return comps, " ".join(notes + ([note(dropped)] if dropped else [])), ""
