"""tools/check_houserules.py against a throwaway git repository: each HARD condition fails the
range with that line HARD, each WARN condition passes it with that line WARN, a clean range passes.

The checker is a new invariant, so these are negative and positive fixture tests; no parent tree
existed for a red-before. Assertions read the exit code and each line's verdict token, never prose.
"""
import os
import subprocess
import sys

import pytest

import repo_paths

CHECKER = repo_paths.REPO / "tools" / "check_houserules.py"
CHANGELOG = "# Changelog\n\n## Unreleased\n\n## 0.1.0\n\n- old entry\n"
BUDGET = 'BUDGETS = {{\n    "services.py": (10, 3),\n    "service_x.py": {x},\n}}\n'
BASE = {
    "CHANGELOG.md": CHANGELOG,
    "docs/a.md": "Alpha fact.\n",
    "docs/b.md": "Beta.\n",
    "lhpc/core/services.py": "from .service_x import X\n\nclass S(X):\n    def run(self):\n        pass\n",
    "lhpc/core/service_x.py": "class X:\n    def a(self):\n        pass\n",
    "tests/repo/test_module_budgets.py": BUDGET.format(x="(3, 1)"),
    "tests/core/test_ok.py": "def test_one():\n    assert 1\n",
}
RED = "item\n\nred-before: AssertionError on the parent\n"
SL = "sle" + "ep"     # assembled, so that this file's own data does not hit the wall-clock rule
TSL = "time." + SL    # nor the base program's textual sleep pattern


def _git(repo, *args, author="makrohard"):
    # An exported GIT_AUTHOR_*/GIT_COMMITTER_* overrides `-c user.name`: the fixture's identity is the one given.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    r = subprocess.run(["git", "-c", f"user.name={author}", "-c", "user.email=dev@example.invalid",
                        "-c", "commit.gpgsign=false", *args], cwd=repo, env=env, check=False, capture_output=True,
                       text=True)
    if r.returncode:                    # F9: git's own reason, not only its exit status
        raise RuntimeError(f"git {' '.join(args)}: exit {r.returncode}: {r.stderr.strip()}")


def _write(repo, files):
    for path, text in files.items():
        p = repo / path
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text) if isinstance(text, bytes) else p.write_text(text)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _write(r, BASE)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "tag", "base")
    return r


def _commit(repo, files, msg=RED, author="makrohard"):
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, author=author)


def _run(repo, *extra):
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD", *extra], cwd=repo,
                       capture_output=True, text=True, check=False)
    rows = {line.split()[0]: line.split()[-1] for line in p.stdout.splitlines()[1:] if line.strip()}
    rows["VERDICT"] = p.stdout.split("VERDICT", 1)[1].split()[0] if "VERDICT" in p.stdout else None
    return p.returncode, rows


LINES = ("changelog", "docs", "tests", "patterns", "markers", "wallclock", "attribution", "modules")


def _others(line):
    # Every fixture violates exactly one condition: all other lines stay OK.
    return [n for n in LINES if n != line]


def _test(body):
    return {"tests/core/test_ok.py": BASE["tests/core/test_ok.py"] + body}


HARD = {
    "changelog-three-bullets": ({"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n",   # with a file beside it
                                 "## Unreleased\n\n- a\n- b\n- c\n"), "notes.txt": "neutral\n"}, RED, "changelog"),
    "changelog-below-top": ({"CHANGELOG.md": CHANGELOG + "- late\n"}, RED, "changelog"),
    "changelog-bullet-over-two-lines": ({"CHANGELOG.md": CHANGELOG.replace(   # the maintainer's "one or two lines"
        "## Unreleased\n", "## Unreleased\n\n- a\n  b\n  c\n")}, RED, "changelog"),
    # One line fewer, one public method more, within every cap: only the method growth is wrong.
    "family-public-growth": ({"lhpc/core/services.py": "from .service_x import X\nclass S(X):\n"
                              "    def run(self): pass\n    def more(self): pass\n"},
                             RED, "modules"),
    "test-outside-layers": ({"tests/misc/test_x.py": "def test_x():\n    assert 1\n"}, RED, "tests"),
    "wallclock-sleep": (_test("\ndef test_two():\n    import time\n    " + TSL + "(0)\n    assert 2\n"), RED, "wallclock"),
    "assert-true": (_test("\ndef test_two():\n    assert True\n"), RED, "patterns"),
    # The patterns are assembled so that this file does not itself match them.
    "getsource": (_test("\ndef test_two():\n    import inspect\n    assert inspect." + "getsource(len)\n"),
                  RED, "patterns"),
    "co-author-trailer": ({"notes.txt": "neutral\n"},
                          "item\n\n" + "Co-" + "Authored-By: someone <x@example.invalid>\n", "attribution"),
    "over-budget": ({"lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n",
                     "lhpc/core/services.py": BASE["lhpc/core/services.py"].replace("\n\n", "\n", 1)},
                    RED, "modules"),
    "family-net-growth": ({"lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"}, RED, "modules"),
    "raise-excuses-only-its-own-file": ({"tests/repo/test_module_budgets.py":
                                         BUDGET.format(x="(4, 1),  # item-9: one line"),
                                         "lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n",
                                         "lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"},
                                        RED, "modules"),
    "raised-without-reason": ({"tests/repo/test_module_budgets.py": BUDGET.format(x="(4, 1)")},
                              RED, "modules"),
}


@pytest.mark.parametrize("case", sorted(HARD))
def test_each_hard_condition_fails_the_range(repo, case):
    files, msg, line = HARD[case]
    _commit(repo, files, msg)
    rc, rows = _run(repo)
    assert (rc, rows[line], rows["VERDICT"]) == (1, "HARD", "FAIL")
    assert {k: v for k, v in rows.items() if k not in (line, "VERDICT")} == dict.fromkeys(_others(line), "OK")


def test_a_foreign_author_fails_the_range(repo):
    _commit(repo, {"docs/a.md": "Alpha fact.\nMore.\n"}, author="someone")
    rc, rows = _run(repo, "--fact", "More.")
    assert (rc, rows["attribution"]) == (1, "HARD")


def test_an_ambient_git_identity_does_not_mask_a_foreign_author(repo, monkeypatch):
    """The release shell exports its identity; git's GIT_AUTHOR_*/GIT_COMMITTER_* override `-c user.name`, so
    the fixture's foreign commit was made by makrohard and the HARD never showed. Red before: attribution OK."""
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "makrohard")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "dev@example.invalid")
    _commit(repo, {"docs/a.md": "Alpha fact.\nMore.\n"}, author="someone")
    rc, rows = _run(repo, "--fact", "More.")
    assert (rc, rows["attribution"]) == (1, "HARD")


@pytest.mark.parametrize("per_commit, verdict", [((1, 1, 1), "OK"), ((1, 1), "OK"), ((3,), "HARD")])
def test_changelog_bullets_are_counted_per_commit(repo, per_commit, verdict):
    """A topic of several items adds a bullet per item: the limit of two holds per commit, not over the range
    (0.13.0's precheck failed a clean three-item range). Red before: three commits of one bullet each, HARD."""
    entries = []
    for n, count in enumerate(per_commit):
        entries += [f"- item {n} line {k}" for k in range(count)]
        text = CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n" + "\n".join(entries) + "\n")
        _commit(repo, {"CHANGELOG.md": text, f"notes{n}.txt": "item\n"})    # an item: its change and its bullets
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (int(verdict == "HARD"), verdict)


TOP = "## Unreleased\n\n- a\n- b\n- c\n"
RELEASE_SECTION = {   # one CHANGELOG-only commit with three bullets: (its CHANGELOG, extra files, options, verdict)
    "top-section": (CHANGELOG.replace("## Unreleased\n", TOP), {}, (), "OK"),
    "new-top-section-with-heading": (CHANGELOG.replace("## Unreleased\n", "## 0.2.0\n\n- a\n- b\n- c\n\n"
                                                       "## Unreleased\n"), {}, (), "OK"),
    "beside-another-file": (CHANGELOG.replace("## Unreleased\n", TOP), {"notes.txt": "x\n"}, (), "HARD"),
    # the other file's added lines (4, 5) lie at the top section's line numbers (3 to 8): only the file test refuses
    "beside-a-file-at-the-top-lines": (CHANGELOG.replace("## Unreleased\n", TOP),
                                       {"lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"]
                                        + "    def b(self):\n        pass\n"}, (), "HARD"),
    "older-section": (CHANGELOG + "- a\n- b\n- c\n", {}, ("--correction",), "HARD"),
    "beside-a-deleted-file": (CHANGELOG.replace("## Unreleased\n", TOP), {"docs/b.md": None}, (), "HARD"),
    "new-section-below-the-top": (CHANGELOG.replace("## 0.1.0\n", "- a\n- b\n- c\n## 0.1.1\n## 0.1.0\n"), {}, (),
                                  "HARD"),                     # its last added line is the second heading
    "above-the-top-heading": (CHANGELOG.replace("# Changelog\n", "# Changelog\n- a\n- b\n- c\n"), {}, (), "HARD"),
    "no-version-heading": ("# Changelog\n\n- a\n- b\n- c\n", {}, (), "HARD"),
    "an-older-section-line-deleted": (CHANGELOG.replace("## Unreleased\n", TOP).replace("- old entry\n", ""), {}, (),
                                      "HARD"),
}


@pytest.mark.parametrize("case", sorted(RELEASE_SECTION))
def test_the_release_section_commit_is_not_capped(repo, case):
    """The release's CHANGELOG section is written once, before the tag (docs/maintenance.md): a commit that changes
    only CHANGELOG.md, every added line inside its top section, is not held to two bullets; any other commit is.
    Red before: top-section and new-top-section-with-heading HARD (the cap held every commit); an-older-section-
    line-deleted OK (only the added lines were judged)."""
    text, extra, options, verdict = RELEASE_SECTION[case]
    _commit(repo, {"CHANGELOG.md": text, **extra})
    rc, rows = _run(repo, *options)
    assert (rc, rows["changelog"]) == (int(verdict == "HARD"), verdict)


SECTION = "## 0.1.1\n\n- a\n- b\n- c\n\n"
PLACED = CHANGELOG.replace("## 0.1.0\n", SECTION + "## 0.1.0\n")      # below the newer `## Unreleased`


def _released(repo, section=SECTION, tag="v0.1.1"):
    """A release off the base, tagged, its section on top there; the main line stays where it was."""
    _git(repo, "checkout", "-q", "-b", "release")
    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n\n", section)}, "0.1.1: changelog\n")
    _git(repo, "tag", tag)
    _git(repo, "checkout", "-q", "main")


def test_a_released_section_copied_whole_below_a_newer_cycle_is_not_held(repo):
    """The merge-back of a release into a `dev` that opened a newer cycle puts the released section, as tagged,
    below that cycle: one CHANGELOG-alone commit with three bullets outside the top section. It is the release's
    section written once, not three new entries or a correction. Red before: HARD (capped, and below the top)."""
    _released(repo)
    _commit(repo, {"CHANGELOG.md": PLACED}, "0.1.1: changelog (released from main)\n")
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (0, "OK")


PLACED_NOT = {   # each one CHANGELOG commit after the tag; nothing wider than the released section copied whole passes
    "beside-another-file": ({"CHANGELOG.md": PLACED, "notes.txt": "x\n"}, "v0.1.1"),
    "two-sections": ({"CHANGELOG.md": CHANGELOG.replace("## 0.1.0\n", SECTION + "## 0.1.2\n\n- d\n\n## 0.1.0\n")},
                     "v0.1.1"),
    "a-bullet-elsewhere": ({"CHANGELOG.md": PLACED + "- late\n"}, "v0.1.1"),
    "a-blank-line-elsewhere": ({"CHANGELOG.md": PLACED + "\n"}, "v0.1.1"),
    "a-line-deleted": ({"CHANGELOG.md": PLACED.replace("- old entry\n", "")}, "v0.1.1"),
    "the-heading-alone-its-bullets-appended-elsewhere": (
        {"CHANGELOG.md": CHANGELOG.replace("## 0.1.0\n", "## 0.1.1\n\n## 0.1.0\n") + "- a\n- b\n- c\n"}, "v0.1.1"),
    "differs-from-the-tag": ({"CHANGELOG.md": PLACED.replace("- c\n", "- c\n- d\n")}, "v0.1.1"),
    "no-tag": ({"CHANGELOG.md": PLACED}, "v0.1.9"),
}


@pytest.mark.parametrize("case", sorted(PLACED_NOT))
def test_nothing_wider_than_the_released_section_copied_whole_passes(repo, case):
    files, tag = PLACED_NOT[case]
    _released(repo, tag=tag)
    _commit(repo, files, "0.1.1: changelog (released from main)\n")
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


@pytest.mark.parametrize("later", [PLACED.replace("- c\n", "- c\n- late\n"), PLACED.replace("- a\n", "- a edited\n")],
                         ids=["a-bullet-added-into-it", "a-bullet-of-it-edited"])
def test_a_later_commit_into_the_placed_section_is_held(repo, later):
    """The placed section is exempt only as released: a later commit of the same range that adds or edits a bullet
    in it corrects a released section and is judged so. Red before (the whole section's lines were dropped from the
    placement rule): OK."""
    _released(repo)
    _commit(repo, {"CHANGELOG.md": PLACED}, "0.1.1: changelog (released from main)\n")
    _commit(repo, {"CHANGELOG.md": later})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


def test_a_merge_bringing_the_placed_section_is_held(repo):
    """A merge is never a released-section commit: its own diff against its first parent carries the section's
    bullets and is capped."""
    _released(repo)
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, {"CHANGELOG.md": PLACED}, "0.1.1: changelog (released from main)\n")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"x.txt": "x\n"})
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


def test_a_second_copy_of_a_released_section_is_held(repo):
    _released(repo)
    _commit(repo, {"CHANGELOG.md": PLACED}, "0.1.1: changelog (released from main)\n")
    _commit(repo, {"CHANGELOG.md": PLACED + "\n" + SECTION}, "again\n")       # one more copy, at the end
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


@pytest.mark.parametrize("side", [{"notes.txt": "x\n"},                                  # another file
                                  {"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- a\n")}],
                         ids=["brings-in-a-file", "brings-in-a-bullet"])
def test_a_merge_is_never_a_release_section_commit(repo, side):
    """A merge that brings in a side commit and adds bullets itself: git's combined diff names CHANGELOG.md only.
    Against its first parent it changes the other file too, or (a bullet brought in) only CHANGELOG.md with three
    bullets: a merge is never exempt, and its bullets count. Red before: OK."""
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, side)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", TOP)})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


def test_a_deletion_from_a_parent_without_a_version_section_is_not_exempt(repo):
    """The parent has no `## ` heading, so a deleted line lies inside no top section: the commit that writes the
    first section over it is capped. Red before: OK (only the added lines were judged)."""
    _commit(repo, {"CHANGELOG.md": "# Changelog\n\nintro\n"})
    _commit(repo, {"CHANGELOG.md": "# Changelog\n\n## 0.2.0\n\n- a\n- b\n- c\n"})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


def test_a_rename_into_changelog_is_not_a_release_section_commit(repo):
    """CHANGELOG.md moved away, then moved back with three bullets: with rename detection the second commit names
    CHANGELOG.md only; without it, it adds CHANGELOG.md and deletes the other path. Red before: OK."""
    _commit(repo, {"CHANGELOG.md": None, "old.md": CHANGELOG})
    _commit(repo, {"old.md": None, "CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", TOP)})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


TOP_ONE = {"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- a\n"), "notes.txt": "x\n"}


def test_a_pure_rename_into_changelog_counts_the_bullets_it_brings(repo):
    """A file with three bullets renamed, unchanged, into CHANGELOG.md: with rename detection its diff adds no line
    and the cap counted none; renames off, the commit adds the whole file and its three bullets count. Red with
    `--no-renames` dropped from own_diff: OK."""
    _commit(repo, {"CHANGELOG.md": None, "old.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- a\n- b\n")})
    _commit(repo, {"old.md": None, "CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- a\n- b\n")})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


@pytest.mark.parametrize("before, text", [({}, CHANGELOG.replace("- old entry\n", "")),
                                          (TOP_ONE, CHANGELOG),       # an item's bullet, then that bullet deleted
                                          ({}, "# Changelog\n")],
                         ids=["older-section-deletion", "top-section-deletion", "every-heading-deleted"])
def test_a_commit_without_an_added_bullet_is_not_a_release_section_commit(repo, before, text):
    """It adds no bullet, so the cap passes it anyway; it is not named as exempt either. Red before: the row
    said "1 release-section commit(s) not capped"."""
    if before:
        _commit(repo, before)
    _commit(repo, {"CHANGELOG.md": text})
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo, capture_output=True, text=True,
                       check=False)
    row = next(line for line in p.stdout.splitlines() if line.startswith("changelog"))
    assert p.returncode == 0 and "release-section" not in row


def test_a_bullet_grown_over_two_commits_past_two_lines_fails(repo):
    """The per-commit count must not lose a bullet's length: commit 2's lone continuation line has no bullet
    of its own, so the length is read over the range. Red before: OK (the line was dropped)."""
    two = CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- item\n  line two\n")
    _commit(repo, {"CHANGELOG.md": two})
    _commit(repo, {"CHANGELOG.md": two.replace("  line two\n", "  line two\n  line three\n")})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (1, "HARD")


WARN = {
    "correction-of-a-released-section": ({"CHANGELOG.md": CHANGELOG + "- late\n"}, RED,
                                         ("--correction",), "changelog"),
    "docs-without-fact": ({"docs/a.md": "Alpha fact.\nMore.\n"}, RED, (), "docs"),
    "fact-in-two-files": ({"docs/b.md": "Beta.\nAlpha fact.\n"}, RED, ("--fact", "Alpha fact."), "docs"),
    "test-without-red-before-wording": (_test("\ndef test_two():\n    assert 2\n"), "item\n", (), "tests"),
    "timed-loop-without-slow": (_test("\n@pytest.mark.wallclock('a timed loop')\ndef test_two():\n    import time\n"
                                      "    while time.monotonic() < 0:\n        pass\n    assert 2\n"),
                                RED, (), "markers"),
    "attribution-word": ({"docs/a.md": "Alpha fact.\n" + "Clau" + "de wrote this.\n"}, RED,
                         ("--fact", "wrote this"), "attribution"),
    "word-in-a-message": ({"docs/a.md": "Alpha fact.\nMore.\n"}, "item\n\nwritten in one " + "ses" + "sion\n",
                          ("--fact", "More."), "attribution"),
    "raised-with-reason": ({"tests/repo/test_module_budgets.py": BUDGET.format(x="(4, 1),  # item-9: one line"),
                            "lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n"},
                           RED, (), "modules"),
}


@pytest.mark.parametrize("case", sorted(WARN))
def test_each_warn_condition_passes_with_a_warning(repo, case):
    files, msg, extra, line = WARN[case]
    _commit(repo, files, msg)
    rc, rows = _run(repo, *extra)
    assert (rc, rows[line], rows["VERDICT"]) == (0, "WARN", "PASS")
    assert {k: v for k, v in rows.items() if k not in (line, "VERDICT")} == dict.fromkeys(_others(line), "OK")


@pytest.mark.parametrize("case, files, line, verdict", [
    # The added "++ x" reads "+++ x" in the diff: the word line after it in the same file still counts.
    ("same-file", {"docs/a.md": "Alpha fact.\n++ x\nWritten in one " + "ses" + "sion.\n"}, "attribution", "WARN"),
    # After a hunk, the next file's "+++ b/" header is still a header: its added lines keep their path.
    ("next-file", {"docs/a.md": "Alpha fact.\n++ x\n", **_test("\ndef test_two():\n    assert True\n")},
     "patterns", "HARD"),
])
def test_an_added_line_starting_with_plus_plus_hides_no_line(repo, case, files, line, verdict):
    _commit(repo, files)
    rc, rows = _run(repo, "--fact", "Alpha fact.")
    assert (rc, rows[line]) == ({"WARN": 0, "HARD": 1}[verdict], verdict)


def _merge(repo, side, merged, msg="merge\n"):
    """A merge of a side branch whose one commit writes `side`; the merge commit writes `merged`."""
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, side)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    _commit(repo, merged, msg=msg)


def test_a_merges_combined_diff_keeps_each_files_path(repo, monkeypatch):
    # The per-commit test count reads `git show` of each commit; for a merge that is a combined diff ("diff --cc", a
    # column per parent), read here directly. Added is what is new against every parent ("++"): the side's line the
    # merge brings in ("+ ") is not, yet it moves the line count. The second file's header closes the first's hunk.
    import importlib.util
    _merge(repo, {"docs/a.md": "Alpha fact.\nside\n"}, {"docs/a.md": "Alpha fact.\nside\none\n", "docs/b.md": "Beta.\ntwo\n"})
    spec = importlib.util.spec_from_file_location("check_houserules_cc", CHECKER)
    hr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hr)
    monkeypatch.chdir(repo)
    diff = hr.patch("show", "-M", "--format=", "HEAD")
    assert "diff --cc docs/b.md" in diff and "\n+ side\n" in diff     # anchor: the combined form, a mixed column
    assert hr.added_lines(diff) == [("docs/a.md", 3, "one"), ("docs/b.md", 2, "two")]


def test_a_merge_that_adds_a_test_itself_needs_its_red_before(repo):
    # The side's test comes in with the side's commit (red-before stated there); the test the merge adds against both
    # parents is the merge's own. Red before: the merge's "++def test_" line read "+def test_", no test.
    side = BASE["tests/core/test_ok.py"] + "\ndef test_side():\n    assert 2\n"
    _merge(repo, {"tests/core/test_ok.py": side}, {"tests/core/test_ok.py": side + "\ndef test_three():\n    assert 3\n"})
    assert _line(repo, "tests").endswith("red-before 1/2 WARN")


def test_a_clean_range_passes_with_every_line_ok(repo):
    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- one line\n"),
                   "docs/a.md": "Alpha fact.\nGamma.\n",
                   "tests/core/test_ok.py": BASE["tests/core/test_ok.py"] + "\ndef test_two():\n    assert 2\n"})
    rc, rows = _run(repo, "--fact", "Gamma.")
    assert rc == 0 and set(rows.values()) == {"OK", "PASS"}


def test_product_tokens_are_not_attribution_words(repo):
    # An HTTP header and pytest's own fixture scope / marker contain the words; they are exempt.
    _commit(repo, _test('\nimport pytest\n\n\n@pytest.fixture(scope="' + "ses" + 'sion")\ndef f():\n'
                        '    return {"User-' + "Ag" + 'ent": "x"}  # signals the ' + "ses" + 'sion leader\n'))
    rc, rows = _run(repo)
    assert (rc, rows["attribution"]) == (0, "OK")


def test_flasks_request_object_in_code_is_not_an_attribution_word(repo):
    # Quoted console code (`session["_csrf"]`, `session.pop(...)`) is the product's object; a
    # sentence that ends in the word is still counted.
    word = "ses" + "sion"
    _commit(repo, _test(f'\ndef helper(s):\n    {word}["_csrf"] = s\n    return {word}.pop("x", None)\n'))
    rc, rows = _run(repo)
    assert (rc, rows["attribution"]) == (0, "OK")
    _commit(repo, {"docs/a.md": f"Alpha fact.\nWritten in one {word}.\n"})
    rc, rows = _run(repo, "--fact", "Alpha fact.")
    assert rows["attribution"] == "WARN"


def _attribution(repo):
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo, capture_output=True, text=True,
                       check=False)
    line = next(x for x in p.stdout.splitlines() if x.startswith("attribution"))
    return p.returncode, line.split()[-1], line.split("; words ", 1)[1].rsplit(None, 1)[0]


W = "Clau" + "de"
F3, F4 = "`" * 3, "`" * 4
CREDITS = f"Reviewed by {W}\nApproved by {W}\n"
# name: (files at <base>, files the range writes, where the words are at <head>). Each moves the text of <base>; no
# move cancels, so every added line counts (the review of the WARN reads the named lines).
MOVES = {
    "a block moved unchanged": ({"docs/a.md": f"Alpha fact.\nOne {W} line.\nA plain line.\n"},
                                {"docs/a.md": "Alpha fact.\n", "docs/b.md": f"Beta.\nOne {W} line.\nA plain line.\n"},
                                "docs/b.md:2"),
    "out of a fenced example (G2 R1)": ({"docs/a.md": f"Do not write:\n{F3}\nReviewed by {W}\n{F3}\n"},
                                        {"docs/a.md": f"Do not write:\n{F3}\n{F3}\n", "docs/b.md": f"Credits\nReviewed by {W}\n"},
                                        "docs/b.md:2"),
    "out of an HTML comment (G2 R2)": ({"docs/a.md": f"Alpha fact.\n<!-- never:\n{CREDITS}-->\n"},
                                       {"docs/a.md": "Alpha fact.\n<!-- never:\n-->\n", "docs/b.md": f"Credits\n{CREDITS}"},
                                       "docs/b.md:2, docs/b.md:3"),
    "out of a fence inside a longer fence (G2 R2)": (
        {"docs/a.md": f"Alpha fact.\n{F4}\n{F3}\n{CREDITS}{F3}\n{F4}\n"},
        {"docs/a.md": f"Alpha fact.\n{F4}\n{F3}\n{F3}\n{F4}\n", "docs/b.md": f"Credits\n{CREDITS}"},
        "docs/b.md:2, docs/b.md:3"),
    "out of a path with a space (G2 R2)": ({"docs/an example.md": f"{F3}\n{CREDITS}{F3}\n"},
                                           {"docs/an example.md": f"{F3}\n{F3}\n", "docs/b.md": f"Credits\n{CREDITS}"},
                                           "docs/b.md:2, docs/b.md:3"),
    "into a path with a space": ({"docs/a.md": f"{F3}\n{CREDITS}{F3}\n"},
                                 {"docs/a.md": f"{F3}\n{F3}\n", "docs/an example.md": f"Credits\n{CREDITS}"},
                                 "docs/an example.md:2, docs/an example.md:3"),
    "into a path git quotes (G2 R2)": ({"docs/a.md": f"{F3}\n{CREDITS}{F3}\n"},
                                       {"docs/a.md": f"{F3}\n{F3}\n", "docs/über.md": f"Credits\n{CREDITS}"},
                                       "docs/über.md:2, docs/über.md:3"),
    "into a quoted path with a space": ({"docs/a.md": f"{F3}\n{CREDITS}{F3}\n"},
                                        {"docs/a.md": f"{F3}\n{F3}\n", "docs/\u00fc x.md": f"Credits\n{CREDITS}"},
                                        "docs/\u00fc x.md:2, docs/\u00fc x.md:3"),
    "a whole fenced example moved (G2 R2)": ({"docs/a.md": f"Alpha fact.\n{F3}\n{CREDITS}{F3}\n"},
                                             {"docs/a.md": "Alpha fact.\n", "docs/b.md": f"Beta.\n{F3}\n{CREDITS}{F3}\n"},
                                             "docs/b.md:3, docs/b.md:4"),
}


@pytest.mark.parametrize("name", sorted(MOVES))
def test_every_added_line_counts_and_the_row_names_where(repo, name):
    before, after, where = MOVES[name]
    _commit(repo, before)
    _git(repo, "tag", "-f", "base")
    _commit(repo, after)
    assert _attribution(repo) == (0, "WARN", f"{where.count(',') + 1} at {where}")


def test_a_message_word_is_named_by_its_commit(repo):
    _commit(repo, {"docs/a.md": "Alpha fact.\nMore.\n"}, msg=RED + "written in one " + "ses" + "sion\n")
    sha = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=repo, capture_output=True, text=True,
                         check=True).stdout.strip()
    assert _attribution(repo) == (0, "WARN", f"1 at commit {sha}")


def test_a_header_without_the_b_prefix_still_counts(repo):
    # A user's diff.noprefix drops git's a/ b/ prefixes: the header's path is kept whole, so its lines still count.
    _git(repo, "config", "diff.noprefix", "true")
    _commit(repo, {"docs/b.md": f"Credits\nReviewed by {W}\n"})
    assert _attribution(repo) == (0, "WARN", "1 at docs/b.md:2")


@pytest.mark.parametrize("edited", [False, True])
def test_a_file_renamed_unchanged_adds_no_word(repo, edited):
    # A rename with identical content has no hunk (-M100%), even when the user's git turns rename detection off; an
    # edited rename counts all its lines.
    _commit(repo, {"docs/a.md": f"Alpha fact.\nOne {W} line.\n"})
    _git(repo, "tag", "-f", "base")
    _git(repo, "config", "diff.renames", "false")
    _git(repo, "mv", "docs/a.md", "docs/c.md")
    _commit(repo, {"docs/c.md": f"Alpha fact.\nOne {W} line.\n" + ("Omega.\n" if edited else "")})
    assert _attribution(repo) == ((0, "WARN", "1 at docs/c.md:2") if edited else (0, "OK", "0"))


def _line(repo, name, *extra):
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD", *extra], cwd=repo, capture_output=True,
                       text=True, check=False)
    assert p.returncode in (0, 1), p.stderr
    return " ".join(next(x for x in p.stdout.splitlines() if x.startswith(name)).split())


# G2-CATTR1-R3: the rows read git's real diff, whatever the repository's git config or a file's bytes say.
# name: (git config, files at <base>, files the range writes, where the word is). Red before: each read OK ("0"),
# or the place shown is wrong, or the checker stopped with a traceback.
REAL_DIFF = {
    "a -diff attribute (B3)": ({}, {".gitattributes": "x.md -diff\n"}, {"x.md": f"Reviewed by {W}\n"}, "x.md:1"),
    "a binary file (B3)": ({}, {}, {"x.bin": f"\0\nReviewed by {W}\n".encode()}, "x.bin:2"),
    "a text conversion (B3)": ({"diff.empty.textconv": "true"}, {".gitattributes": "x.md diff=empty\n"},
                               {"x.md": f"Reviewed by {W}\n"}, "x.md:1"),
    "color.diff=always (B4)": ({"color.diff": "always"}, {}, {"x.md": f"Reviewed by {W}\n"}, "x.md:1"),
    "diff.external (B4)": ({"diff.external": "true"}, {}, {"x.md": f"Reviewed by {W}\n"}, "x.md:1"),
    "a vertical tab in the line (B2)": ({}, {}, {"x.md": f"plain\x0bReviewed by {W}\n"}, "x.md:1"),
    "a carriage return in the line (B2)": ({}, {}, {"x.md": f"plain\rReviewed by {W}\n"}, "x.md:1"),
    "a byte that is not UTF-8 (B2)": ({}, {}, {"x.md": f"\xff Reviewed by {W}\n".encode("latin-1")}, "x.md:1"),
    "a forged header after a vertical tab (B2)": ({}, {}, {"x.md": b'plain\x0bdiff x\x0b+++ "b/\\x"\n'}, None),
    "deleted text after a vertical tab (B2)": ({}, {"x.md": f"old\x0b+Reviewed by {W}\n"}, {"x.md": "clean\n"}, None),
    "two hunks joined by a context line (N1)": ({"diff.interHunkContext": "1"}, {"x.md": "a\nb\nc\n"},
                                                {"x.md": f"A\nb\nReviewed by {W}\n"}, "x.md:3"),
    "a prefixless path that starts with b/ (N2)": ({"diff.noprefix": "true"}, {}, {"b/credits.md": f"Reviewed by {W}\n"},
                                                   "b/credits.md:1"),
}


@pytest.mark.parametrize("name", sorted(REAL_DIFF))
def test_the_attribution_reads_gits_real_diff(repo, name):
    config, before, after, where = REAL_DIFF[name]
    if before:
        _commit(repo, before)
        _git(repo, "tag", "-f", "base")
    for key, value in config.items():
        _git(repo, "config", key, value)
    _commit(repo, after)
    assert _attribution(repo) == ((0, "WARN", f"1 at {where}") if where else (0, "OK", "0"))


@pytest.mark.parametrize("key, value", [("color.diff", "always"), ("diff.external", "true")])
def test_every_row_reads_gits_real_diff(repo, key, value):
    # The same diff feeds the other rows: an added `assert True` in a test stays a HARD pattern.
    _git(repo, "config", key, value)
    _commit(repo, _test("\ndef test_two():\n    assert True\n"))
    assert _run(repo)[1]["patterns"] == "HARD"


def test_a_commit_message_with_a_record_separator_is_read_whole(repo):
    # Red before: the log was split at \x1e, the message's own one cut a commit in two (a traceback).
    _commit(repo, {"docs/a.md": "Alpha fact.\nMore.\n"}, msg=RED + "x\x1e" + "written in one " + "ses" + "sion\n")
    sha = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=repo, capture_output=True, text=True,
                         check=True).stdout.strip()
    assert _attribution(repo) == (0, "WARN", f"1 at commit {sha}")


def test_a_new_test_file_with_a_quoted_name_is_still_layered(repo):
    # Red before: --name-status quoted "tests/misc/test_\303\274.py", which is no test file, so no layer was asked.
    _commit(repo, {"tests/misc/test_\u00fc.py": "def test_x():\n    assert 1\n"})
    assert _run(repo)[1]["tests"] == "HARD"


def test_a_fact_is_found_in_a_quoted_name_without_colour(repo):
    # Red before: git grep quoted the name and color.grep=always wrapped it in escapes.
    _git(repo, "config", "color.grep", "always")
    _commit(repo, {"docs/\u00fcber.md": "Gamma fact.\n"})
    assert _line(repo, "docs", "--fact", "Gamma fact.").endswith('fact "Gamma fact." -> docs/\u00fcber.md OK')


def test_a_rename_is_found_whatever_diff_renames_says(repo):
    # A test file moved unchanged adds no test, in the range and in its commit. Red before: with diff.renames=false
    # the whole file read as added: "1 new tests", and its commit (no red-before wording) "red-before 0/1".
    _git(repo, "config", "diff.renames", "false")
    _git(repo, "mv", "tests/core/test_ok.py", "tests/core/test_ok2.py")
    _commit(repo, {}, msg="item\n")
    assert _line(repo, "tests") == "tests 0 new tests; layers tests/core=core; red-before 0/0 OK"


def test_changelog_lines_are_counted_as_git_counts_them(repo):
    # A bullet under the released section, after a line with vertical tabs: str.splitlines() counted three lines
    # there and put the second heading below the bullet. Red before: OK.
    _commit(repo, {"CHANGELOG.md": "# Changelog\n\n## Unreleased\n- x\x0by\x0bz\n\n## 0.1.0\n- late\n\n- old entry\n"})
    assert _run(repo)[1]["changelog"] == "HARD"


def test_the_release_section_is_found_as_git_counts_lines(repo):
    # Three bullets in the top section, below a title with vertical tabs: the release's section commit, not capped.
    # Red before: the top heading counted two lines lower, the first bullet outside it, the cap applied (HARD).
    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace("# Changelog\n", "# Changelog\x0bi\x0bj\n")})
    _git(repo, "tag", "-f", "base")
    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace("# Changelog\n", "# Changelog\x0bi\x0bj\n").replace(
        "## Unreleased\n", "## Unreleased\n- a\n- b\n- c\n")})
    assert _run(repo)[1]["changelog"] == "OK"


def test_a_clock_after_a_lone_carriage_return_is_found(repo):
    # git ends a line at "\n" only, Python also at a lone "\r": the clock on the Python line after the "\r" is on
    # an added git line. Guards the LF-only reading (with git's lines taken as Python's, it read OK).
    _commit(repo, _test("\ndef test_two():\n    import time\n    assert 2\r    " + TSL + "(0)\n"))
    assert _run(repo)[1]["wallclock"] == "HARD"


def _module(monkeypatch, repo):
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_houserules_mod", CHECKER)
    hr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hr)
    monkeypatch.chdir(repo)
    return hr


def test_an_empty_context_line_does_not_stop_the_deleted_line_count(repo, monkeypatch):
    # Under diff.suppressBlankEmpty git writes an empty context line as "" (no leading space). Red before: line[0]
    # on it raised IndexError.
    hr = _module(monkeypatch, repo)
    assert hr.deleted_lines("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1 @@\n-a\n\n-c\n") == [1, 3]


# The release's section commit under a user's diff.suppressBlankEmpty with context between or around its hunks: the
# fixed invocation (no context, no context between hunks) reads it as with no config. (files at <base>, the commit)
BLANK_CONTEXT = {
    "diff.context": ({}, CHANGELOG.replace("## Unreleased\n", "## Unreleased\n- a\n- b\n- c\n")),
    "diff.interHunkContext": (CHANGELOG.replace("## Unreleased\n", "## Unreleased\n- a\n\n- b\n"),
                              CHANGELOG.replace("## Unreleased\n", "## Unreleased\n- a2\n\n- b2\n")),
}


@pytest.mark.parametrize("key", sorted(BLANK_CONTEXT))
def test_a_release_section_commit_reads_the_same_under_suppress_blank_empty(repo, key):
    before, text = BLANK_CONTEXT[key]
    if before:
        _commit(repo, {"CHANGELOG.md": before})
        _git(repo, "tag", "-f", "base")
    _git(repo, "config", "diff.suppressBlankEmpty", "true")
    _git(repo, "config", key, "3" if key == "diff.context" else "5")
    _commit(repo, {"CHANGELOG.md": text})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (0, "OK")


def test_a_word_on_a_line_starting_with_plus_plus_is_named(repo):
    # Inside a hunk an added "++ " line reads "+++ "; it is the added line, not a header.
    _commit(repo, {"docs/a.md": f"Alpha fact.\n++ Reviewed by {W}\n"})
    assert _attribution(repo) == (0, "WARN", "1 at docs/a.md:2")


def test_a_crlf_line_is_one_python_line(repo):
    # "\r\n" ends one line for git and for Python: the edited line 2 is not the clock's line 4 (counting the "\r"
    # of each "\r\n" once more would put git's line 2 on Python's 3 and 4).
    crlf = "import time\r\nX = {}\r\ndef test_one():\r\n    " + TSL + "(0)\r\n"
    _commit(repo, {"tests/core/test_ok.py": crlf.format(1)})
    _git(repo, "tag", "-f", "base")
    _commit(repo, {"tests/core/test_ok.py": crlf.format(2)})
    assert _run(repo)[1]["wallclock"] == "OK"


def test_a_name_with_a_newline_keeps_the_row_on_one_line(repo):
    # Red before: the decoded newline split the attribution row in two (its location read "docs/a").
    _commit(repo, {"docs/a\nb.md": f"Reviewed by {W}\n"})
    assert _attribution(repo) == (0, "WARN", "1 at docs/a\\x0ab.md:1")


def test_a_hunk_header_is_read_up_to_its_new_start(repo):
    # git's function context after the "@@" can hold "+9": the new start is the first "+" number.
    _commit(repo, {"x.md": "a+9\n-\n-\n"})
    _git(repo, "tag", "-f", "base")
    _commit(repo, {"x.md": f"a+9\n-\n-\nReviewed by {W}\n"})
    assert _attribution(repo) == (0, "WARN", "1 at x.md:4")


def test_a_name_with_a_tab_a_quote_and_a_backslash_is_decoded(repo):
    _commit(repo, {'docs/a\tb"c\\d.md': f"Reviewed by {W}\n"})
    assert _attribution(repo) == (0, "WARN", '1 at docs/a\\x09b"c\\d.md:1')


def test_an_indented_line_after_a_gap_does_not_lengthen_a_bullet(repo):
    # A continuation is contiguous: three indented lines after an unindented one are not the bullet's.
    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace(
        "## Unreleased\n", "## Unreleased\n\n- a\n\nNote\n  x\n  y\n  z\n")})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (0, "OK")


def test_an_indented_test_method_is_not_a_new_test(repo):
    # Only a top-level `def test_` counts; a method needs no red-before wording of its own here.
    _commit(repo, {"tests/core/test_ok.py": BASE["tests/core/test_ok.py"]
                   + "\n\nclass TestX:\n    def test_m(self):\n        assert 1\n"}, msg="item\n")
    rc, rows = _run(repo)
    assert (rc, rows["tests"]) == (0, "OK")


def test_without_a_budget_table_the_modules_line_is_not_applicable(repo):
    _commit(repo, {"tests/repo/test_module_budgets.py": None}, msg="no table\n")
    _git(repo, "tag", "-f", "base")                       # a repository without one (removing it is HARD)
    _commit(repo, {"lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"})
    rc, rows = _run(repo)
    assert (rc, rows["modules"]) == (0, "OK")


def test_a_fact_is_looked_up_at_head_not_in_the_working_copy(repo):
    # The working copy states the fact a second time, uncommitted: <head> does not, so OK.
    _commit(repo, {"docs/a.md": "Alpha fact.\nGamma.\n"})
    (repo / "docs" / "b.md").write_text("Beta.\nGamma.\n")
    rc, rows = _run(repo, "--fact", "Gamma.")
    assert (rc, rows["docs"]) == (0, "OK")


SPLIT_BASE = {"lhpc/core/service_x.py": "class X:\n    def a(self):\n        pass\n    def b(self):\n        pass\n",
              "tests/repo/test_module_budgets.py": BUDGET.format(x="(5, 2)")}
SPLIT_BUDGET = ('BUDGETS = {{\n    "services.py": {facade},\n    "service_x.py": (3, 1),\n'
                '    "service_y.py": (6, 1),  # s1: split from {sources}\n}}\n')
SPLIT = {"lhpc/core/service_x.py": "class X:\n    def a(self):\n        pass\n",
         "lhpc/core/service_y.py": '"""Y."""\nimport os\n\nclass Y:\n    def b(self):\n        pass\n',
         "lhpc/core/services.py": "from .service_x import X\nfrom .service_y import Y\n\nclass S(X, Y):\n"
                                  "    def run(self):\n        pass\n",
         "tests/repo/test_module_budgets.py": SPLIT_BUDGET.format(
             facade="(11, 3),  # s1: the import of the new mixin", sources="service_x.py")}


@pytest.mark.parametrize("case, change, verdict", [
    # The facade's import line is paid by its own budget row, raised with a reason.
    ("split", {}, "WARN"),
    # The header excuse does not cover growth elsewhere: not in the facade without a raise, not in the source,
    # and not a split whose named sources did not all shrink.
    ("split-and-unpaid-facade-growth", {"tests/repo/test_module_budgets.py": SPLIT_BUDGET.format(
        facade="(10, 3)", sources="service_x.py")}, "HARD"),
    ("split-and-growth", {"lhpc/core/service_x.py": SPLIT["lhpc/core/service_x.py"] + "\n"}, "HARD"),
    ("split-from-a-file-that-did-not-shrink", {"tests/repo/test_module_budgets.py": SPLIT_BUDGET.format(
        facade="(11, 3),  # s1: the import of the new mixin", sources="services.py")}, "HARD"),
    ("split-file-without-a-class", {"lhpc/core/service_y.py": '"""Y."""\nimport os\n\n\ndef b():\n    pass\n'}, "HARD"),
    # The header excuse covers only the new file's docstring and imports, not code or comments above its class
    # (each with the file's budget row sized to fit).
    ("split-file-with-code-before-its-class", {
        "lhpc/core/service_y.py": '"""Y."""\nimport os\n\n\ndef _extra():\n    return 1\n\n\n'
                                  'class Y:\n    def b(self):\n        pass\n',
        "tests/repo/test_module_budgets.py": SPLIT["tests/repo/test_module_budgets.py"].replace(
            '"service_y.py": (6, 1)', '"service_y.py": (11, 1)')}, "HARD"),
    ("split-file-with-a-comment-block-before-its-class", {
        "lhpc/core/service_y.py": '"""Y."""\nimport os\n# one\n# two\n# three\n\nclass Y:\n    def b(self):\n'
                                  '        pass\n',
        "tests/repo/test_module_budgets.py": SPLIT["tests/repo/test_module_budgets.py"].replace(
            '"service_y.py": (6, 1)', '"service_y.py": (9, 1)')}, "HARD"),
    # Above the class only the docstring and imports count, as AST nodes (not a statement sharing an import's
    # line), and the class line carries no body.
    ("split-file-with-code-on-an-import-line", {
        "lhpc/core/service_y.py": '"""Y."""\nimport os; _extra = 1\n\nclass Y:\n    def b(self):\n        pass\n'},
     "HARD"),
    ("split-file-with-a-body-on-its-class-line", {
        "lhpc/core/service_y.py": '"""Y."""\nimport os\n\nclass Y: _extra = 1\n'}, "HARD"),
    # The class line is bare, `class Name:`: no bases, no keywords (each row sized to fit).
    ("split-file-with-a-call-in-its-bases", {
        "lhpc/core/service_y.py": '"""Y."""\nimport os\n\nclass Y(type("B", (), {})):\n    def b(self):\n'
                                  '        pass\n'}, "HARD"),
    ("split-file-with-a-keyword-on-its-class-line", {
        "lhpc/core/service_y.py": '"""Y."""\nimport os\n\nclass Y(metaclass=type):\n'
                                  '    def b(self):\n        pass\n'}, "HARD"),
    ("split-from-two-files-where-the-second-grew", {"tests/repo/test_module_budgets.py": SPLIT_BUDGET.format(
        facade="(11, 3),  # s1: the import of the new mixin", sources="service_x.py and services.py")}, "HARD"),
])
def test_a_split_grows_the_family_only_by_the_new_header(repo, case, change, verdict):
    _commit(repo, SPLIT_BASE)
    _git(repo, "tag", "-f", "base")
    _commit(repo, {**SPLIT, **change})
    rc, rows = _run(repo)
    assert (rc, rows["modules"]) == ((0, "WARN") if verdict == "WARN" else (1, "HARD"))


# ---- a profile per repository (`.houserules.toml`, read at <base>) ----------------------------------
def _profiled(repo, text):
    """The profile committed and `base` moved onto it: the rules a later range is checked by."""
    _commit(repo, {".houserules.toml": text}, msg="profile\n")
    _git(repo, "tag", "-f", "base")


def test_a_profile_layers_a_flat_tests_directory(repo):
    """A repository whose tests sit directly in tests/ names that layer; red before: unlayered, HARD."""
    _profiled(repo, 'layers = [["tests/", "unit"]]\n')
    _commit(repo, {"tests/test_flat.py": "def test_flat():\n    assert 1\n"})
    rc, rows = _run(repo)
    assert rc == 0 and rows["tests"] == "OK"


def test_a_profile_counts_its_own_test_files_and_definitions(repo):
    """C++ tests are counted by the profile's globs and definition pattern: a commit adding one without
    "red-before" in its message is a WARN; red before: not a test file, OK."""
    _profiled(repo, 'layers = [["src/tests/", "unit"]]\ntest_files = ["test_*.cpp"]\ntest_def = "static void test_"\n')
    _commit(repo, {"src/tests/test_a.cpp": "static void test_a(void)\n{\n}\n"}, msg="item\n")
    rc, rows = _run(repo)
    assert rc == 0 and rows["tests"] == "WARN"


def test_a_profile_without_a_changelog_or_budget_turns_those_rules_off(repo):
    """Three bullets are HARD under the default; a profile with no changelog and no budget file reads
    neither. Red before: changelog HARD."""
    _profiled(repo, 'changelog = ""\nbudget_file = ""\n')
    _commit(repo, {"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- a\n- b\n- c\n")})
    rc, rows = _run(repo)
    assert rc == 0 and rows["changelog"] == "OK" and rows["modules"] == "OK"


def test_a_profile_replaces_the_warn_patterns(repo):
    """The profile's own warn pattern applies in the test layers; red before: the default set, OK."""
    _profiled(repo, 'warn_patterns = { shell-sleep = "sleep [0-9]" }\n')
    _commit(repo, _test(f"\n@pytest.mark.wallclock('data only')\ndef test_two():\n    run('{SL} 5')\n"))
    rc, rows = _run(repo)
    assert rc == 0 and rows["patterns"] == "WARN"


@pytest.mark.parametrize("budget", [None, "BUDGETS = {\n", "BUDGETS = f()\n", "BUDGETS = {[]: 1}\n", "BUDGETS = []\n",
                                    'BUDGETS = {"services.py": 10}\n'])
def test_a_budget_table_cannot_vanish_in_the_range_it_gates(repo, budget):
    """A range that deletes the budget table <base> had, breaks it, or makes it a non-literal or another shape,
    and grows a family module, is HARD on modules; red before: "N/A (budget table absent)", OK, or a traceback."""
    _commit(repo, {"tests/repo/test_module_budgets.py": budget,
                   "lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n    def b(self):\n        pass\n"})
    rc, rows = _run(repo)
    assert rc == 1 and rows["modules"] == "HARD"


def test_a_range_cannot_relax_its_own_rules(repo):
    """A range that turns the changelog rule off in the same commit as three bullets is checked by
    <base>'s rules, and the profile change itself is HARD; red before: a clean PASS."""
    _commit(repo, {".houserules.toml": 'changelog = ""\n',
                   "CHANGELOG.md": CHANGELOG.replace("## Unreleased\n", "## Unreleased\n\n- a\n- b\n- c\n")})
    rc, rows = _run(repo)
    assert rc == 1 and rows["profile"] == "HARD" and rows["changelog"] == "HARD"


def test_a_range_adding_a_malformed_profile_exits_2(repo):
    """<head>'s profile is checked too, so a profile-only range cannot hand the next range a broken one;
    red before: WARN, 0."""
    _commit(repo, {".houserules.toml": 'test_files = "x"\n'}, msg="profile\n")
    rc, _rows = _run(repo)
    assert rc == 2


def test_a_profile_only_range_is_a_warning(repo):
    """A range that changes only the profile is reviewed on its own (WARN), so a repository can add one."""
    _commit(repo, {".houserules.toml": 'layers = [["tests/", "unit"]]\n'}, msg="profile\n")
    rc, rows = _run(repo)
    assert rc == 0 and rows["profile"] == "WARN"


_PROFILE = ('layers = [["tests/core/", "core"], ["tests/repo/", "repo"]]\ntest_files = ["test_*.py"]\n'
            'warn_patterns = { sleep = "time\\\\.sleep\\\\(" }\n')


@pytest.mark.parametrize("change, verdict", [
    (('["tests/repo/", "repo"]]', '["tests/repo/", "repo"], ["tests/", "misc"]]'), "HARD"),   # a layer added
    (('test_files = ["test_*.py"]', 'test_files = []'), "HARD"),                             # a glob dropped
    (('{ sleep = "time\\\\.sleep\\\\(" }', "{}"), "HARD"),                                  # a pattern dropped
    (("", 'changelog = ""\n'), "HARD"),                                                     # a rule turned off
    (('["tests/core/", "core"], ', ""), "WARN"),                                             # a layer removed
    (('test_files = ["test_*.py"]', 'test_files = ["test_*.py", "*_test.py"]'), "WARN"),     # a glob added
])
def test_a_profile_only_range_that_relaxes_an_existing_profile_is_hard(repo, change, verdict):
    """An existing profile is the base later ranges are judged by: relaxing it alone (a layer or rule more
    lenient, a glob or a pattern dropped, a rule turned off) is HARD; tightening it alone stays the
    reviewer's WARN. Red before: every profile-only range was WARN."""
    _profiled(repo, _PROFILE)
    _commit(repo, {".houserules.toml": _PROFILE.replace(*change) if change[0] else _PROFILE + change[1]},
            msg="profile\n")
    rc, rows = _run(repo)
    assert (rc, rows["profile"]) == (int(verdict == "HARD"), verdict)


def test_deleting_an_existing_profile_is_hard(repo):
    _profiled(repo, _PROFILE)
    _commit(repo, {".houserules.toml": None}, msg="profile\n")
    rc, rows = _run(repo)
    assert (rc, rows["profile"]) == (1, "HARD")


@pytest.mark.parametrize("profile", ["layers = [", 'unknown = "x"\n', 'test_def = "("\n', "changelog = 3\n",
                                     'test_files = "x"\n', 'layers = [["tests/"]]\n', "warn_patterns = { a = 1 }\n",
                                     'layers = "tests/"\n'])
def test_a_malformed_profile_exits_2(repo, profile):
    """A profile that cannot be read is a usage error, never a silent default; red before: ignored, 0."""
    _profiled(repo, profile)
    _commit(repo, _test("\ndef test_two():\n    assert 1\n"))
    rc, _rows = _run(repo)
    assert rc == 2


@pytest.mark.parametrize("env", [{}, {"HOUSERULES_BASE_RUN": "1"}])
def test_a_range_cannot_weaken_the_checker_that_judges_it(repo, env):
    """<base> holds the checker; the range removes its assert-True rule and commits that violation. The
    program the range carries is anchored to <base>'s, which still finds it: rc 1, also with the former
    recursion stop exported in the caller's environment. Red before: the weakened program alone, rc 0."""
    real = CHECKER.read_text()
    weak = real.replace('"assert-True": re.compile(r"^\\s*assert True\\b")', "")
    assert weak != real
    _commit(repo, {"tools/check_houserules.py": real}, msg="checker\n")
    _git(repo, "tag", "-f", "base")
    _commit(repo, {"tools/check_houserules.py": weak, **_test("\ndef test_two():\n    assert True\n")})
    p = subprocess.run([sys.executable, str(repo / "tools" / "check_houserules.py"), "base..HEAD"], cwd=repo,
                       capture_output=True, text=True, check=False, env={**os.environ, **env})
    base_part = p.stdout.split("THIS PROGRAM", 1)[0]
    assert p.returncode == 1 and "BASE PROGRAM" in base_part
    rows = {line.split()[0]: line.split()[-1] for line in base_part.splitlines()[1:] if line.strip()}
    assert rows["patterns"] == "HARD"


def test_the_release_bot_is_an_allowed_author(repo):
    # L50 / DEC-W46: the maintainer's release bot bumps versions; its commits are expected.
    _commit(repo, {"docs/a.md": "Alpha fact.\nMore.\n"}, author="lhpc-release-bot")
    rc, rows = _run(repo, "--fact", "More.")
    assert (rc, rows["attribution"]) == (0, "OK")


def test_an_excepted_commit_is_not_judged_by_its_author(repo, monkeypatch, capsys):
    # L50 / DEC-W46: one commit is excepted by its full sha (ALLOWED_COMMITS); its author is then not judged.
    import importlib.util
    _commit(repo, {"docs/a.md": "Alpha fact.\nMore.\n"}, author="someone")
    spec = importlib.util.spec_from_file_location("check_houserules_l50", CHECKER)
    hr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hr)
    monkeypatch.chdir(repo)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setattr(hr, "ALLOWED_COMMITS", {sha}, raising=False)   # red-before: no such set yet
    hr.main(["base..HEAD", "--fact", "More."])
    rows = {line.split()[0]: line.split()[-1] for line in capsys.readouterr().out.splitlines()[1:] if line.strip()}
    assert rows["attribution"] == "OK"


# --- the wall-clock rule (HARD): a real clock in tests/ or testlab/tests/ needs @pytest.mark.wallclock("reason") ---
# or, outside any test, `# wallclock: reason` on its line. Red-before: on the parent the row did not exist and
# time.sleep was a WARN pattern, so every unmarked case below passed the range.

def _out(repo):
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo, capture_output=True, text=True,
                       check=False)
    return p.returncode, next((ln for ln in p.stdout.splitlines() if ln.startswith("wallclock")), "")


UNMARKED = {
    "time.sleep": "    import time\n    " + TSL + "(0)\n",
    "time.time": "    import time\n    assert time.time()\n",
    "time.monotonic": "    import time\n    assert time.monotonic()\n",
    "module alias": "    import time as _t\n    assert _t.monotonic()\n",
    "function alias": "    from time import sleep as nap\n    nap(0)\n",
    "argv": f"    import subprocess\n    subprocess.run(['{SL}', '5'])\n",
    "shell text": f"    run('sh -c \"{SL} 5\"')\n",
    "fake-script body": f"    body = '''#!/bin/sh\n{SL} 30\n'''\n",
}


@pytest.mark.parametrize("case", sorted(UNMARKED))
def test_wallclock_each_real_clock_in_a_test_is_hard(repo, case):
    _commit(repo, _test("\ndef test_two():\n" + UNMARKED[case]))
    rc, line = _out(repo)
    assert rc == 1 and line.split()[-1] == "HARD" and "tests/core/test_ok.py:" in line, line


@pytest.mark.parametrize("case", sorted(UNMARKED))
def test_wallclock_a_marked_test_passes_and_is_listed(repo, case):
    _commit(repo, _test("\n@pytest.mark.wallclock('real waiting')\ndef test_two():\n" + UNMARKED[case]))
    rc, line = _out(repo)
    assert rc == 0 and line.split()[-1] == "OK", line
    assert "1 escaped: tests/core/test_ok.py::test_two (real waiting)" in line


@pytest.mark.parametrize("mark", ["@pytest.mark.wallclock('')", "@pytest.mark.wallclock(' ')",
                                  "@pytest.mark.wallclock", "@pytest.mark.slow"])
def test_wallclock_a_marker_without_a_reason_is_hard(repo, mark):
    _commit(repo, _test(f"\n{mark}\ndef test_two():\n" + UNMARKED["time.sleep"]))
    rc, line = _out(repo)
    assert rc == 1 and line.split()[-1] == "HARD", line


def test_wallclock_a_clock_added_to_an_existing_marked_test_passes(repo):
    # G1-TR1 B2: neither the def line nor the decorator is in the diff; the HEAD file decides
    _commit(repo, _test("\n@pytest.mark.wallclock('real waiting')\ndef test_two():\n    import time\n    assert 2\n"))
    _git(repo, "tag", "-f", "base")
    _commit(repo, {"tests/core/test_ok.py": (repo / "tests/core/test_ok.py").read_text().replace(
        "    assert 2\n", "    " + TSL + "(0)\n    assert 2\n")})
    rc, line = _out(repo)
    assert rc == 0 and "1 escaped: tests/core/test_ok.py::test_two (real waiting)" in line, line


@pytest.mark.parametrize("mark, verdict", [("", "HARD"), ("    @pytest.mark.wallclock('real waiting')\n", "OK")])
def test_wallclock_a_class_method_is_attributed_to_its_test(repo, mark, verdict):
    _commit(repo, _test(f"\nclass TestTwo:\n{mark}    def test_two(self):\n        import time\n"
                        "        " + TSL + "(0)\n"))
    _rc, line = _out(repo)
    assert line.split()[-1] == verdict, line
    assert ("TestTwo::test_two (real waiting)" in line) == (verdict == "OK")


@pytest.mark.parametrize("comment, verdict", [("", "HARD"), ("  # wallclock: the fake daemon's hang", "OK")])
def test_wallclock_a_line_outside_a_test_needs_its_comment(repo, comment, verdict):
    _commit(repo, {"tests/core/test_fake.py": f"import time\n\nFAKE = '''#!/bin/sh\nhang) {SL} 30 ;;{comment}\n'''\n\n"
                   "def helper():\n    return time.monotonic()" + comment + "\n\n\ndef test_x():\n    assert FAKE\n"})
    _rc, line = _out(repo)
    assert line.split()[-1] == verdict, line
    if verdict == "OK":
        assert "tests/core/test_fake.py:4 (the fake daemon's hang)" in line and "test_fake.py:8 (" in line


def test_wallclock_text_that_names_a_call_is_not_a_call(repo):
    # G1-TR1 NOTE: a string or comment that names the sleep call sleeps nowhere
    _commit(repo, _test("\ndef test_two():\n    example = '" + TSL + "(5)'  # time.monotonic()\n    assert example\n"))
    rc, line = _out(repo)
    assert rc == 0 and line.split()[-1] == "OK", line


def test_wallclock_a_multiline_argv_is_judged_whole(repo):
    # G1-TR1-RE (2): only the argument lines change; the list starts on an unchanged line
    _commit(repo, _test("\ndef test_two():\n    import subprocess\n    subprocess.run([\n        'echo',\n"
                        "        'hello',\n    ])\n"))
    _git(repo, "tag", "-f", "base")
    body = (repo / "tests/core/test_ok.py").read_text()
    _commit(repo, {"tests/core/test_ok.py": body.replace("'echo'", f"'{SL}'").replace("'hello'", "'5'")})
    rc, line = _out(repo)
    assert rc == 1 and line.split()[-1] == "HARD", line


def test_wallclock_a_removed_clock_is_not_counted(repo):
    _commit(repo, _test("\ndef test_two():\n    import time\n    " + TSL + "(0)\n"))
    _git(repo, "tag", "-f", "base")
    _commit(repo, {"tests/core/test_ok.py": BASE["tests/core/test_ok.py"]})
    rc, line = _out(repo)
    assert rc == 0 and line.split()[-1] == "OK", line


def test_wallclock_a_test_file_that_does_not_parse_is_hard(repo):
    _commit(repo, _test("\ndef test_two(:\n"))
    rc, line = _out(repo)
    assert rc == 1 and "tests/core/test_ok.py does not parse" in line, line


@pytest.mark.parametrize("path, verdict", [("testlab/tests/unit/test_x.py", "HARD"),
                                           ("lhpc/data/rfdecode/tests/test_x.py", "OK")])
def test_wallclock_reaches_tests_and_testlab_tests_only(repo, path, verdict):
    _commit(repo, {path: "import time\n\n\ndef test_x():\n    " + TSL + "(0)\n"})
    _rc, line = _out(repo)
    assert line.split()[-1] == verdict, line


# --- placement: a new or renamed test file sits in a layer folder (tests/README.md "Where a new test goes") ------

@pytest.mark.parametrize("dest, verdict", [("tests/test_ok.py", "HARD"), ("tests/repo/test_ok.py", "OK")])
def test_a_renamed_test_file_must_stay_in_a_layer_folder(repo, dest, verdict):
    # red-before: on the parent a rename was never a new file, so the move out of the layers passed (OK)
    (repo / dest).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", "tests/core/test_ok.py", dest)
    _git(repo, "commit", "-q", "-m", RED)
    rc, rows = _run(repo)
    assert rows["tests"] == verdict and (rc == 1) == (verdict == "HARD")


def test_wallclock_marker_text_in_a_parameter_is_no_marker(repo):
    # G1-TR1-C B2: only a real wallclock decorator escapes; the same text as parametrize data does not
    _commit(repo, _test("\n@pytest.mark.parametrize('n', [\"mark.wallclock('not an exemption')\"])\ndef test_two(n):\n"
                        "    import time\n    " + TSL + "(0)\n"))
    rc, line = _out(repo)
    assert rc == 1 and line.split()[-1] == "HARD", line


def test_slow_marker_text_in_a_parameter_is_no_marker(repo):
    # G1-TR1-C B2 (the slow row): a timed loop with "mark.slow" only as parametrize data is still without slow
    _commit(repo, _test("\n@pytest.mark.wallclock('a timed loop')\n@pytest.mark.parametrize('n', ['mark.slow'])\n"
                        "def test_two(n):\n    import time\n    " + "wh" + "ile time.monotonic() < 0:\n        pass\n"))
    _rc, rows = _run(repo)
    assert rows["markers"] == "WARN"


def test_wallclock_a_marked_test_covers_its_decorators(repo):
    # G1-TR1-C B3: a sleep text in a decorator of a marked test is that test's
    _commit(repo, _test(f"\n@pytest.mark.wallclock('data only')\n@pytest.mark.parametrize('command', ['{SL} 5'])\n"
                        "def test_two(command):\n    assert command\n"))
    rc, line = _out(repo)
    assert rc == 0 and "test_ok.py::test_two (data only)" in line, line


def test_a_sleep_outside_the_wallclock_scope_stays_a_warning(repo):
    # G1-TR1-C NOTE: the in-package tests keep the textual sleep WARN the wallclock rule does not reach
    _commit(repo, {"lhpc/data/rfdecode/tests/test_x.py": "import time\n\n\ndef test_x():\n    " + TSL + "(0)\n"})
    _rc, rows = _run(repo)
    assert rows["patterns"] == "WARN" and rows["wallclock"] == "OK"


@pytest.mark.parametrize("deco", ["@Helper.mark.wallclock('not an exemption')", "@helpers.pytest.mark.wallclock('x')"])
def test_wallclock_only_pytest_mark_or_mark_is_a_marker(repo, deco):
    # G1-TR1-C-RE (2): another object's `.mark` attribute is no pytest mark
    _commit(repo, _test(f"\n{deco}\ndef test_two():\n    import time\n    " + TSL + "(0)\n"))
    rc, line = _out(repo)
    assert rc == 1 and line.split()[-1] == "HARD", line


def test_wallclock_the_bare_mark_form_is_a_marker(repo):
    # `from pytest import mark`: @mark.wallclock("reason") is the same mark
    _commit(repo, _test("\nfrom pytest import mark\n\n\n@mark.wallclock('real waiting')\ndef test_two():\n    import time\n    "
                        + TSL + "(0)\n"))
    rc, line = _out(repo)
    assert rc == 0 and "test_ok.py::test_two (real waiting)" in line, line


# ---- CO-26: a budget row may name a file that is not Python (lines only) ---------------------------
VOICE = "int main(void)\n{\n    return 0;\n}\n"


def _c_budgeted(repo, lines=6):
    """A C repository's profile: its family is src/, its budget table names voice.c (lines, public)."""
    _commit(repo, {"src/voice.c": VOICE,
                   "tools/budgets.py": f'BUDGETS = {{\n    "voice.c": ({lines}, 0),\n}}\n'}, msg="c base\n")
    _profiled(repo, 'family_dir = "src/"\nbudget_file = "tools/budgets.py"\n')


def _modules_line(repo):
    """The checker's rc and its modules row; a traceback fails the test even when the row was printed first."""
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo,
                       capture_output=True, text=True, check=False)
    assert "Traceback" not in p.stderr, p.stderr
    return p.returncode, next((ln for ln in p.stdout.splitlines() if ln.startswith("modules")), p.stderr)


def test_a_c_file_in_its_budget_is_measured_by_lines(repo):
    """CO-26; red before at 0af1d413: `ast.parse` of voice.c raised SyntaxError, the checker crashed (rc 1, a
    traceback, no modules line)."""
    _c_budgeted(repo)
    _commit(repo, {"src/voice.c": VOICE.replace("return 0;", "return 1;")})
    rc, line = _modules_line(repo)
    assert rc == 0 and line.split()[0] == "modules" and line.split()[-1] == "OK", line
    assert "voice.c 4->4 lines (budget 6), public N/A (not Python)" in line


# Python ends a line at "\r\n", "\r" or "\n", never at a form feed or a vertical tab; str.splitlines() does, and moved
# every later line of the source by one. name: (files the range writes, row, verdict). Red before: each read the line
# above or below (a comment escaped a clock, a sleep or a slow test was missed, a raise had no reason, a valid split
# header was invalid and over its budget).
PY_LINES = {
    "a comment above does not escape a clock": (
        {"tests/core/test_x.py": "import time\nX = 1\f# wallclock: not this line\n" + TSL + "(0)\n"}, "wallclock", "HARD"),
    "a sleep text is found": (_test('\ndef test_two():\n    x = 1\f# c\n    run("' + SL + ' 5")\n'), "wallclock", "HARD"),
    "slow work on a test's last line is found": (
        _test('\ndef test_two():\n    x = 1\f# c\n    run("pip ' + 'install x")\n'), "markers", "WARN"),
    "a raise reason after a form feed is read": (
        {"tests/repo/test_module_budgets.py": BUDGET.format(x="(4, 1),\f# item-9: one line"),
         "lhpc/core/service_x.py": BASE["lhpc/core/service_x.py"] + "\n"}, "modules", "WARN"),
}


@pytest.mark.parametrize("name", sorted(PY_LINES))
def test_python_source_lines_are_counted_as_python_counts_them(repo, name):
    files, row, verdict = PY_LINES[name]
    _commit(repo, files)
    rc, rows = _run(repo)
    assert (rc, rows[row]) == (int(verdict == "HARD"), verdict)


def test_a_split_header_with_a_form_feed_in_its_docstring_is_valid(repo):
    # The new file is 6 Python lines, at its budget; its header is the docstring and an import.
    _commit(repo, SPLIT_BASE)
    _git(repo, "tag", "-f", "base")
    _commit(repo, {**SPLIT, "lhpc/core/service_y.py": SPLIT["lhpc/core/service_y.py"].replace('"""Y."""', '"""Y.\fmore"""')})
    rc, rows = _run(repo)
    assert (rc, rows["modules"]) == (0, "WARN")


def test_a_c_file_is_measured_in_lines_git_counts(repo):
    # A vertical tab is not a line end: still 4 lines.
    _c_budgeted(repo)
    _commit(repo, {"src/voice.c": VOICE.replace("return 0;", "return 1;\v")})
    rc, line = _modules_line(repo)
    assert "voice.c 4->4 lines (budget 6)" in line, line


def test_a_c_file_over_its_line_budget_is_hard(repo):
    # The budget row is raised with a reason in the same range, so the growth itself is excused: only the line
    # budget (7) makes this HARD; within it the same range would be a WARN.
    _c_budgeted(repo)
    _commit(repo, {"src/voice.c": VOICE + "/* a */\n/* b */\n/* c */\n/* d */\n",
                   "tools/budgets.py": 'BUDGETS = {\n    "voice.c": (7, 0),  # co26: the test needs room\n}\n'})
    rc, line = _modules_line(repo)
    assert rc == 1 and line.split()[0] == "modules" and line.split()[-1] == "HARD", line
    assert "voice.c 4->8 lines (budget 7)" in line


def test_a_c_file_growing_within_its_budget_counts_in_the_family_net(repo):
    # The same rule as for a .py family file ("family-net-growth" above): growth with no raised row is unexcused,
    # HARD, though within the line budget.
    _c_budgeted(repo)
    _commit(repo, {"src/voice.c": VOICE + "/* a */\n"})
    rc, line = _modules_line(repo)
    assert rc == 1 and line.split()[0] == "modules" and line.split()[-1] == "HARD", line
    assert "voice.c 4->5 lines (budget 6)" in line and "family net +1 lines" in line


def test_a_split_into_a_c_file_is_an_invalid_split(repo):
    """CO-26; red before at 535286cc: the checker parsed the split's C target as Python and crashed (a traceback,
    no modules line). A file that is not Python has no header to read, so a split into it is never valid."""
    old = '"""Old."""\n\n\nclass Old:\n    def a(self):\n        pass\n\n    def b(self):\n        pass\n'
    _commit(repo, {"src/old.py": old, "tools/budgets.py": 'BUDGETS = {\n    "old.py": (12, 2),\n}\n'},
            msg="c base\n")
    _profiled(repo, 'family_dir = "src/"\nbudget_file = "tools/budgets.py"\n')
    _commit(repo, {"src/old.py": old[:old.index("\n    def b")] + "\n", "src/voice_part.c": VOICE,
                   "tools/budgets.py": 'BUDGETS = {\n    "old.py": (12, 2),\n'
                                       '    "voice_part.c": (4, 0),  # co26: split from old.py\n}\n'})
    rc, line = _modules_line(repo)
    assert rc == 1 and line.split()[0] == "modules" and line.split()[-1] == "HARD", line
    assert "invalid split" in line and line.split()[-2] == "voice_part.c", line


def test_a_budgeted_file_that_git_cannot_read_stops_the_check(repo, tmp_path):
    """A read error is not an absent file: before, `show()` gave None for any failure, so the file measured 0 lines."""
    _c_budgeted(repo)
    _commit(repo, {"src/voice.c": VOICE.replace("return 0;", "return 1;")})
    fake = tmp_path / "fakegit"
    fake.mkdir()
    real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True, check=True).stdout.strip()
    (fake / "git").write_text(f'#!/bin/sh\ncase "$1 $2" in "show base:src/voice.c") echo "fatal: unable to read 0123" >&2; '
                              f'exit 128;; esac\nexec {real} "$@"\n')
    (fake / "git").chmod(0o755)
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo, capture_output=True, text=True,
                       check=False, env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"})
    assert p.returncode == 2, (p.stdout, p.stderr)
    assert "git failed: git show base:src/voice.c: fatal: unable to read 0123" in p.stderr
    assert "VERDICT" not in p.stdout


@pytest.mark.parametrize("lang", ["C", "de_DE.UTF-8"])
def test_a_range_without_a_profile_reads_the_same_in_any_locale(repo, lang):
    """git's "path does not exist" message is localised (de: "Pfad … existiert nicht in"); absence is decided by
    `git ls-tree`'s exit code and output, so a range without a profile reads the same in every locale. Red before at
    741199ce under de_DE.UTF-8: rc 2, "git failed: … Schwerwiegend: Pfad '.houserules.toml' existiert nicht in …"."""
    if lang != "C" and lang.replace("UTF-8", "utf8") not in subprocess.run(["locale", "-a"], capture_output=True,
                                                                          text=True, check=False).stdout:
        pytest.skip(f"the {lang} locale is not installed")
    _commit(repo, {"lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"})
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo, capture_output=True, text=True,
                       check=False, env={**os.environ, "LC_ALL": lang, "LANG": lang})
    assert p.returncode in (0, 1) and "VERDICT" in p.stdout, (p.returncode, p.stderr)
    assert "git failed" not in p.stderr


# ---- T0: an exemption is judged per pattern hit (tests/README.md "The rules") ------------------------------------
# Assembled, so that this file's own lines do not match the patterns they test.
STW = ".starts" + "with("
NET = "reque" + "sts."
SRC = 'open("lh' + 'pc/core/x.py").read()'
LEGACY = "# rule-1 " + "exception"


def _patterns(repo, body):
    """The checker's rc and patterns row for test_two's body; its first line is tests/core/test_ok.py:5."""
    _commit(repo, _test("\ndef test_two():\n" + body + "\n"))
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo, capture_output=True, text=True,
                       check=False)
    assert "Traceback" not in p.stderr, p.stderr
    return p.returncode, next((ln for ln in p.stdout.splitlines() if ln.startswith("patterns")), p.stderr)


def test_a_tag_of_the_patterns_rule_with_a_reason_exempts_its_hit(repo):
    """Red before at the parent: no `# rule-<n>:` tag was read, the hit counted (startswith 1, WARN)."""
    rc, line = _patterns(repo, f'    assert "a b"{STW}"a")  # rule-10: a log sentence, not a decision')
    assert rc == 0 and line.split()[-1] == "OK" and "startswith 0" in line, line
    assert "1 exempt: tests/core/test_ok.py:5 rule-10 (a log sentence, not a decision)" in line, line


@pytest.mark.parametrize("tag", ["# rule-4: lab-local server", "# rule-99: no such rule", "# rule-10:",
                                 "# rule-10:   ", LEGACY],
                         ids=["wrong-rule", "unknown-rule", "empty-reason", "blank-reason", "legacy-tag"])
def test_a_tag_that_is_not_the_hits_rule_with_a_reason_leaves_the_hit(repo, tag):
    """The four `rule-` cases are controls at the parent (no such tag was read there); their mutants are a tag of any
    rule or an empty reason accepted. The legacy tag is red before: it skipped the whole line."""
    rc, line = _patterns(repo, f'    assert "a b"{STW}"a")  {tag}')
    assert rc == 0 and line.split()[-1] == "WARN" and "startswith 1" in line, line
    assert "exempt" not in line, line


def test_one_tag_exempts_only_its_own_pattern_on_a_line_with_two(repo):
    """Red before at the parent: both hits counted (network 1)."""
    rc, line = _patterns(repo, f'    assert {NET}get(URL).text{STW}"ok")  # rule-4: lab-local server')
    assert rc == 0 and line.split()[-1] == "WARN" and "network 0" in line and "startswith 1" in line, line
    assert "1 exempt: tests/core/test_ok.py:5 rule-4 (lab-local server)" in line, line


def test_the_legacy_tag_does_not_exempt_assert_true(repo):
    """Red before at the parent: the legacy tag skipped the line before the HARD patterns (OK)."""
    rc, line = _patterns(repo, "    assert " + "True  " + LEGACY)
    assert rc == 1 and line.split()[-1] == "HARD" and "assert-True 1" in line, line


@pytest.mark.parametrize("body, pattern", [
    ("    import inspect\n    assert inspect." + "getsource(len)  " + LEGACY, "getsource"),
    ("    pass\n    assert " + SRC + "  " + LEGACY + ": a docs symbol exists", "source-read"),
], ids=["getsource", "source-read"])
def test_the_legacy_tag_exempts_getsource_and_source_read_and_is_listed(repo, body, pattern):
    """Red before at the parent: the hit was skipped but never listed."""
    rc, line = _patterns(repo, body)
    reason = "a docs symbol exists" if pattern == "source-read" else "legacy tag"
    assert rc == 0 and line.split()[-1] == "OK" and f"{pattern} 0" in line, line
    assert f"1 exempt: tests/core/test_ok.py:6 rule-1 ({reason})" in line, line


def test_the_row_lists_every_exemption_as_place_rule_and_reason(repo):
    """The listed form, whole. Red before at the parent: the row had no list."""
    rc, line = _patterns(repo, f'    assert "a b"{STW}"a")  # rule-10: a log sentence\n'
                               f'    assert {NET}get(URL)  # rule-4: lab-local server')
    assert rc == 0, line
    assert line == (f"patterns     getsource 0 assert-True 0 source-read 0 {SL} 0 startswith 0 network 0; 2 exempt: "
                    "tests/core/test_ok.py:5 rule-10 (a log sentence); tests/core/test_ok.py:6 rule-4 (lab-local server)"
                    "   OK")


def test_a_failing_git_call_in_the_fixture_names_gits_reason(repo):
    """F9: a fixture git call that fails raises with git's stderr. Red before at the parent: CalledProcessError named
    only the exit status (128), so the reason of a CI-only failure was lost."""
    reason = subprocess.run(["git", "show", "no-such-rev"], cwd=repo, capture_output=True, text=True,
                            check=False).stderr.strip()
    assert reason, "git printed no reason"
    with pytest.raises(Exception) as exc:
        _git(repo, "show", "no-such-rev")
    assert reason in str(exc.value), str(exc.value)


# G1-TCT0 B1: a tag is read from the line's real comment, never from a string's text.
@pytest.mark.parametrize("body, pattern", [
    (f'    assert v{STW}"# rule-10: example")', "startswith"),
    (f'    r = {NET}get("# rule-4: local")', "network"),
], ids=["startswith", "network"])
def test_a_tag_inside_a_string_exempts_nothing(repo, body, pattern):
    """Red before at the parent 41524ef3: the tag in the string exempted the hit (0 and listed)."""
    rc, line = _patterns(repo, body)
    assert rc == 0 and line.split()[-1] == "WARN" and f"{pattern} 1" in line, line
    assert "exempt" not in line, line


def test_a_file_that_does_not_tokenize_exempts_nothing(repo):
    """Fails closed: without its comments read, no tag applies. Red before at the parent: the tag exempted the hit."""
    rc, line = _patterns(repo, f'    assert "a b"{STW}"a")  # rule-10: a log sentence\n    x = """unterminated')
    assert line.split()[-1] == "WARN" and "startswith 1" in line, line
    assert "exempt" not in line, line


def test_an_oversized_rule_number_is_a_hit(repo):
    """G1-TCT0 NOTE 1: a rule number past int()'s digit limit counts the hit. Red before at the parent: int() raised
    ValueError (a traceback, no patterns row)."""
    rc, line = _patterns(repo, f'    assert "a b"{STW}"a")  # rule-' + "1" * 5000 + ": a reason")
    assert rc == 0 and line.split()[-1] == "WARN" and "startswith 1" in line, line
    assert "exempt" not in line, line


# Second read MINOR 1: the fixed map, whole. Each pattern with a new-form tag of each rule; only its own rule exempts.
HIT_LINES = {"assert-True": "    assert " + "True",
             "getsource": "    inspect = __import__('inspect'); assert inspect." + "getsource(len)",
             "source-read": "    assert " + SRC,
             "startswith": f'    assert "a b"{STW}"a")',
             "network": f"    assert {NET}get(URL)"}
OWN_RULE = {"source-read": 1, "network": 4, "startswith": 10}


@pytest.mark.parametrize("pattern", sorted(HIT_LINES))
@pytest.mark.parametrize("rule", [1, 4, 10])
def test_the_rule_map_exempts_each_pattern_by_its_own_rule_only(repo, pattern, rule):
    """A pin of the map (source-read 1, network 4, startswith 10; assert True and getsource none): passes at the
    parent 7a7fa719 too; its mutants are a pattern added to the map or moved to another rule."""
    rc, line = _patterns(repo, f"{HIT_LINES[pattern]}  # rule-{rule}: pin")
    if OWN_RULE.get(pattern) == rule:
        assert rc == 0 and line.split()[-1] == "OK" and f" {pattern} 0" in line, line
        assert f"1 exempt: tests/core/test_ok.py:5 rule-{rule} (pin)" in line, line
    else:
        hard = pattern in ("assert-True", "getsource")
        assert (rc, line.split()[-1]) == ((1, "HARD") if hard else (0, "WARN")) and f" {pattern} 1" in line, line
        assert "exempt" not in line, line


# Delta second read MINOR 1: the legacy tag is read from the comment too, never from a string's text.
@pytest.mark.parametrize("pattern", ["getsource", "source-read"])
def test_the_legacy_tag_inside_a_string_exempts_nothing(repo, pattern):
    """A pin: passes at the parent 40892d57 as well; its mutant reads the legacy tag from the raw line."""
    rc, line = _patterns(repo, f'{HIT_LINES[pattern]} != "{LEGACY}"')
    hard = pattern == "getsource"
    assert (rc, line.split()[-1]) == ((1, "HARD") if hard else (0, "WARN")) and f" {pattern} 1" in line, line
    assert "exempt" not in line, line
