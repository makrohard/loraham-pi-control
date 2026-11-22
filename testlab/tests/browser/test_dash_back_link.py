""""‹ Back" on a page outside the tree goes one step back in the browser history; opened with no history,
it follows its href to Home. Real browser (Dependency Overview)."""


def test_back_goes_back_one_step_and_home_without_history(flask_page):
    page, _posts = flask_page(path="/stacks")
    base = "/".join(page.url.split("/")[:3])
    page.goto(base + "/dependencies", wait_until="load")
    page.locator("p.pagebar a[data-back]").click()
    page.wait_for_url(base + "/stacks")
    with page.expect_popup() as info:                  # a tab opened straight on the page: no history to go back to
        page.evaluate("u => window.open(u)", base + "/dependencies")
    fresh = info.value
    fresh.wait_for_load_state("load")
    assert fresh.evaluate("() => history.length") == 1
    fresh.locator("p.pagebar a[data-back]").click()
    fresh.wait_for_url(base + "/")
    page.context.close()
