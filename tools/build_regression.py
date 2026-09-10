#!/usr/bin/env python3
"""Print the release marker if a captured `lhpc build` failure was the stack's OWN, else nothing.

    python3 tools/build_regression.py <stack-id> <captured-output-file>

The binary builder runs the same `lhpc build` the release lane runs, and an automated release
freezes a pin on the answer to the same question. So the builder asks the CONTROLLER, at the exact
commit it was told to build, rather than carrying a second copy of the rule in shell — a copy that
would drift, in the direction of freezing an upstream pin over a broken package index.

Exit status is 0 whether or not the failure attributes: "no evidence" is a normal answer, and the
builder's own exit code already says the build failed. Only a usage error is nonzero.
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lhpc.core import build_regression as br            # noqa: E402
from lhpc.core.manifest import parse_manifest           # noqa: E402


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    stack_id, out_file = argv
    manifest = pathlib.Path(__file__).resolve().parents[1] / "lhpc/data/manifest.example.toml"
    stacks = {s.id: s for s in parse_manifest(tomllib.loads(manifest.read_text()))}
    stack = stacks.get(stack_id)
    if stack is None:
        print(f"unknown stack {stack_id!r}", file=sys.stderr)
        return 2
    output = pathlib.Path(out_file).read_text(errors="replace")
    hit = br.attributed_step(stack, output)
    if hit is not None:
        print(br.marker_line(stack_id, "build"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
