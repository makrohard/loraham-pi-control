"""F28 (his word): an action result's ✕ sits top right like the persistent notices' ✕; banners.js shows it (rendered
hidden: no dead control without JavaScript) and a click removes that banner only."""


def test_the_close_x_is_shown_and_removes_its_banner_only(flask_page):
    page, _posts = flask_page(path="/stacks")
    page.evaluate("""() => {
      const mk = (id) => { const d = document.createElement('div'); d.className = 'depnote depnote-ok panel-result'; d.id = id;
        d.innerHTML = '<span class="act dismiss"><button type="button" class="bnrclose" aria-label="Close" hidden></button></span>result ' + id;
        document.querySelector('main').appendChild(d); return d; };
      const root = document.createElement('div'); document.querySelector('main').appendChild(root);
      mk('r1'); mk('r2');
      document.dispatchEvent(new CustomEvent('lhpc:bodyloaded', {detail: {root: document}}));
    }""")
    x1 = page.locator("#r1 button.bnrclose")
    assert x1.is_visible()                                                        # shown by banners.js
    box, b = page.locator("#r1").bounding_box(), x1.bounding_box()
    assert b["x"] + b["width"] > box["x"] + box["width"] - 40 and b["y"] < box["y"] + 20   # top right
    x1.click()
    assert page.locator("#r1").count() == 0 and page.locator("#r2").count() == 1    # that banner only
    page.context.close()
