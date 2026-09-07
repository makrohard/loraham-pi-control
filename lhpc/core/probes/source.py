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


def _numstat(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a, d = out.get(parts[2], (0, 0))
            out[parts[2]] = (a + (int(parts[0]) if parts[0].isdigit() else 0),
                             d + (int(parts[1]) if parts[1].isdigit() else 0))
    return out


def lhpc_patched_only(system: System, abs_path: str, patches) -> bool:
    """True when the tracked modifications in `abs_path` are EXACTLY the LHPC-shipped
    `patches` a build step applies (`{asset}/patches/…`, or absolute paths): they reverse-apply
    cleanly AND the per-file line counts of the working-tree diff equal the patches' own. An
    extra hunk or an extra file is a local modification and keeps the tree dirty."""
    from ..assets import asset_path
    files = [str(asset_path(p[len("{asset}/"):])) if p.startswith("{asset}/") else p
             for p in patches]
    if not files:
        return False
    check = system.runner.run(["git", "-C", abs_path, "apply", "--reverse", "--check", *files],
                              timeout=_TIMEOUT_S)
    if check.returncode != 0:
        return False
    want = system.runner.run(["git", "-C", abs_path, "apply", "--numstat", *files],
                             timeout=_TIMEOUT_S)
    have = system.runner.run(["git", "-C", abs_path, "diff", "HEAD", "--numstat"],
                             timeout=_TIMEOUT_S)
    if want.returncode != 0 or have.returncode != 0:
        return False
    return _numstat(want.stdout) == _numstat(have.stdout)


def probe_source(system: System, spec: SourceSpec, abs_path: str) -> SourceProbe:
    ev = {"path": abs_path}
    if system.fs.readlink(abs_path):
        # A symlink at the managed path is not a managed source (the ONE presence policy:
        # `source_fs.source_present`); nothing behind it is inspected, no git runs on it.
        ev["state"] = "symlink"
        return SourceProbe(SourceState.MISSING, evidence=ev)
    if not system.fs.exists(abs_path):
        ev["state"] = "missing"
        return SourceProbe(SourceState.MISSING, evidence=ev)
    if not system.fs.exists(f"{abs_path}/.git"):
        ev["state"] = "not-a-repo"
        return SourceProbe(SourceState.NOT_A_REPO, evidence=ev)

    # ONE `git status --porcelain=v2 --branch` answers both questions in one call: `# branch.oid` is the
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
    if dirty and spec.patches and lhpc_patched_only(system, abs_path, spec.patches):
        # The only modifications are LHPC's own build-time patch: not an operator change.
        dirty, version = False, version.removesuffix("-dirty")
        ev["patched"] = "lhpc"
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
