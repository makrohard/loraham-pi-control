"""Every entry of the Webserver card (a line with its name: Firewall, LHPC, each page or
address) is its own box (div.wsgroup); the Firewall entry's follow-up lines (the not-set-up note, or the two command
boxes) sit inside the Firewall box; nothing in the body is outside a box. Rendered GET of Home."""
from htmlq import parse


def test_one_box_per_webserver_entry(web):
    doc = parse(web().get("/").get_data(as_text=True))
    box = doc.within(doc.by_id("wsbox"))
    body = box.find("div", class_="body")[0]
    groups = box.find("div", class_="wsgroup")
    assert len(groups) >= 2
    tops = [g for g in groups if not [o for o in groups if o is not g and o.index < g.index <= o.end]]
    assert tops == groups                                                             # no box inside a box
    names = [[a.text for a in box.within(g).find("a", class_="wsname")] for g in groups]
    assert all(len(n) == 1 for n in names) and names[0] == ["Firewall"], names
    lines = box.find("div", class_="wsline")
    assert lines and all(any(g.index < ln.index <= g.end for g in groups) for ln in lines)   # every line in a box
    fw = box.within(groups[0])
    assert "Not set up yet" in fw.text or fw.find("div", class_="cmdbox") or fw.find("pre")  # its follow-up inside it
    assert all(body.index < g.index <= body.end for g in groups)
