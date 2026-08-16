"""Real-browser flows: the pages' JS actually executes (first live check of the static
scripts), forms submit with CSRF through the browser, the accordion/hash behavior works,
and the Test Lab panel switches scenarios."""
from __future__ import annotations

import pytest


@pytest.mark.covers("route:GET /stacks/<sid>/body")
def test_dash_and_stacks_render_without_js_errors(page):
    page.goto(page.lab_base + "/", wait_until="networkidle")
    assert "LoRaHAM" in page.title()
    assert page.locator(".lab-banner").count() == 1
    page.goto(page.lab_base + "/stacks", wait_until="networkidle")
    # open the daemon accordion — the lazy body loads via JS (GET /stacks/<sid>/body)
    row = page.locator("details").filter(has_text="LoRaHAM daemon").first
    row.locator("summary").first.click()
    page.wait_for_timeout(800)


@pytest.mark.covers("form:testlab.html#testlab_bp.action")
def test_testlab_panel_scenario_switch_in_browser(page, lab):
    page.goto(page.lab_base + "/testlab", wait_until="networkidle")
    page.check('input[name="name"][value="degraded"]')
    page.click('button:has-text("Switch scenario")')
    page.wait_for_load_state("networkidle")
    assert (lab.root / "state" / "testlab" / "scenario.json").read_text().find(
        '"degraded"') != -1
    page.check('input[name="name"][value="healthy"]')
    page.click('button:has-text("Switch scenario")')
    page.wait_for_load_state("networkidle")


def test_gps_panel_opens_without_js_errors(page):
    page.goto(page.lab_base + "/stacks?open=gps", wait_until="networkidle")
    gps = page.locator("#gps-row")
    if gps.count() == 0:
        pytest.skip("gps panel not present on this build")
    gps.locator("summary").first.click()
    page.wait_for_timeout(400)


def test_dependencies_page_renders_clean(page):
    page.goto(page.lab_base + "/dependencies", wait_until="networkidle")
    assert page.locator("body").inner_text().strip()
