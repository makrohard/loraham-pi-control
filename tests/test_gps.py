"""GPS: one authoritative source for every stack.

These cover the contracts that were corrected in review — the ones where a wrong answer is
silent rather than loud: a half-parsed source that leaves a stack blind, a device claimed
under two different names, a feed that reports ready when its source is gone, a node left in
the opposite GPS state after a "successful" start.

No real coordinates anywhere: Greenwich is used wherever a concrete position is needed.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from unittest import mock

import pytest

from lhpc.core import gps as gps_mod
from lhpc.core.config import ConfigError, _parse_gps, load_config, save_gps

# Greenwich — publicly known, obviously synthetic, and never anyone's home.
LAT, LON, ALT = "51.4779", "-0.0015", "45"


# --- [gps] parses fail-closed -------------------------------------------------------------

@pytest.mark.contract
@pytest.mark.safety("gps-fail-closed")
@pytest.mark.parametrize("raw", [
    {"source": "bogus"},                                   # unknown source
    {"source": "nmea"},                                    # device missing
    {"source": "nmea", "device": "ttyACM0"},               # not absolute
    {"source": "fixed", "fixed_lat": LAT},                 # half a position
    {"source": "fixed", "fixed_lat": "nan", "fixed_lon": "0"},     # not finite
    {"source": "fixed", "fixed_lat": "91", "fixed_lon": "0"},      # out of range
    {"source": "gpsd", "port": 0},                         # port out of range
    {"source": "gpsd", "port": True},                      # bool is not a port
    {"source": "gpsd", "nmea_baud": 1234},                 # unsupported baud
    "not-a-table",
])
def test_malformed_gps_disables_position_rather_than_half_enabling_it(raw):
    """A half-understood source is worse than none: a stack would start believing it reports
    position and silently report nothing (or something wrong)."""
    diags = []
    cfg = _parse_gps({"gps": raw}, diags)
    assert cfg.source == "off"
    assert cfg.valid is False and cfg.reason
    assert diags, "a disabled source must be explained, not silently swallowed"


def test_a_valid_fixed_position_survives_parsing():
    cfg = _parse_gps({"gps": {"source": "fixed", "fixed_lat": LAT,
                              "fixed_lon": LON, "fixed_alt": ALT}}, [])
    assert (cfg.source, cfg.valid) == ("fixed", True)
    assert (cfg.fixed_lat, cfg.fixed_lon, cfg.fixed_alt) == (LAT, LON, ALT)


def test_only_direct_nmea_claims_a_local_serial_device():
    """off / fixed / remote gpsd open no local device; claiming one would refuse valid
    combinations for no reason."""
    def claims(**kw):
        return _parse_gps({"gps": kw}, []).claims_local_serial

    assert claims(source="nmea", device="/dev/ttyACM0") is True
    assert claims(source="gpsd") is False
    assert claims(source="gpsd", host="192.168.1.5") is False
    assert claims(source="fixed", fixed_lat=LAT, fixed_lon=LON) is False
    assert claims(source="off") is False


def test_local_gpsd_is_about_the_host_not_the_source():
    """Drives the soft dependency: an operator whose gpsd runs on another box must never be
    told they are missing a local package."""
    def local(**kw):
        return _parse_gps({"gps": kw}, []).local_gpsd

    assert local(source="gpsd", host="127.0.0.1") is True
    assert local(source="gpsd", host="::1") is True
    assert local(source="gpsd", host="localhost") is True
    assert local(source="gpsd", host="192.168.1.5") is False
    assert local(source="nmea", device="/dev/ttyACM0") is False


# --- save_gps validates the WHOLE table ---------------------------------------------------

def test_save_gps_rejects_incomplete_combinations_before_writing(tmp_path):
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    for kw in ({"source": "nmea"},
               {"source": "fixed", "fixed_lat": LAT},
               {"source": "gpsd", "nmea_baud": 1234},
               {"source": "nowhere"}):
        with pytest.raises(ConfigError):
            save_gps(paths, **kw)
    # Nothing partial was persisted by the rejected calls -> the default (auto) still applies.
    assert load_config(paths).gps.source == "auto"


def test_save_gps_patches_only_the_named_fields(tmp_path):
    """`lhpc gps --host X` must not wipe the device: the fields are interdependent, so the
    whole table is rewritten from the CURRENT values plus the change."""
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    save_gps(paths, source="nmea", device="/dev/ttyACM0", nmea_baud=4800)
    save_gps(paths, host="192.168.1.5")
    g = load_config(paths).gps
    assert (g.source, g.device, g.nmea_baud, g.host) == ("nmea", "/dev/ttyACM0", 4800, "192.168.1.5")


# --- device identity ----------------------------------------------------------------------

def test_a_device_and_its_alias_resolve_to_one_lock_key(tmp_path):
    """`/dev/ttyACM0` and `/dev/serial/by-id/...` are the SAME receiver. A claim keyed on the
    string would let one stack take each and both believe they held it exclusively."""
    real = next((p for p in ("/dev/ttyACM0", "/dev/ttyUSB0", "/dev/null") if os.path.exists(p)),
                None)
    if real is None:
        pytest.skip("no character device available")
    key, err = gps_mod.device_lock_key(real)
    assert key and not err
    assert key.startswith("serial.dev.")
    st = os.stat(real)
    assert key == f"serial.dev.{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"


def test_a_non_device_path_is_refused_not_guessed(tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("x")
    key, err = gps_mod.device_lock_key(str(f))
    assert key == "" and "character device" in err
    key, err = gps_mod.device_lock_key(str(tmp_path / "missing"))
    assert key == "" and err


# --- the resolved plan --------------------------------------------------------------------

def _plan(**kw):
    class _Cfg:
        gps = None
    cfg = _Cfg()
    cfg.gps = _parse_gps({"gps": kw}, [])
    return gps_mod.plan_from_config(cfg, resolve_device=False)


def test_only_the_consumers_that_need_a_feed_get_one():
    """Meshtastic needs a feed ONLY for gpsd: it reads `nmea` straight off the real device
    (and then detects a real chip) and uses its own fixed-position support. MeshCom needs one
    for every source, because its pinned relay is loopback-gpsd-only."""
    mt, mc = gps_mod.CONSUMER_MESHTASTIC, gps_mod.CONSUMER_MESHCOM
    assert _plan(source="gpsd").needs_bridge(mt) is True
    assert _plan(source="nmea", device="/dev/ttyACM0").needs_bridge(mt) is False
    assert _plan(source="fixed", fixed_lat=LAT, fixed_lon=LON).needs_bridge(mt) is False
    assert _plan(source="off").needs_bridge(mt) is False
    for src, kw in (("gpsd", {}), ("nmea", {"device": "/dev/ttyACM0"}),
                    ("fixed", {"fixed_lat": LAT, "fixed_lon": LON})):
        assert _plan(source=src, **kw).needs_bridge(mc) is True
    assert _plan(source="off").needs_bridge(mc) is False


def test_consumers_get_the_output_shape_they_can_actually_read():
    assert _plan(source="gpsd").output_kind(gps_mod.CONSUMER_MESHTASTIC) == gps_mod.OUT_PTY
    assert _plan(source="gpsd").output_kind(gps_mod.CONSUMER_MESHCOM) == gps_mod.OUT_UNIX


def test_meshcom_feeds_qemus_own_socket_and_does_not_publish_one(tmp_path):
    """ORIENTATION. QEMU is the SERVER: it creates `.run/gps-uart1.sock`
    (`server=on,wait=off`) and the feed CONNECTS to it — the same thing the pinned
    `gps-relay.py` does.

    A feed that listened instead would publish a socket nothing ever connects to: it would
    come up, report healthy, and deliver no position at all. An earlier version of this code
    had it backwards, and a test that connected our own client to our own server "passed"
    while proving nothing about QEMU.
    """
    ep = gps_mod.bridge_endpoint_path(tmp_path, gps_mod.CONSUMER_MESHCOM)
    assert ep.endswith(os.path.join(*gps_mod.MESHCOM_SOURCE_REL)), \
        "MeshCom's endpoint is QEMU's socket in the MeshCom source tree"
    assert gps_mod.bridge_state_dir(tmp_path, gps_mod.CONSUMER_MESHCOM) not in ep, \
        "we must not invent our own socket under state/"


def test_the_meshcom_feed_waits_for_qemu_instead_of_failing(tmp_path):
    """QEMU boots slowly (minutes under emulation) and can restart. The feed must keep
    retrying rather than exiting, and must not block while the guest is absent."""
    from lhpc.core.gps_bridge import UnixClientOutput
    out = UnixClientOutput(str(tmp_path / "absent.sock"))
    out.publish()                      # no server yet -> must not raise
    assert out.connected is False
    out.write(b"$GPGGA,,,,,,0,,,,,,,,*66\r\n")   # must not raise, must not block
    assert out.written == 0

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(tmp_path / "absent.sock"))
    srv.listen(1)
    try:
        out._next_try = 0.0            # skip the backoff window for the test
        out.write(b"$GPGGA,,,,,,0,,,,,,,,*66\r\n")
        conn, _ = srv.accept()
        try:
            assert out.connected is True and out.written > 0
            assert conn.recv(64).startswith(b"$GPGGA")
        finally:
            conn.close()
    finally:
        srv.close()
        out.close()


def test_meshtastic_gps_mode_is_pushed_in_both_directions():
    """Applying only the ON direction leaves a previously-enabled node beaconing after GPS was
    turned off — a start that reports success while the node holds the opposite state."""
    V = gps_mod.meshtastic_post_step_values
    assert V(_plan(source="gpsd"))["gps_mode"] == "ENABLED"
    assert V(_plan(source="nmea", device="/dev/ttyACM0"))["gps_mode"] == "ENABLED"
    assert V(_plan(source="off"))["gps_mode"] == "NOT_PRESENT"
    # A fixed station must not also run the GPS thread hunting for a chip.
    assert V(_plan(source="fixed", fixed_lat=LAT, fixed_lon=LON))["gps_mode"] == "NOT_PRESENT"


def test_fixed_uses_native_position_and_is_always_cleared_when_not_fixed():
    V = gps_mod.meshtastic_post_step_values
    args = V(_plan(source="fixed", fixed_lat=LAT, fixed_lon=LON, fixed_alt=ALT))["gps_fixed_args"]
    assert args[:2] == ["--setlat", LAT] and "--setlon" in args and "--setalt" in args
    # No memory of the previous source: `[gps]` is authoritative, so "not fixed" must mean the
    # node holds no fixed position — including one set by hand outside lhpc.
    for src, kw in (("gpsd", {}), ("off", {}), ("nmea", {"device": "/dev/ttyACM0"})):
        assert V(_plan(source=src, **kw))["gps_fixed_args"] == ["--remove-position"]


def test_coordinates_never_survive_redaction():
    red = gps_mod.redact("$GPGGA,151345.00,5128.6740,N,00000.0900,W,1,09")
    assert "5128" not in red and "nmea redacted" in red
    assert "<redacted>" in gps_mod.redact('{"class":"TPV","lat":51.4779,"lon":-0.0015}')


# --- the bridge -----------------------------------------------------------------------------

def test_synthesized_fixed_sentences_are_valid_nmea():
    from lhpc.core.gps_bridge import _nmea_checksum, fixed_sentences
    out = fixed_sentences(float(LAT), float(LON), float(ALT), time.gmtime(0)).decode()
    lines = [ln for ln in out.split("\r\n") if ln]
    assert len(lines) == 2
    for line in lines:
        body, _, ck = line[1:].partition("*")
        assert _nmea_checksum(body) == ck, "a consumer discards a bad checksum silently"
    assert "GPGGA" in lines[0] and "GPRMC" in lines[1]


def test_the_pty_feed_is_drained_so_a_probing_consumer_cannot_block_it(tmp_path):
    """meshtasticd PROBES the chip and writes into the PTY. With nothing draining, that buffer
    fills and blocks the writer — the feed stops and the node silently loses position."""
    from lhpc.core.gps_bridge import PtyOutput
    from lhpc.core.paths import Paths
    out = PtyOutput(str(tmp_path / "gps" / "nmea0"), Paths(runtime_root=tmp_path))
    out.publish()
    try:
        fd = os.open(str(tmp_path / "gps" / "nmea0"), os.O_RDWR)
        try:
            for _ in range(400):                       # far more than one buffer
                os.write(fd, b"$PDTINFO*3F\r\n")
            deadline = time.time() + 3.0
            while out.drained == 0 and time.time() < deadline:
                time.sleep(0.05)
            assert out.drained > 0, "probe traffic was never drained"
            out.write(b"$GPGGA,000000.00,,,,,0,00,,,M,,M,,*66\r\n")
            assert out.written > 0, "feed blocked behind undrained probe traffic"
        finally:
            os.close(fd)
    finally:
        out.close()


def test_a_stopped_feed_leaves_nothing_that_looks_live(tmp_path):
    """A leftover symlink and a readiness file still saying "ready" is a stopped GPS that
    reads healthy — the exact thing status must never show."""
    from lhpc.core.gps_bridge import PtyOutput, Readiness
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    link = tmp_path / "gps" / "nmea0"
    out = PtyOutput(str(link), paths)
    out.publish()
    ready = Readiness(str(tmp_path / "gps" / "readiness.json"), paths)
    ready.note_data(2, 2, 2)                     # a live feed with a real position
    assert link.is_symlink() and os.path.exists(ready.path)
    ready.clear()
    out.close()
    assert not link.is_symlink() and not link.exists()
    assert not os.path.exists(ready.path)


def test_readiness_follows_the_source_not_the_endpoint(tmp_path):
    """A PTY exists the moment it is created, long before any position flows; readiness that
    trusted the path would report a healthy GPS for a dead gpsd."""
    from lhpc.core.gps_bridge import Readiness
    from lhpc.core.paths import Paths
    ready = Readiness(str(tmp_path / "readiness.json"), Paths(runtime_root=tmp_path))
    assert ready.state == "starting"
    ready.note_data(3, 3, 3)                     # three sentences, all carrying a position
    assert ready.state == "ready"
    ready.last_ok = time.time() - 999
    ready.tick()
    assert ready.state == "stale", "a feed with no sentences must stop reading as ready"
    ready.degrade("source-lost", "gpsd unreachable")
    assert ready.state == "source-lost"


def test_the_fixed_pump_keeps_emitting_with_a_current_timestamp(tmp_path):
    """A consumer discards a fix whose timestamp is stale, so a station that does not move
    must still tick."""
    from lhpc.core.gps_bridge import Readiness, _Output, _pump_fixed

    class _Collect(_Output):
        def __init__(self):
            super().__init__("", None)
            self.chunks = []

        def write(self, data):
            self.chunks.append(data)
            self._bytes += len(data)

    from lhpc.core.paths import Paths
    out, ready = _Collect(), Readiness(str(tmp_path / "r.json"), Paths(runtime_root=tmp_path))
    stop = threading.Event()
    t = threading.Thread(target=_pump_fixed,
                         args=(float(LAT), float(LON), float(ALT), out, ready, stop),
                         daemon=True)
    t.start()
    deadline = time.time() + 3.0
    while len(out.chunks) < 2 and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=2.0)
    assert len(out.chunks) >= 2, "a fixed position must keep ticking, not emit once"
    assert ready.state == "ready"


# --- lifecycle: the plan is resolved BEFORE locks, and drives admission -------------------

def _svc(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(paths=Paths(runtime_root=tmp_path))


def _enable(svc, *stacks):
    """Turn ON the PERSISTED per-stack GPS switch.

    Position needs BOTH a global source and the stack opting in — a non-off source must not
    silently start reporting position from every stack at once. Tests that exercise an active
    GPS path therefore have to say which stacks opted in.
    """
    for sid in stacks:
        svc.save_stack_config(sid, {"use_gps": "on"})


def _feeds(svc, stack):
    order = svc._run_order(stack) or []
    return [c.id for _s, c in order if "gps" in c.id]


@pytest.mark.contract
@pytest.mark.parametrize("src,kw,mt,mc", [
    ("off",   {},                                      [], []),
    ("gpsd",  {},                                      ["meshtastic-gps"], ["meshcom-gps"]),
    ("nmea",  {"device": "/dev/null"},                 [], ["meshcom-gps"]),
    ("fixed", {"fixed_lat": LAT, "fixed_lon": LON},    [], ["meshcom-gps"]),
])
def test_the_gps_plan_decides_which_feed_components_run(tmp_path, src, kw, mt, mc):
    """A `use_gps` flag could not do this: run order is built from static manifest data plus
    saved autostart flags, so the plan has to be resolved here — before anything downstream
    takes a lock — or claims and rendering would describe a different plan than the one that
    actually ran."""
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source=src, **kw)
    svc._invalidate_config()
    assert _feeds(svc, "meshtastic") == mt
    assert _feeds(svc, "meshcom") == mc


def test_the_fixture_relay_never_runs_beside_the_production_feed(tmp_path):
    """Both write the SAME UART socket. Side by side, the node would receive a synthetic
    position interleaved with the real one and nothing could tell which it beaconed — so when
    a global source is configured, production wins.

    The relay otherwise stays an ordinary opt-in component (that is what makes it "explicit
    and test-only"), and an explicit run is always possible.
    """
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="gpsd")
    svc._invalidate_config()
    feeds = _feeds(svc, "meshcom")
    assert "meshcom-gps" in feeds and "meshcom-gps-relay" not in feeds

    order = [c.id for _s, c in (svc._run_order("meshcom-gps-relay") or [])]
    assert order == ["meshcom-gps-relay"], "an explicit fixture run must still be possible"


def test_the_feed_starts_before_the_app_that_reads_it(tmp_path):
    """MeshCom's GPS init is one-shot and meshtasticd opens the device at startup — a feed
    that appears afterwards is a feed that was never seen."""
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="gpsd")
    svc._invalidate_config()
    order = [c.id for _s, c in (svc._run_order("meshtastic") or [])]
    assert order.index("meshtastic-gps") < order.index("meshtastic")


def test_a_broken_gps_section_blocks_the_start_instead_of_starting_blind(tmp_path):
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic')
    (tmp_path / "config" / "local.toml").write_text('[gps]\nsource = "bogus"\n')
    svc._invalidate_config()
    reason, nxt = svc.gps_block("meshtastic")
    assert "invalid" in reason and nxt
    # A stack with no GPS consumer is unaffected.
    assert svc.gps_block("daemon") == ("", [])


@pytest.mark.contract
@pytest.mark.safety("gps-receiver-exclusive")
def test_direct_nmea_is_refused_when_gpsd_owns_the_receiver(tmp_path, monkeypatch):
    """Two readers on one receiver lose fixes intermittently rather than failing cleanly, so
    this is refused up front instead of surfacing later as flaky position."""
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="nmea", device="/dev/null")
    svc._invalidate_config()

    monkeypatch.setattr(gps_mod, "gpsd_owns_device", lambda *a, **k: (True, "/dev/null"))
    reason, nxt = svc.gps_block("meshtastic")
    assert "already owns" in reason and nxt == ["lhpc gps --source gpsd"]

    # Unprovable is ALSO a refusal — never an assumption that the device is free.
    monkeypatch.setattr(gps_mod, "gpsd_owns_device", lambda *a, **k: (None, "connection refused"))
    reason, _ = svc.gps_block("meshtastic")
    assert "cannot prove" in reason

    monkeypatch.setattr(gps_mod, "gpsd_owns_device", lambda *a, **k: (False, ""))
    assert svc.gps_block("meshtastic") == ("", [])


def test_sources_that_open_no_local_device_are_never_ownership_checked(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    called = []
    monkeypatch.setattr(gps_mod, "gpsd_owns_device",
                        lambda *a, **k: (called.append(a), (True, "x"))[1])
    for src, kw in (("off", {}), ("gpsd", {}), ("gpsd", {"host": "192.168.1.5"}),
                    ("fixed", {"fixed_lat": LAT, "fixed_lon": LON})):
        save_gps(svc._paths, source=src, **kw)
        svc._invalidate_config()
        assert svc.gps_block("meshtastic") == ("", [])
    assert not called, "no local device is opened, so nothing may be ownership-checked"


# --- the global setting is authoritative over per-stack values ----------------------------

def test_stack_configs_are_filled_from_the_global_source(tmp_path):
    """Sideband's position fields are hidden and controller-filled: a stale saved value
    cannot compete with the global setting, because it is never read."""
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="gpsd", host="192.168.1.5", port=2948)
    svc._invalidate_config()
    v = svc._gps_config_values("sideband")
    assert v["{gps_source}"] == "gpsd"
    assert v["{gps_host}"] == "192.168.1.5" and v["{gps_port}"] == "2948"
    # Sideband speaks gpsd natively, so it needs no device path.
    assert v["{gps_device}"] == ""

    save_gps(svc._paths, source="nmea", device="/dev/null", nmea_baud=4800)
    svc._invalidate_config()
    v = svc._gps_config_values("meshtastic")
    assert v["{gps_device}"] == "/dev/null", "direct NMEA points at the REAL receiver"
    assert v["{gps_baud}"] == "4800"


def test_meshtastic_gets_the_feed_path_only_for_gpsd(tmp_path):
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="gpsd")
    svc._invalidate_config()
    assert svc._gps_config_values("meshtastic")["{gps_device}"].endswith("/meshtastic/nmea0")
    for src, kw in (("off", {}), ("fixed", {"fixed_lat": LAT, "fixed_lon": LON})):
        save_gps(svc._paths, source=src, **kw)
        svc._invalidate_config()
        assert svc._gps_config_values("meshtastic")["{gps_device}"] == "", \
            "with no stream the key must be OMITTED, not written blank"


def test_stale_per_stack_position_values_are_reported_not_silently_ignored(tmp_path):
    svc = _svc(tmp_path)
    _enable(svc, 'reticulum')
    cfgdir = tmp_path / "config" / "stacks"
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "reticulum.toml").write_text('file_location_source = "nmea"\nfile_gpsd_host = "10.0.0.9"\n')
    legacy = svc.legacy_gps_values()
    assert "reticulum" in legacy
    assert {"gpsd_host", "location_source"} <= set(legacy["reticulum"])


def test_no_gpsd_listening_means_the_device_is_free_not_unprovable(monkeypatch, tmp_path):
    """Refusing here would make direct-NMEA impossible on a box that runs no gpsd — exactly
    the case the mode exists for. Nothing listening proves no daemon holds the receiver."""
    dev = tmp_path / "dev"
    monkeypatch.setattr(gps_mod, "device_lock_key", lambda p: ("serial.dev.1:2", ""))

    monkeypatch.setattr(gps_mod, "gpsd_devices",
                        lambda *a, **k: ([], "ConnectionRefusedError: [Errno 111] Connection refused"))
    assert gps_mod.gpsd_owns_device(str(dev), "127.0.0.1", 2947) == (False, "no gpsd is listening")

    # A reachable-but-silent gpsd stays UNKNOWN: the daemon may be alive and holding it.
    monkeypatch.setattr(gps_mod, "gpsd_devices",
                        lambda *a, **k: ([], "gpsd did not report a DEVICES message"))
    owned, _ = gps_mod.gpsd_owns_device(str(dev), "127.0.0.1", 2947)
    assert owned is None
    monkeypatch.setattr(gps_mod, "gpsd_devices", lambda *a, **k: ([], "TimeoutError: timed out"))
    owned, _ = gps_mod.gpsd_owns_device(str(dev), "127.0.0.1", 2947)
    assert owned is None


# --- the generated Sideband config, not just the resolved values ---------------------------

def _render_location_conf(svc):
    """Generate the real config files and return sideband's location.conf as text."""
    svc.write_config_files("reticulum")
    path = svc._paths.runtime_root / "state" / "sideband" / "location.conf"
    return path.read_text() if path.exists() else ""


def test_sideband_location_config_is_written_from_the_global_source(tmp_path):
    """Values resolving correctly is not the same as them reaching the file Sideband reads.
    This generates the real config and checks its contents."""
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="gpsd", host="192.168.1.5", port=2948, nmea_baud=19200)
    svc._invalidate_config()
    body = _render_location_conf(svc).replace(" ", "")
    assert body, "sideband's location.conf must be generated"
    assert "location_source=gpsd" in body
    assert "gpsd_host=192.168.1.5" in body
    assert "gpsd_port=2948" in body
    assert "nmea_baud=19200" in body


def test_a_saved_per_stack_position_value_cannot_override_the_global_source(tmp_path):
    """The whole point of one authoritative setting: a stale value on disk must not win."""
    svc = _svc(tmp_path)
    cfgdir = tmp_path / "config" / "stacks"
    cfgdir.mkdir(parents=True, exist_ok=True)
    # Legacy values FIRST: the switch is stored in this same bandless file, so enabling
    # afterwards must not be clobbered by a hand-written config.
    (cfgdir / "reticulum.toml").write_text(
        'file_location_source = "nmea"\nfile_gpsd_host = "10.9.9.9"\n')
    _enable(svc, 'reticulum')
    save_gps(svc._paths, source="gpsd", host="192.168.1.5")
    svc._invalidate_config()
    body = _render_location_conf(svc).replace(" ", "")
    assert "location_source=gpsd" in body, "the GLOBAL source must win"
    assert "10.9.9.9" not in body, "a stale per-stack host must never reach the file"


def test_a_direct_receiver_is_an_exclusive_lifecycle_claim(tmp_path):
    """Not just a display value. Without the key in the LOCK set, two stacks configured for
    direct NMEA both open the same receiver — two readers on one device, which loses fixes
    intermittently instead of failing cleanly."""
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="nmea", device="/dev/null")
    svc._invalidate_config()
    keys = [k for k in svc._operation_resource_keys("meshtastic") if k.startswith("gps.")]
    assert len(keys) == 1 and keys[0].startswith("gps.serial.dev."), \
        "direct NMEA must contribute an exclusive claim keyed on the real device"

    # Sources that open no local device must claim nothing, or they would refuse valid combos.
    for kw in ({"source": "off"}, {"source": "gpsd"}, {"source": "gpsd", "host": "192.168.1.5"},
               {"source": "fixed", "fixed_lat": LAT, "fixed_lon": LON}):
        save_gps(svc._paths, **kw)
        svc._invalidate_config()
        assert not [k for k in svc._operation_resource_keys("meshtastic") if k.startswith("gps.")]


def test_ownership_is_checked_against_the_LOCAL_gpsd_not_a_retained_remote_host(tmp_path, monkeypatch):
    """In direct-NMEA mode host/port are leftovers from whenever gpsd was last configured. If
    that was a remote box, querying it would ask a machine that cannot hold THIS receiver and
    report it free while a local gpsd held it."""
    svc = _svc(tmp_path)
    _enable(svc, 'meshtastic', 'meshcom', 'reticulum')
    save_gps(svc._paths, source="gpsd", host="192.168.1.5", port=2948)   # remote, retained
    save_gps(svc._paths, source="nmea", device="/dev/null")
    svc._invalidate_config()
    assert svc.config().gps.host == "192.168.1.5", "the remote host is still on disk"

    asked = []
    monkeypatch.setattr(gps_mod, "gpsd_owns_device",
                        lambda dev, host, port: (asked.append((host, port)), (False, ""))[1])
    svc.gps_block("meshtastic")
    assert asked == [("127.0.0.1", 2947)], f"must ask the LOCAL gpsd, asked {asked}"


def test_the_per_stack_switch_survives_a_band_change(tmp_path):
    """Band-scoped stacks save params per band, but "does this box report its position" is a
    property of the STACK. A switch that silently reverted when the operator moved 868 -> 433
    would be a trap — and it is what happened before this was read across bands."""
    svc = _svc(tmp_path)
    svc.save_stack_config("meshtastic", {"use_gps": "on"}, band="868")
    assert svc.gps_enabled_for("meshtastic") is True, "set on 868"
    save_gps(svc._paths, source="gpsd")
    svc._invalidate_config()
    # The feed is admitted regardless of which band the stack is later run on.
    assert "meshtastic-gps" in [c.id for _s, c in (svc._run_order("meshtastic") or [])]


def test_the_gps_switch_cannot_be_set_for_a_single_start(tmp_path):
    """An ephemeral value would let a launch run with GPS on while the SAVED state said off —
    and claims, generated config and the post-start push all come from the saved state, so the
    launch and what the box actually holds would disagree."""
    svc = _svc(tmp_path)
    # A CHANGE is refused... (the default is on now, so "off" is the change)
    clean, err = svc._normalize_run_params("meshtastic", {"use_gps": "off"})
    assert clean == {} and "cannot be changed for a single start" in err
    # ...but an echo of the CURRENT value is not an override. The console's start form posts
    # every parameter it renders, so rejecting the value outright blocked every web start.
    clean, err = svc._normalize_run_params("meshtastic", {"use_gps": "on"})
    assert err == "" and "use_gps" not in (clean or {})


def test_both_meshcom_feeds_claim_one_exclusive_uart(tmp_path):
    """Production and fixture write the SAME QEMU socket. An exclusive claim makes running
    them together structurally impossible instead of relying on admission logic to remember."""
    svc = _svc(tmp_path)
    st = svc.stack("meshcom")
    keys = {c.id: [r.key for r in c.resources if r.mode.value == "exclusive"]
            for c in st.components if c.id in ("meshcom-gps", "meshcom-gps-relay")}
    assert keys["meshcom-gps"] == keys["meshcom-gps-relay"] == ["meshcom.uart1.feed"]


@pytest.mark.safety("runtime-containment")
def test_a_symlinked_state_dir_cannot_place_the_endpoint_outside_the_runtime_root(tmp_path):
    """Reproduction of a real escape: with `state/gps/<consumer>` replaced by a link to
    somewhere else, plain os.makedirs/symlink/open follow it and create BOTH the endpoint and
    the readiness marker outside the root. Descriptor-anchored, O_NOFOLLOW descent refuses."""
    from lhpc.core import runtime_fs
    from lhpc.core.gps_bridge import PtyOutput, Readiness
    from lhpc.core.paths import Paths

    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "state" / "gps").mkdir(parents=True)
    outside.mkdir()
    (root / "state" / "gps" / "meshtastic").symlink_to(outside)   # the swapped parent
    paths = Paths(runtime_root=root)

    link = root / "state" / "gps" / "meshtastic" / "nmea0"
    out = PtyOutput(str(link), paths)
    with pytest.raises(runtime_fs.PathContainmentError):
        out.publish()
    out.close()

    ready = Readiness(str(root / "state" / "gps" / "meshtastic" / "readiness.json"), paths)
    ready.note_data(1)                       # must swallow the refusal, never write outside
    assert list(outside.iterdir()) == [], "nothing may be created outside the runtime root"


def test_the_endpoint_link_is_published_atomically(tmp_path):
    """A consumer reads the path the moment it exists. Publishing via unlink-then-symlink
    leaves a window where it is missing; the link is renamed into place instead."""
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    link = tmp_path / "state" / "gps" / "x" / "nmea0"
    runtime_fs.publish_symlink(paths, link, "/dev/null")
    assert link.is_symlink() and os.readlink(link) == "/dev/null"
    # Re-publishing over an existing link must not go through a missing state.
    runtime_fs.publish_symlink(paths, link, "/dev/zero")
    assert os.readlink(link) == "/dev/zero"
    runtime_fs.unlink_link(paths, link)
    assert not link.is_symlink()
    # The narrow removal helper must refuse anything that is not a link.
    regular = tmp_path / "state" / "gps" / "x" / "regular"
    regular.write_text("keep me")
    with pytest.raises(runtime_fs.PathContainmentError):
        runtime_fs.unlink_link(paths, regular)
    assert regular.exists()


def test_concurrent_partial_saves_do_not_lose_each_other(tmp_path):
    """Reading the current table before taking the lock loses an update: two partial saves
    each merge onto what they read, and the second silently discards the first."""
    import threading
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    save_gps(paths, source="nmea", device="/dev/null", nmea_baud=9600)

    errors = []

    def setter(**kw):
        try:
            for _ in range(12):
                save_gps(paths, **kw)
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            errors.append(exc)

    a = threading.Thread(target=setter, kwargs={"nmea_baud": 19200})
    b = threading.Thread(target=setter, kwargs={"host": "192.168.1.5"})
    a.start(); b.start(); a.join(); b.join()
    assert not errors, errors
    g = load_config(paths).gps
    # BOTH independent edits must survive; neither writer may clobber the other's field.
    assert g.nmea_baud == 19200 and g.host == "192.168.1.5"
    assert g.device == "/dev/null" and g.source == "nmea"


def test_the_source_cannot_change_under_a_consumer_that_starts_mid_save(tmp_path, monkeypatch):
    """The liveness check must hold at the moment of the WRITE, not only when it was asked."""
    svc = _svc(tmp_path)
    save_gps(svc._paths, source="gpsd")
    svc._invalidate_config()

    # Nothing running when set_gps is entered; a consumer appears by the time the lock is held.
    seen = {"n": 0}

    def _running():
        seen["n"] += 1
        return ["meshtastic"] if seen["n"] > 0 else []

    monkeypatch.setattr(type(svc), "gps_consumers_running",
                        lambda self, snap=None: _running())
    res = svc.set_gps(source="off")
    assert res.ok is False and "in use by" in res.summary
    assert load_config(svc._paths).gps.source == "gpsd", "the write must not have happened"


@pytest.mark.safety("gps-position-privacy")
def test_a_failing_gps_step_cannot_leak_coordinates_into_the_log(tmp_path):
    """The generated post-start launcher copies a child's stderr into the log. A failing GPS
    step reports what it was asked to do, so that text can carry the operator's coordinates,
    a raw NMEA sentence, or gpsd JSON — all of which land on disk and in the console."""
    import subprocess
    from lhpc.core.commands import _POST_RUNNER

    # Exercise the launcher's own scrub function exactly as generated.
    script = (_POST_RUNNER.split("def _record(")[0]
              .replace("__POS_VALUES__", repr([LAT, LON]))
              .replace("__STEPS__", "[]").replace("__BINDING__", "None")
              .replace("__GATED__", "False").replace("__META__", "{}")
              .replace("__ROOT__", repr(str(tmp_path))).replace("__RESULT_REL__", "()"))
    leaky = (f"error: --setlat {LAT} --setlon {LON} rejected\\n"
             f"$GPGGA,151345.00,5128.6740,N,00000.0900,W,1,09\\n"
             f'{{"class":"TPV","lat":{LAT},"lon":{LON}}}\\n')
    probe = script + f"\nprint(_scrub({leaky!r}))\n"
    out = subprocess.run([__import__("sys").executable, "-c", probe],
                         capture_output=True, text=True, timeout=60).stdout

    assert LAT not in out and LON not in out, f"coordinates leaked: {out!r}"
    assert "5128.6740" not in out, "raw NMEA leaked"
    assert "<redacted>" in out and "<nmea redacted>" in out


# --- readiness is CONSUMED, not merely written --------------------------------------------

def _write_marker(tmp_path, consumer, state, detail="", sentences=0, age=0.0, pid=None):
    """Write a feed readiness marker. `age` backdates it; `pid` forges a foreign owner.

    A live feed refreshes this every ~10 s, so `updated` and the owning pid are what make a
    marker belong to the CURRENT run rather than a previous one.
    """
    import json
    import os
    d = tmp_path / "state" / "gps" / consumer
    d.mkdir(parents=True, exist_ok=True)
    (d / "readiness.json").write_text(json.dumps(
        {"state": state, "detail": detail, "sentences": sentences,
         "updated": int(time.time() - age), "pid": os.getpid() if pid is None else pid}))


def _feed_comp(svc, stack, cid):
    return next(c for c in svc.stack(stack).components if c.id == cid)


@pytest.mark.parametrize("state,healthy", [
    ("ready", True),            # sentences flowing
    ("connected", True),        # reachable, no fix yet — a cold start takes minutes
    ("source-lost", False),     # gpsd died / receiver unplugged
    ("stale", False),           # was flowing, then stopped
    ("starting", False),
])
def test_feed_health_follows_the_source_not_the_endpoint(tmp_path, state, healthy):
    """The endpoint exists from the instant it is created. Without consuming the marker, a
    feed whose gpsd died keeps reading RUNNING while delivering nothing."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.status import StatusProber
    svc = _svc(tmp_path)
    _write_marker(tmp_path, "meshtastic", state)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))
    ok, note = prober._gps_feed_ready(comp)
    assert ok is healthy, note


def test_an_absent_marker_is_not_healthy(tmp_path):
    """A feed that never wrote a marker has not reached its source; treating "no news" as
    good is exactly how an unreachable gpsd counted as a successful dependency."""
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    assert svc._gps_feed_ready(comp) == (False, "no readiness marker")


def test_a_feed_that_never_reaches_its_source_fails_the_start(tmp_path, monkeypatch):
    """An unreachable source at startup must FAIL and clean up, not report started."""
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    monkeypatch.setattr(type(svc), "ENDPOINT_VERIFY_TIMEOUT_S", 0.0, raising=False)
    _write_marker(tmp_path, "meshtastic", "source-lost", "gpsd unreachable")
    ok, why = svc._gps_feed_ready(comp)
    assert ok is False and "not reachable" in why

    # Reachable-without-fix is a WARNING, not a failure.
    _write_marker(tmp_path, "meshtastic", "connected")
    ok, why = svc._gps_feed_ready(comp)
    assert ok is True and "waiting for a fix" in why


def test_recovery_needs_no_restart(tmp_path):
    """The verdict is recomputed from the marker on every read, so a source that comes back
    returns the feed to healthy on its own."""
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    _write_marker(tmp_path, "meshtastic", "source-lost", "gpsd unreachable")
    assert svc._gps_feed_ready(comp)[0] is False
    _write_marker(tmp_path, "meshtastic", "ready", sentences=42)
    assert svc._gps_feed_ready(comp)[0] is True


# --- the opt-in GPS dependency bucket ------------------------------------------------------

def test_gpsd_is_opt_in_not_part_of_the_default_bootstrap(tmp_path):
    """A fresh image has no GPS setting yet, so installing a daemon for a feature most boxes
    never enable would be wrong. But silently omitting it left an operator with a local
    receiver no way to install it from bootstrap — hence the declared `--with-gps` flag."""
    svc = _svc(tmp_path)
    script = svc.deps_script()
    assert "--with-gps" in script, "the flag the model promises must exist"
    assert 'WITH_GPS=""' in script and "--with-gps) WITH_GPS=1" in script

    before, sep, after = script.partition('if [ -n "$WITH_GPS" ]; then')
    assert sep, "the opt-in block must exist"
    assert "gpsd" in after.split("\nfi", 1)[0], "gpsd installs inside the opt-in block"

    # …and is never INSTALLED outside it. Checked against install lines only: the flag's help
    # text legitimately names the package, and matching bare text flagged a comment.
    installed = []
    in_install = False
    for line in before.splitlines():
        s = line.strip()
        if "apt-get install" in s:
            in_install = True
            installed += [w for w in s.split() if not w.startswith("-")
                          and w not in ("sudo", "apt-get", "install", "\\")]
            in_install = s.endswith("\\")
            continue
        if in_install:
            installed.append(s.rstrip("\\").strip())
            in_install = s.endswith("\\")
    assert "gpsd" not in installed, f"gpsd must not be a default package, got {installed[:8]}"


def test_the_generated_bootstrap_script_is_valid_shell(tmp_path):
    """It is handed to an operator to run with sudo; a syntax error surfaces as a
    half-provisioned box."""
    import subprocess
    svc = _svc(tmp_path)
    cp = subprocess.run(["bash", "-n", "-c", svc.deps_script()],
                        capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr


def test_a_malformed_local_config_makes_the_position_source_unknown_not_off(tmp_path):
    """`[gps]` lives in that unreadable layer, so its absence means "could not be read", not
    "not configured". Reporting a clean `off` would let a GPS-enabled stack start believing
    there is simply no source, instead of refusing because the setting is unknown."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text("[gps\nsource = broken")
    from lhpc.core.paths import Paths
    g = load_config(Paths(runtime_root=tmp_path)).gps
    assert g.source == "off" and g.valid is False
    assert "unreadable" in g.reason or "malformed" in g.reason


def test_a_malformed_stack_config_is_a_named_diagnostic_not_a_traceback(tmp_path):
    """`load_stack_config` is fail-closed and raises ConfigError — which is neither OSError
    nor ValueError. Catching the wrong types let it escape into a status GET and the
    console's settings view as a 500."""
    svc = _svc(tmp_path)
    cfgdir = tmp_path / "config" / "stacks"
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "reticulum.toml").write_text("not = valid toml [[[")
    legacy = svc.legacy_gps_values()                      # must not raise
    assert "reticulum" in legacy
    assert any("unreadable" in k for k in legacy["reticulum"])
    svc.gps_view()                                        # the console view must not raise either


@pytest.mark.safety("gps-receiver-exclusive")
def test_a_running_stack_holds_its_receiver_against_another_stack(tmp_path, monkeypatch):
    """The device claim must be in the CONFLICT set, not only the lock set.

    Locks are released when a start COMPLETES, so locks alone let stack A start, finish, and
    stack B then start and open the same receiver — two readers on one device, which loses
    fixes intermittently rather than failing cleanly.
    """
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _enable(svc, "meshtastic", "meshcom", "reticulum")
    save_gps(svc._paths, source="nmea", device="/dev/null")
    svc._invalidate_config()

    key = svc._gps_device_claim("meshtastic")
    assert key, "the target must claim the receiver"
    assert key == svc._gps_device_claim("meshcom"), "same device -> same key across stacks"

    # Pretend MeshCom's node is already RUNNING and holding it.
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == "meshcom":
            for cid in ss.components:
                if cid == "meshcom-qemu":
                    ss.components[cid].run_state = RunState.RUNNING
    monkeypatch.setattr(type(svc), "build_snapshot",
                        lambda self, *a, **k: snap)

    blockers = svc.run_blockers("meshtastic")
    assert any(b["resource"] == key for b in blockers), \
        f"a running holder of the receiver must block the second stack, got {blockers}"


def test_no_receiver_claim_means_no_conflict(tmp_path):
    """off / fixed / remote gpsd open no local device, so they must never block each other."""
    svc = _svc(tmp_path)
    _enable(svc, "meshtastic", "meshcom")
    for kw in ({"source": "gpsd"}, {"source": "gpsd", "host": "192.168.1.5"},
               {"source": "fixed", "fixed_lat": LAT, "fixed_lon": LON}, {"source": "off"}):
        save_gps(svc._paths, **kw)
        svc._invalidate_config()
        assert svc._gps_device_claim("meshtastic") == ""
        assert not [b for b in svc.run_blockers("meshtastic")
                    if str(b.get("resource", "")).startswith("gps.")]


def test_the_feed_refuses_a_device_that_changed_underneath_it(tmp_path):
    """The lock key comes from a stat() of the path; opening it is a SECOND resolution, and
    `/dev/serial/by-id/...` is a symlink that can be repointed between the two. Without the
    check the feed could read receiver B while the lifecycle believed it held receiver A."""
    import threading
    from lhpc.core.gps_bridge import Readiness, _Output, _pump_nmea
    from lhpc.core.paths import Paths

    class _Sink(_Output):
        def __init__(self):
            super().__init__("", None)

        def write(self, data):
            self._bytes += len(data)

    ready = Readiness(str(tmp_path / "r.json"), Paths(runtime_root=tmp_path))
    stop = threading.Event()
    t = threading.Thread(
        target=_pump_nmea,
        args=("/dev/null", 9600, _Sink(), ready, stop),
        kwargs={"expect_key": "serial.dev.999:999"},      # not what /dev/null resolves to
        daemon=True)
    t.start()
    deadline = time.time() + 3.0
    while ready.state not in ("source-lost",) and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=2.0)
    assert ready.state == "source-lost", "a mismatched receiver must not read as healthy"


# --- against a FAKE gpsd (the real socket client, not a stub) -------------------------------

def test_the_real_gpsd_client_reads_the_device_list(tmp_path, fake_gpsd):
    """Exercises the actual socket client, not a stubbed return — this is what decides
    whether a receiver is already owned."""
    srv = fake_gpsd(devices=["/dev/null", "/dev/zero"])
    try:
        paths, err = gps_mod.gpsd_devices("127.0.0.1", srv.port, timeout=3.0)
        assert err == "" and paths == ["/dev/null", "/dev/zero"]
    finally:
        srv.close()


def test_ownership_matches_by_device_identity_not_by_path(tmp_path, fake_gpsd):
    """gpsd may report the receiver under a different path than the operator configured;
    matching on st_rdev is what makes the two recognisably the same device."""
    srv = fake_gpsd(devices=["/dev/null"])
    try:
        owned, detail = gps_mod.gpsd_owns_device("/dev/null", "127.0.0.1", srv.port)
        assert owned is True and detail == "/dev/null"
        # A device gpsd does NOT hold is free.
        owned, _ = gps_mod.gpsd_owns_device("/dev/zero", "127.0.0.1", srv.port)
        assert owned is False
    finally:
        srv.close()


def test_the_feed_relays_sentences_from_a_live_gpsd_and_degrades_when_it_closes(tmp_path, fake_gpsd):
    """The gpsd pump end to end: NMEA reaches the output, readiness goes ready, and a server
    that goes away drives the feed to source-lost instead of silently stalling."""
    from lhpc.core.gps_bridge import Readiness, _Output, _pump_gpsd
    from lhpc.core.paths import Paths

    class _Sink(_Output):
        def __init__(self):
            super().__init__("", None)
            self.data = b""

        def write(self, d):
            self.data += d
            self._bytes += len(d)

    srv = fake_gpsd(sentences=["$GPGGA,120000.00,5128.6740,N,00000.0900,W,1,08,0.9,45.0,M,46.9,M,,*44"],
                    close_after=6)
    out = _Sink()
    ready = Readiness(str(tmp_path / "r.json"), Paths(runtime_root=tmp_path))
    stop = threading.Event()
    t = threading.Thread(target=_pump_gpsd,
                         args=("127.0.0.1", srv.port, out, ready, stop), daemon=True)
    t.start()
    deadline = time.time() + 5.0
    while ready.state != "ready" and time.time() < deadline:
        time.sleep(0.05)
    assert ready.state == "ready" and out.data.startswith(b"$GPGGA")

    srv.close()                                   # the source goes away
    deadline = time.time() + 6.0
    while ready.state == "ready" and time.time() < deadline:
        time.sleep(0.1)
    stop.set()
    t.join(timeout=3.0)
    assert ready.state in ("source-lost", "connected", "stale"), ready.state


def test_a_slow_guest_does_not_cause_a_reconnect_loop(tmp_path):
    """Found on a REAL QEMU node: the emulated UART back-pressures constantly, and treating
    `BlockingIOError` as a broken link produced connect/fail/reconnect churn. On a
    non-blocking socket it only means "would block" — the sentence is dropped (the next one
    supersedes it) and the connection is kept."""
    from lhpc.core.gps_bridge import UnixClientOutput
    sock_path = str(tmp_path / "uart.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    out = UnixClientOutput(sock_path)
    try:
        out.publish()
        conn, _ = srv.accept()
        assert out.connected is True

        # Never read from `conn`: the buffer fills and sendall starts raising BlockingIOError.
        for _ in range(4000):
            out.write(b"$GPGGA,000000.00,,,,,0,00,,,M,,M,,*66\r\n")
        assert out.connected is True, "back-pressure must not be mistaken for a dead link"
        conn.close()
    finally:
        out.close()
        srv.close()


def test_binary_install_does_not_self_contend_on_its_own_source_guard(tmp_path, monkeypatch):
    """`binary_install` guards every path in `spec.covers`, then adopts the `clone_required`
    checkout inside that same guard. Passing the bare `locked` told adoption the lock was free
    whenever the guard was OURS, so it re-acquired and blocked — reported as "another source
    operation is in progress" naming what is really our own owner record. That made
    `install --source binary` impossible for any stack with `clone_required` (MeshCom, whose
    artifact runs run.sh / gps-relay.py out of the pinned checkout).

    Drives the REAL `binary_install` against a local index. An earlier version of this test only
    asserted the source path appeared in `spec.covers`, which passes with the bug present.
    """
    import json as _json

    from lhpc.core import binary_install as _bi

    svc = _svc(tmp_path)
    # The guard/adoption logic under test is platform-independent; the target only names the
    # index entry. Skipping where the host is not aarch64 meant this never ran on CI (x86_64
    # runners) — the one place it would be noticed if the self-contention bug came back. Force
    # the supported target instead of skipping.
    target = svc.binary_target() or "aarch64-trixie"
    monkeypatch.setattr(type(svc), "binary_target", lambda self: target)
    spec = svc.binary_spec("meshcom")

    idx = {"schema": 2, "stacks": {"meshcom": {
        "filename": "meshcom-" + "0" * 64 + ".tar.zst", "url": "https://example.invalid/meshcom.tar.zst",
        "sha256": "0" * 64, "size": 1024, "built_from": "b" * 40,
        # The index must record a commit for EVERY covered component or the pins-must-match
        # invariant refuses the entry before adoption is ever reached.
        "components": {c.id: (c.source.pin_commit if c.source else "a" * 40)
                       for c in svc.stack("meshcom").components
                       if c.id in spec.covers},
        "runtime_deps": [], "target": target, "os": target.split("-", 1)[1],
        "smoke": {"mode": "mandatory", "result": "passed"}, "lhpc_commit": "d" * 40,
        "builder_commit": "e" * 40, "container_digest": "debian@sha256:" + "f" * 64,
        "extract_to": "runtime-root"}}}
    monkeypatch.setattr(_bi, "_http_get",
                        lambda url, max_bytes: _json.dumps(idx).encode())

    class _ReachedAdoption(Exception):
        pass

    seen = {}
    inst_cls = type(svc._installer())
    _real_adopt = inst_cls.adopt_source

    def _spy(self, comp, source="pinned", locked=False):
        seen["comp"], seen["locked"] = comp.id, locked
        # Prove the contention is REAL and not hypothetical: at this exact point, an adoption
        # told the lock is free (the old `locked=locked`) is refused by our own guard.
        _blind = _real_adopt(self, comp, source=source, locked=False)
        seen["blind"] = (_blind.status, _blind.detail or "")
        raise _ReachedAdoption()

    monkeypatch.setattr(inst_cls, "adopt_source", _spy)

    dest = svc._paths.resolve_source("src/meshcom-qemu-raspi")
    assert not dest.is_dir(), "the checkout must be absent so adoption is required"
    with pytest.raises(_ReachedAdoption):
        svc.binary_install("meshcom", apply=True)

    assert seen["comp"] == "meshcom-qemu"
    assert seen["locked"] is True, "adoption must be told the source lock is already held"
    assert seen["blind"][0] == "failed", (
        "an adoption told the lock is free must be refused here — that refusal IS the bug this "
        f"guards against, so if it stops happening the test is no longer proving anything "
        f"(got {seen['blind']})")
    assert "in progress" in seen["blind"][1] or "busy" in seen["blind"][1].lower(), seen["blind"]
    assert "src/meshcom-qemu-raspi" in set(svc._binary_source_paths("meshcom")), (
        "the guard covers the very path adoption needs — that is why it self-contended")
    assert "meshcom-qemu" in spec.clone_required


def test_a_stale_marker_cannot_approve_a_start(tmp_path):
    """A persisted `state=ready` from a previous run would otherwise pass the start gate
    instantly, approving a feed that has delivered nothing in this launch."""
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")

    _write_marker(tmp_path, "meshtastic", "ready", sentences=99, age=0)
    assert svc._gps_feed_ready(comp)[0] is True, "a fresh marker is fine"

    _write_marker(tmp_path, "meshtastic", "ready", sentences=99, age=6000)
    ok, why = svc._gps_feed_ready(comp)
    assert ok is False, f"a stale marker must not approve a start ({why})"


def test_a_marker_from_a_dead_feed_is_not_this_run(tmp_path):
    """`updated` alone is not enough: a marker refreshed moments before the feed died still
    reads recent. The owning pid says whether that feed is still there."""
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    _write_marker(tmp_path, "meshtastic", "ready", sentences=5, age=0, pid=999_999)
    assert svc._gps_feed_ready(comp)[0] is False


def test_status_treats_an_expired_marker_as_degraded(tmp_path):
    """Otherwise a feed whose process is long gone keeps the component reading RUNNING."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.status import StatusProber
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))

    _write_marker(tmp_path, "meshtastic", "ready", sentences=7, age=0)
    assert prober._gps_feed_ready(comp)[0] is True
    _write_marker(tmp_path, "meshtastic", "ready", sentences=7, age=6000)
    ok, note = prober._gps_feed_ready(comp)
    assert ok is False and "stale" in note
    # A feed killed right after its last refresh leaves a RECENT marker; the owning pid is
    # what still says the feed is gone. Status must agree with the start gate here, or the
    # console shows RUNNING for a feed nothing is driving.
    _write_marker(tmp_path, "meshtastic", "ready", sentences=7, age=0, pid=999_999)
    ok, note = prober._gps_feed_ready(comp)
    assert ok is False and "gone" in note


# How long a test may wait for a real feed thread to publish its endpoint/readiness. These are
# WAITS, not bounds: the loop exits the moment the artefact appears, so a generous budget costs
# nothing when things are healthy and stops a loaded box from failing a correct implementation.
# 6.0s was not enough — this file's PTY end-to-end case took 4.2s cold on an idle Pi 5, and failed
# once inside the full suite.
_FEED_UP_S = 30.0


def _run_feed(paths, consumer, stop):
    from lhpc.core.gps_bridge import run
    return run(consumer, paths, stop=stop)


def test_the_bridge_serves_meshcore_a_position_feed(tmp_path, fake_gpsd):
    """MeshCore's consumer is the openHop host app: it needs a normalized POSITION
    (line-JSON on a Unix server socket), not a simulated GPS chip. The bridge must
    accept the meshcore consumer, publish the position socket, convert a fixed NMEA
    sentence into {"fix": true, lat, lon}, and tear everything down on stop.
    """
    import json
    import os
    import socket as socket_mod
    import threading
    import time
    from lhpc.core.config import save_gps
    from lhpc.core.gps import bridge_endpoint_path, bridge_state_dir
    from lhpc.core.gps_bridge import EXIT_OK
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    paths = Paths(runtime_root=tmp_path)
    # 52°31.2000'N 13°24.6000'E with a valid fix, then a no-fix GGA.
    srv = fake_gpsd(sentences=[
        "$GPRMC,000001.00,A,5231.2000,N,01324.6000,E,0.0,0.0,010124,,,A*5C",
    ])
    save_gps(paths, source="gpsd", host="127.0.0.1", port=srv.port)
    stop = threading.Event()
    rc = {}
    t = threading.Thread(target=lambda: rc.setdefault("v", _run_feed(paths, "meshcore", stop)),
                         daemon=True)
    t.start()
    state = os.path.join(bridge_state_dir(tmp_path, "meshcore"), "readiness.json")
    sock_path = bridge_endpoint_path(tmp_path, "meshcore")
    deadline = time.time() + _FEED_UP_S
    while not os.path.exists(sock_path) and time.time() < deadline:
        time.sleep(0.05)
    assert os.path.exists(sock_path), "the position socket must be published"

    client = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    client.settimeout(_FEED_UP_S)
    client.connect(sock_path)
    buf = b""
    while b"\n" not in buf and time.time() < deadline:
        buf += client.recv(4096)
    record = json.loads(buf.split(b"\n")[0])
    assert record["fix"] is True
    assert abs(record["lat"] - (52 + 31.2 / 60)) < 1e-6
    assert abs(record["lon"] - (13 + 24.6 / 60)) < 1e-6
    client.close()
    while not os.path.exists(state) and time.time() < deadline:
        time.sleep(0.05)
    assert os.path.exists(state), f"readiness must be written (waited {_FEED_UP_S}s)"

    stop.set()
    t.join(timeout=_FEED_UP_S)
    srv.close()
    assert rc.get("v") == EXIT_OK, "the bridge must not refuse meshcore as an unknown consumer"
    assert not os.path.exists(sock_path), "a stopped bridge must not leave a live-looking feed"
    assert not os.path.exists(state)


def test_no_fix_nmea_becomes_an_explicit_no_fix_record(tmp_path):
    """A navigation sentence WITHOUT a usable fix must reach the consumer as
    {"fix": false} — silence would leave the old moving position advertised."""
    import json
    import socket as socket_mod
    import time
    from lhpc.core.gps_bridge import PosJsonServerOutput
    link = str(tmp_path / "position.sock")
    out = PosJsonServerOutput(link)
    out.publish()
    try:
        # Fix first, then no-fix; a late client still learns the current state.
        out.write(b"$GPRMC,000001.00,A,5231.2000,N,01324.6000,E,0.0,0.0,010124,,,A*5C\r\n")
        out.write(b"$GPRMC,000002.00,V,,,,,,,010124,,,N*79\r\n")
        client = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        client.settimeout(5.0)
        client.connect(link)
        buf = b""
        deadline = time.time() + 5.0
        while b"\n" not in buf and time.time() < deadline:
            buf += client.recv(4096)
        record = json.loads(buf.split(b"\n")[0])
        assert record == {"fix": False}
        client.close()
    finally:
        out.close()


def test_the_position_socket_is_the_one_the_config_names(tmp_path):
    """The generated config must point at exactly the path the bridge creates — a mismatch
    would leave the host app dialing a socket that never appears."""
    from lhpc.core.gps import CONSUMER_MESHCORE, bridge_endpoint_path, bridge_state_dir
    import os
    self_path = bridge_endpoint_path(tmp_path, CONSUMER_MESHCORE)
    assert self_path == os.path.join(
        bridge_state_dir(tmp_path, CONSUMER_MESHCORE), "position.sock")


def test_run_refuses_when_the_source_is_off(tmp_path):
    """Starting a feed with no configured source would publish an endpoint that can never
    deliver — the component must fail, not sit there looking healthy."""
    from lhpc.core.gps_bridge import EXIT_CONFIG, run
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    assert run("meshtastic", Paths(runtime_root=tmp_path)) == EXIT_CONFIG


def test_run_refuses_an_unknown_consumer(tmp_path):
    from lhpc.core.gps_bridge import EXIT_CONFIG, run
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    assert run("not-a-consumer", Paths(runtime_root=tmp_path)) == EXIT_CONFIG


def test_run_serves_a_pty_consumer_end_to_end_and_tears_down(tmp_path, fake_gpsd):
    """The full path: resolve the plan, publish the PTY, relay from a live gpsd, then remove
    BOTH the endpoint and the readiness marker on the way out."""
    import threading

    from lhpc.core.gps_bridge import EXIT_OK
    from lhpc.core.gps import bridge_state_dir
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    srv = fake_gpsd(sentences=["$GPGGA,000000.00,,,,,0,00,,,M,,M,,*66"])
    save_gps(paths, source="gpsd", host="127.0.0.1", port=srv.port)
    stop = threading.Event()
    rc = {}
    t = threading.Thread(target=lambda: rc.setdefault("v", _run_feed(paths, "meshtastic", stop)),
                         daemon=True)
    t.start()
    state = os.path.join(bridge_state_dir(tmp_path, "meshtastic"), "readiness.json")
    link = os.path.join(bridge_state_dir(tmp_path, "meshtastic"), "nmea0")
    deadline = time.time() + _FEED_UP_S
    while not os.path.exists(state) and time.time() < deadline:
        time.sleep(0.05)
    assert os.path.islink(link), "the PTY endpoint must be published"
    assert os.path.exists(state), f"readiness must be written (waited {_FEED_UP_S}s)"

    stop.set()
    t.join(timeout=_FEED_UP_S)
    srv.close()
    assert rc.get("v") == EXIT_OK
    assert not os.path.islink(link), "a stopped feed must leave no live-looking endpoint"
    assert not os.path.exists(state), "a stopped feed must leave no readiness marker"


def test_run_serves_a_fixed_position_without_any_source(tmp_path):
    """`fixed` needs no gpsd and no device: the feed synthesises sentences itself."""
    import threading

    from lhpc.core.gps import bridge_state_dir
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    save_gps(paths, source="fixed", fixed_lat=LAT, fixed_lon=LON, fixed_alt=ALT)

    stop = threading.Event()
    t = threading.Thread(target=lambda: _run_feed(paths, "meshtastic", stop), daemon=True)
    t.start()
    link = os.path.join(bridge_state_dir(tmp_path, "meshtastic"), "nmea0")
    deadline = time.time() + _FEED_UP_S
    while not os.path.islink(link) and time.time() < deadline:
        time.sleep(0.05)
    assert os.path.islink(link)
    fd = os.open(link, os.O_RDONLY | os.O_NONBLOCK)
    try:
        deadline = time.time() + 4.0
        got = b""
        while b"$GP" not in got and time.time() < deadline:
            try:
                got += os.read(fd, 512)
            except BlockingIOError:
                time.sleep(0.05)
        assert b"$GP" in got, "a fixed station must still emit sentences"
    finally:
        os.close(fd)
        stop.set()
        t.join(timeout=_FEED_UP_S)


def test_a_device_sending_binary_is_reported_not_waited_on(tmp_path):
    """Found on hardware: gpsd switches u-blox receivers into UBX BINARY mode, and they stay
    there after gpsd stops. The feed read 434 bytes and forwarded none, sitting at "waiting
    for sentences" forever while the operator saw a start fail with no reason.

    Bytes arriving with no NMEA in them is its own diagnosis, and it names the usual cause.
    """
    import threading

    from lhpc.core.gps_bridge import Readiness, _Output, _pump_nmea
    from lhpc.core.paths import Paths

    dev = tmp_path / "fake-ubx"
    os.mkfifo(str(dev))

    class _Sink(_Output):
        def __init__(self):
            super().__init__("", None)

        def write(self, d):
            self._bytes += len(d)

    ready = Readiness(str(tmp_path / "r.json"), Paths(runtime_root=tmp_path))
    stop = threading.Event()
    t = threading.Thread(target=_pump_nmea,
                         args=(str(dev), 9600, _Sink(), ready, stop), daemon=True)
    t.start()
    # Writer end: UBX sync bytes, never a `$` sentence.
    w = os.open(str(dev), os.O_WRONLY)
    try:
        deadline = time.time() + 20.0
        while ready.state != "source-lost" and time.time() < deadline:
            try:
                os.write(w, b"\xb5\x62\x01\x06" + b"\x00" * 120)
            except OSError:
                break
            time.sleep(0.2)
        assert ready.state == "source-lost", ready.state
        assert "binary" in ready.detail and "NMEA" in ready.detail
    finally:
        stop.set()
        os.close(w)
        t.join(timeout=3.0)


def test_the_gps_switch_survives_a_band_change(tmp_path):
    """On a BAND-SCOPED stack the switch must live in the band-less file.

    Live-found on the Zero: `lhpc config meshtastic use_gps on` wrote `use_gps` into
    `meshtastic@868.toml`, while every GPS decision reads the band-less `meshtastic.toml` — so
    the switch did nothing at all, and would have reverted on a band change anyway. The earlier
    unit tests missed it because the fixture had no band selected, which is the one condition
    that makes the two paths agree.
    """
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()
    assert svc._config_band("meshtastic", "") == "868", "the stack must be band-scoped here"

    # "off" is the stored deviation now — the default flipped to on when the source
    # gained `auto`; the CONTRACT under test (band-less storage) is unchanged.
    res = svc.save_config_bundle("meshtastic", values={"use_gps": "off"})
    assert res.ok, res

    stacks = tmp_path / "config" / "stacks"
    assert "use_gps" in (stacks / "meshtastic.toml").read_text(), "must be in the BAND-LESS file"
    banded = stacks / "meshtastic@868.toml"
    assert "use_gps" not in (banded.read_text() if banded.exists() else ""), \
        "storing it per band is what made it revert on a band change"
    assert svc.gps_enabled_for("meshtastic") is False, "and the read must see it"

    # The other band must agree — that is the whole point of storing it band-lessly.
    _cfg.save_hardware_setup(svc._paths, "uputronics-433")
    svc._invalidate_config()
    assert svc.gps_enabled_for("meshtastic") is False, "the switch must survive a band change"


def test_the_gps_switch_write_keeps_the_rest_of_the_bandless_file(tmp_path):
    """Autostart lives in the same band-less file. Writing the switch by REPLACING that file
    (rather than merging into it) would silently clear autostart — the stack then no longer
    comes up on boot, with nothing pointing at the GPS change that did it."""
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()

    stacks = tmp_path / "config" / "stacks"
    stacks.mkdir(parents=True, exist_ok=True)
    (stacks / "meshtastic.toml").write_text('autostart_meshtastic-gps = "on"\n')

    assert svc.save_config_bundle("meshtastic", values={"use_gps": "off"}).ok
    after = (stacks / "meshtastic.toml").read_text()
    assert "autostart_meshtastic-gps" in after, \
        "the GPS write must MERGE into the band-less file, not replace it"
    assert "use_gps" in after


def test_the_gps_switch_cannot_be_flipped_under_a_running_stack_from_any_surface(tmp_path):
    """The CLI and the console both call `save_config_bundle` directly, so a refusal that lives
    on `save_stack_config` protects neither. Live-found: `lhpc config meshtastic use_gps off`
    was ACCEPTED while meshtastic was running, answering "Restart the stack to apply"."""
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()
    assert svc.gps_enabled_for("meshtastic") is True     # the manifest default

    from lhpc.core.model import RunState
    _snap_with(svc, {"meshtastic": RunState.RUNNING})          # the stack is up
    res = svc.save_config_bundle("meshtastic", values={"use_gps": "off"})
    assert not res.ok, "flipping the switch under a running stack must be refused"
    assert "use_gps" in res.summary and "running" in res.summary
    assert svc.gps_enabled_for("meshtastic") is True, "and nothing may have been written"

    # Re-submitting the SAME value — here the DEFAULT, which the form always posts — is
    # not a change and must not be refused. (Hardcoding "off" as the unset reading made
    # exactly this save look like a change and blocked every Settings save while running.)
    assert svc.save_config_bundle("meshtastic", values={"use_gps": "on"}).ok


def test_the_saved_switch_is_what_the_console_and_cli_show(tmp_path):
    """Reader and writer must agree on WHERE the switch lives.

    Written band-lessly but read from the banded file, it resolved to its DEFAULT: `lhpc config
    meshtastic` and the console's Settings both showed `off` while GPS was on — and because a
    bundle save submits the displayed values, editing any unrelated parameter would then store
    "at default -> remove" and silently clear the switch.
    """
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshtastic", values={"use_gps": "on"}).ok

    assert svc._resolved_param_value("meshtastic", "run", "meshtastic", "use_gps", "") == "on", \
        "the displayed value must be the SAVED value, not the default"

    # The consequence that makes this severe: an unrelated edit must not clear it.
    assert svc.save_config_bundle("meshtastic", values={"node_name": "GREENWICH"}).ok
    assert svc.gps_enabled_for("meshtastic") is True, \
        "editing an unrelated parameter must not turn GPS off"


def test_every_gps_hint_is_a_runnable_command(tmp_path):
    """`next_commands` are meant to be pasted. `lhpc config <stack> use_gps=off` is not a
    command — the CLI takes `<param> <value>`, so it answered "unknown parameter 'use_gps=off'"
    (live-found on the Zero). A hint that does not run is worse than no hint."""
    import re
    svc = _svc(tmp_path)
    # The refusal that carries use_gps hints today: a production feed started ALONE while the
    # plan does not use it. (The old sample — use_gps on + source off — is no longer a refusal:
    # with the switch defaulting on, that combination starts without position instead.)
    svc.save_stack_config("meshcom", {"use_gps": "off"})
    reason, cmds = svc.gps_block("meshcom-gps")
    assert reason, "this is the refusal that carries the hint"
    for c in cmds:
        if "use_gps" in c:
            assert not re.search(r"use_gps=", c), f"not runnable: {c}"
            assert re.search(r"config \S+ use_gps (on|off)$", c), f"not runnable: {c}"


# ---- component targets, the save/start race, marker pids, and the publication race ----------

def test_a_component_target_saves_the_owner_stacks_switch(tmp_path):
    """`lhpc config meshcom-qemu use_gps on` must set the STACK's switch.

    A component target stores component-scoped keys (`__r__meshcom-qemu__use_gps`), which no GPS
    reader consults — the switch appeared to save and then did nothing. There is one switch per
    stack, so whichever target names it, it lands as the owner's flat band-less `use_gps`.
    """
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()

    # "off" is the stored deviation (the default flipped to on); the CONTRACT — flat,
    # band-less, owner-stack storage from a component target — is unchanged.
    assert svc.save_config_bundle("meshcom-qemu", values={"use_gps": "off"}).ok
    body = (tmp_path / "config" / "stacks" / "meshcom.toml").read_text()
    assert 'use_gps = "off"' in body, f"not stored flat/band-less: {body!r}"
    assert "__use_gps" not in body, "a component-scoped key is invisible to every GPS reader"
    assert svc.gps_enabled_for("meshcom") is False
    assert svc.gps_enabled_for("meshcom-qemu") is False, "the component must see it too"
    assert svc._resolved_param_value("meshcom-qemu", "run", "meshcom-qemu", "use_gps", "") == "off"

    assert svc.save_config_bundle("meshcom-qemu", values={"use_gps": "on"}).ok
    assert svc.gps_enabled_for("meshcom") is True, "and turning it back on from the component works"


def test_a_direct_consumer_start_brings_its_gps_feed(tmp_path):
    """Starting the consumer alone (`lhpc stack start meshcom-qemu`) must run under the same plan
    as its stack. A component run order is seeds + dependencies, and the feed is admitted from the
    plan rather than declared as a dependency — so without this the consumer came up with no
    position source at all, looking perfectly healthy."""
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    save_gps(svc._paths, source="gpsd")
    svc._invalidate_config()

    order = [c.id for _s, c in (svc._run_order("meshcom-qemu") or [])]
    assert "meshcom-gps" in order, f"the feed must be included: {order}"
    assert order.index("meshcom-gps") < order.index("meshcom-qemu"), "and start before its reader"

    save_gps(svc._paths, source="off")
    svc._invalidate_config()
    assert "meshcom-gps" not in [c.id for _s, c in (svc._run_order("meshcom-qemu") or [])], \
        "with no position plan there is no feed to add"


def test_a_direct_feed_start_is_refused_unless_the_plan_uses_it(tmp_path):
    """The production feed alone claims the receiver and publishes an endpoint for a consumer
    that is not coming — and with the source off it delivers nothing while looking healthy."""
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    save_gps(svc._paths, source="off")
    svc._invalidate_config()
    reason, cmds = svc.gps_block("meshcom-gps")
    assert reason and "meshcom-gps" in reason, reason
    assert any("lhpc gps" in c for c in cmds), cmds

    save_gps(svc._paths, source="gpsd")                  # now the plan DOES use it
    svc._invalidate_config()
    assert svc.gps_block("meshcom-gps")[0] == "", "allowed once the plan calls for the feed"


def test_a_direct_feed_start_claims_the_same_receiver_as_its_stack(tmp_path):
    """Otherwise the feed could be started directly beside a stack already reading the device."""
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    save_gps(svc._paths, source="nmea", device="/dev/tty", nmea_baud="9600")
    svc._invalidate_config()
    stack_claim = svc._gps_device_claim("meshcom")
    assert stack_claim, "this source does open a local device"
    assert svc._gps_device_claim("meshcom-gps") == stack_claim
    assert stack_claim in svc._operation_resource_keys("meshcom-gps"), \
        "the claim must actually be taken for the direct start, not merely computable"


def test_a_start_that_wins_the_race_still_blocks_the_switch(tmp_path):
    """The pre-check reads state BEFORE the config lock is held, so a start beginning in between
    would derive its feed, claims and generated config from the OLD switch while the new value
    landed underneath it. The authoritative recheck runs inside the exclusive transaction."""
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    from lhpc.core.model import RunState
    calls = {"n": 0}
    # Two DISTINCT snapshots: `build_snapshot()` memoizes, so calling it twice hands back the
    # same object and the second variant would silently overwrite the first.
    stopped = svc.build_snapshot(fresh=True)
    running = svc.build_snapshot(fresh=True)
    assert stopped is not running
    for _sn, _state in ((stopped, RunState.STOPPED), (running, RunState.RUNNING)):
        for ss in _sn.stacks:
            for cid, st in ss.components.items():
                st.run_state = _state if cid == "meshcom-qemu" else RunState.STOPPED

    def racing(fresh=False):
        # The pre-check reads the cached snapshot (nothing running); the recheck inside the
        # transaction forces a fresh one, by which time a start has completed.
        calls["n"] += 1
        return running if fresh else stopped

    svc.build_snapshot = racing
    res = svc.save_config_bundle("meshcom", values={"use_gps": "off"})
    assert not res.ok, "the recheck under the lock must refuse"
    assert any("use_gps" in d and "running" in d for d in (res.details or [])), res.details
    assert svc.gps_enabled_for("meshcom") is True, "and the transaction must roll back"


def test_a_running_probe_that_cannot_answer_refuses_the_switch(tmp_path):
    """Fail-closed: 'we could not tell' must never be read as 'it is stopped'."""
    svc = _svc(tmp_path)

    def boom(fresh=False):
        raise RuntimeError("probe exploded")

    svc.build_snapshot = boom
    res = svc.save_config_bundle("meshtastic", values={"use_gps": "off"})   # a real change
    assert not res.ok and "meshtastic" in res.summary, res.summary
    assert "running" in res.summary or "unknown" in res.summary, res.summary
    assert svc.gps_enabled_for("meshtastic") is True, "nothing may have been written"


@pytest.mark.parametrize("pid, why", [
    (None, "no pid at all"),
    (0, "pid 0"),
    (-1, "a negative pid"),
    (True, "a bool — which IS an int in Python, and would read as the always-alive pid 1"),
    ("123", "a string pid"),
])
def test_a_marker_without_a_usable_pid_is_rejected_everywhere(tmp_path, pid, why):
    """The bridge writes its pid on every refresh, so a marker without a usable one is not from a
    feed we are running. Start and status must agree, or the console shows RUNNING for a feed the
    start gate would refuse."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.status import StatusProber
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))

    d = tmp_path / "state" / "gps" / "meshtastic"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"state": "ready", "detail": "", "sentences": 9, "updated": int(time.time())}
    if pid is not None:
        payload["pid"] = pid
    (d / "readiness.json").write_text(json.dumps(payload))

    assert svc._gps_feed_ready(comp)[0] is False, f"start gate accepted {why}"
    ok, note = prober._gps_feed_ready(comp)
    assert ok is False and "pid" in note, f"status accepted {why}: {note}"


def test_publish_symlink_refuses_and_preserves_a_regular_file(tmp_path):
    """The endpoint path is published with an atomic rename, which would have destroyed whatever
    was already there. Only OUR OWN symlink may be replaced; anything else belongs to someone
    else and the publish must fail with the file byte-identical."""
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    leaf = tmp_path / "state" / "gps" / "meshtastic" / "nmea0"
    leaf.parent.mkdir(parents=True, exist_ok=True)
    original = "OPERATOR FILE - MUST SURVIVE\n"
    leaf.write_text(original)
    before = leaf.stat()

    with pytest.raises(runtime_fs.PathContainmentError):
        runtime_fs.publish_symlink(paths, leaf, "/dev/pts/9")

    assert leaf.is_file() and not leaf.is_symlink(), "the regular file must still be a file"
    assert leaf.read_text() == original, "and byte-identical"
    assert leaf.stat().st_ino == before.st_ino, "not replaced behind an identical-looking name"
    assert not list(leaf.parent.glob(".*tmp*")), "and no temporary left behind"

    # Replacing our OWN symlink is the normal case and must still work.
    leaf.unlink()
    runtime_fs.publish_symlink(paths, leaf, "/dev/pts/9")
    assert leaf.is_symlink() and os.readlink(leaf) == "/dev/pts/9"
    runtime_fs.publish_symlink(paths, leaf, "/dev/pts/10")
    assert os.readlink(leaf) == "/dev/pts/10", "a feed restarting must be able to republish"


def test_a_second_thread_never_sees_a_lock_without_its_owner(tmp_path):
    """The flock is granted before the `.owner` record can be written, so a contender could be
    told "busy" by a holder it could not identify — and same-process contention then failed
    instead of serializing. It was papered over with a fixed sub-second grace, which is a race
    against SD-card write latency and lost under load. Acquisition and publication are now
    serialized per key WITHIN the process, so the in-between state is unobservable here."""
    import contextlib
    import threading

    from lhpc.core import reslock, runtime_fs
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 3.0
    key = "claim.loraham.daemon-socket.433"
    flocked, publish_now, released = (threading.Event() for _ in range(3))
    real_write_marker = runtime_fs.write_marker
    seen = []

    def slow_publish(paths, path, text, *a, **k):
        if path.name.endswith(".owner"):
            flocked.set()
            publish_now.wait(5.0)                 # hold the window open indefinitely
        return real_write_marker(paths, path, text, *a, **k)

    def hold():
        with reslock.operation_lock(svc._paths, key, "stop", "meshcom"):
            time.sleep(0.2)
            released.set()

    with mock.patch.object(runtime_fs, "write_marker", slow_publish):
        t = threading.Thread(target=hold)
        t.start()
        try:
            assert flocked.wait(5.0), "holder never reached the publication window"

            def acquire():
                try:
                    with contextlib.ExitStack() as st:
                        svc._acquire_key(st, key, "start", "kiss")
                        seen.append(("ok", released.is_set()))
                except reslock.ResourceBusy as exc:
                    seen.append(("busy", str(exc)))

            c = threading.Thread(target=acquire)
            c.start()
            # The contender is now blocked on the publication lock, NOT racing a timer: hold the
            # window far longer than any grace would have tolerated and it must still serialize.
            time.sleep(1.0)
            publish_now.set()
            c.join(15.0)
        finally:
            publish_now.set()
            t.join(10.0)

    assert seen and seen[0][0] == "ok", f"contender did not serialize: {seen}"
    assert seen[0][1] is True, "it must have waited for the holder to finish, not jumped the queue"


# ---- second-round audit: value handling, fail-closed probes, who really uses a receiver ------

@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_switch_value_cannot_disable_gps_under_a_running_stack(tmp_path, value):
    """`use_gps=""` differs from the default, so it lands in the OVERRIDE bucket. Reading the
    wanted state from the bucket ("in to_set => on") made it look like "on": it matched the
    current "on", was judged 'no change', skipped the running-stack refusal — and then disabled
    GPS anyway, because every reader compares against the literal "on"."""
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshcom", values={"use_gps": "on"}).ok

    from lhpc.core.model import RunState
    _snap_with(svc, {"meshcom-qemu": RunState.RUNNING})
    res = svc.save_config_bundle("meshcom", values={"use_gps": value})
    assert not res.ok, f"{value!r} is a change to OFF and must be refused while running"
    assert "use_gps" in res.summary and "running" in res.summary, res.summary
    assert svc.gps_enabled_for("meshcom") is True, "and GPS must still be on"


def test_the_switch_is_stored_canonically(tmp_path):
    """Anything that is not "on" is the default and clears the key, so the file never holds a
    third state that reads as neither on nor off."""
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshcom", values={"use_gps": "on"}).ok   # default -> cleared
    body = (tmp_path / "config" / "stacks" / "meshcom.toml").read_text()
    assert "use_gps" not in body, f"the default must clear the key, not store it: {body!r}"
    assert svc.save_config_bundle("meshcom", values={"use_gps": ""}).ok      # stack stopped
    body = (tmp_path / "config" / "stacks" / "meshcom.toml").read_text()
    # An empty value canonicalizes to "off" — a real deviation now, stored in canonical form
    # (never the raw empty string, which no reader treats as either state).
    assert 'use_gps = "off"' in body, body
    assert svc.gps_enabled_for("meshcom") is False


def test_an_unreadable_running_state_blocks_a_global_source_change(tmp_path):
    """Fail closed. Swallowing the probe error reported "nobody is using it" and replaced the
    source under a stack that may well have been running — the opposite of what was asked."""
    svc = _svc(tmp_path)
    _enable(svc, "meshtastic", "meshcom", "reticulum")

    def boom(fresh=False):
        raise OSError("probe exploded")

    svc.build_snapshot = boom
    blocked = svc.gps_consumers_running()
    assert blocked, "an unanswerable probe is itself a blocker"
    assert all("unknown" in b for b in blocked), blocked

    res = svc.set_gps(source="nmea", device="/dev/tty", nmea_baud="9600")
    assert not res.ok and "in use by" in res.summary, res.summary
    assert svc.gps_settings()["source"] == "auto", "nothing may have been written"


def test_a_feed_running_on_its_own_blocks_a_global_source_change(tmp_path):
    """It holds the receiver claim and an endpoint built from the CURRENT source while its stack
    reads as stopped, so probing stacks alone missed it."""
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    _snap_with(svc, {"meshcom-gps": RunState.RUNNING})     # its stack's main reads STOPPED

    assert svc.gps_consumers_running() == ["meshcom-gps"]
    res = svc.set_gps(source="fixed", lat="51.4779", lon="-0.0015", alt="12")
    assert not res.ok and "meshcom-gps" in res.summary, res.summary


@pytest.mark.parametrize("cid", ["meshcom-bridge", "meshcom-firmware"])
def test_a_component_that_reads_no_position_gets_no_feed(tmp_path, cid):
    """`meshcom-bridge` is a TCP relay to the daemon and `meshcom-firmware` is a build artifact.
    Treating "belongs to a GPS stack" as "consumes position" started a feed for components that
    never read one — and claimed the receiver for them."""
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    save_gps(svc._paths, source="gpsd")
    svc._invalidate_config()

    order = [c.id for _s, c in (svc._run_order(cid) or [])]
    assert "meshcom-gps" not in order, f"{cid} pulled in a feed it does not read: {order}"
    assert cid in order

    # ...while the real reader still does.
    assert "meshcom-gps" in [c.id for _s, c in (svc._run_order("meshcom-qemu") or [])]


def test_the_fixture_relay_never_claims_the_receiver(tmp_path):
    """It replays a checked-in NMEA file and touches no hardware. A claim for it refused
    combinations that are perfectly valid — a fixture run beside a stack reading the device."""
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    save_gps(svc._paths, source="nmea", device="/dev/tty", nmea_baud="9600")
    svc._invalidate_config()

    assert svc._gps_device_claim("meshcom"), "the stack itself does open the device"
    assert svc._gps_device_claim("meshcom-gps-relay") == "", "the fixture opens nothing"
    assert not [k for k in svc._operation_resource_keys("meshcom-gps-relay")
                if k.startswith("gps.")]
    for cid in ("meshcom-bridge", "meshcom-firmware"):
        assert svc._gps_device_claim(cid) == "", f"{cid} reads no position"


def test_a_connected_source_with_no_fix_keeps_its_marker_fresh(tmp_path):
    """A cold receiver is reachable but delivers nothing for minutes — the documented
    'source reachable, waiting for a fix' state. Refreshing the marker only when sentences
    ARRIVE meant `updated` froze, and the staleness rule then reported the feed DEGRADED
    purely for being patient (and would have refused the next start)."""
    from lhpc.core.gps_bridge import _READY_REFRESH_S, Readiness
    from lhpc.core.paths import Paths
    d = tmp_path / "state" / "gps" / "meshtastic"
    d.mkdir(parents=True)
    marker = d / "readiness.json"
    ready = Readiness(str(marker), Paths(runtime_root=tmp_path))

    ready.degrade("connected", "source reachable, waiting for a fix")
    first = json.loads(marker.read_text())
    assert first["state"] == "connected" and first["sentences"] == 0

    # `updated` has one-second resolution, so a rewrite within the same second looks identical.
    # The file's mtime is what actually proves the marker was rewritten.
    stamp0 = marker.stat().st_mtime_ns
    ready.tick()                                   # too soon — no needless write
    assert marker.stat().st_mtime_ns == stamp0, "a heartbeat every pass would be pointless churn"

    ready._written_at -= _READY_REFRESH_S + 1      # the refresh interval has elapsed
    ready.tick()
    assert marker.stat().st_mtime_ns > stamp0, "the heartbeat must actually rewrite the marker"
    second = json.loads(marker.read_text())
    assert second["updated"] >= first["updated"]
    assert second["state"] == "connected", "without inventing progress that has not happened"
    assert second["sentences"] == 0
    assert second["pid"] == os.getpid()

    # ...and a marker refreshed this way is accepted by the readers, which is the whole point.
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    assert svc._gps_feed_ready(comp)[0] is True, "connected-without-fix must not fail a start"


# ---- strict liveness, and GPS policy that follows the run order ------------------------------

def _snap_with(svc, states: dict, default=None):
    """Pin a snapshot where `states` maps component id -> RunState (rest = `default`)."""
    from lhpc.core.model import RunState as _RS
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = states.get(cid, default if default is not None else _RS.STOPPED)
    svc.build_snapshot = lambda fresh=False: snap
    return snap


@pytest.mark.parametrize("state_name", ["UNKNOWN", "RUNNING", "DEGRADED"])
def test_a_consumer_in_any_live_state_blocks_a_global_source_change(tmp_path, state_name):
    """UNKNOWN is the real shape of "the probe could not answer" — lhpc reports it as a run
    state, not as an exception. Treating it as "not running" replaced the source (and the
    receiver claim) beneath a reader that may well have been live."""
    from lhpc.core import config as _cfg
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshcom", values={"use_gps": "on"}).ok    # while stopped
    _snap_with(svc, {"meshcom-qemu": getattr(RunState, state_name)})

    assert svc.gps_consumers_running() == ["meshcom-qemu" if state_name != "UNKNOWN"
                                           else "meshcom-qemu (state unknown)"]
    res = svc.set_gps(source="gpsd")
    assert not res.ok and "in use by" in res.summary, res.summary
    assert svc.gps_settings()["source"] == "auto", "nothing may have been written"


def test_a_gps_disabled_stack_does_not_block_a_source_change(tmp_path):
    """It takes no position from the global setting, so it is not affected by the change.
    Blocking on it would make the source unchangeable whenever MeshCom happened to be up."""
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    # off is a DELIBERATE choice now (the default is on) — saved before the stack is up.
    assert svc.save_config_bundle("meshcom", values={"use_gps": "off"}).ok
    _snap_with(svc, {"meshcom-qemu": RunState.RUNNING})          # running, use_gps off
    assert svc.gps_consumers_running() == []
    assert svc.set_gps(source="gpsd").ok


def test_a_feed_running_alone_blocks_its_own_switch(tmp_path):
    """`use_gps=off` checked only the stack's MAIN component, so it succeeded while
    `meshcom-gps` was running by itself — orphaning a feed that holds the receiver claim and an
    endpoint built from the setting being removed."""
    from lhpc.core import config as _cfg
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _cfg.save_hardware_setup(svc._paths, "uputronics-868")
    svc._invalidate_config()
    assert svc.save_config_bundle("meshcom", values={"use_gps": "on"}).ok
    _snap_with(svc, {"meshcom-gps": RunState.RUNNING})           # the stack's main is STOPPED
    assert svc.stack_running("meshcom") is False, "this is why checking the main alone missed it"

    res = svc.save_config_bundle("meshcom", values={"use_gps": "off"})
    assert not res.ok and "meshcom-gps" in res.summary, res.summary
    assert svc.gps_enabled_for("meshcom") is True, "and the switch must be untouched"


@pytest.mark.parametrize("cid", ["meshcom-bridge", "meshcom-firmware", "meshcom-gps-relay"])
def test_a_start_that_reads_no_position_is_not_gated_on_gps(tmp_path, cid):
    """These belong to a GPS stack but read nothing: a TCP relay, a build artifact, and the
    fixture. Refusing them because the STACK has GPS enabled while the source is off blocked
    work that never consults either setting."""
    svc = _svc(tmp_path)
    _enable(svc, "meshcom")
    # A combination that refuses a real consumer TODAY: an explicit source whose plan cannot
    # be resolved. (The old sample — on + source off — starts without position now.)
    save_gps(svc._paths, source="nmea", device="/dev/lhpc-test-missing-receiver")
    svc._invalidate_config()

    assert svc.gps_block(cid) == ("", []), f"{cid} reads no position and must not be gated"
    assert svc.gps_block("meshcom-qemu")[0], "...while the real reader still is"


def test_reticulum_claims_nothing_when_sideband_is_not_selected(tmp_path):
    """Sideband is optional. A Reticulum start whose run order is just `rns` touches no
    receiver, so claiming the device took it from a stack that would actually have read it."""
    svc = _svc(tmp_path)
    _enable(svc, "reticulum")
    save_gps(svc._paths, source="nmea", device="/dev/tty", nmea_baud="9600")
    svc._invalidate_config()

    order = [c.id for _s, c in (svc._run_order("reticulum") or [])]
    assert "sideband" not in order, "this test is about the run order WITHOUT Sideband"
    assert svc._gps_device_claim("reticulum") == "", f"claimed the receiver for {order}"
    assert not [k for k in svc._operation_resource_keys("reticulum") if k.startswith("gps.")]
    assert svc.gps_block("reticulum") == ("", []), "and it is not gated on GPS either"

    # A stack that DOES read a position still claims it.
    _enable(svc, "meshcom")
    assert svc._gps_device_claim("meshcom"), "the real reader must still take the receiver"


def test_a_directly_running_reader_keeps_its_receiver_claim(tmp_path):
    """The peer scan must derive GPS ownership from the LIVE component, not from its stack.

    A stack's claim comes from the run order a FUTURE start would use, which is not what is
    running now. With `autostart_sideband` off, a directly started Sideband reads the receiver
    while `reticulum`'s prospective order is just `rns` — so the stack claim was empty and
    MeshCom was admitted onto the same device the moment Sideband's start lock was released
    (the lock is held only for the start; the conflict set is what holds afterwards). Turning
    that autostart off while Sideband ran did the same to an already-running reader.
    """
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _enable(svc, "reticulum", "meshcom")
    save_gps(svc._paths, source="nmea", device="/dev/tty", nmea_baud="9600")
    svc._invalidate_config()

    claim = svc._gps_device_claim("sideband")
    assert claim, "Sideband does open the receiver"
    assert svc._gps_device_claim("reticulum") == "", (
        "and its STACK does not, because the run order without Sideband reads nothing — "
        "that gap is exactly what this guards")

    _snap_with(svc, {"sideband": RunState.RUNNING})
    gps = [b for b in svc.run_blockers("meshcom") if b["resource"].startswith("gps.")]
    assert gps, "a second reader must be blocked while Sideband holds the receiver"
    assert gps[0]["resource"] == claim and gps[0]["holder"] == "sideband", gps

    # `rns` alone reads no position, so it must NOT block a second reader.
    svc2 = _svc(tmp_path)
    _enable(svc2, "reticulum", "meshcom")
    svc2._invalidate_config()
    _snap_with(svc2, {"rns": RunState.RUNNING})
    assert not [b for b in svc2.run_blockers("meshcom") if b["resource"].startswith("gps.")], \
        "rns holds no receiver — blocking on it would refuse a perfectly valid start"


# ---- READY must mean position, not merely parseable bytes -------------------------------------

@pytest.mark.parametrize("line, positional, why", [
    (b"$GPTXT,01,01,02,u-blox ag - www.u-blox.com*50", False,
     "the startup text a u-blox emits in UBX binary mode — the sentence found on hardware"),
    (b"$GPGSV,3,1,11,01,05,123,20*4D", False, "satellites in view carry no position"),
    (b"$GPGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99*30", False, "GSA with no fix"),
    (b"$GPGGA,120000.00,0000.0000,N,00000.0000,E,0,00,99.9,0.0,M,0.0,M,,*67", False,
     "GGA whose fix quality is 0 — the field exists precisely to say 'no fix'"),
    (b"$GPRMC,120000.00,V,5128.6740,N,00000.0900,W,0.0,0.0,030825,,,N*59", False, "RMC void"),
    (b"$GPGGA,120000.00,5128.6740,N,00000.0900,W,1,08,0.9,45.0,M,46.9,M,,*44", True, "GGA with a fix"),
    (b"$GPRMC,120000.00,A,5128.6740,N,00000.0900,W,0.0,0.0,030825,,,A*41", True, "RMC active"),
    (b"$GNGNS,120000.00,5128.6740,N,00000.0900,W,AA,08,0.9,45.0,46.9,,*70", True, "GNS, both fixed"),
    (b"$GNGNS,120000.00,,,,,NN,00,,,,,*7E", False, "GNS reporting no fix on either constellation"),
    (b"$GPGGA,120000.00", False, "truncated mid-sentence"),
    (b"not nmea at all", False, "not a sentence"),
])
def test_only_a_sentence_with_a_valid_fix_counts_as_position(line, positional, why):
    """`carries_position` is what separates "the source is talking" from "position is flowing"."""
    from lhpc.core.gps_bridge import carries_position
    assert carries_position(line) is bool(positional), why


def test_a_source_that_talks_without_a_fix_is_reachable_not_live(tmp_path):
    """FOUND ON HARDWARE: a u-blox left in UBX binary mode by gpsd emits one `$GPTXT`. The feed
    forwarded 75 bytes, counted 1 sentence, and reported `ready` — "position source live" for a
    feed that delivered no position at all. Reachable-without-a-fix is a WARNING state, not a
    lie, and it must still not block a start (a cold receiver needs minutes)."""
    from lhpc.core.gps_bridge import Readiness
    from lhpc.core.paths import Paths
    marker = tmp_path / "readiness.json"
    ready = Readiness(str(marker), Paths(runtime_root=tmp_path))

    ready.note_data(1, 0, 0)                                # the lone $GPTXT: NOT navigation
    assert ready.state == "starting", (
        "a receiver stuck in UBX binary mode emits exactly this; admitting it is what let a feed "
        "delivering nothing report a healthy state, and it must instead reach the binary "
        "diagnosis")

    ready.note_data(40, 0, 40)                              # real navigation traffic, no fix yet
    assert ready.state == "connected", "a cold receiver IS reachable — that must still be admitted"
    assert ready.fixes == 0
    assert json.loads(marker.read_text())["state"] == "connected"

    ready.note_data(2, 2, 2)                                # a real fix finally arrives
    assert ready.state == "ready"
    got = json.loads(marker.read_text())
    assert got["state"] == "ready" and got["fixes"] == 2
    assert got["sentences"] == 43, "sentence count still records everything forwarded"
    assert got["nav"] == 42, "and `nav` records what was actually navigation traffic"


def test_a_feed_reachable_without_a_fix_still_passes_the_start_gate(tmp_path):
    """The cold-receiver case is documented as a warning: refusing it would make a normal start
    impossible for the first minutes after power-on."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.status import StatusProber
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))

    d = tmp_path / "state" / "gps" / "meshtastic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "readiness.json").write_text(json.dumps(
        {"state": "connected", "detail": "source reachable, waiting for a fix",
         "sentences": 1, "fixes": 0, "updated": int(time.time()), "pid": os.getpid()}))

    assert svc._gps_feed_ready(comp)[0] is True, "a reachable source must not fail the start"
    ok, note = prober._gps_feed_ready(comp)
    assert ok is True and "fix" in note, f"...but status must say what it is waiting for: {note}"


# ---- closure: validated navigation traffic, and an unresolvable plan ---------------------------

def _nmea(body: bytes) -> bytes:
    """`body` with a CORRECT checksum appended — fixtures with wrong checksums prove nothing
    once readiness validates them (three of mine did exactly that)."""
    x = 0
    for b in body[1:]:
        x ^= b
    return body + b"*%02X" % x


def test_a_binary_receiver_that_emits_only_a_banner_never_passes_the_gate(tmp_path):
    """A u-blox left in UBX binary mode emits one `$GPTXT` and then binary forever.

    That single line used to (a) put readiness into an ADMITTED state and (b) increment the
    sentence counter, which permanently disabled the binary detector — it only ran while
    `sentences == 0`. The feed then sat reachable-looking while nothing but binary streamed past.
    """
    from lhpc.core.gps_bridge import Readiness, classify_sentence
    from lhpc.core.paths import Paths
    banner = _nmea(b"$GPTXT,01,01,02,u-blox ag - www.u-blox.com")
    assert classify_sentence(banner) == (False, False), "a banner is not navigation traffic"

    ready = Readiness(str(tmp_path / "readiness.json"), Paths(runtime_root=tmp_path))
    ready.note_data(1, 0, 0)
    assert ready.state == "starting", "must NOT be admitted"
    assert ready.nav == 0, "and must not count as navigation, or the binary detector dies"


def test_a_cold_receiver_sending_real_navigation_is_still_admitted(tmp_path):
    """The other half: refusing genuine navigation traffic without a fix would make a normal
    start impossible for the first minutes after power-on."""
    from lhpc.core.gps_bridge import Readiness, classify_sentence
    from lhpc.core.paths import Paths
    cold = _nmea(b"$GPGGA,120000.00,,,,,0,00,99.9,0.0,M,0.0,M,,")
    assert classify_sentence(cold) == (True, False), "navigation, but no fix"

    ready = Readiness(str(tmp_path / "readiness.json"), Paths(runtime_root=tmp_path))
    ready.note_data(1, 0, 1)
    assert ready.state == "connected"


@pytest.mark.parametrize("line, why", [
    (b"$GPRMC,120000.00,A,,,,,0.0,0.0,030825,,,A", "flagged active with NO coordinates"),
    (b"$GPGGA,120000.00,5128.6740,N,00000.0900,W,X,08,0.9,45.0,M,46.9,M,,", "illegal quality 'X'"),
    (b"$GPGGA,120000.00,5128.6740,N,00000.0900,W,9,08,0.9,45.0,M,46.9,M,,", "quality out of range"),
    (b"$GPGGA,120000.00,5128.6740,X,00000.0900,Q,1,08,0.9,45.0,M,46.9,M,,", "impossible hemispheres"),
])
def test_a_malformed_sentence_is_never_a_position(line, why):
    """Readiness is a claim about the source; it must not rest on values a garbled line invented."""
    from lhpc.core.gps_bridge import carries_position
    assert carries_position(_nmea(line)) is False, why


def test_a_corrupt_sentence_is_never_a_position():
    """Same sentence, one flipped checksum digit — the payload is no longer trustworthy."""
    from lhpc.core.gps_bridge import carries_position
    good = _nmea(b"$GPGGA,120000.00,5128.6740,N,00000.0900,W,1,08,0.9,45.0,M,46.9,M,,")
    assert carries_position(good) is True
    assert carries_position(good[:-2] + b"00") is False, "a bad checksum must not be believed"


def test_a_partial_line_cannot_grow_without_bound():
    """Both pumps accumulate until a newline. A stream with no newlines at all — UBX binary, or a
    wedged remote — grew that buffer forever, and kept growing after the diagnosis had fired."""
    from lhpc.core.gps_bridge import _MAX_PARTIAL, _bounded
    assert _bounded(b"$GPGGA,1200") == b"$GPGGA,1200", "a real partial sentence is kept"
    assert _bounded(b"\xb5\x62" * _MAX_PARTIAL) == b"", "a runaway partial is discarded"
    assert len(_bounded(b"x" * _MAX_PARTIAL)) == _MAX_PARTIAL, "the bound itself is not exceeded"


def test_an_unresolvable_device_refuses_the_start_instead_of_running_blind(tmp_path):
    """`plan_from_config` reports an unusable `nmea` device by returning `valid=False` — and
    represents it as `source = off`. Testing `enabled` before `valid` therefore returned SUCCESS:
    the stack started with no feed and no receiver claim, silently position-blind, while
    `lhpc gps` still reported a healthy `source: nmea`."""
    svc = _svc(tmp_path)
    _enable(svc, "meshtastic")
    save_gps(svc._paths, source="nmea", device="/dev/does-not-exist", nmea_baud="9600")
    svc._invalidate_config()

    plan = svc.gps_plan("meshtastic")
    assert plan.valid is False and plan.enabled is False, "this is the shape that fooled the gate"

    reason, cmds = svc.gps_block("meshtastic")
    assert reason and "could not be resolved" in reason, f"must refuse, got {reason!r}"
    assert cmds == ["lhpc gps"]

    view = svc.gps_settings()
    assert view["source"] == "nmea", "the saved setting is still what the operator chose"
    assert view["plan_valid"] is False, "...but the surfaces must say it does not resolve"
    assert "UNUSABLE" in " ".join(svc.set_gps().details), "and the CLI must name it"


def test_a_genuinely_off_source_is_not_reported_as_unresolvable(tmp_path):
    """The unresolvable-plan refusal must not swallow the ordinary `off` case — which is not a
    refusal AT ALL any more: with the switch defaulting on, `off` + `use_gps on` starts the
    stack without position instead of blocking it."""
    svc = _svc(tmp_path)
    _enable(svc, "meshtastic")
    save_gps(svc._paths, source="off")
    svc._invalidate_config()
    assert svc.gps_block("meshtastic") == ("", [])
    assert svc.gps_settings()["plan_valid"] is True


# ---- gpsd admission, driven through the REAL pump ---------------------------------------------

def _run_gpsd_pump(tmp_path, srv, seconds=1.2):
    """Drive `_pump_gpsd` against a fake gpsd and return its readiness marker.

    Deliberately NOT `Readiness.note_data(...)` by hand: the defect this guards lived in the
    pump's connect path, so a test that calls the state machine directly cannot see it.
    """
    import threading

    from lhpc.core.gps_bridge import PtyOutput, Readiness, _pump_gpsd
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    out = PtyOutput(str(tmp_path / "nmea0"), paths)
    out.publish()
    ready = Readiness(str(tmp_path / "readiness.json"), paths)
    stop = threading.Event()
    t = threading.Thread(target=_pump_gpsd, args=("127.0.0.1", srv.port, out, ready, stop),
                         daemon=True)
    t.start()
    time.sleep(seconds)
    stop.set()
    t.join(5)
    out.close()
    return ready


def test_a_gpsd_that_sends_nothing_never_admits_a_start(tmp_path, fake_gpsd):
    """gpsd accepts connections whether or not it owns a receiver — after a restart it commonly
    reports `devices: []` and streams nothing (hit exactly this on hardware). Announcing
    `connected` on the completed handshake admitted that, and the heartbeat kept it alive
    indefinitely, so the stack came up position-blind."""
    srv = fake_gpsd(sentences=[])
    try:
        ready = _run_gpsd_pump(tmp_path, srv)
    finally:
        srv.close()
    assert (ready.state, ready.nav, ready.fixes) == ("starting", 0, 0), (
        f"an empty gpsd must not be admitted, got {ready.state!r}")

    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.status import StatusProber
    svc = _svc(tmp_path)
    comp = _feed_comp(svc, "meshtastic", "meshtastic-gps")
    d = tmp_path / "state" / "gps" / "meshtastic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "readiness.json").write_text((tmp_path / "readiness.json").read_text())
    assert svc._gps_feed_ready(comp)[0] is False, "and the start gate must refuse it"
    assert StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))._gps_feed_ready(
        comp)[0] is False


def test_a_gpsd_that_sends_only_a_banner_never_admits_a_start(tmp_path, fake_gpsd):
    """Non-navigation traffic is not evidence of a position source either."""
    srv = fake_gpsd(sentences=[_nmea(b"$GPTXT,01,01,02,u-blox ag - www.u-blox.com").decode()])
    try:
        ready = _run_gpsd_pump(tmp_path, srv)
    finally:
        srv.close()
    assert ready.state == "starting", f"a banner-only gpsd must not be admitted ({ready.state})"
    assert ready.sentences >= 1, "the banner IS forwarded — it is just not evidence"
    assert ready.nav == 0


def test_a_gpsd_sending_navigation_without_a_fix_is_admitted_as_connected(tmp_path, fake_gpsd):
    """The cold-receiver case must still start the stack."""
    srv = fake_gpsd(sentences=[_nmea(b"$GPGGA,120000.00,,,,,0,00,99.9,0.0,M,0.0,M,,").decode()])
    try:
        ready = _run_gpsd_pump(tmp_path, srv)
    finally:
        srv.close()
    assert ready.state == "connected" and ready.nav > 0 and ready.fixes == 0, ready.state


def test_a_gpsd_sending_a_real_fix_becomes_ready(tmp_path, fake_gpsd):
    srv = fake_gpsd(sentences=[
        _nmea(b"$GPGGA,120000.00,5128.6740,N,00000.0900,W,1,08,0.9,45.0,M,46.9,M,,").decode()])
    try:
        ready = _run_gpsd_pump(tmp_path, srv)
    finally:
        srv.close()
    assert ready.state == "ready" and ready.fixes > 0, ready.state


def test_production_gps_feed_is_not_an_optional_start_choice(tmp_path):
    # Whether a production feed runs is decided by the stack's `use_gps` switch plus the global
    # source — never by a per-start checkbox. Offering one let the operator contradict the
    # position plan (and a direct start is refused anyway: "the current position plan does not
    # use it"), and it put a SECOND GPS entry beside the fixture on the confirm page.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    feeds = svc._all_gps_feed_ids()
    assert "meshcom-gps" in feeds and "meshtastic-gps" in feeds
    for sid in ("meshcom", "meshtastic"):
        offered = {o["id"] for o in svc.config_view(sid)["optional"]}
        assert not (offered & feeds), f"{sid} still offers a production GPS feed: {offered & feeds}"


def test_the_gps_fixture_is_not_an_operator_start_choice(tmp_path):
    # meshcom-gps-relay replays a checked-in synthetic NMEA file. It sat on the confirm page as an
    # ordinary optional component, beside the real feed — two GPS entries, one of which silently
    # ignores the global source. `optional` could not express "test facility" (it already means
    # "not started with the stack"), hence the explicit `test_fixture` declaration.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    relay = next(c for c in svc.stack("meshcom").components if c.id == "meshcom-gps-relay")
    assert relay.test_fixture is True and relay.optional is True
    assert not {o["id"] for o in svc.config_view("meshcom")["optional"]}, \
        "meshcom must offer no GPS start choices at all — use_gps is the opt-out"
    # ...but a genuine optional component is still offered (guard against over-filtering).
    assert "loraham-kiss-serial" in {o["id"] for o in svc.config_view("kiss")["optional"]}


def test_graywolf_post_step_values_map_the_plan_to_its_native_gps():
    """graywolf needs NO bridge — it speaks gpsd and serial NMEA natively — so the resolved plan
    maps straight onto its own /api/gps settings. Applied in BOTH directions: `off` and `fixed`
    must push `none`, or a station enabled earlier keeps reporting from its old source."""
    from lhpc.core.gps import GpsPlan, graywolf_post_step_values

    def argv(plan):
        return graywolf_post_step_values(plan)["gps_args"]

    assert argv(GpsPlan(source="gpsd", host="192.168.0.10", port=2947)) == [
        "--gps-source", "gpsd", "--gps-host", "192.168.0.10", "--gps-port", "2947"]
    assert argv(GpsPlan(source="nmea", device="/dev/ttyACM0", nmea_baud=38400)) == [
        "--gps-source", "serial", "--gps-device", "/dev/ttyACM0", "--gps-baud", "38400"]

    # fixed -> none: graywolf's GPS has no fixed mode, and a fixed position belongs to its
    # beacons, which are the operator's. LHPC must not invent coordinates.
    assert argv(GpsPlan(source="fixed", fixed_lat="48.4", fixed_lon="9.9")) == [
        "--gps-source", "none"]
    assert argv(GpsPlan()) == ["--gps-source", "none"]                       # off
    # A stack that opted out reports nothing, whatever the global source is.
    assert argv(GpsPlan(source="gpsd", host="h", port=1).disabled_for_stack()) == [
        "--gps-source", "none"]
    # An incomplete plan must not produce a half-configured source.
    assert argv(GpsPlan(source="gpsd")) == ["--gps-source", "none"]
    assert argv(GpsPlan(source="nmea")) == ["--gps-source", "none"]


def test_graywolf_needs_no_gps_bridge_component():
    """meshtastic-gps and meshcom-gps exist because those apps can only read a local device or
    a local gpsd. graywolf can do neither-bridge: if a graywolf-gps component ever appears, this
    reasoning changed and the manifest comment is stale."""
    import pathlib

    from lhpc.core.manifest import load_manifest

    gw = next(s for s in load_manifest() if s.id == "graywolf")
    assert [c.id for c in gw.components] == ["graywolf"]
    assert any(p.name == "use_gps" for p in gw.components[0].run_params)
    del pathlib


def test_gps_capable_stacks_are_derived_from_the_manifest(tmp_path):
    """The GPS-capable set was a hardcoded tuple, and it drifted: graywolf declared `use_gps`
    while the tuple named only the older three, so `gps_enabled_for("graywolf")` answered False
    on a box whose SAVED value was "on". The start form echoes the saved value, that echo then
    read as a per-start CHANGE, and every graywolf start was refused with "use_gps cannot be
    changed for a single start". Deriving the set from the manifest is what makes that
    unrepresentable — a stack declaring the param IS GPS-capable, by construction."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))

    declared = {s.id for s in svc.stacks()
                for c in s.components
                if any(p.name == "use_gps" for p in c.run_params)}
    assert svc._gps_stacks() == declared
    assert "graywolf" in declared                      # the stack the old tuple missed

    for sid in declared:
        assert svc.gps_owner_stack(sid) == sid
        # ... and the saved switch is actually readable for each, in BOTH states.
        svc.save_stack_config(sid, {"use_gps": "on"})
        assert svc.gps_enabled_for(sid) is True
        svc.save_stack_config(sid, {"use_gps": "off"})
        assert svc.gps_enabled_for(sid) is False

    # A stack with no such param stays out — the switch is not invented for it.
    assert svc.gps_owner_stack("kiss") == ""


def _gsvc(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_position_feed_is_never_an_operator_autostart_choice(tmp_path):
    """LIVE-FOUND on the box: MeshCom would not start. The Confirm:start page offered the GPS feed
    AND the test fixture as auto-start checkboxes — two GPS controls beside the real `use_gps`
    switch. Ticking the feed saved `autostart_meshcom-gps = on`, which forced it into the run order
    while `use_gps` was off, and starting a feed the plan does not want is REFUSED ("the current
    position plan does not use it") — so one stale tick stopped the whole stack from starting, and
    kept stopping it. The plan is the only admitter; the checkboxes are gone."""
    svc = _gsvc(tmp_path)
    feeds = svc._all_gps_feed_ids()
    assert "meshcom-gps" in feeds                                  # precondition

    # Neither a feed nor a fixture is offered as a checkbox any more.
    offered = {o["id"] for o in svc.optional_start_components("meshcom")}
    assert not (offered & feeds)
    assert "meshcom-gps-relay" not in offered                      # test_fixture
    # ... and the same is true for every stack that has a feed at all.
    for sid in {svc.stack_of(f) for f in feeds}:
        assert not ({o["id"] for o in svc.optional_start_components(sid)} & feeds), sid

    # The exact broken state from the box: feed ticked, GPS off -> feed stays OUT.
    # (off is explicit now — the switch defaults on since the source gained `auto`.)
    svc.save_stack_config("meshcom", {"autostart_meshcom-gps": "on", "use_gps": "off"})
    assert svc.gps_enabled_for("meshcom") is False
    assert "meshcom-gps" not in [c.id for _, c in svc._run_order("meshcom")]

    # And the plan still admits it when GPS is genuinely on — the checkbox was never what
    # turned the feed on, so removing it takes nothing away.
    svc.set_gps(source="gpsd", host="127.0.0.1", port=2947)
    svc.save_stack_config("meshcom", {"use_gps": "on"})
    svc._invalidate_config()
    assert svc.gps_plan("meshcom").enabled
    assert "meshcom-gps" in [c.id for _, c in svc._run_order("meshcom")]


def test_gps_consumers_are_derived_from_the_manifest_and_gate_graywolf(tmp_path):
    """REVIEW-FOUND: the first derivation fix covered `_gps_stacks()` but left the PARALLEL
    hardcoded consumer set one function below it, still missing graywolf — so the whole GPS
    admission gate (`gps_block`) returned early for graywolf and it started position-blind with
    use_gps on and the source off, while meshtastic was refused. Both sets are manifest-derived
    now (`reads_position` on the component that actually reads, because the `use_gps` param
    cannot say who does: reticulum declares it on rns, sideband is the reader)."""
    svc = _gsvc(tmp_path)

    declared = {c.id for s in svc.stacks() for c in s.components if c.reads_position}
    assert svc._gps_consumer_ids() == declared
    assert {"graywolf", "meshtastic", "meshcom-qemu", "sideband"} <= declared
    assert "rns" not in declared                      # declares use_gps, reads nothing
    # Every GPS-capable stack must contain a RUNTIME reader — a use_gps switch with nobody
    # reading is exactly the drift this test exists to catch. MeshCore joined that set when
    # it gained a live NMEA feed: it now holds a reader open for as long as it runs, so it
    # must gate the admission/liveness machinery like every other consumer.
    assert "meshcore" in svc._gps_stacks()
    assert "meshcore-node" in declared
    for sid in svc._gps_stacks():
        s = svc.stack(sid)
        assert any(c.reads_position for c in s.components), sid

    # graywolf must pass the SAME consumer gate as meshtastic. The on+source-off refusal is
    # gone (defaults are on+auto; that combination now starts without position), so parity is
    # proven on a refusal that survives: a malformed [gps] section, which stays fail-closed.
    (svc._paths.runtime_root / "config" / "local.toml").write_text(
        '[gps]\nsource = "banana"\n')
    svc._invalidate_config()
    reasons = {}
    for sid in ("meshtastic", "graywolf"):
        reason, _cmds = svc.gps_block(sid)
        reasons[sid] = reason
        assert "invalid" in reason, (sid, reason)
    assert reasons["meshtastic"] == reasons["graywolf"]

    # Derived sets are cached: same frozen object every call (they sit on hot paths).
    assert svc._gps_consumer_ids() is svc._gps_consumer_ids()
    assert svc._gps_stacks() is svc._gps_stacks()


def test_a_stale_fixture_autostart_tick_is_ignored_by_the_run_order(tmp_path):
    """REVIEW-FOUND: the stale-tick fix covered production FEEDS only, while the removed
    confirm-page checkboxes covered feeds AND the fixture — so a pre-existing saved
    `autostart_meshcom-gps-relay = on` still forced the fixture into every meshcom start,
    silently replaying a synthetic position on air, with no UI left that could show or clear
    the flag. Feeds and fixtures now share ONE predicate (`_never_operator_autostart`)."""
    svc = _gsvc(tmp_path)
    svc.save_stack_config("meshcom", {"autostart_meshcom-gps-relay": "on"})

    order = [c.id for _, c in svc._run_order("meshcom")]
    assert "meshcom-gps-relay" not in order
    # The deliberate path is untouched: naming the fixture directly still runs it.
    assert svc.run_action("start", "meshcom-gps-relay", apply=False).ok is True
    # And the predicate is THE shared rule: everything it excludes is absent from both
    # operator-facing lists, for every stack.
    for s in svc.stacks():
        excluded = {c.id for c in s.components if svc._never_operator_autostart(c)}
        assert not ({o["id"] for o in svc.optional_start_components(s.id)} & excluded), s.id


def test_auto_source_resolves_softly_and_explicit_sources_stay_fail_closed(tmp_path,
                                                                            monkeypatch):
    """The DEFAULTS are now `auto` + per-stack `use_gps = on`. A default must never refuse a
    start (nobody expressed intent), so `auto` is soft: a listening localhost gpsd becomes an
    ordinary gpsd plan; nothing listening becomes "off, running without position" — starts
    proceed. Fail-closed protection moves to EXPLICIT intent: a malformed section still
    refuses, and explicit `off` simply runs without position."""
    from lhpc.core import gps as gps_mod
    svc = _gsvc(tmp_path)

    # Untouched box: source auto, every GPS-capable stack's switch defaults ON.
    assert svc.gps_settings()["source"] == "auto"
    for sid in svc._gps_stacks():
        assert svc.gps_enabled_for(sid) is True, sid

    # No gpsd (the conftest default): resolved off, nothing refused, nothing admitted.
    assert svc.gps_settings()["resolved_source"] == "off"
    plan = svc.gps_plan("graywolf")
    assert not plan.enabled and "no gpsd on this box" in plan.reason
    for sid in svc._gps_stacks():
        assert svc.gps_block(sid) == ("", []), sid
    assert "meshcom-gps" not in [c.id for _, c in svc._run_order("meshcom")]

    # A gpsd appears: the verdict is FROZEN per loaded config, so the change is noticed at
    # the request boundary (refresh_gps_auto — what the web app calls per request), never
    # mid-operation.
    monkeypatch.setattr(gps_mod, "local_gpsd_listening", lambda: True)
    assert svc.gps_settings()["resolved_source"] == "off"    # the loaded config's verdict stands
    svc.refresh_gps_auto()                                   # request boundary
    assert svc.gps_settings()["resolved_source"] == "gpsd"
    plan = svc.gps_plan("graywolf")
    assert plan.source == "gpsd" and (plan.host, plan.port) == ("127.0.0.1", 2947)
    assert "meshcom-gps" in [c.id for _, c in svc._run_order("meshcom")]
    assert gps_mod.graywolf_post_step_values(plan)["gps_args"][:2] == ["--gps-source", "gpsd"]

    # Explicit off: not auto, and not a refusal either — the stack runs without position.
    monkeypatch.setattr(gps_mod, "local_gpsd_listening", lambda: False)
    svc.set_gps(source="off")
    svc._invalidate_config()
    assert svc.gps_settings()["source"] == "off"
    assert svc.gps_block("meshtastic") == ("", [])

    # Malformed section: still fail-closed — broken config must not become best-effort.
    (tmp_path / "config" / "local.toml").write_text('[gps]\nsource = "banana"\n')
    svc._invalidate_config()
    reason, _ = svc.gps_block("meshtastic")
    assert "invalid" in reason
    assert svc.gps_block("meshtastic") == svc.gps_block("graywolf")   # consumer-gate parity


def test_changing_the_source_is_blocked_by_a_running_graywolf(tmp_path):
    """AUDIT-FOUND: `gps_consumers_running` was the THIRD hardcoded GPS stack list, and it
    drifted like the other two — graywolf missing, so `lhpc gps --source ...` went through
    under a running GPS-enabled graywolf, leaving its live process on the old source while
    the saved plan described the new one. The list is the derived set now."""
    svc = _gsvc(tmp_path)
    watched = set()
    svc.gps_liveness_blockers = lambda ids, snap=None, require_enabled=False: (
        watched.update(ids) or [])
    svc.gps_consumers_running()
    assert watched == set(svc._gps_stacks())
    assert "graywolf" in watched


def test_fixed_source_says_that_graywolf_gets_no_position(tmp_path):
    """AUDIT-FOUND honesty gap: graywolf has no fixed GPS mode (its API takes serial, gpsd
    or none), so a `fixed` box runs it with GPS off while `use_gps` reads on. Deliberately
    NOT a refusal — with the switch defaulting on, a fixed box could otherwise never start
    graywolf — but `lhpc gps` must say it, where the operator chose the source."""
    svc = _gsvc(tmp_path)
    svc.set_gps(source="fixed", fixed_lat="48.4", fixed_lon="11.6")
    svc._invalidate_config()
    from lhpc.core.gps import graywolf_post_step_values
    assert graywolf_post_step_values(svc.gps_plan("graywolf"))["gps_args"] == \
        ["--gps-source", "none"]
    assert svc.gps_block("graywolf") == ("", [])              # starts, deliberately
    report = svc.set_gps()
    assert any("graywolf has no fixed GPS mode" in d for d in report.details)


def test_meshcore_cli_build_byte_compiles_the_pinned_source():
    """AUDIT-FOUND: upstream meshcore-cli once shipped Python-3.12-only f-string syntax
    (PEP 701) while LHPC supports >=3.11 (Bookworm), so the pin was held back to the last
    3.11-clean commit. Upstream has since fixed that — v1.6.3 byte-compiles under a real
    3.11 — and the old pin had to move anyway: a `main` rewrite left it in no branch and no
    tag, so a fresh clone+checkout could not reach it at all.

    The build still byte-compiles the source so a future pin that regresses on 3.11 fails
    the BUILD with file and line instead of a SyntaxError at first run. This test pins both
    halves of that contract: the guard step exists, and the pin is the vetted one."""
    from lhpc.core.manifest import load_manifest
    mc = next(c for s in load_manifest() for c in s.components if c.id == "meshcore-cli")
    assert mc.source.pin_commit == "568d158bc780c318c3d8706f71bfb980cb1ca588"   # v1.6.3
    compile_steps = [st for st in mc.build_steps
                     if "compileall" in " ".join(st.get("argv", []))]
    assert len(compile_steps) == 1
    assert compile_steps[0]["argv"][0] == ".venv/bin/python"  # the venv the code will run in


def test_the_auto_probe_accepts_only_listeners_reachable_at_the_advertised_endpoint():
    """AUDIT-FOUND: the parser matched LISTEN + port only, so an ::1-only, a 192.168.x-bound,
    or a non-gpsd 2947 listener activated `auto` — and every consumer was then pointed at
    127.0.0.1:2947, where nothing listened: the promised soft "no gpsd" turned into GPS-feed
    start failures. Only 127.0.0.1 and the IPv4 wildcard prove that endpoint. These are the
    DIRECT parser tests the mocked-out suite lacked."""
    from lhpc.core.gps import _tcp4_shows_local_gpsd

    hdr = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
           "   uid  timeout inode\n")

    def row(addr_hex, port, state="0A"):
        return (f"   0: {addr_hex}:{port:04X} 00000000:0000 {state} 00000000:00000000"
                f" 00:00000000 00000000  0        0 12345 1 0000000000000000 100 0 0 10 0\n")

    assert _tcp4_shows_local_gpsd(hdr + row("0100007F", 2947)) is True     # 127.0.0.1
    assert _tcp4_shows_local_gpsd(hdr + row("00000000", 2947)) is True     # 0.0.0.0 wildcard
    assert _tcp4_shows_local_gpsd(hdr + row("0100007F", 2947, state="01")) is False  # ESTABLISHED
    assert _tcp4_shows_local_gpsd(hdr + row("0100007F", 2948)) is False    # wrong port
    assert _tcp4_shows_local_gpsd(hdr + row("0101A8C0", 2947)) is False    # 192.168.1.1-bound
    assert _tcp4_shows_local_gpsd(hdr) is False                            # nothing at all
    # Several rows: one reachable listener among noise is enough.
    noisy = hdr + row("0101A8C0", 2947) + row("0100007F", 8080) + row("00000000", 2947)
    assert _tcp4_shows_local_gpsd(noisy) is True


def test_one_loaded_config_is_one_frozen_auto_decision(tmp_path, monkeypatch):
    """AUDIT-FOUND: `auto` re-probed live /proc state on every resolution, so one applied
    start could see gpsd when planning the run order and not-gpsd when rendering or when the
    bridge process resolved again — admitting a feed that then failed the whole start, a
    refusal `auto` promises never to produce. The verdict is now FROZEN into the loaded
    GpsConfig, and the controller hands it to the bridge process via AUTO_ENV."""
    from lhpc.core import gps as gps_mod
    from lhpc.core.config import load_config
    from lhpc.core.gps import plan_from_config
    from lhpc.core.paths import Paths

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    paths = Paths(runtime_root=tmp_path)

    # gpsd is up at load time; the loaded config freezes that verdict...
    monkeypatch.setattr(gps_mod, "local_gpsd_listening", lambda: True)
    cfg = load_config(paths)
    assert cfg.gps.source == "auto" and cfg.gps.auto_listening is True
    # ...so a later flap changes NOTHING for consumers of this config object.
    monkeypatch.setattr(gps_mod, "local_gpsd_listening",
                        lambda: (_ for _ in ()).throw(AssertionError("re-probed a frozen config")))
    for _ in range(3):
        assert plan_from_config(cfg).source == "gpsd"

    # The bridge boundary: an explicit hint overrides everything — the controller's verdict
    # wins over whatever the bridge's own load would have seen.
    assert plan_from_config(cfg, auto_hint=False).source == "off"
    assert "auto: no gpsd" in plan_from_config(cfg, auto_hint=False).reason


def test_the_controller_hands_its_auto_verdict_to_the_bridge_process(tmp_path, monkeypatch):
    """The bridge re-resolves `[gps]` in its own process; under `auto` the controller's
    frozen verdict must cross that boundary (AUTO_ENV), or a gpsd stopping mid-start makes
    the bridge exit EXIT_CONFIG and fail the stack. Only the bare verdict crosses — never a
    host or port."""
    from lhpc.core import gps as gps_mod
    from lhpc.core.config import load_config
    from lhpc.core.gps import AUTO_ENV
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gps_mod, "local_gpsd_listening", lambda: True)
    cfg = load_config(Paths(runtime_root=tmp_path))

    seen = {}

    def spy_spawn(argv, log_path, cwd=None, env=None):
        seen["env"] = env or {}
        return None                                          # launch "fails" — env already captured

    life = Lifecycle(Paths(runtime_root=tmp_path), (), cfg, FakeSystem().system, spawn=spy_spawn)
    from lhpc.core.manifest import load_manifest
    stack = next(s for s in load_manifest() if s.id == "meshtastic")
    feed = next(c for c in stack.components if c.id == "meshtastic-gps")
    life.start(stack, feed)
    assert seen["env"].get(AUTO_ENV) == "gpsd"               # the frozen verdict, nothing more
    assert "2947" not in seen["env"].get(AUTO_ENV, "")

    # A non-feed component gets NO such variable — the channel exists for bridges only.
    main = next(c for c in stack.components if c.id == "meshtastic")
    seen.clear()
    life.start(stack, main)
    assert AUTO_ENV not in seen.get("env", {})


def _admission_svc(tmp_path, feed_marker_text):
    """A service whose meshtastic feed marker holds `feed_marker_text`, ready for the START
    gate (`_gps_feed_admission`) — the piece that decides whether the stack start proceeds."""
    svc = _svc(tmp_path)
    d = tmp_path / "state" / "gps" / "meshtastic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "readiness.json").write_text(feed_marker_text)
    return svc, _feed_comp(svc, "meshtastic", "meshtastic-gps")


def test_auto_admits_a_stack_whose_gpsd_vanished_or_delivers_nothing(tmp_path, fake_gpsd,
                                                                      monkeypatch):
    """AUDIT-FOUND: freezing the auto verdict was not enough. gpsd disappearing (or owning no
    receiver) after admission left the feed starting/source-lost, the gate refused it, and
    the stack did not start — a refusal produced purely by `auto`. Under `auto` those two
    LIVE-feed states are non-gating: the stack starts without position and the feed is NOT
    stopped, so a gpsd appearing later starts delivering with no restart. Markers come from
    the REAL pump, never hand-written."""
    from lhpc.core import gps as gps_mod

    # (1) gpsd DISAPPEARED before the bridge connected: dial a closed port -> source-lost.
    srv = fake_gpsd(sentences=[])
    port = srv.port
    srv.close()                                            # nothing listens here any more
    ready = _run_gpsd_pump(tmp_path, type("S", (), {"port": port})())
    assert ready.state in ("starting", "source-lost"), ready.state

    monkeypatch.setattr(gps_mod, "local_gpsd_listening", lambda: True)   # auto froze "gpsd"
    svc, comp = _admission_svc(tmp_path, (tmp_path / "readiness.json").read_text())
    assert svc.gps_settings()["source"] == "auto"          # the default, untouched
    ok, ev = svc._gps_feed_admission(comp)
    assert ok is True and "WITHOUT position" in ev

    # (2) gpsd LISTENING but no receiver / no NMEA: the pump stays `starting`.
    srv2 = fake_gpsd(sentences=[])
    try:
        ready2 = _run_gpsd_pump(tmp_path, srv2)
    finally:
        srv2.close()
    assert ready2.state == "starting", ready2.state
    svc2, comp2 = _admission_svc(tmp_path / "b", (tmp_path / "readiness.json").read_text())
    ok2, ev2 = svc2._gps_feed_admission(comp2)
    assert ok2 is True and "WITHOUT position" in ev2

    # A feed with NO marker at all (dead/never-published bridge) still fails normally.
    svc3 = _svc(tmp_path / "c")
    comp3 = _feed_comp(svc3, "meshtastic", "meshtastic-gps")
    assert svc3._gps_feed_admission(comp3)[0] is False


def test_explicit_gpsd_with_an_empty_gpsd_still_refuses_the_start(tmp_path, fake_gpsd):
    """The soft rule is `auto`-only: an operator who NAMED gpsd gets today's fail-closed
    refusal for a gpsd that streams nothing — starting position-blind would hide breakage
    they explicitly configured against."""
    srv = fake_gpsd(sentences=[])
    try:
        ready = _run_gpsd_pump(tmp_path, srv)
    finally:
        srv.close()
    assert ready.state == "starting"

    svc, comp = _admission_svc(tmp_path, (tmp_path / "readiness.json").read_text())
    save_gps(svc._paths, source="gpsd")                    # EXPLICIT
    svc._invalidate_config()
    ok, ev = svc._gps_feed_admission(comp)
    assert ok is False and "not reachable" in ev
