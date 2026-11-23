"""The CI step that runs tools/check_houserules.py: the range tools/houserules_ci_range.sh picks for each event
and branch shape, on a throwaway git repository, and the workflow's floor. Red-before: neither the script nor the
step existed on the parent (afd0cfeb), so every case below failed there."""
import os
import re
import shutil
import subprocess
import sys

import pytest

import repo_paths

SCRIPT = repo_paths.REPO / "tools" / "houserules_ci_range.sh"
CHECKER = repo_paths.REPO / "tools" / "check_houserules.py"
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
    ("pull_request", "", "head", "old..hotfix2"),                   # its base moved on: from the merge base
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
    assert ('bash tools/houserules_ci_range.sh "$HR_FLOOR" "$EVENT" "$BEFORE" "${PR_HEAD:-$GITHUB_SHA}" "$PR_BASE"'
            in text)
    assert 'python tools/check_houserules.py "$range"' in text



def _step(name="House rules"):
    """The `run:` text of a ci.yml step, as the runner's bash gets it."""
    lines = WORKFLOW.read_text().splitlines()
    at = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}" or
              line.strip().startswith(f"- name: {name} "))
    run = next(i for i in range(at, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


def _c(repo, files, msg, author="makrohard"):
    for path, text in files.items():
        (repo / path).write_text(text)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    for args in (["add", "-A", "--", *files], ["commit", "-q", "-m", msg]):
        subprocess.run(["git", "-c", f"user.name={author}", "-c", "user.email=dev@example.invalid", "-c",
                        "commit.gpgsign=false", *args], cwd=repo, env=env, check=True, capture_output=True)
    return _git(repo, "rev-parse", "HEAD")


RELEASED = "## 0.1.1\n\n- a\n- b\n- c\n\n"


@pytest.fixture
def merge_back(tmp_path):
    """floor -> dev (opened 0.2.0); a release 0.1.1 tagged off the floor; a merge-back branch off dev: (1') a file,
    (2') the released section copied whole below 0.2.0; GitHub's synthetic merge of the branch into dev."""
    r = tmp_path / "r"
    r.mkdir()
    (r / "tools").mkdir()           # the step runs the checker and the range script from the checkout
    shutil.copy(SCRIPT, r / "tools")
    shutil.copy(CHECKER, r / "tools")
    _git(r, "init", "-q", "-b", "dev")
    (r / ".git" / "info" / "exclude").write_text("tools/\n")
    log = "# Changelog\n\n## 0.1.0\n\n- old entry\n"
    out = {"repo": r, "floor": _c(r, {"CHANGELOG.md": log}, "floor")}
    _git(r, "checkout", "-q", "-b", "release")
    _c(r, {"CHANGELOG.md": log.replace("## 0.1.0", RELEASED + "## 0.1.0")}, "0.1.1: changelog")
    _git(r, "tag", "v0.1.1")
    _git(r, "checkout", "-q", "dev")
    dev = log.replace("## 0.1.0", "## 0.2.0\n\n- y\n\n## 0.1.0")
    out["dev"] = _c(r, {"CHANGELOG.md": dev, "dev.txt": "y\n"}, "item\n\nred-before: none, a file\n")
    _git(r, "checkout", "-q", "-b", "integrate")
    _c(r, {"moves.txt": "pins\n"}, "0.1.1 (released from main)")
    out["head"] = _c(r, {"CHANGELOG.md": dev.replace("## 0.1.0", RELEASED + "## 0.1.0")},
                     "0.1.1: changelog (released from main)")
    _git(r, "checkout", "-q", "dev")
    _git(r, "-c", "user.name=makrohard", "merge", "-q", "--no-ff", "-m", "Merge integrate", "integrate")
    out["merge"] = _git(r, "rev-parse", "HEAD")
    return out


def _hr_step(s, event, sha, pr_head="", before="", pr_base=""):
    env = {**os.environ, "HR_FLOOR": s["floor"], "EVENT": event, "BEFORE": before, "GITHUB_SHA": sha,
           "PR_BASE": pr_base, "PR_HEAD": pr_head,
           "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")}
    return subprocess.run(["bash", "-e", "-c", _step()], cwd=s["repo"], env=env, capture_output=True, text=True,
                          check=False)


def test_a_pull_request_is_judged_on_its_own_head_not_the_synthetic_merge(merge_back):
    """GitHub runs a pull request's CI on refs/pull/N/merge: a merge with dev first, whose own diff carries every
    bullet the PR brings, so the bot's merge-back PR was HARD however its commits were cut, and that merge never
    lands (dev takes a fast-forward). Red before (the step handed $GITHUB_SHA on): exit 1, changelog HARD."""
    s = merge_back
    p = _hr_step(s, "pull_request", s["merge"], pr_head=s["head"], pr_base=s["dev"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"house rules range: {s['floor']}..{s['head']}" in p.stdout


def test_a_pull_requests_own_commit_that_breaks_a_rule_is_still_hard(merge_back):
    s = merge_back
    r = s["repo"]
    _git(r, "checkout", "-q", "integrate")
    log = (r / "CHANGELOG.md").read_text().replace("- y\n", "- y\n- p\n- q\n- r\n")
    head = _c(r, {"CHANGELOG.md": log, "more.txt": "x\n"}, "item\n\nred-before: none\n")
    p = _hr_step(s, "pull_request", s["merge"], pr_head=head, pr_base=s["dev"])
    assert p.returncode == 1 and re.search(r"^changelog .* HARD$", p.stdout, re.MULTILINE), p.stdout


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_push_and_a_dispatch_still_end_the_range_at_their_own_sha(merge_back, event):
    s = merge_back
    p = _hr_step(s, event, s["dev"], before=s["floor"] if event == "push" else "")
    assert f"house rules range: {s['floor']}..{s['dev']}" in p.stdout, p.stdout + p.stderr


def test_a_pull_request_cut_before_the_floor_is_judged_on_every_commit_it_brings(tmp_path):
    """A pull request head that does not contain the floor (cut before it) is judged from its merge base with the
    pull request's base: an earlier commit of it that breaks a rule is HARD. Red before: the range fell back to
    HEAD^..HEAD (the base had moved on, so it was no ancestor of the head) and the earlier commit escaped."""
    r = tmp_path / "r"
    r.mkdir()
    (r / "tools").mkdir()
    shutil.copy(SCRIPT, r / "tools")
    shutil.copy(CHECKER, r / "tools")
    _git(r, "init", "-q", "-b", "dev")
    (r / ".git" / "info" / "exclude").write_text("tools/\n")
    log = "# Changelog\n\n## 0.2.0\n\n## 0.1.0\n\n- old entry\n"
    old = _c(r, {"CHANGELOG.md": log}, "old")
    floor = _c(r, {"floor.txt": "f\n"}, "floor")
    _git(r, "checkout", "-q", "-b", "topic", old)
    _c(r, {"CHANGELOG.md": log.replace("## 0.1.0", "- p\n- q\n- r\n\n## 0.1.0"), "a.txt": "a\n"}, "three\n")
    head = _c(r, {"b.txt": "b\n"}, "harmless\n")
    _git(r, "checkout", "-q", "dev")
    _git(r, "-c", "user.name=makrohard", "merge", "-q", "--no-ff", "-m", "Merge topic", "topic")
    s = {"repo": r, "floor": floor}
    p = _hr_step(s, "pull_request", _git(r, "rev-parse", "HEAD"), pr_head=head, pr_base=floor)
    assert f"house rules range: {old}..{head}" in p.stdout, p.stdout + p.stderr
    assert p.returncode == 1 and re.search(r"^changelog .* HARD$", p.stdout, re.MULTILINE), p.stdout
