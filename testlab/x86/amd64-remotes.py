#!/usr/bin/env python3
"""The upstream git sources the x86_64 lanes clone: mirror-sources.py's list plus the remotes it leaves
out only because the aarch64 lab installs those stacks from the binary channel.

    amd64-remotes.py <manifest>    one remote per line, sorted (the input of mirror-sources.py config)

There is no binary channel on x86_64 (lhpc/core/service_binary_channel.py), so meshtastic and meshcom
build from their pinned sources there, and their remotes are mirrored like every other. Every other
exclusion of mirror-sources.py stands (the lab's RadioLib fake). The list is computed by
mirror-sources.py's own `lane_remotes`, with those three exclusions lifted.

Exit 0 with the list. Exit 1, naming the reason, when one of the three is no longer excluded by
mirror-sources.py (its table changed: re-read it before trusting this list), when the manifest lacks
one of them, or when the manifest cannot be read or parsed; exit 2 on usage. Stdlib only.
"""
from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

SOURCE_BUILT_ON_AMD64 = ("meshtastic", "meshcom-firmware", "meshcom-bridge")
MIRROR_SOURCES = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "mirror-sources.py"


def _mirror_sources():
    spec = importlib.util.spec_from_file_location("mirror_sources", MIRROR_SOURCES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _component_ids(manifest: Path) -> set:
    stacks = tomllib.loads(manifest.read_text(encoding="utf-8")).get("stack")
    if type(stacks) is not list:
        raise ValueError("no [[stack]] table list")
    ids = set()
    for stack in stacks:
        comps = stack.get("component", []) if type(stack) is dict else None
        if type(comps) is not list:
            raise ValueError("a stack whose component entry is not a table list")
        ids.update(c.get("id") for c in comps if type(c) is dict and type(c.get("id")) is str)
    return ids


def remotes(manifest: Path) -> list:
    ms = _mirror_sources()
    kept = [c for c in SOURCE_BUILT_ON_AMD64 if c not in ms.NOT_MIRRORED]
    if kept:
        raise LookupError(f"mirror-sources.py no longer excludes {', '.join(kept)}: re-read its table")
    lost = [c for c in SOURCE_BUILT_ON_AMD64 if c not in _component_ids(manifest)]
    if lost:
        raise LookupError(f"{manifest} has no component {', '.join(lost)}")
    ms.NOT_MIRRORED = {k: v for k, v in ms.NOT_MIRRORED.items() if k not in SOURCE_BUILT_ON_AMD64}
    return ms.lane_remotes(manifest)


def main(argv: list) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        out = remotes(Path(argv[0]))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, LookupError, KeyError,
            AttributeError, TypeError) as exc:
        print(f"amd64-remotes: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:     # mirror-sources.py's own refusal (a stale exclusion)
        print(f"amd64-remotes: {exc}", file=sys.stderr)
        return 1
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
