"""Dash plan c17 (his word): the All menus page has a second card below the tree with exactly two links, Dependency
Overview (/dependencies) and Auto-install (/auto-install), each answering 200; nothing else is added. Rendered GET."""
from htmlq import parse


def test_the_second_card_lists_the_two_pages(web):
    c = web()
    doc = parse(c.get("/stacks/menus").get_data(as_text=True))
    other = doc.by_id("menu-other")
    assert other is not None and other.has_class("card")
    tree = [d for d in doc.find("div", class_="card") if d is not other][0]
    assert tree.index < other.index                                        # below the tree card
    links = [(a.text, a["href"]) for a in doc.within(other).find("a")]
    assert links == [("Dependency Overview", "/dependencies"), ("Auto-install", "/auto-install")]
    assert all(c.get(h).status_code == 200 for _t, h in links)
