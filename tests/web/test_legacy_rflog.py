"""LEGACY-RFLOG (0.14.0, maintainer's word 11-18: "Remove legacy. … rf-logs not compatible"): the 0.13.x daemon band
RF logs `logs/rf-daemon-<band>.log` (+ `.1`) are no RF log any more: nothing links them (Home's RF line, a radio's
Hardware page, the daemon's tree entry), a typed URL shows one as any job log (no RF view, no Clear), the RF API
refuses it, Clear all leaves it alone. Upgrade steps delete them (CHANGELOG); no delete code."""

from __future__ import annotations

import re

import pytest
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_STATUS = (b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
           b"HIGHPOWER=0 CHIPFAMILY=SX127x\n")
_BAND = ("rf-daemon-433.log", "rf-daemon-433.log.1", "rf-daemon-868.log")


def _client(web, tmp_path, radios=True):
    """A box with the 0.13.x band files in logs/ (and, with `radios`, a two-radio list with live daemons)."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs" / "r1").mkdir(parents=True, exist_ok=True)
    for name in _BAND:
        (tmp_path / "logs" / name).write_text("2026-01-01T00:00:00Z RX rssi=-90 snr=5 len=3 hex=414243\n")
    (tmp_path / "logs" / "r1" / "rf-daemon.log").write_text("")
    p = Paths(runtime_root=tmp_path)
    fs = FakeSystem()
    if radios:
        assert radio_start.setup_radios(p, "uputronics")[0]
        cfgmod.save_hardware_setup(p, "uputronics")
        fs = FakeSystem(unix_replies={dc.conf_socket(r): _STATUS for r in ("r1", "r2")})
    svc = ControllerService(system=fs.system, paths=p)
    return web(service_factory=lambda: svc), svc


def _html(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


def _no_band_file(html, where):
    assert "0.13.x, read-only" not in html, where
    assert not re.search(r"rf-daemon-(433|868)\.log", html), where


def test_home_and_the_hardware_page_link_only_the_radios_rf_logs(tmp_path, web, monkeypatch, daemon_record):
    c, svc = _client(web, tmp_path)
    monkeypatch.setattr(svc, "_daemon_installed", lambda _st: True)      # F8: the Hardware page links an installed daemon's
    daemon_record("r1", "433")                                           # the RF links sit in its daemon row
    home = _html(c, "/")
    _no_band_file(home, "/")
    doc = parse(home)
    col = doc.within(doc.find("div", **{"data-radio-band": "433"})[0])
    pills = [(a.text.strip(), a["href"]) for a in col.find("a", class_="logslink") if "rf-daemon" in a["href"]]
    assert pills == [("daemon r1 (433)", "/logs/loraham-daemon?job=r1/rf-daemon.log&radio=r1")], pills
    hw = _html(c, "/hardware/radio/r1")
    _no_band_file(hw, "/hardware/radio/r1")
    assert "job=r1/rf-daemon.log" in hw


def _tree_rf_log(c, where):
    tree = _html(c, "/stacks/menus")
    _no_band_file(tree, where)
    links = [a["href"] for a in parse(tree).find("a") if a["href"].startswith("/logs/loraham-daemon?job=")]
    assert links and all(re.search(r"job=r\d/rf-daemon\.log", h) for h in links), (where, links)
    r = c.get("/stacks/daemon/menu/rflog-daemon")
    assert r.status_code == 302, (where, r.status_code)
    return r.headers["Location"]


@pytest.mark.no_default_hardware        # no hardware list at all: the daemon has no radio
def test_without_a_radio_the_daemons_tree_rf_log_opens_r1s_file(tmp_path, web):
    # was the 433 band file (app._own_page's fallback)
    c, _svc = _client(web, tmp_path, radios=False)
    assert _tree_rf_log(c, "no radio") == "/logs/loraham-daemon?job=r1/rf-daemon.log"


def test_with_radios_the_daemons_tree_rf_log_opens_a_radios_file(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    assert "job=r1/rf-daemon.log" in _tree_rf_log(c, "radios")


def test_a_typed_band_file_url_is_a_plain_job_log_and_the_rf_api_refuses_it(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    html = _html(c, "/logs/loraham-daemon?job=rf-daemon-433.log")
    doc = parse(html)
    assert "Job log" in html and "2026-01-01T00:00:00Z RX rssi=-90" in html      # the file's line, as any job log
    assert not doc.by_id("rfview") and not doc.by_id("rflog-clear") and not doc.find("nav", **{"aria-label": "RF logs"})
    assert c.get("/api/rflog/loraham-daemon?job=rf-daemon-433.log").status_code == 404
    assert c.get("/api/rflog/loraham-daemon/decoded?job=rf-daemon-433.log").status_code == 404
    # the radio's own file keeps its RF view
    assert parse(_html(c, "/logs/loraham-daemon?job=r1/rf-daemon.log")).by_id("rfview")


def test_clear_all_leaves_the_band_files_alone(tmp_path, web, csrf):
    c, _svc = _client(web, tmp_path)
    before = {n: (tmp_path / "logs" / n).read_text() for n in _BAND}
    tok = csrf(c, "/logs/loraham-daemon?job=r1/rf-daemon.log")
    r = c.post("/rflog/clear-all", data={"_csrf": tok})
    assert r.status_code in (302, 303), r.status_code
    assert {n: (tmp_path / "logs" / n).read_text() for n in _BAND} == before
