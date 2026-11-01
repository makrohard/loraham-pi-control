#!/usr/bin/env python3
"""Static checks of .github/workflows/testlab-x86.yml: the properties that keep it an amd64,
non-gating, least-privilege workflow. Reads files, writes nothing, needs no network. Stdlib only;
PyYAML, when importable, is a second, independent parse (A12), otherwise A12 says NOT RUN.

    check_workflow.py [--workflow PATH] [--reference PATH] [--ci PATH]

Defaults: the workflow, testlab.yml (the reference whose `uses:` lines are copied) and ci.yml of this
checkout. One line per assertion, PASS or FAIL with the reason, then VERDICT. Exit 0 when every
assertion passes, 1 when one fails, 2 when a file cannot be read or the workflow cannot be parsed.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNNER = "ubuntu-24.04"
PREFIX = "testlab-x86"
SHA_PIN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")
BLOCK = {"|", "|-", "|+", ">", ">-", ">+"}
# The x86_64 guard every job must open with (A13), line by line after the strip.
GUARD = ('m=$(uname -m) || { echo "::error::uname -m failed"; exit 1; }',
         'if [ "$m" != x86_64 ]; then',
         'echo "::error::this workflow runs on x86_64 only; this runner is $m"',
         "exit 1",
         "fi")


class ParseError(Exception):
    pass


# ---- a reader for the YAML subset GitHub workflows of this repository use ------------------------
# Block mappings and sequences, plain or quoted scalars, flow lists of scalars, block scalars and
# comments. Anything else (anchors, flow mappings, multi-line plain scalars) is a ParseError.

def _strip_comment(s: str) -> str:
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _scalar(s: str):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    if s[:1] in ("{", "&", "*", "!"):
        raise ParseError(f"unsupported scalar {s!r}")
    return s


def _tokens(text: str) -> list:
    """(indent, is_item, key or None, value) per logical line; a block scalar is one value."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ParseError(f"line {i}: a tab in the indentation")
        body = _strip_comment(raw)
        if not body.strip():
            continue
        indent = len(body) - len(body.lstrip(" "))
        body = body.strip()
        item = body == "-" or body.startswith("- ")
        if item:
            body = body[1:].strip()
            if not body:
                out.append((indent, True, None, None))
                continue
            out.append((indent, True, None, None))
            indent += 2
        m = re.match(r"([^\s'\"][^:]*?|'[^']*'|\"[^\"]*\"):(?:\s+(.*))?$", body)
        if not m:
            if item:
                out[-1] = (indent - 2, True, None, _scalar(body))
                continue
            raise ParseError(f"line {i}: not a key: {raw.strip()!r}")
        key, value = _scalar(m.group(1)), m.group(2)
        if value in BLOCK:
            block = []
            while i < len(lines) and (not lines[i].strip() or
                                      len(lines[i]) - len(lines[i].lstrip(" ")) > indent):
                block.append(lines[i])
                i += 1
            out.append((indent, False, key, "\n".join(block)))
        else:
            out.append((indent, False, key, None if value is None else _scalar(value)))
    return out


def _node(toks: list, k: int, indent: int):
    if k < len(toks) and toks[k][0] == indent and toks[k][1]:
        seq = []
        while k < len(toks) and toks[k][0] == indent and toks[k][1]:
            _, _, _, value = toks[k]
            k += 1
            if value is not None:
                seq.append(value)
            elif k < len(toks) and toks[k][0] > indent:
                child, k = _node(toks, k, toks[k][0])
                seq.append(child)
            else:
                seq.append(None)
        return seq, k
    mapping = {}
    while k < len(toks) and toks[k][0] == indent and not toks[k][1]:
        _, _, key, value = toks[k]
        if key in mapping:
            raise ParseError(f"duplicate key {key!r}")
        k += 1
        if value is None and k < len(toks) and toks[k][0] > indent:
            value, k = _node(toks, k, toks[k][0])
        mapping[key] = value
    return mapping, k


def parse(text: str) -> dict:
    toks = _tokens(text)
    if not toks:
        raise ParseError("empty document")
    doc, k = _node(toks, 0, toks[0][0])
    if k != len(toks) or type(doc) is not dict:
        raise ParseError(f"unparsed from token {k}: {toks[k] if k < len(toks) else '-'}")
    return doc


# ---- the assertions ------------------------------------------------------------------------------

def _uses_lines(text: str) -> list:
    return [line.strip().removeprefix("- ").strip() for line in text.split("\n")
            if re.match(r"\s*(-\s+)?uses:", line)]


def _steps(w: dict) -> list:
    return [s for job in w["jobs"].values() if type(job) is dict
            for s in (job.get("steps") or []) if type(s) is dict]


def _artifact_names(w: dict) -> list:
    return [(s.get("with") or {}).get("name") for s in _steps(w)
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")]


def checks(text: str, w: dict, ref_text: str, ref: dict, ci: dict) -> list:
    jobs = w.get("jobs")
    if type(jobs) is not dict or not jobs:
        return [("A0", False, "no jobs mapping")]
    rows = []

    labels = {j: job.get("runs-on") if type(job) is dict else None for j, job in jobs.items()}
    bad = {j: v for j, v in labels.items() if v != RUNNER}
    rows.append(("A1 runs-on is ubuntu-24.04 in every job", not bad, f"wrong: {bad}" if bad else str(labels)))

    arm = [n for n, line in enumerate(text.split("\n"), 1) if "-arm" in line.lower()]
    rows.append(("A2 no -arm label anywhere", not arm, f"lines {arm}" if arm else "none"))

    on = w.get("on")
    want_on = (type(on) is dict and set(on) == {"push", "workflow_dispatch"}
               and type(on["push"]) is dict and set(on["push"]) == {"branches"}
               and on["push"]["branches"] == ["cons/**"]
               and (on["workflow_dispatch"] is None or type(on["workflow_dispatch"]) is dict))
    rows.append(("A3 triggers are exactly workflow_dispatch + push on cons/**", want_on, repr(on)))

    sec = [n for n, line in enumerate(text.split("\n"), 1) if re.search(r"secrets\s*[.\[]", line)]
    rows.append(("A4 no secrets reference", not sec, f"lines {sec}" if sec else "none"))

    uses = _uses_lines(text)
    ref_uses = set(_uses_lines(ref_text))
    unpinned = [u for u in uses if not SHA_PIN.fullmatch(_strip_comment(u).removeprefix("uses:").strip())]
    foreign = [u for u in uses if u not in ref_uses]
    rows.append(("A5 every uses: pinned to a full sha and copied from testlab.yml",
                 bool(uses) and not unpinned and not foreign,
                 f"{len(uses)} uses; unpinned {unpinned}; not in the reference {foreign}"))

    conc = w.get("concurrency")
    group = conc.get("group") if type(conc) is dict else None
    ref_group = (ref.get("concurrency") or {}).get("group")
    ok = type(group) is str and group.startswith(PREFIX + "-") and group != ref_group
    rows.append(("A6 concurrency group starts with testlab-x86-", ok, repr(group)))

    names = _artifact_names(w)
    ok = bool(names) and all(type(n) is str and n.startswith(PREFIX + "-") for n in names)
    rows.append(("A7 every artifact name starts with testlab-x86-", ok, repr(names)))

    calls = [n for n, line in enumerate(text.split("\n"), 1) if re.search(r"workflow_(call|run)", line)]
    needs = {j: job.get("needs") for j, job in jobs.items() if type(job) is dict and "needs" in job}
    flat = {j: [v] if type(v) is str else v for j, v in needs.items()}
    foreign_needs = {j: v for j, v in flat.items() if type(v) is not list or not set(v) <= set(jobs)}
    job_uses = [j for j, job in jobs.items() if type(job) is dict and "uses" in job]
    ok = not calls and not foreign_needs and not job_uses
    rows.append(("A8 no workflow_call/workflow_run, needs only on jobs of this file, no job-level uses", ok,
                 f"call/run lines {calls}; needs {needs}; job-level uses {job_uses}"))

    job_perms = [j for j, job in jobs.items() if type(job) is dict and "permissions" in job]
    ok = w.get("permissions") == {"contents": "read"} and not job_perms
    rows.append(("A9 permissions are contents: read only", ok,
                 f"top {w.get('permissions')!r}; job-level {job_perms}"))

    taken = set(ref.get("jobs") or {}) | set(ci.get("jobs") or {})
    bad = [j for j, job in jobs.items()
           if not j.endswith("-x86") or type(job) is not dict or job.get("name") != j or j in taken]
    rows.append(("A10 job ids end in -x86, name equals id, no id of testlab.yml or ci.yml", not bad,
                 f"bad {bad}" if bad else str(sorted(jobs))))

    refs = sorted(set(re.findall(r"testlab/x86/([A-Za-z0-9_.-]+)", text)))
    missing = [r for r in refs if not (HERE / r).is_file()]
    rows.append(("A11 every testlab/x86 file the workflow names exists", bool(refs) and not missing,
                 f"named {refs}; missing {missing}"))

    # A guard with an `if:`, `continue-on-error` or `uses` could be skipped or ignored: only name + run.
    unguarded = []
    for j, job in jobs.items():
        steps = job.get("steps") if type(job) is dict else None
        first = steps[0] if type(steps) is list and steps else None
        body = [line.strip() for line in first.get("run", "").split("\n") if line.strip()] \
            if type(first) is dict and type(first.get("run")) is str else []
        if type(first) is not dict or set(first) != {"name", "run"} or tuple(body[:len(GUARD)]) != GUARD \
                or "continue-on-error" in job:
            unguarded.append(j)
    rows.append(("A13 every job's first step is the x86_64 guard (before checkout and any acquisition)",
                 not unguarded, f"unguarded {unguarded}" if unguarded else f"{len(jobs)} jobs guarded"))
    return rows


def yaml_check(text: str, w: dict) -> tuple:
    """A12: PyYAML's parse agrees with this reader on every asserted field; ('NOT RUN', why) without it."""
    try:
        import yaml
    except ImportError:
        return "NOT RUN", "PyYAML is not importable: the second parse was not run"
    try:
        y = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return "FAIL", f"PyYAML cannot parse it: {' '.join(str(exc).split())[:200]}"
    if type(y) is not dict or type(y.get("jobs")) is not dict:
        return "FAIL", "PyYAML: not a mapping with jobs"
    on = y.get("on", y.get(True))          # YAML 1.1 reads the bare key on as True
    diffs = []
    if type(on) is not dict or set(on) != set(w["on"]) or on.get("push") != w["on"].get("push"):
        diffs.append(f"on {on!r}")
    if y.get("permissions") != w.get("permissions"):
        diffs.append(f"permissions {y.get('permissions')!r}")
    if (y.get("concurrency") or {}).get("group") != (w.get("concurrency") or {}).get("group"):
        diffs.append("concurrency group")
    if {j: (v or {}).get("runs-on") for j, v in y["jobs"].items()} != \
            {j: (v or {}).get("runs-on") for j, v in w["jobs"].items()}:
        diffs.append("runs-on")
    if sorted(map(str, _artifact_names(y))) != sorted(map(str, _artifact_names(w))):
        diffs.append("artifact names")
    if [s.get("uses") for s in _steps(y) if "uses" in s] != [_strip_comment(s["uses"]) for s in _steps(w) if "uses" in s]:
        diffs.append("uses")
    return ("FAIL", "differs: " + ", ".join(diffs)) if diffs else ("PASS", "agrees on on, permissions, "
                                                                     "concurrency, runs-on, artifacts, uses")


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workflow", type=Path, default=ROOT / ".github/workflows/testlab-x86.yml")
    ap.add_argument("--reference", type=Path, default=ROOT / ".github/workflows/testlab.yml")
    ap.add_argument("--ci", type=Path, default=ROOT / ".github/workflows/ci.yml")
    args = ap.parse_args(argv)
    try:
        text, ref_text, ci_text = (p.read_text(encoding="utf-8") for p in (args.workflow, args.reference, args.ci))
        w, ref, ci = parse(text), parse(ref_text), parse(ci_text)
    except (OSError, UnicodeDecodeError, ParseError) as exc:
        print(f"UNKNOWN  cannot check: {type(exc).__name__}: {exc}")
        print("VERDICT  UNKNOWN")
        return 2
    rows = checks(text, w, ref_text, ref, ci)
    for name, ok, why in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  [{why}]")
    state, why = yaml_check(text, w)
    print(f"{state}  A12 PyYAML parse agrees  [{why}]")
    failed = sum(not ok for _, ok, _ in rows) + (state == "FAIL")
    print(f"VERDICT  {'FAIL' if failed else 'PASS'}  ({len(rows) + 1} assertions, {failed} failed"
          + (", A12 not run" if state == "NOT RUN" else "") + ")")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
