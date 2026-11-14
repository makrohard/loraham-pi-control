"""The auto-install page and the task banner show the CURRENT run (A3). While a new run is starting over the last
run's terminal marker (its reservation live, its own marker not written yet), neither says the last run completed:
the page shows the starting card alone, the banner the new run as running. A run in which no stack had its tests on
says so in one line and leaves every Tests cell empty (Q-B2-3 B); a run with tests on keeps each row's text."""
from __future__ import annotations

import os
import time

import pytest
from htmlq import parse

from lhpc.core import auto_install as ai
from lhpc.core import procident
from lhpc.core.services import ControllerService

A, B = "a" * 32, "b" * 32
STACKS = ("daemon", "kiss", "reticulum")
OFF = "tests were disabled for this run"


def _marker(svc, run, state, rows=None, tests=None, detail="skipped (tests disabled)"):
    m = ai.new_marker(run, "install", "pinned", False, False, [{"id": s} for s in STACKS])
    m["state"] = state
    m["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if state in ai.TERMINAL_OK else ""
    for r in m["stacks"]:
        r["status"] = (rows or {}).get(r["id"], "success")
        r["selected"] = {"version": "pinned", "tests": (tests or {}).get(r["id"], False), "tx": False}
        r["tests"] = {"ran": False, "ok": None, "detail": detail}
    assert ai.write_marker(svc._paths, m)
    return m


@pytest.fixture
def page(tmp_path, web, monkeypatch):
    def make(running=False):
        monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: running)
        c = web()
        return c, c.get("/auto-install").get_data(as_text=True)
    return make


def _svc(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    return svc


def _cells(html):
    doc = parse(html)
    table = doc.within(doc.find("table", id="ai-tasks")[0])
    return [r.find("td")[2].text.strip() for r in (table.within(tr) for tr in table.find("tr")) if r.find("td")]


@pytest.mark.needs_session
def test_a_starting_run_replaces_the_last_runs_outcome_on_the_page_and_the_banner(tmp_path, page):
    svc = _svc(tmp_path)
    _marker(svc, A, "completed")
    ok, why = ai.write_reservation(svc._paths, B, os.getpid(), procident.proc_identity(os.getpid()), phase="claimed")
    assert ok, why
    c, html = page()
    assert "Run starting" in html and "run completed" not in html, html
    tasks = [(t["run_id"], t["state"]) for t in c.get("/api/tasks").get_json()["tasks"] if t["kind"] == "auto-install"]
    assert tasks == [(B, "running")], tasks


@pytest.mark.needs_session
@pytest.mark.parametrize("reservation", ["none", "dead", "same-run"])
def test_the_last_run_shows_its_outcome_when_no_new_run_starts(tmp_path, page, reservation):
    # a reservation whose process is gone, or one of the last run itself, starts nothing new
    svc = _svc(tmp_path)
    _marker(svc, A, "completed")
    if reservation != "none":
        pid = 999999 if reservation == "dead" else os.getpid()
        ident = procident.proc_identity(os.getpid())
        assert ai.write_reservation(svc._paths, B if reservation == "dead" else A, pid, ident, phase="claimed")[0]
    c, html = page()
    assert "run completed" in html and "Run starting" not in html
    assert [(t["run_id"], t["state"]) for t in c.get("/api/tasks").get_json()["tasks"]
            if t["kind"] == "auto-install"] == [(A, "done")]


@pytest.mark.needs_session
def test_a_running_run_stays_the_current_one_whatever_reservation_is_live(tmp_path, page):
    svc = _svc(tmp_path)
    _marker(svc, A, "running", rows={"reticulum": "building"})
    assert ai.write_reservation(svc._paths, B, os.getpid(), procident.proc_identity(os.getpid()), phase="claimed")[0]
    c, html = page(running=True)
    assert "Run starting" not in html and "run in progress" in html
    assert [(t["run_id"], t["state"]) for t in c.get("/api/tasks").get_json()["tasks"]
            if t["kind"] == "auto-install"] == [(A, "running")]


@pytest.mark.parametrize("state, running", [("completed", False), ("running", True)])
def test_a_run_without_tests_says_so_once_and_leaves_the_tests_cells_empty(tmp_path, page, state, running):
    svc = _svc(tmp_path)
    _marker(svc, A, state, rows={"reticulum": "building"} if running else None)
    _c, html = page(running=running)
    assert html.count(OFF) == 1
    assert _cells(html) == ["", "", ""], _cells(html)
    assert ("run in progress" in html) is running                 # a building run never reads as finished


@pytest.mark.parametrize("detail", ["skipped (tests disabled)", "deferred — 1 test(s) need the running stack"])
def test_a_run_with_tests_on_for_some_stacks_keeps_each_rows_text(tmp_path, page, detail):
    svc = _svc(tmp_path)
    _marker(svc, A, "completed", tests={"kiss": True}, detail=detail)
    _c, html = page()
    assert OFF not in html
    assert _cells(html) == [detail] * 3, _cells(html)
