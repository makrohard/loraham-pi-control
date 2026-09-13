"""The suite's own HTML query helper: what every web test relies on to assert structure instead of
markup strings. Exact by design — these are the helper's contract."""

from __future__ import annotations

import htmlq

PAGE = """<html><body>
<details class="stackrow" id="stackrow-daemon"><summary><span class="pill mono ver ver-yellow" title="behind">@abc</span>
 Daemon <b>running</b></summary>
 <div class="body"><form><select name="value"><option value="on">on</option><option value="off" selected>off</option></select>
 <input type="hidden" name="job" value="rf-daemon-433.log"><br><p>inner &amp; text</p></form></div></details>
<div class="row-actions"><a class="update-link" href="/stacks?open=daemon&amp;inst=daemon">Update</a></div>
<details class="stackrow" id="stackrow-kiss"><summary>KISS</summary><select name="value"><option value="on" selected>on</option></select></details>
</body></html>"""


def test_class_tokens_scopes_text_and_order():
    doc = htmlq.parse(PAGE)
    pills = doc.find("span", class_="pill")
    assert len(pills) == 1 and pills[0].has_class("ver-yellow") and pills[0]["title"] == "behind"
    assert doc.find("a", class_="update-link")[0]["href"] == "/stacks?open=daemon&inst=daemon"   # unescaped
    daemon = doc.within(doc.by_id("stackrow-daemon"))
    assert daemon.find("select", name="value") and not daemon.find("a", class_="update-link")
    assert daemon.text.startswith("@abc Daemon running") and "inner & text" in daemon.text
    assert doc.index(doc.by_id("stackrow-daemon")) < doc.index(doc.find("a", class_="update-link")[0]) \
        < doc.index(doc.by_id("stackrow-kiss"))


def test_field_default_is_scoped_and_prefers_the_selected_option():
    doc = htmlq.parse(PAGE)
    assert doc.field_default("value") == "off"                       # the first select on the page
    assert doc.within(doc.by_id("stackrow-kiss")).field_default("value") == "on"
    assert doc.within(doc.by_id("stackrow-daemon")).field_default("job") == "rf-daemon-433.log"
    assert doc.field_default("absent") is None


def test_void_and_unclosed_tags_do_not_swallow_their_neighbours():
    doc = htmlq.parse("<div id=a><input name=x value=1><p>one<p>two</div><div id=b>after</div>")
    a = doc.within(doc.by_id("a"))
    assert a.field_default("x") == "1" and a.text == "one two"
    assert doc.within(doc.by_id("b")).text == "after" and not doc.within(doc.by_id("b")).find("input")
