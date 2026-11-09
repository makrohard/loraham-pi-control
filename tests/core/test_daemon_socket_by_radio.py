"""Every read and SET of a band's daemon reaches its radio's own socket at stage L5 (B4-D33; d9cc1faa W3-1, W6-1).

When the hardware list resolves a band to a radio, the daemon serves `loraconf-<rid>.sock` and `lora-<rid>.sock`
only; a 0.13.x band socket (`loraconf<band>.sock`) is never that radio's (W3-6). The fake daemon here answers on
the rid sockets alone and fails every request to a band socket, so a path that still builds the band socket
reads "unreachable" instead of passing on a reply the real daemon would never give. Without a hardware list the
band socket is still the one read (a 0.13.x daemon).
"""
from __future__ import annotations

import dataclasses

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

RID = {"433": "r1", "868": "r2"}
BAND_SOCKETS = {f"{d}/{p}{b}.sock" for d in ("/tmp", "/run/loraham") for p in ("loraconf", "lora", "loraf")
                for b in RID}


@dataclasses.dataclass
class RidDaemon(FakeSystem):
    """A daemon per radio: each CONF socket keeps its own STATUS fields, a SET changes them, GET STATS counts
    TXOK, and a send on the data socket transmits one frame. `serve` names the CONF socket paths it answers;
    every other path raises, and `asked` records each path in order."""
    serve: dict = dataclasses.field(default_factory=dict)       # conf path -> STATUS fields
    asked: list = dataclasses.field(default_factory=list)
    txok: dict = dataclasses.field(default_factory=dict)        # conf path -> TXOK count

    def request(self, path, payload, timeout, max_bytes):
        self.asked.append(path)
        if path not in self.serve:
            raise ConnectionRefusedError(f"no daemon on {path}")
        st = self.serve[path]
        line = payload.decode("ascii").strip()
        if line.startswith("SET "):
            self.sent.append((path, payload))
            k, _, v = line[4:].partition("=")
            st[k] = v
            return b"OK\n"
        if line == "GET STATUS":
            return ("STATUS " + " ".join(f"{k}={v}" for k, v in st.items()) + "\n").encode()
        if line == "GET STATS":
            return f"STATS TXOK={self.txok.get(path, 0)} RXOK=0\n".encode()
        if line.startswith("GET CHANNEL"):
            return b"CHANNEL MODE=RX RSSI=-120 CADSTATE=NOTSCANNED BUSY=0\n"
        return b"ERR UNKNOWN\n"

    def send(self, path, payload, timeout):
        self.asked.append(path)
        conf = path.replace("/lora-", "/loraconf-")
        if path.endswith(".sock") and "/lora-" in path and conf in self.serve:
            self.sent.append((path, payload))
            self.txok[conf] = self.txok.get(conf, 0) + 1
            return
        raise ConnectionRefusedError(f"no daemon on {path}")


def _status(rid, band):
    return {"RADIO": "READY", "ID": rid, "BOUND": "-", "BAND": band, "TXMODE": "DIRECT", "POWER": "10"}


def _svc(tmp_path, *, hardware_list=True):
    from lhpc.core import config as cfgmod
    from lhpc.core import radios
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    if hardware_list:
        rows = (radios.Radio("r1", "low", "uputronics", "433", ce="ce0"),
                radios.Radio("r2", "high", "uputronics", "868", ce="ce1"))
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "radios.toml").write_text(radios.render(rows))
        serve = {f"/tmp/loraconf-{r}.sock": _status(r, b) for b, r in RID.items()}
    else:
        serve = {f"/tmp/loraconf{b}.sock": {"RADIO": "READY", "BAND": b, "TXMODE": "DIRECT", "POWER": "10"}
                 for b in RID}
    fake = RidDaemon(serve=serve)
    return ControllerService(system=fake.system, paths=p), fake


def _no_band_socket(fake):
    hit = [x for x in fake.asked if x in BAND_SOCKETS]
    assert not hit, f"a band socket was asked at L5: {hit}"


# ---- the reads ------------------------------------------------------------------------------------

@pytest.mark.parametrize("band", ["433", "868"])
def test_the_daemon_view_of_a_band_reads_its_radios_socket(tmp_path, band):
    svc, fake = _svc(tmp_path)
    view = svc.daemon_view(band)
    assert view.reachable and view.status.get("ID") == RID[band], view.error
    assert view.stats.get("TXOK") == "0"                                   # GET STATS on the same socket
    assert f"/tmp/loraconf-{RID[band]}.sock" in fake.asked
    _no_band_socket(fake)


@pytest.mark.parametrize("call", ["daemon_channel", "daemon_channel_scan"])
def test_the_channel_reads_of_a_band_reach_its_radios_socket(tmp_path, call):
    svc, fake = _svc(tmp_path)
    assert getattr(svc, call)("433").get("MODE") == "RX"
    assert fake.asked == ["/tmp/loraconf-r1.sock"]


def test_the_socket_monitor_line_of_a_band_is_its_radios(tmp_path):
    svc, fake = _svc(tmp_path)
    line = svc.daemon_socket_line("868")
    assert line.startswith("STATUS ") and "ID=r2" in line.split()
    assert fake.asked == ["/tmp/loraconf-r2.sock"]


# ---- SET and its read-back ------------------------------------------------------------------------

def test_a_live_set_goes_to_the_radios_socket_and_is_confirmed_by_its_read_back(tmp_path):
    svc, fake = _svc(tmp_path)
    res = svc.daemon_set("433", "TXMODE", "MANAGED", apply=True)
    assert res.ok and res.data.get("confirmed") is True, (res.summary, res.details)
    assert ("/tmp/loraconf-r1.sock", b"SET TXMODE=MANAGED\n") in fake.sent
    assert fake.serve["/tmp/loraconf-r1.sock"]["TXMODE"] == "MANAGED"
    assert fake.serve["/tmp/loraconf-r2.sock"]["TXMODE"] == "DIRECT"      # the other radio is untouched
    _no_band_socket(fake)


def test_a_required_tx_mode_is_set_and_read_back_on_the_radios_socket(tmp_path):
    svc, fake = _svc(tmp_path)
    ok, detail = svc._apply_tx_mode("868", "MANAGED")
    assert ok, detail
    assert fake.serve["/tmp/loraconf-r2.sock"]["TXMODE"] == "MANAGED"
    _no_band_socket(fake)


def test_the_high_power_state_reads_the_radios_daemon(tmp_path):
    svc, fake = _svc(tmp_path)
    svc.high_power_state("433")
    assert "/tmp/loraconf-r1.sock" in fake.asked
    _no_band_socket(fake)


# ---- the TX test: GET STATS and the data socket ------------------------------------------------------

def test_the_tx_test_sends_on_the_radios_data_socket_and_counts_on_its_conf_socket(tmp_path, monkeypatch):
    from lhpc.core import lifecycle
    svc, fake = _svc(tmp_path)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: None)
    res = svc._lifecycle().run_daemon_tx_test("433", "test")
    assert res.ok and (res.txok_before, res.txok_after) == (0, 1), res.detail
    assert fake.sent == [("/tmp/lora-r1.sock", b"test")]
    _no_band_socket(fake)


# ---- a stopping client releases the daemon band only while the radio's daemon is reachable ------------

def test_the_release_on_a_client_stop_sees_the_radios_daemon(tmp_path, monkeypatch):
    svc, fake = _svc(tmp_path)
    client = next(s for s in svc.stacks()
                  if s.main != svc.DAEMON_ID and any(svc.DAEMON_ID in (c.depends_on or ()) for c in s.components))
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, sid, bands=None: [])
    daemon_sid, release = svc._daemon_bands_to_release(client, client.id, {"433"})
    assert daemon_sid and release == ["433"]
    _no_band_socket(fake)


# ---- a band with one enabled radio and a disabled one: the enabled radio's socket ------------------------

def test_a_disabled_second_radio_of_the_band_leaves_the_enabled_ones_socket(tmp_path):
    from lhpc.core import radios
    svc, fake = _svc(tmp_path)
    rows = (radios.Radio("r2", "high", "uputronics", "868", ce="ce1"),     # a valid list: r3 takes the free ce0
            radios.Radio("r3", "spare", "uputronics", "868", ce="ce0", enabled=False))
    (tmp_path / "config" / "radios.toml").write_text(radios.render(rows))
    assert radios.load(svc._paths).kind == radios.VALID          # an invalid list would keep the band key
    view = svc.daemon_view("868")
    assert view.reachable and view.status.get("ID") == "r2", view.error
    assert "/tmp/loraconf-r3.sock" not in fake.asked
    _no_band_socket(fake)


# ---- without a hardware list: the 0.13.x band socket ---------------------------------------------------

@pytest.mark.no_default_hardware                  # the list is really absent (conftest's baseline list off)
def test_without_a_hardware_list_the_band_socket_is_read(tmp_path):
    svc, fake = _svc(tmp_path, hardware_list=False)
    view = svc.daemon_view("433")
    assert view.reachable, view.error
    assert fake.asked[0] == "/tmp/loraconf433.sock"


# ---- G2-0.14.0-1 item 1 (W5-6/W5-7), DEC-W384az: the row's dp_* and an instance's set reach its radio's daemon ----

def _svc_dp(tmp_path, r1_dp=(("CADWAIT", "200"),), legacy=("daemon", {"dp_433_CADWAIT": "900"})):
    from lhpc.core import config as cfgmod
    from lhpc.core import radios
    svc, fake = _svc(tmp_path)
    rows = (radios.Radio("r1", "low", "uputronics", "433", ce="ce0", dp=r1_dp),
            radios.Radio("r2", "high", "uputronics", "868", ce="ce1"))
    (tmp_path / "config" / "radios.toml").write_text(radios.render(rows))
    if legacy:
        cfgmod.update_stack_config(svc._paths, legacy[0], legacy[1])
    return svc, fake


def _sets(fake, name):
    return [(p, v.decode().strip()) for p, v in fake.sent if v.decode().startswith(f"SET {name}=")]


def test_apply_sends_the_rows_dp_value_to_its_radio_over_a_conflicting_legacy_value(tmp_path):
    svc, fake = _svc_dp(tmp_path)
    res = svc.apply_daemon_params("daemon", rid="r1")                  # the public Apply
    assert "CADWAIT" in res.data["applied"], (res.summary, res.details)
    assert _sets(fake, "CADWAIT") == [("/tmp/loraconf-r1.sock", "SET CADWAIT=200")]     # the row, r1 only
    _no_band_socket(fake)


def test_the_start_loops_apply_after_ready_sends_the_rows_dp_value(tmp_path):
    # The call _ensure_daemon makes once per READY radio (service_lifecycle_ops: `_apply_stack_daemon_params(start_sid,
    # b, rid)`); a public start of an already-serving daemon is a no-op, so the start loop's own call is driven here.
    svc, fake = _svc_dp(tmp_path)
    lines, _tx_ok, gate = svc._apply_stack_daemon_params("daemon", "433", "r1")
    assert gate == "" and _sets(fake, "CADWAIT") == [("/tmp/loraconf-r1.sock", "SET CADWAIT=200")], lines


def test_w384az_an_instance_start_sends_its_own_set_over_its_radios_row(tmp_path):
    # kiss@r1's own CADWAIT goes to r1 at its start (over r1's row 200); r1's row CADIDLE is kept under it; kiss's
    # old per-band value (dp_433_* in kiss.toml) goes nowhere.
    svc, fake = _svc_dp(tmp_path, r1_dp=(("CADWAIT", "200"), ("CADIDLE", "400")), legacy=("kiss", {"dp_433_CADWAIT": "900"}))
    assert svc.save_daemon_params("kiss", rid="r1", values={"CADWAIT": "300"}).ok
    lines, _tx_ok, gate = svc._apply_stack_daemon_params("kiss", "433", "r1")
    assert gate == "", lines
    assert _sets(fake, "CADWAIT") == [("/tmp/loraconf-r1.sock", "SET CADWAIT=300")]
    assert _sets(fake, "CADIDLE") == [("/tmp/loraconf-r1.sock", "SET CADIDLE=400")]
    _no_band_socket(fake)


def test_without_a_row_value_the_default_is_applied_never_an_old_band_value(tmp_path):
    svc, fake = _svc_dp(tmp_path, r1_dp=())
    svc.apply_daemon_params("daemon", rid="r1")
    assert _sets(fake, "CADWAIT") == [("/tmp/loraconf-r1.sock", "SET CADWAIT=1500")]


def test_the_view_shows_the_rows_dp_value(tmp_path):
    svc, _fake = _svc_dp(tmp_path)
    assert svc._daemon_param_applies("daemon", "r1").get("CADWAIT") == "200"
    assert {r["name"]: r["value"] for r in svc.daemon_params_view("daemon", rid="r1")["rows"]}["CADWAIT"] == "200"
