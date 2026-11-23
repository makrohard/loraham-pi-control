#!/usr/bin/env python3
"""Print the HOUSE RULES KEPT block for one item's commit range. Reads git, writes nothing.

    check_houserules.py <base>..<head> [--fact LITERAL]...

One line per rule (changelog, docs, tests, patterns, markers, wallclock, attribution, modules), each ending in
OK, WARN or HARD, then `VERDICT PASS|FAIL`. Exit 1 when a line is HARD, 2 on a usage or git error.
The exact conditions: tests/README.md rule 13 and the plan of this tool. WARN lines are judged by
the reviewer; HARD lines block.

A repository may carry `.houserules.toml` with its own layout (layers, test_files, test_def, budget_file,
family_dir, changelog, warn_patterns; a budgeted file that is not Python is held to its lines only), read at
<base> so a range cannot relax its own rules: a range that changes it is a `profile` HARD line; changing nothing
else it is a WARN, unless it relaxes or deletes a profile <base> has (a layer added, a test glob or warn pattern
dropped or changed, any other key changed): HARD. Without it the constants below apply. Attribution, markers and
the HARD patterns are the same for every repository. A malformed profile exits 2.

The gate is two runs, the program at <base> first (docs/maintenance.md has the command); the exit code is
the worse of the two, so a range cannot weaken the checker that judges it. A direct run does the same itself
when <base> holds a different tools/check_houserules.py (its output first, under its own heading).
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import fnmatch
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import tomllib

LAYERS = (
    ("tests/core/", "core"), ("tests/stacks/", "stacks"), ("tests/web/", "web"),
    ("tests/cli/", "cli"), ("tests/install/", "install"), ("tests/host/", "host"),
    ("tests/repo/", "repo"), ("tests/golden/", "golden"),
    ("testlab/tests/unit/", "testlab"), ("testlab/tests/acceptance/", "testlab"),
    ("testlab/tests/browser/", "testlab"), ("testlab/tests/release/", "testlab"),
    ("testlab/tests/slowbuild/", "testlab"), ("testlab/tests/hardware/", "testlab"),
    ("lhpc/data/meshcore_host/tests/", "in-package"), ("lhpc/data/rfdecode/tests/", "in-package"),
)
BUDGET_FILE = "tests/repo/test_module_budgets.py"
FAMILY_DIR = "lhpc/core/"
CHANGELOG = "CHANGELOG.md"
TEST_FILES = ("test_*.py",)            # globs on the file name, or on the path when they hold a "/"
TEST_DEF = re.compile(r"(async )?def test_")
# Assembled from fragments so that no committed file spells the names it looks for.
ATTRIBUTION = ("Clau" + "de", "Chat" + "GPT", "Anthro" + "pic", "Generated " + "with")
ALLOWED_AUTHORS = ("makrohard", "lhpc-release-bot")      # the maintainer's login and his release bot (L50)
ALLOWED_COMMITS = {"46f24a10ae6a61df2d4af215520120d4540bdfde"}  # maintainer's GitHub web merge
TRAILER = re.compile("^" + "Co-" + "Authored-By:", re.IGNORECASE | re.MULTILINE)
WORDS = re.compile(r"\b(" + "ag" + "ent" + "|" + "ses" + "sion" + r")\b", re.IGNORECASE)
# Product tokens that contain those words and are not attribution: an HTTP header, pytest's fixture
# scope, the suite's marker, the POSIX process term and Flask's request object used as code (an
# index or a dict method call; a sentence ending in the word stays counted).
WORD_EXEMPT = re.compile(r"user[-_]" + "ag" + "ent" + r'|scope="' + "ses" + 'sion"|needs_' + "ses" + "sion"
                         + r"|\b" + "ses" + r"sion leader"
                         + r"|\b" + "ses" + r"sion(?=\[|\.(?:get|pop|clear|setdefault)\()", re.IGNORECASE)
HARD_PATTERNS = {"getsource": re.compile(r"inspect\.getsource\("),
                 "assert-True": re.compile(r"^\s*assert True\b")}
WARN_PATTERNS = {"source-read": re.compile(r"""(read_text|open)\(.*["']lhpc/[^"']+\.(py|js|css)["']"""),
                 "sleep": re.compile(r"time\.sleep\("),           # outside WALLCLOCK_DIRS only: inside, the HARD row
                 "startswith": re.compile(r"assert .*\.startswith\("),
                 "network": re.compile(r"\b(requests\.|urllib\.request|socket\.create_connection)")}
# An exemption is judged per pattern hit: a hit is exempt only when its line's comment (a real Python comment, read
# with tokenize: never a string's text) carries the tag of that pattern's rule (tests/README.md "The rules") with a
# reason, `# rule-<n>: <reason>`. A wrong or unknown rule number or an empty
# reason exempts nothing, and one tag exempts only its own pattern. The legacy `# rule-1 exception` exempts getsource
# and source-read only; `assert True` and the textual sleep take no tag. Every exemption is listed in the row.
PATTERN_RULE = {"source-read": 1, "network": 4, "startswith": 10}
RULE_TAG = re.compile(r"#\s*rule-(?P<n>\d+):(?P<reason>[^#]*)")
EXEMPT = "# rule-1 exception"
LEGACY_PATTERNS = ("getsource", "source-read")
# The wall-clock rule (HARD, the same for every repository): a real clock in a test, judged on the HEAD file's AST
# for the added lines only. A call of time.time/monotonic/sleep (also through an alias of the module or of the
# function), a string with "sleep N" (an executed shell text or a fake-script body alike) and an argv list or tuple
# whose first element is "sleep". The escape: @pytest.mark.wallclock("reason") on the test, or `# wallclock: reason`
# on a line outside any test. A node is judged whole when any of its lines is an added line.
WALLCLOCK_DIRS = ("tests/", "testlab/tests/")
WALLCLOCK_CALLS = ("time", "monotonic", "sleep")
SLEEP_TEXT = re.compile(r"\bsleep\s+\d")
WALLCLOCK_COMMENT = re.compile(r"#\s*wallclock:\s*(?P<reason>\S.*)$")
RED_BEFORE = re.compile(r"red[- ]before", re.IGNORECASE)
RAISE_REASON = re.compile(r"#\s*\S+:\s*\S")
# A new family file whose budget row says `# <item>: split from <file> [and <file>...]`: the moved code cancels
# out in the family net, which may then grow only by the new file's header: above the first class only the
# docstring and imports, as AST nodes; the class line is bare (`class Name:`: no bases, no keywords, no type
# parameters) and carries no body.
# Facade growth is not excused; it is paid by the budget row.
SPLIT_FROM = re.compile(r"#\s*\S+:\s*split from (\S+\.py(?:\s+and\s+\S+\.py)*)")
# The rules without a profile, in the profile's terms: what `relaxed` compares an existing profile against.
DEFAULTS = {"layers": LAYERS, "test_files": TEST_FILES, "test_def": TEST_DEF.pattern, "budget_file": BUDGET_FILE,
            "family_dir": FAMILY_DIR, "changelog": CHANGELOG,
            "warn_patterns": {k: v.pattern for k, v in WARN_PATTERNS.items()}}


def git(*args: str) -> str:
    """git's output as text: UTF-8, an invalid byte read as U+FFFD, every line end kept as git wrote it."""
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout.decode("utf-8", "replace")


# Every diff the rows read is git's real one, whatever the repository's or the user's git config says
# (G2-CATTR1-R3): no colour, no external diff or text conversion, every file as text (binary, "-diff"), the b/
# prefix, no context line joining two hunks (the parsers count one all the same). Each caller names its rename
# detection.
PATCH = ("--no-color", "--no-ext-diff", "--no-textconv", "--text", "--dst-prefix=b/", "--unified=0",
         "--inter-hunk-context=0")
# Where Python ends a line; git ends one only at "\n".
PY_EOL = re.compile(r"\r\n|\r|\n")
# A control character in a row (a file name with a newline or a tab) is shown as \xNN: one row stays one line.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
UNQUOTE = {b"a": b"\a", b"b": b"\b", b"t": b"\t", b"n": b"\n", b"v": b"\v", b"f": b"\f", b"r": b"\r"}


def shown(text: str) -> str:
    return CONTROL.sub(lambda m: f"\\x{ord(m[0]):02x}", text)


def patch(cmd: str, *args: str) -> str:
    return git(cmd, *PATCH, *args)


def py_text_lines(src: str) -> list[str]:
    """The lines of a Python source as Python counts them (ast, tokenize): ended by "\\r\\n", "\\r" or "\\n" only, never
    by "\\f", "\\v" or the other ends str.splitlines() knows."""
    lines = PY_EOL.split(src)
    return lines[:-1] if lines[-1] == "" else lines


def lf_lines(text: str) -> list[str]:
    """The lines of a text as git counts them: split at "\n" only (str.splitlines also splits at "\r", "\v", "\f"...)."""
    lines = text.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def name_status(out: str) -> list[list[str]]:
    """`--name-status -z` output as [status, path(, new path)]: NUL-separated, so no path is quoted."""
    rows, fields = [], iter(out.split("\0")[:-1])
    for status in fields:
        rows.append([status, *(next(fields, "") for _ in range(2 if status[:1] in "RC" else 1))])
    return rows


def show(rev: str, path: str) -> str | None:
    """The file at rev; None only when rev has no such path. Decided by `git ls-tree`'s exit code and output (an absent
    path: rc 0, no line), never by git's message, which is localised; any failure is a git error (exit 2)."""
    if not git("ls-tree", rev, "--", path).strip():
        return None
    return git("show", f"{rev}:{path}")


def header_path(header: str) -> str | None:
    """The path a `+++ ` header names: the tab git appends after a path with a space dropped, git's C-quoted form
    ("b/\\303\\274.md") decoded, the b/ prefix removed; None for /dev/null."""
    header = header.removesuffix("\t")
    if header.startswith('"') and header.endswith('"'):
        raw = re.sub(rb"\\([0-3][0-7]{2}|.)", lambda m: bytes([int(m[1], 8)]) if len(m[1]) == 3
                     else UNQUOTE.get(m[1], m[1]), header[1:-1].encode())
        header = raw.decode("utf-8", "replace")
    return None if header == "/dev/null" else header.removeprefix("b/")


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(path, new line number, text) of every added line in a `PATCH` diff, read at "\n" only. A "+++ " line is the
    file header only outside a hunk: inside one it is an added line that starts with "++ ". In a merge's combined
    diff ("@@@", a column per parent) a line is added when it is new against every parent."""
    out, path, n, cols = [], None, 0, 0
    for line in lf_lines(diff):
        if line.startswith("diff "):
            cols = 0
        elif line.startswith("+++ ") and not cols:
            path = header_path(line[4:])
        elif m := re.match(r"(@@+) .*?\+(\d+)", line):
            n, cols = int(m[2]), len(m[1]) - 1
        elif cols and path and line[:1] in " +-" and "-" not in line[:cols]:     # a line of the new file
            if line[:cols] == "+" * cols:
                out.append((path, n, line[cols:]))
            n += 1
    return out


def py_lines(src: str, lines: set[int]) -> set[int]:
    """The Python line numbers on the given git lines of `src` (Python also ends a line at a lone "\r")."""
    out, first = set(), 1
    for g, text in enumerate(src.split("\n"), 1):
        k = len(PY_EOL.findall(text.removesuffix("\r"))) + 1
        if g in lines:
            out.update(range(first, first + k))
        first += k
    return out


def is_test_file(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path if "/" in g else path.rsplit("/", 1)[-1], g) for g in TEST_FILES)


class ProfileError(Exception):
    pass


def _as(p: dict, key: str, kind: type):
    """`p[key]` when it is a `kind` (an absent key: empty); any other type is a ProfileError."""
    v = p.get(key, kind())
    if not isinstance(v, kind):
        raise ProfileError(f"{key} must be a {kind.__name__}")
    return v


def profile(rev: str) -> dict | None:
    """`rev`'s `.houserules.toml` as the rules it sets over the defaults (patterns as their source text),
    None without one; a malformed profile is a ProfileError."""
    text = show(rev, ".houserules.toml")
    if text is None:
        return None
    try:
        p = tomllib.loads(text)
        if unknown := set(p) - {"layers", "test_files", "test_def", "budget_file", "family_dir", "changelog",
                                "warn_patterns"}:
            raise ProfileError(f"unknown keys {sorted(unknown)}")
        if (any(not isinstance(p[k], str) for k in ("test_def", "budget_file", "family_dir", "changelog") if k in p)
                or not all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x)
                           for x in _as(p, "layers", list))
                or not all(isinstance(g, str) for g in _as(p, "test_files", list))
                or not all(isinstance(v, str) for v in _as(p, "warn_patterns", dict).values())):
            raise ProfileError("layers is a list of [prefix, name] pairs, test_files a list of strings, "
                               "warn_patterns a table of strings, the other keys strings")
        [re.compile(v) for v in [p.get("test_def", ""), *_as(p, "warn_patterns", dict).values()]]
    except (tomllib.TOMLDecodeError, re.error, TypeError, ValueError, AttributeError) as exc:
        raise ProfileError(str(exc)) from None
    return {**DEFAULTS, **{k: v for k, v in p.items() if k not in ("layers", "test_files")},
            **({"layers": tuple(tuple(x) for x in p["layers"])} if "layers" in p else {}),
            **({"test_files": tuple(p["test_files"])} if "test_files" in p else {})}


def relaxed(old: dict, new: dict | None) -> list[str]:
    """The keys in which `new` is more lenient than `old`: a layer added (more paths count as layered), a test
    glob or warn pattern dropped or changed, any other key changed; every key when the profile is deleted.
    Tightening only removes layers or adds globs and patterns."""
    if new is None:
        return ["deleted"]
    out = [k for k in ("test_def", "budget_file", "family_dir", "changelog") if new[k] != old[k]]
    out += ["layers"] * bool(set(new["layers"]) - set(old["layers"]))
    out += ["test_files"] * bool(set(old["test_files"]) - set(new["test_files"]))
    out += ["warn_patterns"] * any(new["warn_patterns"].get(k) != v for k, v in old["warn_patterns"].items())
    return out


def load_profile(rev: str, apply: bool = True) -> None:
    """Check `rev`'s `.houserules.toml` and apply it over the constants above (one run, one repository);
    `apply=False` only checks it (<head>'s copy: a range must not hand the next range a broken profile)."""
    global LAYERS, TEST_FILES, TEST_DEF, BUDGET_FILE, FAMILY_DIR, CHANGELOG, WARN_PATTERNS
    p = profile(rev)
    if p is None or not apply:
        return
    LAYERS, TEST_FILES, TEST_DEF = p["layers"], p["test_files"], re.compile(p["test_def"])
    BUDGET_FILE, FAMILY_DIR, CHANGELOG = p["budget_file"], p["family_dir"], p["changelog"]
    WARN_PATTERNS = {str(k): re.compile(v) for k, v in p["warn_patterns"].items()}


def layer_of(path: str) -> str | None:
    return next((layer for prefix, layer in LAYERS if path.startswith(prefix)), None)


def measure(source: str, path: str = ".py") -> tuple[int, int | None]:
    """(lines, public methods); a budgeted file that is not Python is measured by its lines only (public None)."""
    if not path.endswith(".py"):
        return len(lf_lines(source)), None
    tree = ast.parse(source)
    public = sum(1 for cls in tree.body if isinstance(cls, ast.ClassDef) for node in cls.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and not node.name.startswith("_"))
    return len(py_text_lines(source)), public


def header_lines(source: str) -> int | None:
    """The lines up to and including the module's first `class` line, when above it there are only the docstring
    and imports, as AST nodes, every other line is blank, and the class line is bare (`class Name:`: no bases, no
    keywords, no type parameters) and carries no body. None otherwise (code or comments above the class, code on
    the class line, or no class)."""
    body = ast.parse(source).body
    first = next((n for n in body if isinstance(n, ast.ClassDef)), None)
    if (first is None or first.bases or first.keywords or getattr(first, "type_params", None)
            or first.body[0].lineno == first.lineno):
        return None
    above = body[:body.index(first)]
    if not all(isinstance(n, (ast.Import, ast.ImportFrom)) or (k == 0 and isinstance(n, ast.Expr)
               and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))
               for k, n in enumerate(above)):
        return None
    allowed = {i for n in above for i in range(n.lineno, n.end_lineno + 1)}
    lines = py_text_lines(source)
    return None if any(lines[i - 1].strip() and i not in allowed for i in range(1, first.lineno)) else first.lineno


def budgets(source: str | None) -> dict | None:
    if source is None:
        return None
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return None
    for node in body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BUDGETS" for t in node.targets):
            try:
                table = ast.literal_eval(node.value)
            except (ValueError, TypeError):    # not a literal (a call, a name, an unhashable key)
                return None
            # The only shape the rule reads: {"module.py": (lines, public)}; anything else is no table.
            ok = isinstance(table, dict) and all(
                isinstance(k, str) and isinstance(v, (tuple, list)) and len(v) == 2
                and all(isinstance(n, int) and not isinstance(n, bool) for n in v) for k, v in table.items())
            return table if ok else None
    return None


def changelog_sections(rev: str) -> tuple[int, int]:
    """(the line of the second `## ` heading, the top section's length) of CHANGELOG at `rev`."""
    lines = lf_lines(show(rev, CHANGELOG) or "") if CHANGELOG else []
    heads = [i + 1 for i, line in enumerate(lines) if line.startswith("## ")]
    second = heads[1] if len(heads) > 1 else len(lines) + 1
    return second, (second - heads[0] - 1) if heads else 0


def changelog_bullets(added: list, second: int) -> tuple[list[int], bool]:
    """The lines of each added CHANGELOG bullet, and whether one sits below the top section (at `second`)."""
    bullets, below, active, last = [], False, False, None
    for path, n, text in added:
        if path != CHANGELOG:
            continue
        if text.startswith("- "):
            bullets.append(1)
            below = below or n >= second
            active = True
        elif text.startswith("  ") and active and n == last + 1:
            bullets[-1] += 1                 # a continuation: directly below its bullet, indented
        else:
            active = False
        last = n
    return bullets, below


def own_diff(sha: str, *args: str) -> str:
    """The commit's own change, against its first parent, renames off (a merge's combined diff hides what it brings
    in; a root commit: against nothing)."""
    parents = git("rev-list", "--parents", "-n", "1", sha).split()[1:]
    if not parents:
        return patch("show", "--no-renames", "--format=", *args, sha)
    return patch("diff", "--no-renames", *args, parents[0], sha)


def deleted_lines(diff: str) -> list[int]:
    """The old line number of every deleted line in a two-tree `PATCH` diff, read at "\n" only."""
    out, n, hunk = [], 0, False
    for line in lf_lines(diff):
        if line.startswith("diff --git"):
            hunk = False
        elif m := re.match(r"@@ -(\d+)", line):
            n, hunk = int(m[1]), True
        elif hunk and line[:1] in "- ":      # a deleted or a context line ("" under diff.suppressBlankEmpty)
            if line[:1] == "-":
                out.append(n)
            n += 1
    return out


def top_section(rev: str) -> tuple[int, int] | None:
    """CHANGELOG's top `## ` section at `rev`: (its heading's line, the line after its end); None without a heading."""
    lines = lf_lines(show(rev, CHANGELOG) or "")
    heads = [i + 1 for i, line in enumerate(lines) if line.startswith("## ")]
    return (heads[0], heads[1] if len(heads) > 1 else len(lines) + 1) if heads else None


def release_section_commit(sha: str) -> bool:
    """Whether commit `sha` writes the release's section, written once before the tag, which the cap of two bullets
    per commit does not hold: not a merge, it modifies CHANGELOG alone, adds at least one bullet, and every added
    line lies inside its top `## ` section, every deleted one inside its parent's. Any other commit is held to it."""
    if len(git("rev-list", "--parents", "-n", "1", sha).split()) != 2:      # a merge or a root commit
        return False
    if name_status(own_diff(sha, "--name-status", "-z")) != [["M", CHANGELOG]]:
        return False
    new, old = top_section(sha), top_section(f"{sha}^1")
    diff = own_diff(sha)
    added = added_lines(diff)
    return (new is not None and any(t.startswith("- ") for _, _, t in added)
            and all(new[0] <= n < new[1] for _, n, _ in added)
            and all(old is not None and old[0] <= n < old[1] for n in deleted_lines(diff)))


def section_span(lines: list[str], version: str) -> tuple[int, int] | None:
    """The `## <version>` section of CHANGELOG lines: (its heading's line, the line after its end, at the next `## `
    heading or the end); None without that heading."""
    starts = [i + 1 for i, line in enumerate(lines) if line.startswith("## ") and line[3:].strip() == version]
    if not starts:
        return None
    return starts[0], next((i + 1 for i in range(starts[0], len(lines)) if lines[i].startswith("## ")),
                           len(lines) + 1)


def section_text(text: str | None, version: str) -> str | None:
    """The `## <version>` section of a CHANGELOG text, stripped; None without that heading."""
    lines = lf_lines(text or "")
    span = section_span(lines, version)
    return "\n".join(lines[span[0] - 1:span[1] - 1]).strip() if span else None


def placed_section_commit(sha: str) -> str | None:
    """The version of the RELEASED section a commit copies whole into CHANGELOG, wherever it sits (a merge-back below
    a newer cycle), else None. Not a merge, it modifies CHANGELOG alone and deletes none of its lines, its non-blank
    added lines are exactly one `## <v>` section's (blank ones only inside it or just above its heading), its parent
    has no `## <v>`, and the section equals `## <v>` at tag `v<v>`. The cap of two bullets and the placement rule do
    not hold it: it is the release's section, written once and reviewed at the release."""
    if not CHANGELOG or len(git("rev-list", "--parents", "-n", "1", sha).split()) != 2:
        return None
    if name_status(own_diff(sha, "--name-status", "-z")) != [["M", CHANGELOG]]:
        return None
    diff = own_diff(sha)
    added = [(n, t) for path, n, t in added_lines(diff) if path == CHANGELOG]
    if deleted_lines(diff) or not added:
        return None
    lines = lf_lines(show(sha, CHANGELOG) or "")
    new = [n for n, t in added if t.startswith("## ")]
    if len(new) != 1:
        return None
    start, version = new[0], lines[new[0] - 1][3:].strip()
    end = next((i + 1 for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines) + 1)
    if sorted(n for n, t in added if t.strip()) != [n for n in range(start, end) if lines[n - 1].strip()]:
        return None
    if any(not start - 1 <= n < end for n, _ in added):
        return None
    if section_text(show(f"{sha}^1", CHANGELOG), version) is not None:
        return None
    tag = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/v{version}^{{commit}}"],
                         capture_output=True, check=False)
    if tag.returncode or section_text(show(f"refs/tags/v{version}", CHANGELOG), version) != \
            section_text("\n".join(lines), version):
        return None
    return version


def misplaced(sha: str) -> bool:
    """Whether commit `sha` puts a CHANGELOG entry below the top section it saw: a bullet below its own top
    section, or a `## ` heading anywhere but above all of its parent's (which would file current entries under
    an older-looking section), or a `## ` heading of its parent removed or renamed (its entries would read as
    another section's; a release-section commit renaming its top heading is not judged here). Judged per commit, so a bullet written into the then-top section stays
    placed when a later commit opens a newer one above it. A parent without a `## ` heading constrains no heading."""
    added = added_lines(own_diff(sha))
    second, _ = changelog_sections(sha)
    if changelog_bullets(added, second)[1]:
        return True
    lines = lf_lines(show(sha, CHANGELOG) or "")
    old_top = top_section(f"{sha}^1") if len(git("rev-list", "--parents", "-n", "1", sha).split()) > 1 else None
    if old_top is None:
        return False
    old_lines = lf_lines(show(f"{sha}^1", CHANGELOG) or "")
    # a heading removed (or renamed): its entries now read as another section's
    if Counter(line for line in old_lines if line.startswith("## ")) - Counter(
            line for line in lines if line.startswith("## ")):
        return True
    old_heading = old_lines[old_top[0] - 1]
    first_old = lines.index(old_heading) + 1
    return any(p == CHANGELOG and t.startswith("## ") and n > first_old for p, n, t in added)


def marks_of(fn) -> dict[str, str | None]:
    """The pytest marks a function's decorators apply, from their AST (G1-TR1-C B2): `@pytest.mark.<name>` or
    `@mark.<name>`, bare or called; the value is the first argument when it is a non-blank string, else None.
    Text that only looks like a mark (a parametrize value) is no mark."""
    out = {}
    for d in fn.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        if not isinstance(f, ast.Attribute):
            continue
        v = f.value
        if (isinstance(v, ast.Attribute) and v.attr == "mark" and isinstance(v.value, ast.Name)
                and v.value.id == "pytest") or (isinstance(v, ast.Name) and v.id == "mark"):
            a = d.args[0] if isinstance(d, ast.Call) and d.args else None
            ok = isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.strip()
            out[f.attr] = a.value.strip() if ok else None
    return out


def test_scopes(src: str) -> list[tuple[int, int, int, str, dict[str, str | None]]]:
    """(first line with its decorators, def line, last line, name, marks) of every test in a source: module-level
    `test_*` functions and the `test_*` methods of a `Test*` class, as pytest collects them. Raises SyntaxError for a
    file that does not parse. The one attribution of a line to its test (G1-TR1 B2): it reads the whole file, not the
    diff; a test's decorators are its own lines (G1-TR1-C B3)."""
    out = []

    def add(fn, prefix=""):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name.startswith("test"):
            first = min([fn.lineno, *(d.lineno for d in fn.decorator_list)])
            out.append((first, fn.lineno, fn.end_lineno, prefix + fn.name, marks_of(fn)))

    for node in ast.parse(src).body:
        add(node)
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                add(item, node.name + "::")
    return out


def wallclock_lines(src: str, added: set[int]) -> list[int]:
    """The added lines of a source that use a real clock (WALLCLOCK_CALLS, SLEEP_TEXT, a "sleep" argv). Raises
    SyntaxError for a file that does not parse."""
    tree = ast.parse(src)
    modules, funcs = {"time"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "time"}
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            funcs |= {a.asname or a.name for a in node.names if a.name in WALLCLOCK_CALLS}
    lines = py_text_lines(src)
    hits = set()
    for node in ast.walk(tree):
        span = [n for n in range(node.lineno, node.end_lineno + 1) if n in added] if hasattr(node, "lineno") else []
        if not span:
            continue
        f = node.func if isinstance(node, ast.Call) else None
        clock = (isinstance(f, ast.Attribute) and f.attr in WALLCLOCK_CALLS and isinstance(f.value, ast.Name)
                 and f.value.id in modules) or (isinstance(f, ast.Name) and f.id in funcs)
        argv = (isinstance(node, (ast.List, ast.Tuple)) and bool(node.elts)
                and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "sleep")
        if clock or argv:
            hits.add(span[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            hits |= {n for n in span if SLEEP_TEXT.search(lines[n - 1])}
    return sorted(hits)


def comments(src: str | None) -> dict[int, str]:
    """Line number -> its comment, from tokenize; {} for a file that does not tokenize, so nothing in it is exempt."""
    out = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src or "").readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string
    except (tokenize.TokenError, SyntaxError):
        return {}
    return out


def exemption(pattern: str, text: str) -> str | None:
    """`rule-<n> (<reason>)` when the line's comment exempts this pattern's hit on it, else None (the hit counts)."""
    rule = PATTERN_RULE.get(pattern)
    for m in RULE_TAG.finditer(text):
        if rule is not None and m["n"] == str(rule) and m["reason"].strip():  # as text: any other number is a hit
            return f"rule-{rule} ({m['reason'].strip()})"
    if pattern in LEGACY_PATTERNS and EXEMPT in text:
        return f"rule-1 ({text.split(EXEMPT, 1)[1].strip(' :') or 'legacy tag'})"
    return None


def check(base: str, head: str, facts: list[str], correction: bool = False) -> list[tuple[str, str, str]]:
    rng = f"{base}..{head}"
    added = added_lines(patch("diff", "-M", rng))
    status = name_status(patch("diff", "-M", "--name-status", "-z", rng))
    new_files = {p[-1] for p in status if p[0][0] in "ARC"}     # a renamed or copied test file is placed anew
    changed = {p[-1] for p in status}
    # one commit at a time: a message may hold any byte but NUL, so no separator could split the log safely
    commits = [(sha, *git("log", "-1", "--no-color", "--format=%an%x00%B", sha).rstrip("\n").split("\x00", 1))
               for sha in git("rev-list", rng).split()]
    rows = []

    if ".houserules.toml" in changed:      # alone: reviewed on its own; with other files: it would gate itself
        old = profile(base)
        loose = relaxed(old, profile(head)) if old is not None else []
        rows.append(("profile", ".houserules.toml changed in this range (rules read at <base>)"
                     + (f"; relaxes {', '.join(loose)}" if loose else ""),
                     "WARN" if changed == {".houserules.toml"} and not loose else "HARD"))

    # changelog: the count per commit (one item each, against its first parent), except a release-section commit;
    # each bullet's length over the range, so a bullet grown over two commits is still one bullet; its place per
    # commit, against the sections that commit saw (`misplaced`): a later, newer section above it moves nothing
    second, top = changelog_sections(head)
    bullets, _ = changelog_bullets(added, second)
    copies = {sha: v for sha, _, _ in commits if (v := placed_section_commit(sha))}
    placed = set(copies.values())
    release = {sha for sha, _, _ in commits if sha in copies or release_section_commit(sha)}
    below = any(misplaced(sha) for sha, _, _ in commits if sha not in release) if CHANGELOG else False
    most = max((len(changelog_bullets(added_lines(own_diff(sha)), second)[0])
                for sha, _, _ in commits if sha not in release), default=0) if CHANGELOG else 0
    long_bullet = any(b > 2 for b in bullets)
    # `--correction`: an item that only corrects a released section says so, and its bullets
    # below the top section are shown (WARN), not refused.
    v = ("HARD" if most > 2 or long_bullet or (below and not correction)
         else "WARN" if below else "OK")
    rows.append(("changelog", f"+{len(bullets)} bullets, at most {most} in a commit (limit 2 per commit, each "
                              f"≤ 2 lines; longest {max(bullets, default=0)}), top section {top} lines"
                              + (f"; {len(release)} release-section commit(s) not capped" if release else "")
                              + (f", released section(s) {', '.join(sorted(placed))} placed whole" if placed else ""),
                 v))

    # docs
    docs = sorted(p for p in changed if (p.startswith("docs/") and p.endswith(".md")) or p == "README.md")
    doc_lines = sum(1 for p, _, _ in added if p in docs)
    found, repeated = [], False
    for fact in facts:
        r = subprocess.run(["git", "grep", "--no-color", "-lzF", "--", fact, head, "--", "docs", "README.md"],
                           capture_output=True, check=False)
        files = sorted({x.split(":", 1)[1] for x in r.stdout.decode("utf-8", "replace").split("\0") if x})
        found.append(f'fact "{fact}" -> {",".join(files) or "none"}')
        repeated = repeated or len(files) > 1
    v = "WARN" if (docs and not facts) or repeated else "OK"
    rows.append(("docs", f"{len(docs)} files, +{doc_lines} lines; " + ("; ".join(found) or "no fact given"), v))

    # tests
    new_tests = sorted(p for p in new_files if is_test_file(p))
    unlayered = [p for p in new_tests if layer_of(p) is None]
    layers = sorted({f"{p.rsplit('/', 1)[0]}={layer_of(p) or 'NONE'}" for p in changed if is_test_file(p)})
    with_tests = stated = 0
    for sha, _, msg in commits:
        own = added_lines(patch("show", "-M", "--format=", sha))
        if any(is_test_file(p) and TEST_DEF.match(t) for p, _, t in own):
            with_tests += 1
            stated += bool(RED_BEFORE.search(msg))
    n_new = sum(1 for p, _, t in added if is_test_file(p) and TEST_DEF.match(t))
    v = "HARD" if unlayered else "WARN" if stated < with_tests else "OK"
    rows.append(("tests", f"{n_new} new tests; layers {' '.join(layers) or '-'}; "
                          f"red-before {stated}/{with_tests}" + (f"; unlayered {' '.join(unlayered)}" if unlayered else ""), v))

    # patterns
    in_tests = [(p, n, t) for p, n, t in added if is_test_file(p) or layer_of(p)]
    counts = dict.fromkeys((*HARD_PATTERNS, *WARN_PATTERNS), 0)
    exempt, notes = [], {}
    for p, n, text in in_tests:
        if p not in notes:
            notes[p] = comments(show(head, p)) if p.endswith(".py") else {}
        for k, rx in (*HARD_PATTERNS.items(), *WARN_PATTERNS.items()):
            if k == "sleep" and p.startswith(WALLCLOCK_DIRS):
                continue                    # the wallclock row judges a sleep there (HARD), not this textual WARN
            if not rx.search(text):
                continue
            reason = exemption(k, notes[p].get(n, ""))
            if reason:
                exempt.append(f"{p}:{n} {reason}")
            else:
                counts[k] += 1
    v = ("HARD" if any(counts[k] for k in HARD_PATTERNS)
         else "WARN" if any(counts[k] for k in WARN_PATTERNS) else "OK")
    rows.append(("patterns", " ".join(f"{k} {c}" for k, c in counts.items())
                 + (f"; {len(exempt)} exempt: {'; '.join(exempt)}" if exempt else ""), v))

    # markers
    unmarked = 0
    by_file: dict[str, set[int]] = {}
    for p, n, _ in in_tests:
        by_file.setdefault(p, set()).add(n)
    by_file = {p: py_lines(show(head, p) or "", lines) for p, lines in by_file.items()}   # as Python numbers them
    for p, lines in by_file.items():
        src = show(head, p)
        try:
            scopes = test_scopes(src) if src is not None else []
        except SyntaxError:
            scopes = []                      # the wallclock row reports an unparsable file in its scope
        src_lines = py_text_lines(src or "")
        for _, defline, last, _, marks in scopes:
            if defline not in lines:         # only a test whose def line is added
                continue
            text = "\n".join(src_lines[defline:last])
            slow_work = "venv" in text or "pip install" in text or ("while" in text and "monotonic" in text)
            unmarked += slow_work and "slow" not in marks
    rows.append(("markers", f"{unmarked} without slow", "WARN" if unmarked else "OK"))

    # wallclock
    bad, escapes = [], []
    for p, lines in sorted(by_file.items()):
        if not p.startswith(WALLCLOCK_DIRS) or not p.endswith(".py"):
            continue
        src = show(head, p)
        if src is None:
            continue
        try:
            hits = wallclock_lines(src, lines)
            scopes = test_scopes(src)
        except SyntaxError:
            bad.append(f"{p} does not parse")
            continue
        src_lines = py_text_lines(src)
        for n in hits:
            test = next((t for t in scopes if t[0] <= n <= t[2]), None)
            if test is not None:
                reason, where = test[4].get("wallclock"), f"{p}::{test[3]}"
            else:
                m = WALLCLOCK_COMMENT.search(src_lines[n - 1])
                reason, where = (m["reason"].strip() if m else None), f"{p}:{n}"
            if reason is None:
                bad.append(f"{p}:{n}")
            elif f"{where} ({reason})" not in escapes:
                escapes.append(f"{where} ({reason})")
    rows.append(("wallclock", f"{len(bad)} unmarked{': ' + ' '.join(bad) if bad else ''}; "
                              f"{len(escapes)} escaped{': ' + '; '.join(escapes) if escapes else ''}",
                 "HARD" if bad else "OK"))

    # attribution
    authors = sorted({a for sha, a, _ in commits if sha not in ALLOWED_COMMITS})
    trailers = sum(len(TRAILER.findall(msg)) for _, _, msg in commits)
    # Every added line counts, wherever its text was before; only a file renamed with identical content (-M100%: no
    # hunk, whatever diff.renames says) adds none. The row names where each word is, for the review of the WARN.
    texts = [(f"commit {sha[:8]}", msg) for sha, _, msg in commits]
    texts += [(f"{path}:{n}", t) for path, n, t in added_lines(patch("diff", "-M100%", rng))]
    hits = [(where, c) for where, t in texts
            if (c := sum(name.lower() in t.lower() for name in ATTRIBUTION) + len(WORDS.findall(WORD_EXEMPT.sub("", t))))]
    words = sum(c for _, c in hits)
    v = "HARD" if trailers or any(a not in ALLOWED_AUTHORS for a in authors) else "WARN" if words else "OK"
    rows.append(("attribution", f"authors {','.join(authors) or '-'}; trailers {trailers}; words {words}"
                 + (f" at {', '.join(where for where, _ in hits)}" if hits else ""), v))

    # modules
    table, base_table = (budgets(show(head, BUDGET_FILE)), budgets(show(base, BUDGET_FILE))) if BUDGET_FILE else (None, None)
    before = base_table or {}
    if table is None:                      # a table <base> had cannot vanish in the range it would gate
        rows.append(("modules", f"{BUDGET_FILE} has no budget table at <head>" if base_table is not None
                     else "N/A (budget table absent)", "HARD" if base_table is not None else "OK"))
        return rows
    parts, over, net, excused, raised_bad, raised_ok, splits, sizes = [], False, 0, 0, [], set(), {}, {}
    net_public, excused_public = 0, 0
    head_budget_lines = py_text_lines(show(head, BUDGET_FILE) or "")
    for name, value in table.items():
        if name in before and any(v > b for v, b in zip(value, before[name], strict=True)):
            line = next((ln for ln in head_budget_lines if f'"{name}"' in ln), "")
            (raised_ok.add(name) if RAISE_REASON.search(line) else raised_bad.append(name))
        elif name not in before:
            line = next((ln for ln in head_budget_lines if f'"{name}"' in ln), "")
            if m := SPLIT_FROM.search(line):
                splits[name] = re.findall(r"\S+\.py", m.group(1))
    for name in sorted(table):
        path = FAMILY_DIR + name
        if path not in changed:
            continue
        a = measure(show(base, path) or "", path)
        b = measure(show(head, path) or "", path)
        sizes[name] = (a[0], b[0])
        net += b[0] - a[0]
        excused += max(0, b[0] - a[0]) if name in raised_ok else 0
        over = over or b[0] > table[name][0]
        if b[1] is None:                   # not Python: no public-method count, its column not applied
            parts.append(f"{name} {a[0]}->{b[0]} lines (budget {table[name][0]}), public N/A (not Python)")
            continue
        net_public += b[1] - a[1]
        excused_public += max(0, b[1] - a[1]) if name in raised_ok else 0
        over = over or b[1] > table[name][1]
        parts.append(f"{name} {a[0]}->{b[0]} lines (budget {table[name][0]}), "
                     f"{a[1]}->{b[1]} public (budget {table[name][1]})")
    # A split counts only when every file it names is a family file that shrank in this range and the new file's
    # header is valid (`header_lines`).
    heads = {name: header_lines(show(head, FAMILY_DIR + name) or "") if name.endswith(".py") else None
             for name in splits}
    split_bad = [name for name, sources in splits.items() if name not in sizes or heads[name] is None
                 or not all(s in sizes and sizes[s][1] < sizes[s][0] for s in sources)]
    excused += sum(heads[name] for name in splits if name not in split_bad)
    # Only a raised file's own growth is excused, for lines and for public methods alike.
    grown_unexcused = net - excused > 0 or net_public - excused_public > 0
    v = ("HARD" if over or grown_unexcused or raised_bad or split_bad
         else "WARN" if net > 0 or net_public > 0 else "OK")
    rows.append(("modules", "; ".join(parts or ["no family file touched"])
                 + f"; family net {net:+d} lines, {net_public:+d} public" + (f"; raised without reason {','.join(raised_bad)}" if raised_bad else "")
                 + (f"; invalid split (a named file did not shrink, no class, more than docstring and imports above it, or code on its class line) {','.join(split_bad)}" if split_bad else ""), v))
    return rows


def base_program(base: str, argv: list[str]) -> int | None:
    """Run <base>'s checker on the same arguments when it differs from this one; its exit code, or None
    (no checker at <base>, or the same program). No marker stops the recursion, so nothing a caller sets can
    skip the base run: in the base run `__file__` is <base>'s program, which equals itself."""
    theirs = show(base, "tools/check_houserules.py")
    with open(__file__, encoding="utf-8") as fh:
        if theirs is None or theirs == fh.read():
            return None
    with tempfile.TemporaryDirectory() as d:
        prog = os.path.join(d, "check_houserules.py")
        with open(prog, "w", encoding="utf-8") as fh:
            fh.write(theirs)
        p = subprocess.run([sys.executable, "-I", prog, *argv], capture_output=True, text=True, check=False)
    print(f"BASE PROGRAM  tools/check_houserules.py at {base} (authoritative)")
    print(p.stdout + p.stderr, end="")
    print("THIS PROGRAM  (forward check)")
    return p.returncode


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("range", help="<base>..<head>")
    ap.add_argument("--fact", action="append", default=[], help="a literal to look for in docs/")
    ap.add_argument("--correction", action="store_true",
                    help="this item only corrects a released CHANGELOG section")
    args = ap.parse_args(argv)
    if ".." not in args.range:
        ap.error("the range must be <base>..<head>")
    base, head = args.range.split("..", 1)
    base_rc = base_program(base, argv)
    rc = _check(args, base, head)
    return rc if base_rc is None else max(rc, base_rc)


def _check(args, base: str, head: str) -> int:
    try:
        load_profile(head, apply=False)
        load_profile(base)
        rows = check(base, head, args.fact, args.correction)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {' '.join(exc.cmd)}: {exc.stderr.decode('utf-8', 'replace').strip()}", file=sys.stderr)
        return 2
    except ProfileError as exc:
        print(f".houserules.toml: {exc}", file=sys.stderr)
        return 2
    hard = sum(v == "HARD" for _, _, v in rows)
    warn = sum(v == "WARN" for _, _, v in rows)
    print(f"HOUSE RULES KEPT  {args.range}")
    for name, text, verdict in rows:
        print(f"{name:<12} {shown(text)}   {verdict}")
    print(f"{'VERDICT':<12} {'FAIL' if hard else 'PASS'}  (hard {hard}, warn {warn})")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
