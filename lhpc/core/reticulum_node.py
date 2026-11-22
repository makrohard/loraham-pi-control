"""The Reticulum node's LoRa interfaces in its one generated config (pure, no I/O; plan reti v21 §1c-§1d, B3).

One RNS node per box: the owner radio's interface and each intended member's, one `[[LoRa <rid>]]` section each, in rid
order. The base ships one `[[LoRa]]` block; `expand` copies it per interface, `per_interface` points the LoRa params at
one interface's section, and `replace_section` swaps exactly one interface's block in a written config (an attach,
reload or detach of one member never re-renders another's).
"""
from __future__ import annotations

import dataclasses
import re

from .model import FileParam

SECTION = "interfaces/LoRa"          # the LoRa params' section in the manifest
_BLOCK = "[[LoRa]]"
_HEADER = re.compile(r"^(\s*)\[\[([^\[\]]+)\]\]\s*$")


def name(rid: str) -> str:
    """The interface's section and RNS name, `LoRa <rid>` (the seam's name, `rnstatus --attach` / `-j`)."""
    return f"LoRa {rid}"


def _blocks(lines):
    """[(start, end, name)] of every `[[…]]` block of `lines`: from its header to the next `[…]` header (or the end),
    less the blank and comment lines right before that header (they introduce the next section)."""
    heads = [(i, m.group(2).strip()) for i, ln in enumerate(lines) if (m := _HEADER.match(ln))]
    starts = [i for i, ln in enumerate(lines) if ln.strip()[:1] == "["]
    out = []
    for i, n in heads:
        e = next((x for x in starts if x > i), len(lines))
        while e > i + 1 and (not lines[e - 1].strip() or lines[e - 1].strip()[:1] == "#"):
            e -= 1
        out.append((i, e, n))
    return out


def expand(base_text: str, rids) -> str:
    """`base_text` with its `[[LoRa]]` block copied once per rid of `rids` as `[[LoRa <rid>]]`, in place and in order;
    every other line as it is. ValueError when the base has no `[[LoRa]]` block."""
    lines = base_text.splitlines()
    block = next(((s, e) for s, e, n in _blocks(lines) if f"[[{n}]]" == _BLOCK), None)
    if block is None:
        raise ValueError("config base has no [[LoRa]] block")
    s, e = block
    copies = [ln for rid in rids for ln in [lines[s].replace(_BLOCK, f"[[{name(rid)}]]"), *lines[s + 1:e]]]
    return "\n".join([*lines[:s], *copies, *lines[e:]]) + "\n"


def per_interface(params, rid: str):
    """The LoRa params of `params` (section `interfaces/LoRa`) re-sectioned to interface `rid`'s; the others dropped."""
    return tuple(dataclasses.replace(p, section=f"{SECTION} {rid}") for p in params if p.section == SECTION)


def for_owner(params, rid: str):
    """The node file's params for the owner `rid`: its LoRa params on `LoRa <rid>`, every other section's as declared."""
    return tuple(dataclasses.replace(p, section=f"{SECTION} {rid}") if p.section == SECTION else p for p in params)


def for_member(params, rid: str):
    """A member `rid`'s LoRa params on its own section, rendered disabled: RNS starts it only through an attach
    (force_attach), so a node start never collides with the member's attach (plan §1d, a25 F2)."""
    return (*per_interface(params, rid), FileParam(name="__member_enabled", key="enabled", section=f"{SECTION} {rid}",
                                                   default="no", hidden=True))


def is_lora(params) -> bool:
    """True when a config file's params render a LoRa interface (the node's file)."""
    return any(p.section == SECTION for p in params)


def section_text(text: str, rid: str) -> str:
    """Interface `rid`'s `[[LoRa <rid>]]` block of a written config, "" when it has none."""
    lines = text.splitlines()
    hit = next((lines[s:e] for s, e, n in _blocks(lines) if n == name(rid)), None)
    return "" if hit is None else "\n".join(hit) + "\n"


def replace_section(text: str, rid: str, block: str) -> str:
    """`text` with interface `rid`'s block replaced by `block` ("" removes it), every other line as it is; a block not
    there yet goes right after the last `[[LoRa …]]` block (the LoRa blocks stay together)."""
    lines, new = text.splitlines(), block.splitlines()
    blocks = _blocks(lines)
    hit = next(((s, e) for s, e, n in blocks if n == name(rid)), None)
    if hit is None:
        lora = [e for _s, e, n in blocks if n.split(" ", 1)[0] == "LoRa"]
        at = lora[-1] if lora else len(lines)
        hit = (at, at)
    s, e = hit
    return "\n".join([*lines[:s], *new, *lines[e:]]) + "\n"
