#!/usr/bin/env python3
"""Emit the shared managed-source pin contract for the CI cross-repository pin-validation job (and
assert the consumers agree). Manifest-only, no git, no network.

    manifest_pin.py <source-path>          e.g.  src/meshcom-qemu-raspi

Prints shell-eval'able assignments and exits 0:

    CONSUMERS=<n>
    REMOTE=<git remote>
    BRANCH=<branch>
    PIN=<full 40-hex commit>
    TAG=<pin_tag>
    SCRIPTS=<space-separated repo-relative scripts referenced by build_steps/run/test/build/pre>

Exits nonzero if there are no pinned consumers of the path, the consumers disagree on
pin_commit/pin_tag/remote/branch, or the pin is not a full 40-hex SHA.
"""
import pathlib
import re
import sys
import tomllib

_SHA = re.compile(r"^[0-9a-f]{40}$")
_TOKEN = re.compile(r"^(?:scripts|tools|bin)/[\w./-]+\.(?:sh|py)$")
_EMBED = re.compile(r"(?:^|[\s'\"=])((?:scripts|tools|bin)/[\w./-]+\.(?:sh|py))")


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: manifest_pin.py <source-path>", file=sys.stderr)
        return 2
    want = argv[1]
    root = pathlib.Path(__file__).resolve().parents[1]
    m = tomllib.loads((root / "lhpc" / "data" / "manifest.example.toml").read_text())
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
    print(f"CONSUMERS={len(consumers)}")
    print(f"REMOTE={next(iter(remotes))}")
    print(f"BRANCH={next(iter(branches))}")
    print(f"PIN={pin}")
    print(f"TAG={next(iter(tags))}")
    print(f"SCRIPTS={' '.join(sorted(scripts))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
