"""Read-only source/build/version probe.

Checks a configured LOCAL source path against its pinned commit using bounded
local git commands only. It never fetches, pulls, resets, cleans or scans the
repository contents recursively. A pin match is reported factually; it is NOT a
"confirmed working" judgement (that requires validation evidence, not just a pin).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import SourceSpec, SourceState
from .backends import System

_TIMEOUT_S = 3.0


@dataclass
class SourceProbe:
    state: SourceState
    head: str = ""               # full HEAD commit, when resolvable
    version: str = ""            # human version: `git describe --tags --always`
    evidence: dict[str, str] = field(default_factory=dict)


def parse_status_v2(text: str) -> tuple[str, bool]:
    """(head, dirty) from `git status --porcelain=v2 --branch` output. `head` is the
    `# branch.oid` value ("" for an unborn `(initial)` branch or a missing header); `dirty`
    is True when any entry line (ordinary `1`, rename/copy `2`, unmerged `u`) is present —
    headers (`#`) and, with -uno, nothing else appear otherwise. Detached HEAD
    (`# branch.head (detached)`) still carries its oid."""
    head, dirty = "", False
    for line in text.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2].strip()
            head = "" if oid == "(initial)" else oid
        elif line and not line.startswith("#"):
            dirty = True
    return head, dirty


def probe_source(system: System, spec: SourceSpec, abs_path: str) -> SourceProbe:
    ev = {"path": abs_path}
    if not system.fs.exists(abs_path):
        ev["state"] = "missing"
        return SourceProbe(SourceState.MISSING, evidence=ev)
    if not system.fs.exists(f"{abs_path}/.git"):
        ev["state"] = "not-a-repo"
        return SourceProbe(SourceState.NOT_A_REPO, evidence=ev)

    # ONE `git status --porcelain=v2 --branch` answers both questions the probe used to ask
    # git separately (HEAD via rev-parse, tracked dirtiness via status): `# branch.oid` is the
    # full HEAD commit, and every non-header line is a TRACKED-file change (-uno excludes
    # untracked files: build artifacts, *.log and app runtime data the programs write into
    # their own repo are NOT source edits). Two subprocesses per source instead of three.
    status_res = system.runner.run(
        ["git", "-C", abs_path, "status", "--porcelain=v2", "--branch", "--untracked-files=no"],
        timeout=_TIMEOUT_S,
    )
    if status_res.timed_out:
        ev["error"] = "git status timed out"
        return SourceProbe(SourceState.UNKNOWN, evidence=ev)
    if status_res.returncode != 0:
        ev["error"] = (status_res.stderr or "git status failed").strip()[:120]
        return SourceProbe(SourceState.UNKNOWN, evidence=ev)
    head, dirty = parse_status_v2(status_res.stdout)
    if not head:
        # An unborn branch (`(initial)`) or an unparseable answer: no HEAD to compare -> UNKNOWN.
        ev["error"] = "git status: no HEAD commit"
        return SourceProbe(SourceState.UNKNOWN, evidence=ev)
    ev["head"] = head

    desc = system.runner.run(
        ["git", "-C", abs_path, "describe", "--tags", "--always", "--dirty"],
        timeout=_TIMEOUT_S,
    )
    version = desc.stdout.strip() if desc.returncode == 0 else head[:12]
    ev["version"] = version

    if dirty:
        ev["dirty"] = "yes"
        return SourceProbe(SourceState.DIRTY, head=head, version=version, evidence=ev)

    if spec.pin_commit and head == spec.pin_commit:
        ev["pin"] = "match"
        return SourceProbe(SourceState.MATCH, head=head, version=version, evidence=ev)
    if spec.pin_commit:
        ev["pin"] = "differs"
        ev["pinned"] = spec.pin_commit
        return SourceProbe(SourceState.DIFFERS, head=head, version=version, evidence=ev)
    # No pinned commit recorded, tracked tree clean: a clean working copy on its
    # branch — report MATCH (clean), not UNKNOWN (which means the probe failed).
    ev["pin"] = "none"
    return SourceProbe(SourceState.MATCH, head=head, version=version, evidence=ev)
