"""The +20 dBm opt-in (LoRaHAM daemon 1.2.0 `--high-power`): the saved per-band switch, the live
POWER predicate on the RUNNING daemon's report, the argv the switch produces, the start gate and
what the console shows. Backed by FakeSystem: the daemon's STATUS line is seeded per band."""

import pytest

from lhpc.core import commands
from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import daemon_params as dp
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _status(family="SX127x", highpower="0"):
    """A daemon 1.2.0 STATUS line with the two additive witnesses at the end."""
    return (f"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
            f"HIGHPOWER={highpower} CHIPFAMILY={family}\n").encode()


_OLD_STATUS = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1\n"


def _svc(tmp_path, setup="uputronics", status=None):
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, setup)
    replies = {dc.conf_socket(b): status for b in ("433", "868")} if status is not None else {}
    fs = FakeSystem(unix_replies=replies)
    return ControllerService(system=fs.system, paths=p), fs


# --- validate_set: the saved gate ------------------------------------------------------------

@pytest.mark.parametrize("value,high_power,ok", [
    ("20", False, False), ("20", True, True),
    ("18", False, False), ("18", True, False),
    ("19", False, False), ("19", True, False),
    ("0", True, False), ("1", True, False),
    ("17", True, True), ("2", True, True), ("21", True, False),
])
def test_sx127x_twenty_needs_the_saved_switch(value, high_power, ok):
    assert (dc.validate_set("POWER", value, "sx127x", high_power) is None) is ok


def test_refusal_of_twenty_names_the_switch():
    err = dc.validate_set("POWER", "20", "sx127x")
    assert err and "high-power" in err and "[2, 17]" in err


@pytest.mark.parametrize("value", ["0", "20"])
@pytest.mark.parametrize("high_power", [False, True])
def test_sx1262_ignores_the_switch(value, high_power):
    assert dc.validate_set("POWER", value, "sx1262", high_power) is None


def test_no_family_stays_the_union_utility():
    # `apply_set` re-validates with NO family; its default high_power=False must never refuse
    # an already-authorised 20 — the union admits it, the caller predicate authorised it.
    assert dc.validate_set("POWER", "20") is None
    assert dc.validate_set("POWER", "20", "", False) is None
    assert dc.validate_set("POWER", "21", "", True) is not None


def test_contiguous_range_and_panel_bounds():
    assert dc.int_range("POWER", "sx127x") == (2, 17)          # unchanged: the extra is disjoint
    assert dp.numeric_range("POWER", "sx127x") == ("2", "17")
    assert dp.numeric_range("POWER", "sx127x", True) == ("2", "20")
    assert dp.numeric_range("POWER", "sx1262", True) == ("0", "20")
    assert dp.numeric_range("POWER", "", True) == ("0", "20")


# --- the running daemon's report ---------------------------------------------------------------

@pytest.mark.parametrize("raw,fam", [("SX127x", "sx127x"), ("sx127X", "sx127x"), ("SX1262", "sx1262"),
                                     ("banana", ""), ("", "")])
def test_chip_family_from_status_normalises_or_says_unknown(raw, fam):
    assert dc.chip_family_from_status({"CHIPFAMILY": raw} if raw else {}) == fam


@pytest.mark.parametrize("raw,val", [("1", True), ("0", False), ("yes", None), ("", None)])
def test_high_power_from_status_is_tri_state(raw, val):
    assert dc.high_power_from_status({"HIGHPOWER": raw} if raw else {}) is val


# --- the live predicate: the RUNNING family decides every live POWER value ---------------------

@pytest.mark.parametrize("value", [0, 1, 17, 18, 19, 20])
@pytest.mark.parametrize("live", [True, False, None])
@pytest.mark.parametrize("saved", [True, False])
def test_live_predicate_on_sx1262_is_the_full_range(value, live, saved):
    assert dc.live_power_error(str(value), "sx1262", live, saved) is None


@pytest.mark.parametrize("value", [2, 10, 17])
@pytest.mark.parametrize("family", ["sx127x", ""])
@pytest.mark.parametrize("live", [True, False, None])
@pytest.mark.parametrize("saved", [True, False])
def test_live_predicate_admits_the_intersection_everywhere(value, family, live, saved):
    assert dc.live_power_error(str(value), family, live, saved) is None


@pytest.mark.parametrize("value", [0, 1, 18, 19])
@pytest.mark.parametrize("family", ["sx127x", ""])
@pytest.mark.parametrize("live", [True, False, None])
@pytest.mark.parametrize("saved", [True, False])
def test_live_predicate_refuses_the_rfo_and_api_holes(value, family, live, saved):
    assert dc.live_power_error(str(value), family, live, saved) is not None


@pytest.mark.parametrize("live,saved,ok,needle", [
    (True, True, True, ""),
    (True, False, False, "saved off"),
    (False, True, False, "restart"),
    (False, False, False, "not enabled"),
    (None, True, False, "older daemon"),
])
def test_live_twenty_on_sx127x_needs_running_and_saved(live, saved, ok, needle):
    err = dc.live_power_error("20", "sx127x", live, saved)
    assert (err is None) is ok
    if needle:
        assert needle in err


@pytest.mark.parametrize("live", [True, False, None])
@pytest.mark.parametrize("saved", [True, False])
def test_live_twenty_with_unknown_family_is_refused_not_guessed(live, saved):
    err = dc.live_power_error("20", "", live, saved)
    assert err and "chip family" in err


# --- the saved switch -------------------------------------------------------------------------

def test_saved_switch_is_the_canonical_on_only(tmp_path):
    svc, _ = _svc(tmp_path)
    assert not svc.high_power_for_band("433") and not svc.high_power_for_band("868")
    assert svc.save_config_bundle("daemon", values={"hipower_433": "on"}).ok
    assert svc.high_power_for_band("433") and not svc.high_power_for_band("868")
    assert svc.save_config_bundle("daemon", values={"hipower_433": "off"}).ok
    assert not svc.high_power_for_band("433")
    assert not svc.high_power_for_band("bogus")


@pytest.mark.parametrize("bad", ["banana", "False", "1", "yes", "ON", " on "])
def test_saved_switch_refuses_everything_but_off_and_on(tmp_path, bad):
    # A flag-kind param would keep any string and the emitter would read it as ON; the stored
    # switch is a strict enum precisely so this cannot happen.
    svc, _ = _svc(tmp_path)
    assert not svc.save_config_bundle("daemon", values={"hipower_433": bad}).ok
    assert not svc.set_high_power("433", bad).ok
    assert not svc.high_power_for_band("433")


@pytest.mark.parametrize("raw", ["ON", "On", " on ", "true", "1", "banana", "yes", "off"])
def test_hand_edited_stored_value_never_grants_the_permission(tmp_path, raw):
    # The strict enum refuses junk at save time; this writes the stack config file DIRECTLY, the
    # way a hand edit would, and proves the read/launch path is literal too: only exactly "on"
    # is the permission, and the injected flag emits nothing for anything else. The daemon is
    # skipped by the generic saved-launch refusal, so this compare is its only gate (audit P1).
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    path = cfgmod._stack_config_path(p, "daemon")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'hipower_433 = "{raw}"\n')
    svc = ControllerService(system=FakeSystem().system, paths=p)
    assert svc.stack_config("daemon").get("hipower_433") == raw          # stored verbatim
    assert svc.high_power_for_band("433") is False
    comp = svc.stack("daemon").component("loraham-daemon")
    params = {"radio": "433", "hw": "uputronics-ce0", "txmode": "managed", "cadmon": "off",
              "cadrssi": "-90", "rf_log": "on",
              "hipower": "on" if svc.high_power_for_band("433") else "off"}   # the spawn mapping
    argv = commands.expand_argv(comp.run_argv, comp, params, svc.config().operator, "/rt", "/src", "433")
    assert "--high-power" not in argv
    assert not svc.set_high_power("433", raw if raw != "off" else "ON ").ok  # literal at the boundary too


def test_exactly_on_in_the_file_is_the_permission(tmp_path):
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    path = cfgmod._stack_config_path(p, "daemon")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('hipower_433 = "on"\n')
    svc = ControllerService(system=FakeSystem().system, paths=p)
    assert svc.high_power_for_band("433") is True and svc.high_power_for_band("868") is False


def test_set_high_power_saves_and_restarts_nothing(tmp_path):
    svc, fs = _svc(tmp_path)
    r = svc.set_high_power("433", "on")
    assert r.ok and "restart" in r.summary and "warranty" in r.summary.lower()
    assert svc.high_power_for_band("433")
    assert not svc.set_high_power("999", "on").ok
    assert fs.sent == []                                  # nothing was sent to any daemon


# --- the argv ---------------------------------------------------------------------------------

def test_argv_carries_the_bare_flag_only_when_on(tmp_path):
    svc, _ = _svc(tmp_path)
    comp = svc.stack("daemon").component("loraham-daemon")
    names = {p.name for p in comp.run_params}
    assert {"hipower", "hipower_433", "hipower_868"} <= names
    op = svc.config().operator
    base = {"radio": "433", "hw": "uputronics-ce0", "txmode": "managed", "cadmon": "off",
            "cadrssi": "-90", "rf_log": "on"}
    off = commands.expand_argv(comp.run_argv, comp, {**base, "hipower": "off"}, op, "/rt", "/src", "433")
    assert "--high-power" not in off
    on = commands.expand_argv(comp.run_argv, comp, {**base, "hipower": "on"}, op, "/rt", "/src", "433")
    assert on.count("--high-power") == 1
    assert on[on.index("--high-power") + 1].startswith("--")        # bare: no value token follows


# --- live SET through the service: the running daemon decides --------------------------------

def test_live_set_twenty_needs_running_permission_and_saved_switch(tmp_path):
    svc, _ = _svc(tmp_path, status=_status("SX127x", "1"))
    assert not svc.daemon_set("433", "POWER", "20").ok            # saved off
    svc.set_high_power("433", "on")
    assert svc.daemon_set("433", "POWER", "20").ok                # running on + saved on
    assert not svc.daemon_set("433", "POWER", "18").ok


def test_live_set_twenty_refused_while_restart_is_pending(tmp_path):
    svc, _ = _svc(tmp_path, status=_status("SX127x", "0"))
    svc.set_high_power("433", "on")
    r = svc.daemon_set("433", "POWER", "20")
    assert not r.ok and "restart" in r.summary


def test_live_set_uses_the_running_family_not_the_saved_board(tmp_path):
    # Saved SX127x (uputronics), running SX1262: a live POWER=0 is legitimate and must pass.
    svc, _ = _svc(tmp_path, status=_status("SX1262", "0"))
    assert svc.chip_family_for_band("433") == "sx127x"
    assert svc.daemon_set("433", "POWER", "0").ok
    assert svc.daemon_set("433", "POWER", "20").ok
    # Saved SX1262 (waveshare), running SX127x: a live POWER=0 must be refused before it is sent.
    svc2, fs2 = _svc(tmp_path / "b", "waveshare-433", status=_status("SX127x", "0"))
    assert svc2.chip_family_for_band("433") == "sx1262"
    assert not svc2.daemon_set("433", "POWER", "0", apply=True).ok
    assert fs2.sent == []
    assert svc2.daemon_set("433", "POWER", "17").ok


def test_live_set_against_an_older_daemon_admits_only_the_intersection(tmp_path):
    svc, _ = _svc(tmp_path, status=_OLD_STATUS)
    assert svc.daemon_set("433", "POWER", "17").ok
    assert not svc.daemon_set("433", "POWER", "0").ok
    r = svc.daemon_set("433", "POWER", "20")
    assert not r.ok and "chip family" in r.summary


# --- the start gate ---------------------------------------------------------------------------

def _chat_wants_twenty(svc):
    assert svc.set_high_power("433", "on").ok
    assert svc.save_daemon_params("chat", "433", {"POWER": "20"}).ok
    assert svc._daemon_param_applies("chat", "433").get("POWER") == "20"


def test_saving_twenty_into_a_profile_needs_the_saved_switch(tmp_path):
    svc, _ = _svc(tmp_path)
    assert not svc.save_daemon_params("chat", "433", {"POWER": "20"}).ok
    assert svc.set_high_power("433", "on").ok
    assert svc.save_daemon_params("chat", "433", {"POWER": "20"}).ok
    assert not svc.save_daemon_params("chat", "433", {"POWER": "19"}).ok


def test_start_preflight_gates_on_an_unavailable_permission(tmp_path):
    svc, fs = _svc(tmp_path, status=_status("SX127x", "0"))
    _chat_wants_twenty(svc)
    lines, ok = svc._apply_stack_daemon_params("chat", "433")
    assert not ok
    assert any("[fail]" in ln and "POWER=20" in ln and "HIGHPOWER=0" in ln for ln in lines)
    assert fs.sent == []                                  # refused BEFORE any RF setter


def test_start_preflight_passes_with_the_running_permission(tmp_path):
    svc, fs = _svc(tmp_path, status=_status("SX127x", "1"))
    _chat_wants_twenty(svc)
    lines, ok = svc._apply_stack_daemon_params("chat", "433")
    assert ok, lines
    assert any(b"POWER=20" in payload for _p, payload in fs.sent)


def test_saved_off_after_saving_twenty_filters_it_but_revokes_nothing(tmp_path):
    svc, _ = _svc(tmp_path, status=_status("SX127x", "1"))
    _chat_wants_twenty(svc)
    assert svc.set_high_power("433", "off").ok
    assert "POWER" not in svc._daemon_param_overrides("chat", "433")     # filtered, not deleted
    st = svc.high_power_state("433")
    assert st["live"] is True and st["warn"] and "saved off" in st["mismatch"]


# --- what the console shows -------------------------------------------------------------------

@pytest.mark.parametrize("setup,family,hp,saved,warn,needle", [
    ("uputronics", "SX127x", "1", True, True, ""),
    ("uputronics", "SX127x", "1", False, True, "saved off"),
    ("uputronics", "SX127x", "0", True, False, "restart"),
    ("waveshare-433", "SX1262", "1", True, False, ""),       # the flag is inert: no banner
    ("waveshare-433", "SX1262", "1", False, False, ""),
    ("uputronics", "SX1262", "1", False, False, "running daemon is sx1262"),  # saved/running differ
    ("waveshare-433", "SX127x", "1", False, True, "running daemon is sx127x"),  # banner keys on RUNNING; the family line outranks the switch line
])
def test_high_power_state_keys_on_the_running_daemon(tmp_path, setup, family, hp, saved, warn,
                                                     needle):
    svc, _ = _svc(tmp_path, setup, status=_status(family, hp))
    if saved:
        svc.set_high_power("433", "on")
    st = svc.high_power_state("433")
    assert st["warn"] is warn
    assert st["family"] == family.lower()
    assert (needle in st["mismatch"]) if needle else st["mismatch"] == ""


def test_high_power_state_old_daemon_and_unreachable(tmp_path):
    svc, _ = _svc(tmp_path, status=_OLD_STATUS)
    svc.set_high_power("433", "on")
    st = svc.high_power_state("433")
    assert st["live"] is None and st["family"] == "" and not st["warn"]
    assert "older daemon" in st["mismatch"]
    svc2, _ = _svc(tmp_path / "u")
    st2 = svc2.high_power_state("433")
    assert not st2["reachable"] and st2["live"] is None and not st2["warn"] and st2["mismatch"] == ""


def test_dashboard_cards_carry_the_witnesses(tmp_path):
    svc, _ = _svc(tmp_path, status=_status("SX127x", "1"))
    cards = {c["band"]: c for c in svc.radio_overview()}
    assert cards["433"]["daemon"]["high_power"]["warn"]
    svc2, _ = _svc(tmp_path / "w", "waveshare-433", status=_status("SX1262", "1"))
    cards2 = {c["band"]: c for c in svc2.radio_overview()}
    assert not cards2["433"]["daemon"]["high_power"]["warn"]
