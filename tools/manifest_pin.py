#!/usr/bin/env python3
"""Emit the shared managed-source pin contract for the CI cross-repository pin-validation job (and
assert the consumers agree). Manifest-only, no git, no network.

    manifest_pin.py --list                 every pinned source path, one per line
    manifest_pin.py [--shell] <source-path>          e.g.  src/meshcom-qemu-raspi

`--list` is what the CI job iterates: the validated set is DERIVED from the manifest, so a newly
pinned repo is covered the moment it is added (a hardcoded path silently covers only itself).

Prints shell-eval'able assignments and exits 0:

    CONSUMERS=<n>
    REMOTE=<git remote>
    BRANCH=<branch>
    PIN=<full 40-hex commit>
    TAG=<pin_tag>
    SCRIPTS=<space-separated repo-relative scripts referenced by build_steps/run/test/build/pre>

`--shell` quotes every value so the whole block is safe to `eval` (SCRIPTS is space-separated and
may be empty — a pinned repo that references no scripts is still validated for pin + ancestry).

Exits nonzero if there are no pinned consumers of the path, the consumers disagree on
pin_commit/pin_tag/remote/branch, or the pin is not a full 40-hex SHA.
"""
import pathlib
import re
import shlex
import sys
import tomllib

_SHA = re.compile(r"^[0-9a-f]{40}$")
_TOKEN = re.compile(r"^(?:scripts|tools|bin)/[\w./-]+\.(?:sh|py)$")
_EMBED = re.compile(r"(?:^|[\s'\"=])((?:scripts|tools|bin)/[\w./-]+\.(?:sh|py))")


def _load_manifest() -> dict:
    root = pathlib.Path(__file__).resolve().parents[1]
    return tomllib.loads((root / "lhpc" / "data" / "manifest.example.toml").read_text())


def pinned_paths(m: dict) -> list:
    """Every distinct source path carrying a pin_commit, in manifest order."""
    out = []
    for st in m["stack"]:
        for c in st.get("component", []):
            src = c.get("source") or {}
            p = src.get("path")
            if p and src.get("pin_commit") and p not in out:
                out.append(p)
    return out


def main(argv) -> int:
    args = list(argv[1:])
    if args and args[0] == "--list":
        if len(args) != 1:
            print("usage: manifest_pin.py --list", file=sys.stderr)
            return 2
        for p in pinned_paths(_load_manifest()):
            print(p)
        return 0
    quote = False
    if args and args[0] == "--shell":
        quote = True
        args = args[1:]
    if len(args) != 1:
        print("usage: manifest_pin.py [--shell] <source-path> | --list", file=sys.stderr)
        return 2
    want = args[0]
    m = _load_manifest()
    consumers = []
    scripts: set = set()
    for st in m["stack"]:
        for c in st.get("component", []):
            src = c.get("source") or {}
            if src.get("path") != want or not src.get("pin_commit"):
                continue
            consumers.append((c["id"], src["pin_commit"], src.get("pin_tag", ""),
                              src.get("remote", ""), src.get("branch", "main")))
            for step in c.get("build_steps", []):
                for tok in step.get("argv", []):
                    if _TOKEN.match(str(tok)):
                        scripts.add(str(tok))
            for k in ("run", "test", "build", "pre"):
                v = c.get(k)
                if isinstance(v, str):
                    scripts.update(_EMBED.findall(v))
    if not consumers:
        print(f"no pinned consumers of {want}", file=sys.stderr)
        return 1
    pins = {c[1] for c in consumers}
    tags = {c[2] for c in consumers}
    remotes = {c[3] for c in consumers}
    branches = {c[4] for c in consumers}
    if len(pins) != 1 or len(tags) != 1:
        print(f"split pin across consumers of {want}: {consumers}", file=sys.stderr)
        return 1
    if len(remotes) != 1 or len(branches) != 1:
        print(f"split remote/branch across consumers of {want}: {consumers}", file=sys.stderr)
        return 1
    pin = next(iter(pins))
    if not _SHA.match(pin):
        print(f"pin is not a full 40-hex SHA: {pin!r}", file=sys.stderr)
        return 1
    q = shlex.quote if quote else (lambda s: s)
    print(f"CONSUMERS={q(str(len(consumers)))}")
    print(f"REMOTE={q(next(iter(remotes)))}")
    print(f"BRANCH={q(next(iter(branches)))}")
    print(f"PIN={q(pin)}")
    print(f"TAG={q(next(iter(tags)))}")
    print(f"SCRIPTS={q(' '.join(sorted(scripts)))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
