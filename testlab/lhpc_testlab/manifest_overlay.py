"""The lab manifest overlay: the REAL packaged manifest with exactly two source stanzas
retargeted at locally-materialized fake repos (the fake `loraham_daemon` and a stub
RadioLib build-dependency), so `lhpc install daemon` runs the PRODUCTION adopt → build →
start path with zero network and a deterministic pin (the link strategy comes from the [install] config, not the manifest — the schema forbids it there). Everything else in the manifest is
byte-identical — the transform is line-based and pinned by tests against the real file.
"""
from __future__ import annotations

from pathlib import Path

from lhpc.core import runtime_fs
from lhpc.core.manifest import default_manifest_path

# (source path in the stanza) -> (adopt local_dir the fake repo is materialized under)
RETARGETS = {"src/loraham-daemon": "loraham-daemon-fake",
             "src/RadioLib": "radiolib-fake"}

# Line-level swaps: config bases the lab replaces with a simulated-hardware variant
# (meshtasticd runs upstream's `Module: sim` radio — no SPI, everything else real).
# The sim yaml is staged into the runtime root by reset (the manifest validator only
# accepts {runtime}/... or {asset}/... bases, and {asset} is lhpc/data — not this
# package), so the swap points there.
SIM_YAML_RUNTIME = "{runtime}/state/testlab/meshtasticd-sim.yaml"

# SAFETY: force graywolf's iGate upstream to the lab's local APRS-IS sink at RENDER time. The
# `{param:igate_server}`/`{param:igate_port}` tokens (which read the OPERATOR's config) are
# replaced by LITERAL sink values, so the effective command graywolf-provision.py receives is
# always `--igate-server 127.0.0.1 --igate-port 14580` — no matter what is saved, and through the
# real launcher/argv-render path. graywolf is Go (bypasses the DNS interposer); this removes the
# operator-controlled value from the manifest entirely. `{param:igate}` (enable/disable) is kept.
GRAYWOLF_SINK_FROM = '"{param:igate}", "{param:igate_server}", "{param:igate_port}",'
GRAYWOLF_SINK_TO = ('"{param:igate}", "--igate-server", "127.0.0.1", '
                    '"--igate-port", "14580",')

LINE_SWAPS = {'base = "{asset}/bases/meshtasticd.yaml"': f'base = "{SIM_YAML_RUNTIME}"',
              GRAYWOLF_SINK_FROM: GRAYWOLF_SINK_TO}


def overlay_path(paths) -> Path:
    return paths.under("state", "testlab", "manifest.toml")


def generate(paths, commits: dict[str, str]) -> Path:
    """Render the overlay. `commits` maps the RETARGETS local_dir -> fake-repo HEAD."""
    lines = default_manifest_path().read_text().splitlines(keepends=True)
    out: list[str] = []
    i = 0
    stack_id = ""
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Track the current stack (the stack `id` is unindented; component ids are
        # indented) so the daemon-specific edit applies only to it.
        if line.startswith('id = "'):
            stack_id = stripped.split('"')[1]
        # The daemon is ALWAYS the fake (source-only) — drop its binary channel so
        # `lhpc install daemon` never resolves to the real aarch64 Pi binary (which
        # needs SPI). meshcom/meshtastic keep THEIR binary specs (that is the point).
        if stack_id == "daemon" and stripped == "[stack.binary]":
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if not s or s.startswith("["):
                    break
                i += 1
            continue
        target = src_path = None
        if stripped == "[stack.component.source]":
            # Peek the stanza's `path =` line (comments before it are fine) to decide
            # whether it is a retarget — and REUSE that peeked value; re-reading a
            # hard-coded offset crashed on a leading comment (re-review finding).
            # scan the WHOLE stanza (until a blank line or the next table header) for
            # `path =` — not just a contiguous run, so a reordered key never hides it.
            j = i + 1
            while j < len(lines):
                p = lines[j].strip()
                if not p or p.startswith("["):
                    break
                if p.startswith("path ="):
                    src_path = p.split('"')[1]
                    target = RETARGETS.get(src_path)
                    break
                j += 1
        if target is None:
            swapped = LINE_SWAPS.get(stripped)
            if swapped is not None:
                indent = line[:len(line) - len(line.lstrip())]
                out.append(f"{indent}{swapped}\n")
            else:
                out.append(line)
            i += 1
            continue
        indent = line[:len(line) - len(line.lstrip())]
        out.append(line)
        out.append(f'{indent}path = "{src_path}"\n')
        out.append(f'{indent}pin_commit = "{commits[target]}"\n')
        out.append(f'{indent}pin_tag = "testlab"\n')
        out.append(f'{indent}remote = ""\n')
        out.append(f'{indent}branch = ""\n')
        out.append(f'{indent}local_dir = "{target}"\n')
        # Skip the ORIGINAL stanza body (until a blank line / next table header).
        i += 1
        while i < len(lines):
            s = lines[i].strip()
            if not s or s.startswith("["):
                break
            i += 1
    text = "".join(out)
    runtime_fs.atomic_write(paths, overlay_path(paths), text, 0o600)
    return overlay_path(paths)
