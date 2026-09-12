"""The RF-Logs submenu on a stack card, its one-key switch route, and the RF log page with its
switcher and confirmed Clear — all driven by the registry, authorized by nothing else."""

from __future__ import annotations

import os
import re

import pytest

from htmlq import parse
from lhpc.adapters.web.app import create_app
from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _app(tmp_path):
    def factory():
        return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(service_factory=factory).test_client()


def _csrf(client, path="/stacks"):
    m = re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True))
    return m.group(1) if m else ""


def _logs(tmp_path, name, text=""):
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / name).write_text(text)
    return tmp_path / "logs" / name


def _links(doc, target, job):
    return doc.find("a", href=f"/logs/{target}?job={job}")


# ---- the submenu -------------------------------------------------------------------------------

def test_graywolf_card_carries_the_kiss_switch_and_link(tmp_path):
    doc = parse(_app(tmp_path).get("/stacks?open=graywolf").get_data(as_text=True))
    assert doc.present("stack-rflog-graywolf")
    assert doc.field_default("value") == "on"
    assert doc.by_id("rflog-switch-graywolf")["name"] == "value"
    assert _links(doc, "loraham-kiss-tnc", "rf-kiss.log")           # the registry's target/job
    assert not _links(doc, "graywolf", "rf-graywolf.log")


def test_daemon_card_links_both_band_logs(tmp_path):
    doc = parse(_app(tmp_path).get("/stacks?open=daemon").get_data(as_text=True))
    assert doc.present("stack-rflog-daemon")
    assert _links(doc, "loraham-daemon", "rf-daemon-433.log")
    assert _links(doc, "loraham-daemon", "rf-daemon-868.log")


@pytest.mark.parametrize("sid", ["chat", "kiss", "voice"])
def test_stacks_without_an_rf_log_have_no_submenu(tmp_path, sid):
    doc = parse(_app(tmp_path).get(f"/stacks?open={sid}").get_data(as_text=True))
    assert doc.present(f"stackrow-{sid}") and not doc.present(f"stack-rflog-{sid}")


def test_switch_saves_one_key_on_the_owner_and_merges(tmp_path):
    c = _app(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("kiss", values={"rx_only": "on"}).ok
    before = cfgmod.load_stack_config(svc._paths, "kiss", "")
    r = c.post("/stacks/graywolf/rflog", data={"_csrf": _csrf(c), "value": "off"})
    assert r.status_code == 302
    after = cfgmod.load_stack_config(svc._paths, "kiss", "")
    assert after["rf_log"] == "off" and {k: v for k, v in after.items() if k != "rf_log"} == before
    assert "rf_log" not in cfgmod.load_stack_config(svc._paths, "graywolf", "")
    doc = parse(c.get("/stacks?open=graywolf").get_data(as_text=True))
    assert doc.field_default("value") == "off"


def test_switch_requires_csrf_and_a_known_stack(tmp_path):
    c = _app(tmp_path)
    assert c.post("/stacks/graywolf/rflog", data={"value": "off"}).status_code == 400
    assert c.post("/stacks/nope/rflog", data={"_csrf": _csrf(c), "value": "off"}).status_code == 404
    r = c.post("/stacks/chat/rflog", data={"_csrf": _csrf(c), "value": "off"})   # no RF log
    assert r.status_code == 302
    assert not (tmp_path / "config" / "stacks").exists()


def test_submenu_says_restart_required_after_a_live_change(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: sid == "kiss")
    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid, d="": "433")
    c = _app(tmp_path)
    assert c.post("/stacks/graywolf/rflog", data={"_csrf": _csrf(c), "value": "off"}).status_code == 302
    body = c.get("/stacks?open=graywolf").get_data(as_text=True)
    assert re.search(r'id="stack-rflog-graywolf".*?restart required', body, re.S)


# ---- the page ----------------------------------------------------------------------------------

def test_rf_log_page_has_the_switcher_and_a_scoped_clear_form(tmp_path):
    c = _app(tmp_path)
    doc = parse(c.get("/logs/loraham-kiss-tnc?job=rf-kiss.log").get_data(as_text=True))
    nav = doc.find("nav", **{"aria-label": "RF logs"})
    assert nav
    current = doc.find("a", **{"aria-current": "page"})
    assert [a["href"] for a in current] == ["/logs/loraham-kiss-tnc?job=rf-kiss.log"]
    for target, job in (("loraham-daemon", "rf-daemon-433.log"), ("loraham-daemon", "rf-daemon-868.log"),
                        ("meshcom-bridge", "rf-meshcom.log"), ("meshtastic", "rf-meshtastic.log"),
                        ("meshcore-node", "rf-meshcore.log"), ("rns", "rf-reticulum.log")):
        assert _links(doc, target, job)
    assert doc.find("form", action="/logs/loraham-kiss-tnc/clear")
    assert doc.find("input", name="job")[0]["value"] == "rf-kiss.log"


@pytest.mark.parametrize("url", ["/logs/loraham-kiss-tnc?job=rf-made-up.log",
                                 "/logs/loraham-daemon?job=rf-kiss.log",       # not this writer's
                                 "/logs/loraham-kiss-tnc"])
def test_other_logs_get_neither_switcher_nor_clear(tmp_path, url):
    doc = parse(_app(tmp_path).get(url).get_data(as_text=True))
    assert not doc.find("nav", **{"aria-label": "RF logs"})
    assert not doc.find("input", name="job")


def test_page_and_api_show_both_segments(tmp_path):
    _logs(tmp_path, "rf-kiss.log.1", "old\n")
    _logs(tmp_path, "rf-kiss.log", "new\n")
    c = _app(tmp_path)
    assert c.get("/api/logs/loraham-kiss-tnc?job=rf-kiss.log").get_json()["lines"] == ["old", "new"]
    assert "(no log file yet)" in c.get("/logs/loraham-daemon?job=rf-daemon-433.log").get_data(as_text=True)


def test_clear_empties_only_the_shown_log(tmp_path):
    a = _logs(tmp_path, "rf-kiss.log", "a\n")
    a1 = _logs(tmp_path, "rf-kiss.log.1", "old\n")
    b = _logs(tmp_path, "rf-meshcom.log", "b\n")
    c = _app(tmp_path)
    r = c.post("/logs/loraham-kiss-tnc/clear", data={"_csrf": _csrf(c), "job": "rf-kiss.log"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/logs/loraham-kiss-tnc?job=rf-kiss.log")
    assert a.read_text() == "" and not a1.exists() and b.read_text() == "b\n"


@pytest.mark.parametrize("target,job,status", [
    ("loraham-kiss-tnc", "rf-made-up.log", 404),     # absent from the registry, whatever its name
    ("loraham-daemon", "rf-kiss.log", 404),          # registered, but not this writer's
    ("loraham-kiss-tnc", "../rf-kiss.log", 404),
    ("loraham-kiss-tnc", "rf-kiss.txt", 404),
    ("bogus", "rf-kiss.log", 404),
])
def test_clear_refuses_everything_outside_the_registry(tmp_path, target, job, status):
    keep = [_logs(tmp_path, n, "keep\n") for n in ("rf-kiss.log", "rf-made-up.log", "rf-kiss.txt")]
    c = _app(tmp_path)
    assert c.post(f"/logs/{target}/clear", data={"_csrf": _csrf(c), "job": job}).status_code == status
    assert all(p.read_text() == "keep\n" for p in keep)


def test_clear_requires_csrf_and_refuses_a_symlink(tmp_path):
    outside = tmp_path / "outside.log"
    outside.write_text("precious\n")
    (tmp_path / "logs").mkdir()
    os.symlink(outside, tmp_path / "logs" / "rf-meshcom.log")
    c = _app(tmp_path)
    assert c.post("/logs/meshcom-bridge/clear", data={"job": "rf-meshcom.log"}).status_code == 400
    assert c.post("/logs/meshcom-bridge/clear",
                  data={"_csrf": _csrf(c), "job": "rf-meshcom.log"}).status_code == 302
    assert outside.read_text() == "precious\n"
