"""The Latest-stable lane (PLAN-UPDATE-v6 U1, D8): ONE target for install, update and the freshness check.

Candidates: the remote's version-shaped tags (the WHOLE name an optional `v` and one to three dot-separated numbers:
`v112`, `v1.2`, `1.5.2`) and, on a GitHub remote, the tag of its latest release (`releases/latest`: no drafts, no
prereleases), so a build-suffixed tag (`v2.8.0.7239fe8`) counts only when the upstream published it as its
release. They are compared by ONE strict parser (`version_of`); a tie goes to the plain tag. Neither: the
default-branch HEAD. The result is VERIFIED when every applicable source was read (tags; on GitHub also the
releases API, a 404 meaning "no release"); a failed call, a release without a parseable tag name, or one the tag list
does not show (yet) leaves it DEGRADED.
An update of an installed source runs only to a verified target that is not older than the installed commit: the
highest version among ALL its tag names (`update_refusal`); a tag list that cannot be read refuses (fail closed).
"""
from __future__ import annotations

import json
import re
from typing import NamedTuple

STABLE_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+){0,2})$")                        # major[.minor[.patch]]
# A version: one to three numbers; after exactly three, a 4th dot part of 7-40 hex is the commit suffix, never a number.
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+){0,2})$|^v?(\d+\.\d+\.\d+)\.[0-9a-f]{7,40}$")
# A GitHub remote (https or scp-style ssh): its owner and repository, for the releases API.
_GITHUB_REMOTE_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)"
                               r"(?:\.git)?/?$")


NO_VERSION = "publishes no version-shaped releases; use --source pinned or dev"   # a refusal no retry changes


def is_final(why: str) -> bool:
    """`why` (a `StableRef.why`, also through `frozen()`) is the final no-version refusal, not a read failure."""
    return why.endswith(NO_VERSION)


class StableRef(NamedTuple):
    """The target commit and its label ("" + `why` when unresolved), whether every applicable source was read, the
    upstream's tag names per commit, the target's own key (None = the default-branch HEAD), and whether `why` is final
    (a retry cannot change it: the upstream publishes no version-shaped releases)."""
    sha: str = ""
    label: str = ""
    verified: bool = False
    why: str = ""
    tags: dict | None = None
    key: tuple | None = None
    final: bool = False

    def frozen(self) -> tuple:
        """The plan's `((sha, label), "")`, or `((None, None), why)` when unresolved."""
        return ((self.sha, self.label), "") if self.sha else ((None, None), self.why)


def version_of(name):
    """THE version parser: `v?N[.N[.N]]`, or three numbers and a hex commit suffix, as an integer tuple without
    trailing zeros (`v1.2` = `v1.2.0`, `v2.8.0.7239fe8` and `v2.8.0.1234567` -> (2, 8)); None for anything else
    (`v2.8.0.9`, `v99.0-dev`): a tag that does not parse fails closed."""
    m = _VERSION_RE.match(name) if isinstance(name, str) else None
    if not m:
        return None
    key = [int(x) for x in (m.group(1) or m.group(2)).split(".")]
    while len(key) > 1 and key[-1] == 0:
        key.pop()
    return tuple(key)


def stable_tag(names, release_tag: str = "") -> str:
    """The newest version-shaped tag of `names` or `release_tag` (a candidate when it is one of `names` and has a
    version), by `version_of`; on a tie the version-shaped tag. "" when there is no candidate."""
    cands = [(version_of(n), 1, n) for n in names if STABLE_TAG_RE.match(n)]
    if release_tag in names and version_of(release_tag) is not None:
        cands.append((version_of(release_tag), 0, release_tag))
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
    tags: dict = {}
    for n, sha in commits.items():
        tags.setdefault(sha, []).append(n)
    release, verified = "", True
    if gh := _GITHUB_REMOTE_RE.match(remote):
        api = run(["curl", "-sSL", "--max-time", "10", "-H", "Accept: application/vnd.github+json",
                   "-w", "\n%{http_code}", f"https://api.github.com/repos/{gh[1]}/{gh[2]}/releases/latest"],
                  timeout=12.0)
        body, _, code = (api.stdout or "").rpartition("\n")
        if api.returncode == 0 and code == "200":
            try:
                tag = json.loads(body).get("tag_name")
            except (ValueError, AttributeError):
                tag = None
            if isinstance(tag, str) and tag and not version_of(tag):        # MeshCom's `v4.40a.11.14`: never verifiable
                return StableRef(why=f"{gh[1]}/{gh[2]} {NO_VERSION}", final=True)
            release = tag if version_of(tag) else ""
            verified = release in commits                         # unparseable, or not in the tag list yet: no answer
        elif not (api.returncode == 0 and code == "404"):         # 404: no release, nothing missed
            verified = False
    note = "" if verified else " — releases not checked"
    if name := stable_tag(commits, release):
        sha = commits[name]
        return StableRef(sha, f"stable {name} @ {sha[:9]}{note}", verified, "", tags, version_of(name))
    head = run(["git", "ls-remote", remote, "HEAD"], timeout=15.0)
    if head.returncode != 0 or not head.stdout.strip():
        return StableRef(why=f"could not resolve HEAD on {remote}")
    sha = head.stdout.split()[0]
    return StableRef(sha, f"stable: default branch @ {sha[:9]} (no release tags){note}", verified, "", tags)


def local_tags(run, src) -> list | None:
    """The tag names on the checkout's HEAD, kept from its clone (a tag the upstream deleted since still counts); None
    when they cannot be read, never [] (that is "no tag")."""
    out = run(["git", "-C", str(src), "tag", "--points-at", "HEAD"], timeout=5.0)
    return out.stdout.split() if out.returncode == 0 else None


def is_older(ref: StableRef, installed: str, mine=(), forward=None) -> bool | None:
    """The target is older than the installed commit: both versions known, the highest `version_of` among ALL the
    installed commit's tag names (the upstream's and `mine`, `local_tags`) above the target's. A version missing on
    either side: the COMMITS decide, offered (False) only when `forward()` says the installed commit is an ancestor of
    the target (the plan's rule L6, a real forward move); proven not an ancestor: older (True). None (could not check):
    the installed commit, its tags or the ancestry not read, or tags of which none parses (`v3.0-rc1` alone)."""
    names = None if mine is None else [*(ref.tags or {}).get(installed, ()), *mine]
    have = max(filter(None, map(version_of, names or ())), default=None)
    if not installed or names is None or (names and have is None):
        return None
    if have is not None and ref.key is not None:
        return ref.key < have
    fwd = True if installed == ref.sha else forward() if forward else None
    return None if fwd is None else not fwd


def is_ancestor(run, src, older: str, newer: str, remote: str) -> bool | None:
    """`older` is an ancestor of `newer` in the checkout at `src`, which fetches `newer` by its commit first when it lacks
    it; None when it cannot be told."""
    cmd = ["git", "-C", str(src), "merge-base", "--is-ancestor", older, newer]
    r = run(cmd, timeout=10.0)
    if r.returncode not in (0, 1) and run(["git", "-C", str(src), "fetch", "--quiet", "--no-tags", "--end-of-options",
                                           remote, newer], timeout=60.0).returncode == 0:
        r = run(cmd, timeout=10.0)
    return {0: True, 1: False}.get(r.returncode)


def assess(run, src, ref: StableRef, remote: str) -> tuple[str, bool | None]:
    """The installed commit at `src` ("" when not read) and `is_older` for it: the one reading of the checkout the
    update gate and the freshness check share."""
    head = run(["git", "-C", str(src), "rev-parse", "HEAD"], timeout=5.0)
    installed = (head.stdout or "").strip() if head.returncode == 0 else ""
    return installed, is_older(ref, installed, local_tags(run, src),
                               lambda: is_ancestor(run, src, installed, ref.sha, remote))


def update_gate(run, paths, items, source: str, remote_of):
    """An update's selector per source (`source`, or for a bare update "" each source's recorded one, none: pinned) and
    U1's gate on it: `(sel, refs, refused)`, refs the stable targets read for the plan, refused the per-source refusal
    lines. Within the stable lane no downgrade passes (`assess`); an explicit switch onto it (the source recorded
    another lane) needs only a resolved, verified release version. The plan runs it without locks; the apply runs it ONCE, under its locks."""
    from . import source_fs, source_registry
    sel = {c.source.path: source or source_registry.recorded_selector(paths, c.source.path) for _, c in items}
    refs, refused = {}, []
    for _, c in items:
        if sel[c.source.path] == "stable" and not c.source.artifact and source_fs.source_present(
                paths, src := paths.resolve_source(c.source.path)):
            ref = refs[c.source.path] = refs.get(c.source.path) or resolve(run, remote_of(c))   # once per path
            if source_registry.recorded_selector(paths, c.source.path) != "stable":   # an explicit lane SWITCH:
                why = update_refusal(ref, "", False if ref.key is not None else None)   # a resolved release only
            else:                                                                       # within the lane: no downgrade
                why = update_refusal(ref, *assess(run, src, ref, remote_of(c)))
            if why:
                refused.append(f"  {c.id}: {why}")
    return sel, refs, refused


def update_refusal(ref: StableRef, installed: str, older: bool | None) -> str:
    """Why an installed source at `installed` (`assess`) may not move to `ref` ("" = it may)."""
    if ref.final:
        return ref.why
    if not ref.sha or not ref.verified or older is None:
        why = ref.why or ("" if older is not None else "the installed commit's version could not be read")
        return "releases could not be checked — try again later" + (f" ({why})" if why else "")
    if older:
        return f"the stable target ({ref.label}) is not newer than the installed version: refused"
    return ""
