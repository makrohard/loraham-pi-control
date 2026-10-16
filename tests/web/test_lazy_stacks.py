"""The Apps page defers each closed stack's heavy settings body to a fetch on first expand
(stacklazy.js), keeping the initial DOM small. A forced-open row and the no-JS fallback both render
the body server-side, so the settings are always reachable."""

from __future__ import annotations

import pytest
from htmlq import parse
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _client(web, tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return web(service_factory=lambda: svc), svc


def test_closed_stack_bodies_are_deferred(tmp_path, web):
    c, svc = _client(web, tmp_path)
    body = c.get("/stacks").get_data(as_text=True)
    n = len(svc.stacks())
    assert len(parse(body).find("div", class_="lazy-body")) == n   # every (closed) stack body is deferred
    assert "stacklazy.js" in body and "data-body-url" in body


def test_forced_open_row_renders_its_body_inline(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    doc = parse(c.get("/stacks?open=kiss").get_data(as_text=True))
    kiss = doc.within(doc.by_id("stackrow-kiss"))
    assert not kiss.find("div", class_="lazy-body")       # the forced-open body is inline, not deferred
    assert doc.find("div", class_="lazy-body")            # …the other rows stay deferred


def test_partial_body_scopes_per_stack_build_to_one_row(tmp_path, web):
    """The lazy-body partial builds the heavy per-stack detail for ONLY the requested stack (the
    holistic snapshot is still computed once). Guards against a per-expand full-page recompute — the
    biggest server-CPU cost of the lazy design if left unscoped."""
    c, svc = _client(web, tmp_path)
    assert len(svc.stacks()) > 1                       # there ARE other stacks it could waste work on
    calls = []
    orig = svc.deps_report
    svc.deps_report = lambda *a, **k: (calls.append(a[0] if a else None), orig(*a, **k))[1]
    c.get("/stacks/kiss/body")
    # The requested stack's detail is built exactly once ...
    assert calls.count("kiss") == 1, f"requested detail built {calls.count('kiss')}x: {calls}"
    # ... and no OTHER stack's detail is built except the ones the page-banner aggregation has
    # always walked (dependency_overview covers every INSTALLED stack — meshtastic is source-less,
    # so it is installed by construction). Anything beyond that is a per-expand full recompute.
    installed = {s.id for s in svc.stacks() if svc.is_installed(s.id)}
    assert set(calls) - {"kiss"} <= installed, f"partial recomputed unrelated stacks: {calls}"


def test_partial_body_route_renders_every_stack(tmp_path, web):
    c, svc = _client(web, tmp_path)
    for sid in [s.id for s in svc.stacks()]:
        r = c.get(f"/stacks/{sid}/body")
        assert r.status_code == 200, sid
        assert len(r.data) > 100
    assert c.get("/stacks/does-not-exist/body").status_code == 404


def test_remembered_open_row_renders_inline_and_open(tmp_path, web):
    """A row named in the lhpc_open cookie (stacks_state.js mirrors the open set there) renders
    already-open with its body inline — present at first paint, so re-opening it on reload does not
    shift the page (CLS). Every OTHER row stays deferred."""
    c, svc = _client(web, tmp_path)
    c.set_cookie("lhpc_open", "kiss")
    doc = parse(c.get("/stacks").get_data(as_text=True))
    row = doc.by_id("stackrow-kiss")
    assert row.has_attr("open")                                    # opened at first paint
    assert not doc.within(row).find("div", class_="lazy-body")     # body inline, not deferred
    assert len(doc.find("div", class_="lazy-body")) == len(svc.stacks()) - 1  # all other rows stay lazy


def test_closed_placeholder_has_noscript_fallback_link(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    body = c.get("/stacks").get_data(as_text=True)
    # A no-JS / fetch-failure fallback: a link that opens the stack server-side.
    assert "<noscript>" in body and "?open=" in body


_JOB = {"stack": "kiss", "log": "build-loraham-kiss-tnc.log", "op": "build", "target": "kiss"}
_SCENARIOS = {"closed": "/stacks", "jobs": "/stacks", "cookie": "/stacks", "open": "/stacks?open=kiss",
              "cfg": "/stacks?cfg=kiss", "dp": "/stacks?dp=kiss", "inst": "/stacks?inst=kiss"}


def _scenario(c, svc, how):
    """The URL of one way a row renders: all closed, or `kiss` forced open by a running job, the
    remembered-open cookie, or one of the four query arguments."""
    if how == "jobs":
        svc.active_jobs = lambda: [dict(_JOB)]
    if how == "cookie":
        c.set_cookie("lhpc_open", "kiss")
    return _SCENARIOS[how]


def test_a_closed_row_builds_no_settings_body(tmp_path, web):
    # The page built every row's settings body (config views, dependency reports, …: about half of a
    # Stacks render on a Zero) although a closed row's body is fetched only when it is opened.
    c, svc = _client(web, tmp_path)
    calls = []
    orig = svc.config_view
    svc.config_view = lambda sid, *a, **k: (calls.append(sid), orig(sid, *a, **k))[1]
    c.get("/stacks?open=kiss")
    assert set(calls) == {"kiss"}


@pytest.mark.parametrize("how", list(_SCENARIOS))
def test_the_stacks_page_is_the_same_byte_for_byte(tmp_path, web, monkeypatch, how):
    """The whole response equals the one the page gives when every row builds its body (what it did
    before), for every way a row renders; one test client, nothing normalised."""
    from lhpc.adapters.web import app as app_mod
    c, svc = _client(web, tmp_path)
    url = _scenario(c, svc, how)
    lazy = c.get(url).get_data(as_text=True)
    monkeypatch.setattr(app_mod, "_row_needs_body", lambda inline, only_sid: True)
    full = c.get(url).get_data(as_text=True)
    first = next((i for i, (a, b) in enumerate(zip(lazy.splitlines(), full.splitlines())) if a != b), None)
    assert lazy == full, (first, lazy.splitlines()[first] if first is not None else len(lazy) - len(full))


@pytest.mark.parametrize("jobs, cookie, args, inline", [
    ([], set(), {}, False),                                     # nothing names it: lazy
    ([_JOB], set(), {}, True),                                  # a running job
    ([], {"kiss"}, {}, True),                                   # remembered open
    ([], {"kiss"}, {"open": "chat"}, False),                    # remembered, but another row is named
    ([], {"kiss"}, {"cfg": "kiss"}, True),                      # remembered and named
    ([], set(), {"open": "kiss"}, True), ([], set(), {"cfg": "kiss"}, True),
    ([], set(), {"dp": "kiss"}, True), ([], set(), {"inst": "kiss"}, True),
    ([], set(), {"open": "chat", "dp": "kiss"}, True),          # any of the four naming it
    ([], set(), {"open": "chat"}, False),                       # another row named
])
def test_which_rows_render_their_body_inline(jobs, cookie, args, inline):
    from lhpc.adapters.web.app import _row_inline
    assert _row_inline("kiss", jobs, cookie, args) is inline
