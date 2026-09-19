"""The GPS Monitor's gpsd side: the bounded JSON reader against the fake gpsd, the view for every
source, the ownership helper's corrected contract, and the NMEA window. No hardware anywhere."""

import json

import pytest

from lhpc.core import gps as _gps
from lhpc.core.config import save_gps
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

VERSION = b'{"class":"VERSION","release":"3.25","proto_major":3,"proto_minor":15}\n'
DEV_A = b'{"class":"DEVICES","devices":[{"path":"/dev/ttyACM0","driver":"u-blox"}]}\n'
DEV_AB = (b'{"class":"DEVICES","devices":[{"path":"/dev/ttyACM0"},'
          b'{"path":"/dev/ttyACM1"}]}\n')
DEV_NONE = b'{"class":"DEVICES","devices":[]}\n'


def _tpv(mode, lat=51.5, lon=-0.12, device="/dev/ttyACM0", **extra):
    d = {"class": "TPV", "mode": mode, "time": "2026-09-19T12:34:56.000Z"}
    if device:
        d["device"] = device
    if mode >= 2:
        d.update(lat=lat, lon=lon)
    d.update(extra)
    return (json.dumps(d) + "\n").encode()


def _sky(sats, device="/dev/ttyACM0", **extra):
    d = {"class": "SKY", "satellites": sats}
    if device:
        d["device"] = device
    d.update(extra)
    return (json.dumps(d) + "\n").encode()


def _sat(prn, el=45, az=180, ss=30, used=True):
    return {"PRN": prn, "el": el, "az": az, "ss": ss, "used": used}


def _snap(fake_gpsd, *lines, timeout=1.2):
    srv = fake_gpsd(json_lines=[VERSION, *lines])
    return _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=timeout)


# --- the four-way contract ---------------------------------------------------------------------

def test_connection_failure_is_unavailable():
    r = _gps.gpsd_snapshot("127.0.0.1", 1, timeout=0.5)          # nothing listens on port 1
    assert not r["ok"] and r["state"] == "unavailable" and r["error"]


def test_empty_devices_is_no_device_and_exits_early(fake_gpsd):
    r = _snap(fake_gpsd, DEV_NONE, _tpv(3))
    assert r["ok"] and r["state"] == "no-device" and r["lat"] is None


def test_device_listed_but_no_tpv_is_no_data_not_receiver(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, _sky([_sat(1)]))
    assert r["state"] == "gpsd-no-data" and r["devices"] == ["/dev/ttyACM0"]


def test_tpv_without_sky_is_a_fix_with_satellites_na(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, _tpv(3, altMSL=120.5))
    assert r["state"] == "3d" and r["lat"] == 51.5 and r["sats_seen"] is None
    assert r["alt"] == 120.5 and r["alt_kind"] == "msl"


# --- collecting for the whole budget, per device ---------------------------------------------

def test_a_later_tpv_in_the_session_wins(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, _tpv(1), _tpv(3))
    assert r["state"] == "3d"


def test_losing_the_fix_clears_position_and_altitude(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, _tpv(3, altMSL=10.0), _tpv(1))
    assert r["state"] == "no-fix" and r["lat"] is None and r["alt"] is None


def test_mode_1_with_coordinates_renders_no_position(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, _tpv(1, lat=51.5, lon=-0.12) if False else
              (json.dumps({"class": "TPV", "device": "/dev/ttyACM0", "mode": 1,
                           "lat": 51.5, "lon": -0.12}) + "\n").encode())
    assert r["state"] == "no-fix" and r["lat"] is None


def test_sky_from_another_device_never_joins_a_tpv(fake_gpsd):
    r = _snap(fake_gpsd, DEV_AB, _tpv(3, device="/dev/ttyACM0"),
              _sky([_sat(7)], device="/dev/ttyACM1"))
    assert r["state"] == "3d" and r["device"] == "/dev/ttyACM0" and r["satellites"] == []


def test_two_tpv_devices_are_ambiguous_never_merged(fake_gpsd):
    r = _snap(fake_gpsd, DEV_AB, _tpv(3, device="/dev/ttyACM0"),
              _tpv(2, lat=1.0, lon=2.0, device="/dev/ttyACM1"))
    assert r["state"] == "ambiguous" and r["lat"] is None and not r["nmea_ok"]
    assert "/dev/ttyACM0" in r["note"] and "/dev/ttyACM1" in r["note"]


def test_untagged_tpv_with_one_device_is_attributed(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, _tpv(3, device=None))
    assert r["state"] == "3d" and r["device"] == "/dev/ttyACM0" and r["nmea_ok"]


def test_untagged_tpv_beside_several_devices_keeps_the_ambiguity(fake_gpsd):
    r = _snap(fake_gpsd, DEV_AB, _tpv(3, device="/dev/ttyACM0"), _tpv(3, device=None))
    assert r["state"] == "3d" and r["device"] == "/dev/ttyACM0"
    assert not r["nmea_ok"] and "unresolved" in r["note"]
    r2 = _snap(fake_gpsd, DEV_AB, _tpv(3, device=None))
    assert r2["state"] == "ambiguous"


def test_non_gps_device_in_the_list_does_not_become_a_receiver(fake_gpsd):
    devs = (b'{"class":"DEVICES","devices":[{"path":"/dev/ttyACM0"},'
            b'{"path":"tcp://ais.example:1234","flags":4}]}\n')
    r = _snap(fake_gpsd, devs)
    assert r["state"] == "gpsd-no-data"


# --- fields, framing, finiteness ---------------------------------------------------------------

def test_altitude_datum_order_and_legacy_fallback(fake_gpsd):
    assert _snap(fake_gpsd, DEV_A, _tpv(3, altHAE=50.0))["alt_kind"] == "hae"
    r = _snap(fake_gpsd, DEV_A, _tpv(3, alt=33.0))
    assert r["alt"] == 33.0 and r["alt_kind"] == "legacy"
    r = _snap(fake_gpsd, DEV_A, _tpv(3, altMSL=1.0, altHAE=2.0, alt=3.0))
    assert r["alt"] == 1.0 and r["alt_kind"] == "msl"


def test_sky_counts_come_from_gpsd_when_present_and_bad_points_are_skipped(fake_gpsd):
    sats = [_sat(1), _sat(2, el=float("nan")) if False else {"PRN": 2, "el": "x", "az": 10, "ss": 5},
            {"PRN": 3, "el": 200, "az": 10, "ss": 5}, {"PRN": 4, "el": 10, "az": 370, "ss": -3},
            {"PRN": 5, "az": 90, "ss": 20, "used": False}]
    r = _snap(fake_gpsd, DEV_A, _tpv(3), _sky(sats, uSat=7, nSat=12))
    assert r["sats_used"] == 7 and r["sats_seen"] == 12
    by = {s["prn"]: s for s in r["satellites"]}
    assert by[2]["el"] is None and by[3]["el"] is None
    assert by[4]["az"] == 10.0 and by[4]["ss"] is None
    assert by[5]["el"] is None and by[5]["used"] is False


def test_out_of_range_coordinates_are_not_a_position(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, _tpv(3, lat=95.0, lon=10.0))
    assert r["state"] == "3d" and r["lat"] is None       # mode says fix; the value is unusable


def test_a_report_fragmented_across_reads_is_reassembled(fake_gpsd):
    big = _sky([_sat(i) for i in range(1, 200)])           # well above 4 KB
    assert len(big) > 4096
    chunks = [big[i:i + 1000] for i in range(0, len(big), 1000)]
    srv = fake_gpsd(json_lines=[VERSION, DEV_A, _tpv(3), *chunks])
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=1.5)
    assert r["state"] == "3d" and r["sats_seen"] == 199


def test_a_truncated_oversized_report_is_dropped_without_an_exception(fake_gpsd):
    junk = b'{"class":"SKY","device":"/dev/ttyACM0","satellites":[' + b'{"PRN":1},' * 3000
    r = _snap(fake_gpsd, DEV_A, _tpv(3), junk[:12000] + b"\n", _tpv(2))
    assert r["ok"] and r["state"] == "2d" and r["satellites"] == []


def test_malformed_json_lines_are_skipped(fake_gpsd):
    r = _snap(fake_gpsd, DEV_A, b'{"class":"TPV",,,\n', b"garbage\n", _tpv(3))
    assert r["state"] == "3d"


def test_unsupported_protocol_major_is_unavailable(fake_gpsd):
    srv = fake_gpsd(json_lines=[b'{"class":"VERSION","proto_major":4,"proto_minor":0}\n', DEV_A])
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=1.0)
    assert r["state"] == "unavailable" and "protocol" in r["error"]


def test_a_silent_gpsd_exhausts_the_budget_as_unavailable(fake_gpsd):
    import time
    srv = fake_gpsd(silent=True)
    t0 = time.monotonic()
    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.6)
    assert r["state"] == "unavailable" and time.monotonic() - t0 < 2.0


# --- the NMEA window ---------------------------------------------------------------------------

def test_nmea_window_keeps_only_checksum_valid_sentences_and_is_bounded(fake_gpsd):
    good = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    srv = fake_gpsd(sentences=[good, "$GPGGA,bad,no,checksum", "not a sentence"])
    lines = _gps.gpsd_nmea_window("127.0.0.1", srv.port, max_lines=5, timeout=1.0)
    assert lines and all(line == good for line in lines) and len(lines) <= 5


# --- the view for every source -----------------------------------------------------------------

def _svc(tmp_path, **gps):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    if gps:
        save_gps(p, **gps)
    return ControllerService(system=FakeSystem().system, paths=p)


def test_off_and_fixed_need_no_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(_gps, "gpsd_snapshot", lambda *a, **k: pytest.fail("socket opened"))
    assert _svc(tmp_path, source="off").gps_monitor()["state"] == "off"
    m = _svc(tmp_path, source="fixed", fixed_lat="51.5", fixed_lon="-0.12",
             fixed_alt="45").gps_monitor()
    assert m["state"] == "fixed" and m["lat"] == 51.5 and m["alt"] == 45.0 and m["alt_kind"] == "msl"
    assert m["label"] == "fixed position (configured)"


def test_auto_probes_live_without_touching_config(tmp_path, monkeypatch):
    svc = _svc(tmp_path)                                   # default source: auto
    cfg_before = svc.config()
    monkeypatch.setattr(_gps, "local_gpsd_listening", lambda: False)
    assert svc.gps_monitor()["state"] == "auto-off"
    monkeypatch.setattr(_gps, "local_gpsd_listening", lambda: True)
    monkeypatch.setattr(_gps, "gpsd_snapshot",
                        lambda h, p, timeout=2.5: {**_EMPTY, "ok": True, "state": "3d",
                                                   "lat": 1.0, "lon": 2.0})
    m = svc.gps_monitor()
    assert m["state"] == "3d" and m["resolved_source"] == "gpsd"
    assert svc.config() is cfg_before                     # no invalidation, no write
    assert "[gps]" not in ((tmp_path / "config" / "local.toml").read_text()
                            if (tmp_path / "config" / "local.toml").exists() else "")   # nothing persisted


_EMPTY = {"ok": False, "error": "", "state": "unavailable", "devices": [], "device": None,
          "mode": None, "lat": None, "lon": None, "alt": None, "alt_kind": None, "time": None,
          "time_has_date": False, "sats_used": None, "sats_seen": None, "satellites": [],
          "nmea": [], "nmea_ok": True, "note": ""}


def test_gpsd_source_unreachable_is_unavailable_and_never_raises(tmp_path):
    svc = _svc(tmp_path, source="gpsd", host="127.0.0.1", port=1)
    m = svc.gps_monitor()
    assert m["state"] == "unavailable" and m["label"] == "gpsd unavailable" and not m["nmea_ok"]


def test_gps_nmea_is_a_plain_list_and_gpsd_only(tmp_path, monkeypatch):
    assert _svc(tmp_path, source="off").gps_nmea() == []
    assert _svc(tmp_path, source="fixed", fixed_lat="1", fixed_lon="2").gps_nmea() == []
    monkeypatch.setattr(_gps, "gpsd_nmea_window", lambda h, p, **k: ["$GPGGA,x*00"])
    assert _svc(tmp_path, source="gpsd", host="127.0.0.1", port=2947).gps_nmea() == ["$GPGGA,x*00"]


# --- the ownership helper's corrected contract ------------------------------------------------

def test_ownership_matching_alias_is_owned(tmp_path, monkeypatch):
    dev = str(tmp_path / "tty")
    monkeypatch.setattr(_gps, "device_lock_key", lambda p: ("serial.dev.4:64", "") if p in (dev, "/dev/alias") else ("", "no"))
    monkeypatch.setattr(_gps, "gpsd_devices", lambda h, p, timeout=3.0: (["/dev/alias"], ""))
    assert _gps.gpsd_owns_device(dev, "127.0.0.1", 2947) == (True, "/dev/alias")


def test_ownership_unresolvable_local_path_is_indeterminate(monkeypatch):
    monkeypatch.setattr(_gps, "device_lock_key",
                        lambda p: ("serial.dev.4:64", "") if p == "/dev/mine" else ("", "gone"))
    monkeypatch.setattr(_gps, "gpsd_devices", lambda h, p, timeout=3.0: (["/dev/vanished"], ""))
    owned, detail = _gps.gpsd_owns_device("/dev/mine", "127.0.0.1", 2947)
    assert owned is None and "cannot establish" in detail


def test_ownership_network_source_and_unrelated_device_are_free(monkeypatch):
    keys = {"/dev/mine": ("serial.dev.4:64", ""), "/dev/other": ("serial.dev.4:65", "")}
    monkeypatch.setattr(_gps, "device_lock_key", lambda p: keys.get(p, ("", "no")))
    monkeypatch.setattr(_gps, "gpsd_devices",
                        lambda h, p, timeout=3.0: (["/dev/other", "tcp://feed:2000"], ""))
    assert _gps.gpsd_owns_device("/dev/mine", "127.0.0.1", 2947) == (False, "")


def test_ownership_refused_connection_is_free_timeout_is_indeterminate(monkeypatch):
    monkeypatch.setattr(_gps, "device_lock_key", lambda p: ("serial.dev.4:64", ""))
    monkeypatch.setattr(_gps, "gpsd_devices",
                        lambda h, p, timeout=3.0: ([], "ConnectionRefusedError: [Errno 111]"))
    assert _gps.gpsd_owns_device("/dev/mine", "127.0.0.1", 2947)[0] is False
    monkeypatch.setattr(_gps, "gpsd_devices", lambda h, p, timeout=3.0: ([], "TimeoutError: x"))
    assert _gps.gpsd_owns_device("/dev/mine", "127.0.0.1", 2947)[0] is None
