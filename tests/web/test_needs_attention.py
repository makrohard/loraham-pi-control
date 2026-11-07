"""W272/W273 through the console: Home's one "Needs attention" banner lists the important conditions, one line each
linked to its row; one ✕ hides the set until it changes, and the +20 dBm permission line comes back on every visit.
Each condition also shows in its own row (the stack's on the Apps page, the radio's on the Hardware list). Home has
no radio columns any more (W250/DEC-W57)."""
from __future__ import annotations

import re

import radios_fixture

from lhpc.core import radio_start, radios
from lhpc.core.model import ComponentStatus, RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import Snapshot, StackStatus

LOST = "source not delivering (gpsd: device activation failed)"
ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))


def _svc(tmp_path, monkeypatch, *, states=None, evidence=None, hp=None, row_attention=None, tasks=()):
    """A box whose meshcore stack is in `states`, whose bands' +20 dBm state is `hp`, whose radio rows carry
    `row_attention` ({rid: [line]}) and whose task feed is `tasks`."""
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    st = svc.stack("meshcore")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=st)
    for comp in st.components:
        ss.components[comp.id] = ComponentStatus(component_id=comp.id, run_state=(states or {}).get(comp.id, RunState.STOPPED),
                                                 evidence=dict((evidence or {}).get(comp.id, {})))
    snap.stacks.append(ss)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *a, **k: snap)
    monkeypatch.setattr(ControllerService, "high_power_state",
                        lambda self, band, view=None: {"warn": band in (hp or ()), "mismatch": ""})
    monkeypatch.setattr(ControllerService, "running_tasks", lambda self: list(tasks))
    real = radio_start.hardware_view

    def view(svc_):
        v = real(svc_)
        for r in v["rows"]:
            r["attention"] = (row_attention or {}).get(r["rid"], [])
        return v
    monkeypatch.setattr(radio_start, "hardware_view", view)
    return svc


def _home(web, svc):
    return web(service_factory=lambda: svc).get("/").get_data(as_text=True)


def _lines(page):
    box = re.search(r'<div class="depnote depnote-warn" id="needs-attention">(.*?)</div>', page, re.S)
    return re.findall(r'<li data-attention="[^"]*"><a href="([^"]*)">([^<]*)</a></li>', box.group(1)) if box else []


def test_a_degraded_stack_is_one_line_linked_to_its_row(web, tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED})
    (href, line), = _lines(_home(web, svc))
    assert (href, line) == ("/stacks?open=meshcore#stackrow-meshcore", "MeshCore (OpenHop) is degraded")


def test_the_stack_row_names_its_degraded_component_and_why(web, tmp_path, monkeypatch):
    # W14's reason moved from Home's radio card to the stack's own row on the Apps page.
    svc = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.RUNNING, "meshcore-gps": RunState.DEGRADED},
               evidence={"meshcore-gps": {"gps.feed": LOST, "gps.fix": ""}})
    page = web(service_factory=lambda: svc).get("/stacks?open=meshcore").get_data(as_text=True)
    assert re.search(r'data-note="meshcore-gps">⚠ meshcore-gps: GPS feed: ' + re.escape(LOST), page), page[:200]


def test_a_radios_important_note_and_the_usb_setup_are_listed(web, tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, row_attention={"r2": ["daemon offline while meshcore needs it"]})
    assert ("/hardware/radio/r2", "r2: daemon offline while meshcore needs it") in _lines(_home(web, svc))


def test_an_unsafe_task_is_listed(web, tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, tasks=[{"kind": "job", "run_id": "j1", "state": "unsafe", "label": "Build kiss",
                                              "href": "/jobs/j1"}])
    assert ("/jobs/j1", "Build kiss: unsafe — needs attention") in _lines(_home(web, svc))


def test_nothing_is_listed_for_a_stack_the_operator_stopped(web, tmp_path, monkeypatch):
    page = _home(web, _svc(tmp_path, monkeypatch))
    assert 'id="needs-attention"' not in page     # W285: Home keeps its radio cards (DEC-W57's removal withdrawn)


def test_one_x_hides_the_set_until_it_changes(web, tmp_path, monkeypatch, csrf):
    svc = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED})
    c = web(service_factory=lambda: svc)
    assert _dismiss_shown(c, csrf).status_code == 302
    assert _lines(c.get("/").get_data(as_text=True)) == []                  # the same set: hidden
    svc2 = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.FAILED})
    assert [line for _h, line in _lines(_home(web, svc2))] == ["MeshCore (OpenHop) is failed"]   # a new set shows again


def test_the_high_power_line_comes_back_on_every_visit(web, tmp_path, monkeypatch, csrf):
    svc = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED}, hp=("433",))
    c = web(service_factory=lambda: svc)
    assert _dismiss_shown(c, csrf).status_code == 302
    page = c.get("/").get_data(as_text=True)
    assert _lines(page) == [("/hardware/radio/r1", "+20 dBm permission enabled on 433 MHz")]
    assert 'action="/attention/dismiss"' not in page                       # nothing left to close


def test_without_the_token_the_x_does_nothing(web, tmp_path, monkeypatch):
    c = web(service_factory=lambda: _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED}))
    assert c.post("/attention/dismiss", data={"_csrf": "wrong"}).status_code == 400
    assert _lines(c.get("/").get_data(as_text=True))


def test_a_fault_in_the_collector_shows_no_banner_never_an_error_page(web, tmp_path, monkeypatch):
    from lhpc.core import attention
    svc = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED})
    monkeypatch.setattr(attention, "conditions", lambda s: 1 / 0)
    r = web(service_factory=lambda: svc).get("/")
    assert r.status_code == 200 and 'id="needs-attention"' not in r.get_data(as_text=True)


def _dismiss_shown(c, csrf):
    """The ✕ as the page offers it: its form's fields (the shown set's identity included) posted back."""
    page = c.get("/").get_data(as_text=True)
    form = re.search(r'<form method="post" action="/attention/dismiss".*?</form>', page, re.S).group(0)
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
    return c.post("/attention/dismiss", data={**fields, "_csrf": csrf(c, "/")})


def test_c23_b1_a_fault_that_recovers_and_returns_shows_again(web, tmp_path, monkeypatch, csrf):
    # Gate 1 C23 B1: the ✕ hid {MeshCore degraded}; it recovered (nothing listed), then degraded again: it shows.
    svc = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED})
    c = web(service_factory=lambda: svc)
    assert _dismiss_shown(c, csrf).status_code == 302 and _lines(c.get("/").get_data(as_text=True)) == []
    assert _lines(_home(web, _svc(tmp_path, monkeypatch))) == []                         # recovered
    again = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED})
    assert [line for _h, line in _lines(_home(web, again))] == ["MeshCore (OpenHop) is degraded"]


def test_c23_b2_the_x_hides_the_set_it_showed_never_one_that_arrived_since(web, tmp_path, monkeypatch, csrf):
    # Gate 1 C23 B2: the page showed {degraded}; before the ✕ is pressed an unsafe task arrives. The ✕ hides the shown
    # set only, so the new set (degraded + unsafe) stays visible.
    svc = _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED})
    c = web(service_factory=lambda: svc)
    page = c.get("/").get_data(as_text=True)
    form = re.search(r'<form method="post" action="/attention/dismiss".*?</form>', page, re.S).group(0)
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
    _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED},
         tasks=[{"kind": "job", "run_id": "j1", "state": "unsafe", "label": "Build kiss", "href": "/jobs/j1"}])
    c.post("/attention/dismiss", data={**fields, "_csrf": csrf(c, "/")})
    assert len(_lines(c.get("/").get_data(as_text=True))) == 2


def test_c23_a_posted_identity_that_is_no_set_identity_hides_nothing(web, tmp_path, monkeypatch, csrf):
    c = web(service_factory=lambda: _svc(tmp_path, monkeypatch, states={"meshcore-node": RunState.DEGRADED}))
    c.post("/attention/dismiss", data={"_csrf": csrf(c, "/"), "set": "x" * 4096})
    assert _lines(c.get("/").get_data(as_text=True)) and not (tmp_path / "state" / "attention-dismissed").exists()
