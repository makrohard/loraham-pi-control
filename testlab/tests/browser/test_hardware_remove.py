"""The Hardware page's Remove, in a real browser over the lab server: a radio's page opens the confirm step, which
names what stays (its kept folders); Cancel changes nothing, and Remove takes the row off the list.

The lab is shared by the whole browser lane, so the test puts the lab's baseline back (`lhpc-testlab reset`) after it
has removed a radio.
"""
from __future__ import annotations

import pytest
from lhpc_testlab.testing import run_lab

RID = "r2"                                       # the lab's 868 MHz radio; nothing runs on it


@pytest.fixture()
def kept_folder(lab):
    """A folder of RID on disk, which a remove keeps; the lab's baseline comes back afterwards."""
    folder = lab.root / "logs" / RID
    folder.mkdir(parents=True, exist_ok=True)
    yield folder
    run_lab(lab.env, "reset", check=True)


def _rows(page):
    return page.eval_on_selector_all("#hw-list tr.hwrow", "rows => rows.map(r => r.dataset.rid)")


def _open_remove(page):
    page.goto(page.lab_base + f"/hardware/radio/{RID}", wait_until="load")
    page.click("a.button.danger")                # the radio page's Remove
    page.wait_for_url(f"**/hardware/remove/{RID}")


def test_remove_names_what_stays_cancel_keeps_the_row_and_remove_takes_it_off(page, kept_folder):
    _open_remove(page)
    facts = page.eval_on_selector_all("#remove-facts dt, #remove-facts dd", "els => els.map(e => e.textContent.trim())")
    assert facts == ["State", "enabled", "Binding", "SPI (the board's lines)", "Kept folders", str(kept_folder)]
    assert page.text_content("#hardware-remove form p").strip() == f"Remove {RID} (loraham)?"

    page.click("#hardware-remove >> text=Cancel")             # Cancel: back to the list, nothing removed
    page.wait_for_url("**/hardware")
    assert _rows(page) == ["r1", RID]

    _open_remove(page)
    page.click("#hardware-remove button.danger")               # Remove
    page.wait_for_url("**/hardware")
    assert _rows(page) == ["r1"]
    assert kept_folder.is_dir()                                # the remove keeps the radio's folder
