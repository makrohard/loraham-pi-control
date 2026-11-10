"""The PA over-current limit (LoRaHAM daemon 1.3.0 `--ocp MA`): a radio's `ocp_ma` in its row of the hardware list
(`auto` or mA, K2: never a per-band value), what `auto` means (140 mA with the radio's +20 dBm grant, else 100), the
chip-legal values `set_ocp` accepts, the argv every daemon start carries, and the start that refuses a row value the
radio's chip cannot represent. Backed by FakeSystem; the spawn itself is captured."""

import pytest
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radios
from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


# stage L5: the hardware list that matches each 0.13.x setup (the band's radio of that board)
_LIST = {"uputronics": (radios.Radio("r1", "low", "uputronics", "433", ce="ce0"),
                        radios.Radio("r2", "high", "uputronics", "868", ce="ce1")),
         "waveshare-433": (radios.Radio("r1", "low", "waveshare-sx1262", "433"),)}


def _svc(tmp_path, setup="uputronics", stored=None):
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, setup)
    radios_fixture.write(tmp_path, _LIST[setup])
    if stored is not None:                       # a 0.13.x per-band value, written by hand: never read (K2)
        path = cfgmod._stack_config_path(p, "daemon")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stored)
    return ControllerService(system=FakeSystem().system, paths=p)


def _spawned(svc, monkeypatch, band="433"):
    """Start the daemon through the real per-band spawn path; return (lines, the params `band`'s
    spawn was given, or None when it was never reached). Every band is spawned (I5)."""
    seen = {}

    def start(self, stack, comp, params, band=None, **_kw):
        seen[band] = dict(params)
        return StartLaunch(False, "", "captured (no process)")
    monkeypatch.setattr(Lifecycle, "start", start)
    monkeypatch.setattr(ControllerService, "is_built", lambda self, c: True)
    monkeypatch.setattr(ControllerService, "_source_present", lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    res = svc.start("daemon", apply=True)
    return "\n".join((res.summary, *res.details)), seen.get(band)


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


# --- T1/T2/T3: what auto and an operator value mean (through the start's argv) --------------------

def _ocp(svc, monkeypatch, band="433"):
    _text, params = _spawned(svc, monkeypatch, band)
    return params and params["ocp"]


def test_auto_is_100_without_high_power(tmp_path, monkeypatch):
    assert _ocp(_svc(tmp_path), monkeypatch) == "100"


def test_auto_is_140_with_the_radios_high_power(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    assert svc.set_high_power("r1", "on").ok
    assert _ocp(svc, monkeypatch, "433") == "140" and _ocp(svc, monkeypatch, "868") == "100"


@pytest.mark.parametrize("hp", ["off", "on"])
def test_an_operator_value_is_used_either_way(tmp_path, monkeypatch, hp):
    svc = _svc(tmp_path)
    assert svc.set_high_power("r1", hp).ok
    assert svc.set_ocp("r1", "120").ok
    assert _ocp(svc, monkeypatch) == "120"


@pytest.mark.parametrize("hp,ocp,want", [("off", None, "100"), ("on", None, "140"),
                                         ("on", "100", "100"), ("off", "120", "120")])
def test_every_daemon_start_carries_its_ocp(tmp_path, monkeypatch, hp, ocp, want):
    svc = _svc(tmp_path)
    assert svc.set_high_power("r1", hp).ok
    if ocp:
        assert svc.set_ocp("r1", ocp).ok
    assert _ocp(svc, monkeypatch) == want
    comp = svc.stack("daemon").component("loraham-daemon")
    i = comp.run_argv.index("--ocp")
    assert comp.run_argv[i + 1] == "{param:ocp}"           # the injected value, always present


def test_k2_an_old_per_band_value_is_not_read(tmp_path, monkeypatch):
    # K2 (13.8 SAFETY): a 0.13.x `ocp_433` / `hipower_433` in the daemon's config is no longer read: r1 starts with
    # LHPC's auto (100 mA) and without --high-power. RED before K2: the carried 120 and the carried grant.
    svc = _svc(tmp_path, stored='ocp_433 = "120"\nhipower_433 = "on"\n')
    _text, params = _spawned(svc, monkeypatch, "433")
    assert (params["ocp"], params["hipower"]) == ("100", "off")


# --- T4: what set_ocp accepts ---------------------------------------------------------------------

@pytest.mark.parametrize("setup,value,ok", [
    ("waveshare-433", "150", False),   # SX1262 tops out at 140
    ("waveshare-433", "0", False),     # off exists on SX127x only
    ("waveshare-433", "140", True),
    ("uputronics", "125", False),      # SX127x: steps of 10 above 120
    ("uputronics", "0", True),         # SX127x: protection off
    ("uputronics", "140", True),
    ("uputronics", "auto", True),
])
def test_set_ocp_takes_only_what_the_radios_chip_represents(tmp_path, setup, value, ok):
    svc = _svc(tmp_path, setup=setup)
    r = svc.set_ocp("r1", value)
    assert r.ok is ok, r.summary
    if not ok:
        assert dc.OCP_LEGAL_TEXT[svc.radio_chip_family("r1")] in r.summary


@pytest.mark.parametrize("value", ["", "abc", "-5", "100.0", "0100", " 100", "AUTO", "²", "١٠٠", "+100",
                                   pytest.param("1" * 5000, id="5000-digits")])
def test_set_ocp_refuses_junk(tmp_path, value):
    svc = _svc(tmp_path)
    assert not svc.set_ocp("r1", value).ok
    assert radios.load(svc._paths).radios[0].ocp_ma is None          # nothing saved


def test_set_ocp_refuses_an_unknown_radio(tmp_path):
    assert not _svc(tmp_path).set_ocp("r9", "100").ok


# --- T5: a row value the chip cannot represent refuses that radio's start ----------------------------

@pytest.mark.parametrize("raw", ['"150"', '"abc"', '"0100"', '"²"', '"١٠٠"', "150", pytest.param('"' + "1" * 5000 + '"', id="5000-digits")])
def test_an_illegal_row_value_refuses_the_start_and_sends_no_ocp(tmp_path, monkeypatch, raw):
    svc = _svc(tmp_path, setup="waveshare-433")
    f = tmp_path / "config" / "radios.toml"
    f.write_text(f.read_text().replace('band = "433"', f'band = "433"\nocp_ma = {raw}', 1))   # a hand edit
    text, params = _spawned(svc, monkeypatch)
    assert params is None                                 # never spawned, never clamped
    assert "ocp_ma" in text


# --- T6: saving restarts nothing ---------------------------------------------------------------------

def test_set_ocp_saves_and_restarts_nothing(tmp_path):
    p = Paths(runtime_root=tmp_path)
    radios_fixture.write(tmp_path, _LIST["uputronics"])
    fs = FakeSystem()
    svc = ControllerService(system=fs.system, paths=p)
    r = svc.set_ocp("r2", "100")
    assert r.ok and "restart" in r.summary
    assert [x.ocp_ma for x in radios.load(svc._paths).radios] == [None, 100]
    assert fs.sent == []                                  # nothing was sent to any daemon (G1-C59 N)
