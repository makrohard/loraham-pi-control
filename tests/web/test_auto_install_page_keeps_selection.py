"""Week item 11 (W206, W207): the Auto-Install page keeps the operator's selection, and its task reads
"Auto-install".

After Install with only openHop chosen, the page came back with every box checked again. The selection a POST
submitted is now kept in the signed cookie and each row renders from it; a saved row that no longer fits is ignored
(that row shows its default). The running/finished task in the banner is labelled "Auto-install" (the banner
appends "running…", "finished", "failed"). The dependency auto-ticking is gone from the page script; that is
the browser lane's.
"""
from __future__ import annotations

import pytest
from htmlq import parse

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _client(web, tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return web(service_factory=lambda: svc), svc


def _only(svc, tok, chosen, **per):
    """A POST payload installing only the stacks in `chosen`; `per` = {sid: {"version": …, "tests": True}}."""
    data = {"_csrf": tok}
    for r in svc.auto_install_rows():
        sid = r["id"]
        data[f"version:{sid}"] = per.get(sid, {}).get("version", r["default_channel"])
        if sid in chosen:
            data[f"install:{sid}"] = "yes"
        if per.get(sid, {}).get("tests"):
            data[f"tests:{sid}"] = "yes"
    return data


def _boxes(client):
    doc = parse(client.get("/auto-install").get_data(as_text=True))
    install = {i["name"].split(":", 1)[1]: i.has_attr("checked") for i in doc.find("input", class_="ai-install")}
    return doc, install


@pytest.fixture
def no_spawn(monkeypatch):
    # The run itself is the auto-install driver's (tests/install/test_auto_install.py); here only the page.
    spawned = []
    monkeypatch.setattr(ControllerService, "spawn_auto_install_job",
                        lambda self, selection: (spawned.append(selection), ("job", ""))[1])
    return spawned


def test_r1_after_install_with_only_meshcore_only_meshcore_stays_checked(web, csrf, tmp_path, monkeypatch, no_spawn):
    """RED before: every Install box came back checked. (The daemon here is not ready, so the server's dependency
    refusal is stood in for: on his box the daemon was installed and the run started.)"""
    monkeypatch.setattr(ControllerService, "auto_install_check_selection", lambda self, selection: [])
    client, svc = _client(web, tmp_path)
    client.post("/auto-install/start", data=_only(svc, csrf(client, "/auto-install"), {"meshcore"}))
    assert len(no_spawn) == 1                                          # the run was started
    doc, install = _boxes(client)
    assert {sid for sid, on in install.items() if on} == {"meshcore"}
    assert not doc.by_id("ai-all-install").has_attr("checked")


def test_r2_after_a_refused_install_the_submitted_selection_stays(web, csrf, tmp_path, no_spawn):
    """RED before. The real refusal: meshcore without its daemon, which is not ready here."""
    client, svc = _client(web, tmp_path)
    r = client.post("/auto-install/start", data=_only(svc, csrf(client, "/auto-install"), {"meshcore"}),
                    follow_redirects=True)
    assert "dependency skipped and not ready" in r.get_data(as_text=True) and no_spawn == []
    _doc, install = _boxes(client)
    assert {sid for sid, on in install.items() if on} == {"meshcore"}


def test_r3_the_version_and_the_tests_choice_stay_too(web, csrf, tmp_path, monkeypatch, no_spawn):
    monkeypatch.setattr(ControllerService, "auto_install_check_selection", lambda self, selection: [])
    client, svc = _client(web, tmp_path)
    row = next(r for r in svc.auto_install_rows() if r["testable"] and len(r["channels"]) > 1)
    other = next(ch for ch in row["channels"] if ch != row["default_channel"])
    client.post("/auto-install/start", data=_only(svc, csrf(client, "/auto-install"), {row["id"]},
                                                 **{row["id"]: {"version": other, "tests": True}}))
    doc, _install = _boxes(client)
    assert doc.field_default(f"version:{row['id']}") == other                      # RED before: the default
    assert doc.find("input", name=f"tests:{row['id']}")[0].has_attr("checked")    # RED before: unchecked


@pytest.mark.parametrize("saved", [None, "x", [], {"meshcore": "yes"}, {"meshcore": {"install": "yes"}},
                                   {"meshcore": {"install": False, "version": "nightly", "tests": False, "tx": False}},
                                   {"nosuch": {"install": False, "version": "pinned", "tests": False, "tx": False}},
                                   {"meshcore": {"install": 0, "version": "pinned", "tests": False, "tx": False}}],
                         ids=["none", "str", "list", "row-str", "flag-str", "bad-version", "unknown-stack", "flag-int"])
def test_r4_control_a_saved_selection_that_does_not_fit_shows_the_defaults(web, tmp_path, saved):
    client, _svc = _client(web, tmp_path)
    if saved is not None:
        with client.session_transaction() as s:
            s["_ai_selection"] = saved
    doc, install = _boxes(client)
    assert all(install.values()) and doc.by_id("ai-all-install").has_attr("checked")


def test_r5_control_the_first_visit_checks_every_install_box(web, tmp_path):
    client, _svc = _client(web, tmp_path)
    doc, install = _boxes(client)
    assert install and all(install.values()) and doc.by_id("ai-all-install").has_attr("checked")


@pytest.mark.parametrize("state", ["running", "interrupted"])
def test_r6_the_auto_install_task_is_labelled_auto_install(web, tmp_path, monkeypatch, state):
    """RED before: "Install / build all stacks". The banner appends the state ("finished")."""
    monkeypatch.setattr(ControllerService, "auto_install_status",
                        lambda self: {"run_id": "a" * 32, "state": state, "finished_at": ""})
    client, _svc = _client(web, tmp_path)
    tasks = [t for t in client.get("/api/tasks").get_json()["tasks"] if t["kind"] == "auto-install"]
    assert [t["label"] for t in tasks] == ["Auto-install"]


def test_r7_control_a_saved_tests_or_tx_choice_is_kept_only_where_the_stack_has_it(web, tmp_path):
    """A row without host tests (or without a TX test) never renders its box checked, whatever was saved."""
    client, svc = _client(web, tmp_path)
    rows = svc.auto_install_rows()
    bare = next(r for r in rows if not r["testable"] and not r["tx_capable"])
    with client.session_transaction() as s:
        s["_ai_selection"] = {bare["id"]: {"install": True, "version": bare["default_channel"], "tests": True, "tx": True}}
    doc, _install = _boxes(client)
    assert not doc.find("input", name=f"tests:{bare['id']}")[0].has_attr("checked")
    assert not doc.find("input", name=f"tx:{bare['id']}")[0].has_attr("checked")


def test_r8_a_crafted_long_version_is_not_kept_and_the_cookie_stays_small(web, csrf, tmp_path, no_spawn):
    """A version no channel has (5000 characters) is stored as "", so the signed cookie keeps its CSRF token."""
    client, svc = _client(web, tmp_path)
    data = _only(svc, csrf(client, "/auto-install"), {"meshcore"})
    data["version:meshcore"] = "x" * 5000
    client.post("/auto-install/start", data=data)
    with client.session_transaction() as s:
        assert s["_ai_selection"]["meshcore"]["version"] == ""
        assert s.get("_csrf")
    cookie = client.get_cookie(client.application.config["SESSION_COOKIE_NAME"])
    assert cookie is not None and len(cookie.value) < 4000
