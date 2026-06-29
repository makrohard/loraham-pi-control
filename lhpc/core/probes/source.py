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


def probe_source(system: System, spec: SourceSpec, abs_path: str) -> SourceProbe:
    ev = {"path": abs_path}
    if not system.fs.exists(abs_path):
        ev["state"] = "missing"
        return SourceProbe(SourceState.MISSING, evidence=ev)
    if not system.fs.exists(f"{abs_path}/.git"):
        ev["state"] = "not-a-repo"
        return SourceProbe(SourceState.NOT_A_REPO, evidence=ev)

    head_res = system.runner.run(
        ["git", "-C", abs_path, "rev-parse", "HEAD"], timeout=_TIMEOUT_S
    )
    if head_res.timed_out:
        ev["error"] = "git rev-parse timed out"
        return SourceProbe(SourceState.UNKNOWN, evidence=ev)
    if head_res.returncode != 0:
        ev["error"] = (head_res.stderr or "git rev-parse failed").strip()[:120]
        return SourceProbe(SourceState.UNKNOWN, evidence=ev)
    head = head_res.stdout.strip()
    ev["head"] = head

    desc = system.runner.run(
        ["git", "-C", abs_path, "describe", "--tags", "--always", "--dirty"],
        timeout=_TIMEOUT_S,
    )
    version = desc.stdout.strip() if desc.returncode == 0 else head[:12]
    ev["version"] = version

    # Only TRACKED-file modifications count as "dirty" (-uno excludes untracked
    # files). Build artifacts (compiled binaries, *.log) and app runtime data the
    # programs write into their own repo (channels.json, contacts.mesh, …) are NOT
    # source edits and must not mark the source modified.
    status_res = system.runner.run(
        ["git", "-C", abs_path, "status", "--porcelain", "--untracked-files=no"],
        timeout=_TIMEOUT_S,
    )
    if status_res.timed_out or status_res.returncode != 0:
        # Can't determine cleanliness -> report UNKNOWN rather than assuming clean.
        ev["error"] = (status_res.stderr or "git status failed").strip()[:120]
        return SourceProbe(SourceState.UNKNOWN, evidence=ev)
    dirty = bool(status_res.stdout.strip())
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
