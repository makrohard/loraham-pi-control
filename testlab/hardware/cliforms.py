"""Every lhpc call of the hardware runners, parsed by the CLI's own argparse at a head.

usage: python -I testlab/hardware/cliforms.py <file>...   (TSV on stdout; the parser is this checkout's)
A `<name>.dry.txt` file (a runner's dry output, every remote command printed with its variables expanded) is read from
its `[dry] <host> > <file>: <command>` lines; any other file (a .md checklist) from its code blocks. A call is
`python -m lhpc ARGS` / `.../bin/lhpc ARGS` (a scratch runtime at the topic head: CHECKED) or a bare `lhpc ARGS`
(the box's installed CLI: INSTALLED, listed, not parsed at the head). ARGS end at the first shell operator.
Verdicts: OK; UNKNOWN (a verb, sub-verb, flag or choice the head does not know); REVIEW (another argparse error, or
a `$` left unexpanded: read by hand); INSTALLED. Reads its inputs only.
"""
import contextlib
import io
import pathlib
import re
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))   # this checkout's lhpc: `python -I` drops PYTHONPATH
from lhpc.adapters.cli.main import build_parser

DRY = re.compile(r"^\s*\[dry\] ([^:]+): (.*)$")
CALL = re.compile(r"(-m lhpc|/bin/lhpc|(?<![\w/.-])lhpc)\s+(?=[a-z_-])")
OPS = set(";&|<>()`{}")
REDIRECT_FD = re.compile(r"(?<=\s)\d+(?=[<>])")


def lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.readlines()
    if path.endswith(".dry.txt"):
        for n, line in enumerate(text, 1):
            m = DRY.match(line)
            if m:
                yield n, m.group(1), m.group(2)
        return
    code = False
    for n, line in enumerate(text, 1):
        if line.lstrip().startswith("```"):
            code = not code
        elif code and not line.lstrip().startswith("#"):
            yield n, "md", line.rstrip("\n")


def args_of(rest):
    rest = REDIRECT_FD.sub("", rest)            # the fd of a redirect (`2>/dev/null`): a digit directly before < or >
    lex = shlex.shlex(rest, posix=True, punctuation_chars=True)
    lex.wordchars += "@$:,%+"
    toks = []
    try:
        toks.extend(lex)
    except ValueError:
        pass
    out = []
    for t in toks:
        if set(t) <= OPS:
            break
        out.append(t)
    return out


def in_quote(text, start, end):
    """The call's text: up to the closing quote when the call sits inside a '...' or "..." of the line."""
    q, i = "", 0
    while i < start:
        c = text[i]
        if c == "\\" and q != "'":
            i += 2
            continue
        if q == "" and c in "'\"":
            q = c
        elif c == q:
            q = ""
        i += 1
    rest = text[end:]
    if not q:
        return rest
    j = 0
    while j < len(rest):
        if rest[j] == "\\" and q == '"':
            j += 2
            continue
        if rest[j] == q:
            return rest[:j]
        j += 1
    return rest


ZERO_REAL = "/home/lhpc/loraham-pi-control/venv/lhpc/bin/"     # E1: a box's installed CLI by its absolute path, by design


def parse(argv):
    """The verdict lhpc's own _run gives a call, without running it: `meshtastic[@rN] ...` is a passthrough to the
    managed Meshtastic CLI (intercepted before the parser, main.py _run); `--band` is refused by name; else
    parse_known_args, trailing positionals folded into `config`'s rest. "" = accepted, else the error line."""
    if isinstance(argv, str):
        argv = shlex.split(argv)
    if argv and argv[0].partition("@")[0] == "meshtastic":
        return ""
    if any(t == "--band" or t.startswith("--band=") for t in argv):
        return "--band is refused by name"
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        try:
            args, extra = build_parser().parse_known_args(argv)
        except SystemExit as exc:
            if exc.code in (0, None):
                return ""
            text = err.getvalue().strip()
            return text.splitlines()[-1] if text else f"exit {exc.code}"
    if extra and not (getattr(args, "command", "") == "config" and all(not t.startswith("-") for t in extra)):
        return "lhpc: error: unrecognized arguments: " + " ".join(extra)
    return ""


def main(paths):
    print("file\tline\thost\tkind\tverdict\tcall\tdetail")
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        for n, host, text in lines(path):
            for m in CALL.finditer(text):
                argv = args_of(in_quote(text, m.start(), m.end()))
                if not argv:
                    continue
                call = "lhpc " + " ".join(argv)
                if ZERO_REAL in text[max(0, m.start() - 60):m.end()]:
                    print(f"{name}\t{n}\t{host}\tzero-real\tE1\t{call}\tthe box's installed CLI (by design)")
                    continue
                if m.group(1) == "lhpc" and host != "md":
                    print(f"{name}\t{n}\t{host}\tinstalled\tINSTALLED\t{call}\t")
                    continue
                if any("$" in a for a in argv):
                    print(f"{name}\t{n}\t{host}\thead\tREVIEW\t{call}\ta $ is left unexpanded")
                    continue
                why = parse(argv)
                if not why:
                    v = "OK"
                elif re.search(r"invalid choice|unrecognized arguments", why):
                    v = "UNKNOWN"
                else:
                    v = "REVIEW"
                print(f"{name}\t{n}\t{host}\thead\t{v}\t{call}\t{why}")


if __name__ == "__main__":
    main(sys.argv[1:])
