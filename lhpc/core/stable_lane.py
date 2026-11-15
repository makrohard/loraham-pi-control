"""The Latest-stable lane (PLAN-UPDATE-v6 U1, D8; MESHCOM-STABLE slice A): ONE target for install, update and the
freshness check, decided by the upstream's HISTORY, never by comparing version numbers.

The history: one bare, treeless mirror per remote (`history_dir`), fetched on every resolve (commits and tag objects of
all branches and tags, deleted refs pruned). A commit is PUBLISHED when a current branch or tag of the mirror reaches it;
an object the mirror still holds after a force-push is not. Every ancestry answer is true, false or unknown (`_is`).

Target (`resolve`): the release R (GitHub `releases/latest`, no drafts, no prereleases) whatever its name (MeshCom's
`v4.40a.11.14`: slice B), and the plain version-shaped tags (the WHOLE name an optional `v` and one to three
numbers) in R's future; no release: every plain tag. The target is the candidate commit all others lead to;
candidates that are not one line of history refuse by name (finally only when the release read is verified; else
"could not check"). Tags in R's past or beside R's line are not successors of the release and never the target. No
candidate: the default-branch HEAD. VERIFIED when every applicable source was read (the mirror; on GitHub also the
releases API, a 404 meaning "no release"); a failed read, or a release the tags do not show (yet), leaves it
DEGRADED.

Move (`classify`): from the installed commit I to the target S only when I is published and an ancestor of S; S in I's
past is "not newer"; anything else (diverged, unpublished, unknown) refuses by name. An explicit switch onto the lane
(the user's choice) may go back, but only to a target the history places (`update_gate`).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import NamedTuple

STABLE_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+){0,2})$")                        # major[.minor[.patch]]
# A version: one to three numbers; after exactly three, a 4th dot part of 7-40 hex is the commit suffix, never a number.
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+){0,2})$|^v?(\d+\.\d+\.\d+)\.[0-9a-f]{7,40}$")
# A GitHub remote (https or scp-style ssh): its owner and repository, for the releases API.
_GITHUB_REMOTE_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)"
                               r"(?:\.git)?/?$")

FINAL_HINT = "use --source pinned or dev"            # every refusal no retry changes ends with it (`is_final`)
TRY_LATER = "releases could not be checked — try again later"


def is_final(why: str) -> bool:
    """`why` (a `StableRef.why`, also through `frozen()`) is a final refusal, not a read failure."""
    return why.endswith(FINAL_HINT)


def version_of(name):
    """THE version parser: `v?N[.N[.N]]`, or three numbers and a hex commit suffix, as an integer tuple without
    trailing zeros (`v1.2` = `v1.2.0`, `v2.8.0.7239fe8` and `v2.8.0.1234567` -> (2, 8)); None for anything else
    (`v2.8.0.9`, `v99.0-dev`). It decides which names are versions, and the label among names on ONE commit."""
    m = _VERSION_RE.match(name) if isinstance(name, str) else None
    if not m:
        return None
    key = [int(x) for x in (m.group(1) or m.group(2)).split(".")]
    while len(key) > 1 and key[-1] == 0:
        key.pop()
    return tuple(key)


class StableRef(NamedTuple):
    """The target commit and its label ("" + `why` when unresolved), whether every applicable source was read, the
    winning tag (`name`, "" = the default-branch HEAD) and the history mirror."""
    sha: str = ""
    label: str = ""
    verified: bool = False
    why: str = ""
    name: str = ""
    history: str = ""

    @property
    def key(self):
        """The target's version (None = the default-branch HEAD, or a release whose name is not a version)."""
        return version_of(self.name) if self.name else None

    def frozen(self) -> tuple:
        """The plan's `((sha, label), "")`, or `((None, None), why)` when unresolved."""
        return ((self.sha, self.label), "") if self.sha else ((None, None), self.why)


def history_dir(paths, remote: str):
    """The remote's history mirror: `state/stable-history/<16 hex of sha256(remote)>.git` under the runtime root."""
    return paths.under("state", "stable-history", hashlib.sha256(remote.encode()).hexdigest()[:16] + ".git")


# Reads never fetch a missing object from the remote (a treeless mirror is a partial clone; git >= 2.44 honours this,
# an older git fetches and fails, which reads as "could not tell" and refuses as well).
_NO_LAZY = {"GIT_NO_LAZY_FETCH": "1"}


def _git(run, mirror, *argv, timeout=10.0):
    return run(["git", "-C", str(mirror), *argv], timeout=timeout, env=_NO_LAZY)


def _fetch(run, mirror, remote: str) -> bool:
    """Bring the mirror up to the remote: all branches and tags, deleted ones pruned, no trees or blobs; never shallow."""
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        if run(["git", "init", "--quiet", "--bare", str(mirror)], timeout=10.0).returncode != 0:
            return False
    out = _git(run, mirror, "fetch", "--quiet", "--filter=tree:0", "--prune", "--prune-tags", "--no-write-fetch-head",
               "--end-of-options", remote, "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*", timeout=120.0)
    if out.returncode != 0:
        return False
    shallow = _git(run, mirror, "rev-parse", "--is-shallow-repository")
    return shallow.returncode == 0 and shallow.stdout.strip() == "false"


def _is(run, mirror, older: str, newer: str) -> bool | None:
    """`older` is an ancestor of (or equal to) `newer` in the mirror: True, False, or None (could not tell)."""
    return {0: True, 1: False}.get(_git(run, mirror, "merge-base", "--is-ancestor", older, newer).returncode)


def _published(run, mirror, commit: str) -> bool | None:
    """A current branch or tag of the mirror reaches `commit`: True, False (also: the mirror never saw it), or None
    (could not tell). An object the mirror still holds after a force-push is NOT published: only refs count."""
    have = _git(run, mirror, "cat-file", "-e", commit)      # 1: the mirror has no such object (a local commit)
    if have.returncode != 0:
        return False if have.returncode == 1 else None
    out = _git(run, mirror, "for-each-ref", "--count=1", "--format=%(refname)", f"--contains={commit}",
               "refs/heads", "refs/tags")
    return bool(out.stdout.strip()) if out.returncode == 0 else None


def resolve(run, remote: str, paths) -> StableRef:
    """The stable target on `remote` (validated here first), through `run(argv, timeout=)`: the history mirror's fetch,
    on GitHub ONE releases API call (curl, 10 s, no retry), and `git ls-remote <remote> HEAD` when no tag qualifies."""
    from . import validators
    try:
        remote = validators.remote_url(remote or "", field="remote")
    except validators.ValidationError as exc:
        return StableRef(why=f"invalid remote ({exc})")
    if not remote:
        return StableRef(why="no remote configured")
    mirror = history_dir(paths, remote)
    if not _fetch(run, mirror, remote):
        return StableRef(why=f"could not list tags on {remote}")
    refs = _git(run, mirror, "for-each-ref", "refs/tags", "--format=%(refname:strip=2) %(objectname) %(*objectname)")
    if refs.returncode != 0:
        return StableRef(why=f"could not list tags on {remote}")
    commits = {}
    for line in (refs.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            commits[parts[0]] = parts[-1]                    # an annotated tag: its peeled commit
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
            release = tag if isinstance(tag, str) else ""        # any name: the upstream's statement (MeshCom)
            verified = release in commits                         # unparseable, or not in the tag list yet: no answer
            release = release if verified else ""
        elif not (api.returncode == 0 and code == "404"):         # 404: no release, nothing missed
            verified = False
    note = "" if verified else " — releases not checked"
    plain = [n for n in commits if STABLE_TAG_RE.match(n)]
    if release:                                               # the release's line: R and the plain tags in its future
        line = [release]
        for n in plain:
            if n != release:
                if (fut := _is(run, mirror, commits[release], commits[n])) is None:
                    return StableRef(why=f"could not read the history of {remote}")
                if fut:
                    line.append(n)
    else:
        line = plain
    if line:                                                  # the tips: the candidates no other one leads to
        tips = _git(run, mirror, "merge-base", "--independent", *sorted({commits[n] for n in line}))
        if tips.returncode != 0:
            return StableRef(why=f"could not read the history of {remote}")
        tip = tips.stdout.split()
        if len(tip) != 1:
            if not verified:                                  # only verified facts refuse for good
                return StableRef(why=f"{remote}: its version tags are not one line of history and its releases "
                                     "could not be checked")
            return StableRef(why=f"{remote}: its release and version tags are not one line of history; refused; "
                                 f"{FINAL_HINT}")
        sha = tip[0]
        on_tip = [n for n in line if commits[n] == sha]
        name = release if release in on_tip else max(on_tip, key=lambda n: (version_of(n), n))   # a label, one commit
        return StableRef(sha, f"stable {name} @ {sha[:9]}{note}", verified, "", name, str(mirror))
    head = run(["git", "ls-remote", remote, "HEAD"], timeout=15.0)
    if head.returncode != 0 or not head.stdout.strip():
        return StableRef(why=f"could not resolve HEAD on {remote}")
    sha = head.stdout.split()[0]
    return StableRef(sha, f"stable: default branch @ {sha[:9]} (no release tags){note}", verified, "", "", str(mirror))


def resolver(run, paths):
    """`get(remote) -> StableRef`: ONE target per remote for one operation (an update gate, a source-check sweep; the
    install plan keeps one frozen target per remote itself); two source paths that share a remote read it once and see
    the same answer."""
    seen: dict = {}

    def get(remote: str) -> StableRef:
        if remote not in seen:
            seen[remote] = resolve(run, remote, paths)
        return seen[remote]
    return get


def classify(run, installed: str, ref: StableRef) -> tuple[bool | None, str]:
    """May the installed commit move to `ref`? `(older, why)`: older False = a forward move (or current), True = the
    target is in the installed commit's past, None = refused (`why` names the reason, "" = a read failed)."""
    if not installed or not ref.sha or not ref.history:
        return None, ""
    if installed == ref.sha:
        return False, ""
    from pathlib import Path
    mirror = Path(ref.history)
    pub = _published(run, mirror, installed)
    if pub is None:
        return None, ""
    if not pub:
        return None, f"the installed commit {installed[:9]} is not in the upstream's published history: refused"
    fwd = _is(run, mirror, installed, ref.sha)
    if fwd is None:
        return None, ""
    if fwd:
        return False, ""
    back = _is(run, mirror, ref.sha, installed)
    if back is None:
        return None, ""
    if back:
        return True, ""
    return None, f"the installed commit is not in the history of the stable target ({ref.label}): refused"


def assess(run, src, ref: StableRef, remote: str = "") -> tuple[str, bool | None]:
    """The installed commit at `src` ("" when not read) and `classify`'s `older` for it: the one reading of the checkout
    the update gate and the freshness check share."""
    head = run(["git", "-C", str(src), "rev-parse", "HEAD"], timeout=5.0)
    installed = (head.stdout or "").strip() if head.returncode == 0 else ""
    return installed, classify(run, installed, ref)[0]


def update_gate(run, paths, items, source: str, remote_of):
    """An update's selector per source (`source`, or for a bare update "" each source's recorded one, none: pinned) and
    U1's gate on it: `(sel, refs, refused)`, refs the stable targets read for the plan (per source path), refused the
    per-source refusal lines. ONE target per remote: two paths that share a remote read it once. Within the stable lane
    no move goes back or sideways (`classify`); an explicit switch onto it (the source recorded another lane) is the
    user's choice and may go back, but only to a verified release or tag the history places. The plan runs it without
    locks; the apply runs it ONCE, under its locks."""
    from . import source_fs, source_registry
    sel = {c.source.path: source or source_registry.recorded_selector(paths, c.source.path) for _, c in items}
    refs, refused, target = {}, [], resolver(run, paths)
    for _, c in items:
        if sel[c.source.path] == "stable" and not c.source.artifact and source_fs.source_present(
                paths, src := paths.resolve_source(c.source.path)):
            ref = refs[c.source.path] = target(remote_of(c))
            head = run(["git", "-C", str(src), "rev-parse", "HEAD"], timeout=5.0)
            installed = (head.stdout or "").strip() if head.returncode == 0 else ""
            older, why = classify(run, installed, ref)
            if source_registry.recorded_selector(paths, c.source.path) != "stable":   # an explicit lane SWITCH:
                if older is not None and not ref.name:                              # a release or tag target only
                    older = None
                why = update_refusal(ref, installed, None if older is None else False, why)
            else:                                                                   # within the lane: forward only
                why = update_refusal(ref, installed, older, why)
            if why:
                refused.append(f"  {c.id}: {why}")
    return sel, refs, refused


def update_refusal(ref: StableRef, installed: str, older: bool | None, why: str = "") -> str:
    """Why an installed source at `installed` may not move to `ref` ("" = it may); `older`/`why` from `classify`."""
    if is_final(ref.why):
        return ref.why
    if not ref.sha or not ref.verified or older is None:
        if why:
            return why
        why = ref.why or ("" if older is not None else "the installed commit's history could not be read")
        return TRY_LATER + (f" ({why})" if why else "")
    if older:
        return (f"the stable target ({ref.label}) is not newer than the installed version: refused; "
                "use --source pinned to go back to LHPC's tested versions")
    return ""
