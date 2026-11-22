"""F28 (his rule): on the Apps pages every action result appears directly under the row of the clicked button, inside
its node (the node opened), never on top; Home keeps its results on top. Every action-result banner has the same close X
(rendered hidden; banners.js shows it). The coverage population is every rendered submit control (external `form=`
submitters included); the only exceptions are listed in EXEMPT with their reason."""
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from htmlq import parse

from lhpc.adapters.web import app as _app

TEMPLATES = Path(_app.__file__).resolve().parent / "templates"
# Apps-tree partials and macros (pages outside the tree: confirm pages, Home, hardware, logs, the level page shell)
TREE = sorted(p for p in TEMPLATES.glob("_*.html") if p.name not in ("_hw_add_form.html",))
EXEMPT = {                                           # route -> why its controls carry no row slot
    "action": "lifecycle start/stop/install/...: a job or confirm page, not a tree result",
    "radio_set": "the daemon radio Apply answers in place (no redirect)",
    "dep_note_dismiss": "the persistent notice's own server close (it is a banner's ✕, not an action row)",
    "hardware_changes_dismiss": "the persistent notice's own server close",
}
ADDRESSED = {                                        # route -> the row address its route flashes to (= its slot id)
    "stack_config_save": "stack-cfgacts-", "stack_config_reset": "stack-cfgacts-",
    "stack_web_configure": "stack-webserver-", "stack_webs_configure": "ws-stackwebs",
}
_FORM = re.compile(r'<form\b[^>]*method="post"[^>]*>.*?</form>', re.S | re.I)
_ROUTE = re.compile(r"url_for\('([a-z_]+)'")
_SLOT_VALUE = re.compile(r'name="_slot" value="([^"]+)"')


def _forms(text):
    for m in _FORM.finditer(text):
        route = (_ROUTE.search(m.group(0)) or [None, ""])[1]
        yield route, m.group(0)


@pytest.mark.parametrize("tpl", TREE, ids=lambda p: p.name)
def test_every_tree_submit_button_names_its_row_and_its_row_has_the_slot(tpl):
    text = tpl.read_text()
    for route, form in _forms(text):
        if route in EXEMPT or route.endswith("_dismiss") or 'action="{{ x }}"' in form:   # a persistent notice's ✕
            continue
        buttons = re.findall(r'<button\b[^>]*type="submit"[^>]*>', form) + re.findall(r'<input\b[^>]*type="submit"[^>]*>', form)
        if route in ADDRESSED:
            assert f"result_slot('{ADDRESSED[route]}" in text or "result_slot_results('" + ADDRESSED[route] in text, \
                (tpl.name, route, "its route address has no slot here")
            continue
        for b in buttons:
            m = _SLOT_VALUE.search(b)
            assert m, (tpl.name, route, b)                                        # every control names its row
            value = m.group(1)
            if value.startswith("{{"):                                             # a template variable holds the id
                var = value.strip("{} ")
                assert f"result_slot({var})" in text, (tpl.name, value)
            elif "{{" not in value:
                assert f"result_slot('{value}')" in text, (tpl.name, value)          # a fixed id: the exact slot
            else:
                prefix = value.split("{{")[0]
                assert f"result_slot('{prefix}' ~" in text or f"result_slot('{prefix}" in text, (tpl.name, value)
    for ext in re.findall(r'<button\b[^>]*\bform="[^"]+"[^>]*type="submit"[^>]*>|<button\b[^>]*type="submit"[^>]*\bform="[^"]+"[^>]*>', text):
        assert "_slot" in ext or "cfg-form-" in ext, (tpl.name, ext)                # an external submitter too


# ---- behaviour: every slotted control of every level page the test box renders ----

def _level_pages(c):
    doc = parse(c.get("/stacks/menus").get_data(as_text=True))
    return sorted({a["href"] for a in doc.find("a") if (a["href"] or "").startswith("/stacks/") and "/menu/" in a["href"]})


def _controls(doc):
    """(form, submitter) of every rendered submit control carrying `_slot` (external `form=` submitters included)."""
    forms = {f["id"]: f for f in doc.find("form") if f["id"]}
    out = []
    for b in doc.find("button"):
        if b["name"] != "_slot" or (b["type"] or "submit") != "submit" or b.has_attr("disabled"):
            continue
        owner = forms.get(b["form"]) if b["form"] else next((f for f in doc.find("form") if f.index < b.index <= f.end), None)
        if owner is not None:
            out.append((owner, b))
    return out


def _fields(doc, form, submitter):
    out = {i["name"]: i.attrs.get("value", "") for i in doc.within(form).find("input")
           if i.attrs.get("name") and (i.attrs.get("type") not in ("checkbox", "radio") or "checked" in i.attrs)}
    out.update({s["name"]: (doc.within(s).find("option") or [None])[0].attrs.get("value", "")
                for s in doc.within(form).find("select") if s.attrs.get("name") and doc.within(s).find("option")})
    out[submitter["name"]] = submitter["value"]
    return out


def _row(doc, b):
    """The clicked control's row: its nearest .actrow/.actline/table row, else its form."""
    rows = [e for tag, cls in (("div", "actrow"), ("div", "actline"), ("p", "actrow")) for e in doc.find(tag, class_=cls)
            if e.index < b.index <= e.end]
    trs = [e for e in doc.find("tr") if e.index < b.index <= e.end]
    forms = [f for f in doc.find("form") if f.index < b.index <= f.end] or [f for f in doc.find("form") if f["id"] == b["form"]]
    cand = rows + trs                                    # the innermost row holding it, else its form
    return max(cand, key=lambda e: e.index) if cand else (forms[-1] if forms else None)


def _banner_under(doc, slot_value):
    """The banner rendered for the row `slot_value` names, if it sits directly under that row (inside its node)."""
    for b in doc.find("button"):
        if b["name"] == "_slot" and b["value"] == slot_value:
            row = _row(doc, b)
            nxt = [e for e in doc.find("div", class_="panel-result") if e.index == row.end + 1]
            if not nxt and row.tag == "tr":                                          # a table row: the next row holds it
                rr = [t for t in doc.find("tr") if t.index == row.end + 1]
                nxt = [e for e in doc.find("div", class_="panel-result") if rr and rr[0].index < e.index <= rr[0].end]
            if nxt:
                nodes = [d for d in doc.find("details") if d.index < row.index <= d.end]
                return nxt[0], nodes
    return None, []


def test_every_slotted_control_lands_under_its_row_on_the_level_page(web):
    c = web()
    seen, landed, skipped = set(), 0, []
    for page in _level_pages(c):
        doc = parse(c.get(page).get_data(as_text=True))
        for form, b in _controls(doc):
            key = (urlsplit(b["formaction"] or form["action"]).path, b["value"])
            if key in seen:
                continue
            seen.add(key)
            r = c.post(b["formaction"] or form["action"], data=_fields(doc, form, b))
            if r.status_code not in (302, 303):
                skipped.append((key, r.status_code)); continue
            back = r.headers["Location"].split("#")[0]
            after = parse(c.get(back).get_data(as_text=True))
            if urlsplit(back).path not in ("/stacks",) and not urlsplit(back).path.startswith("/stacks/"):
                skipped.append((key, back)); continue                               # lands outside the tree (job page)
            banner, nodes = _banner_under(after, b["value"])
            assert banner is not None, (page, key, back)                              # right under its row
            assert all(d.has_attr("open") for d in nodes), (page, key)                # its nodes arrive open
            assert after.find("p", class_="flash") == [], (page, key)                # nothing on top
            assert len(after.within(banner).find("button", class_="bnrclose")) == 1   # its X
            landed += 1
    assert landed >= 8, (landed, skipped)                                           # a floor: an empty scan cannot pass


def test_every_slotted_control_lands_under_its_row_from_the_apps_page(web):
    # the other entry context: the Apps page, whose script sends the acted level (`_level`) and no `return`
    c = web()
    seen, landed = set(), 0
    for page in _level_pages(c):
        owner, mid = page.split("/stacks/")[1].split("/menu/")
        doc = parse(c.get(page).get_data(as_text=True))
        for form, b in _controls(doc):
            key = (urlsplit(b["formaction"] or form["action"]).path, b["value"])
            if key in seen:
                continue
            seen.add(key)
            data = {k: v for k, v in _fields(doc, form, b).items() if k != "return"}
            data["_level"] = f"{owner}/{mid.split('?')[0]}"
            r = c.post(b["formaction"] or form["action"], data=data)
            if r.status_code not in (302, 303):
                continue
            back = r.headers["Location"].split("#")[0]
            if not urlsplit(back).path.startswith("/stacks"):
                continue
            after = parse(c.get(back).get_data(as_text=True))
            if not [x for x in after.find("button") if x["name"] == "_slot" and x["value"] == b["value"]]:
                continue                                                             # its row is not rendered inline here
            banner, nodes = _banner_under(after, b["value"])
            assert banner is not None and all(d.has_attr("open") for d in nodes), (page, key, back)
            assert after.find("p", class_="flash") == [], (page, key)
            landed += 1
    assert landed >= 4, landed


def test_pages_outside_the_apps_tree_keep_results_on_top(web, csrf):
    # his rule is for the Apps pages; Home and the Hardware page (which shares the radio list and its row slot) keep
    # their results on top
    c = web()
    for back in ("/hardware", "/"):
        r = c.post("/hardware/enabled", data={"_csrf": csrf(c), "rid": "r1", "enabled": "off", "_slot": "slot-hw-r1",
                                             "back": back})
        assert r.status_code in (302, 303)
        doc = parse(c.get(back).get_data(as_text=True))
        assert not doc.find("div", class_="panel-result"), back                      # no row slot there
        assert doc.find("p", class_="flash"), back                                   # the result on top


def test_the_menu_return_scan_matches_the_clicked_submitter():
    # a POST naming its row came from the form whose submitter carries that row (two identical forms, two rows)
    html = ('<form method="post" action="/x"><input type="hidden" name="a" value="1">'
            '<button type="submit" name="_slot" value="row-a">A</button></form>'
            '<form method="post" action="/x" id="f2"><input type="hidden" name="a" value="2"></form>'
            '<button type="submit" form="f2" name="_slot" value="row-b">B</button>')
    from werkzeug.datastructures import MultiDict
    assert _app._renders_form(html, "/x", MultiDict(), MultiDict({"a": "1", "_slot": "row-a"}))
    assert not _app._renders_form(html, "/x", MultiDict(), MultiDict({"a": "1", "_slot": "row-b"}))
    assert _app._renders_form(html, "/x", MultiDict(), MultiDict({"a": "2", "_slot": "row-b"}))   # external submitter


def test_a_bad_slot_value_is_ignored(web, csrf):
    c = web()
    c.post("/self-update/check", data={"_csrf": csrf(c), "_slot": "BAD slot!"})
    doc = parse(c.get("/stacks").get_data(as_text=True))
    assert doc.find("p", class_="flash") or doc.find("div", class_="panel-result")   # shown as before (level / top)


def test_the_close_x_is_rendered_hidden_on_every_result_banner(web, csrf):
    c = web()
    c.post("/self-update/check", data={"_csrf": csrf(c)})                            # a top result (no row named)
    doc = parse(c.get("/").get_data(as_text=True))
    flashes = doc.find("p", class_="flash")
    assert flashes and all(len(doc.within(p).find("button", class_="bnrclose")) == 1 for p in flashes)
    assert all(b.has_attr("hidden") for b in doc.find("button", class_="bnrclose"))   # no dead control without JS


def test_the_open_pass_opens_only_the_banners_ancestors_and_ignores_scripts():
    html = ('<details id="a"><details id="b"><div class="depnote panel-result">r</div></details></details>'
            '<details id="c"><p>x</p></details><script>var s = "<details><div class=\\"panel-result\\">";</script>')
    out = parse(_app._open_result_nodes(html))
    assert out.by_id("a").has_attr("open") and out.by_id("b").has_attr("open")
    assert not out.by_id("c").has_attr("open")


@pytest.mark.parametrize("brk", ["\r", "a\rb", "\x0c", "\x0b", "\x85", "\u2028", "\u2029", "\r\n"],
                         ids=lambda c: repr(c))
def test_the_open_pass_keeps_the_markup_whatever_line_break_precedes_the_node(brk):
    """The parser counts lines by "\n" only: a \r, form feed, NEL or U+2028 in rendered text before a node must not move
    the inserted ` open` into the tag (was: `<d openetails>`)."""
    html = (f'<p>{brk}</p>\n<details id="a"><summary>s</summary>\n'
            '<div class="panel-result">r</div></details>\n<details id="c"><p>x</p></details>')
    out = _app._open_result_nodes(html)
    assert out == html.replace('<details id="a">', '<details id="a" open>', 1)


def test_a_stray_end_tag_keeps_the_banners_ancestors():
    html = '<details id="a"><div><span></p></span><div class="panel-result">r</div></div></details>'
    assert parse(_app._open_result_nodes(html)).by_id("a").has_attr("open")
