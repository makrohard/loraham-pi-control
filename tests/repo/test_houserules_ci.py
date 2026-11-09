"""The CI step that runs tools/check_houserules.py: the range tools/houserules_ci_range.sh picks for each event
and branch shape, on a throwaway git repository, and the workflow's floor. Red-before: neither the script nor the
step existed on the parent (afd0cfeb), so every case below failed there."""
import os
import re
import subprocess

import pytest

import repo_paths

SCRIPT = repo_paths.REPO / "tools" / "houserules_ci_range.sh"
WORKFLOW = repo_paths.REPO / ".github" / "workflows" / "ci.yml"
ZERO = "0" * 40


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                           "commit.gpgsign=false", *args], cwd=repo, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def shas(tmp_path):
    """old -> floor -> mid -> head on main; side branches off old (a hotfix) and off mid (a rewritten push)."""
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    out = {}
    for name in ("old", "floor", "mid", "head"):
        (r / f"{name}.txt").write_text(name)
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", name)
        out[name] = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "-b", "hotfix", out["old"])
    for name in ("hotfix1", "hotfix2"):
        (r / f"{name}.txt").write_text(name)
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", name)
        out[name] = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "-b", "rewritten", out["mid"])
    (r / "other.txt").write_text("other")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "other")
    out["other"] = _git(r, "rev-parse", "HEAD")
    out["repo"] = r
    return out


def _range(s, event, before, head, prbase=""):
    p = subprocess.run(["bash", str(SCRIPT), s["floor"], event, before, head, prbase], cwd=s["repo"],
                       capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip(), p.stderr


def test_a_fast_forward_push_checks_what_it_pushed(shas):
    assert _range(shas, "push", shas["mid"], shas["head"])[0] == f"{shas['mid']}..{shas['head']}"


@pytest.mark.parametrize("before", ["zero", "old", "other", "nonsense"])
def test_a_push_without_a_usable_before_checks_from_the_floor(shas, before):
    # all-zero (a new ref), older than the floor, not an ancestor of the head (a non-fast-forward push), unknown
    b = {"zero": ZERO, "nonsense": "f" * 40}.get(before, shas.get(before))
    assert _range(shas, "push", b, shas["head"])[0] == f"{shas['floor']}..{shas['head']}"


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_a_pull_request_and_a_dispatch_check_from_the_floor(shas, event):
    assert _range(shas, event, "", shas["head"], shas["mid"])[0] == f"{shas['floor']}..{shas['head']}"


@pytest.mark.parametrize("event, before, prbase, want", [
    ("push", "hotfix1", "", "hotfix1..hotfix2"),
    ("push", "old", "", "old..hotfix2"),                            # two commits pushed: never only HEAD^
    ("push", "zero", "", "hotfix1..hotfix2"),                       # HEAD^..HEAD
    ("pull_request", "", "old", "old..hotfix2"),
    ("workflow_dispatch", "", "", "hotfix1..hotfix2"),
])
def test_a_branch_off_the_floor_never_fails_and_checks_what_it_changed(shas, event, before, prbase, want):
    b = ZERO if before == "zero" else shas.get(before, "")
    got, note = _range(shas, event, b, shas["hotfix2"], shas.get(prbase, ""))
    base, head = want.split("..")
    assert got == f"{shas[base]}..{shas[head]}"
    assert "is not an ancestor" in note


def test_a_root_commit_off_the_floor_checks_nothing(shas):
    got, note = _range(shas, "workflow_dispatch", "", shas["old"])
    assert got == "" and "nothing to check" in note


def test_the_workflow_runs_the_range_and_the_checker_from_a_recorded_floor():
    text = WORKFLOW.read_text()
    m = re.search(r"^ +HR_FLOOR: ([0-9a-f]{40})$", text, re.MULTILINE)
    assert m, "HR_FLOOR is not one 40-hex sha"
    assert 'bash tools/houserules_ci_range.sh "$HR_FLOOR" "$EVENT" "$BEFORE" "$GITHUB_SHA" "$PR_BASE"' in text
    assert 'python tools/check_houserules.py "$range"' in text


@pytest.mark.needs_git_checkout
def test_the_floor_is_in_this_history():
    floor = re.search(r"HR_FLOOR: ([0-9a-f]{40})", WORKFLOW.read_text())[1]
    p = subprocess.run(["git", "merge-base", "--is-ancestor", floor, "HEAD"], cwd=repo_paths.REPO,
                       capture_output=True, check=False)
    assert p.returncode == 0, f"HR_FLOOR {floor} is not an ancestor of HEAD"
