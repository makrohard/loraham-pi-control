"""Dash plan c10: a Home radio card collapses like Webserver / System. div#radio-<rid> keeps its id and data attributes;
inside it details#radiobox-<rid> (open by default) > summary (title + daemon pill) + body. The operator's choice is stored
(lhpc:radiobox-<rid>, "0" and "1" both restored) across ordinary reloads; the signature reload's id-keyed snapshot wins
over the stored choice; a collapsed card still polls, its pill stays live. Real browser, r3's poll answered by the test."""
_READY = b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"


def _reply(label):
    return {"reachable": True, "ready": label == "daemon READY", "label": label, "channel": {}, "status": {"RADIO": "READY"},
            "stats": {}, "feed": []}


def _open(page):
    return page.evaluate("() => document.getElementById('radiobox-r3').open")


def _reload(page):
    page.reload(wait_until="load")


def test_the_card_is_a_collapsible_box_open_by_default(flask_page):
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api={"/api/daemon/r3": _reply("daemon READY")})
    s = page.evaluate("""() => { const card = document.getElementById('radio-r3'), box = document.getElementById('radiobox-r3');
      return {parent: box.parentElement === card, rid: card.dataset.radioRid, band: card.dataset.radioBand, open: box.open,
              summary: [...box.querySelector(':scope > summary').children].map(e => e.id || e.tagName),
              body: !!box.querySelector(':scope > div.body #monitor-r3')}; }""")
    assert s == {"parent": True, "rid": "r3", "band": "868", "open": True, "summary": ["H2", "rd-badge-r3"], "body": True}, s
    page.context.close()


def test_the_stored_choice_and_the_reload_snapshot(flask_page):
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api={"/api/daemon/r3": _reply("daemon READY")})
    page.locator("#radiobox-r3 > summary").click()                                  # the operator closes it
    page.wait_for_function("() => localStorage.getItem('lhpc:radiobox-r3') === '0'")
    _reload(page)
    assert _open(page) is False                                                     # stored "0" restored
    page.locator("#radiobox-r3 > summary").click()
    page.wait_for_function("() => localStorage.getItem('lhpc:radiobox-r3') === '1'")
    _reload(page)
    assert _open(page) is True                                                      # stored "1" restored
    # a signature reload's snapshot wins over the stored choice (both ways)
    for snap, stored, want in ((0, "1", False), (1, "0", True)):
        page.evaluate("([s, v]) => { localStorage.setItem('lhpc:radiobox-r3', v);"
                      " sessionStorage.setItem('dashDetails', JSON.stringify({'radiobox-r3': s})); }", [snap, stored])
        _reload(page)
        assert _open(page) is want, (snap, stored)
    page.context.close()


def test_a_collapsed_card_keeps_polling_and_its_pill_live(flask_page):
    api = {"/api/daemon/r3": _reply("daemon READY")}
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api=api)
    page.locator("#radiobox-r3 > summary").click()
    assert _open(page) is False
    for label in ("daemon (FAILED)", "daemon READY"):
        api["/api/daemon/r3"] = _reply(label)
        page.wait_for_function("l => document.getElementById('rd-badge-r3').textContent === l", arg=label)
        assert page.locator("#rd-badge-r3").is_visible(), label
    page.context.close()
