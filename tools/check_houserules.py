#!/usr/bin/env python3
"""Print the HOUSE RULES KEPT block for one item's commit range. Reads git, writes nothing.

    check_houserules.py <base>..<head> [--fact LITERAL]...

One line per rule (changelog, docs, tests, patterns, markers, attribution, modules), each ending in
OK, WARN or HARD, then `VERDICT PASS|FAIL`. Exit 1 when a line is HARD, 2 on a usage or git error.
The exact conditions: tests/README.md rule 13 and the plan of this tool. WARN lines are judged by
the reviewer; HARD lines block.
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys

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
# Assembled from fragments so that no committed file spells the names it looks for.
ATTRIBUTION = ("Clau" + "de", "Chat" + "GPT", "Anthro" + "pic", "Generated " + "with")
TRAILER = re.compile("^" + "Co-" + "Authored-By:", re.IGNORECASE | re.MULTILINE)
WORDS = re.compile(r"\b(" + "ag" + "ent" + "|" + "ses" + "sion" + r")\b", re.IGNORECASE)
# Product tokens that contain those words and are not attribution: an HTTP header, pytest's fixture
# scope, the suite's marker and the POSIX process term.
WORD_EXEMPT = re.compile(r"user[-_]" + "ag" + "ent" + r'|scope="' + "ses" + 'sion"|needs_' + "ses" + "sion"
                         + r"|\b" + "ses" + r"sion leader", re.IGNORECASE)
HARD_PATTERNS = {"getsource": re.compile(r"inspect\.getsource\("),
                 "assert-True": re.compile(r"^\s*assert True\b")}
WARN_PATTERNS = {"source-read": re.compile(r"""(read_text|open)\(.*["']lhpc/[^"']+\.(py|js|css)["']"""),
                 "sleep": re.compile(r"time\.sleep\("),
                 "startswith": re.compile(r"assert .*\.startswith\("),
                 "network": re.compile(r"\b(requests\.|urllib\.request|socket\.create_connection)")}
EXEMPT = "# rule-1 exception"
RED_BEFORE = re.compile(r"red[- ]before", re.IGNORECASE)
RAISE_REASON = re.compile(r"#\s*\S+:\s*\S")
# A new family file whose budget row says `# <item>: split from <file>`: the moved code cancels out in the
# family net, which may then grow only by the new file's header and, per split, the facade's import and bases.
SPLIT_FROM = re.compile(r"#\s*\S+:\s*split from (\S+\.py)\b")
FACADE = "services.py"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def show(rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else None


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
    return path.rsplit("/", 1)[-1].startswith("test_") and path.endswith(".py")


def layer_of(path: str) -> str | None:
    return next((layer for prefix, layer in LAYERS if path.startswith(prefix)), None)


def measure(source: str) -> tuple[int, int]:
    tree = ast.parse(source)
    public = sum(1 for cls in tree.body if isinstance(cls, ast.ClassDef) for node in cls.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and not node.name.startswith("_"))
    return len(source.splitlines()), public


def first_class(source: str) -> ast.ClassDef | None:
    return next((n for n in ast.parse(source).body if isinstance(n, ast.ClassDef)), None)


def header_lines(source: str) -> int:
    """The lines up to and including the module's first `class` line: docstring, imports, constants."""
    first = first_class(source)
    return len(source.splitlines()) if first is None else first.lineno


def facade_edits(base: str, head: str, classes: dict[str, str]) -> int:
    """The facade lines the splits add: per split class (`{module: class}`) whose name the base revision does
    not bind at top level, its one-line `from .<module> import <class>`, and at most one more line in the
    bases span (up to the last base or keyword) of a class that newly inherits it."""
    tree, old_tree = ast.parse(head), ast.parse(base)
    bound = {a.asname or a.name for n in old_tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    bound |= {n.name for n in old_tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    bound |= {t.id for n in old_tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    imported = {n.module for n in tree.body if isinstance(n, ast.ImportFrom) and n.level == 1
                and n.lineno == n.end_lineno and [a.name for a in n.names] == [classes.get(n.module)]
                and classes[n.module] not in bound}
    lines = len(imported)
    old = {n.name: n for n in old_tree.body if isinstance(n, ast.ClassDef)}

    def base_names(node: ast.ClassDef) -> set:
        return {getattr(b, "id", None) for b in node.bases}

    def bases_span(node: ast.ClassDef) -> int:
        return max((b.end_lineno for b in (*node.bases, *node.keywords)), default=node.lineno) - node.lineno

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in old:
            added = sum(classes[m] in base_names(node) - base_names(old[node.name]) for m in imported)
            grown = bases_span(node) - bases_span(old[node.name])
            lines += min(max(0, grown), added)
    return lines


def budgets(source: str | None) -> dict | None:
    if source is None:
        return None
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BUDGETS" for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def check(base: str, head: str, facts: list[str], correction: bool = False) -> list[tuple[str, str, str]]:
    rng = f"{base}..{head}"
    added = added_lines(git("diff", "--unified=0", rng))
    status = [line.split("\t") for line in git("diff", "--name-status", rng).splitlines()]
    new_files = {p[-1] for p in status if p[0].startswith("A")}
    changed = {p[-1] for p in status}
    commits = [c.strip("\n").split("\x00", 2)
               for c in git("log", "--format=%H%x00%an%x00%B%x1e", rng).split("\x1e") if c.strip()]
    rows = []

    # changelog
    head_cl = (show(head, "CHANGELOG.md") or "").splitlines()
    heads = [i + 1 for i, line in enumerate(head_cl) if line.startswith("## ")]
    second = heads[1] if len(heads) > 1 else len(head_cl) + 1
    top = (second - heads[0] - 1) if heads else 0
    bullets, below, active, last = [], False, False, None
    for path, n, text in added:
        if path != "CHANGELOG.md":
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
    long_bullet = any(b > 3 for b in bullets)
    # `--correction`: an item that only corrects a released section says so, and its bullets
    # below the top section are shown (WARN), not refused.
    v = ("HARD" if len(bullets) > 2 or long_bullet or (below and not correction)
         else "WARN" if below else "OK")
    rows.append(("changelog", f"+{len(bullets)} bullets (limit 2, each ≤ 3 lines; longest "
                              f"{max(bullets, default=0)}), top section {top} lines", v))

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
        if any(is_test_file(p) and re.match(r"(async )?def test_", t) for p, _, t in own):
            with_tests += 1
            stated += bool(RED_BEFORE.search(msg))
    n_new = sum(1 for p, _, t in added if is_test_file(p) and re.match(r"(async )?def test_", t))
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
            if not re.match(r"(async )?def test_", t):
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
    table, before = budgets(show(head, BUDGET_FILE)), budgets(show(base, BUDGET_FILE)) or {}
    if table is None:
        rows.append(("modules", "N/A (budget table absent)", "OK"))
        return rows
    parts, over, net, excused, raised_bad, raised_ok, splits, sizes = [], False, 0, 0, [], set(), {}, {}
    split_bad = []
    net_public, excused_public = 0, 0
    head_budget_lines = (show(head, BUDGET_FILE) or "").splitlines()
    for name, value in table.items():
        if name in before and any(v > b for v, b in zip(value, before[name], strict=True)):
            line = next((ln for ln in head_budget_lines if f'"{name}"' in ln), "")
            (raised_ok.add(name) if RAISE_REASON.search(line) else raised_bad.append(name))
        elif name not in before:
            line = next((ln for ln in head_budget_lines if f'"{name}"' in ln), "")
            if m := SPLIT_FROM.search(line):
                splits[name] = m.group(1)
    for name in sorted(table):
        path = FAMILY_DIR + name
        if path not in changed:
            continue
        a = measure(show(base, path) or "")
        b = measure(show(head, path) or "")
        sizes[name] = (a[0], b[0])
        net += b[0] - a[0]
        excused += max(0, b[0] - a[0]) if name in raised_ok else 0
        net_public += b[1] - a[1]
        excused_public += max(0, b[1] - a[1]) if name in raised_ok else 0
        over = over or b[0] > table[name][0] or b[1] > table[name][1]
        parts.append(f"{name} {a[0]}->{b[0]} lines (budget {table[name][0]}), "
                     f"{a[1]}->{b[1]} public (budget {table[name][1]})")
    for name, source in splits.items():
        # A split counts only when the file it names is a family file that shrank in this range.
        if name in sizes and source in sizes and sizes[source][1] < sizes[source][0]:
            excused += header_lines(show(head, FAMILY_DIR + name) or "")
        else:
            split_bad.append(name)
    # The facade excuse covers only the edits that wire a split in, never other growth of the facade.
    classes = {name[:-3]: c.name for name in splits
               if name not in split_bad and (c := first_class(show(head, FAMILY_DIR + name) or ""))}
    if classes and FACADE in sizes:
        excused += facade_edits(show(base, FAMILY_DIR + FACADE) or "", show(head, FAMILY_DIR + FACADE) or "", classes)
    # Only a raised file's own growth is excused, for lines and for public methods alike.
    grown_unexcused = net - excused > 0 or net_public - excused_public > 0
    v = ("HARD" if over or grown_unexcused or raised_bad or split_bad
         else "WARN" if net > 0 or net_public > 0 else "OK")
    rows.append(("modules", "; ".join(parts or ["no family file touched"])
                 + f"; family net {net:+d} lines, {net_public:+d} public" + (f"; raised without reason {','.join(raised_bad)}" if raised_bad else "")
                 + (f"; split from a file that did not shrink {','.join(split_bad)}" if split_bad else ""), v))
    return rows


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
    try:
        rows = check(base, head, args.fact, args.correction)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {' '.join(exc.cmd)}: {exc.stderr.strip()}", file=sys.stderr)
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
