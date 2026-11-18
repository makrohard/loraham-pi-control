"""The git lanes' targets and moves (PLAN-UPDATE-v6 U1; STABLE-LANE): Latest stable is "the latest release or tag on
the current branch", decided by the upstream's HISTORY, never by comparing version numbers.

The history: one bare, treeless mirror per remote AND branch (`history_dir`), fetched on every read: that branch and
every tag, deleted ones pruned. A commit is PUBLISHED when the mirror's branch or a tag reaches it; an object the
mirror still holds after a force-push is not. Every ancestry answer is true, false or unknown (`_is`).

Stable target (`resolve`), the branch being the component's manifest branch (one the remote lacks refuses for good):
GitHub's latest release (no drafts, no pre-releases) on any branch, unless a plain version tag (an optional `v` and
one to three numbers, nothing else) on the branch's first-parent line comes after it: then the plain tag nearest to
the branch tip. No release: that tag; none: the branch tip. A release read that fails (also a 404 from a repository
the API does not show) gives no target: "try again later".

Moves, every git lane (`contains`, `classify`, `update_gate`): a routine bare update moves only when the checkout does
not contain the lane's target (the badge's own predicate); one that contains it is kept ("not newer"). A move the user
names (`--source`) installs the target wherever it lies. Back, sideways or away from own commits: a warning in the
plan, applied on Confirm / `--yes`. An answer that cannot be read is "could not check", never a warning.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import NamedTuple

STABLE_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+){0,2})$")                        # major[.minor[.patch]]
# A version: one to three numbers; after exactly three, a 4th dot part of 7-40 hex is the commit suffix, never a number.
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+){0,2})$|^v?(\d+\.\d+\.\d+)\.[0-9a-f]{7,40}$")
# A GitHub remote (https or scp-style ssh): its owner and repository, for the releases API.
_GITHUB_REMOTE_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)"
                               r"(?:\.git)?/?$")

FINAL_HINT = "use --source pinned"                   # every refusal no retry changes ends with it (`is_final`)
TRY_LATER = "releases could not be checked — try again later"
COULD_NOT_CHECK = "could not check the installed commit's history — try again later"
# The plan's warnings (X4): a move the user chose that goes back, sideways or away from commits not upstream.
WARN_LINE = ("the {lane} target ({label}) is {how} the installed version: its saved config/state may not match; "
             "if the stack fails: remove it, reinstall Known working")
WARN_OWN = ("the installed commit {installed} is not upstream; installing deletes {n} commit(s) only this checkout "
            "has, not recoverable by LHPC: {commits}; to keep them, push or copy them first")
WARN_MOVED = ("the checkout moved since LHPC installed it (HEAD {installed}, installed {recorded}); installing "
              "replaces it")


def is_final(why: str) -> bool:
    """`why` (a `StableRef.why`, also through `frozen()`) is a final refusal, not a read failure."""
    return why.endswith(FINAL_HINT)


def version_of(name):
    """THE version parser: `v?N[.N[.N]]`, or three numbers and a hex commit suffix, as an integer tuple without
    trailing zeros (`v1.2` = `v1.2.0`, `v2.8.0.7239fe8` and `v2.8.0.1234567` -> (2, 8)); None for anything else
    (`v2.8.0.9`, `v99.0-dev`). Only the label among names on ONE commit uses it."""
    m = _VERSION_RE.match(name) if isinstance(name, str) else None
    if not m:
        return None
    key = [int(x) for x in (m.group(1) or m.group(2)).split(".")]
    while len(key) > 1 and key[-1] == 0:
        key.pop()
    return tuple(key)


class StableRef(NamedTuple):
    """The target commit and its label ("" + `why` when unresolved), whether every applicable source was read, the
    winning name (`name`, "" = the branch tip), the history mirror, whether `name` is GitHub's release, and the
    branch it was resolved on."""
    sha: str = ""
    label: str = ""
    verified: bool = False
    why: str = ""
    name: str = ""
    history: str = ""
    released: bool = False
    branch: str = ""

    def frozen(self) -> tuple:
        """The plan's `((sha, label), "")`, or `((None, None), why)` when unresolved."""
        return ((self.sha, self.label), "") if self.sha else ((None, None), self.why)


def remote_tip(run, remote: str, ref: str, timeout: float) -> str:
    """The commit `ref` names on `remote`, "" when it cannot be read: `HEAD` or the branch `refs/heads/<ref>` by its
    EXACT name. `git ls-remote` matches name tails, so `main` also lists `refs/heads/feature/main` (sorted first)."""
    full = ref if ref == "HEAD" else f"refs/heads/{ref}"
    out = run(["git", "ls-remote", remote, full], timeout=timeout)
    rows = [ln.split() for ln in out.stdout.splitlines()] if out.returncode == 0 else []
    return next((r[0] for r in rows if r[1:] == [full] and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", r[0])), "")


def history_dir(paths, remote: str, branch: str):
    """The (remote, branch) history mirror: `state/stable-history-v2/<16 hex of sha256(remote NUL branch)>.git` under
    the runtime root. A new name: an older per-remote mirror holds every branch, so a commit only a stale branch
    reaches would still read as published there."""
    return paths.under("state", "stable-history-v2", hashlib.sha256(f"{remote}\0{branch}".encode()).hexdigest()[:16]
                       + ".git")


def lane_ref(branch: str) -> str:
    """The mirror's ref for the branch (the remote's HEAD when the source names none)."""
    return f"refs/heads/{branch}" if branch else "refs/heads/_default"


# Reads never fetch a missing object from the remote (a treeless mirror is a partial clone; git >= 2.44 honours this,
# an older git fetches and fails, which reads as "could not tell" and refuses as well).
_NO_LAZY = {"GIT_NO_LAZY_FETCH": "1"}


def _git(run, mirror, *argv, timeout=10.0):
    return run(["git", "-C", str(mirror), *argv], timeout=timeout, env=_NO_LAZY)


def fetch_history(run, remote: str, branch: str, paths) -> tuple:
    """`(mirror, "")` with the mirror up to the remote's branch and tags (deleted ones pruned, no trees or blobs,
    never shallow), or `(None, why)`: the branch is not on the remote (final), or the remote could not be read."""
    mirror = history_dir(paths, remote, branch)
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        if run(["git", "init", "--quiet", "--bare", str(mirror)], timeout=10.0).returncode != 0:
            return None, f"could not list tags on {remote}"
    out = _git(run, mirror, "fetch", "--quiet", "--filter=tree:0", "--prune", "--prune-tags", "--no-write-fetch-head",
               "--end-of-options", remote, f"+{f'refs/heads/{branch}' if branch else 'HEAD'}:{lane_ref(branch)}",
               "+refs/tags/*:refs/tags/*", timeout=120.0)
    if out.returncode != 0:
        if branch and run(["git", "ls-remote", "--exit-code", remote, f"refs/heads/{branch}"],
                          timeout=15.0).returncode == 2 and _forget_gone(run, mirror, remote, branch):
            return None, f"branch {branch!r} is not on {remote}: refused; {FINAL_HINT}"
        return None, f"could not list tags on {remote}"
    shallow = _git(run, mirror, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        return None, f"could not list tags on {remote}"
    return mirror, ""


def _forget_gone(run, mirror, remote: str, branch: str) -> bool:
    """The branch is gone upstream: its cached tip moves to `refs/gone/` (kept as history, never as publication: G2
    R2 B1) and the copy's tags are read again, so only refs the upstream still has count. False: not done."""
    tip = _git(run, mirror, "rev-parse", "--verify", "-q", lane_ref(branch))
    if tip.returncode == 0 and (_git(run, mirror, "update-ref", f"refs/gone/{branch}", tip.stdout.strip()).returncode
                                or _git(run, mirror, "update-ref", "-d", lane_ref(branch)).returncode):
        return False
    return _git(run, mirror, "fetch", "--quiet", "--filter=tree:0", "--prune", "--prune-tags", "--no-write-fetch-head",
                "--end-of-options", remote, "+refs/tags/*:refs/tags/*", timeout=120.0).returncode == 0


def held_copy(run, paths, remote: str, branch: str, installed: str):
    """The history copy this box holds for `branch` when it holds the installed commit, else None."""
    held = history_dir(paths, remote, branch)
    return held if (held / "HEAD").exists() and _git(run, held, "cat-file", "-e", installed).returncode == 0 else None


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


def resolve(run, remote: str, branch: str, paths) -> StableRef:
    """The stable target on `remote`'s `branch` (the remote validated here first), through `run(argv, timeout=)`: the
    mirror's fetch and, on GitHub, the releases API (curl, 10 s, no retry; on a 404 the repository's own page)."""
    from . import validators
    try:
        remote = validators.remote_url(remote or "", field="remote")
    except validators.ValidationError as exc:
        return StableRef(why=f"invalid remote ({exc})")
    if not remote:
        return StableRef(why="no remote configured")
    mirror, why = fetch_history(run, remote, branch, paths)
    if mirror is None:
        return StableRef(why=why)
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
        code, body = _api(run, f"https://api.github.com/repos/{gh[1]}/{gh[2]}/releases/latest")
        if code == "200":
            try:
                tag = json.loads(body).get("tag_name")
            except (ValueError, AttributeError):
                tag = None
            release = tag if isinstance(tag, str) else ""        # any name: the upstream's statement (MeshCom)
            verified = release in commits                         # unparseable, or not in the tag list yet: no answer
        else:                                                     # 404 is "no release" only from a repo the API shows
            verified = code == "404" and _api(run, f"https://api.github.com/repos/{gh[1]}/{gh[2]}")[0] == "200"
    if not verified:
        return StableRef(why=TRY_LATER, history=str(mirror), branch=branch)
    walk = _git(run, mirror, "rev-list", "--first-parent", lane_ref(branch))
    line = walk.stdout.split() if walk.returncode == 0 else []
    if not line:
        return StableRef(why=f"could not read the history of {remote}")
    plain: dict = {}
    for n, c in commits.items():
        if STABLE_TAG_RE.match(n):
            plain.setdefault(c, []).append(n)
    tagged = next((c for c in line if c in plain), "")          # the plain tag nearest to the branch tip
    sha, name = (tagged, max(plain[tagged], key=lambda n: (version_of(n), n))) if tagged else (line[0], "")
    if release and sha != commits[release]:
        after = bool(tagged) and _is(run, mirror, commits[release], tagged)
        if after is None:
            return StableRef(why=f"could not read the history of {remote}")
        if not after:                                         # no plain tag after the release on this branch
            sha, name = commits[release], release
    elif release:
        name = release                                        # the release and a tag on one commit: its label
    label = f"stable {name} @ {sha[:9]}" if name else f"stable: branch {branch or 'HEAD'} @ {sha[:9]} (no release tags)"
    return StableRef(sha, label, True, "", name, str(mirror), bool(release) and name == release, branch)


def _api(run, url: str) -> tuple[str, str]:
    """One GitHub API GET: (HTTP code, "" when the call itself failed; body)."""
    out = run(["curl", "-sSL", "--max-time", "10", "-H", "Accept: application/vnd.github+json", "-w", "\n%{http_code}",
               url], timeout=12.0)
    body, _, code = (out.stdout or "").rpartition("\n")
    return (code if out.returncode == 0 else ""), body


def resolver(run, paths):
    """`get(remote, branch) -> StableRef`: ONE target per remote and branch for one operation (an update gate, a
    source-check sweep); `get.history(remote, branch) -> (mirror, why)` the same for the other lanes' mirror reads."""
    seen: dict = {}
    hist: dict = {}

    def get(remote: str, branch: str) -> StableRef:
        if (remote, branch) not in seen:
            seen[remote, branch] = resolve(run, remote, branch, paths)
        return seen[remote, branch]

    def history(remote: str, branch: str) -> tuple:
        if (remote, branch) not in hist:
            hist[remote, branch] = fetch_history(run, remote, branch, paths)
        return hist[remote, branch]
    get.history = history
    return get


def contains(run, mirror, src, installed: str, target: str) -> bool | None:
    """The checkout at `src` (HEAD `installed`) already contains `target`: True, False, or None (could not check). The
    badge and the routine update's "not newer" both ask this, always of `installed` (never the live HEAD). Full history
    decides, never a missing object: the mirror when it holds the installed commit; else the checkout: the target
    found in the installed commit's past = contained; a "no" (target absent, or not found in that past) only from a
    full clone, a shallow checkout's cut history cannot tell (G2-STABLELANE-S1 B1)."""
    if installed == target:
        return True
    if mirror is not None and (known := _is(run, mirror, target, installed)) is not None:
        return known
    have = run(["git", "-C", str(src), "cat-file", "-e", target], timeout=5.0, env=_NO_LAZY)
    if have.returncode not in (0, 1):
        return None
    if have.returncode == 0 and (anc := run(["git", "-C", str(src), "merge-base", "--is-ancestor", target, installed],
                                            timeout=10.0, env=_NO_LAZY).returncode) != 1:
        return True if anc == 0 else None
    shallow = run(["git", "-C", str(src), "rev-parse", "--is-shallow-repository"], timeout=5.0)
    return False if shallow.returncode == 0 and shallow.stdout.strip() == "false" else None


def classify(run, mirror, installed: str, target: str) -> str | None:
    """Where `target` lies from the installed commit, for the plan's warning: "current", "forward", "back",
    "sideways", "own" (the installed commit is not upstream), or None (a history read failed)."""
    if installed == target:
        return "current"
    if mirror is None:
        return None
    pub = _published(run, mirror, installed)
    if not pub:
        return None if pub is None else "own"
    fwd = _is(run, mirror, installed, target)
    back = _is(run, mirror, target, installed) if fwd is False else False
    if fwd is None or back is None:
        return None
    return "forward" if fwd else "back" if back else "sideways"


def assess(run, src) -> str:
    """The installed commit at `src` ("" when not read): the one reading of the checkout the gate and the check share."""
    head = run(["git", "-C", str(src), "rev-parse", "HEAD"], timeout=5.0)
    return (head.stdout or "").strip() if head.returncode == 0 else ""


def own_commits(run, mirror, src, installed: str) -> list | None:
    """Every commit the installed commit (captured, never the live HEAD) reaches that no branch or tag of the upstream reaches ("<short> <subject>", children first),
    all of them, also behind a merge: what an update deletes with the old checkout. None = not readable. The checkout
    may lack the upstream's tips (a shallow clone), so each remaining candidate is asked of the mirror, and a published
    one (with its ancestors) is excluded in turn."""
    refs = _git(run, mirror, "for-each-ref", "--format=%(objectname) %(*objectname)", "refs/heads", "refs/tags") \
        if mirror is not None else None
    if refs is None or refs.returncode != 0:
        return None
    stop = sorted({ln.split()[-1] for ln in refs.stdout.splitlines() if ln.split()})
    for _ in range(64):                                  # each round excludes one more published commit
        out = run(["git", "-C", str(src), "rev-list", "--topo-order", "--ignore-missing", installed, "--not", *stop],
                  timeout=10.0, env=_NO_LAZY)
        if out.returncode != 0:
            return None
        cands = out.stdout.split()
        for c in cands:
            if (pub := _published(run, mirror, c)) is None:
                return None
            if pub:
                stop.append(c)
                break
        else:
            if not cands:
                return []
            log = run(["git", "-C", str(src), "log", "--no-walk=unsorted", "--format=%h %s", *cands], timeout=10.0,
                      env=_NO_LAZY)
            return [ln.strip() for ln in log.stdout.splitlines() if ln.strip()] if log.returncode == 0 else None
    return None


def warning(lane: str, how: str, label: str, installed: str, own=()) -> str:
    """The plan's warning for a move `classify` placed `how` ("" for a plain forward move); `own` the commits it
    deletes (`own_commits`)."""
    if how == "own":
        return WARN_OWN.format(installed=installed[:9], n=len(own), commits="; ".join(own[:10]) + (
            f"; and {len(own) - 10} more" if len(own) > 10 else ""))
    if how in ("back", "sideways"):
        return WARN_LINE.format(lane=lane, label=label, how="older than" if how == "back" else "on another line than")
    return ""


def keeps(run, paths, src, comp, remote: str, selector: str, frozen: str) -> str:
    """Why auto-install leaves the installed source at `src` where it is ("" = it moves to its target, `frozen` or
    the pin): it already holds the target, or the target is back, sideways or over commits that are not upstream (a
    move only the user names), or that cannot be read. Auto-install never moves a source back or sideways (P-5)."""
    target = frozen or (comp.source.pin_commit if selector == "pinned" else "")
    if not target or comp.source.artifact:
        return ""
    installed = assess(run, src)
    mirror = fetch_history(run, remote, comp.source.branch, paths)[0]
    if installed and contains(run, mirror, src, installed, target):
        return f"already holds the {selector} target {target[:9]}"
    how = classify(run, mirror, installed, target) if installed else None
    if how == "forward":
        return ""
    why = {"sideways": f"the {selector} target {target[:9]} is on another line than the installed version",
           "own": f"the installed commit {installed[:9]} is not upstream"}.get(how or "", COULD_NOT_CHECK)
    return f"{why}; auto-install never moves it (to move it: lhpc update <stack> --source {selector})"


def update_gate(run, paths, items, source: str, remote_of):
    """An update's selector per source (`source`, or for a bare update "" each source's recorded one, none: pinned) and
    the gate on every installed git source: `(sel, refs, refused, kept, warned)`, refs the stable targets read for
    the plan (per source path), refused the per-source refusal lines (nothing moves), kept the lines of the sources a
    routine update leaves (per path: the checkout is ahead of the target), warned per path `(lines, HEAD)`: the plan's
    warnings and the HEAD they name (a confirmed move may replace that HEAD, nothing else). A named `--source`
    installs its target wherever it lies; a routine update moves only a source whose checkout lacks it. The plan runs
    it without locks; the apply runs it ONCE, under its locks."""
    from . import source_fs, source_registry
    sel = {c.source.path: source or source_registry.recorded_selector(paths, c.source.path) for _, c in items}
    refs, refused, kept, warned, done, target = {}, [], {}, {}, set(), resolver(run, paths)
    for _, c in items:
        path, lane = c.source.path, sel[c.source.path]
        if path in done or c.source.artifact or not source_fs.source_present(paths, src := paths.resolve_source(path)):
            continue
        done.add(path)
        remote, branch = remote_of(c), c.source.branch
        if lane == "stable":
            ref = refs[path] = target(remote, branch)
            if why := update_refusal(ref):
                refused.append(f"  {c.id}: {why}")
                continue
            want, mirror, label = ref.sha, Path(ref.history), ref.label
        elif not remote:
            continue                                       # a local source: nothing upstream to place it against
        else:
            want = c.source.pin_commit if lane == "pinned" else remote_tip(run, remote, branch or "HEAD", 15.0)
            if not want and lane == "dev" and not source and branch and c.source.pin_commit and (installed := assess(
                    run, src)) and is_final(target.history(remote, branch)[1]):   # gone: the pin only forward
                copy = held_copy(run, paths, remote, branch, installed)     # --source pinned places the pin with it
                way = "--source pinned moves it to the pin, after a warning" if copy else \
                    "to go to the pin: remove the stack, reinstall Known working"
                has = contains(run, copy, src, installed, c.source.pin_commit)
                how = classify(run, copy, installed, c.source.pin_commit) if has is False and copy else None
                if has is None or (has is False and how is None):          # no copy: retrying cannot help
                    refused.append(f"  {c.id}: " + (COULD_NOT_CHECK if copy else f"upstream branch {branch!r} gone and "
                                   f"this box holds no history placing the pin: nothing moves; {way}"))
                elif how != "forward":                                      # back, sideways, over own commits
                    kept[path] = f"  {c.id}: upstream branch {branch!r} gone; kept {installed[:9]}; {way}"
                    #                  a plain Update moves only forward (his rule); forward: the adoption's pin below
            if not want:
                continue                                   # no pin / dev unreachable: the adoption's own policy
                #                                            (dev falls back to the pin, disclosed)
            mirror, label = None, f"{'pinned' if lane == 'pinned' else 'development branch'} @ {want[:9]}"
        installed = assess(run, src)
        if not installed and lane != "stable":
            continue                                       # not a readable checkout: the adoption's identity decides
        if mirror is None and installed and installed != want:
            mirror, why = target.history(remote, branch)
            if mirror is None and is_final(why):
                mirror = held_copy(run, paths, remote, branch, installed)   # the branch is gone upstream: the box's copy
        if not source and installed:                       # routine: the badge's predicate first (never back)
            if (has := contains(run, mirror, src, installed, want)) is None:
                refused.append(f"  {c.id}: {COULD_NOT_CHECK}")
                continue
            if has:
                if installed != want:                      # ahead of it: kept; at it: the plan as before (no move)
                    kept[path] = (f"  {c.id}: the {lane} target ({label}) is not newer than the installed version: "
                                  f"kept (to install it anyway: --source {lane})")
                continue
        how = classify(run, mirror, installed, want) if installed else None
        own = own_commits(run, mirror, src, installed) if how == "own" else ()
        if how is None or own is None:
            refused.append(f"  {c.id}: {COULD_NOT_CHECK}")
            continue
        rec = source_registry.read_record(paths, path)
        lines = [ln for ln in (warning(lane, how, label, installed, own), how != "own" and rec is not None and
                               rec.resolved_commit and rec.resolved_commit != installed and WARN_MOVED.format(
                                   installed=installed[:9], recorded=rec.resolved_commit[:9])) if ln]
        if lines:
            warned[path] = ([f"  {c.id}: warning: {ln}" for ln in lines], installed)
    return sel, refs, refused, kept, warned


def update_refusal(ref: StableRef) -> str:
    """Why an installed source may not move to the stable `ref` ("" = it may): its branch is not on the remote
    (final), or the target could not be read ("try again later")."""
    if is_final(ref.why):
        return ref.why
    if not ref.sha:
        return TRY_LATER + (f" ({ref.why})" if ref.why and ref.why != TRY_LATER else "")
    return ""
