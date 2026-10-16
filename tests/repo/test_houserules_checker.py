"""tools/check_houserules.py against a throwaway git repository: each HARD condition fails the
range with that line HARD, each WARN condition passes it with that line WARN, a clean range passes.

The checker is a new invariant, so these are negative and positive fixture tests; no parent tree
existed for a red-before. Assertions read the exit code and each line's verdict token, never prose.
"""
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
    subprocess.run(["git", "-c", f"user.name={author}", "-c", "user.email=dev@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True)


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
    "changelog-bullet-over-three-lines": ({"CHANGELOG.md": CHANGELOG.replace(
        "## Unreleased\n", "## Unreleased\n\n- a\n  b\n  c\n  d\n")}, RED, "changelog"),
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
    _commit(repo, {"tests/repo/test_module_budgets.py": None,
                   "lhpc/core/services.py": BASE["lhpc/core/services.py"] + "\n"})
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
SPLIT = {"lhpc/core/service_x.py": "class X:\n    def a(self):\n        pass\n",
         "lhpc/core/service_y.py": '"""Y."""\nimport os\n\nclass Y:\n    def b(self):\n        pass\n',
         "lhpc/core/services.py": "from .service_x import X\nfrom .service_y import Y\n\nclass S(X, Y):\n"
                                  "    def run(self):\n        pass\n",
         "tests/repo/test_module_budgets.py": BUDGET.format(x="(3, 1),\n    \"service_y.py\": (6, 1),  # s1: split from service_x.py")}


@pytest.mark.parametrize("case, change, verdict", [
    ("split", {}, "WARN"),
    # The header excuse does not cover growth elsewhere, nor a split whose named source did not shrink.
    ("split-and-growth", {"lhpc/core/service_x.py": SPLIT["lhpc/core/service_x.py"] + "\n"}, "HARD"),
    ("split-from-a-file-that-did-not-shrink", {"tests/repo/test_module_budgets.py": BUDGET.format(
        x="(3, 1),\n    \"service_y.py\": (6, 1),  # s1: split from services.py")}, "HARD"),
])
def test_a_split_grows_the_family_only_by_the_new_header_and_the_facade_lines(repo, case, change, verdict):
    _commit(repo, SPLIT_BASE)
    _git(repo, "tag", "-f", "base")
    _commit(repo, {**SPLIT, **change})
    rc, rows = _run(repo)
    assert (rc, rows["modules"]) == ((0, "WARN") if verdict == "WARN" else (1, "HARD"))
