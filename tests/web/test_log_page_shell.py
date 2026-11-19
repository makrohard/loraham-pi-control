"""UI-REORG c6 (plan v7 s5): the static full log pages (controller, webserver, firewall) share the log page shell:
breadcrumb · header (pill + tabs, no "reload" pill: his yes) · the path on its own line · the log, refreshed by logs.js
from /api/logpreview/<source> with the page's history depth (?lines=300; the tree's preview stays 30)."""
from __future__ import annotations

import pytest
from htmlq import parse

from lhpc.core.paths import Paths


@pytest.mark.parametrize("url, source", [("/controller/logs", "controller-web"),
                                         ("/controller/logs?src=selfupdate", "controller-selfupdate"),
                                         ("/webserver/logs?src=access", "webserver-access"),
                                         ("/firewall/logs", "firewall")])
def test_each_static_log_page_has_the_shell(web, url, source):
    html = web().get(url).get_data(as_text=True)
    doc = parse(html)
    card = doc.by_id("log-card")
    assert card["data-preview"] == source and doc.find("nav", class_="crumbs")
    body = doc.within(card)
    [path] = body.find("p", class_="logpath")
    assert path.text.strip() and body.find("pre", class_="logbox")
    assert ">reload<" not in html                                            # the reload pill is gone (his yes)
    assert '<script src="/static/logs.js" defer></script>' in html
    if source == "firewall":                                                 # its tmpfs note under the path
        assert "tmpfs" in body.text and html.index("logpath") < html.index("tmpfs")


def test_a_full_pages_preview_carries_its_history_depth(web, tmp_path):
    # red before c6: ?lines= ignored, always the last 30
    c = web()
    log = Paths(runtime_root=tmp_path).runtime_root / "logs" / "lhpc-web.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f"line {i}\n" for i in range(320)))
    get = lambda q="": c.get("/api/logpreview/controller-web" + q).get_json()["lines"]   # noqa: E731
    assert len(get()) == 30 and get()[-1] == "line 319"                     # the tree's preview: 30
    assert get("?lines=300") == [f"line {i}" for i in range(20, 320)]       # a full page: 300 (>30 after a poll)
    assert len(get("?lines=9999")) == 300 and len(get("?lines=0")) == 1 and len(get("?lines=x")) == 30
