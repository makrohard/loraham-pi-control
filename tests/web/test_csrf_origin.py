"""A state-changing POST that a browser marks as coming from another origin is refused, token or not.

The stack web UIs are served on other ports of the same host: the browser treats them as the same
SITE, sends the console's cookie along (SameSite=Lax does not separate ports), and the signed cookie
is readable, so the token alone does not prove the console's own page sent the form. The browser's
`Sec-Fetch-Site` does. A request without it (the CLI, curl with a cert) is judged by the token alone,
as before.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def _post(c, token, headers):
    return c.post("/action", data={"_csrf": token, "op": "start", "target": "kiss"},
                  headers=headers)


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_a_post_from_another_origin_is_refused_even_with_the_token(web, csrf, site):
    # same-site: a stack UI on another port of this host; cross-site: any other page.
    c = web()
    token = csrf(c)
    assert token
    assert _post(c, token, {"Sec-Fetch-Site": site}).status_code == 400


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "same-origin"},   # the console's own form or fetch
    {"Sec-Fetch-Site": "none"},          # user-initiated
    {},                                  # the CLI / curl with a cert: no browser metadata
], ids=["same-origin", "user-initiated", "no-browser-metadata"])
def test_a_post_from_the_console_itself_still_works(web, csrf, headers):
    c = web()
    token = csrf(c)
    assert _post(c, token, headers).status_code == 302


def test_the_origin_check_never_replaces_the_token(web):
    c = web()
    r = c.post("/action", data={"op": "start", "target": "kiss"},
               headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 400
