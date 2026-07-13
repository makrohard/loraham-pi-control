"""Grouped dependency diagnosis for a stack — read-only, bounded, no network.

Three kinds, kept strictly separate (LHPC NEVER installs system packages itself —
every unmet system prerequisite is presented as an exact copy/pasteable command the
OPERATOR runs manually):

  * ``system``  — declared `require` prerequisites (packages, headers, device nodes);
  * ``build``   — `build_requires` source checkouts this component's build consumes
                  (e.g. loraham-daemon -> RadioLib at src/RadioLib);
  * ``runtime`` — `depends_on` start-ordering dependencies (components that must be
                  running first).
"""

from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import GROUP_MISSING_HINT, GROUP_RESTART_CMD, GROUP_RESTART_HINT

NOT_EXECUTED_NOTE = "not executed by LHPC — run it yourself"


@dataclass(frozen=True)
class DepItem:
    kind: str            # "system" | "build" | "runtime"
    component: str       # the component declaring the dependency
    label: str           # human description of WHAT is needed
    satisfied: bool
    detail: str = ""     # current state / why unsatisfied
    install_cmd: str = ""  # exact operator command ("" when none applies)
    note: str = NOT_EXECUTED_NOTE
    runtime: bool = False  # run-time capability (e.g. group membership) — "grant" not "install"
    restart_pending: bool = False  # groups grant CONFIGURED but not yet EFFECTIVE — restart, not usermod


def stack_report(lifecycle, paths, stacks, stack_id: str, comp_index: dict) -> list:
    """Every dependency of `stack_id`'s components, grouped by kind. `lifecycle`
    supplies the bounded `missing_requirements` probe; `comp_index` maps component
    id -> Component manifest-wide (for build/runtime edge resolution)."""
    stack = next((s for s in stacks if s.id == stack_id), None)
    if stack is None:
        return []
    out: list = []
    seen_sys: set = set()
    for c in stack.components:
        missing = lifecycle.missing_requirements(c)
        for req in c.requires:
            key = req.install or req.cmd or req.check_file
            if not key or key in seen_sys:
                continue
            seen_sys.add(key)
            sat = req not in missing
            # A groups grant that is configured but not yet effective (restart pending) is still
            # unsatisfied, but the fix is a restart, not another usermod — swap the detail + suppress
            # the grant command.
            pending = (not sat) and bool(req.groups) and lifecycle.group_grant_pending(req)
            if sat:
                detail = "present"
            elif req.groups:                       # state-specific, never both at once
                detail = GROUP_RESTART_HINT if pending else GROUP_MISSING_HINT
            else:
                detail = f"missing: {req.check_file or req.cmd}"
            out.append(DepItem(
                kind="system", component=c.id,
                label=req.note or req.cmd or req.check_file,
                satisfied=sat,
                detail=detail,
                # restart-pending shows the copyable restart command (re-running usermod would not help);
                # a genuinely-missing grant shows the usermod grant command.
                install_cmd=GROUP_RESTART_CMD if pending else (req.install or ""),
                runtime=bool(req.groups), restart_pending=pending))
        for dep_id in c.build_requires:
            dep = comp_index.get(dep_id)
            present = bool(dep and dep.source
                           and paths.resolve_source(dep.source.path).is_dir())
            out.append(DepItem(
                kind="build", component=c.id,
                label=f"{dep_id} source checkout"
                      + (f" ({dep.source.path})" if dep and dep.source else ""),
                satisfied=present,
                detail=("installed" if present else
                        "source not installed — install it before building"),
                install_cmd="" if present else f"lhpc install {_stack_of(stacks, dep_id)}",
                note=("consumed by the build" if present else NOT_EXECUTED_NOTE)))
        for dep_id in c.depends_on:
            dep = comp_index.get(dep_id)
            out.append(DepItem(
                kind="runtime", component=c.id,
                label=f"{dep_id} must be running first",
                satisfied=True,          # an ORDERING fact, not a current-state probe
                detail="start ordering handled by LHPC",
                note="runtime ordering"))
    return out


def _stack_of(stacks, comp_id: str) -> str:
    for s in stacks:
        if any(c.id == comp_id for c in s.components):
            return s.id
    return comp_id


def grouped(report: list) -> dict:
    """{kind: [DepItem...]} preserving order — the render shape for doctor/pages."""
    out: dict = {"system": [], "build": [], "runtime": []}
    for item in report:
        out.setdefault(item.kind, []).append(item)
    return out
