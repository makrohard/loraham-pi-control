"""The GPS Monitor's direct-receiver side, ALL FAKED (no NMEA hardware exists): the NmeaSnapshot
parser on canned sentences, the bridge's best-effort monitor socket over a FIFO device, and the
console's three states with the lock sequence over a pty device. The safety property under test is
"never a second reader on a serial port"."""

import json
import os
import socket
import threading
import time
from contextlib import ExitStack

import pytest

from lhpc.core import gps as _gps
from lhpc.core import reslock
from lhpc.core.config import save_gps
from lhpc.core.gps import NmeaSnapshot
from lhpc.core.model import RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def nmea(body: str) -> bytes:
    """A checksum-valid sentence from its body (without `$` and `*HH`)."""
    cs = 0
    for b in body.encode():
        cs ^= b
    return f"${body}*{cs:02X}".encode()


GGA_3D = nmea("GPGGA,123519.00,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
GGA_NOFIX = nmea("GPGGA,123520.00,,,,,0,00,99.9,,,,,,")
GGA_NOALT = nmea("GPGGA,123521.00,4807.038,N,01131.000,E,1,08,0.9,,M,46.9,M,,")
RMC_A = nmea("GPRMC,123522.00,A,4807.038,N,01131.000,E,0.1,0.0,190926,,,A")
RMC_V = nmea("GPRMC,123523.00,V,,,,,,,190926,,,N")
GSA_3D = nmea("GPGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1")
GSA_2D = nmea("GPGSA,A,2,04,05,,09,,,,,,,,,2.5,1.3,2.1")
GNS = nmea("GNGNS,123524.00,4807.038,N,01131.000,E,AA,10,0.9,123.4,46.9,,,V")
GSV_1 = nmea("GPGSV,2,1,07,04,72,010,40,05,15,080,32,09,44,190,25,12,30,250,")
GSV_2 = nmea("GPGSV,2,2,07,24,85,300,45,25,10,020,,29,05,340,22")
GSV_L5_1 = nmea("GPGSV,1,1,02,04,72,010,35,24,85,300,38,8")     # NMEA 4.10: signal id 8


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _parser():
    c = Clock()
    return NmeaSnapshot(clock=c), c


# --- validity, dimension, clearing ------------------------------------------------------------

def test_fresh_parser_is_no_data():
    p, _ = _parser()
    assert p.snapshot()["state"] == "no-data"


def test_cold_receiver_talking_stays_no_fix_never_stale():
    p, c = _parser()
    for _ in range(10):
        p.feed(GGA_NOFIX)
        c.t += 5
    s = p.snapshot()
    assert s["state"] == "no-fix" and s["lat"] is None and s["mode"] == 1


def test_usable_position_without_gsa_is_fix_dimension_unknown():
    p, _ = _parser()
    p.feed(GGA_3D)
    s = p.snapshot()
    assert s["state"] == "fix" and s["mode"] is None
    assert abs(s["lat"] - 48.1173) < 1e-3 and abs(s["lon"] - 11.5167) < 1e-3
    assert s["alt"] == 545.4 and s["alt_kind"] == "msl" and s["sats_used"] == 8


def test_gsa_refines_the_dimension():
    p, _ = _parser()
    p.feed(GGA_3D)
    p.feed(GSA_3D)
    assert p.snapshot()["state"] == "3d"
    p.feed(GSA_2D)
    assert p.snapshot()["state"] == "2d"


def test_no_fix_sentence_clears_position_and_altitude_at_once():
    p, _ = _parser()
    p.feed(GGA_3D)
    p.feed(GSA_3D)
    p.feed(RMC_V)
    s = p.snapshot()
    assert s["state"] == "no-fix" and s["lat"] is None and s["alt"] is None


def test_silence_becomes_stale_and_hides_everything():
    p, c = _parser()
    p.feed(GGA_3D)
    c.t += 21
    s = p.snapshot()
    assert s["state"] == "stale" and s["lat"] is None and s["alt"] is None


def test_fix_status_with_unusable_coordinates_is_no_fix():
    p, _ = _parser()
    bad = nmea("GPGGA,123519.00,9907.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")   # lat 99°
    p.feed(GGA_3D)
    p.feed(bad)
    s = p.snapshot()
    assert s["state"] == "no-fix" and s["lat"] is None
    p.feed(nmea("GPGGA,123519.00,4867.000,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"))  # 67 minutes: impossible
    assert p.snapshot()["state"] == "no-fix"


def test_cold_receiver_with_sky_but_no_navigation_is_no_fix():
    p, _ = _parser()
    p.feed(GSV_1)
    p.feed(GSV_2)
    snap = p.snapshot()
    assert snap["state"] == "no-fix" and snap["sats_seen"] == 7 and snap["lat"] is None


def test_used_is_per_constellation_when_gsa_carries_system_ids():
    """Audit P1: GP 4 in the fix says nothing about GA 4. GSA with NMEA 4.11 system ids
    (1 = GPS, 3 = Galileo) is matched to the GSV talker's constellation, never unioned."""
    p, _ = _parser()
    p.feed(nmea("GNGSA,A,3,04,05,,,,,,,,,,,2.5,1.3,2.1,1"))       # GPS: 4 and 5 used
    p.feed(nmea("GNGSA,A,3,07,,,,,,,,,,,,2.5,1.3,2.1,3"))         # Galileo: only 7 used
    p.feed(nmea("GPGSV,1,1,02,04,72,010,40,05,15,080,32"))
    p.feed(nmea("GAGSV,1,1,02,04,30,100,35,07,60,200,38"))
    used = {(s["talker"], s["prn"]): s["used"] for s in p.snapshot()["satellites"]}
    assert used == {("GP", 4): True, ("GP", 5): True, ("GA", 4): False, ("GA", 7): True}


def test_used_is_unknown_for_a_combined_gsa_over_several_constellations():
    p, _ = _parser()
    p.feed(nmea("GNGSA,A,3,04,07,,,,,,,,,,,2.5,1.3,2.1"))         # no system id, two talkers
    p.feed(nmea("GPGSV,1,1,01,04,72,010,40"))
    p.feed(nmea("GAGSV,1,1,01,04,30,100,35"))
    assert {s["used"] for s in p.snapshot()["satellites"]} == {None}


def test_signed_or_padded_degrees_are_not_a_coordinate():
    """Audit P2: the hemisphere carries the sign; a signed degree prefix is malformed input,
    not a different valid coordinate."""
    from lhpc.core.gps import _nmea_coord
    assert _nmea_coord(b"4807.038", b"N", True) is not None
    assert _nmea_coord(b"-807.038", b"N", True) is None
    assert _nmea_coord(b"+807.038", b"S", True) is None
    assert _nmea_coord(b" 807.038", b"N", True) is None
    assert _nmea_coord(b"4807.0e1", b"N", True) is None


def test_bad_checksum_is_ignored():
    p, _ = _parser()
    p.feed(b"$GPGGA,123519.00,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*00")
    assert p.snapshot()["state"] == "no-data" and p.errors == 0


def test_feed_never_raises_on_garbage():
    p, _ = _parser()
    for junk in (b"", b"$", b"$GPGSV,x,y", b"\xff\xfe$GPGGA", nmea("GPGSV,1,1,01,04"),
                 nmea("GPGSA,A,9,"), b"$GPRMC,*00"):
        p.feed(junk)
    assert p.snapshot()["state"] in ("no-data",)


# --- per-value provenance and age --------------------------------------------------------------

def test_altitude_ages_out_under_rmc_only_traffic():
    p, c = _parser()
    p.feed(GGA_3D)
    for _ in range(6):
        c.t += 5
        p.feed(RMC_A)
    s = p.snapshot()
    assert s["state"] == "fix" and s["lat"] is not None and s["alt"] is None


def test_missing_altitude_field_is_not_a_new_observation():
    p, _ = _parser()
    p.feed(GGA_3D)
    p.feed(GGA_NOALT)
    assert p.snapshot()["alt"] == 545.4


def test_gns_supplies_altitude_and_count():
    p, _ = _parser()
    p.feed(GNS)
    s = p.snapshot()
    assert s["state"] == "fix" and s["alt"] == 123.4 and s["sats_used"] == 10


def test_stale_gsa_falls_back_to_dimension_unknown():
    p, c = _parser()
    p.feed(GSA_3D)
    c.t += 21
    p.feed(GGA_3D)
    assert p.snapshot()["state"] == "fix"


def test_date_only_from_rmc_and_it_ages_separately():
    p, c = _parser()
    p.feed(GGA_3D)
    s = p.snapshot()
    assert s["time"] == "12:35:19" and s["time_has_date"] is False
    p.feed(RMC_A)
    s = p.snapshot()
    assert s["time"] == "2026-09-19 12:35:22" and s["time_has_date"]
    c.t += 21
    p.feed(GGA_3D)
    s = p.snapshot()
    assert s["time"] == "12:35:19" and s["time_has_date"] is False


# --- GSV / GSA satellites ----------------------------------------------------------------------

def test_gsv_assembly_used_flags_and_dedup_across_signals():
    p, _ = _parser()
    p.feed(GSV_1)
    assert p.snapshot()["sats_seen"] is None          # incomplete set: nothing yet
    p.feed(GSV_2)
    p.feed(GSA_3D)
    s = p.snapshot()
    by = {x["prn"]: x for x in s["satellites"]}
    assert s["sats_seen"] == 7 and by[4]["used"] is True and by[25]["used"] is False
    assert by[12]["ss"] is None and by[24]["ss"] == 45.0
    p.feed(GSV_L5_1)                                  # a second signal set: PRN 4 and 24 again
    s = p.snapshot()
    by = {x["prn"]: x for x in s["satellites"]}
    assert s["sats_seen"] == 7 and by[24]["ss"] == 45.0 and by[4]["ss"] == 40.0   # strongest wins


def test_gsv_count_change_and_new_first_fragment_reset_the_assembly():
    p, _ = _parser()
    p.feed(GSV_1)
    p.feed(nmea("GPGSV,3,2,09,24,85,300,45"))         # total changed mid-way: dropped
    assert p.snapshot()["sats_seen"] is None
    p.feed(GSV_1)
    p.feed(GSV_1)                                     # a new 1/N restarts, no double count
    p.feed(GSV_2)
    assert p.snapshot()["sats_seen"] == 7


def test_gsv_group_ages_out_and_used_is_unknown_without_gsa():
    p, c = _parser()
    p.feed(GSV_1)
    p.feed(GSV_2)
    s = p.snapshot()
    assert all(x["used"] is None for x in s["satellites"])
    c.t += 21
    assert p.snapshot()["sats_seen"] is None


def test_elevation_below_horizon_is_accepted():
    p, _ = _parser()
    p.feed(nmea("GPGSV,1,1,01,04,-10,010,40"))
    s = p.snapshot()
    assert s["satellites"][0]["el"] == -10.0


def test_tail_is_bounded_and_concurrent_feed_snapshot_is_safe():
    p, _ = _parser()
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            p.feed(GGA_3D)
            p.feed(GSV_1)
            p.feed(GSV_2)

    t = threading.Thread(target=writer)
    t.start()
    for _ in range(200):
        s = p.snapshot()
        assert len(s["nmea"]) <= 22
    stop.set()
    t.join()
    assert p.errors == 0


# --- the bridge's monitor socket over a FIFO device ------------------------------------------

def _fifo_bridge(tmp_path, monitor_ok=True):
    from lhpc.core.gps_bridge import MonitorServer, Readiness, _Output, _pump_nmea
    paths = Paths(runtime_root=tmp_path)
    fifo = tmp_path / "gps.fifo"
    os.mkfifo(fifo)

    class Collect(_Output):
        def __init__(self):
            super().__init__("", paths)
            self.data = b""

        def write(self, data):
            self.data += data

    out = Collect()
    ready = Readiness(str(tmp_path / "state" / "gps" / "meshcom" / "readiness.json"), paths)
    monitor = MonitorServer("meshcom", paths)
    if not monitor_ok:
        # force the publish to fail: a regular FILE where the state dir must be
        (tmp_path / "state" / "gps").mkdir(parents=True)
        (tmp_path / "state" / "gps" / "meshcom").write_text("not a dir")
    monitor.publish()
    stop = threading.Event()
    t = threading.Thread(target=_pump_nmea, args=(str(fifo), 9600, out, ready, stop),
                         kwargs={"monitor": monitor}, daemon=True)
    t.start()
    return paths, fifo, out, ready, monitor, stop, t


def _ask_monitor(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(path)
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(8192)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0])


def test_monitor_socket_answers_the_feeds_snapshot_and_is_private(tmp_path):
    paths, fifo, out, ready, monitor, stop, t = _fifo_bridge(tmp_path)
    try:
        assert monitor.enabled
        with open(fifo, "wb", buffering=0) as w:
            for line in (GGA_3D, GSA_3D, GSV_1, GSV_2):
                w.write(line + b"\r\n")
            time.sleep(0.5)
            got = _ask_monitor(monitor.path)
        assert got["state"] == "3d" and got["sats_seen"] == 7 and len(got["nmea"]) == 4
        assert oct(os.stat(monitor.path).st_mode & 0o777) == "0o600"
        assert oct(os.stat(os.path.dirname(monitor.path)).st_mode & 0o777) == "0o700"
        assert GGA_3D + b"\r\n" in out.data                    # the consumer got its bytes
        marker = (tmp_path / "state" / "gps" / "meshcom" / "readiness.json").read_text()
        assert "4807" not in marker and "lat" not in marker    # no coordinate on disk
    finally:
        stop.set()
        t.join(3)
        monitor.close()
    assert not os.path.exists(monitor.path)


def test_monitor_failure_never_touches_the_feed(tmp_path):
    paths, fifo, out, ready, monitor, stop, t = _fifo_bridge(tmp_path, monitor_ok=False)
    try:
        assert not monitor.enabled
        with open(fifo, "wb", buffering=0) as w:
            w.write(GGA_3D + b"\r\n")
            time.sleep(0.4)
        assert GGA_3D + b"\r\n" in out.data
        assert ready.state in ("connected", "running", "starting", "ready")
        monitor.feed(b"\xff\xfe")                              # the tee never raises
    finally:
        stop.set()
        t.join(3)
        monitor.close()


def test_a_parser_exception_carrying_a_coordinate_never_reaches_the_bridge_log(tmp_path, monkeypatch, capsys):
    """The tee logs an exception's TYPE only: the text may carry data. Forced here, since the
    parser itself never raises."""
    from lhpc.core.gps_bridge import MonitorServer
    monitor = MonitorServer("meshcom", Paths(runtime_root=tmp_path))

    def boom(self, line):
        raise ValueError("bad coordinate 48.117300 in " + line.decode("ascii", "replace"))
    monkeypatch.setattr(NmeaSnapshot, "feed", boom)
    monitor.feed(GGA_3D)
    monitor.feed(GGA_3D)
    err = capsys.readouterr().err
    assert "ValueError" in err and "48.117" not in err and "4807" not in err
    assert err.count("monitor parser error") == 1                 # logged once


def test_feed_output_is_byte_identical_with_and_without_a_monitor_client(tmp_path):
    paths, fifo, out, ready, monitor, stop, t = _fifo_bridge(tmp_path)
    try:
        with open(fifo, "wb", buffering=0) as w:
            w.write(GGA_3D + b"\r\n")
            time.sleep(0.3)
            before = out.data
            _ask_monitor(monitor.path)
            w.write(GSA_3D + b"\r\n")
            time.sleep(0.3)
        assert out.data == before + GSA_3D + b"\r\n"
    finally:
        stop.set()
        t.join(3)
        monitor.close()


# --- the console's three states over a pty ------------------------------------------------------

@pytest.fixture
def pty_device():
    master, slave = os.openpty()
    yield master, os.ttyname(slave)
    os.close(master)
    os.close(slave)


def _svc_nmea(tmp_path, device, monkeypatch, gpsd_free=True):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    save_gps(p, source="nmea", device=device, nmea_baud=9600)
    svc = ControllerService(system=FakeSystem().system, paths=p)
    monkeypatch.setattr(_gps, "gpsd_owns_device",
                        lambda d, h, po, timeout=3.0: (False, "no gpsd is listening") if gpsd_free
                        else (None, "cannot establish that the device is free"))
    return svc


def _snap_with(svc, states: dict):
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = states.get(cid, RunState.STOPPED)
    svc.build_snapshot = lambda fresh=False: snap
    return snap


def _opens(monkeypatch):
    calls = []
    real = os.open

    def spy(path, *a, **k):
        if isinstance(path, str) and "/pts/" in path:
            calls.append(path)
        return real(path, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    return calls


def test_idle_receiver_gives_one_sample_with_status_and_tail(tmp_path, pty_device, monkeypatch):
    master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {})
    svc._MONITOR_SAMPLE_S = 0.6
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            os.write(master, GGA_3D + b"\r\n" + GSA_3D + b"\r\n")
            time.sleep(0.1)
    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        m = svc.gps_monitor()
    finally:
        stop.set()
        t.join(1)
    assert m["state"] == "3d" and m["lat"] is not None and m["nmea"] and m["nmea_ok"]
    assert m["device"] == dev and "sample" in m["note"]
    # the claim was released: taking it now succeeds
    key = f"claim.gps.{svc.gps_settings()['device_key']}"
    with reslock.operation_lock(svc._paths, key, "test", "t"):
        pass


def test_native_reader_running_is_held_and_never_opens(tmp_path, pty_device, monkeypatch):
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {"meshtastic": RunState.RUNNING})
    calls = _opens(monkeypatch)
    m = svc.gps_monitor()
    assert m["state"] == "held" and "meshtastic" in m["note"] and "directly" in m["note"]
    assert calls == []


@pytest.mark.parametrize("cid", ["graywolf", "sideband", "meshtastic"])
@pytest.mark.parametrize("state", [RunState.RUNNING, RunState.DEGRADED, RunState.UNKNOWN])
def test_unknown_enablement_is_a_holder(tmp_path, pty_device, monkeypatch, cid, state):
    """F1: a native reader whose stack config is unreadable still holds the open device."""
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    from lhpc.core import config as cfgmod
    path = cfgmod._stack_config_path(svc._paths, svc._owner_stack_id(cid))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this = is = not = toml\n")
    svc._invalidate_config()
    _snap_with(svc, {cid: state})
    calls = _opens(monkeypatch)
    assert not svc.gps_enabled_for(cid)                      # the filtered helper says "off"...
    m = svc.gps_monitor()
    assert m["state"] == "held" and calls == []              # ...the Monitor still holds


def test_positively_disabled_consumer_is_not_a_holder(tmp_path, pty_device, monkeypatch):
    master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    from lhpc.core import config as cfgmod
    cfgmod.update_stack_config(svc._paths, "graywolf", {"use_gps": "off"})
    svc._invalidate_config()
    _snap_with(svc, {"graywolf": RunState.RUNNING})
    svc._MONITOR_SAMPLE_S = 0.3
    m = svc.gps_monitor()
    assert m["state"] != "held"


def test_orphan_feed_is_a_holder_and_broken_monitor_never_falls_through(tmp_path, pty_device, monkeypatch):
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {"meshcom-gps": RunState.RUNNING})       # the feed alone, no socket, no marker
    calls = _opens(monkeypatch)
    m = svc.gps_monitor()
    assert m["state"] == "held" and "monitor unavailable" in m["note"] and calls == []


def test_live_feed_with_working_monitor_is_via_feed(tmp_path, pty_device, monkeypatch):
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {"meshcom-gps": RunState.RUNNING})
    state_dir = tmp_path / "state" / "gps" / "meshcom"
    state_dir.mkdir(parents=True)
    (state_dir / "readiness.json").write_text(json.dumps(
        {"state": "running", "updated": int(time.time()), "pid": os.getpid()}))
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(state_dir / "monitor.sock"))
    srv.listen(1)
    body = json.dumps({**NmeaSnapshot().snapshot(), "ok": True, "state": "3d", "lat": 1.0,
                       "lon": 2.0, "nmea": ["$GPGGA,x*00"]}) + "\n"

    def serve():
        c, _ = srv.accept()
        c.sendall(body.encode())
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    calls = _opens(monkeypatch)
    try:
        m = svc.gps_monitor()
    finally:
        srv.close()
    assert m["state"] == "3d" and m["lat"] == 1.0 and "via the meshcom GPS feed" in m["note"]
    assert m["nmea"] == ["$GPGGA,x*00"] and calls == []


def test_gpsd_ownership_indeterminate_is_held(tmp_path, pty_device, monkeypatch):
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch, gpsd_free=False)
    _snap_with(svc, {})
    calls = _opens(monkeypatch)
    m = svc.gps_monitor()
    assert m["state"] == "held" and "cannot establish" in m["note"] and calls == []


def test_claim_held_by_another_operation_is_held(tmp_path, pty_device, monkeypatch):
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {})
    key = f"claim.gps.{svc.gps_settings()['device_key']}"
    calls = _opens(monkeypatch)
    with reslock.operation_lock(svc._paths, key, "start", "meshtastic"):
        m = svc.gps_monitor()
    assert m["state"] == "held" and "start" in m["note"] and calls == []


def test_two_concurrent_samples_serialise_on_the_claim(tmp_path, pty_device, monkeypatch):
    master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {})
    svc._MONITOR_SAMPLE_S = 0.5
    results = []

    def one():
        results.append(svc.gps_monitor()["state"])
    a, b = threading.Thread(target=one), threading.Thread(target=one)
    a.start()
    b.start()
    a.join(5)
    b.join(5)
    assert sorted(results).count("held") == 1                # one read, one held


def test_a_consumer_that_starts_during_the_check_is_seen_by_the_recheck(tmp_path, pty_device, monkeypatch):
    """The race the re-check exists for: the first (snapshot) check sees nothing running, a
    native consumer's process appears before the claim is taken, and the runtime re-check under
    the claim — process evidence, not the pinned snapshot — refuses the read."""
    _master, dev = pty_device
    fake = FakeSystem()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    save_gps(svc._paths, source="nmea", device=dev, nmea_baud=9600)
    monkeypatch.setattr(_gps, "gpsd_owns_device", lambda d, h, po, timeout=3.0: (False, ""))
    _snap_with(svc, {})                                    # the pre-claim picture: all stopped
    fake.cmdlines_data[4242] = ["meshtasticd", "--fsdir", "x"]   # ... but the process is there
    calls = _opens(monkeypatch)
    m = svc.gps_monitor()
    assert m["state"] == "held" and "started while" in m["note"] and calls == []


def test_a_stalled_source_probe_cannot_extend_the_claim(tmp_path, pty_device, monkeypatch):
    """Audit P1: the re-check under the claim must not build a snapshot — a source probe
    (git, 3 s per checkout) would hold the receiver claim past the lifecycle's 5 s wait. A
    stalled probe and a stalled snapshot are both left running; the claim is held only for the
    runtime check and the bounded read, and the snapshot is built exactly once, BEFORE it."""
    from lhpc.core import status as _status
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    svc._MONITOR_SAMPLE_S = 0.4
    held: list = []
    probes_before: list = []
    real_probe = _status.probe_source

    def probe(*a, **k):
        if held and len(held[-1]) == 2:                     # inside the claim: stall, then fail
            time.sleep(3.0)
            raise AssertionError("a source probe ran under the receiver claim")
        probes_before.append(time.monotonic())              # before it: the real (fast) probe
        return real_probe(*a, **k)
    monkeypatch.setattr(_status, "probe_source", probe)
    real_lock = reslock.operation_lock

    def timed_lock(paths, key, op, target, **kw):
        cm = real_lock(paths, key, op, target, **kw)

        class _Timed:
            def __enter__(self):
                held.append(["in", time.monotonic()])
                return cm.__enter__()

            def __exit__(self, *exc):
                held[-1].append(time.monotonic())
                return cm.__exit__(*exc)
        return _Timed()
    monkeypatch.setattr(reslock, "operation_lock", timed_lock)
    m = svc.gps_monitor()                                   # unpinned: the real prober runs
    assert m["state"] == "no-data"                          # the sample ran (silent pty)
    assert probes_before and len(held) == 1
    assert max(probes_before) < held[0][1]                  # every source probe BEFORE the claim
    assert held[0][2] - held[0][1] < svc._MONITOR_SAMPLE_S + 0.5   # claim = check + read only


def test_budget_exhaustion_abandons_the_sample_and_releases(tmp_path, pty_device, monkeypatch):
    """The runtime re-check itself is bounded: a unit probe that stalls (systemctl hanging)
    exhausts the hold budget, the sample is abandoned and the claim released."""
    from lhpc.core import service_params as _sp
    _master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {})
    svc._MONITOR_HOLD_BUDGET_S = 0.3
    real_holders = svc._gps_runtime_holders

    def slow(deadline):
        time.sleep(0.5)
        return real_holders(deadline)
    monkeypatch.setattr(svc, "_gps_runtime_holders", slow)
    assert _sp is not None
    calls = _opens(monkeypatch)
    t0 = time.monotonic()
    m = svc.gps_monitor()
    assert m["state"] == "held" and "budget" in m["note"] and calls == []
    assert time.monotonic() - t0 < 2.0
    key = f"claim.gps.{svc.gps_settings()['device_key']}"
    with reslock.operation_lock(svc._paths, key, "test", "t"):     # released
        pass


def test_bytes_without_sentences_is_no_data_binary_hint(tmp_path, pty_device, monkeypatch):
    master, dev = pty_device
    svc = _svc_nmea(tmp_path, dev, monkeypatch)
    _snap_with(svc, {})
    svc._MONITOR_SAMPLE_S = 0.4
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            os.write(master, b"\xb5\x62\x01\x07" * 20)
            time.sleep(0.05)
    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        m = svc.gps_monitor()
    finally:
        stop.set()
        t.join(1)
    assert m["state"] == "no-data" and "binary" in m["note"]


# --- the lifecycle's narrow wait for the monitor's claim (separate processes) -----------------

_HOLD_SCRIPT = """
import fcntl, json, os, sys, time
root, key, mode, hold = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
from lhpc.core import reslock, runtime_fs
from pathlib import Path
from lhpc.core.paths import Paths
paths = Paths(runtime_root=Path(root))
if mode == "published":
    with reslock.operation_lock(paths, key, "gps-monitor", "gps"):
        print("held", flush=True); time.sleep(hold)
elif mode == "other":
    with reslock.operation_lock(paths, key, "build", "x"):
        print("held", flush=True); time.sleep(hold)
else:  # "unpublished": the flock without an owner record (mid-publication / mid-release window)
    from pathlib import Path
    lockfile = paths.under("state", "locks", reslock.canonical_key(key) + ".lock")
    fh = runtime_fs.open_lock(paths, lockfile)
    fcntl.flock(fh, fcntl.LOCK_EX)
    print("held", flush=True); time.sleep(hold)
    fcntl.flock(fh, fcntl.LOCK_UN)
"""


def _hold_in_child(tmp_path, key, mode, hold):
    import subprocess
    import sys
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    p = subprocess.Popen([sys.executable, "-c", _HOLD_SCRIPT, str(tmp_path), key, mode, str(hold)],
                         stdout=subprocess.PIPE, text=True, env=env)
    assert p.stdout.readline().strip() == "held"
    return p


def _acquire(svc, key, op):
    with ExitStack() as stack:
        svc._acquire_key(stack, key, op, "meshtastic")
        return True


@pytest.mark.parametrize("mode", ["published", "unpublished"])
def test_a_start_waits_for_the_monitors_bounded_claim(tmp_path, mode):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    key = "claim.gps.serial.dev.4:64"
    child = _hold_in_child(tmp_path, key, mode, 1.0)
    try:
        t0 = time.monotonic()
        assert _acquire(svc, key, "start")
        assert 0.5 < time.monotonic() - t0 < 4.0
    finally:
        child.wait(5)


def test_a_start_still_fails_fast_on_another_external_operation(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    key = "claim.gps.serial.dev.4:64"
    child = _hold_in_child(tmp_path, key, "other", 1.0)
    try:
        t0 = time.monotonic()
        with pytest.raises(reslock.ResourceBusy):
            _acquire(svc, key, "start")
        assert time.monotonic() - t0 < 0.5
    finally:
        child.wait(5)


def test_a_holder_that_never_releases_still_fails_within_the_deadline(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc._SELF_LOCK_WAIT_S = 0.6
    key = "claim.gps.serial.dev.4:64"
    child = _hold_in_child(tmp_path, key, "published", 5.0)
    try:
        t0 = time.monotonic()
        with pytest.raises(reslock.ResourceBusy):
            _acquire(svc, key, "start")
        assert time.monotonic() - t0 < 2.0
    finally:
        child.terminate()
        child.wait(5)


def test_only_gps_keys_during_start_get_the_wait(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    child = _hold_in_child(tmp_path, "claim.loraham.radio.433", "published", 1.0)
    try:
        with pytest.raises(reslock.ResourceBusy):
            _acquire(svc, "claim.loraham.radio.433", "start")
    finally:
        child.wait(5)
    child = _hold_in_child(tmp_path, "claim.gps.serial.dev.4:64", "published", 1.0)
    try:
        with pytest.raises(reslock.ResourceBusy):
            _acquire(svc, "claim.gps.serial.dev.4:64", "stop")
    finally:
        child.wait(5)
