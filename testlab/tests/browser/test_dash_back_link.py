""""‹ Back" on a page outside the tree goes one step back in the browser history; opened with no history,
it follows its href to Home, also at a tab's first entry with a forward entry after it (new tab, Apps, the
browser's Back, then "‹ Back"): navigation.canGoBack decides. Without the Navigation API history.length decides (its
stated limit: there that last case does nothing). Real browser (Dependency Overview)."""


_NO_NAV_API = "delete Window.prototype.navigation; delete window.navigation;"   # a browser without the Navigation API


def _one_step_back_and_home_in_a_new_tab(page):
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


def test_back_goes_back_one_step_and_home_without_history(flask_page):
    page, _posts = flask_page(path="/stacks")
    assert page.evaluate("() => typeof navigation.canGoBack") == "boolean"       # Chromium: the Navigation API decides
    _one_step_back_and_home_in_a_new_tab(page)


def test_without_the_navigation_api_history_length_decides(flask_page):
    page, _posts = flask_page(path="/stacks")
    page.context.add_init_script(_NO_NAV_API)
    page.reload(wait_until="load")
    assert page.evaluate("() => typeof window.navigation") == "undefined"
    _one_step_back_and_home_in_a_new_tab(page)


def test_back_at_the_first_entry_with_a_forward_entry_goes_home(flask_page):
    page, _posts = flask_page(path="/stacks")
    base = "/".join(page.url.split("/")[:3])
    with page.expect_popup() as info:                  # a new tab on the page, then Apps, then the browser's Back
        page.evaluate("u => window.open(u)", base + "/dependencies")
    fresh = info.value
    fresh.wait_for_load_state("load")
    fresh.goto(base + "/stacks", wait_until="load")
    fresh.go_back(wait_until="load")
    assert fresh.url == base + "/dependencies" and fresh.evaluate("() => history.length") == 2
    fresh.locator("p.pagebar a[data-back]").click()
    fresh.wait_for_url(base + "/", timeout=5000)
    page.context.close()
