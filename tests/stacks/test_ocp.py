"""The PA over-current limit (LoRaHAM daemon 1.3.0 `--ocp MA`): the saved per-band `ocp_<band>`
(`auto` or mA), what `auto` means (140 mA with the band's high power, else 100), the chip-legal
values `set_ocp` accepts, the argv every daemon start carries, and the start that refuses a stored
value the band's chip cannot represent. Backed by FakeSystem; the spawn itself is captured."""

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, setup="uputronics", stored=None):
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, setup)
    if stored is not None:                       # a value written by hand, as an operator could
        path = cfgmod._stack_config_path(p, "daemon")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stored)
    return ControllerService(system=FakeSystem().system, paths=p)


def _spawned(svc, monkeypatch, band="433"):
    """Start the daemon through the real per-band spawn path; return (lines, the params the
    spawn was given, or None when it was never reached)."""
    seen = {}

    def start(self, stack, comp, params, band=None, **_kw):
        seen["params"] = dict(params)
        return StartLaunch(False, "", "captured (no process)")
    monkeypatch.setattr(Lifecycle, "start", start)
    monkeypatch.setattr(ControllerService, "is_built", lambda self, c: True)
    monkeypatch.setattr(ControllerService, "_source_present", lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    res = svc.start("daemon", apply=True)
    return "\n".join((res.summary, *res.details)), seen.get("params")


# --- the rule: the daemon's own representable sets ----------------------------------------------

@pytest.mark.parametrize("ma,fam,ok", [
    (0, "sx127x", True), (45, "sx127x", True), (120, "sx127x", True), (125, "sx127x", False),
    (130, "sx127x", True), (135, "sx127x", False), (240, "sx127x", True), (40, "sx127x", False),
    (5, "sx1262", True), (140, "sx1262", True), (125, "sx1262", True), (0, "sx1262", False),
    (145, "sx1262", False), (150, "sx1262", False), (102, "sx1262", False),
])
def test_ocp_valid_is_the_daemons_rule(ma, fam, ok):
    assert dc.ocp_valid(ma, fam) is ok


def test_an_unknown_family_admits_either_chips_values():
    assert dc.ocp_valid(0, "") and dc.ocp_valid(125, "") and dc.ocp_valid(240, "")
    assert not dc.ocp_valid(102, "") and not dc.ocp_valid(250, "")


# --- T1/T2/T3: what auto and an operator value mean ---------------------------------------------

def test_auto_is_100_without_high_power(tmp_path):
    assert _svc(tmp_path).ocp_for_band("433") == 100


def test_auto_is_140_with_the_bands_high_power(tmp_path):
    svc = _svc(tmp_path)
    assert svc.set_high_power("433", "on").ok
    assert svc.ocp_for_band("433") == 140 and svc.ocp_for_band("868") == 100


@pytest.mark.parametrize("hp", ["off", "on"])
def test_an_operator_value_is_used_either_way(tmp_path, hp):
    svc = _svc(tmp_path)
    assert svc.set_high_power("433", hp).ok
    assert svc.set_ocp("433", "120").ok
    assert svc.ocp_for_band("433") == 120


# --- the argv every start carries -----------------------------------------------------------------

@pytest.mark.parametrize("hp,ocp,want", [("off", None, "100"), ("on", None, "140"),
                                         ("on", "100", "100"), ("off", "120", "120")])
def test_every_daemon_start_carries_its_ocp(tmp_path, monkeypatch, hp, ocp, want):
    svc = _svc(tmp_path)
    assert svc.set_high_power("433", hp).ok
    if ocp:
        assert svc.set_ocp("433", ocp).ok
    _text, params = _spawned(svc, monkeypatch)
    assert params is not None and params["ocp"] == want
    comp = svc.stack("daemon").component("loraham-daemon")
    i = comp.run_argv.index("--ocp")
    assert comp.run_argv[i + 1] == "{param:ocp}"           # the injected value, always present


# --- T4: what set_ocp accepts ---------------------------------------------------------------------

@pytest.mark.parametrize("setup,band,value,ok", [
    ("waveshare-433", "433", "150", False),   # SX1262 tops out at 140
    ("waveshare-433", "433", "0", False),     # off exists on SX127x only
    ("waveshare-433", "433", "140", True),
    ("uputronics", "433", "125", False),      # SX127x: steps of 10 above 120
    ("uputronics", "433", "0", True),         # SX127x: protection off
    ("uputronics", "433", "140", True),
    ("uputronics", "433", "auto", True),
])
def test_set_ocp_takes_only_what_the_bands_chip_represents(tmp_path, setup, band, value, ok):
    svc = _svc(tmp_path, setup=setup)
    r = svc.set_ocp(band, value)
    assert r.ok is ok, r.summary
    if not ok:
        assert dc.OCP_LEGAL_TEXT[svc.chip_family_for_band(band)] in r.summary


@pytest.mark.parametrize("value", ["", "abc", "-5", "100.0", "0100", " 100", "AUTO"])
def test_set_ocp_refuses_junk(tmp_path, value):
    svc = _svc(tmp_path)
    assert not svc.set_ocp("433", value).ok
    assert svc.ocp_for_band("433") == 100                 # nothing saved


def test_set_ocp_refuses_an_unknown_band(tmp_path):
    assert not _svc(tmp_path).set_ocp("999", "100").ok


# --- T5: a stored value the chip cannot represent refuses that band's start -------------------------

@pytest.mark.parametrize("raw", ["150", "abc", "0100"])
def test_an_illegal_stored_value_refuses_the_start_and_sends_no_ocp(tmp_path, monkeypatch, raw):
    svc = _svc(tmp_path, setup="waveshare-433", stored=f'ocp_433 = "{raw}"\n')
    assert svc.ocp_for_band("433") is None
    text, params = _spawned(svc, monkeypatch)
    assert params is None                                 # never spawned, never clamped
    assert f"[BLOCKED] daemon on 433: ocp_433 = {raw!r}" in text
    assert "lhpc hardware --ocp 433 auto|MA" in text


# --- T6: saving restarts nothing and marks the restart -----------------------------------------------

def test_set_ocp_saves_and_restarts_nothing(tmp_path):
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    fs = FakeSystem()
    svc = ControllerService(system=fs.system, paths=p)
    r = svc.set_ocp("868", "100")
    assert r.ok and "restart" in r.summary and "(100 mA)" in r.summary
    assert svc.stack_config("daemon").get("ocp_868") == "100"
    assert fs.sent == []                                  # nothing was sent to any daemon
