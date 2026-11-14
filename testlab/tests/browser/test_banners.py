"""Banners in a real browser: each banner's ✕ inside the banner, top right, on the banner's own colour, at a
phone's and a desktop's width. Home and Apps run on the `flask_page` fixture with every banner raised."""
from __future__ import annotations

_LONG = "the bridge of meshcom@r3 did not answer on its own port after the start, and its web page stays unreachable"


def _banners(monkeypatch):
    """Every banner with a ✕, each with a first line long enough to wrap on a phone."""
    from lhpc.core import attention, hotplug
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(attention, "banner", lambda svc: [{"key": "k1", "kind": "stack", "ref": "meshcom",
                                                           "line": _LONG, "sticky": False}])
    monkeypatch.setattr(hotplug, "banner", lambda svc, scan=None, now=None: {"lines": [{"id": "c1", "at": "12:00",
                                                                                         "line": _LONG}]})
    monkeypatch.setattr(ControllerService, "auto_install_welcome", lambda self: {"fresh": True})
    monkeypatch.setattr(ControllerService, "welcome_note_dismissed", lambda self: False)
    real = ControllerService.dependency_overview
    monkeypatch.setattr(ControllerService, "dependency_overview", lambda self: {
        **real(self), "mandatory_missing": 0, "restart_pending": 0, "optional_missing": 2, "gui_missing": 1})
    monkeypatch.setattr(ControllerService, "dep_note_dismissed", lambda self, summary: False)


_GEOMETRY = """sel => [...document.querySelectorAll(sel)].map(b => {
  const x = b.querySelector(':scope > form.dismiss button'); const bb = b.getBoundingClientRect();
  const xb = x.getBoundingClientRect(); const pad = parseFloat(getComputedStyle(b).paddingTop);
  const others = [];
  for (const n of b.childNodes) {
    if (n.nodeType === 1 && n.matches('form.dismiss')) continue;
    const r = document.createRange(); r.selectNodeContents(n);
    for (const q of r.getClientRects()) if (q.width && q.height) others.push([q.left, q.top, q.right, q.bottom]);
  }
  const hit = others.some(([l, t, r, btm]) => l < xb.right - 0.5 && r > xb.left + 0.5 && t < xb.bottom - 0.5 && btm > xb.top + 0.5);
  return {id: b.id || b.className, inside: xb.left >= bb.left && xb.right <= bb.right && xb.top >= bb.top && xb.bottom <= bb.bottom,
          top: xb.top - bb.top - pad, right: bb.right - xb.right, overlap: hit};
})"""


_LOOK = """sel => [...document.querySelectorAll(sel)].map(b => {
  const s = getComputedStyle(b.querySelector(':scope > form.dismiss button'));
  return {bg: s.backgroundColor, border: s.borderTopColor, ink: s.color, banner_ink: getComputedStyle(b).color};
})"""


def test_each_banner_closes_from_its_top_right_corner(flask_page, monkeypatch):
    # A16, his "B top right": the ✕ inside the banner, on its first line at the right, over no text, 360 and 1280 px.
    _banners(monkeypatch)
    for path, n in (("/", 3), ("/stacks", 1)):
        page, _posts = flask_page(path=path)
        for width in (1280, 360):
            page.set_viewport_size({"width": width, "height": 900})
            got = page.evaluate(_GEOMETRY, ".depnote:has(> form.dismiss)")
            assert len(got) == n, (path, got)
            for g in got:
                assert g["inside"] and g["top"] <= 4 and g["right"] <= 20 and not g["overlap"], (path, width, g)
        look = page.evaluate(_LOOK, ".depnote:has(> form.dismiss)")
        assert look and all(x["bg"] == "rgba(0, 0, 0, 0)" and x["border"] == "rgba(0, 0, 0, 0)" and x["ink"] == x["banner_ink"]
                            for x in look), (path, look)
        page.context.close()
