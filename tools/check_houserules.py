#!/usr/bin/env python3
"""Print the HOUSE RULES KEPT block for one item's commit range. Reads git, writes nothing.

    check_houserules.py <base>..<head> [--fact LITERAL]...

One line per rule (changelog, docs, tests, patterns, markers, attribution, modules), each ending in
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
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
import tomllib

LAYERS = (
    ("tests/core/", "core"), ("tests/stacks/", "stacks"), ("tests/web/", "web"),
    ("tests/cli/", "cli"), ("tests/install/", "install"), ("tests/host/", "host"),
    ("tests/repo/", "repo"), ("tests/golden/", "golden"),
    ("testlab/tests/unit/", "testlab"), ("testlab/tests/acceptance/", "testlab"),
    ("testlab/tests/browser/", "testlab"), ("testlab/tests/release/", "testlab"),
    ("testlab/tests/slowbuild/", "testlab"),
    ("lhpc/data/meshcore_host/tests/", "in-package"), ("lhpc/data/rfdecode/tests/", "in-package"),
)
BUDGET_FILE = "tests/repo/test_module_budgets.py"
FAMILY_DIR = "lhpc/core/"
CHANGELOG = "CHANGELOG.md"
TEST_FILES = ("test_*.py",)            # globs on the file name, or on the path when they hold a "/"
TEST_DEF = re.compile(r"(async )?def test_")
# Assembled from fragments so that no committed file spells the names it looks for.
ATTRIBUTION = ("Clau" + "de", "Chat" + "GPT", "Anthro" + "pic", "Generated " + "with")
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
                 "sleep": re.compile(r"time\.sleep\("),
                 "startswith": re.compile(r"assert .*\.startswith\("),
                 "network": re.compile(r"\b(requests\.|urllib\.request|socket\.create_connection)")}
EXEMPT = "# rule-1 exception"
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
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def show(rev: str, path: str) -> str | None:
    """The file at rev; None only when rev has no such path. Decided by `git ls-tree`'s exit code and output (an absent
    path: rc 0, no line), never by git's message, which is localised; any failure is a git error (exit 2)."""
    if not git("ls-tree", rev, "--", path).strip():
        return None
    return git("show", f"{rev}:{path}")


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(path, new line number, text) of every added line in a `--unified=0` diff."""
    out, path, n = [], None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            n = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and path:
            out.append((path, n, line[1:]))
            n += 1
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
        return len(source.splitlines()), None
    tree = ast.parse(source)
    public = sum(1 for cls in tree.body if isinstance(cls, ast.ClassDef) for node in cls.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and not node.name.startswith("_"))
    return len(source.splitlines()), public


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
    lines = source.splitlines()
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
    lines = (show(rev, CHANGELOG) or "").splitlines() if CHANGELOG else []
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


def check(base: str, head: str, facts: list[str], correction: bool = False) -> list[tuple[str, str, str]]:
    rng = f"{base}..{head}"
    added = added_lines(git("diff", "--unified=0", rng))
    status = [line.split("\t") for line in git("diff", "--name-status", rng).splitlines()]
    new_files = {p[-1] for p in status if p[0].startswith("A")}
    changed = {p[-1] for p in status}
    commits = [c.strip("\n").split("\x00", 2)
               for c in git("log", "--format=%H%x00%an%x00%B%x1e", rng).split("\x1e") if c.strip()]
    rows = []

    if ".houserules.toml" in changed:      # alone: reviewed on its own; with other files: it would gate itself
        old = profile(base)
        loose = relaxed(old, profile(head)) if old is not None else []
        rows.append(("profile", ".houserules.toml changed in this range (rules read at <base>)"
                     + (f"; relaxes {', '.join(loose)}" if loose else ""),
                     "WARN" if changed == {".houserules.toml"} and not loose else "HARD"))

    # changelog: the count per commit (one item each); each bullet's length and place over the range, against
    # <head>'s sections, so a bullet grown over two commits is still one bullet
    second, top = changelog_sections(head)
    bullets, below = changelog_bullets(added, second)
    most = max((len(changelog_bullets(added_lines(git("show", "--unified=0", "--format=", sha)), second)[0])
                for sha, _, _ in commits), default=0) if CHANGELOG else 0
    long_bullet = any(b > 2 for b in bullets)
    # `--correction`: an item that only corrects a released section says so, and its bullets
    # below the top section are shown (WARN), not refused.
    v = ("HARD" if most > 2 or long_bullet or (below and not correction)
         else "WARN" if below else "OK")
    rows.append(("changelog", f"+{len(bullets)} bullets, at most {most} in a commit (limit 2 per commit, each "
                              f"≤ 2 lines; longest {max(bullets, default=0)}), top section {top} lines", v))

    # docs
    docs = sorted(p for p in changed if (p.startswith("docs/") and p.endswith(".md")) or p == "README.md")
    doc_lines = sum(1 for p, _, _ in added if p in docs)
    found, repeated = [], False
    for fact in facts:
        r = subprocess.run(["git", "grep", "-lF", "--", fact, head, "--", "docs", "README.md"],
                           capture_output=True, text=True, check=False)
        files = sorted({line.split(":", 1)[1] for line in r.stdout.splitlines()})
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
        own = added_lines(git("show", "--unified=0", "--format=", sha))
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
    for _, _, text in in_tests:
        if EXEMPT in text:
            continue
        for k, rx in (*HARD_PATTERNS.items(), *WARN_PATTERNS.items()):
            counts[k] += bool(rx.search(text))
    v = ("HARD" if any(counts[k] for k in HARD_PATTERNS)
         else "WARN" if any(counts[k] for k in WARN_PATTERNS) else "OK")
    rows.append(("patterns", " ".join(f"{k} {c}" for k, c in counts.items()), v))

    # markers
    unmarked = 0
    by_file: dict[str, list[tuple[int, str]]] = {}
    for p, n, t in in_tests:
        by_file.setdefault(p, []).append((n, t))
    for lines in by_file.values():
        for i, (_, t) in enumerate(lines):
            if not TEST_DEF.match(t):
                continue
            body = []
            for _, b in lines[i + 1:]:
                if b.strip() and not b[0].isspace():     # the next top-level statement ends it
                    break
                body.append(b)
            text = "\n".join(body)
            slow_work = "venv" in text or "pip install" in text or ("while" in text and "monotonic" in text)
            above = []
            for _, a in reversed(lines[:i]):
                if not a.strip().startswith("@"):
                    break
                above.append(a)
            unmarked += slow_work and not any("mark.slow" in a for a in above)
    rows.append(("markers", f"{unmarked} without slow", "WARN" if unmarked else "OK"))

    # attribution
    authors = sorted({a for _, a, _ in commits})
    trailers = sum(len(TRAILER.findall(msg)) for _, _, msg in commits)
    texts = [msg for _, _, msg in commits] + [t for _, _, t in added]
    words = sum(1 for t in texts for name in ATTRIBUTION if name.lower() in t.lower())
    words += sum(len(WORDS.findall(WORD_EXEMPT.sub("", t))) for t in texts)
    v = "HARD" if trailers or any(a != "makrohard" for a in authors) else "WARN" if words else "OK"
    rows.append(("attribution", f"authors {','.join(authors) or '-'}; trailers {trailers}; words {words}", v))

    # modules
    table, base_table = (budgets(show(head, BUDGET_FILE)), budgets(show(base, BUDGET_FILE))) if BUDGET_FILE else (None, None)
    before = base_table or {}
    if table is None:                      # a table <base> had cannot vanish in the range it would gate
        rows.append(("modules", f"{BUDGET_FILE} has no budget table at <head>" if base_table is not None
                     else "N/A (budget table absent)", "HARD" if base_table is not None else "OK"))
        return rows
    parts, over, net, excused, raised_bad, raised_ok, splits, sizes = [], False, 0, 0, [], set(), {}, {}
    net_public, excused_public = 0, 0
    head_budget_lines = (show(head, BUDGET_FILE) or "").splitlines()
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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
        print(f"git failed: {' '.join(exc.cmd)}: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    except ProfileError as exc:
        print(f".houserules.toml: {exc}", file=sys.stderr)
        return 2
    hard = sum(v == "HARD" for _, _, v in rows)
    warn = sum(v == "WARN" for _, _, v in rows)
    print(f"HOUSE RULES KEPT  {args.range}")
    for name, text, verdict in rows:
        print(f"{name:<12} {text}   {verdict}")
    print(f"{'VERDICT':<12} {'FAIL' if hard else 'PASS'}  (hard {hard}, warn {warn})")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
