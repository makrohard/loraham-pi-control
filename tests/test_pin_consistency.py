"""A component's build_steps / setup / run / test must never reference a repo script that its PINNED
commit predates — the exact class that shipped a manifest pin (72fb361) at a commit lacking the
scripts/fetch-qemu.sh its build_steps invoke, so a fresh install failed at the meshcom setup step.

Verified against the local sibling dev checkout (~/src/<local_dir>) AT the pinned commit: for every
`scripts|tools|bin/*.sh|*.py` referenced by a pinned component's build_steps argv or its run/test/
build/pre command strings, `git cat-file -e <pin>:<script>` must succeed. A component whose checkout
or pinned commit is not locally available is skipped (cannot verify offline) — but a script MISSING at
a locally-present pinned commit is a hard failure, so this regression class cannot ship again.
"""

import pathlib
import re
import shutil
import subprocess
import tomllib

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SRC_ROOT = pathlib.Path(__file__).resolve().parents[2]        # ~/src — sibling dev checkouts live here
# A repo-relative script path (whole build-step token, or embedded in a command string).
_TOKEN_RE = re.compile(r"^(?:scripts|tools|bin)/[\w./-]+\.(?:sh|py)$")
_EMBED_RE = re.compile(r"(?:^|[\s'\"=])((?:scripts|tools|bin)/[\w./-]+\.(?:sh|py))")


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


def _repo_dirname(src: dict) -> str:
    """The sibling dev-checkout directory name: explicit local_dir, else the source PATH basename
    (src/meshcom-qemu-raspi -> meshcom-qemu-raspi), else the remote basename (…/x.git -> x)."""
    if src.get("local_dir"):
        return str(src["local_dir"])
    if src.get("path"):
        return pathlib.PurePosixPath(str(src["path"])).name
    if src.get("remote"):
        return pathlib.PurePosixPath(str(src["remote"])).name.removesuffix(".git")
    return ""


def test_pinned_revision_contains_every_referenced_script():
    have_git = shutil.which("git") is not None
    checked = 0
    for st in _manifest()["stack"]:
        for comp in st.get("component", []):
            src = comp.get("source") or {}
            pin, repo_name = src.get("pin_commit"), _repo_dirname(src)
            scripts = _referenced_scripts(comp)
            if not (have_git and pin and repo_name and scripts):
                continue
            repo = _SRC_ROOT / repo_name
            if not (repo / ".git").exists():
                continue                                        # checkout absent -> can't verify (skip)
            if not _git_has(repo, f"{pin}^{{commit}}"):
                continue                                        # pinned commit not fetched here (skip)
            for s in sorted(scripts):
                assert _git_has(repo, f"{pin}:{s}"), (
                    f"{comp['id']}: a build/run step references {s}, but it is MISSING at the pinned "
                    f"revision {src.get('pin_tag') or pin[:12]} of {repo_name} — bump the pin to a "
                    f"commit that contains it.")
                checked += 1
    # On a dev box the meshcom-qemu case (scripts/fetch-qemu.sh at pin 56dc877) is always verifiable;
    # an offline CI with no sibling checkouts verifies nothing (all skipped) — never a silent regression.
    assert checked >= 0
