"""Web: per-stack daemon radio-parameter panel — display (greyed app-owned rows, editable
LBT), save + reset. Backed by a fake-system service (daemon unreachable)."""

import re

import pytest

from htmlq import parse
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
import launch_band_fixture


def _row(body, sid):
    """One stack's row on the combined /stacks page, as an htmlq scope."""
    doc = parse(body)
    return doc.within(doc.by_id(f"stackrow-{sid}"))


def _dp_of(svc, target, rid):
    """The saved daemon params of `target` on radio `rid` (DEC-W384az): the daemon's = the radio row's dp_*, a client
    stack's = its instance set `dp_<PARAM>` in `<sid>@<rid>.toml`; keys `dp_<PARAM>`."""
    from lhpc.core import commands, radios
    from lhpc.core import config as cfgmod
    if target == "daemon":
        return {f"dp_{k}": v for k, v in next(r for r in radios.load(svc._paths).radios if r.id == rid).dp}
    return cfgmod.load_stack_config(svc._paths, target, inst=commands.Instance(rid))


def test_server_side_validation_rejects_bad_values(tmp_path):
    # JS is optional; the server must reject every invalid value regardless.
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert not svc.save_daemon_params("daemon", rid="r1", values={"MODE": "BOGUS"}).ok
    assert not svc.save_daemon_params("daemon", rid="r1", values={"TXQUEUE": "7"}).ok
    assert not svc.save_daemon_params("daemon", rid="r1", values={"SF": "13"}).ok        # out of 7..12
    assert not svc.save_daemon_params("daemon", rid="r1", values={"POWER": "99"}).ok     # out of 0..20
    assert svc.save_daemon_params("daemon", rid="r1", values={"MODE": "FSK"}).ok         # valid enum member


@pytest.mark.contract
def test_live_mode_fsk_confirm_warns(web, csrf):
    # The live-setting confirm page for MODE=FSK must carry the break-LoRa warning.
    c = web()
    tok = csrf(c)
    body = c.post("/radio/r1/set",
                  data={"_csrf": tok, "key": "MODE", "value": "FSK"}).get_data(as_text=True)
    assert "MODE=FSK" in body and "break LoRa" in body        # FSK warning present
    # A non-FSK live change gets no such warning.
    body2 = c.post("/radio/r1/set",
                   data={"_csrf": tok, "key": "TXMODE", "value": "DIRECT"}).get_data(as_text=True)
    assert "break LoRa" not in body2


@pytest.mark.contract
def test_apply_live_disabled_unless_running_or_daemon(web):
    c = web()
    # Panels live in the (lazy) stack bodies — fetch each row force-opened.
    mc = c.get("/stacks?open=meshcom").get_data(as_text=True)
    dm = c.get("/stacks?open=daemon").get_data(as_text=True)
    def apply_live_disabled(body, sid):
        buttons = [b for b in _row(body, sid).find("button") if b.text == "Apply live"]
        assert buttons, "no Apply live button"
        return all(b.has_attr("disabled") for b in buttons)
    assert apply_live_disabled(mc, "meshcom")        # app, not running
    assert not apply_live_disabled(dm, "daemon")     # daemon: always on


@pytest.mark.contract
def test_apply_live_rejected_server_side_when_not_running(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r = svc.apply_daemon_params("meshcom", rid="r1")         # not running -> refused
    assert not r.ok and "meshcom@r1 is running" in r.summary


def test_meshtastic_has_no_daemon_panel(web):
    body = web().get("/stacks?open=meshtastic").get_data(as_text=True)   # force its lazy body inline
    assert "Daemon radio parameters" not in _row(body, "meshtastic").text   # direct-SPI: no daemon panel


def test_daemon_panel_follows_upper_band_switch(web):
    c = web()
    # The per-component band badge (Dependencies list) carries each stack's configured band. A 433
    # stack shows (433 MHz), an 868 stack shows (868 MHz); both now live in their lazy stack bodies.
    assert "(433 MHz)" in c.get("/stacks?open=meshcom").get_data(as_text=True)          # 433-band stack
    assert "(868 MHz)" in c.get("/stacks?open=meshcore").get_data(as_text=True)           # 868-band stack


@pytest.mark.contract
def test_apply_live_saves_then_reports(web, csrf):
    # Daemon unreachable in tests: Apply persists the values, then reports it can't reach it.
    c = web()
    tok = csrf(c)
    r = c.post("/stacks/meshcom/daemon-params/apply",
               data={"_csrf": tok, "radio": "r1", "dp_CADIDLE": "33"})
    assert r.status_code in (302, 303)
    assert 'value="33"' in c.get("/stacks?open=meshcom").get_data(as_text=True)  # saved (lazy body)


def test_save_then_reset_daemon_params(web, csrf):
    c = web()
    tok = csrf(c)
    r = c.post("/stacks/meshcom/daemon-params",
               data={"_csrf": tok, "radio": "r1", "dp_CADIDLE": "40", "dp_CADWAIT": ""})
    assert r.status_code in (302, 303)
    body = c.get("/stacks?open=meshcom").get_data(as_text=True)
    assert 'value="40"' in body                              # override persisted + shown
    c.post("/stacks/meshcom/daemon-params/reset", data={"_csrf": tok, "radio": "r1"})
    body = c.get("/stacks?open=meshcom").get_data(as_text=True)
    assert 'value="28"' in body and 'value="40"' not in body  # back to default


def test_panel_stays_open_after_save(web, csrf):
    c = web()
    tok = csrf(c)
    r = c.post("/stacks/meshcom/daemon-params",
               data={"_csrf": tok, "radio": "r1", "dp_CADIDLE": "40"})
    loc = r.headers["Location"]
    assert r.status_code in (302, 303)
    # TARGET-SPECIFIC: reopen only meshcom's row + its panel (no generic dp=1).
    assert "open=meshcom" in loc and "dp=meshcom" in loc and loc.endswith("#stack-daemon-params-meshcom")
    assert parse(c.get(loc).get_data(as_text=True)) \
        .by_id("stack-daemon-params-meshcom").has_attr("open")               # forced open on return
    # By default meshcom's row is closed, so its body (and this panel) is lazy-loaded → absent,
    # i.e. never auto-open.
    assert parse(c.get("/stacks").get_data(as_text=True)) \
        .by_id("stack-daemon-params-meshcom") is None


def test_dp_target_specific_opens_only_matching_panel(web):
    doc = parse(web().get("/stacks?open=meshcom&dp=meshcom").get_data(as_text=True))
    # ONLY meshcom's daemon panel is forced open; the daemon stack's row is closed, so its whole
    # body (and its own daemon panel) is lazy-loaded → absent, and thus certainly not forced open.
    assert doc.by_id("stack-daemon-params-meshcom").has_attr("open")
    assert doc.by_id("stack-daemon-params-daemon") is None


def test_dp1_no_longer_opens_every_daemon_panel(web):
    # Regression: the old ?dp=1 opened EVERY daemon-params panel globally. `dp` must now match a
    # stack id, so a non-matching value opens NONE. We force meshcom's body inline (so a daemon panel
    # is actually rendered) — dp=1 must still not force it open.
    doc = parse(web().get("/stacks?open=meshcom&dp=1").get_data(as_text=True))
    panels = doc.find("details", **{"class": "advcfg dparams"})
    assert panels                                            # panels present...
    assert not any(p.has_attr("open") for p in panels)       # ...but none forced open by dp=1


def test_save_rejects_out_of_range(web, csrf):
    c = web()
    tok = csrf(c)
    c.post("/stacks/meshcom/daemon-params",
           data={"_csrf": tok, "radio": "r1", "dp_CADIDLE": "99999", "dp_CADWAIT": ""})
    body = c.get("/stacks?open=meshcom").get_data(as_text=True)   # meshcom panel force-opened (lazy body)
    assert 'value="99999"' not in body                       # rejected, not stored


@pytest.mark.contract
def test_save_requires_csrf(web):
    c = web()
    r = c.post("/stacks/meshcom/daemon-params",
               data={"radio": "r1", "dp_CADIDLE": "40"})     # no token
    assert r.status_code == 400


# --- Apply-live truthfulness, config atomicity, radio/lifecycle locking -------------------

def _svc(tmp_path, reply=None):
    unix = {"/tmp/loraconf-r1.sock": reply} if reply else {}
    return ControllerService(system=FakeSystem(unix_replies=unix).system,
                             paths=Paths(runtime_root=tmp_path))


def _echo_svc(tmp_path):
    # A daemon that echoes STATUS and CHANNEL correctly, so every confirmable param read-backs OK.
    class _Echo:
        def request(self, path, payload, timeout, max_bytes):
            if payload.startswith(b"SET "):
                return b"OK\n"
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
    r = svc.apply_daemon_params("daemon", rid="r1")            # daemon: apply always permitted
    assert r.ok and not r.data["failed"] and r.data["band"] == "433"


@pytest.mark.contract
def test_apply_live_total_failure_is_not_success(tmp_path):
    # Radio not READY -> every set fails -> ok=False (never green), nothing applied.
    svc = _svc(tmp_path, b"STATUS RADIO=UNINITIALIZED\n")
    r = svc.apply_daemon_params("daemon", rid="r1")
    assert not r.ok and r.data["applied"] == [] and r.data["failed"]


def test_apply_live_partial_failure_is_visibly_partial(tmp_path):
    # READY, but CADIDLE cannot be confirmed -> partial -> ok=False with PARTIAL summary.
    svc = _svc(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=99\n")
    r = svc.apply_daemon_params("daemon", rid="r1")
    assert not r.ok and "PARTIAL" in r.summary
    assert "CADIDLE" in r.data["failed"] and r.data["applied"]


def test_apply_live_reports_confirmed_vs_sent_unconfirmed(tmp_path):
    # Radio params the daemon does not echo are reported SENT, never claimed confirmed.
    svc = _svc(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n")
    d = svc.apply_daemon_params("daemon", rid="r1").data
    assert "SF" in d["sent_unconfirmed"] and "SF" not in d["confirmed"]
    assert "CADIDLE" in d["confirmed"]


def test_apply_live_counts_a_refused_set_as_failed(tmp_path):
    # The daemon refuses SF with ERR: Apply counts it failed, never "sent".
    sock = "/tmp/loraconf-r1.sock"
    svc = ControllerService(system=FakeSystem(
        unix_replies={sock: b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"},
        set_replies={sock: b"ERR INVALID\n"}).system, paths=Paths(runtime_root=tmp_path))
    r = svc.apply_daemon_params("daemon", rid="r1")
    assert not r.ok and "SF" in r.data["failed"] and "SF" not in r.data["sent_unconfirmed"]


def test_apply_live_failure_flashes_warning(web, csrf):
    # Daemon unreachable in _app: save persists, apply fails -> the flash is a warning, not green.
    c = web()
    tok = csrf(c)
    r = c.post("/stacks/daemon/daemon-params/apply",
               data={"_csrf": tok, "radio": "r1", "dp_CADIDLE": "40"})              # W3: the radio's apply
    body = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "flash-warn" in body and "flash-ok" not in body


def test_saved_override_persists_after_failed_apply(tmp_path):
    svc = _svc(tmp_path, b"STATUS RADIO=UNINITIALIZED\n")   # apply will fail
    assert svc.save_daemon_params("daemon", rid="r1", values={"CADIDLE": "40"}).ok
    r = svc.apply_daemon_params("daemon", rid="r1")
    assert not r.ok and r.data["persisted"]
    assert _dp_of(svc, "daemon", "r1")["dp_CADIDLE"] == "40"   # still saved


def test_save_preserves_unrelated_config(tmp_path):
    from lhpc.core import commands
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_stack_config(svc._paths, "meshcom",
                             {"autostart_meshcom-gps-relay": "on", "c_foo": "bar"})
    cfgmod.save_stack_config(svc._paths, "meshcom", {"c_foo": "bar"}, inst=commands.Instance("r1"))
    assert svc.save_daemon_params("meshcom", rid="r1", values={"CADIDLE": "40"}).ok
    assert cfgmod.load_stack_config(svc._paths, "meshcom")["autostart_meshcom-gps-relay"] == "on"   # global untouched
    stored = _dp_of(svc, "meshcom", "r1")
    assert stored["c_foo"] == "bar" and stored["dp_CADIDLE"] == "40"


def test_save_preserves_the_other_instances_set(tmp_path):
    # kiss@r1 and kiss@r2 hold their own sets; a save or reset of one never touches the other.
    svc = _svc(tmp_path)
    assert svc.save_daemon_params("kiss", rid="r2", values={"CADIDLE": "77"}).ok
    assert svc.save_daemon_params("kiss", rid="r1", values={"CADIDLE": "40"}).ok
    assert _dp_of(svc, "kiss", "r2")["dp_CADIDLE"] == "77" and _dp_of(svc, "kiss", "r1")["dp_CADIDLE"] == "40"
    assert svc.reset_daemon_params("kiss", rid="r1").ok
    assert _dp_of(svc, "kiss", "r2")["dp_CADIDLE"] == "77" and "dp_CADIDLE" not in _dp_of(svc, "kiss", "r1")


def test_rejected_save_leaves_prior_config_intact(tmp_path):
    # A pre-write validation failure never touches the previously saved good config.
    svc = _svc(tmp_path)
    assert svc.save_daemon_params("daemon", rid="r1", values={"CADIDLE": "40"}).ok
    assert not svc.save_daemon_params("daemon", rid="r1", values={"POWER": "999"}).ok   # invalid -> reject
    assert _dp_of(svc, "daemon", "r1")["dp_CADIDLE"] == "40"


def test_apply_live_respects_same_band_lock(tmp_path):
    from lhpc.core import reslock
    svc = _svc(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n")
    svc._SELF_LOCK_WAIT_S = 0.2          # fast contention (default 5.0s just delays the busy result)
    with reslock.operation_lock(svc._paths, "claim.loraham.radio.r1", "start", "other"):
        r = svc.apply_daemon_params("daemon", rid="r1")
    assert not r.ok and r.data.get("busy") and "radio r1 is busy" in r.summary                 # typed busy, not a race


def test_apply_live_reentrant_within_held_guard(tmp_path):
    # Called while THIS thread already holds the band lock (as a Start would) -> no self-deadlock.
    svc = _echo_svc(tmp_path)
    with svc._keys_guard("start", "daemon", ["lifecycle.daemon", "claim.loraham.radio.r1"]):
        r = svc.apply_daemon_params("daemon", rid="r1")
    assert r.ok                                            # re-entrant, completed


def test_apply_live_other_band_not_blocked(tmp_path):
    from lhpc.core import reslock
    svc = _echo_svc(tmp_path)
    # A held 868 lock must NOT block a 433 apply (independent bands).
    with reslock.operation_lock(svc._paths, "claim.loraham.radio.r2", "start", "other"):
        r = svc.apply_daemon_params("daemon", rid="r1")
    assert r.ok


# --- Area 4: canonical daemon-value persistence ----------------------------------------------

@pytest.mark.parametrize("target", ["daemon", "kiss"])
def test_lowercase_enum_canonicalized(tmp_path, target):
    svc = _svc(tmp_path)
    assert svc.save_daemon_params(target, rid="r1", values={"MODE": "fsk", "TXMODE": "direct"}).ok
    st = _dp_of(svc, target, "r1")
    assert st["dp_MODE"] == "FSK"                         # fsk -> FSK (valid, canonical)
    assert st["dp_TXMODE"] == "DIRECT"                    # direct -> DIRECT (differs from default)


def test_leading_zero_int_canonicalized_and_default_equiv_clears(tmp_path):
    from lhpc.core import daemon_params
    svc = _svc(tmp_path)
    assert daemon_params.default_value("kiss", "433", "CADIDLE") == "250"
    assert svc.save_daemon_params("kiss", rid="r1", values={"CADIDLE": "040"}).ok       # 40 != default 250 -> store 40
    assert _dp_of(svc, "kiss", "r1")["dp_CADIDLE"] == "40"
    assert svc.save_daemon_params("kiss", rid="r1", values={"CADIDLE": "0250"}).ok      # == what it inherits -> clear
    assert "dp_CADIDLE" not in _dp_of(svc, "kiss", "r1")


@pytest.mark.parametrize("target", ["daemon", "kiss"])
def test_omitted_key_leaves_override_unchanged(tmp_path, target):
    svc = _svc(tmp_path)
    assert svc.save_daemon_params(target, rid="r1", values={"CADIDLE": "40"}).ok
    assert svc.save_daemon_params(target, rid="r1", values={"MODE": "FSK"}).ok          # CADIDLE omitted -> unchanged
    st = _dp_of(svc, target, "r1")
    assert st["dp_CADIDLE"] == "40" and st["dp_MODE"] == "FSK"


@pytest.mark.parametrize("target", ["daemon", "kiss"])
def test_explicit_blank_clears_only_that_key(tmp_path, target):
    svc = _svc(tmp_path)
    assert svc.save_daemon_params(target, rid="r1", values={"CADIDLE": "40", "MODE": "FSK"}).ok
    assert svc.save_daemon_params(target, rid="r1", values={"CADIDLE": ""}).ok          # blank clears CADIDLE only
    st = _dp_of(svc, target, "r1")
    assert "dp_CADIDLE" not in st and st["dp_MODE"] == "FSK"


@pytest.mark.parametrize("target", ["daemon", "kiss"])
def test_reset_clears_all_profile_keys(tmp_path, target):
    svc = _svc(tmp_path)
    assert svc.save_daemon_params(target, rid="r1", values={"CADIDLE": "40", "MODE": "FSK", "CADWAIT": "1200"}).ok
    assert svc.reset_daemon_params(target, rid="r1").ok
    assert _dp_of(svc, target, "r1") == {}


# --- Area 1: strict ephemeral Start-confirm override validation ------------------------------

def _daemon_svc(tmp_path):
    import os
    b = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    b.parent.mkdir(parents=True); b.write_text("#!/bin/sh\nsleep 0.1\n"); os.chmod(b, 0o755)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


# --- Area 1: strict Start-confirm dp_* field parsing -----------------------------------------

def _daemon_web(web, tmp_path):
    import os
    b = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    b.parent.mkdir(parents=True); b.write_text("#!/bin/sh\n"); os.chmod(b, 0o755)
    return web()


def _start_post(c, extra):
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    c.get("/stacks").get_data(as_text=True)).group(1)
    data = {"_csrf": tok, "op": "start", "target": "daemon", "confirmed": "yes", "p_radio": "433"}
    data.update(extra)
    return c.post("/action", data=data, follow_redirects=True)


# --- in-panel 433/868 band chooser (like the Settings band switch) ---------------------------

def _plain_svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_daemon_params_view_is_one_radios_and_offers_the_radios_it_can_run_on(tmp_path):
    svc = _plain_svc(tmp_path)
    v = svc.daemon_params_view("meshcom", rid="r1")
    assert (v["rid"], v["band"], v["bands"], v["all_bands"]) == ("r1", "433", ["433"], ["433", "868"])   # fixed 433
    assert [r[0] for r in v["radios"]] == ["r1"]
    assert svc.daemon_params_view("meshcore", rid="r2")["bands"] == ["868"]                              # fixed 868
    assert [r[0] for r in svc.daemon_params_view("kiss", rid="r2")["radios"]] == ["r1", "r2"]            # switchable
    assert svc.daemon_params_view("kiss", rid="r2")["band"] == "868"
    assert svc.daemon_params_view("daemon", rid="r1")["bands"] == ["433", "868"]                         # every radio


def test_a_fixed_band_stack_has_no_panel_on_a_radio_it_cannot_run_on(tmp_path):
    # meshcom (433 only) has no settings on the 868 radio: no panel, and a save there is refused by name.
    svc = _plain_svc(tmp_path)
    assert svc.daemon_params_view("meshcom", rid="r2") == {}
    r = svc.save_daemon_params("meshcom", rid="r2", values={"CADIDLE": "40"})
    assert not r.ok and "meshcom has no daemon parameters on r2" in r.summary
    assert "dp_CADIDLE" not in _dp_of(svc, "meshcom", "r2")


def test_meshtastic_direct_spi_has_no_daemon_params_view(tmp_path):
    # Negative case: no panel at all -> the chooser never appears for a non-daemon client.
    assert _plain_svc(tmp_path).daemon_params_view("meshtastic", rid="r1") == {}


def test_daemon_params_radio_chooser_rendered(web):
    # K8d (DEC-W384az, W399 "per-band toggle shall be per-radio"): every stack's panel chooses among the radios able to
    # run it (W3's chooser), never a band
    c = web()

    def dp_seg(sid):
        doc = parse(c.get(f"/stacks?open={sid}").get_data(as_text=True))   # panel is in the lazy body
        switch = doc.within(doc.by_id(f"stack-daemon-params-{sid}")).find("span", class_="bandswitch")
        assert switch, "no radio switch"
        return doc.within(switch[0])

    # fixed-band stack (meshcom, 433): its one radio, r1
    ms = dp_seg("meshcom")
    assert [a.text for a in ms.find("a")] == ["r1 · 433"]
    assert all("radio=r1" in a["href"] and "dp=meshcom" in a["href"] and "band=" not in a["href"] for a in ms.find("a"))
    # switchable stack (kiss): both radios
    assert [a.text for a in dp_seg("kiss").find("a")] == ["r1 · 433", "r2 · 868"]


def test_a_start_applies_the_saved_daemon_params_and_nothing_else(tmp_path):
    # Settings is the only place daemon parameters change; a start applies the SAVED values of its
    # instance on its radio. `_daemon_param_applies` has no per-launch input any more.
    svc = _svc(tmp_path)
    assert svc.save_daemon_params("meshcom", rid="r1", values={"CADIDLE": "40"}).ok
    assert svc._daemon_param_applies("meshcom", "r1")["CADIDLE"] == "40"
    assert _dp_of(svc, "meshcom", "r1")["dp_CADIDLE"] == "40"
    # each radio's row is its own
    assert svc.save_daemon_params("daemon", rid="r1", values={"CADIDLE": "111"}).ok
    assert svc.save_daemon_params("daemon", rid="r2", values={"CADIDLE": "222"}).ok
    assert svc._daemon_param_applies("daemon", "r1")["CADIDLE"] == "111"
    assert svc._daemon_param_applies("daemon", "r2")["CADIDLE"] == "222"


def test_w384az_an_instances_set_wins_over_its_radios_row_which_wins_over_the_default(tmp_path):
    # DEC-W384az C1: kiss@r1 gets its own set over r1's row over kiss's default; kiss@r2 never sees r1's.
    from lhpc.core import daemon_params
    svc = _svc(tmp_path)
    assert svc.save_daemon_params("daemon", rid="r1", values={"CADIDLE": "111", "CADWAIT": "1200"}).ok
    assert svc.save_daemon_params("kiss", rid="r1", values={"CADIDLE": "40"}).ok
    got = svc._daemon_param_applies("kiss", "r1")
    assert (got["CADIDLE"], got["CADWAIT"]) == ("40", "1200")                   # its set, then the row
    assert svc._daemon_param_applies("kiss", "r2")["CADIDLE"] == daemon_params.default_value("kiss", "868", "CADIDLE")
    shown = {r["name"]: r["value"] for r in svc.daemon_params_view("kiss", rid="r1")["rows"]}
    assert (shown["CADIDLE"], shown["CADWAIT"]) == ("40", "1200")               # the panel shows what it gets


def test_w384az_an_instance_can_hold_its_default_against_its_radios_row(tmp_path):
    # DEC-W384az C2: a value equal to what the instance inherits clears; one equal only to the default is kept.
    svc = _svc(tmp_path)
    assert svc.save_daemon_params("daemon", rid="r1", values={"CADIDLE": "111"}).ok
    assert svc.save_daemon_params("kiss", rid="r1", values={"CADIDLE": "250"}).ok     # kiss's default, not the row's
    assert _dp_of(svc, "kiss", "r1")["dp_CADIDLE"] == "250"
    assert svc._daemon_param_applies("kiss", "r1")["CADIDLE"] == "250"
    assert svc.save_daemon_params("kiss", rid="r1", values={"CADIDLE": "111"}).ok     # the row's: inherited
    assert "dp_CADIDLE" not in _dp_of(svc, "kiss", "r1")


def test_w384az_an_old_per_band_value_is_read_nowhere(tmp_path):
    # DEC-W384az: dp_<band>_* in <sid>.toml (and daemon.toml) is no store any more: not applied, not shown, not
    # carried into the radio's start (CHANGELOG: move it to <sid>@<rid> by hand).
    from lhpc.core import config as cfgmod
    from lhpc.core import radio_start
    svc = _svc(tmp_path)
    cfgmod.save_stack_config(svc._paths, "kiss", {"dp_433_CADIDLE": "77"})
    cfgmod.save_stack_config(svc._paths, "daemon", {"dp_433_CADIDLE": "78"})
    for t in ("kiss", "daemon"):
        assert svc._daemon_param_applies(t, "r1")["CADIDLE"] == "250"
        assert {r["name"]: r["value"] for r in svc.daemon_params_view(t, rid="r1")["rows"]}["CADIDLE"] == "250"
    assert radio_start.row_dp(svc, "r1") == {}


@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
def test_crafted_start_post_fields_are_ignored_not_applied(tmp_path, web):
    # A crafted web POST carrying the OLD per-launch fields (dp_<band>_<PARAM>, p_<name>) changes
    # nothing: the start runs the saved configuration, the saved daemon params stay untouched,
    # and no "invalid daemon parameter" refusal exists any more.
    from lhpc.core import config as cfgmod
    c = _daemon_web(web, tmp_path)
    from lhpc.core.services import ControllerService as _CS
    svc = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.save_daemon_params("daemon", rid="r1", values={"CADIDLE": "40"}).ok
    r = _start_post(c, {"dp_433_SF": "99", "dp_433_CADIDLE": "250", "dp_bad": "x"})
    assert b"invalid daemon parameter" not in r.data and b"malformed daemon field" not in r.data
    assert _dp_of(svc, "daemon", "r1")["dp_CADIDLE"] == "40"
    assert not {"dp_433_SF", "dp_433_CADIDLE", "dp_bad", "dp_CADIDLE"} & set(cfgmod.load_stack_config(svc._paths, "daemon"))


@pytest.mark.no_default_hardware
def test_daemon_param_save_refuses_without_radio_hardware(tmp_path):
    """With no radio hardware there is no radio, so a save has nowhere to go: refused by name, nothing written
    (before this, inert `dp__<PARAM>` keys were persisted and reported as saved)."""
    from lhpc.core import commands
    from lhpc.core.config import load_stack_config
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for target in ("daemon", "kiss"):
        r = svc.save_daemon_params(target, rid="r1", values={"MODE": "FSK"})
        assert not r.ok and "r1 is not a radio of the hardware list" in r.summary
        assert load_stack_config(svc._paths, target, inst=commands.Instance("r1")) == {}
    assert not (tmp_path / "config" / "radios.toml").exists()


def _two_stacks(tmp_path):
    """meshcom (fixed 433, so meshcom@r1) running, and kiss running on 868 (kiss@r2) — both daemon clients, both
    radios READY."""
    ready = b"STATUS RADIO=READY TXMODE=MANAGED\n"
    fake = FakeSystem(cmdlines_data={300: ["meshcom-loraham-bridge"], 301: ["qemu-system-xtensa"],
                                     4242: ["loraham-kiss-tnc", "--config", "X"]},
                      unix_replies={"/tmp/loraconf-r1.sock": ready, "/tmp/loraconf-r2.sock": ready})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    launch_band_fixture.set_launch_band(svc, "kiss", "868")   # K6: kiss@r2 runs on 868
    return svc


@pytest.mark.safety("radio-band-exclusive")
def test_app_apply_refused_on_a_radio_another_stack_uses(tmp_path, monkeypatch):
    # meshcom@r1's set must never retune r1 while another stack's instance runs there: refused, it named, no SET.
    from lhpc.core import radio_start
    svc = _two_stacks(tmp_path)
    assert svc.apply_daemon_params("meshcom", rid="r1").data.get("attempted")    # alone on r1: applies
    real = radio_start.radio_clients
    monkeypatch.setattr(radio_start, "radio_clients",
                        lambda s, b, rid, sid: (["chat@r1"], real(s, b, rid, sid)[1]) if rid == "r1" else real(s, b, rid, sid))
    r = svc.apply_daemon_params("meshcom", rid="r1")
    assert not r.ok and "that radio serves chat@r1" in r.summary
    assert r.data["rid"] == "r1" and "attempted" not in r.data
    r = svc.apply_daemon_params("meshcom", rid="r2")                             # no meshcom@r2 runs (or can)
    assert not r.ok and "attempted" not in (r.data or {})


@pytest.mark.safety("radio-band-exclusive")
def test_g1_k8d_n1_the_panel_posts_the_radio_it_shows_and_a_post_without_one_saves_nothing(web, csrf, tmp_path):
    # G1-K8D N1: two radios on 433 (r1, r3) able to run kiss: the panel names the radio it shows (hidden radio, W3) and its
    # save goes there; a post with the band alone is refused and writes nothing (red on 8fcd7efa: no rid field in
    # the form, the save refused)
    import radios_fixture
    from lhpc.core import radios
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r3", "b", "uputronics", "433", ce="ce1")))
    c = web()
    doc = parse(c.get("/stacks?open=kiss").get_data(as_text=True))
    form = doc.within(doc.by_id("stack-daemon-params-kiss"))
    shown = [i["value"] for i in form.find("input") if i.has_attr("name") and i["name"] == "radio"]
    assert shown in (["r1"], ["r3"])
    tok = csrf(c)
    assert c.post("/stacks/kiss/daemon-params", data={"_csrf": tok, "dp_CADIDLE": "40"}).status_code == 400   # no radio
    assert not [n for n in ("kiss@r1.toml", "kiss@r3.toml") if (tmp_path / "config" / "stacks" / n).exists()]
    c.post("/stacks/kiss/daemon-params", data={"_csrf": tok, "radio": shown[0], "dp_CADIDLE": "40"})
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert _dp_of(svc, "kiss", shown[0])["dp_CADIDLE"] == "40"


def test_g1_k8d_n2_apply_is_offered_only_while_the_instance_runs_alone_on_its_radio(tmp_path, monkeypatch):
    # G1-K8D N2: kiss@r1 runs and another stack's instance too: the panel does not offer Apply (red on 8fcd7efa:
    # can_apply True, the Apply then refused)
    from lhpc.core import radio_start
    svc = _plain_svc(tmp_path)
    monkeypatch.setattr(radio_start, "radio_clients", lambda s, b, rid, sid: (["chat@r1"], True))
    assert svc.daemon_params_view("kiss", rid="r1")["can_apply"] is False
    monkeypatch.setattr(radio_start, "radio_clients", lambda s, b, rid, sid: ([], True))
    assert svc.daemon_params_view("kiss", rid="r1")["can_apply"] is True


def test_app_apply_holds_the_band_claim_against_a_competing_start(tmp_path, monkeypatch):
    # The conflict check and every SET run under `claim.loraham.radio.<rid>`: a start that tries to
    # take the radio right after the check is refused while the apply holds it — the apply never
    # retunes a radio that start would then use.
    import threading

    from lhpc.core import radio_start, reslock
    svc = _two_stacks(tmp_path)
    refused = []

    def start():
        try:
            with reslock.operation_lock(svc._paths, "claim.loraham.radio.r1", "start", "meshcore"):
                pass
        except reslock.ResourceBusy:
            refused.append("meshcore")
    real = radio_start.radio_clients
    calls = []

    def used(s, b, rid, sid):
        out = real(s, b, rid, sid)
        calls.append(rid)
        if len(calls) == 2:                     # the re-check under the claim
            t = threading.Thread(target=start)
            t.start()
            t.join()
        return out
    monkeypatch.setattr(radio_start, "radio_clients", used)
    assert svc.apply_daemon_params("meshcom", rid="r1").data.get("attempted")
    assert refused == ["meshcore"]
