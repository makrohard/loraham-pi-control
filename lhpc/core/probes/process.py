"""Read-only process-identity probe.

Matches a process by structured identity, never by a bare whole-command-line
substring: the executable basename must equal `exec_name`, every `all_args`
pattern must appear within some argv token, and (if given) at least one
`any_args` pattern must appear within some argv token. Matching is scoped to
individual argv tokens (NUL-separated), so a pattern cannot accidentally span
two arguments.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from ..model import ProcessSpec
from .backends import System


@dataclass
class ProcessMatch:
    matched: bool
    pids: list[int]
    evidence: dict[str, str]


def _token_contains(argv: list[str], pattern: str) -> bool:
    return any(pattern in token for token in argv)


_PY_INTERP = re.compile(r"^python[0-9.]*$")


def matches(spec: ProcessSpec, argv: list[str]) -> bool:
    if not argv:
        return False
    exec_basename = posixpath.basename(argv[0])
    if exec_basename != spec.exec_name:
        # CONSOLE-SCRIPT form: a pip entry point named `exec_name` executes as
        # "<venv>/bin/pythonX.Y <venv>/bin/<exec_name> …" (the shebang interpreter
        # becomes argv[0]). Accept it only when argv[0] IS a python interpreter and
        # the SCRIPT token (argv[1]) has exactly the expected basename — still a
        # structured identity match, never a substring guess.
        if not (_PY_INTERP.match(exec_basename) and len(argv) >= 2
                and posixpath.basename(argv[1]) == spec.exec_name):
            return False
    if not all(_token_contains(argv, p) for p in spec.all_args):
        return False
    if spec.any_args and not any(_token_contains(argv, p) for p in spec.any_args):
        return False
    return True


def probe_process(system: System, spec: ProcessSpec) -> ProcessMatch:
    pids: list[int] = []
    sample = ""
    for pid, argv in sorted(system.procfs.cmdlines().items()):
        if matches(spec, argv):
            pids.append(pid)
            if not sample:
                sample = " ".join(argv)
    ev = {"exec": spec.exec_name}
    if pids:
        ev["pids"] = ",".join(str(p) for p in pids)
        ev["cmdline"] = sample
    return ProcessMatch(matched=bool(pids), pids=pids, evidence=ev)
