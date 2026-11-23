"""The live-run pollers that navigate from a polling tick (auto_install.js) navigate ONCE: a later tick
never sends the navigation again while it still loads. On a page that renders slower than the poll (2 s),
each repeat aborted the last and the page never arrived.

A real Chromium over the console app (`flask_page`): the page's poll is answered from the fixture's `api` map,
switched to the answer that navigates only after the load. From then on every request for the page's own path is
held (a slow page) and counted; the test waits for the polls that follow, never sleeps."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

_RUN = "7" * 32


def _held_navigations(page, path: str) -> list:
    """Every later navigation of the page is counted and every request for `path` is held (never answered)."""
    navs: list = []
    page.on("request", lambda r: navs.append(r.url) if r.is_navigation_request() else None)
    page.route(lambda url: urlsplit(url).path == path, lambda route: None)
    return navs


def _polls_after_the_navigation(page, poll: str, n: int = 3) -> None:
    with page.expect_request(lambda r: r.is_navigation_request(), timeout=15_000):
        pass
    for _ in range(n):                                 # n more polls while that navigation still loads
        with page.expect_request(f"**{poll}*", timeout=15_000):
            pass


def _starting_auto_install(tmp_path, flask_page, answer: dict):
    from lhpc.core import auto_install as ai_mod
    from lhpc.core import procident
    from lhpc.core.paths import Paths
    ok, _ = ai_mod.write_reservation(Paths(runtime_root=tmp_path), _RUN, os.getpid(),
                                     procident.proc_identity(os.getpid()), phase="spawned")
    assert ok
    api = {"/api/auto-install": {"run_id": "", "spawn_live": True}}     # still starting: no navigation
    page, _posts = flask_page(path="/auto-install", api=api)
    assert page.get_attribute("#ai-run", "data-run-expect") == _RUN
    navs = _held_navigations(page, "/auto-install")
    api["/api/auto-install"] = answer
    return page, navs


def test_the_auto_install_starting_card_reloads_once_when_its_run_appears(tmp_path, flask_page):
    page, navs = _starting_auto_install(tmp_path, flask_page, {"run_id": _RUN, "spawn_live": True})
    _polls_after_the_navigation(page, "/api/auto-install")
    assert len(navs) == 1, f"{len(navs)} reloads for one run: {navs}"


def test_the_auto_install_starting_card_leaves_once_when_its_spawn_ended(tmp_path, flask_page):
    page, navs = _starting_auto_install(tmp_path, flask_page, {"run_id": "", "spawn_live": False})
    _polls_after_the_navigation(page, "/api/auto-install")
    assert navs == [f"http://127.0.0.1:9/auto-install?spawn={_RUN}"], f"{len(navs)} navigations: {navs}"

