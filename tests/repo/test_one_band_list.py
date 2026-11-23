"""F34 C1 (K3): the band list lives in lhpc/core/known_bands.py only. No code keeps its own copy: no ("433", "868")
tuple (either quote, either order, split across lines), no 433|868 regex alternation, no `==` / `!=` test of a band
on either side. Comments are dropped (tokenize) and docstrings (ast); ordinary string literals stay in the scan; a
statement is scanned whole (its logical line), so a tuple split across lines is one text. A value KEYED by band is not a copy of the list: the
exemptions below name such places by file and symbol (docs/adding-a-band.md lists them as places to extend)."""
import ast
import io
import re
import tokenize

import repo_paths

ROOT = repo_paths.REPO
SCANNED = ("lhpc", "demo/lhpc_demo", "testlab/lhpc_testlab")
SKIP = ("lhpc/data/meshcore_host/",)                 # a separate package (pyproject [tool.ruff] extend-exclude)
_Q = "[\"']"                                         # either quote
PATTERNS = (re.compile(rf"{_Q}433{_Q}\s*,\s*{_Q}868{_Q}|{_Q}868{_Q}\s*,\s*{_Q}433{_Q}"),
            re.compile(r"433\|868|868\|433"),
            re.compile(rf"[=!]=\s*{_Q}(433|868){_Q}|{_Q}(433|868){_Q}\s*[=!]="))
EXEMPT = {
    # the dual-board SPI rule (its 433 and 868 modules share SPI bus 0, SPI_SHARED_WARNING): a hardware fact, not the
    # band list
    "lhpc/core/service_lifecycle_ops.py": {"_spi_shared_warning"},
    # the demo's simulated daemon: values per band ("every non-433 band is 868" there)
    "demo/lhpc_demo/daemon_sim.py": {"_live_rssi", "radio", "channel"},
}


def _docstring_lines(tree) -> set:
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                lines |= set(range(first.lineno, first.end_lineno + 1))
    return lines


def _symbols(tree) -> list:
    """(first line, last line, name) of every def/class and every named assignment, at any depth."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((node.lineno, node.end_lineno, node.name))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(t, ast.Name):
                    out.append((node.lineno, node.end_lineno, t.id))
    return out


def code_lines(src: str) -> dict:
    """{first line of a statement: its code without comments or docstrings}, the tokens of the whole logical line
    joined by spaces (a tuple split across lines is one text)."""
    doc = _docstring_lines(ast.parse(src))
    lines: dict = {}
    first = None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NEWLINE:
            first = None
            continue
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
            continue
        if tok.type == tokenize.STRING and tok.start[0] in doc:
            continue
        first = tok.start[0] if first is None else first
        lines.setdefault(first, []).append(tok.string)
    return {n: " ".join(t) for n, t in lines.items()}


def copies(rel: str, src: str) -> list:
    tree = ast.parse(src)
    syms, exempt = _symbols(tree), EXEMPT.get(rel, set())
    hits = []
    for n, text in sorted(code_lines(src).items()):
        if any(p.search(text) for p in PATTERNS):
            names = {name for lo, hi, name in syms if lo <= n <= hi}
            if not names & exempt:
                hits.append(f"{rel}:{n}: {text}")
    return hits


def _files():
    for top in SCANNED:
        for p in sorted((ROOT / top).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if rel != "lhpc/core/known_bands.py" and not rel.startswith(SKIP) and not p.name.startswith("test_"):
                yield rel, p.read_text(encoding="utf-8")


def test_no_code_keeps_its_own_band_list():
    files = list(_files())
    assert len(files) > 100, len(files)                       # floor: an empty scan cannot pass
    hits = [h for rel, src in files for h in copies(rel, src)]
    assert not hits, "\n".join(hits)


def test_every_exemption_still_covers_a_band_literal():
    # an exemption whose symbol no longer holds a band literal is stale: drop it
    for rel, names in EXEMPT.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree, lines = ast.parse(src), code_lines(src)
        for name in names:
            spans = [(lo, hi) for lo, hi, n in _symbols(tree) if n == name]
            assert spans and any(p.search(lines.get(i, "")) for lo, hi in spans for i in range(lo, hi + 1)
                                 for p in PATTERNS), (rel, name)


def test_the_scan_sees_a_copy_and_skips_comments_and_docstrings():
    # the control: each pattern in code is found; the same text in a comment or a docstring is not; a plain string
    # literal (not a docstring) is still scanned
    src = ('"""A docstring: ("433", "868")."""\n'
           'X = ("433", "868")   # comment ("433", "868")\n'
           'RE = r"(433|868)"\n'
           'def f(b):\n'
           '    """433|868"""\n'
           '    return b == "868"\n'
           'MSG = "a plain string 433|868"\n'
           "Y = ('868', '433')\n"
           'Z = ("433",\n'
           '     "868")\n'
           'def g(b):\n'
           '    return b != "433" or "868" == b\n'
           'RE2 = r"868|433"\n')
    assert [h.split(":")[1] for h in copies("x.py", src)] == ["2", "3", "6", "7", "8", "9", "12", "13"]
