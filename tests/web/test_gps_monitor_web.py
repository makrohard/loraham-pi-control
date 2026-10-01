"""Web: the GPS block's Monitor and Settings sub-sections, the two read-only monitor endpoints, the
script that drives them, and the privacy rule that no coordinate ever reaches the application log."""

import contextlib
import logging
import socket

import pytest

from lhpc.core import gps as _gps
from lhpc.core.config import save_gps
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

from htmlq import parse

LAT, LON = 51.477812, -0.001545


@contextlib.contextmanager
def _refusing_port():
    """A loopback port that refuses every connection: bound here and never listening, so no other
    process can be serving it (a fixed "unused" port such as 1 is a guess about the host)."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        yield s.getsockname()[1]
    finally:
        s.close()


def _client(web, tmp_path, **gps):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    if gps:
        save_gps(p, **gps)
    return web(system=FakeSystem().system, paths=p), p


@pytest.mark.contract
def test_monitor_before_settings_and_the_form_where_it_was(web, tmp_path):
    c, _ = _client(web, tmp_path)
    doc = parse(c.get("/stacks?open=gps").get_data(as_text=True))
    assert doc.present("gps-row")
    assert doc.index(doc.by_id("gps-monitor")) < doc.index(doc.by_id("gps-settings"))
    settings = doc.within(doc.by_id("gps-settings"))
    form = settings.find("form", action="/gps")                             # the form, unchanged
    assert form and doc.within(form[0]).field_default("gps_source") is not None
    assert all(doc.present(i) for i in ("gps-mon-state", "gps-sky", "gps-nmea-body"))
    assert any((s["src"] or "").endswith("gps.js") for s in doc.find("script"))   # stacks.html loads it


@pytest.mark.contract
def test_api_gps_shape_for_off_fixed_and_unavailable(web, tmp_path):
    c, _ = _client(web, tmp_path, source="off")
    d = c.get("/api/gps").get_json()
    assert d["state"] == "off" and d["label"] == "no position source" and d["lat"] is None
    for k in ("source", "resolved_source", "state", "label", "note", "error", "nmea_ok", "devices",
              "device", "mode", "lat", "lon", "alt", "alt_kind", "time", "time_has_date",
              "sats_used", "sats_seen", "satellites", "nmea"):
        assert k in d
    c, _ = _client(web, tmp_path / "f", source="fixed", fixed_lat=str(LAT), fixed_lon=str(LON),
                   fixed_alt="45")
    d = c.get("/api/gps").get_json()
    assert d["state"] == "fixed" and d["lat"] == LAT and d["alt_kind"] == "msl"
    with _refusing_port() as port:
        c, _ = _client(web, tmp_path / "u", source="gpsd", host="127.0.0.1", port=port)
        r = c.get("/api/gps")
    assert r.status_code == 200 and r.get_json()["state"] == "unavailable"    # never breaks


@pytest.mark.contract
def test_api_nmea_is_gpsd_only_and_never_opens_a_device(web, tmp_path, monkeypatch):
    import os
    c, _ = _client(web, tmp_path, source="off")
    assert c.get("/api/gps/nmea").get_json() == {"lines": []}
    monkeypatch.setattr(_gps, "gpsd_nmea_window", lambda h, p, **k: ["$GPGGA,x*00"])
    c, _ = _client(web, tmp_path / "g", source="gpsd", host="127.0.0.1", port=2947)
    assert c.get("/api/gps/nmea").get_json() == {"lines": ["$GPGGA,x*00"]}
    # a direct receiver: the endpoint answers [] and opens nothing
    master, slave = os.openpty()
    try:
        c, _ = _client(web, tmp_path / "n", source="nmea", device=os.ttyname(slave), nmea_baud=9600)
        opened = []
        real = os.open
        monkeypatch.setattr(os, "open", lambda p, *a, **k: (opened.append(p) if isinstance(p, str)
                                                            and "/pts/" in p else None) or real(p, *a, **k))
        assert c.get("/api/gps/nmea").get_json() == {"lines": []}
        assert opened == []
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.contract
def test_no_coordinate_reaches_the_log_on_any_path(web, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    good = {"ok": True, "error": "", "state": "3d", "devices": ["/dev/x"], "device": "/dev/x",
            "mode": 3, "lat": LAT, "lon": LON, "alt": 1.0, "alt_kind": "msl", "time": "t",
            "time_has_date": True, "sats_used": 1, "sats_seen": 1, "satellites": [],
            "nmea": [], "nmea_ok": True, "note": ""}
    monkeypatch.setattr(_gps, "gpsd_snapshot", lambda h, p, timeout=2.5: good)
    c, _ = _client(web, tmp_path, source="gpsd", host="127.0.0.1", port=2947)
    d = c.get("/api/gps").get_json()
    assert d["lat"] == LAT                                 # shown by design ...

    def boom(h, p, timeout=2.5):
        raise ValueError(f"bad coordinate {LAT},{LON} in report")
    monkeypatch.setattr(_gps, "gpsd_snapshot", boom)      # ... the failure path fails soft ...
    d = c.get("/api/gps").get_json()
    assert d["state"] == "unavailable" and "ValueError" in d["error"] and str(LAT) not in d["error"]
    assert str(LAT) not in caplog.text and str(LON) not in caplog.text   # ... and logs nothing


@pytest.mark.contract
def test_responses_are_no_store(web, tmp_path):
    c, _ = _client(web, tmp_path, source="off")
    assert "no-store" in c.get("/api/gps").headers.get("Cache-Control", "")

