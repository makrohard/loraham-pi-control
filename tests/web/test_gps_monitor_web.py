"""Web: the GPS block's Monitor and Settings sub-sections, the two read-only monitor endpoints, the
script that drives them, and the privacy rule that no coordinate ever reaches the application log."""

import logging

import pytest

from lhpc.core import gps as _gps
from lhpc.core.config import save_gps
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

LAT, LON = 51.477812, -0.001545


def _client(web, tmp_path, **gps):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    if gps:
        save_gps(p, **gps)
    return web(system=FakeSystem().system, paths=p), p


@pytest.mark.contract
def test_monitor_before_settings_and_the_form_where_it_was(web, tmp_path):
    c, _ = _client(web, tmp_path)
    body = c.get("/stacks?open=gps").get_data(as_text=True)
    assert 'id="gps-row"' in body
    i_mon, i_set = body.index('id="gps-monitor"'), body.index('id="gps-settings"')
    assert i_mon < i_set
    assert 'action="/gps"' in body and 'name="gps_source"' in body        # the form, unchanged
    assert 'id="gps-mon-state"' in body and 'id="gps-sky"' in body and 'id="gps-nmea-body"' in body
    assert "gps.js" in body                                                # stacks.html loads it


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
    c, _ = _client(web, tmp_path / "u", source="gpsd", host="127.0.0.1", port=1)
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


def test_service_monitor_matches_cli_snapshot(tmp_path, capsys):
    """`lhpc gps --monitor` prints the same snapshot; `--sats` needs it; setting flags refused."""
    from lhpc.adapters.cli.main import main
    assert main(["gps", "--source", "fixed", "--lat", str(LAT), "--lon", str(LON), "--alt", "45"]) == 0
    capsys.readouterr()
    assert main(["gps", "--monitor"]) == 0
    out = capsys.readouterr().out
    assert "fixed position (configured)" in out and f"{LAT:.6f}" in out and "45.0 m MSL" in out
    assert main(["gps", "--sats"]) == 2
    assert "--sats needs --monitor" in capsys.readouterr().out
    assert main(["gps", "--monitor", "--source", "gpsd"]) == 2
    assert "read-only" in capsys.readouterr().out
    svc = ControllerService(system=FakeSystem().system)
    assert svc.gps_settings()["source"] == "fixed"          # the refused call changed nothing
