"""The +20 dBm opt-in (LoRaHAM daemon 1.2.0 `--high-power`): the saved per-band switch, the live
POWER predicate on the RUNNING daemon's report, the argv the switch produces, the start gate and
what the console shows. Backed by FakeSystem: the daemon's STATUS line is seeded per band."""

import pytest

from lhpc.core import commands
from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import daemon_params as dp
from lhpc.core import radios
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
    from lhpc.core import radio_start
    assert radio_start.setup_radios(p, setup)[0]        # S2: the list `lhpc hardware <setup>` writes decides the board
    cfgmod.save_hardware_setup(p, setup)
    replies = {dc.conf_socket(r): status for r in ("r1", "r2")} if status is not None else {}   # the baseline list
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


# --- the grant: the radio's row, on the band it was given on (W311, K2) ----------------------------

def _granted(svc, rid, band):
    return svc.high_power_state(band, rid=rid)["saved"]


def test_the_grant_is_the_rows_and_only_that_radios(tmp_path):
    svc, _ = _svc(tmp_path)
    assert not _granted(svc, "r1", "433") and not _granted(svc, "r2", "868")
    assert svc.set_high_power("r1", "on").ok
    assert _granted(svc, "r1", "433") and not _granted(svc, "r2", "868")
    row = radios.load(svc._paths).radios[0]
    assert (row.high_power, row.high_power_band) == (True, "433")       # the band it was given on
    assert svc.set_high_power("r1", "off").ok
    assert not _granted(svc, "r1", "433")
    assert not svc.set_high_power("r9", "on").ok


@pytest.mark.parametrize("bad", ["banana", "False", "1", "yes", "ON", " on "])
def test_set_high_power_refuses_everything_but_off_and_on(tmp_path, bad):
    svc, _ = _svc(tmp_path)
    assert not svc.set_high_power("r1", bad).ok
    assert radios.load(svc._paths).radios[0].high_power is None          # nothing written


@pytest.mark.parametrize("raw", ['"on"', '"ON"', '"true"', "1", '"yes"'])
def test_a_hand_edited_row_value_never_grants_the_permission(tmp_path, raw):
    # The row's high_power is a TOML boolean; anything else a hand edit writes makes the list invalid, so no radio
    # is granted (and no daemon starts on an invalid list).
    svc, _ = _svc(tmp_path)
    f = tmp_path / "config" / "radios.toml"
    f.write_text(f.read_text().replace('band = "433"', f'band = "433"\nhigh_power = {raw}\nhigh_power_band = "433"', 1))
    assert radios.load(svc._paths).kind == radios.INVALID
    assert not _granted(svc, "r1", "433")


def test_k2_a_grant_given_on_another_band_is_denied_and_named(tmp_path):
    # Handler DEC (replaces the band-edit guard): r1's grant was given on 433 and its band was moved to 868 by hand.
    # RED before K2: the row's high_power alone granted it on 868.
    from lhpc.core import radio_start
    svc, _ = _svc(tmp_path)
    assert svc.set_high_power("r1", "on").ok
    f = tmp_path / "config" / "radios.toml"
    f.write_text(f.read_text().replace('band = "433"', 'band = "868"', 1))      # the hand edit of the band
    params, refusal, notes = radio_start.start_plan(svc, "r1")
    assert params["hipower"] == "off" and not refusal
    assert any("r1: the +20 dBm permission was given on 433 MHz, the radio is on 868 MHz now: denied" in n
               for n in notes)
    assert not _granted(svc, "r1", "868")


def test_k2_a_hand_written_grant_without_its_band_is_denied(tmp_path):
    # A row `high_power = true` with no band on record (a hand edit, or a row written before K2): denied and named.
    # RED before K2 through the start plan: the row's high_power alone put --high-power on r1's daemon.
    from lhpc.core import radio_start
    svc, _ = _svc(tmp_path)
    f = tmp_path / "config" / "radios.toml"
    f.write_text(f.read_text().replace('band = "433"', 'band = "433"\nhigh_power = true', 1))
    params, refusal, notes = radio_start.start_plan(svc, "r1")
    assert (params["hipower"], refusal) == ("off", "")
    assert any("r1: the +20 dBm permission was given with no band on record, the radio is on 433 MHz now: denied"
               in n for n in notes)


@pytest.mark.parametrize("setter,first,second,field", [("set_high_power", "on", "off", "high_power"),
                                                      ("set_ocp", "120", "130", "ocp_ma")])
@pytest.mark.parametrize("running", [True, False])
def test_c59_a_change_while_the_daemon_runs_sets_the_restart_marker(tmp_path, setter, first, second, field, running):
    # G1-C59 B: the daemon reads the grant and the OCP only at start, so a change while r1's daemon runs is flagged
    # restart-required (the durable marker), and a change of a stopped one is not. RED on 046d6439: no marker.
    svc, _ = _svc(tmp_path, status=_status("SX127x", "0") if running else None)
    assert svc.set_ocp("r1", "auto").ok and svc.restart_required("daemon@r1") is None    # no change: nothing flagged
    assert getattr(svc, setter)("r1", first).ok
    marker = svc.restart_required("daemon@r1")                  # the radio's own marker (G1-C60-RE)
    assert (marker is not None) is running
    if running:
        assert field in marker["params"] and marker["band"] == "433"
    assert getattr(svc, setter)("r1", second).ok
    assert (svc.restart_required("daemon@r1") is not None) is running


def test_c59_two_radios_on_433_only_the_granted_one_reaches_twenty(tmp_path):
    # W311's safety matrix (G1-C59): r1 and r2 both on 433, both SX127x daemons running and reporting the
    # permission; only r1 is granted. Start: --high-power on r1 only. Reconfigure: POWER=20 saves on r1, is refused on
    # r2. Live SET: r1's Apply sends POWER=20; r2's sends none; a live POWER=20 SET reaches r1 only.
    from lhpc.core import radio_start
    import radios_fixture
    p = Paths(runtime_root=tmp_path)
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce1")))
    fs = FakeSystem(unix_replies={dc.conf_socket(r): _status("SX127x", "1") for r in ("r1", "r2")})
    svc = ControllerService(system=fs.system, paths=p)
    assert svc.set_high_power("r1", "on").ok
    assert [radio_start.start_plan(svc, r)[0]["hipower"] for r in ("r1", "r2")] == ["on", "off"]
    assert svc.save_daemon_params("daemon", {"POWER": "20"}, rid="r1").ok
    assert not svc.save_daemon_params("daemon", {"POWER": "20"}, rid="r2").ok
    fs.sent.clear()
    svc.apply_daemon_params("daemon", rid="r2")
    assert not any(b"POWER=20" in payload for _path, payload in fs.sent)
    svc.apply_daemon_params("daemon", rid="r1")
    assert [path for path, payload in fs.sent if b"POWER=20" in payload] == [dc.conf_socket("r1")]
    fs.sent.clear()
    assert not svc.daemon_set("r2", "POWER", "20", apply=True).ok and fs.sent == []      # r2: no grant
    assert svc.daemon_set("r1", "POWER", "20", apply=True).ok and [p for p, _b in fs.sent] == [dc.conf_socket("r1")]


def test_set_high_power_saves_and_restarts_nothing(tmp_path):
    svc, fs = _svc(tmp_path)
    r = svc.set_high_power("r1", "on")
    assert r.ok and "restart" in r.summary and "warranty" in r.summary.lower() and "r1 (433 MHz)" in r.summary
    assert _granted(svc, "r1", "433")
    assert fs.sent == []                                  # nothing was sent to any daemon


# --- the argv ---------------------------------------------------------------------------------

def test_argv_carries_the_bare_flag_only_when_on(tmp_path):
    svc, _ = _svc(tmp_path)
    comp = svc.stack("daemon").component("loraham-daemon")
    names = {p.name for p in comp.run_params}
    assert "hipower" in names and not {"hipower_433", "hipower_868"} & names        # K2: no per-band switch
    op = svc.config().operator
    base = {"radio": "433", "hw": "uputronics-ce0", "txmode": "managed", "cadmon": "off",
            "cadrssi": "-90", "rf_log": "on", "id": "r1", "rflog_path": "{runtime}/logs/r1/rf-daemon.log"}
    off = commands.expand_argv(comp.run_argv, comp, {**base, "hipower": "off"}, op, "/rt", "/src", "433")
    assert "--high-power" not in off
    on = commands.expand_argv(comp.run_argv, comp, {**base, "hipower": "on"}, op, "/rt", "/src", "433")
    assert on.count("--high-power") == 1
    assert [a for a in on if a != "--high-power"] == off           # bare: no value token added


# --- live SET through the service: the running daemon decides --------------------------------

def test_live_set_twenty_needs_running_permission_and_saved_switch(tmp_path):
    svc, _ = _svc(tmp_path, status=_status("SX127x", "1"))
    assert not svc.daemon_set("r1", "POWER", "20").ok            # saved off
    assert svc.set_high_power("r1", "on").ok
    assert svc.daemon_set("r1", "POWER", "20").ok                # running on + saved on
    assert not svc.daemon_set("r1", "POWER", "18").ok


def test_live_set_twenty_refused_while_restart_is_pending(tmp_path):
    svc, _ = _svc(tmp_path, status=_status("SX127x", "0"))
    assert svc.set_high_power("r1", "on").ok
    r = svc.daemon_set("r1", "POWER", "20")
    assert not r.ok and "restart" in r.summary


@pytest.mark.parametrize("board,saved,running,value,ok", [
    # Saved SX127x (uputronics), running SX1262: a live POWER=0 is legitimate and must pass.
    ("uputronics", "sx127x", "SX1262", "0", True),
    ("uputronics", "sx127x", "SX1262", "20", True),
    # Saved SX1262 (waveshare), running SX127x: a live POWER=0 must be refused before it is sent.
    ("waveshare-433", "sx1262", "SX127x", "0", False),
    ("waveshare-433", "sx1262", "SX127x", "17", True),
])
def test_live_set_uses_the_running_family_not_the_saved_board(tmp_path, board, saved, running, value, ok):
    svc, fs = _svc(tmp_path, board, status=_status(running, "0"))
    assert svc.chip_family_for_band("433") == saved
    assert svc.daemon_set("r1", "POWER", value, apply=True).ok is ok
    assert any(f"POWER={value}".encode() in payload for _p, payload in fs.sent) is ok


def test_live_set_against_an_older_daemon_admits_only_the_intersection(tmp_path):
    svc, _ = _svc(tmp_path, status=_OLD_STATUS)
    assert svc.daemon_set("r1", "POWER", "17").ok
    assert not svc.daemon_set("r1", "POWER", "0").ok
    r = svc.daemon_set("r1", "POWER", "20")
    assert not r.ok and "chip family" in r.summary


# --- the start gate ---------------------------------------------------------------------------

def _chat_wants_twenty(svc):
    assert svc.set_operator_identity(callsign="XX0XXA").ok          # chat refuses to start without one
    assert svc.set_high_power("r1", "on").ok
    assert svc.save_daemon_params("chat", {"POWER": "20"}, rid="r1").ok


def test_saving_twenty_into_a_profile_needs_the_saved_switch(tmp_path):
    svc, _ = _svc(tmp_path)
    assert not svc.save_daemon_params("chat", {"POWER": "20"}, rid="r1").ok
    assert svc.set_high_power("r1", "on").ok
    assert svc.save_daemon_params("chat", {"POWER": "20"}, rid="r1").ok
    assert not svc.save_daemon_params("chat", {"POWER": "19"}, rid="r1").ok


@pytest.mark.contract
@pytest.mark.safety("RF-TX-opt-in")
def test_start_preflight_gates_on_an_unavailable_permission(tmp_path):
    svc, fs = _svc(tmp_path, status=_status("SX127x", "0"))
    _chat_wants_twenty(svc)
    res = svc.start("chat", apply=True)
    text = "\n".join((res.summary, *res.details))
    assert not res.ok, text
    assert "POWER=20" in text and "high-power permission" in text      # the stack's refusal reason
    assert fs.sent == []                                  # refused BEFORE any RF setter


def test_start_preflight_passes_with_the_running_permission(tmp_path):
    svc, fs = _svc(tmp_path, status=_status("SX127x", "1"))
    _chat_wants_twenty(svc)
    res = svc.start("chat", apply=True)
    assert any(b"POWER=20" in payload for _p, payload in fs.sent), "\n".join((res.summary, *res.details))


def test_a_refused_permission_does_not_report_the_daemon_as_failed(tmp_path):
    """The start gate refuses a stack whose profile asks for POWER=20 while the running daemon
    lacks the permission. The run summary used to read "Run FAILED for 'kiss': loraham-daemon,
    loraham-kiss-tnc did not start/verify" and the TNC "daemon not ready" — the daemon was
    serving the band and untouched; only the stack's own config was refused (live test
    2026-09-26, row 6)."""
    svc, fs = _svc(tmp_path, status=_status("SX127x", "0"))
    assert svc.set_high_power("r1", "on").ok
    assert svc.save_daemon_params("kiss", {"POWER": "20"}, rid="r1").ok
    res = svc.start("kiss", apply=True)
    text = "\n".join((res.summary, *res.details))
    assert not res.ok, text
    assert "loraham-daemon" not in res.summary, res.summary
    assert any("loraham-kiss-tnc" in d and "POWER=20" in d and "permission" in d
               and "daemon not ready" not in d for d in res.details), text
    assert fs.sent == [] or not any(b"POWER=20" in payload for _p, payload in fs.sent)


def test_saved_off_after_saving_twenty_filters_it_but_revokes_nothing(tmp_path):
    svc, _ = _svc(tmp_path, status=_status("SX127x", "1"))
    _chat_wants_twenty(svc)
    assert svc.set_high_power("r1", "off").ok
    from lhpc.core import commands, radio_start
    from lhpc.core import config as cfgmod
    assert "POWER" not in radio_start.instance_dp(svc, "chat", "r1")      # filtered, not deleted
    assert cfgmod.load_stack_config(svc._paths, "chat", inst=commands.Instance("r1"))["dp_POWER"] == "20"
    st = svc.high_power_state("433")
    assert st["live"] is True and st["warn"] and "not granted" in st["mismatch"]


# --- what the console shows -------------------------------------------------------------------

@pytest.mark.parametrize("setup,family,hp,saved,warn,needle", [
    ("uputronics", "SX127x", "1", True, True, ""),
    ("uputronics", "SX127x", "1", False, True, "not granted"),
    ("uputronics", "SX127x", "0", True, False, "restart"),
    ("waveshare-433", "SX1262", "1", True, False, ""),       # no switch: the grant is refused, no banner
    ("waveshare-433", "SX1262", "1", False, False, ""),
    ("uputronics", "SX1262", "1", False, False, "running daemon is sx1262"),  # saved/running differ
    ("waveshare-433", "SX127x", "1", False, True, "running daemon is sx127x"),  # banner keys on RUNNING; the family line outranks the switch line
])
def test_high_power_state_keys_on_the_running_daemon(tmp_path, setup, family, hp, saved, warn,
                                                     needle):
    svc, _ = _svc(tmp_path, setup, status=_status(family, hp))
    if saved:                       # a model without the switch refuses the grant (the list's own check)
        r = svc.set_high_power("r1", "on")
        assert r.ok is (setup == "uputronics") and (r.ok or "has no high-power switch" in r.summary)
    st = svc.high_power_state("433")
    assert st["warn"] is warn
    assert st["family"] == family.lower()
    assert (needle in st["mismatch"]) if needle else st["mismatch"] == ""


def test_high_power_state_old_daemon_and_unreachable(tmp_path):
    svc, _ = _svc(tmp_path, status=_OLD_STATUS)
    assert svc.set_high_power("r1", "on").ok
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
