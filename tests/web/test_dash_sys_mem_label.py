"""The System card's memory row label shows MEM (it fits its 3.6rem
column on phones); the row header keeps aria-label "Memory" for screen readers. Rendered GET of Home."""
from htmlq import parse


def test_the_memory_row_label_is_mem(web):
    doc = parse(web().get("/").get_data(as_text=True))
    row = doc.find("tr", class_="sysrow-mem")[0]
    th = doc.within(row).find("th")[0]
    assert (th.text, th["aria-label"], th["scope"]) == ("MEM", "Memory", "row")
