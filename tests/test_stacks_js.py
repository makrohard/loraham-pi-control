"""Source-text contracts for the Stacks page scroll-pinning JS.

These are the ONLY automated regression for the anti-jump behavior (node is not available
locally, and the one node harness is purpose-built for system.js — its DOM stub swallows
events and has no layout, so it cannot drive an accordion). Because of that, the tests pin
the critical ARITHMETIC of the helper, not merely its existence: a later sign flip, a
dropped second measurement, or a scrollTo-instead-of-scrollBy rewrite must go red even
while `pinDuring` still "exists and is called". Behavior itself is verified live.
"""

import pathlib
import re

_STATIC = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web" / "static"


def _pin_body(js: str, path: str) -> str:
    """The pinDuring function body (both files carry a deliberate local twin)."""
    m = re.search(r"function pinDuring\(el, mutate\) \{(.*?)\n  \}", js, re.S)
    assert m, f"{path}: pinDuring(el, mutate) missing"
    return m.group(1)


def _assert_pin_arithmetic(js: str, path: str) -> None:
    """The pin is exactly: measure top, mutate, measure top again, scroll by (after - before).

    The measure-AFTER order is what makes it compose with native scroll anchoring (it corrects
    only the residual); the subtraction order is what makes the correction move the right way.
    """
    body = _pin_body(js, path)
    # (a) exactly two viewport measurements, with the mutation between them.
    reads = body.count(".getBoundingClientRect().top")
    assert reads == 2, f"{path}: expected exactly 2 top measurements in pinDuring, found {reads}"
    assert re.search(
        r"var before = s\.getBoundingClientRect\(\)\.top;\s*"
        r"mutate\(\);\s*"
        r"var delta = s\.getBoundingClientRect\(\)\.top - before;", body), \
        f"{path}: pinDuring must be measure -> mutate() -> measure, delta = after - before"
    # (b) the subtraction order: the fresh after-read MINUS the stored before-value.
    assert "getBoundingClientRect().top - before" in body, \
        f"{path}: delta must subtract `before` from the after-measurement (sign matters)"
    assert "before - s.getBoundingClientRect" not in body
    # (c) the correction is a RELATIVE scroll of that delta, applied only when non-zero.
    assert re.search(r"if \(delta\) \{ window\.scrollBy\(0, delta\); \}", body), \
        f"{path}: the correction must be window.scrollBy(0, delta) — never scrollTo"
    assert "scrollTo" not in body


def test_the_accordion_pins_the_clicked_header():
    """Closing sections ABOVE the clicked one shrinks the page above the cursor; without the
    pin the clicked header leaps away (the reported 'jumping'). Both accordion levels must
    run their auto-closes inside pinDuring, and the helper's arithmetic must be exact."""
    js = (_STATIC / "stacks_state.js").read_text()
    _assert_pin_arithmetic(js, "stacks_state.js")
    # Both handlers wrap their close loops in the pin, keyed on the CLICKED element.
    assert "pinDuring(row, function () {" in js, "attachAccordion must pin the clicked row"
    assert "pinDuring(sub, function () {" in js, "attachSubAccordion must pin the clicked panel"
    # The pin anchors on the element's own summary (the thing under the cursor).
    assert ':scope > summary' in js


def test_the_action_return_restores_the_stored_scroll_position():
    """Form submits store {k, y: scrollY}; the restore path used to read only `k` and then
    scrollIntoView() yanked the acted section to the viewport top. An action return must land
    at the stored y, with block:'nearest' (a no-op when the section is already visible)."""
    js = (_STATIC / "stacks_state.js").read_text()
    assert re.search(r"typeof act\.y === \"number\"", js), "the stored y must be used"
    assert "window.scrollTo(0, act.y);" in js
    assert 'scrollIntoView({ block: "nearest" })' in js
    # The plain-link jump stays: a bare scrollIntoView() remains for the link/hash branch.
    assert re.search(r"else if \(target\) \{ target\.scrollIntoView\(\); \}", js), \
        "a link/hash navigation must still jump to the section"


def test_the_lazy_body_injection_is_pinned_and_reserves_space():
    """A body fetched after first expand grows the row asynchronously; the injection must run
    inside the pin (same arithmetic, local twin — the files are deliberately independent),
    and the placeholder reserves a little height in CSS."""
    js = (_STATIC / "stacklazy.js").read_text()
    _assert_pin_arithmetic(js, "stacklazy.js")
    assert "pinDuring(details, function () {" in js, "the insertBefore loop must be pinned"
    css = (_STATIC / "style.css").read_text()
    assert re.search(r"\.lazy-body \{ min-height:", css), \
        ".lazy-body must reserve placeholder height"
