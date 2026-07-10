"""Web: per-stack daemon radio-parameter panel — display (greyed app-owned rows, editable
LBT), save + reset. Backed by a fake-system service (daemon unreachable)."""

import re

from htmlq import parse
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.adapters.web.app import create_app


def _app(tmp_path):
    def factory():
        return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(service_factory=factory).test_client()


def _csrf(client, path="/stacks"):
    m = re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True))
    return m.group(1) if m else ""


def _row(body, sid):
    # Slice a single stack's row out of the combined /stacks page (all stacks render inline now).
    start = body.index('id="stackrow-' + sid + '"')
    nxt = body.find('id="stackrow-', start + 1)
    return body[start:(nxt if nxt != -1 else len(body))]


def test_server_side_validation_rejects_bad_values(tmp_path):
    # JS is optional; the server must reject every invalid value regardless.
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert not svc.save_daemon_params("daemon", "433", {"MODE": "BOGUS"}).ok
    assert not svc.save_daemon_params("daemon", "433", {"TXQUEUE": "7"}).ok
    assert not svc.save_daemon_params("daemon", "433", {"SF": "13"}).ok        # out of 7..12
    assert not svc.save_daemon_params("daemon", "433", {"POWER": "99"}).ok     # out of 0..20
    assert svc.save_daemon_params("daemon", "433", {"MODE": "FSK"}).ok         # valid enum member


def test_live_mode_fsk_confirm_warns(tmp_path):
    # The live-setting confirm page for MODE=FSK must carry the break-LoRa warning.
    c = _app(tmp_path)
    tok = _csrf(c)
    body = c.post("/radio/433/set",
                  data={"_csrf": tok, "key": "MODE", "value": "FSK"}).get_data(as_text=True)
    assert "MODE=FSK" in body and "break LoRa" in body        # FSK warning present
    # A non-FSK live change gets no such warning.
    body2 = c.post("/radio/433/set",
                   data={"_csrf": tok, "key": "TXMODE", "value": "DIRECT"}).get_data(as_text=True)
    assert "break LoRa" not in body2


def test_apply_live_disabled_unless_running_or_daemon(tmp_path):
    body = _app(tmp_path).get("/stacks").get_data(as_text=True)
    disabled = 'disabled title="Available only while the stack is running"'
    assert disabled in _row(body, "meshcom")       # app, not running
    assert disabled not in _row(body, "daemon")    # daemon: always on


def test_apply_live_rejected_server_side_when_not_running(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r = svc.apply_daemon_params("meshcom", "433")            # not running -> refused
    assert not r.ok and "running" in r.summary


def test_meshtastic_has_no_daemon_panel(tmp_path):
    body = _app(tmp_path).get("/stacks").get_data(as_text=True)
    assert "Daemon radio parameters" not in _row(body, "meshtastic")   # direct-SPI: no daemon panel


def test_daemon_panel_follows_upper_band_switch(tmp_path):
    c = _app(tmp_path)
    assert "(433 MHz)" in c.get("/stacks").get_data(as_text=True)          # default
    assert "(868 MHz)" in c.get("/stacks?band=868").get_data(as_text=True)  # switched


def test_apply_live_saves_then_reports(tmp_path):
    # Daemon unreachable in tests: Apply persists the values, then reports it can't reach it.
    c = _app(tmp_path)
    tok = _csrf(c)
    r = c.post("/stacks/meshcom/daemon-params/apply",
               data={"_csrf": tok, "band": "433", "dp_CADIDLE": "33"})
    assert r.status_code in (302, 303)
    assert 'value="33"' in c.get("/stacks").get_data(as_text=True)  # saved


def test_save_then_reset_daemon_params(tmp_path):
    c = _app(tmp_path)
    tok = _csrf(c)
    r = c.post("/stacks/meshcom/daemon-params",
               data={"_csrf": tok, "band": "433", "dp_CADIDLE": "40", "dp_CADWAIT": ""})
    assert r.status_code in (302, 303)
    body = c.get("/stacks").get_data(as_text=True)
    assert 'value="40"' in body                              # override persisted + shown
    c.post("/stacks/meshcom/daemon-params/reset", data={"_csrf": tok, "band": "433"})
    body = c.get("/stacks").get_data(as_text=True)
    assert 'value="28"' in body and 'value="40"' not in body  # back to default


def test_panel_stays_open_after_save(tmp_path):
    c = _app(tmp_path)
    tok = _csrf(c)
    r = c.post("/stacks/meshcom/daemon-params",
               data={"_csrf": tok, "band": "433", "dp_CADIDLE": "40"})
    loc = r.headers["Location"]
    assert r.status_code in (302, 303)
    # TARGET-SPECIFIC: reopen only meshcom's row + its panel (no generic dp=1).
    assert "open=meshcom" in loc and "dp=meshcom" in loc and loc.endswith("#stack-daemon-params-meshcom")
    assert parse(c.get(loc).get_data(as_text=True)) \
        .by_id("stack-daemon-params-meshcom").has_attr("open")               # forced open on return
    assert not parse(c.get("/stacks").get_data(as_text=True)) \
        .by_id("stack-daemon-params-meshcom").has_attr("open")               # collapsed by default


def test_dp_target_specific_opens_only_matching_panel(tmp_path):
    doc = parse(_app(tmp_path).get("/stacks?open=meshcom&dp=meshcom").get_data(as_text=True))
    # ONLY meshcom's daemon panel is forced open; the daemon stack's own panel stays collapsed.
    assert doc.by_id("stack-daemon-params-meshcom").has_attr("open")
    assert not doc.by_id("stack-daemon-params-daemon").has_attr("open")


def test_dp1_no_longer_opens_every_daemon_panel(tmp_path):
    # Regression: the old ?dp=1 opened EVERY daemon-params panel globally. `dp` must now match a
    # stack id, so a non-matching value opens NONE.
    doc = parse(_app(tmp_path).get("/stacks?dp=1").get_data(as_text=True))
    panels = doc.find("details", **{"class": "advcfg dparams"})
    assert panels                                            # panels present...
    assert not any(p.has_attr("open") for p in panels)       # ...but none forced open by dp=1


def test_save_rejects_out_of_range(tmp_path):
    c = _app(tmp_path)
    tok = _csrf(c)
    c.post("/stacks/meshcom/daemon-params",
           data={"_csrf": tok, "band": "433", "dp_CADIDLE": "99999", "dp_CADWAIT": ""})
    body = c.get("/stacks").get_data(as_text=True)
    assert 'value="99999"' not in body                       # rejected, not stored


def test_save_requires_csrf(tmp_path):
    c = _app(tmp_path)
    r = c.post("/stacks/meshcom/daemon-params",
               data={"band": "433", "dp_CADIDLE": "40"})     # no token
    assert r.status_code == 400


# --- Apply-live truthfulness, config atomicity, radio/lifecycle locking -------------------

def _svc(tmp_path, reply=None):
    unix = {"/tmp/loraconf433.sock": reply} if reply else {}
    return ControllerService(system=FakeSystem(unix_replies=unix).system,
                             paths=Paths(runtime_root=tmp_path))


def _echo_svc(tmp_path):
    # A daemon that echoes STATUS and CHANNEL correctly, so every confirmable param read-backs OK.
    class _Echo:
        def request(self, path, payload, timeout, max_bytes):
            if payload.strip().startswith(b"GET CHANNEL"):
                return b"CHANNEL MODE=LORA\n"
            return (b"STATUS RADIO=READY TXMODE=MANAGED TXQUEUE=1 CADMONITOR=0 CADRSSI=-90 "
                    b"CADTXAFTERTIMEOUT=0 CADWAIT=1500 CADIDLE=250\n")
        def send(self, path, payload, timeout):
            pass
    sysm = FakeSystem().system
    sysm.unix = _Echo()
    return ControllerService(system=sysm, paths=Paths(runtime_root=tmp_path))


def test_apply_live_full_success(tmp_path):
    svc = _echo_svc(tmp_path)
    r = svc.apply_daemon_params("daemon", "433")            # daemon: apply always permitted
    assert r.ok and not r.data["failed"] and r.data["band"] == "433"


def test_apply_live_total_failure_is_not_success(tmp_path):
    # Radio not READY -> every set fails -> ok=False (never green), nothing applied.
    svc = _svc(tmp_path, b"STATUS RADIO=UNINITIALIZED\n")
    r = svc.apply_daemon_params("daemon", "433")
    assert not r.ok and r.data["applied"] == [] and r.data["failed"]


def test_apply_live_partial_failure_is_visibly_partial(tmp_path):
    # READY, but CADIDLE cannot be confirmed -> partial -> ok=False with PARTIAL summary.
    svc = _svc(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=99\n")
    r = svc.apply_daemon_params("daemon", "433")
    assert not r.ok and "PARTIAL" in r.summary
    assert "CADIDLE" in r.data["failed"] and r.data["applied"]


def test_apply_live_reports_confirmed_vs_sent_unconfirmed(tmp_path):
    # Radio params the daemon does not echo are reported SENT, never claimed confirmed.
    svc = _svc(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n")
    d = svc.apply_daemon_params("daemon", "433").data
    assert "SF" in d["sent_unconfirmed"] and "SF" not in d["confirmed"]
    assert "CADIDLE" in d["confirmed"]


def test_apply_live_failure_flashes_warning(tmp_path):
    # Daemon unreachable in _app: save persists, apply fails -> the flash is a warning, not green.
    c = _app(tmp_path)
    tok = _csrf(c)
    r = c.post("/stacks/daemon/daemon-params/apply",
               data={"_csrf": tok, "band": "433", "dp_CADIDLE": "40"})
    body = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "flash-warn" in body and "flash-ok" not in body


def test_saved_override_persists_after_failed_apply(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path, b"STATUS RADIO=UNINITIALIZED\n")   # apply will fail
    assert svc.save_daemon_params("daemon", "433", {"CADIDLE": "40"}).ok
    r = svc.apply_daemon_params("daemon", "433")
    assert not r.ok and r.data["persisted"]
    assert cfgmod.load_stack_config(svc._paths, "daemon")["dp_433_CADIDLE"] == "40"   # still saved


def test_save_preserves_unrelated_config(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_stack_config(svc._paths, "meshcom",
                             {"autostart_meshcom-gps-relay": "on", "c_foo": "bar"})
    assert svc.save_daemon_params("meshcom", "433", {"CADIDLE": "40"}).ok
    stored = cfgmod.load_stack_config(svc._paths, "meshcom")
    assert stored["autostart_meshcom-gps-relay"] == "on" and stored["c_foo"] == "bar"
    assert stored["dp_433_CADIDLE"] == "40"


def test_save_preserves_other_band_overrides(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_stack_config(svc._paths, "voice", {"dp_868_CADIDLE": "77"})
    assert svc.save_daemon_params("voice", "433", {"CADIDLE": "40"}).ok
    stored = cfgmod.load_stack_config(svc._paths, "voice")
    assert stored["dp_868_CADIDLE"] == "77" and stored["dp_433_CADIDLE"] == "40"
    # reset 433 keeps 868
    assert svc.reset_daemon_params("voice", "433").ok
    stored = cfgmod.load_stack_config(svc._paths, "voice")
    assert stored["dp_868_CADIDLE"] == "77" and "dp_433_CADIDLE" not in stored


def test_rejected_save_leaves_prior_config_intact(tmp_path):
    # A pre-write validation failure never touches the previously saved good config.
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    assert svc.save_daemon_params("daemon", "433", {"CADIDLE": "40"}).ok
    assert not svc.save_daemon_params("daemon", "433", {"POWER": "999"}).ok   # invalid -> reject
    assert cfgmod.load_stack_config(svc._paths, "daemon")["dp_433_CADIDLE"] == "40"


def test_apply_live_respects_same_band_lock(tmp_path):
    from lhpc.core import reslock
    svc = _svc(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n")
    with reslock.operation_lock(svc._paths, "claim.loraham.radio.433", "start", "other"):
        r = svc.apply_daemon_params("daemon", "433")
    assert not r.ok and r.data.get("busy")                 # typed busy, not a race


def test_apply_live_reentrant_within_held_guard(tmp_path):
    # Called while THIS thread already holds the band lock (as a Start would) -> no self-deadlock.
    svc = _echo_svc(tmp_path)
    with svc._keys_guard("start", "daemon", ["lifecycle.daemon", "claim.loraham.radio.433"]):
        r = svc.apply_daemon_params("daemon", "433")
    assert r.ok                                            # re-entrant, completed


def test_apply_live_other_band_not_blocked(tmp_path):
    from lhpc.core import reslock
    svc = _echo_svc(tmp_path)
    # A held 868 lock must NOT block a 433 apply (independent bands).
    with reslock.operation_lock(svc._paths, "claim.loraham.radio.868", "start", "other"):
        r = svc.apply_daemon_params("daemon", "433")
    assert r.ok


def test_daemon_overrides_are_ephemeral_not_persisted(tmp_path):
    # A confirm-page value (or its "Reset to defaults") is applied for THIS start only; the saved
    # config is never touched.
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    svc.save_daemon_params("meshcom", "433", {"CADIDLE": "40"})                     # saved config
    assert svc._daemon_param_applies("meshcom", "433")["CADIDLE"] == "40"           # config applied
    ephem = svc._daemon_param_applies("meshcom", "433", {"CADIDLE": "28"})          # ephemeral wins
    assert ephem["CADIDLE"] == "28"
    assert cfgmod.load_stack_config(svc._paths, "meshcom")["dp_433_CADIDLE"] == "40"  # config untouched


# --- Area 4: canonical daemon-value persistence ----------------------------------------------

def test_lowercase_enum_canonicalized(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    svc.save_daemon_params("daemon", "433", {"MODE": "fsk", "TXMODE": "direct"})
    st = cfgmod.load_stack_config(svc._paths, "daemon")
    assert st["dp_433_MODE"] == "FSK"                     # fsk -> FSK (valid, canonical)
    assert st["dp_433_TXMODE"] == "DIRECT"                # direct -> DIRECT (differs from default)


def test_leading_zero_int_canonicalized_and_default_equiv_clears(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    svc.save_daemon_params("daemon", "433", {"CADIDLE": "040"})       # 40 != default 250 -> store 40
    assert cfgmod.load_stack_config(svc._paths, "daemon")["dp_433_CADIDLE"] == "40"
    svc.save_daemon_params("daemon", "433", {"CADIDLE": "0250"})      # == default 250 -> clear
    assert "dp_433_CADIDLE" not in cfgmod.load_stack_config(svc._paths, "daemon")


def test_omitted_key_leaves_override_unchanged(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    svc.save_daemon_params("daemon", "433", {"CADIDLE": "40"})
    svc.save_daemon_params("daemon", "433", {"MODE": "FSK"})          # CADIDLE omitted -> unchanged
    st = cfgmod.load_stack_config(svc._paths, "daemon")
    assert st["dp_433_CADIDLE"] == "40" and st["dp_433_MODE"] == "FSK"


def test_explicit_blank_clears_only_that_key(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    svc.save_daemon_params("daemon", "433", {"CADIDLE": "40", "MODE": "FSK"})
    svc.save_daemon_params("daemon", "433", {"CADIDLE": ""})          # blank clears CADIDLE only
    st = cfgmod.load_stack_config(svc._paths, "daemon")
    assert "dp_433_CADIDLE" not in st and st["dp_433_MODE"] == "FSK"


def test_reset_clears_all_profile_keys(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    svc.save_daemon_params("daemon", "433", {"CADIDLE": "40", "MODE": "FSK", "CADWAIT": "1200"})
    assert svc.reset_daemon_params("daemon", "433").ok
    assert not [k for k in cfgmod.load_stack_config(svc._paths, "daemon") if k.startswith("dp_433_")]


# --- Area 1: strict ephemeral Start-confirm override validation ------------------------------

def _daemon_svc(tmp_path):
    import os
    b = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    b.parent.mkdir(parents=True); b.write_text("#!/bin/sh\nsleep 0.1\n"); os.chmod(b, 0o755)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_ephemeral_invalid_value_fails_before_launch(tmp_path):
    svc = _daemon_svc(tmp_path)
    r = svc.run_action("start", "daemon", apply=True, daemon_overrides={"433": {"SF": "99"}})
    assert not r.ok and "invalid daemon parameter" in r.summary and "SF" in r.summary
    assert not any("start daemon" in d or "already serving" in d for d in r.details)   # no launch


def test_ephemeral_unknown_key_and_wrong_band_rejected(tmp_path):
    svc = _daemon_svc(tmp_path)
    assert not svc.run_action("start", "daemon", apply=True,
                              daemon_overrides={"433": {"BOGUS": "x"}}).ok      # unknown key
    r = svc.run_action("start", "meshcom", apply=True, daemon_overrides={"868": {"CADIDLE": "40"}})
    assert not r.ok and "not part of this start" in r.summary                   # 868 not in a 433 start


def test_ephemeral_mode_fsk_accepted_by_validation(tmp_path):
    svc = _daemon_svc(tmp_path)
    r = svc.run_action("start", "daemon", apply=True, daemon_overrides={"433": {"MODE": "FSK"}})
    assert "invalid daemon parameter" not in (r.summary or "")                  # FSK is valid


def test_crafted_web_post_invalid_override_warns(tmp_path):
    import os, re
    b = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    b.parent.mkdir(parents=True); b.write_text("#!/bin/sh\n"); os.chmod(b, 0o755)
    c = create_app(service_factory=lambda: ControllerService(
        system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))).test_client()
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/stacks").get_data(as_text=True)).group(1)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "daemon",
                                "confirmed": "yes", "p_radio": "433", "dp_433_SF": "99"},
               follow_redirects=True)
    assert b"invalid daemon parameter" in r.data                                # rejected, no launch


# --- Area 2: genuinely per-band Start-confirm panels -----------------------------------------

def test_daemon_start_panels_per_radio_mode(tmp_path):
    svc = _svc(tmp_path)
    assert [p["band"] for p in svc.daemon_start_panels("daemon", {"radio": "both"})] == ["433", "868"]
    assert [p["band"] for p in svc.daemon_start_panels("daemon", {"radio": "433"})] == ["433"]
    assert [p["band"] for p in svc.daemon_start_panels("daemon", {"radio": "868"})] == ["868"]


def test_confirm_both_renders_two_band_scoped_panels(tmp_path):
    import os, re
    b = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    b.parent.mkdir(parents=True); b.write_text("#!/bin/sh\n"); os.chmod(b, 0o755)
    c = create_app(service_factory=lambda: ControllerService(
        system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))).test_client()
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/stacks").get_data(as_text=True)).group(1)
    body = c.post("/action", data={"_csrf": tok, "op": "start", "target": "daemon",
                                    "p_radio": "both"}).get_data(as_text=True)
    assert "(433 MHz)" in body and "(868 MHz)" in body                          # two panels
    assert 'name="dp_433_CADIDLE"' in body and 'name="dp_868_CADIDLE"' in body  # band-scoped names


def test_per_band_ephemeral_values_do_not_cross(tmp_path):
    svc = _svc(tmp_path)
    assert svc._daemon_param_applies("daemon", "433", {"CADIDLE": "111"})["CADIDLE"] == "111"
    assert svc._daemon_param_applies("daemon", "868", {"CADIDLE": "222"})["CADIDLE"] == "222"


def test_ephemeral_start_leaves_saved_overrides_untouched(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _daemon_svc(tmp_path)
    svc.save_daemon_params("daemon", "433", {"CADIDLE": "40"})
    svc.run_action("start", "daemon", apply=True, daemon_overrides={"433": {"CADIDLE": "250"}})
    assert cfgmod.load_stack_config(svc._paths, "daemon")["dp_433_CADIDLE"] == "40"


# --- Area 3: browser-only FSK warning covers inline start-confirm ----------------------------

# --- Area 1: strict Start-confirm dp_* field parsing -----------------------------------------

def test_parse_start_daemon_overrides_shapes():
    from lhpc.adapters.web.app import _parse_start_daemon_overrides as parse
    from werkzeug.datastructures import MultiDict
    # malformed field SHAPE rejected at the parser (before any launch)
    for bad in ("dp_bad", "dp_433_", "dp_"):
        pb, err = parse(MultiDict([(bad, "x")]))
        assert err is not None and pb is None
    # duplicated field rejected
    _, err = parse(MultiDict([("dp_433_MODE", "LORA"), ("dp_433_MODE", "FSK")]))
    assert err is not None
    # unknown band/param are PARSED here (service normalizer rejects them, not this parser)
    pb, err = parse(MultiDict([("dp_433_BOGUS", "x")]))
    assert err is None and pb == {"433": {"BOGUS": "x"}}
    # valid incl. FSK
    pb, err = parse(MultiDict([("dp_433_MODE", "FSK"), ("dp_868_CADIDLE", "40")]))
    assert err is None and pb == {"433": {"MODE": "FSK"}, "868": {"CADIDLE": "40"}}


def _daemon_web(tmp_path):
    import os
    b = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    b.parent.mkdir(parents=True); b.write_text("#!/bin/sh\n"); os.chmod(b, 0o755)
    return create_app(service_factory=lambda: ControllerService(
        system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))).test_client()


def _start_post(c, extra):
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    c.get("/stacks").get_data(as_text=True)).group(1)
    data = {"_csrf": tok, "op": "start", "target": "daemon", "confirmed": "yes", "p_radio": "433"}
    data.update(extra)
    return c.post("/action", data=data, follow_redirects=True)


def test_crafted_malformed_field_fails_before_launch(tmp_path):
    c = _daemon_web(tmp_path)
    r = _start_post(c, {"dp_bad": "x"})
    assert b"malformed daemon field" in r.data                 # parser-level rejection
    # no start marker written -> no launch occurred
    assert not (tmp_path / "state").exists() or not list((tmp_path / "state").glob("*.json"))


def test_crafted_unknown_param_fails_via_normalizer(tmp_path):
    c = _daemon_web(tmp_path)
    r = _start_post(c, {"dp_433_BOGUS": "x"})
    assert b"invalid daemon parameter" in r.data and b"BOGUS" in r.data


def test_crafted_unknown_band_fails(tmp_path):
    c = _daemon_web(tmp_path)
    r = _start_post(c, {"dp_999_MODE": "LORA"})
    assert b"invalid daemon parameter" in r.data                # unknown band 999


def test_crafted_empty_param_field_rejected(tmp_path):
    c = _daemon_web(tmp_path)
    assert b"malformed daemon field" in _start_post(c, {"dp_433_": "x"}).data
