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


def _git(repo, *args, author="makrohard"):
    # An exported GIT_AUTHOR_*/GIT_COMMITTER_* overrides `-c user.name`: the fixture's identity is the one given.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    subprocess.run(["git", "-c", f"user.name={author}", "-c", "user.email=dev@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=repo, env=env, check=True, capture_output=True)


def _write(repo, files):
    for path, text in files.items():
        p = repo / path
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


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


LINES = ("changelog", "docs", "tests", "patterns", "markers", "attribution", "modules")


def _others(line):
    # Every fixture violates exactly one condition: all other lines stay OK.
    return [n for n in LINES if n != line]


def _test(body):
    return {"tests/core/test_ok.py": BASE["tests/core/test_ok.py"] + body}


HARD = {
    "changelog-three-bullets": ({"CHANGELOG.md": CHANGELOG.replace("## Unreleased\n",
                                 "## Unreleased\n\n- a\n- b\n- c\n")}, RED, "changelog"),
    "changelog-below-top": ({"CHANGELOG.md": CHANGELOG + "- late\n"}, RED, "changelog"),
    "changelog-bullet-over-two-lines": ({"CHANGELOG.md": CHANGELOG.replace(   # the maintainer's "one or two lines"
        "## Unreleased\n", "## Unreleased\n\n- a\n  b\n  c\n")}, RED, "changelog"),
    # One line fewer, one public method more, within every cap: only the method growth is wrong.
    "family-public-growth": ({"lhpc/core/services.py": "from .service_x import X\nclass S(X):\n"
                              "    def run(self): pass\n    def more(self): pass\n"},
                             RED, "modules"),
    "test-outside-layers": ({"tests/misc/test_x.py": "def test_x():\n    assert 1\n"}, RED, "tests"),
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
        _commit(repo, {"CHANGELOG.md": text})
    rc, rows = _run(repo)
    assert (rc, rows["changelog"]) == (int(verdict == "HARD"), verdict)


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
    "sleep": (_test("\ndef test_two():\n    import time\n    time." + "sleep(0)\n    assert 2\n"),
              RED, (), "patterns"),
    "timed-loop-without-slow": (_test("\ndef test_two():\n    import time\n"
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
    _commit(repo, _test("\ndef test_two():\n    run('sleep 5')\n"))
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


# ---- CO-26: a budget row may name a file that is not Python (lines only) ---------------------------
VOICE = "int main(void)\n{\n    return 0;\n}\n"


def _c_budgeted(repo, lines=6):
    """A C repository's profile: its family is src/, its budget table names voice.c (lines, public)."""
    _commit(repo, {"src/voice.c": VOICE,
                   "tools/budgets.py": f'BUDGETS = {{\n    "voice.c": ({lines}, 0),\n}}\n'}, msg="c base\n")
    _profiled(repo, 'family_dir = "src/"\nbudget_file = "tools/budgets.py"\n')


def _modules_line(repo):
    p = subprocess.run([sys.executable, str(CHECKER), "base..HEAD"], cwd=repo,
                       capture_output=True, text=True, check=False)
    return p.returncode, next((ln for ln in p.stdout.splitlines() if ln.startswith("modules")), p.stderr)


def test_a_c_file_in_its_budget_is_measured_by_lines(repo):
    """CO-26; red before at 0af1d413: `ast.parse` of voice.c raised SyntaxError, the checker crashed (rc 1, a
    traceback, no modules line)."""
    _c_budgeted(repo)
    _commit(repo, {"src/voice.c": VOICE.replace("return 0;", "return 1;")})
    rc, line = _modules_line(repo)
    assert rc == 0 and line.split()[0] == "modules" and line.split()[-1] == "OK", line
    assert "voice.c 4->4 lines (budget 6), public N/A (not Python)" in line


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
