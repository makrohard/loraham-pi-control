"""Week item 8 (W203): a confirm page's Apply button shows a busy state the moment it is clicked.

A confirmed stop runs inside the POST (no detached job), so the page used to sit unchanged until the stop
ended and looked frozen. The confirmed form now names its busy label and loads confirm.js, which disables
the button and shows that label on submit; the click itself is the browser lane's. Here: what the page
hands the script, for every op whose confirm page renders the form.
"""
from __future__ import annotations

import pytest
from htmlq import parse

# op -> (target whose plan renders the confirmed form, the label's leading word)
OPS = {"stop": ("kiss@r1", "Stopping"),                  # W2: a stop names its instance "update": ("kiss", "Updating"), "uninstall": ("kiss", "Uninstalling"),
       "clean": ("kiss", "Cleaning"), "build": ("kiss", "Building")}


def _confirm_page(web, csrf, op, target):
    c = web()
    r = c.post("/action", data={"_csrf": csrf(c), "op": op, "target": target})
    assert r.status_code == 200, r.status_code
    return parse(r.data.decode())


def _confirmed_form(doc):
    forms = [f for f in doc.find("form") if any(i["name"] == "confirmed" and i["value"] == "yes"
                                                 for i in doc.within(f).find("input"))]
    assert len(forms) == 1, f"{len(forms)} confirmed forms"
    return forms[0]


@pytest.mark.parametrize("op", sorted(OPS))
def test_the_confirmed_form_names_its_busy_label(web, csrf, op):
    target, word = OPS[op]
    doc = _confirm_page(web, csrf, op, target)
    form = _confirmed_form(doc)
    assert form.has_attr("data-busy-form")
    buttons = [b for b in doc.within(form).find("button") if b["type"] == "submit"]
    assert len(buttons) == 1
    assert buttons[0].has_attr("data-busy-label")
    assert buttons[0]["data-busy-label"].partition("…")[0] == word                    # the op's own leading word


@pytest.mark.parametrize("op", sorted(OPS))
def test_the_confirm_page_loads_the_busy_script(web, csrf, op):
    doc = _confirm_page(web, csrf, op, OPS[op][0])
    assert "/static/confirm.js" in [s["src"] for s in doc.find("script")]


def test_control_cancel_stays_a_link_outside_the_busy_form(web, csrf):
    doc = _confirm_page(web, csrf, "stop", "kiss@r1")
    form = _confirmed_form(doc)
    cancel = doc.within(form).find("a", class_="cancel")
    assert len(cancel) == 1 and cancel[0]["href"] and not cancel[0].has_attr("data-busy-label")
