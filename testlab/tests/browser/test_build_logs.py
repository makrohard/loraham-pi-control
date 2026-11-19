"""UI-REORG c13 (his OPEN-UX2-2 = A): Install › Build logs in a real browser: an entry's panel asks its component's
newest install log (here the build log) once when opened and shows its lines; an entry with no log yet asks nothing."""
from __future__ import annotations


def test_a_build_log_panel_asks_its_job_log_once_on_open_and_a_no_log_entry_asks_nothing(flask_page, tmp_path):
    from lhpc.core.paths import Paths
    log = Paths(runtime_root=tmp_path).under("logs", "build-radiolib.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("radiolib built\n")
    page, _posts = flask_page(path="/stacks/daemon/menu/stack-install-daemon--build-logs")
    asked = []
    page.on("request", lambda r: asked.append(r.url.split("/api/logs/", 1)[1]) if "/api/logs/" in r.url else None)
    page.click("#build-radiolib > summary")
    page.wait_for_function("() => document.querySelector('#build-radiolib .logpanel-text').textContent"
                           ".includes('radiolib built')", timeout=15000)
    page.click("#build-libch341 > summary")
    page.wait_for_function("() => document.getElementById('build-libch341').open", timeout=15000)
    assert "no log yet" in page.inner_text("#build-libch341")
    assert asked == ["radiolib?job=build-radiolib.log"]
    assert page.locator("a.pagelink[href='/logs/radiolib?job=build-radiolib.log']").count() == 1
    assert page.locator("a.pagelink[href$='/menu/build-libch341']").count() == 1       # no log yet: its menu page
