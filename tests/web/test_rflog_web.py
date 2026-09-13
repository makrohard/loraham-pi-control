"""The RF log page — band row, stack row, the switches at the bottom, the scoped Clear and
Clear all — and the dashboard's RF-log links: all driven by the registry, authorized by nothing
else."""

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


def _links(doc, target, job, band=""):
    """The links to one RF log, by their parsed URL (query order is nobody's contract)."""
    from urllib.parse import parse_qs, urlsplit
    want = {"job": [job], **({"band": [band]} if band else {})}
    return [a for a in doc.find("a") if a["href"] and urlsplit(a["href"]).path == f"/logs/{target}"
            and parse_qs(urlsplit(a["href"]).query) == want]


def _row(doc, label):
    return doc.within(doc.find("nav", **{"aria-label": label})[0])


def _owner_values(paths):
    return {e.owner: cfgmod.load_stack_config(paths, e.owner, "").get(e.key) for e in rflog.REGISTRY}


def _scripts(doc):
    return [s["src"] or "" for s in doc.find("script")]


# ---- the switches at the bottom of the page ---------------------------------------------------

def test_the_stack_switch_saves_one_key_on_the_owner_and_returns_to_the_page(tmp_path, web, csrf):
    c = web()
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    assert svc.save_config_bundle("kiss", values={"rx_only": "on"}).ok
    before = cfgmod.load_stack_config(paths, "kiss", "")
    r = c.post("/stacks/graywolf/rflog", data={"_csrf": csrf(c), "value": "off", "from": "logs",
                                               "job": "rf-kiss.log"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/logs/loraham-kiss-tnc?job=rf-kiss.log")
    after = cfgmod.load_stack_config(paths, "kiss", "")
    assert after["rf_log"] == "off" and {k: v for k, v in after.items() if k != "rf_log"} == before
    assert "rf_log" not in cfgmod.load_stack_config(paths, "graywolf", "")
    doc = parse(c.get("/logs/loraham-kiss-tnc?job=rf-kiss.log").get_data(as_text=True))
    form = doc.within(doc.by_id("rflog-switch"))
    assert form.field_default("value") == "off" and form.field_default("from") == "logs"
    assert form.field_default("job") == "rf-kiss.log"
    # Without `from=logs` (another page's form) the save lands on the owner's Settings.
    r = c.post("/stacks/graywolf/rflog", data={"_csrf": csrf(c), "value": "on"})
    assert r.status_code == 302 and "/stacks?open=graywolf#stack-settings-graywolf" in r.headers["Location"]


def test_switch_requires_csrf_and_a_known_stack(tmp_path, web, csrf):
    c = web()
    assert c.post("/stacks/graywolf/rflog", data={"value": "off"}).status_code == 400
    assert c.post("/stacks/nope/rflog", data={"_csrf": csrf(c), "value": "off"}).status_code == 404
    r = c.post("/stacks/chat/rflog", data={"_csrf": csrf(c), "value": "off"})   # no RF log
    assert r.status_code == 302
    assert not (tmp_path / "config" / "stacks").exists()


def test_the_page_says_restart_required_after_a_live_change(monkeypatch, web, csrf):
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: sid == "kiss")
    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid, d="": "433")
    c = web()
    url = "/logs/loraham-kiss-tnc?job=rf-kiss.log"
    assert not parse(c.get(url).get_data(as_text=True)).present("rflog-restart")
    assert c.post("/stacks/graywolf/rflog", data={"_csrf": csrf(c), "value": "off"}).status_code == 302
    # The typed flag (`rflog_switch(...)["restart_required"]`) is owned by tests/core; the page
    # renders it only as this phrase, so the phrase is asserted inside the stack form.
    doc = parse(c.get(url).get_data(as_text=True))
    assert doc.within(doc.by_id("rflog-switch")).present("rflog-restart")


def test_logging_sets_every_owner_at_once_and_shows_mixed(tmp_path, web, csrf):
    c = web()
    paths = Paths(runtime_root=tmp_path)
    url = "/logs/loraham-kiss-tnc?job=rf-kiss.log"
    r = c.post("/rflog/all", data={"_csrf": csrf(c), "value": "off", "job": "rf-kiss.log"})
    assert r.status_code == 302 and r.headers["Location"].endswith(url)
    assert set(_owner_values(paths).values()) == {"off"}
    doc = parse(c.get(url).get_data(as_text=True))
    assert doc.by_id("rflog-all-state")["data-state"] == "off"
    assert doc.within(doc.by_id("rflog-all")).field_default("value") == "off"
    assert c.post("/stacks/graywolf/rflog", data={"_csrf": csrf(c), "value": "on"}).status_code == 302
    doc = parse(c.get(url).get_data(as_text=True))
    assert doc.by_id("rflog-all-state")["data-state"] == "mixed"
    # A bad value changes nothing; without a CSRF token the route refuses outright.
    assert c.post("/rflog/all", data={"_csrf": csrf(c), "value": "maybe", "job": "rf-kiss.log"}).status_code == 302
    assert c.post("/rflog/all", data={"value": "on", "job": "rf-kiss.log"}).status_code == 400
    doc = parse(c.get(url).get_data(as_text=True))
    assert doc.by_id("rflog-all-state")["data-state"] == "mixed" and _owner_values(paths)["daemon"] == "off"


# ---- the page ----------------------------------------------------------------------------------

@pytest.mark.parametrize("band", ControllerService.RADIO_BANDS)
def test_the_page_has_a_band_row_and_the_stack_row_of_that_band(tmp_path, band, web):
    """Both rows are plain links. The band row lists every band and marks the shown one; the
    stack row is the registry's answer for that band (owned by tests/core), rendered complete,
    each pill carrying the band so the next page opens on the same one."""
    c = web()
    doc = parse(c.get(f"/logs/loraham-kiss-tnc?job=rf-kiss.log&band={band}").get_data(as_text=True))
    bands = _row(doc, "RF log bands")
    assert [a.text for a in bands.find("a")] == list(ControllerService.RADIO_BANDS)
    assert [a.text for a in bands.find("a", **{"aria-current": "page"})] == [band]
    for b in ControllerService.RADIO_BANDS:
        assert len(_links(bands, "loraham-kiss-tnc", "rf-kiss.log", b)) == 1
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    expected = svc.rflog_switcher("rf-kiss.log", band)["stacks"]
    stacks = _row(doc, "RF logs")
    assert len(stacks.find("a")) == len(expected) > 1
    for st in expected:
        assert len(_links(stacks, st["target"], st["job"], band)) == 1, st
    assert [a.text for a in stacks.find("a", **{"aria-current": "page"})] == ["graywolf"]
    assert doc.find("form", action="/logs/loraham-kiss-tnc/clear")
    assert doc.within(doc.by_id("rflog-clear")).field_default("job") == "rf-kiss.log"


def test_the_shown_band_falls_back_to_the_files_own_band(web):
    doc = parse(web().get("/logs/loraham-daemon?job=rf-daemon-868.log").get_data(as_text=True))
    bands = _row(doc, "RF log bands")
    assert [a.text for a in bands.find("a", **{"aria-current": "page"})] == ["868"]
    assert [a.text for a in _row(doc, "RF logs").find("a", **{"aria-current": "page"})] == ["daemon 868"]
    # On the daemon's page a band pill opens THAT band's file in one tap (one file per band).
    assert len(_links(bands, "loraham-daemon", "rf-daemon-433.log", "433")) == 1


@pytest.mark.parametrize("url", ["/logs/loraham-kiss-tnc?job=rf-made-up.log",
                                 "/logs/loraham-daemon?job=rf-kiss.log",       # not this writer's
                                 "/logs/loraham-kiss-tnc"])
def test_other_logs_get_neither_switcher_nor_clear(url, web):
    doc = parse(web().get(url).get_data(as_text=True))
    assert not doc.find("nav", **{"aria-label": "RF logs"}) and not doc.find("nav", **{"aria-label": "RF log bands"})
    assert not doc.find("input", name="job") and not doc.find("form", action="/rflog/all")


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


def test_clear_all_empties_every_registered_log_and_nothing_else(tmp_path, web, csrf):
    jobs = [j for e in rflog.REGISTRY for _b, j in e.jobs]
    live = [_logs(tmp_path, j, "x\n") for j in jobs]
    prev = [_logs(tmp_path, j + ".1", "old\n") for j in jobs]
    keep = [_logs(tmp_path, n, "keep\n") for n in ("rf-made-up.log", "start-loraham-daemon-433.log")]
    c = web()
    assert c.post("/rflog/clear-all", data={"job": "rf-kiss.log"}).status_code == 400   # no CSRF
    assert all(p.read_text() == "x\n" for p in live)
    r = c.post("/rflog/clear-all", data={"_csrf": csrf(c), "job": "rf-kiss.log"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/logs/loraham-kiss-tnc?job=rf-kiss.log")
    assert all(p.read_text() == "" for p in live) and not any(p.exists() for p in prev)
    assert all(p.read_text() == "keep\n" for p in keep)
    doc = parse(c.get("/logs/loraham-kiss-tnc?job=rf-kiss.log").get_data(as_text=True))
    assert doc.by_id("rflog-clear-all")["data-confirm"] and doc.by_id("rflog-clear")["data-confirm"]
    assert doc.by_id("rflog-clear-all")["data-confirm"] != doc.by_id("rflog-clear")["data-confirm"]


# ---- the dashboard's second line -----------------------------------------------------------------

def test_the_dashboard_links_the_bands_rf_logs(tmp_path, web):
    """Under each radio card's daemon control: the daemon's file for that band, then the file of
    every running stack the registry knows — on its own band only. No file exists yet, and the
    links still lead to a page that says so."""
    def factory():
        svc = ControllerService(
            system=FakeSystem(cmdlines_data={100: ["loraham_daemon", "--radio", "433"], 200: ["meshtasticd"]},
                              unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=READY TXMODE=MANAGED\n"}).system,
            paths=Paths(runtime_root=tmp_path))
        svc._set_running_band("meshtastic", "868")
        return svc
    c = web(service_factory=factory)
    doc = parse(c.get("/").get_data(as_text=True))
    col433 = doc.within(doc.find("div", **{"data-radio-band": "433"})[0])
    col868 = doc.within(doc.find("div", **{"data-radio-band": "868"})[0])
    assert len(_links(col433, "loraham-daemon", "rf-daemon-433.log")) == 1
    assert not _links(col433, "meshtastic", "rf-meshtastic.log")
    assert len(_links(col868, "loraham-daemon", "rf-daemon-868.log")) == 1
    assert len(_links(col868, "meshtastic", "rf-meshtastic.log")) == 1
    assert not _links(col868, "loraham-kiss-tnc", "rf-kiss.log")           # not running
    r = c.get(_links(col868, "meshtastic", "rf-meshtastic.log")[0]["href"])
    assert r.status_code == 200 and "(no log file yet)" in r.get_data(as_text=True)


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
    assert doc.by_id("rfview") and doc.by_id("rf-cols") and doc.by_id("rf-raw")
    assert not doc.find("input", type="search") and not doc.find("select", id="rf-dir")   # no filter
    assert doc.by_id("log-card")["data-decoder"] == ("1" if encrypted else "0")
    assert bool(doc.by_id("rf-decrypt")) == encrypted
    assert bool(doc.find("th", **{"data-col": "decoded"})) == encrypted
    assert bool(doc.find("input", value="decoded")) == encrypted
    if encrypted:
        # its own row, below the switcher, off by default
        nav = doc.find("nav", **{"aria-label": "RF logs"})[0]
        assert doc.index(nav) < doc.index(doc.by_id("rf-decrypt")) < doc.index(doc.by_id("rf-cols"))
        assert doc.by_id("rf-decrypt")["aria-pressed"] == "false"
    scripts = _scripts(doc)
    assert any(s.endswith("/rflog.js") for s in scripts) and not any(s.endswith("/logs.js") for s in scripts)


def test_a_run_log_page_keeps_the_plain_viewer(web):
    doc = parse(web().get("/logs/loraham-kiss-tnc").get_data(as_text=True))
    assert not doc.by_id("rfview") and not doc.by_id("rf-decrypt") and doc.by_id("logbox")
    scripts = _scripts(doc)
    assert any(s.endswith("/logs.js") for s in scripts) and not any(s.endswith("/rflog.js") for s in scripts)
