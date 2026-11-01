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
])
def test_w7_8_any_other_rf_log_path_is_refused(value):
    """W7-8: the named path validator accepts exactly {runtime}/logs/r<1-99>/rf-daemon.log and nothing else."""
    from lhpc.core.validators import ValidationError
    with pytest.raises((commands.CommandError, ValidationError)):
        _daemon_argv_with_rflog(value)
