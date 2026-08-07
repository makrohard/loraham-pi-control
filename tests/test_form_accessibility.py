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


def test_icon_only_buttons_have_an_accessible_name():
    """A button whose whole content is an icon glyph (×, ✓, …) is announced as that glyph — or as
    nothing — unless it carries an accessible name. Lighthouse/axe flag it as "Buttons do not have
    an accessible name", and it is the one a11y regression this console keeps re-introducing,
    because a dismiss control is naturally written as `<button>&times;</button>`.

    `title` alone is a last-resort fallback in the accname spec and is not exposed on touch, so it
    does not count here. Behaviour-shaped like its sibling above: it never pins label TEXT, only
    that some accessible name exists."""
    offenders = []
    for tpl in sorted(TEMPLATES.glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", text, re.S):
            attrs, body = m.group(1), m.group(2).strip()
            # Visible text = the body minus HTML entities and Jinja STATEMENT tags. A `{{ expr }}`
            # is kept: it renders real text, so such a button is already named. Only a body that
            # is nothing but an icon glyph needs an explicit accessible name.
            visible = re.sub(r"&[#a-zA-Z0-9]+;|\{%.*?%\}", "", body, flags=re.S).strip()
            if visible:
                continue
            if "aria-label" in attrs or "aria-labelledby" in attrs:
                continue
            offenders.append(f"{tpl.name}: <button{attrs[:60]}>{body[:20]}")
    assert not offenders, ("icon-only buttons without an accessible name:\n"
                           + "\n".join(offenders))


def test_templates_carry_no_csp_blocked_inline_code():
    """The console sends `Content-Security-Policy: default-src 'self'` with no 'unsafe-inline',
    so the browser BLOCKS inline styles, inline scripts and on* handler attributes — the control
    silently does not work and DevTools/Lighthouse report a violation.

    This caught a real one: `_task_banner.html` rendered `style="display:none"` to hide an empty
    hint, blocked as `style-src-attr` on the stacks page. It is now a class the CSS owns and
    `taskbanner.js` toggles.

    Behaviour, not markup pinning: it forbids the inline VECTORS, never a particular class name.
    JS writing `element.style.x` (CSSOM) is deliberately NOT covered — CSP does not block it."""
    vectors = (
        (re.compile(r"<[^>]*\sstyle\s*=", re.I), "inline style= attribute (style-src-attr)"),
        (re.compile(r"<style[\s>]", re.I), "<style> block (style-src-elem)"),
        (re.compile(r"<script(?![^>]*\ssrc=)[^>]*>", re.I), "inline <script> (script-src-elem)"),
        (re.compile(r"<[^>]*\son[a-z]+\s*=\s*[\"']", re.I), "on* handler attribute (script-src-attr)"),
        (re.compile(r"[\"']javascript:", re.I), "javascript: URL"),
    )
    offenders = []
    for tpl in sorted(TEMPLATES.glob("*.html")):
        # Strip Jinja comments first: the explanatory notes about these very vectors are not code.
        text = re.sub(r"\{#.*?#\}", "", tpl.read_text(encoding="utf-8"), flags=re.S)
        for rx, what in vectors:
            for m in rx.finditer(text):
                offenders.append(f"{tpl.name}:{text[:m.start()].count(chr(10)) + 1}: {what}")
    assert not offenders, ("CSP would block these — move them to static/*.css or static/*.js:\n"
                           + "\n".join(offenders))
