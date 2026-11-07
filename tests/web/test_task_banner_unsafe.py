"""The task banner's dismiss for an UNSAFE task (UX, UNSAFE-X).

An unsafe banner stays until it is acted on. Its dismiss (✕) is rendered beside Recover for a
job and beside "view →" for every other kind. Failed, running and done banners keep today's
controls.

The dismiss is found by the hooks taskbanner.js binds (`button.ti-close`, `button.ti-recover`),
not by its glyph.
"""

from __future__ import annotations

import calendar
import json
import time

import htmlq
import pytest
from flask import render_template

from lhpc.core import jobresult, runtime_fs
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_LOG = "build-meshcom-qemu.log"
_ATTEMPT = "a" * 32


def _utc(delta_s=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(calendar.timegm(time.gmtime()) + delta_s))


def _svc(tmp_path, monkeypatch):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    monkeypatch.setattr(ControllerService, "stack_of", lambda self, t: "meshcom" if t else "")
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)
    return svc


def _unsafe_job(svc):
    """A build attempt's marker in state `unsafe`, written as the job child would leave it."""
    rec = {"op": "build", "target": "meshcom-qemu", "stack": "meshcom", "log": _LOG,
           "attempt_id": _ATTEMPT, "state": "unsafe", "startup_unverified": False, "source_keys": [],
           "started_at": _utc(-5), "finished_at": _utc(0), "detail": ""}
    assert jobresult._write(svc._paths, _LOG, rec)
    return rec


def _unsafe_hmac(svc):
    m = {"run_id": "b" * 32, "sid": "meshcom", "action": "renew", "phase": "unsafe", "finished": True,
         "steps": [], "finished_at": "2000-01-01T00:00:00Z"}
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"), json.dumps(m),
                            0o600)


def _items(body, kind=None, state=None):
    doc = htmlq.parse(body)
    banner = doc.by_id("task-banner")
    assert banner is not None, "no task banner rendered"
    scope = doc.within(banner)
    out = []
    for el in scope.find("div", class_="taskitem"):
        if (kind is None or el["data-kind"] == kind) and (state is None or el.has_class(f"taskitem-{state}")):
            out.append((el, doc.within(el)))
    return out


def _controls(item_scope):
    return {"dismiss": item_scope.find("button", class_="ti-close"),
            "recover": item_scope.find("button", class_="ti-recover"),
            "view": item_scope.find("a", class_="ti-view")}


def _render(client, tasks):
    with client.application.test_request_context("/"):
        return render_template("_task_banner.html", tasks=tasks)


def _task(kind, state, **extra):
    return {"kind": kind, "run_id": "r" * 8, "attempt_id": "t" * 8 if kind == "job" else "",
            "state": state, "label": f"{kind} task", "hint": "", "href": "/x", **extra}


# ---- T-UX1: the render ----------------------------------------------------------------------------

def test_an_unsafe_job_banner_offers_dismiss_beside_recover(tmp_path, monkeypatch, web):
    """T-UX1 / X1 (red at dev 6c69faa2, X13): the dashboard's banner for an unsafe build job holds
    both the dismiss and Recover, and the item carries what the existing dismiss post sends."""
    svc = _svc(tmp_path, monkeypatch)
    _unsafe_job(svc)
    body = web(service_factory=lambda: svc).get("/").get_data(as_text=True)
    items = _items(body, kind="job", state="unsafe")
    assert len(items) == 1
    el, scope = items[0]
    c = _controls(scope)
    assert len(c["recover"]) == 1, "Recover must stay the explicit action"
    assert len(c["dismiss"]) == 1, "an unsafe job's banner has no dismiss"
    assert c["dismiss"][0]["type"] == "button" and c["dismiss"][0]["aria-label"] == "Dismiss"
    assert (el["data-run"], el["data-attempt"]) == (_LOG, _ATTEMPT)


def test_the_banner_template_renders_both_controls_for_an_unsafe_job(web):
    """T-UX1 / X1: the template itself, for a hand-built unsafe job task."""
    body = _render(web(), [_task("job", "unsafe")])
    (_, scope), = _items(body)
    c = _controls(scope)
    assert len(c["dismiss"]) == 1 and len(c["recover"]) == 1


@pytest.mark.parametrize("kind", ["hmac", "auto-install", "boot-restore"])
def test_an_unsafe_banner_of_every_other_kind_offers_dismiss_beside_view(web, kind):
    """X5 (adopted): every unsafe banner, whatever its kind, has the dismiss beside "view →"."""
    body = _render(web(), [_task(kind, "unsafe")])
    (_, scope), = _items(body)
    c = _controls(scope)
    assert len(c["dismiss"]) == 1 and len(c["view"]) == 1


def test_an_unsafe_hmac_banner_on_the_dashboard_offers_dismiss(tmp_path, monkeypatch, web):
    """X5 through the dashboard: an unsafe HMAC apply's banner has the dismiss beside "view →"."""
    svc = _svc(tmp_path, monkeypatch)
    _unsafe_hmac(svc)
    body = web(service_factory=lambda: svc).get("/").get_data(as_text=True)
    (_, scope), = _items(body, kind="hmac", state="unsafe")
    c = _controls(scope)
    assert len(c["dismiss"]) == 1 and len(c["view"]) == 1


@pytest.mark.parametrize("no_dismiss, shown", [(True, 0), (False, 1)])
def test_failed_banner_dismiss_follows_no_dismiss(web, no_dismiss, shown):
    """X3 (a control, unchanged): a failed banner has the dismiss unless `no_dismiss` is set."""
    extra = {"no_dismiss": True} if no_dismiss else {}
    body = _render(web(), [_task("boot-restore", "failed", **extra)])
    (_, scope), = _items(body)
    assert len(_controls(scope)["dismiss"]) == shown


@pytest.mark.parametrize("kind", ["job", "hmac"])
@pytest.mark.parametrize("state", ["running", "done"])
def test_running_and_done_banners_offer_no_dismiss_and_no_recover(web, kind, state):
    """X4 (a control, unchanged): running and done banners do not stay; neither control."""
    body = _render(web(), [_task(kind, state)])
    (_, scope), = _items(body)
    c = _controls(scope)
    assert c["dismiss"] == [] and c["recover"] == []

