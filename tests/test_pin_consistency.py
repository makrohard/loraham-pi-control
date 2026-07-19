"""Authoritative pin validation — catches BOTH orphaned-pin failure classes:
  (a) a manifest pin that PREDATES a script its build_steps/run/test invoke;
  (b) a once-valid SHA ORPHANED by force-pushing/amending the source branch (no longer an ancestor of
      the branch tip) — the exact failure that hit twice (56dc877 -> 3ed3498 amend).

Local behavior (this test): if NO sibling checkout exists, an offline skip is allowed. But a PRESENT
sibling checkout HARD-FAILS unless the pin is a full 40-hex SHA, `git cat-file -e <pin>^{commit}`
resolves, the configured pinned-branch tip exists locally, `git merge-base --is-ancestor <pin> <tip>`
succeeds, and EVERY referenced script exists at the pin. A present checkout with a missing/orphaned
commit is NEVER treated like an absent checkout. The hosted cross-repository CI job (ci.yml) performs
the same checks against the live remote branch. Hosted/local both fail if zero consumers/scripts were
actually validated (no meaningless `assert >= 0`).
"""

import pathlib
import re
import shutil
import subprocess
import tomllib

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SRC_ROOT = pathlib.Path(__file__).resolve().parents[2]        # ~/src — sibling dev checkouts live here
_TOKEN_RE = re.compile(r"^(?:scripts|tools|bin)/[\w./-]+\.(?:sh|py)$")
_EMBED_RE = re.compile(r"(?:^|[\s'\"=])((?:scripts|tools|bin)/[\w./-]+\.(?:sh|py))")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _manifest() -> dict:
    return tomllib.loads((_REPO / "lhpc" / "data" / "manifest.example.toml").read_text())


def _referenced_scripts(comp: dict) -> set:
    scripts: set = set()
    for step in comp.get("build_steps", []):
        for tok in step.get("argv", []):
            if _TOKEN_RE.match(str(tok)):
                scripts.add(str(tok))
    for key in ("run", "test", "build", "pre"):
        val = comp.get(key)
        if isinstance(val, str):
            scripts.update(_EMBED_RE.findall(val))
    return scripts


def _git_has(repo: pathlib.Path, obj: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", obj],
                          capture_output=True).returncode == 0


def _is_ancestor(repo: pathlib.Path, pin: str, tip: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", pin, tip],
                          capture_output=True).returncode == 0


def _repo_dirname(src: dict) -> str:
    if src.get("local_dir"):
        return str(src["local_dir"])
    if src.get("path"):
        return pathlib.PurePosixPath(str(src["path"])).name
    if src.get("remote"):
        return pathlib.PurePosixPath(str(src["remote"])).name.removesuffix(".git")
    return ""


def _branch_tip_ref(repo: pathlib.Path, branch: str):
    for ref in (f"origin/{branch}", f"refs/remotes/origin/{branch}", branch, f"refs/heads/{branch}"):
        if _git_has(repo, ref):
            return ref
    return None


def test_pins_are_full_sha_and_shared_source_consumers_agree():
    # Manifest-only (always runs): every pinned component has a full 40-hex SHA, and consumers of a
    # SHARED source path agree on the identical pin_commit + pin_tag (no split pin).
    by_path: dict = {}
    for st in _manifest()["stack"]:
        for comp in st.get("component", []):
            src = comp.get("source") or {}
            if src.get("pin_commit"):
                assert _SHA_RE.match(src["pin_commit"]), \
                    f"{comp['id']}: pin_commit must be a full 40-hex SHA, got {src['pin_commit']!r}"
                by_path.setdefault(src.get("path", ""), []).append(
                    (comp["id"], src["pin_commit"], src.get("pin_tag", "")))
    shared = {p: v for p, v in by_path.items() if len(v) > 1}
    assert shared, "expected at least one shared-source pin group (meshcom-qemu-raspi)"
    for path, consumers in shared.items():
        assert len({c[1] for c in consumers}) == 1, f"{path}: split pin_commit {consumers}"
        assert len({c[2] for c in consumers}) == 1, f"{path}: split pin_tag {consumers}"


def test_pinned_revision_valid_ancestor_and_has_scripts():
    have_git = shutil.which("git") is not None
    validated_consumers = 0
    validated_scripts = 0
    checkouts_present = 0
    for st in _manifest()["stack"]:
        for comp in st.get("component", []):
            src = comp.get("source") or {}
            pin, repo_name = src.get("pin_commit"), _repo_dirname(src)
            scripts = _referenced_scripts(comp)
            if not (have_git and pin and repo_name and scripts):
                continue
            repo = _SRC_ROOT / repo_name
            if not (repo / ".git").exists():
                continue                                    # ABSENT checkout -> offline skip allowed
            checkouts_present += 1
            assert _SHA_RE.match(pin), f"{comp['id']}: pin must be a full 40-hex SHA"
            # A PRESENT checkout with a missing pinned object is the orphaned-pin signature -> HARD FAIL
            # (never treated like an absent checkout).
            assert _git_has(repo, f"{pin}^{{commit}}"), (
                f"{comp['id']}: pinned commit {pin} is NOT present in local {repo_name} — orphaned pin? "
                "(force-push/amend of the source branch, or a bad pin).")
            branch = src.get("branch") or "main"
            tip = _branch_tip_ref(repo, branch)
            assert tip is not None, f"{comp['id']}: pinned branch '{branch}' tip not found in {repo_name}"
            assert _is_ancestor(repo, pin, tip), (
                f"{comp['id']}: pinned commit {pin} is NOT an ancestor of {tip} in {repo_name} — "
                "orphaned by a force-push/amend.")
            for s in sorted(scripts):
                assert _git_has(repo, f"{pin}:{s}"), (
                    f"{comp['id']}: build/run step references {s}, MISSING at pinned "
                    f"{src.get('pin_tag') or pin[:12]} — bump the pin.")
                validated_scripts += 1
            validated_consumers += 1
    # If ANY sibling checkout was present, we must have actually validated consumers AND scripts —
    # a present checkout can never silently validate nothing.
    if checkouts_present:
        assert validated_consumers > 0 and validated_scripts > 0, \
            "a sibling checkout was present but no consumer/script was validated"


def test_orphaned_pin_is_detected_by_ancestry(tmp_path):
    # Deterministic proof of the force-push/amend ORPHAN signature: a commit that is NOT an ancestor of
    # the branch tip fails the ancestry check (and, after a branch-only fetch, would fail cat-file too).
    import os
    r = tmp_path / "r"; r.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def g(*a):
        return subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, env=env)

    g("init", "-q", "-b", "main")
    (r / "scripts").mkdir()
    (r / "scripts" / "x.sh").write_text("echo\n")
    g("add", "-A"); g("commit", "-q", "-m", "base")
    base = g("rev-parse", "HEAD").stdout.strip()
    g("checkout", "-q", "--detach")                       # an orphan commit NOT reachable from main
    (r / "scripts" / "x.sh").write_text("echo changed\n")
    g("add", "-A"); g("commit", "-q", "-m", "orphan")
    orphan = g("rev-parse", "HEAD").stdout.strip()
    g("checkout", "-q", "main")                           # main tip stays at `base`
    assert _git_has(r, f"{base}^{{commit}}") and _is_ancestor(r, base, "main")     # good pin
    assert not _is_ancestor(r, orphan, "main")            # orphaned pin -> HARD-FAIL signal
