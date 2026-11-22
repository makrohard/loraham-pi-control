"""Dash plan c12 S8c (his word, read narrowly): the System card's host info line starts empty, the placeholder "Expand to
load system information." is gone; the line itself stays (system.js fills it by id, test_system_box.py). Rendered GET."""
from htmlq import parse


def test_the_info_line_has_no_placeholder(web):
    body = web().get("/").get_data(as_text=True)
    info = parse(body).by_id("sys-info")
    assert info is not None and info.has_class("sys-info") and info.text == ""
    assert "Expand to load system information" not in body
