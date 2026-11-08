#!/usr/bin/env python3
"""The lhpc-binaries artifacts the testlab lanes install, served from a local mirror (L1, DEC-W339).

    mirror-binaries.py list <index-v3.json> <target> <stack>...    the file name of every entry of those stacks
                                                                   for <target>, one per line; exit 1 if none
    mirror-binaries.py verify <dir> <target> <stack>...            <dir>/index-v3.json and every listed file are
                                                                   there with the index's sha256 and size; exit 1
                                                                   if not

The workflow downloads index-v3.json and the listed files before the lanes with `gh release download` (the API
asset URL with the job's token, wrapped in the transfer backoff: GitHub's browser download URLs answered 504 on
2026-11-08), proves the mirror complete in a container with no network, and mounts it read-only for the lanes
with LHPC_BINARY_MIRROR set; the product then reads the mirror and fails closed on a file it lacks
(lhpc/core/binary_install.py `_mirror_file`). Every candidate of the target is listed, not only the one the product
picks: the choice stays the product's, inside the lane, from its own inputs. Stdlib only: it runs on the CI host.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def entries(index_path, target: str, stacks) -> list[dict]:
    idx = json.loads(Path(index_path).read_text(encoding="utf-8"))
    if idx.get("schema") != 3 or not isinstance(idx.get("stacks"), dict):
        raise SystemExit(f"{index_path}: not an index-v3 (schema 3 with a stacks table)")
    out = [e for s in stacks for e in idx["stacks"].get(s, []) if e.get("target") == target]
    if not out:
        raise SystemExit(f"{index_path}: no entry of {' '.join(stacks)} for {target}: nothing to mirror")
    return out


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def verify(d: Path, target: str, stacks) -> list[str]:
    bad = []
    for e in entries(d / "index-v3.json", target, stacks):
        p = d / e["filename"]
        if not p.is_file():
            bad.append(f"{e['filename']}: missing")
        elif p.stat().st_size != e["size"] or _sha256(p) != e["sha256"]:
            bad.append(f"{e['filename']}: size or sha256 differs from the index")
    return bad


def main(argv) -> int:
    if len(argv) >= 4 and argv[0] == "list":
        print("\n".join(e["filename"] for e in entries(argv[1], argv[2], argv[3:])))
        return 0
    if len(argv) >= 4 and argv[0] == "verify":
        bad = verify(Path(argv[1]), argv[2], argv[3:])
        print("\n".join(bad) or "binary mirror complete")
        return 1 if bad else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
