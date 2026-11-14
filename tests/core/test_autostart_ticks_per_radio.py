"""An optional component's auto-start tick is per radio (Q-09 C). It is saved in the instance's own file
(`<stack>@<rid>.toml`), and that radio's start plan, Settings rows, GPS gate and receiver claim, source locks and restart
read it from there. Another radio of the same stack is unaffected, and a tick left in the old stack-wide file is read by
no radio. A box without a hardware list keeps its one stack-wide file."""
from __future__ import annotations

import tomllib

import pytest
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import gps as gpsmod
from lhpc.core import instances, radio_start, radios, reslock
from lhpc.core.model import RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
READY = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode()
         for r in ("r1", "r2", "r3")}
SERIAL = "loraham-kiss-serial"


@pytest.fixture
def box(tmp_path, monkeypatch):
    def make(rows=ROWS):
        if rows:
            radios_fixture.write(tmp_path, rows)
        (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
        for name, value in (("hardware_configured", lambda self: True), ("binary_behind", lambda self, c: ""),
                            ("is_built", lambda self, c: True), ("_source_present", lambda self, c: True),
                            ("_verify_band_up", lambda self, b: True)):
            monkeypatch.setattr(ControllerService, name, value)
        monkeypatch.setattr(radio_start, "verify_identity", lambda svc, rid: "")
        return ControllerService(system=FakeSystem(unix_replies=READY).system, paths=Paths(runtime_root=tmp_path))
    return make


def _inst(svc, key):
    return instances.parse_target(svc, key, "start")[1]


def _tick(svc, sid, rid, comp, **values):
    res = svc.save_config_bundle(sid, values={f"autostart_{comp}": "on", **values}, inst=_inst(svc, f"{sid}@{rid}"))
    assert res.ok, (res.summary, res.details)


def _plan(svc, sid, rid=""):
    res = svc.run_action("start", sid, inst=_inst(svc, f"{sid}@{rid}") if rid else None, apply=False)
    return res, "\n".join(res.details)


def _stored(tmp_path, name):
    p = tmp_path / "config" / "stacks" / name
    return tomllib.loads(p.read_text()) if p.exists() else {}


def test_a_tick_on_one_radio_starts_that_radios_component_only(box):
    svc = box()
    _tick(svc, "kiss", "r1", SERIAL)
    assert f"[start] {SERIAL}" in _plan(svc, "kiss", "r1")[1]
    assert f"[start] {SERIAL}" not in _plan(svc, "kiss", "r2")[1]


def test_the_tick_is_saved_in_the_radios_own_file(box, tmp_path):
    svc = box()
    _tick(svc, "kiss", "r1", SERIAL)
    _tick(svc, "meshcore", "r3", "meshcore-webui")
    assert _stored(tmp_path, "kiss@r1.toml").get(f"autostart_{SERIAL}") == "on"
    assert _stored(tmp_path, "meshcore@r3.toml").get("autostart_meshcore-webui") == "on"
    for other, key in (("kiss@r2.toml", SERIAL), ("kiss.toml", SERIAL), ("meshcore@r2.toml", "meshcore-webui"),
                       ("meshcore.toml", "meshcore-webui")):
        assert f"autostart_{key}" not in _stored(tmp_path, other), other


def test_a_tick_left_in_the_stack_wide_file_is_read_by_no_radio(box, tmp_path):
    svc = box()
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "kiss", {f"autostart_{SERIAL}": "on"})
    for rid in ("r1", "r2"):
        assert f"[start] {SERIAL}" not in _plan(svc, "kiss", rid)[1], rid


def test_the_settings_rows_show_a_radios_own_tick(box):
    svc = box()
    _tick(svc, "kiss", "r1", SERIAL)

    def ticked(rid):
        return next(o for o in svc.config_view("kiss", rid=rid)["optional"] if o["id"] == SERIAL)["autostart"]
    assert (ticked("r1"), ticked("r2")) == (True, False)


@pytest.mark.no_default_hardware                     # the list is really absent (conftest's baseline list off)
def test_a_box_without_a_hardware_list_keeps_its_one_stack_wide_file(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("kiss", values={f"autostart_{SERIAL}": "on"}).ok
    assert _stored(tmp_path, "kiss.toml").get(f"autostart_{SERIAL}") == "on"
    assert next(o for o in svc.config_view("kiss")["optional"] if o["id"] == SERIAL)["autostart"] is True


def _gps_on(svc, monkeypatch, device):
    for rid in ("r1", "r2"):
        assert svc.save_config_bundle("reticulum", values={"use_gps": "on"}, inst=_inst(svc, f"reticulum@{rid}")).ok
    cfgmod.save_gps(svc._paths, source="nmea", device=device, nmea_baud="9600")
    svc._invalidate_config()
    monkeypatch.setattr(gpsmod, "gpsd_owns_device", lambda d, h, po, timeout=3.0: (False, "free"))


def test_only_the_radio_whose_sideband_is_ticked_is_gated_on_gps(box, monkeypatch):
    svc = box()
    _gps_on(svc, monkeypatch, "/dev/lhpc-test-missing-receiver")       # a plan that cannot resolve: refused
    _tick(svc, "reticulum", "r2", "sideband")
    assert svc.gps_block("reticulum", _inst(svc, "reticulum@r2"))[0]
    assert svc.gps_block("reticulum", _inst(svc, "reticulum@r1")) == ("", [])


def _start_lock_keys(svc, key):
    seen, orig = [], svc._lifecycle_lock_keys

    def spy(*a, **k):
        seen.append(orig(*a, **k))
        raise reslock.ResourceBusy("probe", {"operation": "probe"})
    svc._lifecycle_lock_keys = spy
    res = svc.start(key.partition("@")[0], inst=_inst(svc, key), apply=True)
    svc._lifecycle_lock_keys = orig
    assert not res.ok and "busy" in res.summary, res.summary
    return seen[0]


def test_a_start_claims_the_receiver_and_locks_the_sources_of_its_own_radios_ticks(box, monkeypatch):
    svc = box()
    _gps_on(svc, monkeypatch, "/dev/null")
    _tick(svc, "reticulum", "r2", "sideband")
    claim = "claim." + svc._gps_device_claim("reticulum", _inst(svc, "reticulum@r2"))
    source = reslock.source_lock_key("src/sideband")
    r2, r1 = _start_lock_keys(svc, "reticulum@r2"), _start_lock_keys(svc, "reticulum@r1")
    assert claim != "claim." and claim in r2 and source in r2, r2
    assert claim not in r1 and source not in r1, r1


def test_a_restart_brings_back_by_name_only_what_its_radio_does_not_tick(box, monkeypatch):
    svc = box()
    _tick(svc, "kiss", "r1", SERIAL)
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if cid in ("loraham-kiss-tnc", SERIAL) else RunState.STOPPED
    monkeypatch.setattr(svc, "build_snapshot", lambda fresh=False: snap)

    def restarted(rid):
        return svc.run_action("restart", "kiss", inst=_inst(svc, f"kiss@{rid}"), apply=False).data.get("optional_restarted")
    assert (restarted("r1"), restarted("r2")) == ([], [SERIAL])
