"""The daemon per radio (plan A §2.7, stage L5; B4-TABLES-v2 rows D1, D2, D5, D6, D8, D10–D13, S1, S6, H10, M7).

INTERFACE-B4-v8 §2–§3 names the functions; decisions B4-D1, B4-D3a and B4-D4 settle their PROPOSAL cells.
The radios functions are new (no old path): each such test imports the module itself. The resolved-argv
test runs through the existing public path (`commands.expand_argv` over the manifest's daemon component)
with literal params and literal expected tokens, so it fails on the release before L5 by behaviour.
"""
from __future__ import annotations

import dataclasses

import pytest

from lhpc.core import commands, radio_models
from lhpc.core.config import Config
from lhpc.core.manifest import load_manifest

MODELS = radio_models.load()
RT, SRC = "/rt", "/rt/src/x"
BUSY = "[CH341] refused: BUSY:"


def _radios():
    from lhpc.core import radios
    return radios


def _rd():
    from lhpc.core import radio_daemon  # B4-D5: the daemon-per-radio functions
    return radio_daemon


def _r(rid="r1", model="loraham", band="433", **kw):
    return _radios().Radio(id=rid, name=f"Radio {rid}", model=model, band=band, **kw)


def _stick(rid="r1", serial="", port="", **kw):
    return _r(rid, model="meshstick-1262", band="868", usb_serial=serial, usb_port=port, **kw)


# ---- the daemon's argv through the manifest (class (a): an existing public path) ---------------------

DAEMON = next(c for st in load_manifest() for c in st.components if c.id == "loraham-daemon")


def _daemon_argv(rid, hipower="off", ocp="100"):
    params = {"radio": "868", "hw": "loraham", "txmode": "managed", "cadmon": "off", "cadrssi": "-90",
              "hipower": hipower, "ocp": ocp, "rf_log": "on", "id": rid, "usb_serial": "",
              "rflog_path": f"{{runtime}}/logs/{rid}/rf-daemon.log"}
    return commands.expand_argv(DAEMON.run_argv, DAEMON, params, Config().operator, RT, SRC, "868")


def _pair(argv, opt, value):
    return any(argv[i] == opt and argv[i + 1] == value for i in range(len(argv) - 1))


@pytest.mark.parametrize("rid", ["r1", "r2", "r12"])
def test_t_d1_the_daemon_argv_names_its_radio_by_id(rid):
    argv = _daemon_argv(rid)
    assert _pair(argv, "--id", rid), argv
    assert _pair(argv, "--radio", "868") and _pair(argv, "--hw", "loraham")


def test_t_d1_two_radios_on_one_band_log_to_two_files():
    a, b = _daemon_argv("r1"), _daemon_argv("r2")
    assert _pair(a, "--rflog-path", f"{RT}/logs/r1/rf-daemon.log"), a
    assert _pair(b, "--rflog-path", f"{RT}/logs/r2/rf-daemon.log"), b


def test_t_co9_ocp_is_always_passed_and_high_power_only_when_on():
    off, on = _daemon_argv("r1", "off", "100"), _daemon_argv("r1", "on", "140")
    assert _pair(off, "--ocp", "100") and _pair(on, "--ocp", "140")
    assert "--high-power" in on
    assert "--high-power" not in off                        # +20 dBm only on the operator's on


def test_the_daemon_argv_never_carries_a_usb_port():
    assert not [t for t in _daemon_argv("r1") if t.startswith("--usb-port")]


# ---- daemon_params (class (b)) ---------------------------------------------------------------------

def _settings(**kw):
    base = {"txmode": "managed", "cadmon": "off", "cadrssi": -90, "hipower": False, "ocp": "auto", "dp": {}}
    base.update(kw)
    cls = _radios().RadioSettings
    names = {f.name for f in dataclasses.fields(cls)}
    if "notes" in names:                                  # B4-D5: RadioSettings also carries notes
        base.setdefault("notes", ())
    return cls(**base)


def test_daemon_params_for_a_stick_pass_the_serial_prefix_and_the_preset():
    p = _rd().daemon_params(_stick("r3", "ABCDEFGHIJ"), MODELS["meshstick-1262"], _settings(), rf_log="on")
    assert p["radio"] == "868" and p["hw"] == "meshstick-1262" and p["id"] == "r3"
    assert p["usb_serial"] == "ABCDEFGH"                     # D13: the first 8 characters
    assert p["rflog_path"] == "{runtime}/logs/r3/rf-daemon.log"


def test_daemon_params_for_the_serialless_stick_pass_no_selector():
    p = _rd().daemon_params(_stick("r3", port="1-1.3"), MODELS["meshstick-1262"], _settings(), rf_log="on")
    assert p["usb_serial"] == ""


@pytest.mark.parametrize("hipower,ocp,want", [(False, "auto", "100"), (True, "auto", "140"),
                                              (True, "90", "90"), (False, "130", "130")])
def test_daemon_params_carry_the_effective_ocp_and_the_high_power_flag(hipower, ocp, want):
    p = _rd().daemon_params(_r("r1", high_power=hipower), MODELS["loraham"],
                          _settings(hipower=hipower, ocp=ocp), rf_log="on")
    assert p["ocp"] == want
    assert p["hipower"] == ("on" if hipower else "off")


# ---- sockets (S1) -----------------------------------------------------------------------------------

def test_socket_paths_name_the_three_sockets_per_radio():
    sp = _rd().socket_paths("r7", "/tmp")
    assert (sp.conf, sp.data, sp.frame) == ("/tmp/loraconf-r7.sock", "/tmp/lora-r7.sock", "/tmp/loraf-r7.sock")


@pytest.mark.parametrize("rid", ["r0", "r100", "", "R1", "r1/../x", "r1\n"])
def test_socket_paths_refuse_an_id_outside_r1_to_r99(rid):
    with pytest.raises(ValueError):
        _rd().socket_paths(rid, "/tmp")


def test_s1_a_socket_path_longer_than_107_bytes_is_refused():
    ok_dir = "/" + "d" * (107 - len("/loraconf-r99.sock") - 1)
    assert len(_rd().socket_paths("r99", ok_dir).conf.encode()) == 107
    with pytest.raises(ValueError):
        _rd().socket_paths("r99", ok_dir + "d")
    assert _rd().check_sockets([_r("r99")], ok_dir) == []
    assert _rd().check_sockets([_r("r99")], ok_dir + "d")


def test_s1_check_sockets_skips_disabled_radios():
    assert _rd().check_sockets([_r("r99", enabled=False)], "/" + "d" * 200) == []


# ---- daemon_entry: is this /proc entry the daemon? (S6, D6) ----------------------------------------

def _entry(exe=None, exe_err="", argv=None, argv_err="", comm=None, comm_err=""):
    return _rd().daemon_entry(exe, exe_err, argv, argv_err, comm, comm_err)


def test_daemon_entry_by_exe_argv_or_comm():
    T = _rd().Tri
    assert _entry(exe="/rt/src/loraham_daemon/loraham_daemon", argv=["x"], comm="x") is T.YES
    assert _entry(exe=None, exe_err="EACCES", argv=["loraham_daemon/loraham_daemon", "--id", "r1"],
                  comm="loraham_daemon") is T.YES
    assert _entry(exe=None, exe_err="EACCES", argv=None, argv_err="EACCES", comm="loraham_daemon") is T.YES


def test_daemon_entry_sees_a_daemon_whose_binary_was_rebuilt():
    T = _rd().Tri
    assert _entry(exe="/rt/src/loraham_daemon/loraham_daemon (deleted)", argv=["x"], comm="x") is T.YES


def test_daemon_entry_zombie_is_the_daemon_and_parses_as_unknown():
    """B4-D4: a zombie daemon refuses that radio's start while it lasts."""
    assert _entry(exe=None, exe_err="ENOENT", argv=[], comm="loraham_daemon") is _rd().Tri.YES
    proc = _rd().parse_daemon_proc(42, [])
    assert proc.kind == "unknown"
    assert _rd().start_refusal(_r("r1"), [proc])


def test_daemon_entry_no_for_vanished_kernel_thread_and_other_program():
    T = _rd().Tri
    assert _entry(exe=None, exe_err="ENOENT", argv=None, argv_err="ENOENT", comm=None, comm_err="ENOENT") is T.NO
    assert _entry(exe=None, exe_err="ESRCH", argv=None, argv_err="ENOENT", comm=None, comm_err="ESRCH") is T.NO
    assert _entry(exe=None, exe_err="ENOENT", argv=[], comm="kworker/0:1") is T.NO
    assert _entry(exe="/usr/bin/python3", argv=["python3", "loraham_daemon"], comm="python3") is T.NO


def test_daemon_entry_unknown_when_nothing_is_readable():
    T = _rd().Tri
    assert _entry(exe=None, exe_err="EACCES", argv=None, argv_err="EACCES", comm=None, comm_err="EACCES") is T.UNKNOWN


# ---- parse_daemon_proc and start_refusal (S6, D5, D6) ----------------------------------------------

@pytest.mark.parametrize("argv,kind,rid,band", [
    (["loraham_daemon", "--radio", "868", "--id", "r2"], "id", "r2", ""),
    (["loraham_daemon", "--id=r12", "--radio=433"], "id", "r12", ""),
    (["loraham_daemon", "--radio", "433"], "no-id", "", "433"),
    (["loraham_daemon", "--radio=868"], "no-id", "", "868"),
])
def test_parse_daemon_proc_exact_tokens(argv, kind, rid, band):
    p = _rd().parse_daemon_proc(7, argv)
    assert (p.kind, p.rid, p.band) == (kind, rid, band)


@pytest.mark.parametrize("argv", [
    None,                                                        # cmdline unreadable
    ["loraham_daemon"],                                          # neither option
    ["loraham_daemon", "--id", "r1", "--id", "r2"],              # two ids
    ["loraham_daemon", "--id", "r100"],                          # not r1..r99
    ["loraham_daemon", "--id"],                                  # option without a value
    ["loraham_daemon", "--radio", "915"],                        # a band the daemon does not serve
    ["loraham_daemon", "--identity", "r1", "--radio", "433x"],   # a substring is no option
])
def test_parse_daemon_proc_unknown(argv):
    p = _rd().parse_daemon_proc(7, argv)
    assert p.kind == "unknown" and p.reason


def test_parse_daemon_proc_never_matches_a_substring():
    assert _rd().parse_daemon_proc(7, ["loraham_daemon", "--idx", "r1", "--radio", "433"]).kind == "no-id"


def test_start_refusal_cases():
    r1 = _r("r1", band="433")
    P = _rd().DaemonProc
    assert _rd().start_refusal(r1, []) is None
    assert _rd().start_refusal(r1, [P(pid=9, kind="id", rid="r2", band="", reason="")]) is None
    same = _rd().start_refusal(r1, [P(pid=9, kind="id", rid="r1", band="", reason="")])
    assert same and "9" in same                                   # D5: already runs elsewhere
    stray = _rd().start_refusal(r1, [P(pid=11, kind="no-id", rid="", band="433", reason="")])
    assert stray and "11" in stray                                # D6 (B4-D1)
    assert _rd().start_refusal(r1, [P(pid=12, kind="no-id", rid="", band="868", reason="")]) is None
    unk = _rd().start_refusal(r1, [P(pid=13, kind="unknown", rid="", band="", reason="cmdline unreadable")])
    assert unk and "13" in unk                                    # S6


# ---- STATUS checks (D10, H10) -------------------------------------------------------------------

def test_d10_a_wrong_or_missing_id_is_a_wrong_daemon():
    assert _rd().check_status_id({"ID": "r1"}, "r1") is None
    assert "r1" in _rd().check_status_id({"ID": "r2"}, "r1")
    assert _rd().check_status_id({}, "r1")
    assert _rd().check_status_id({"ID": ""}, "r1")


def _bound_cases():
    s = ("meshstick-1262", {"usb_serial": "ABCDEFGHXYZ"})
    n = ("meshstick-1262", {"usb_port": "1-1.3"})
    p = ("loraham", {})
    return [
        (s, "serial:ABCDEFGH", True), (s, "serial:ABCDEFGX", False), (s, None, False),
        (s, "port:1-1.3", False), (s, "serial:ABC%44EFGH", True), (s, "serial:ABC%4GEFGH", False),
        (n, "port:1-1.3", True), (n, "port:1-1.4", False), (n, None, False), (n, "serial:ABCDEFGH", False),
        (p, "-", True), (p, "serial:ABCDEFGH", False), (p, None, False),
    ]


@pytest.mark.parametrize("case", _bound_cases(), ids=lambda c: f"{c[0][0]}-{c[1]}-{c[2]}")
def test_h10_bound_after_ready(case):
    (model, kw), bound, ok = case
    band = "868" if model == "meshstick-1262" else "433"
    radio = _r("r1", model=model, band=band, **kw)
    status = {} if bound is None else {"BOUND": bound}
    got = _rd().check_bound(status, radio, MODELS[model])
    assert (got is None) == ok, (bound, got)


# ---- exits, verify bound, start order (D1, D2, D5, D8, D11, D12) ---------------------------------

@pytest.mark.parametrize("attempt,delay", [(0, 2.5), (1, 5.0), (2, 10.0)])
def test_d11_busy_is_retried_after_2_5_5_10_seconds(attempt, delay):
    act = _rd().start_exit_action(4, f"some line\n{BUSY} stick 1-1.3\n", attempt)
    assert act[0] == "retry" and act[1] == delay


def test_d11_busy_after_the_third_retry_fails_with_the_line():
    act = _rd().start_exit_action(4, f"{BUSY} stick 1-1.3\n", 3)
    assert act[0] == "fail" and BUSY in act[2]


def test_d11_busy_only_on_the_last_line_and_only_with_exit_4():
    assert _rd().start_exit_action(4, f"{BUSY} earlier\nanother refusal\n", 0)[0] == "fail"
    assert _rd().start_exit_action(5, f"{BUSY} x\n", 0)[0] == "fail"


@pytest.mark.parametrize("code", [4, 5, 1])
def test_d12_other_exits_fail_at_once_with_the_daemon_line(code):
    act = _rd().start_exit_action(code, "noise\n[CH341] refused: no stick on 1-1.3\n\n", 0)
    assert act[0] == "fail" and act[2] == "[CH341] refused: no stick on 1-1.3"


@pytest.mark.parametrize("code,tail", [(3, ""), (0, ""), (2, "x\n"), (7, ""), (-9, ""), (4, ""), (5, "\n\n")])
def test_every_other_exit_fails_with_a_line_never_retries(code, tail):
    """B4-D4: exit 0 / other codes / a signal / an empty tail each fail with the stated line."""
    act = _rd().start_exit_action(code, tail, 0)
    assert act[0] == "fail" and act[1] == 0.0 and act[2].strip()


def test_d5_exit_3_is_never_retried():
    assert _rd().start_exit_action(3, f"{BUSY} x\n", 0)[0] == "fail"


@pytest.mark.parametrize("transport,n,want", [("spi", 0, 4.0), ("spi", 3, 4.0), ("usb-ch341", 0, 11.6),
                                              ("usb-ch341", 1, 11.6), ("usb-ch341", 3, 22.4)])
def test_d8_verify_bound(transport, n, want):
    assert _rd().verify_bound_s(transport, n) == pytest.approx(want)


def test_d1_d2_a_failed_radio_does_not_stop_the_next():
    seen = []

    def launch(r):
        seen.append(r.id)
        if r.id == "r2":
            return False, "no READY after 4.0 s"
        if r.id == "r10":
            raise RuntimeError("boom")
        return True, ""
    radios = [_r("r10", band="433"), _r("r2", band="868"), _r("r3", model="waveshare-sx1262", band="868"),
              _r("r4", model="uputronics", band="433", ce="ce1", enabled=False)]
    lines, ok_all = _rd().run_starts(radios, launch)
    assert seen == ["r2", "r3", "r10"]                           # numeric id order, enabled only
    assert ok_all is False and len(lines) == 3


def test_run_starts_lets_an_interrupt_through():

    def launch(r):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _rd().run_starts([_r("r1")], launch)


# ---- per-radio settings: fields on the row (B4-D3a), resolution at start (M7, B4-D1, B4-D4) ----------

DEFAULTS = {"txmode": "managed", "cadmon": "off", "cadrssi": "-90", "hipower": "off", "ocp": "auto"}


def _resolve(radio, model="loraham", legacy=None, legacy_dp=None):
    return _radios().radio_settings(radio, MODELS[model], legacy or {}, legacy_dp or {}, DEFAULTS)


def test_m7_band_params_are_carried_by_default():
    rad = _radios()
    assert rad.CARRY_BAND_PARAMS is True
    s = _resolve(_r("r1", band="433"), legacy={"tx_433": "direct", "cadmon_433": "on", "cadrssi_433": "-100",
                                                "hipower_433": "on", "tx_868": "managed"})
    assert (s.txmode, s.cadmon, s.cadrssi, s.hipower) == ("direct", "on", -100, True)


def test_a_value_on_the_row_wins_over_legacy():
    s = _resolve(_r("r1", band="433", txmode="managed"), legacy={"tx_433": "direct"})
    assert s.txmode == "managed"


def test_q8_an_explicit_false_on_the_row_beats_a_legacy_on():
    """Q8: the operator's saved high power off must not be replaced by a carried +20 dBm on."""
    s = _resolve(_r("r1", band="433", high_power=False), legacy={"hipower_433": "on"})
    assert s.hipower is False
    p = _rd().daemon_params(_r("r1", band="433", high_power=False), MODELS["loraham"], s, rf_log="on")
    assert p["hipower"] == "off"


def test_q8_an_unset_high_power_carries_a_legacy_on_on_a_model_with_the_switch():
    assert MODELS["loraham"].high_power_switch
    assert _resolve(_r("r1", band="433"), legacy={"hipower_433": "on"}).hipower is True


def test_a_legacy_high_power_on_is_not_carried_onto_a_model_without_the_switch():
    """B4-D4: not carried (off is the safe side)."""
    assert not MODELS["waveshare-sx1262"].high_power_switch
    s = _resolve(_r("r1", model="waveshare-sx1262", band="868"), model="waveshare-sx1262",
                 legacy={"hipower_868": "on"})
    assert s.hipower is False


@pytest.mark.parametrize("legacy", [{"tx_433": "loud"}, {"cadmon_433": "yes"}, {"cadrssi_433": "-0"},
                                    {"cadrssi_433": "-090"}, {"cadrssi_433": "5"}, {"cadrssi_433": "-131"},
                                    {"cadrssi_433": True}, {"hipower_433": "1"}, {"tx_433": 1}])
def test_an_invalid_carried_value_refuses_naming_its_key(legacy):
    with pytest.raises(ValueError) as e:
        _resolve(_r("r1", band="433"), legacy=legacy)
    assert next(iter(legacy)) in str(e.value)


def test_m7_with_the_switch_off_the_defaults_apply(monkeypatch):
    rad = _radios()
    monkeypatch.setattr(rad, "CARRY_BAND_PARAMS", False)
    s = _resolve(_r("r1", band="433"), legacy={"tx_433": "direct", "hipower_433": "on", "ocp_433": "130"})
    assert (s.txmode, s.hipower) == ("managed", False)
    assert s.ocp == "130"                                         # OCP is carried regardless (RK11)


def test_dp_carried_only_when_every_client_stack_agrees():
    """B4-D4: dp carried only when every client stack agrees, else named and not carried."""
    agree = _resolve(_r("r1", band="433"), legacy_dp={"loraham-chat": {"CADWAIT": "200"},
                                                      "loraham-kiss-tnc": {"CADWAIT": "200"}})
    assert agree.dp.get("CADWAIT") == "200"
    differ = _resolve(_r("r1", band="433"), legacy_dp={"loraham-chat": {"CADWAIT": "200"},
                                                       "loraham-kiss-tnc": {"CADWAIT": "300"}})
    assert "CADWAIT" not in differ.dp


def test_dp_empty_or_invalid_legacy_values_are_dropped():
    """B4-D4: "" or invalid → dropped (and named); they do not count against agreement."""
    s = _resolve(_r("r1", band="433"), legacy_dp={"loraham-chat": {"CADWAIT": "200"},
                                                  "loraham-kiss-tnc": {"CADWAIT": ""},
                                                  "loraham-voice": {"CADWAIT": "not-a-number"}})
    assert s.dp.get("CADWAIT") == "200"


def test_check_refuses_invalid_settings_on_the_row():
    rad = _radios()
    for kw in ({"txmode": "loud"}, {"cadmon": "1"}, {"cadrssi": 5}, {"cadrssi": -131}, {"cadrssi": True}):
        assert rad.check([_r("r1", band="433", **kw)], MODELS), kw
    assert rad.check([_r("r1", band="433", txmode="direct", cadmon="on", cadrssi=-100)], MODELS) == []


@pytest.mark.parametrize("value,ok", [("200", True), ("nope", False), ("-1", False), ("99999", False), ("", False)])
def test_a_dp_value_on_the_row_is_checked_like_todays_override(value, ok):
    """B4-D3a: the dp_<PARAM> fields on the row get today's validation (daemon_control.validate_set)."""
    rad = _radios()
    text = ('[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\nband = "433"\n'
            f'dp_CADWAIT = "{value}"\n')
    assert (rad.load_text(text, MODELS).kind == rad.VALID) == ok, value


# ---- the wiring rows (B4-WIRING-TABLES-v1 d9cc1faa, B4-D14) -----------------------------------------

def test_w4_5_a_013x_stray_without_id_refuses_every_radio_of_its_band():
    """W4-5: 0.13.x drove the band's SPI HAT, so a daemon without --id may hold any radio of that band."""
    rd = _rd()
    stray = [rd.DaemonProc(pid=21, kind="no-id", rid="", band="868", reason="")]
    for radio in (_r("r1", band="868"), _stick("r2", "ABCDEFGH"), _r("r3", model="waveshare-sx1262", band="868")):
        why = rd.start_refusal(radio, stray)
        assert why and "21" in why, radio.id
    assert rd.start_refusal(_r("r4", band="433"), stray) is None


def _daemon_argv_with_rflog(value):
    params = {"radio": "868", "hw": "loraham", "txmode": "managed", "cadmon": "off", "cadrssi": "-90",
              "hipower": "off", "ocp": "100", "rf_log": "on", "id": "r12", "usb_serial": "", "rflog_path": value}
    return commands.expand_argv(DAEMON.run_argv, DAEMON, params, Config().operator, RT, SRC, "868")


@pytest.mark.parametrize("rid", ["r1", "r12", "r99"])
def test_w7_8_the_rf_log_path_of_a_radio_is_accepted(rid):
    argv = _daemon_argv_with_rflog(f"{{runtime}}/logs/{rid}/rf-daemon.log")
    assert _pair(argv, "--rflog-path", f"{RT}/logs/{rid}/rf-daemon.log"), argv


@pytest.mark.parametrize("value", [
    "{runtime}/logs/r1/../rf-daemon.log", "{runtime}/logs/../r1/rf-daemon.log", "{runtime}/logs/r1 /rf-daemon.log",
    "{runtime}/logs/{x}/rf-daemon.log", "{runtime}/logs/r0/rf-daemon.log", "{runtime}/logs/r100/rf-daemon.log",
    "{runtime}/logs/R1/rf-daemon.log", "{runtime}/logs/r1/rf-daemon.log.bak", "/tmp/logs/r1/rf-daemon.log",
    "{runtime}/logs/rf-daemon-868.log", "", "{runtime}/logs/r1/rf-daemon.log\n",
    "/rt/logs/r1/rf-daemon.log",                       # the substituted form: the check sees only templates
])
def test_w7_8_any_other_rf_log_path_is_refused(value):
    """W7-8: the named path validator accepts exactly {runtime}/logs/r<1-99>/rf-daemon.log and nothing else."""
    from lhpc.core.validators import ValidationError
    with pytest.raises((commands.CommandError, ValidationError)):
        _daemon_argv_with_rflog(value)


# ---- the wiring functions (INTERFACE-B4-W5 b2beded0, confirmed by the code role) ----------------------
# W1-1..W1-11 resolve_target, W4-7 stop_targets, W4-9 reticulum_rid, W5-8/H3/H4 settings_save_refusals.

def _state(*radios, kind=None, reasons=()):
    rad = _radios()
    return rad.State(kind or rad.VALID, radios=tuple(radios), reasons=tuple(reasons))


def _always(radio, model):
    return True


def _target(state, **kw):
    rd = _rd()
    return rd.resolve_target(state, MODELS, kw.pop("can_run", _always), **kw)


def _proceeds(t, rid):
    return t.outcome == "proceed" and t.rid == rid


def _refuses(t):
    return t.outcome == "refuse" and t.rid == "" and bool(t.reason)


def _two_868():
    """r1 (SPI loraham) and r2 (SPI waveshare), both on 868; built per test (no import at collection)."""
    return (_r("r1", band="868"), _r("r2", model="waveshare-sx1262", band="868"))


# W1-1 / W1-2: an explicit rid
def test_w1_1_an_enabled_rid_proceeds_with_an_empty_reason():
    t = _target(_state(*_two_868()), rid="r2")
    assert _proceeds(t, "r2") and t.reason == ""


@pytest.mark.parametrize("rid", ["r9", "r0", "r100", "R1", "r01", "r1;x", " r1", "r1\n", 1, True, ["r1"], {"r": 1}])
def test_w1_2_an_unknown_or_malformed_rid_refuses_never_falling_back_to_a_band(rid):
    assert _refuses(_target(_state(*_two_868()), rid=rid))


def test_w1_2_a_disabled_rid_refuses():
    assert _refuses(_target(_state(_r("r1", band="868", enabled=False), _two_868()[1]), rid="r1"))


def test_w1_2_a_rid_the_stack_cannot_run_on_refuses():
    t = _target(_state(*_two_868()), rid="r2", can_run=lambda r, m: r.id != "r2")
    assert _refuses(t)


# not given: None and "" resolve like the bare call
@pytest.mark.parametrize("kw", [{"rid": None}, {"rid": ""}, {"band": None}, {"band": ""}, {"rid": "", "band": ""}])
def test_not_given_resolves_like_the_bare_call(kw):
    st = _state(*_two_868())
    assert _target(st, running=("r2",), **kw) == _target(st, running=("r2",))


# both given
def test_both_given_proceeds_only_when_the_rid_is_on_that_band():
    st = _state(_r("r3", model="uputronics", band="433", ce="ce1"), *_two_868())
    assert _proceeds(_target(st, rid="r2", band="868"), "r2")
    t = _target(st, rid="r2", band="433")
    assert _refuses(t) and "r2" in t.reason and "433" in t.reason
    assert _refuses(_target(_state(_r("r1", band="868", enabled=False), _two_868()[1]), rid="r1", band="868"))
    assert _refuses(_target(st, rid="r9", band="868"))


# W1-9: the band value
@pytest.mark.parametrize("band", ["434", "868 ", "８６８", 868, True, [], {}])
def test_w1_9_a_band_outside_433_868_refuses(band):
    assert _refuses(_target(_state(*_two_868()), band=band))


# W1-3 / W1-4 / W1-5: a band
def test_w1_3_one_radio_of_the_band_proceeds_naming_it():
    t = _target(_state(_r("r1", band="433"), _two_868()[1]), band="868")
    assert _proceeds(t, "r2") and "r2" in t.reason


def test_w1_4_no_radio_of_the_band_refuses():
    assert _refuses(_target(_state(_r("r1", band="433")), band="868"))
    assert _refuses(_target(_state(_r("r1", band="868", enabled=False)), band="868"))


def test_w1_5_two_or_three_radios_of_the_band_refuse_naming_each_in_radio_order():
    t = _target(_state(*_two_868()), band="868")
    assert _refuses(t) and "r1" in t.reason and "r2" in t.reason
    three = _state(_r("r10", band="868"), _stick("r3", "ABCDEFGH"), _two_868()[1])
    t = _target(three, band="868")
    assert _refuses(t) and t.candidates == ("r2", "r3", "r10")


# W1-6: bare <stack>
def test_w1_6a_one_running_instance_is_the_target():
    assert _proceeds(_target(_state(*_two_868()), running=("r2",)), "r2")


def test_w1_6b_the_fallback_marker_is_the_target_when_nothing_runs():
    assert _proceeds(_target(_state(*_two_868()), fallback_rid="r2"), "r2")


def test_w1_6c_nothing_running_and_no_marker_with_two_radios_refuses_naming_both():
    t = _target(_state(*_two_868()))
    assert _refuses(t) and "r1" in t.reason and "r2" in t.reason


def test_w1_6d_a_stale_marker_on_a_disabled_radio_is_ignored():
    st = _state(_r("r1", band="868"), _r("r2", model="waveshare-sx1262", band="868", enabled=False),
                _r("r3", model="uputronics", band="868", ce="ce1"))
    assert _refuses(_target(st, fallback_rid="r2"))


def test_w1_6e_a_running_rid_that_is_no_candidate_is_ignored():
    st = _state(*_two_868())
    t = _target(st, running=("r9",))
    assert _refuses(t)
    assert _proceeds(_target(_state(_two_868()[1]), running=("r9",)), "r2")


# type checks: never raising
@pytest.mark.parametrize("kw", [{"rid": 1}, {"band": 868}, {"running": "r1"}, {"running": {"r1": 1}},
                                {"running": ("r1", 2)}, {"fallback_rid": 2}, {"old_config": "yes"}])
def test_resolve_target_type_checks_refuse_never_raise(kw):
    assert _refuses(_target(_state(*_two_868()), **kw))


@pytest.mark.parametrize("kw", [{"state": None}, {"models": []}, {"can_run": None}])
def test_resolve_target_caller_built_inputs_refuse_never_raise(kw):
    rd = _rd()
    args = {"state": _state(*_two_868()), "models": MODELS, "can_run": _always}
    args.update(kw)
    t = rd.resolve_target(args["state"], args["models"], args["can_run"])
    assert _refuses(t)


# W1-8: the list is not startable
def test_w1_8_the_reasons_of_an_absent_or_invalid_list():
    rad, rd = _radios(), _rd()
    absent = _state(kind=rad.ABSENT)
    r_no = _target(absent, old_config=rd.Tri.NO)
    r_yes = _target(absent, old_config=rd.Tri.YES)
    r_unk = _target(absent, old_config=rd.Tri.UNKNOWN)
    assert all(_refuses(t) for t in (r_no, r_yes, r_unk))
    assert len({r_no.reason, r_yes.reason, r_unk.reason}) == 3
    assert r_unk.reason == "the old configuration cannot be read"
    inv = _target(_state(kind=rad.INVALID, reasons=("radio row 1: band is required",)))
    assert _refuses(inv) and "band is required" in inv.reason
    assert _refuses(_target(_state(kind=rad.INVALID)))


# W1-10: the result follows the state passed in (the caller loads it inside the flock)
def test_w1_10_a_disable_between_resolution_and_launch_refuses():
    before = _state(*_two_868())
    after = _state(_r("r1", band="868"), _r("r2", model="waveshare-sx1262", band="868", enabled=False))
    assert _proceeds(_target(before, rid="r2"), "r2")
    assert _refuses(_target(after, rid="r2"))


# cannot tell: never silently dropped
def test_a_radio_whose_can_run_raises_refuses_the_band_and_bare_rules():
    def can_run(radio, model):
        if radio.id == "r1":
            raise RuntimeError("cannot tell")
        return True
    st = _state(*_two_868())
    for kw in ({"band": "868"}, {}):
        t = _target(st, can_run=can_run, **kw)
        assert _refuses(t) and "r1" in t.reason, kw
    assert _proceeds(_target(st, can_run=can_run, rid="r2"), "r2")


def test_a_radio_of_a_missing_model_refuses_the_band_rule():
    rd = _rd()
    models = {k: v for k, v in MODELS.items() if k != "loraham"}
    t = rd.resolve_target(_state(*_two_868()), models, _always, band="868")
    assert _refuses(t) and "r1" in t.reason


# W4-7: stop_targets
def _P(pid, kind, rid="", band="", reason=""):
    return _rd().DaemonProc(pid=pid, kind=kind, rid=rid, band=band, reason=reason)


def test_w4_7_only_the_verified_own_daemon_is_signalled():
    rd = _rd()
    procs = [_P(10, "id", rid="r2"), _P(11, "id", rid="r2"), _P(12, "unknown", reason="cmdline unreadable"),
             _P(13, "id", rid="r1"), _P(14, "no-id", band="868")]
    plan = rd.stop_targets("r2", procs, {10})
    assert plan.signal == (10,)
    named = {pid for pid, _ in plan.named}
    assert named == {11, 12} and plan.ok is False
    assert rd.stop_targets("r2", [_P(10, "id", rid="r2")], {10}).ok is True


def test_w4_7_a_bool_pid_never_matches_and_odd_entries_are_named():
    rd = _rd()
    plan = rd.stop_targets("r1", [_P(True, "id", rid="r1")], {1})
    assert plan.signal == () and plan.ok is False
    plan = rd.stop_targets("r1", ["not a proc", _P(5, "zombie")], set())
    assert plan.signal == () and len(plan.named) == 2 and plan.ok is False


def test_w4_7_an_invalid_rid_raises():
    with pytest.raises(ValueError):
        _rd().stop_targets("r0", [], set())


# W4-9: reticulum_rid
def test_w4_9_the_one_spi_radio_of_the_band():
    rd = _rd()
    assert _proceeds(rd.reticulum_rid(_state(_r("r2", band="868")), MODELS, "868"), "r2")
    mixed = _state(_r("r2", band="868"), _stick("r3", "ABCDEFGH"))
    assert _proceeds(rd.reticulum_rid(mixed, MODELS, "868"), "r2")


def test_w4_9_none_usb_only_or_two_spi_radios_refuse():
    rd = _rd()
    assert _refuses(rd.reticulum_rid(_state(_r("r1", band="433")), MODELS, "868"))
    usb = rd.reticulum_rid(_state(_stick("r3", "ABCDEFGH")), MODELS, "868")
    assert _refuses(usb) and "r3" in usb.reason
    two = rd.reticulum_rid(_state(*_two_868()), MODELS, "868")
    assert _refuses(two) and "r1" in two.reason and "r2" in two.reason


def test_w4_9_the_list_state_band_and_inputs():
    rad, rd = _radios(), _rd()
    absent = _state(kind=rad.ABSENT)
    no = rd.reticulum_rid(absent, MODELS, "868", old_config=rd.Tri.NO)
    yes = rd.reticulum_rid(absent, MODELS, "868", old_config=rd.Tri.YES)
    unk = rd.reticulum_rid(absent, MODELS, "868", old_config=rd.Tri.UNKNOWN)
    assert all(_refuses(t) for t in (no, yes, unk)) and len({no.reason, yes.reason, unk.reason}) == 3
    assert unk.reason == "the old configuration cannot be read"
    inv = rd.reticulum_rid(_state(kind=rad.INVALID, reasons=("x",)), MODELS, "868")
    assert _refuses(inv) and "x" in inv.reason
    assert _refuses(rd.reticulum_rid(_state(kind=rad.INVALID), MODELS, "868"))
    assert _refuses(rd.reticulum_rid(_state(_r("r2", band="868")), MODELS, "868", old_config="yes"))
    assert _refuses(rd.reticulum_rid(_state(_r("r2", band="868")), MODELS, "915"))
    models = {k: v for k, v in MODELS.items() if k != "loraham"}
    assert _refuses(rd.reticulum_rid(_state(_r("r2", band="868")), models, "868"))
    assert _refuses(rd.reticulum_rid(None, MODELS, "868"))
    assert _refuses(rd.reticulum_rid(_state(_r("r2", band="868")), [], "868"))


# W5-8 / H3 / H4: settings_save_refusals
REFUSED_FIELDS = [("model", "uputronics", {"ce": "ce1"}), ("band", "868", {}), ("ce", "ce0", {}),
                  ("usb_serial", "ABCDEFGH", {}), ("usb_port", "1-1.3", {})]
ALLOWED_FIELDS = [("name", "Other"), ("txmode", "direct"), ("cadmon", "on"), ("cadrssi", -100),
                  ("high_power", True), ("ocp_ma", "120")]


def _row_for(field):
    if field in ("usb_serial", "usb_port"):
        return _stick("r1", "ABCDEFGH" if field == "usb_port" else "", port="" if field == "usb_port" else "1-1.2")
    if field == "ce":
        return _r("r1", model="uputronics", band="433", ce="ce1")
    return _r("r1", band="433")


@pytest.mark.parametrize("field,value,extra", REFUSED_FIELDS, ids=[f[0] for f in REFUSED_FIELDS])
def test_w5_8_a_connection_or_model_edit_is_refused_while_a_user_runs(field, value, extra):
    rd = _rd()
    old = _row_for(field)
    new = dataclasses.replace(old, **{field: value}, **extra)
    reasons = rd.settings_save_refusals(old, new, ("meshtastic@r1",))
    assert reasons and any("meshtastic@r1" in x for x in reasons)
    assert rd.settings_save_refusals(old, new, ()) == []


@pytest.mark.parametrize("field,value", ALLOWED_FIELDS, ids=[f[0] for f in ALLOWED_FIELDS])
def test_w5_8_a_settings_only_edit_is_allowed_while_a_user_runs(field, value):
    rd = _rd()
    old = _r("r1", band="433")
    assert rd.settings_save_refusals(old, dataclasses.replace(old, **{field: value}), ("meshtastic@r1",)) == []


def test_w5_8_a_dp_edit_is_allowed_while_a_user_runs():
    rd = _rd()
    old = _radios().load_text('[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\nband = "433"\n',
                              MODELS).radios[0]
    new = _radios().load_text('[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\nband = "433"\n'
                              'dp_CADWAIT = "200"\n', MODELS).radios[0]
    assert rd.settings_save_refusals(old, new, ("meshtastic@r1",)) == []


@pytest.mark.parametrize("proc,owned", [(("id", "r1", ""), {20}), (("id", "r1", ""), set()),
                                        (("no-id", "", "433"), set()), (("unknown", "", ""), set())],
                         ids=["own-daemon", "unverified-id", "no-id-of-the-band", "unknown"])
def test_w5_8_each_daemon_blocker_class_refuses_a_model_edit_naming_the_pid(proc, owned):
    rd = _rd()
    old = _r("r1", band="433")
    new = dataclasses.replace(old, model="uputronics", ce="ce1")
    kind, rid, band = proc
    reasons = rd.settings_save_refusals(old, new, (), (_P(20, kind, rid=rid, band=band, reason="x"),), owned)
    assert reasons and any("20" in x for x in reasons)


def test_w5_8_unrelated_daemons_do_not_block():
    rd = _rd()
    old = _r("r1", band="433")
    new = dataclasses.replace(old, model="uputronics", ce="ce1")
    procs = (_P(21, "no-id", band="868"), _P(22, "id", rid="r2"))
    assert rd.settings_save_refusals(old, new, (), procs, set()) == []


def test_w5_8_reticulum_as_the_only_user_refuses_a_model_edit():
    rd = _rd()
    old = _r("r1", band="433")
    assert rd.settings_save_refusals(old, dataclasses.replace(old, model="uputronics", ce="ce1"),
                                     ("reticulum@r1",))


def test_w5_8_disable_is_not_refused_here():
    rd = _rd()
    old = _r("r1", band="433")
    assert rd.settings_save_refusals(old, dataclasses.replace(old, enabled=False), ("meshtastic@r1",)) == []


@pytest.mark.parametrize("args", [("id",), ("users-str",), ("procs-str",), ("zombie",)])
def test_w5_8_bad_inputs_raise(args):
    rd = _rd()
    old = _r("r1", band="433")
    new = dataclasses.replace(old, model="uputronics", ce="ce1")
    with pytest.raises(ValueError):
        if args[0] == "id":
            rd.settings_save_refusals(old, dataclasses.replace(new, id="r2"), ())
        elif args[0] == "users-str":
            rd.settings_save_refusals(old, new, "meshtastic@r1")
        elif args[0] == "procs-str":
            rd.settings_save_refusals(old, new, (), "x")
        else:
            rd.settings_save_refusals(old, new, (), (_P(5, "zombie"),), set())
