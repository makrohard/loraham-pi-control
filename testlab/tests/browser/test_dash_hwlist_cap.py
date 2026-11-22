"""Dash plan c12 S13 (his "cap hw list table"): the Hardware page's radio list table (#hw-list) is today's 950 px on a
big screen and fits the width on a phone (no sideways scroll). Real browser."""


def test_the_hardware_list_is_capped(flask_page):
    page, _posts = flask_page(path="/hardware")
    width = "() => Math.round(document.getElementById('hw-list').getBoundingClientRect().width)"
    page.set_viewport_size({"width": 1920, "height": 1000})
    assert page.evaluate(width) == 950
    page.set_viewport_size({"width": 360, "height": 900})
    assert page.evaluate(width) < 360 and page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
    page.context.close()
