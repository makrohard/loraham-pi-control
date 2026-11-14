"""The Latest-stable lane (PLAN-UPDATE-v6 U1, D8): ONE target for install, update and the freshness check.

Candidates: the remote's version-shaped tags (the WHOLE name an optional `v` and dot-separated numbers: `v112`,
`v1.2`, `1.5.2`) and, on a GitHub remote, the tag of its latest release (`releases/latest`: no drafts, no
prereleases), so a build-suffixed tag (`v2.8.0.7239fe8`) counts only when the upstream published it as its
release. They are compared by their leading version (`version_key`); a tie goes to the plain tag. Neither: the
default-branch HEAD. The result is VERIFIED when every applicable source was read (tags; on GitHub also the
releases API, a 404 meaning "no release"); a failed API call leaves it DEGRADED. An update of an installed source
runs only to a verified target that is not older than the installed commit (`update_refusal`).
"""
from __future__ import annotations

import json
import re
from typing import NamedTuple

STABLE_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_LEADING_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)(?![0-9A-Za-z])")     # whole numeric segments only
# A GitHub remote (https or scp-style ssh): its owner and repository, for the releases API.
_GITHUB_REMOTE_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)"
                               r"(?:\.git)?/?$")


class StableRef(NamedTuple):
    """The target commit and its label ("" + `why` when unresolved), whether every applicable source was read, the
    newest version key per tagged commit, the target's own key (None = the default-branch HEAD)."""
    sha: str = ""
    label: str = ""
    verified: bool = False
    why: str = ""
    keys: dict | None = None
    key: tuple | None = None

    def frozen(self) -> tuple:
        """The plan's `((sha, label), "")`, or `((None, None), why)` when unresolved."""
        return ((self.sha, self.label), "") if self.sha else ((None, None), self.why)


def version_key(name):
    """The name's leading `v?N(.N)*` as an integer tuple without trailing zeros (`v1.2` = `v1.2.0`,
    `v2.8.0.7239fe8` -> (2, 8), `v2.8.0.9` > `v2.8.0`), or None when the name does not start with a version."""
    m = _LEADING_VERSION_RE.match((name or "").strip())
    if not m:
        return None
    key = [int(x) for x in m.group(1).split(".")]
    while len(key) > 1 and key[-1] == 0:
        key.pop()
    return tuple(key)


def stable_tag(names, release_tag: str = "") -> str:
    """The newest version-shaped tag of `names` or `release_tag` (a candidate when it is one of `names` and starts
    with a version), by `version_key`; on a tie the version-shaped tag. "" when there is no candidate."""
    cands = [(version_key(n), 1, n) for n in names if STABLE_TAG_RE.match(n)]
    if release_tag in names and version_key(release_tag) is not None:
        cands.append((version_key(release_tag), 0, release_tag))
    return max(cands)[2] if cands else ""


def resolve(run, remote: str) -> StableRef:
    """The stable target on `remote` (validated here first), through `run(argv, timeout=)`: `git ls-remote --tags`
    (peeled commits), on GitHub ONE releases API call (curl, 10 s, no retry), else `git ls-remote <remote> HEAD`."""
    from . import validators
    try:
        remote = validators.remote_url(remote or "", field="remote")
    except validators.ValidationError as exc:
        return StableRef(why=f"invalid remote ({exc})")
    if not remote:
        return StableRef(why="no remote configured")
    out = run(["git", "ls-remote", "--tags", remote], timeout=15.0)
    if out.returncode != 0:
        return StableRef(why=f"could not list tags on {remote}")
    plain, peeled = {}, {}
    for line in (out.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/tags/"):
            name = parts[1][len("refs/tags/"):]
            (peeled if name.endswith("^{}") else plain)[name.removesuffix("^{}")] = parts[0]
    commits = {n: peeled.get(n, sha) for n, sha in plain.items()}
    keys: dict = {}
    for n, sha in commits.items():
        if (k := version_key(n)) is not None and k > keys.get(sha, ()):
            keys[sha] = k
    release, verified = "", True
    if gh := _GITHUB_REMOTE_RE.match(remote):
        api = run(["curl", "-sSL", "--max-time", "10", "-H", "Accept: application/vnd.github+json",
                   "-w", "\n%{http_code}", f"https://api.github.com/repos/{gh[1]}/{gh[2]}/releases/latest"],
                  timeout=12.0)
        body, _, code = (api.stdout or "").rpartition("\n")
        if api.returncode == 0 and code == "200":
            try:
                release = str(json.loads(body).get("tag_name") or "")
            except (ValueError, AttributeError):
                verified = False
        elif not (api.returncode == 0 and code == "404"):         # 404: no release, nothing missed
            verified = False
    note = "" if verified else " — releases not checked"
    if name := stable_tag(commits, release):
        sha = commits[name]
        return StableRef(sha, f"stable {name} @ {sha[:9]}{note}", verified, "", keys, version_key(name))
    head = run(["git", "ls-remote", remote, "HEAD"], timeout=15.0)
    if head.returncode != 0 or not head.stdout.strip():
        return StableRef(why=f"could not resolve HEAD on {remote}")
    sha = head.stdout.split()[0]
    return StableRef(sha, f"stable: default branch @ {sha[:9]} (no release tags){note}", verified, "", keys)


def is_older(ref: StableRef, installed: str) -> bool:
    """The target is older than the installed commit: a lower tag key, or none while the installed commit has one."""
    have = (ref.keys or {}).get(installed)
    return have is not None and (ref.key is None or ref.key < have)


def update_refusal(ref: StableRef, installed: str) -> str:
    """Why an installed source at `installed` may not move to `ref` ("" = it may)."""
    if not ref.sha or not ref.verified:
        return "releases could not be checked — try again later" + (f" ({ref.why})" if ref.why else "")
    if is_older(ref, installed):
        return f"the stable target ({ref.label}) is older than the installed commit — nothing to update"
    return ""
