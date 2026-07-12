"""Accessibility invariant for form dropdowns.

Every `<select>` a screen-reader user meets must have an accessible name — an `aria-label` /
`aria-labelledby`, or a wrapping `<label>` — otherwise the control is announced only as
"combo box" with no hint of what it changes. This is a PROPERTY check (does a name exist?),
not markup pinning: it never asserts a specific label text, class, or attribute order, so it
survives harmless template edits and only fires when a new unlabelled dropdown is introduced.
"""

from __future__ import annotations

import re
from pathlib import Path

from lhpc.adapters.web import app as _app

TEMPLATES = Path(_app.__file__).resolve().parent / "templates"


def _inside_open_label(text: str, pos: int) -> bool:
    # A wrapping <label>…<select>…</label> names the control. True when the nearest <label
    # before `pos` has not yet been closed.
    before = text[:pos]
    return before.rfind("<label") > before.rfind("</label>")


def test_every_select_dropdown_has_an_accessible_name():
    offenders = []
    for f in sorted(TEMPLATES.glob("*.html")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"<select\b[^>]*>", text):      # [^>] spans newlines: multi-line tags OK
            tag = m.group(0)
            if "aria-label" in tag:                          # aria-label AND aria-labelledby
                continue
            if _inside_open_label(text, m.start()):
                continue
            offenders.append(f"{f.name}: {tag.strip()}")
    assert not offenders, "form dropdowns without an accessible name:\n" + "\n".join(offenders)
