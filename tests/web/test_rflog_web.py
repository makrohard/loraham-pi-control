"""The RF-Logs submenu on a stack card, its one-key switch route, and the RF log page with its
switcher and confirmed Clear — all driven by the registry, authorized by nothing else."""

from __future__ import annotations

import os

import pytest

from htmlq import parse
from lhpc.core import config as cfgmod, rflog
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _logs(tmp_path, name, text=""):
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / name).write_text(text)
    return tmp_path / "logs" / name


def _links(doc, target, job):
    return doc.find("a", href=f"/logs/{target}?job={job}")


def _scripts(doc):
    return [s["src"] or "" for s in doc.find("script")]


# ---- the submenu -------------------------------------------------------------------------------

def test_graywolf_card_carries_the_kiss_switch_and_link(web):
    doc = parse(web().get("/stacks?open=graywolf").get_data(as_text=True))
    assert doc.present("stack-rflog-graywolf")
    assert doc.field_default("value") == "on"
    assert doc.by_id("rflog-switch-graywolf")["name"] == "value"
    assert _links(doc, "loraham-kiss-tnc", "rf-kiss.log")           # the registry's target/job
    assert not _links(doc, "graywolf", "rf-graywolf.log")


def test_daemon_card_links_both_band_logs(web):
    doc = parse(web().get("/stacks?open=daemon").get_data(as_text=True))
    assert doc.present("stack-rflog-daemon")
    assert _links(doc, "loraham-daemon", "rf-daemon-433.log")
    assert _links(doc, "loraham-daemon", "rf-daemon-868.log")


@pytest.mark.parametrize("sid", ["chat", "kiss", "voice"])
def test_stacks_without_an_rf_log_have_no_submenu(sid, web):
    doc = parse(web().get(f"/stacks?open={sid}").get_data(as_text=True))
    assert doc.present(f"stackrow-{sid}") and not doc.present(f"stack-rflog-{sid}")


def test_switch_saves_one_key_on_the_owner_and_merges(tmp_path, web, csrf):
    c = web()
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    assert svc.save_config_bundle("kiss", values={"rx_only": "on"}).ok
    before = cfgmod.load_stack_config(paths, "kiss", "")
    r = c.post("/stacks/graywolf/rflog", data={"_csrf": csrf(c), "value": "off"})
    assert r.status_code == 302
    after = cfgmod.load_stack_config(paths, "kiss", "")
    assert after["rf_log"] == "off" and {k: v for k, v in after.items() if k != "rf_log"} == before
    assert "rf_log" not in cfgmod.load_stack_config(paths, "graywolf", "")
    doc = parse(c.get("/stacks?open=graywolf").get_data(as_text=True))
    assert doc.field_default("value") == "off"


def test_switch_requires_csrf_and_a_known_stack(tmp_path, web, csrf):
    c = web()
    assert c.post("/stacks/graywolf/rflog", data={"value": "off"}).status_code == 400
    assert c.post("/stacks/nope/rflog", data={"_csrf": csrf(c), "value": "off"}).status_code == 404
    r = c.post("/stacks/chat/rflog", data={"_csrf": csrf(c), "value": "off"})   # no RF log
    assert r.status_code == 302
    assert not (tmp_path / "config" / "stacks").exists()


def test_submenu_says_restart_required_after_a_live_change(monkeypatch, web, csrf):
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: sid == "kiss")
    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid, d="": "433")
    c = web()
    assert c.post("/stacks/graywolf/rflog", data={"_csrf": csrf(c), "value": "off"}).status_code == 302
    doc = parse(c.get("/stacks?open=graywolf").get_data(as_text=True))
    # The typed flag (`rflog_view(...)["restart_required"]`) is owned by tests/core; the submenu
    # renders it only as this phrase, so the phrase is asserted INSIDE the submenu.
    assert doc.present("rflog-restart-graywolf")


# ---- the page ----------------------------------------------------------------------------------

def test_rf_log_page_has_the_switcher_and_a_scoped_clear_form(web):
    c = web()
    doc = parse(c.get("/logs/loraham-kiss-tnc?job=rf-kiss.log").get_data(as_text=True))
    nav = doc.find("nav", **{"aria-label": "RF logs"})
    assert nav
    current = doc.find("a", **{"aria-current": "page"})
    assert [a["href"] for a in current] == ["/logs/loraham-kiss-tnc?job=rf-kiss.log"]
    # The switcher is complete: exactly one link per registered (writer, job). The job names
    # themselves are pinned by tests/stacks/test_rflog_manifest.py, not spelled again here.
    switcher = doc.within(nav[0])
    for e in rflog.REGISTRY:
        for _band, job in e.jobs:
            assert len(_links(switcher, e.writer, job)) == 1, (e.writer, job)
    assert doc.find("form", action="/logs/loraham-kiss-tnc/clear")
    assert doc.find("input", name="job")[0]["value"] == "rf-kiss.log"


@pytest.mark.parametrize("url", ["/logs/loraham-kiss-tnc?job=rf-made-up.log",
                                 "/logs/loraham-daemon?job=rf-kiss.log",       # not this writer's
                                 "/logs/loraham-kiss-tnc"])
def test_other_logs_get_neither_switcher_nor_clear(url, web):
    doc = parse(web().get(url).get_data(as_text=True))
    assert not doc.find("nav", **{"aria-label": "RF logs"})
    assert not doc.find("input", name="job")


def test_page_and_api_show_both_segments(tmp_path, web):
    _logs(tmp_path, "rf-kiss.log.1", "old\n")
    _logs(tmp_path, "rf-kiss.log", "new\n")
    c = web()
    assert c.get("/api/logs/loraham-kiss-tnc?job=rf-kiss.log").get_json()["lines"] == ["old", "new"]
    absent = c.get("/api/logs/loraham-daemon?job=rf-daemon-433.log").get_json()   # no file yet
    assert absent["lines"] == [] and absent["path"] == "" and absent["running"] is False


def test_clear_empties_only_the_shown_log(tmp_path, web, csrf):
    a = _logs(tmp_path, "rf-kiss.log", "a\n")
    a1 = _logs(tmp_path, "rf-kiss.log.1", "old\n")
    b = _logs(tmp_path, "rf-meshcom.log", "b\n")
    c = web()
    r = c.post("/logs/loraham-kiss-tnc/clear", data={"_csrf": csrf(c), "job": "rf-kiss.log"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/logs/loraham-kiss-tnc?job=rf-kiss.log")
    assert a.read_text() == "" and not a1.exists() and b.read_text() == "b\n"


@pytest.mark.parametrize("target,job,status", [
    ("loraham-kiss-tnc", "rf-made-up.log", 404),     # absent from the registry, whatever its name
    ("loraham-daemon", "rf-kiss.log", 404),          # registered, but not this writer's
    ("loraham-kiss-tnc", "../rf-kiss.log", 404),
    ("loraham-kiss-tnc", "rf-kiss.txt", 404),
    ("bogus", "rf-kiss.log", 404),
])
def test_clear_refuses_everything_outside_the_registry(tmp_path, target, job, status, web, csrf):
    keep = [_logs(tmp_path, n, "keep\n") for n in ("rf-kiss.log", "rf-made-up.log", "rf-kiss.txt")]
    c = web()
    assert c.post(f"/logs/{target}/clear", data={"_csrf": csrf(c), "job": job}).status_code == status
    assert all(p.read_text() == "keep\n" for p in keep)


def test_clear_requires_csrf_and_refuses_a_symlink(tmp_path, web, csrf):
    outside = tmp_path / "outside.log"
    outside.write_text("precious\n")
    (tmp_path / "logs").mkdir()
    os.symlink(outside, tmp_path / "logs" / "rf-meshcom.log")
    c = web()
    assert c.post("/logs/meshcom-bridge/clear", data={"job": "rf-meshcom.log"}).status_code == 400
    assert c.post("/logs/meshcom-bridge/clear",
                  data={"_csrf": csrf(c), "job": "rf-meshcom.log"}).status_code == 302
    assert outside.read_text() == "precious\n"


# ---- the viewer's records and the Decrypt toggle -----------------------------------------------

KISS_LINE = '2026-09-12T16:05:01.020Z RX rssi=-71.00 snr=9.75 len=3 hex=010203 ascii="..."'


def test_the_records_api_parses_both_segments_and_is_registry_authorized(tmp_path, web):
    _logs(tmp_path, "rf-kiss.log.1", "old junk\n")
    _logs(tmp_path, "rf-kiss.log", KISS_LINE + "\n")
    c = web()
    d = c.get("/api/rflog/loraham-kiss-tnc?job=rf-kiss.log").get_json()
    assert d["target"] == "loraham-kiss-tnc" and d["path"].endswith("/logs/rf-kiss.log")
    assert [r["raw"] for r in d["records"]] == ["old junk", KISS_LINE]
    assert d["records"][1]["rssi"] == -71.0 and d["records"][1]["hex"] == "010203"
    assert set(d["records"][0]) == {"key", "raw"}
    for url in ("/api/rflog/loraham-kiss-tnc?job=rf-made-up.log", "/api/rflog/loraham-daemon?job=rf-kiss.log",
                "/api/rflog/loraham-kiss-tnc", "/api/rflog/bogus?job=rf-kiss.log"):
        assert c.get(url).status_code == 404


def test_the_decoded_api_exists_only_for_encrypted_logs_and_is_never_cached(tmp_path, monkeypatch, web):
    _logs(tmp_path, "rf-meshtastic.log", '{"timestamp":1789228997,"rssi":-67,"snr":11.25,"from":1,"to":2,"size":4,"bytes":"01020304"}\n')
    calls = []

    def fake(self, target, job, records):
        calls.append((target, job, len(records)))
        return {"records": [{**r, "status": "ok", "kind": "text", "peer": "!00000001", "decoded": "<b>hi</b>"}
                            for r in records], "error": ""}
    monkeypatch.setattr(ControllerService, "rflog_decode", fake)
    c = web()
    r = c.get("/api/rflog/meshtastic/decoded?job=rf-meshtastic.log")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    d = r.get_json()
    assert calls == [("meshtastic", "rf-meshtastic.log", 1)]
    assert d["error"] == "" and d["records"][0]["decoded"] == "<b>hi</b>" and d["records"][0]["kind"] == "text"


@pytest.mark.parametrize("url", [
    "/api/rflog/loraham-kiss-tnc/decoded?job=rf-kiss.log",         # plaintext: nothing to decode
    "/api/rflog/meshcom-bridge/decoded?job=rf-meshcom.log",        # plaintext
    "/api/rflog/loraham-daemon/decoded?job=rf-daemon-433.log",     # plaintext
    "/api/rflog/meshtastic/decoded?job=rf-kiss.log",               # registered, but not this writer's
    "/api/rflog/meshtastic/decoded?job=rf-made-up.log",            # absent from the registry
    "/api/rflog/meshtastic/decoded",                               # no job at all
])
def test_the_decoded_api_refuses_what_the_registry_cannot_decode(tmp_path, monkeypatch, url, web):
    """The registry-authorization boundary of the decoder route: 404, and the decoder never
    runs. The plaintext files exist, so the refusal is the registry's, not a missing file's."""
    for name in ("rf-kiss.log", "rf-meshcom.log", "rf-daemon-433.log", "rf-meshtastic.log"):
        _logs(tmp_path, name, KISS_LINE + "\n")
    monkeypatch.setattr(ControllerService, "rflog_decode",
                        lambda self, *a, **k: pytest.fail(f"the decoder ran for {url}"))
    assert web().get(url).status_code == 404


def test_a_decoder_level_error_is_reported_with_the_records_intact(tmp_path, monkeypatch, web):
    _logs(tmp_path, "rf-reticulum.log", KISS_LINE + "\n")
    monkeypatch.setattr(ControllerService, "rflog_decode",
                        lambda self, t, j, recs: {"records": [{**r, "status": "", "kind": "", "peer": "", "decoded": ""} for r in recs],
                                                  "error": "reticulum is not built — nothing to decode with"})
    d = web().get("/api/rflog/rns/decoded?job=rf-reticulum.log").get_json()
    assert d["error"].startswith("reticulum is not built") and d["records"][0]["raw"] == KISS_LINE


@pytest.mark.parametrize("url,encrypted", [
    ("/logs/meshtastic?job=rf-meshtastic.log", True),
    ("/logs/meshcore-node?job=rf-meshcore.log", True),
    ("/logs/rns?job=rf-reticulum.log", True),
    ("/logs/loraham-kiss-tnc?job=rf-kiss.log", False),
    ("/logs/loraham-daemon?job=rf-daemon-433.log", False),
    ("/logs/meshcom-bridge?job=rf-meshcom.log", False),
])
def test_the_page_carries_the_table_and_decrypt_only_where_keys_can_open_it(url, encrypted, web):
    doc = parse(web().get(url).get_data(as_text=True))
    assert doc.by_id("rfview") and doc.by_id("rf-filter") and doc.by_id("rf-raw")
    assert doc.by_id("log-card")["data-decoder"] == ("1" if encrypted else "0")
    assert bool(doc.by_id("rf-decrypt")) == encrypted
    assert bool(doc.find("th", **{"data-col": "decoded"})) == encrypted
    assert bool(doc.find("input", value="decoded")) == encrypted
    if encrypted:
        # its own row, below the switcher, off by default
        nav = doc.find("nav", **{"aria-label": "RF logs"})[0]
        assert doc.index(nav) < doc.index(doc.by_id("rf-decrypt")) < doc.index(doc.by_id("rf-filter"))
        assert doc.by_id("rf-decrypt")["aria-pressed"] == "false"
    scripts = _scripts(doc)
    assert any(s.endswith("/rflog.js") for s in scripts) and not any(s.endswith("/logs.js") for s in scripts)


def test_a_run_log_page_keeps_the_plain_viewer(web):
    doc = parse(web().get("/logs/loraham-kiss-tnc").get_data(as_text=True))
    assert not doc.by_id("rfview") and not doc.by_id("rf-decrypt") and doc.by_id("logbox")
    scripts = _scripts(doc)
    assert any(s.endswith("/logs.js") for s in scripts) and not any(s.endswith("/rflog.js") for s in scripts)
