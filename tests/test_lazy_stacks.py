"""The Apps page defers each closed stack's heavy settings body to a fetch on first expand
(stacklazy.js), keeping the initial DOM small. A forced-open row and the no-JS fallback both render
the body server-side, so the settings are always reachable."""

from __future__ import annotations

from lhpc.adapters.web.app import create_app
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _client(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(lambda: svc).test_client(), svc


def test_closed_stack_bodies_are_deferred(tmp_path):
    c, svc = _client(tmp_path)
    body = c.get("/stacks").get_data(as_text=True)
    n = len(svc.stacks())
    assert body.count('class="lazy-body"') == n          # every (closed) stack body is deferred
    assert "stacklazy.js" in body and "data-body-url" in body


def test_forced_open_row_renders_its_body_inline(tmp_path):
    c, _svc = _client(tmp_path)
    body = c.get("/stacks?open=kiss").get_data(as_text=True)
    kiss = body.split("stackrow-kiss", 1)[1].split("</details>", 1)[0]
    assert "data-body-url" not in kiss                    # the forced-open body is inline, not deferred
    assert 'class="lazy-body"' in body                    # …the other rows stay deferred


def test_partial_body_route_renders_every_stack(tmp_path):
    c, svc = _client(tmp_path)
    for sid in [s.id for s in svc.stacks()]:
        r = c.get(f"/stacks/{sid}/body")
        assert r.status_code == 200, sid
        assert len(r.data) > 100
    assert c.get("/stacks/does-not-exist/body").status_code == 404


def test_remembered_open_row_renders_inline_and_open(tmp_path):
    """A row named in the lhpc_open cookie (stacks_state.js mirrors the open set there) renders
    already-open with its body inline — present at first paint, so re-opening it on reload does not
    shift the page (CLS). Every OTHER row stays deferred."""
    c, svc = _client(tmp_path)
    c.set_cookie("lhpc_open", "kiss")
    body = c.get("/stacks").get_data(as_text=True)
    head = body.split("stackrow-kiss", 1)[1]
    assert " open" in head[:60]                                    # opened at first paint
    kiss = head.split("</details>", 1)[0]
    assert "data-body-url" not in kiss                             # body inline, not deferred
    assert body.count('class="lazy-body"') == len(svc.stacks()) - 1  # all other rows stay lazy


def test_closed_placeholder_has_noscript_fallback_link(tmp_path):
    c, _svc = _client(tmp_path)
    body = c.get("/stacks").get_data(as_text=True)
    # A no-JS / fetch-failure fallback: a link that opens the stack server-side.
    assert "<noscript>" in body and "?open=" in body
