"""Week item 12 (PLAN-WEBCLIENT-LINE-SELECTOR-v1, decided A in DEC-W19): the Meshtastic web client's line from the
console. The meshtastic stack's web-client row shows the installed client and a selector of the three lines in his
plain words (binary / stable / dev); Switch opens the existing plan page (the exact version or why not, read only
then), and Confirm installs that line. Nothing is installed before Confirm. The meshtastic stack's refusal of a
source channel on a binary install names this control.
"""
from __future__ import annotations

from htmlq import parse

from lhpc.core import web_client
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService


def _console(web, tmp_path, *, line="", version=""):
    p = Paths(runtime_root=tmp_path)
    if version:
        (tmp_path / web_client.ROOT_REL).mkdir(parents=True, exist_ok=True)
        web_client.write_provenance(p, {"web_version": version, "selector": line, "meshtasticd": ""})
    svc = ControllerService(system=FakeSystem().system, paths=p)
    return web(service_factory=lambda: svc), svc


def _row(client):
    doc = parse(client.get("/stacks/meshtastic/body").get_data(as_text=True))
    assert doc.present("webclient-line"), "no web-client control in the meshtastic stack body"
    return doc, doc.within(doc.by_id("webclient-line"))


def test_r1_the_row_offers_the_three_lines_with_the_pin_and_the_current_line(web, tmp_path):
    """RED before: the row had only Build."""
    client, svc = _console(web, tmp_path, line="stable", version="2.7.1")
    _doc, row = _row(client)
    opts = row.find("option")
    pin = web_client.pin(web_client.component(svc))[0]
    assert [o["value"] for o in opts] == ["binary", "stable", "dev"]
    assert opts[0].text.split()[:2] == ["binary", pin]
    assert [o["value"] for o in opts if o.has_attr("selected")] == ["stable"]
    assert "2.7.1" in row.text and not any(o.has_attr("disabled") for o in opts)


def test_r2_without_a_binary_index_stable_and_dev_are_disabled_with_the_reason(web, tmp_path, monkeypatch):
    """RED before. (binary_spec is the box's declaration; stubbed so this box has none.)"""
    monkeypatch.setattr(ControllerService, "binary_spec", lambda self, sid: None)
    client, _svc = _console(web, tmp_path)
    _doc, row = _row(client)
    off = {o["value"]: o.text for o in row.find("option") if o.has_attr("disabled")}
    assert sorted(off) == ["dev", "stable"] and all("no binary index" in t for t in off.values())


def test_r3_switch_opens_the_plan_page_and_installs_nothing(web, csrf, tmp_path):
    """RED before for the page's own choices (the meshtastic stack's four channels were offered; pinned is refused
    for the web client): the plan page offers exactly binary / stable / dev, the chosen one selected."""
    client, _svc = _console(web, tmp_path, line="binary", version="2.7.2")
    before = (tmp_path / web_client.PROVENANCE_REL).read_text()
    r = client.post("/action", data={"_csrf": csrf(client, "/stacks?open=meshtastic"), "op": "update",
                                     "target": "meshtastic-web", "source": "binary"})
    doc = parse(r.get_data(as_text=True))
    assert r.status_code == 200 and "Plan: install the known-working web client" in doc.text
    sel = doc.find("select", name="source")[0]
    opts = doc.within(sel).find("option")
    assert [(o["value"], o.text.strip()) for o in opts] == [("binary", "binary"), ("stable", "stable"), ("dev", "dev")]
    assert [o["value"] for o in opts if o.has_attr("selected")] == ["binary"]
    assert (tmp_path / web_client.PROVENANCE_REL).read_text() == before


def test_r4_control_confirm_installs_the_chosen_line(web, csrf, tmp_path, monkeypatch):
    """The confirmed POST reaches web_client.install with that line (the install itself: tests of web_client)."""
    calls = []

    def install(svc, selector_="", apply=False):
        calls.append((selector_, apply))
        return ActionResult(True, f"Installed the web client ({selector_}).")
    monkeypatch.setattr(web_client, "install", install)
    client, _svc = _console(web, tmp_path, line="binary", version="2.7.2")
    r = client.post("/action", data={"_csrf": csrf(client, "/stacks?open=meshtastic"), "op": "update",
                                     "target": "meshtastic-web", "source": "dev", "confirmed": "yes"})
    assert r.status_code == 302 and calls == [("dev", True)]


def test_r5_control_without_csrf_nothing_happens(web, tmp_path):
    client, _svc = _console(web, tmp_path)
    assert client.post("/action", data={"op": "update", "target": "meshtastic-web", "source": "dev",
                                        "confirmed": "yes"}).status_code == 400


def test_r6_control_a_line_outside_the_three_is_refused_and_changes_nothing(web, csrf, tmp_path):
    client, _svc = _console(web, tmp_path, line="binary", version="2.7.2")
    before = (tmp_path / web_client.PROVENANCE_REL).read_text()
    r = client.post("/action", data={"_csrf": csrf(client, "/stacks?open=meshtastic"), "op": "update",
                                     "target": "meshtastic-web", "source": "pinned", "confirmed": "yes"},
                    follow_redirects=True)
    assert "no compiled lane" in r.get_data(as_text=True)
    assert (tmp_path / web_client.PROVENANCE_REL).read_text() == before


def test_r7_the_meshtastic_stacks_channel_refusal_names_the_web_client_control(web, csrf, tmp_path, monkeypatch):
    """RED before: the refusal named only a CLI install. (on_binary_channel stubbed: this box's stack is binary.)"""
    monkeypatch.setattr(ControllerService, "on_binary_channel", lambda self, sid: sid == "meshtastic")
    client, _svc = _console(web, tmp_path)
    r = client.post("/action", data={"_csrf": csrf(client, "/stacks?open=meshtastic"), "op": "update",
                                     "target": "meshtastic", "source": "dev", "confirmed": "yes"},
                    follow_redirects=True)
    assert "use the Web client control (binary / stable / dev)" in " ".join(parse(r.get_data(as_text=True)).text.split())


def test_r8_control_another_stack_has_no_web_client_control(web, tmp_path):
    client, _svc = _console(web, tmp_path)
    doc = parse(client.get("/stacks/kiss/body").get_data(as_text=True))
    assert not doc.present("webclient-line")
