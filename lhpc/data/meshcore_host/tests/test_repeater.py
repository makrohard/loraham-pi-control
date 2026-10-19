"""The repeater roles' translation of LHPC's TOML into upstream's in-memory config, and the bits of
lifecycle LHPC owns around the injected radio. Pure-Python: no upstream import needed except where
marked (skipped when openhop_repeater is not installed in this interpreter)."""
from __future__ import annotations

import pytest

from meshcore_host import repeater as rep
from meshcore_host.config import HostConfig, load_config

SEED = "11" * 32


def _cfg(**over) -> HostConfig:
    cfg = HostConfig(name="Chat 1", allow="127.0.0.1", bind="127.0.0.1", port=5000, key=SEED,
                     frequency=869618000, bandwidth=62500, spreading_factor=8, coding_rate=8,
                     txpower=14, preamble=16, mode="chat+repeater", jwt_secret="j" * 64,
                     repeater_state_dir="/rt/state/openhop", use_gps=True,
                     gps_file="/run/lhpc/meshcore-gps.nmea", restart_argv=["lhpc", "restart"])
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _user_file():
    """openHop's file as a user left it: their settings, the one-store values, stale LHPC keys."""
    return {"repeater": {"node_name": "Relay 1", "mode": "monitor", "identity_key": bytes(64),
                         "security": {"admin_password": "p" * 15, "jwt_secret": "stale"}},
            "radio": {"frequency": 868100000}, "kiss": {"port": "/dev/ttyUSB0"},
            "http": {"host": "0.0.0.0", "port": 8000}, "mesh": {"default_region": "eu"},
            "mqtt_brokers": {"brokers": [{"preset": "waev"}]}, "glass": {"enabled": True},
            "gps": {"enabled": True, "source": "serial", "time_sync_enabled": True},
            "duty_cycle": {"enforcement_enabled": True, "max_airtime_per_minute": 3600},
            "identities": {"companions": [{"name": "Mine", "identity_key": "33" * 32}],
                           "room_servers": [{"name": "Room"}]}}


def test_lhpcs_values_replace_its_keys_and_the_users_stay():
    conf = rep.apply_overlay(_user_file(), _cfg())
    assert list(conf)[0] == "lhpc_managed"
    assert conf["radio_type"] == "external" and conf["radio"]["frequency"] == 869618000
    assert conf["radio"]["preamble_length"] == 16 and conf["radio"]["tx_power"] == 14
    assert conf["http"] == {"enabled": True, "host": "127.0.0.1", "port": 8000}
    assert conf["storage"]["storage_dir"] == "/rt/state/openhop" and "kiss" not in conf
    assert conf["glass"] == {"enabled": True, "cert_store_dir": "/rt/state/openhop/glass"}
    assert conf["setup_completed"] is True
    assert conf["repeater"]["security"] == {"admin_password": "p" * 15, "jwt_secret": "j" * 64}
    # one store and the user's: untouched
    assert conf["repeater"]["node_name"] == "Relay 1" and conf["repeater"]["mode"] == "monitor"
    assert conf["duty_cycle"]["max_airtime_per_minute"] == 3600
    assert conf["mesh"] == {"default_region": "eu"} and conf["mqtt_brokers"]["brokers"]
    assert conf["identities"]["room_servers"] == [{"name": "Room"}]


def test_gps_is_lhpcs_only_while_use_gps_is_on():
    on = rep.apply_overlay(_user_file(), _cfg())["gps"]
    assert on["source"] == "file" and on["source_path"] == "/run/lhpc/meshcore-gps.nmea"
    assert on["time_sync_enabled"] is False and on["persist_gps_fix_to_config"] is False
    off = rep.apply_overlay(_user_file(), _cfg(use_gps=False))["gps"]
    assert off == {"enabled": True, "source": "serial", "time_sync_enabled": False}


def test_the_chat_node_is_found_by_key_and_user_companions_stay():
    """gate 2 r2a N6: an existing entry with the chat node's key under another name is found by
    that key and updated in place (no second entry); the user companion stays as it was, whole."""
    user = _user_file()
    mine = dict(user["identities"]["companions"][0])
    user["identities"]["companions"].append({"name": "Old chat", "identity_key": SEED})
    comps = rep.apply_overlay(user, _cfg())["identities"]["companions"]
    assert [c["name"] for c in comps] == ["Mine", "Chat 1"]
    assert comps[0] == mine
    assert comps[1]["identity_key"] == SEED
    assert comps[1]["settings"] == {"node_name": "Chat 1", "bind_address": "127.0.0.1",
                                    "tcp_port": 5000, "tcp_timeout": 0}


def test_a_rename_renames_the_entry_and_leaves_no_stale_one():
    conf = rep.apply_overlay(_user_file(), _cfg())
    renamed = rep.apply_overlay(conf, _cfg(name="Chat 2"))["identities"]["companions"]
    assert [c["name"] for c in renamed] == ["Mine", "Chat 2"]


def test_a_user_companion_with_the_chat_nodes_name_refuses_the_start():
    with pytest.raises(rep.ConfigError, match="already uses this name with a different identity key"):
        rep.apply_overlay(_user_file(), _cfg(name="Mine"))


def test_mode_transitions_add_and_remove_only_the_chat_node():
    conf = rep.apply_overlay(_user_file(), _cfg())
    alone = rep.apply_overlay(conf, _cfg(mode="repeater", key="", chat_node_id=rep.ofile.key_id(SEED)))
    assert [c["name"] for c in alone["identities"]["companions"]] == ["Mine"]
    back = rep.apply_overlay(alone, _cfg())["identities"]["companions"]      # r2a N1: from the result
    assert [c["name"] for c in back] == ["Mine", "Chat 1"]


@pytest.mark.parametrize("change, match", [
    ({"node_name": ""}, "node_name in openHop.s file is empty"),
    ({"identity_key": "ab" * 32}, "not valid key material"),
    ({"identity_key": bytes(10)}, "not valid key material"),
    ({"mode": "relay"}, "mode in openHop's file must be one of"),
])
def test_one_store_values_upstream_cannot_run_with_refuse_the_start(change, match):
    conf = _user_file()
    conf["repeater"].update(change)
    with pytest.raises(Exception, match=match):
        rep.check_one_store(conf)


def test_one_store_values_lhpc_would_not_choose_still_start():
    conf = _user_file()
    conf["repeater"]["security"]["admin_password"] = "short123"      # upstream's >= 8, not LHPC's 15
    rep.check_one_store(conf)


def test_the_pinned_radio_presets_are_placed_where_upstream_reads_them(tmp_path):
    src = tmp_path / "src" / "radio-presets.json"
    src.parent.mkdir()
    src.write_bytes(b'{"config": {"suggested_radio_settings": {"entries": []}}}\n')
    state = tmp_path / "state" / "openhop"                         # absent before the first start
    cfg = _cfg(presets_file=str(src), repeater_state_dir=str(state))
    rep.install_presets(cfg)
    assert (state / "radio-presets.json").read_bytes() == src.read_bytes()
    src.write_bytes(b'{"moved": "with the pin"}\n')                 # the next start follows the pin
    rep.install_presets(cfg)
    assert (state / "radio-presets.json").read_bytes() == b'{"moved": "with the pin"}\n'


def test_a_missing_presets_source_neither_fails_the_start_nor_writes(tmp_path):
    state = tmp_path / "state" / "openhop"
    rep.install_presets(_cfg(presets_file=str(tmp_path / "absent.json"), repeater_state_dir=str(state)))
    assert not (state / "radio-presets.json").exists()


def test_radio_status_follows_the_link_state():
    assert rep.radio_status(True, True, True) == "ok"
    assert rep.radio_status(True, False, True) == "degraded"       # TX enabled, handshake pending
    assert rep.radio_status(True, False, False) == "ok"            # RX-only: link alone suffices
    assert rep.radio_status(False, True, True) == "degraded"


def test_config_loader_validates_the_repeater_table_only_in_repeater_roles(tmp_path):
    base = f'''
[companion]
name = "Chat 1"
[identity]
%s
[radio]
preset = "eu_uk_narrow"
[repeater]
role = "%s"
jwt_secret = "{'j' * 64}"
state_dir = "/rt/state/openhop"
use_gps = "on"
gps_file = "/run/lhpc/meshcore-gps.nmea"
restart_cmd = "lhpc stack restart meshcore --yes"
'''
    chat_key = f'key = "{SEED}"'
    p = tmp_path / "c.toml"
    p.write_text(base % (chat_key, "chat"))
    assert load_config(p).mode == "chat"                          # chat ignores the repeater rows
    p.write_text(base % ("", "repeater"))
    cfg = load_config(p)
    assert cfg.repeater_on and not cfg.companion_on and cfg.jwt_secret == "j" * 64
    assert cfg.use_gps and cfg.restart_argv == ["lhpc", "stack", "restart", "meshcore", "--yes"]
    for drop, match in (('jwt_secret = "' + "j" * 64 + '"\n', "jwt_secret is required"),
                        ('restart_cmd = "lhpc stack restart meshcore --yes"\n', "restart_cmd is required"),
                        ('gps_file = "/run/lhpc/meshcore-gps.nmea"\n', "needs an absolute gps_file")):
        p.write_text((base % ("", "repeater")).replace(drop, ""))
        with pytest.raises(Exception, match=match):
            load_config(p)
    p.write_text((base % ("", "repeater")).replace('use_gps = "on"', 'use_gps = "yes"'))
    with pytest.raises(Exception, match="use_gps must be on or off"):
        load_config(p)
    p.write_text(base % ("", "bogus"))
    with pytest.raises(Exception, match="role must be one of"):
        load_config(p)
    for role in ("chat", "chat+repeater"):
        p.write_text(base % ("", role))
        with pytest.raises(Exception, match="exactly one of key_file or key"):
            load_config(p)
        p.write_text(base % (chat_key, role))
        assert load_config(p).companion_on
    p.write_text(base % (chat_key, "repeater"))
    with pytest.raises(Exception, match="not used in the repeater role"):
        load_config(p)


def test_the_host_reads_openhops_file_overlays_and_writes_it_back(tmp_path):
    pytest.importorskip("repeater.main")
    import yaml
    from meshcore_host import openhop_file
    state = tmp_path / "state" / "openhop"
    state.mkdir(parents=True)
    path = openhop_file.path_in(state)
    path.write_text(yaml.safe_dump(_user_file()))
    host = rep._Host(_cfg(repeater_state_dir=str(state), gps_file=str(tmp_path / "gps.nmea"),
                          cli_lock=str(tmp_path / "cli.lock")))
    assert host.daemon.config_path == str(path)                  # openHop's own file, set explicitly
    written = yaml.safe_load(path.read_text())
    assert list(written)[0] == "lhpc_managed" and written["radio"]["frequency"] == 869618000
    assert written["mesh"] == {"default_region": "eu"} and written["repeater"]["mode"] == "monitor"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert host.daemon.radio is host.radio and host.daemon.radio_status == "degraded"
    host.radio.on_link_state(True, True)
    assert host.daemon.radio_status == "ok"


def test_a_missing_openhop_file_refuses_the_start(tmp_path):
    pytest.importorskip("repeater.main")
    with pytest.raises(rep.ConfigError, match="config file .* is missing"):
        rep._Host(_cfg(repeater_state_dir=str(tmp_path)))


def test_the_self_check_runs_before_anything_is_read_or_written(tmp_path, monkeypatch):
    from meshcore_host import guards
    seen = []
    monkeypatch.setattr(guards, "self_check", lambda: (seen.append("check"), _raise())[1])
    monkeypatch.setattr(rep, "load_openhop_config", lambda cfg: seen.append("load"))
    with pytest.raises(rep.ConfigError, match="not supported"):
        rep._Host(_cfg(repeater_state_dir=str(tmp_path)))
    assert seen == ["check"] and not list(tmp_path.iterdir())


def _raise():
    raise rep.ConfigError("this openHop version is not supported by this LHPC (missing x)")


def test_the_radio_speaks_upstreams_attribute_names_for_tx_power():
    """openhop_repeater caches `radio.tx_power` for the hosted Companion's self-info and
    openhop_core resolves `max_tx_power_dbm`; the adapter's own spellings are txpower/txmaxpower."""
    from meshcore_host.loraham_radio import LoRaHAMRadio
    import inspect
    sig = inspect.signature(LoRaHAMRadio.__init__)
    kw = {"frequency": 869618000, "spreading_factor": 8, "bandwidth": 62500, "coding_rate": 8,
          "preamble_length": 16, "syncword": 0x12, "txpower": 17, "txmaxpower": 20}
    kw = {k: v for k, v in kw.items() if k in sig.parameters}
    radio = LoRaHAMRadio(data_socket="/tmp/x.sock", config_socket="/tmp/y.sock", **kw) \
        if "data_socket" in sig.parameters else LoRaHAMRadio(**kw)
    assert radio.tx_power == radio.txpower == 17
    assert radio.max_tx_power_dbm == radio.txmaxpower == 20


def test_the_pure_repeater_removes_the_chat_node_by_the_controllers_handle_only():
    """gate 1 5l F2: the handle comes from LHPC (chat_node_id), never from openHop's file, so an
    import or Glass write of a planted marker cannot pick which companion gets removed."""
    conf = rep.apply_overlay(_user_file(), _cfg())
    assert "chat_node" not in conf[rep.ofile.MARKER_KEY]
    conf[rep.ofile.MARKER_KEY]["chat_node"] = rep.ofile.key_id("33" * 32)      # planted on "Mine": ignored
    kept = rep.apply_overlay(conf, _cfg(mode="repeater", key=""))["identities"]["companions"]
    assert [c["name"] for c in kept] == ["Mine", "Chat 1"]                     # no handle: nothing removed
    alone = rep.apply_overlay(conf, _cfg(mode="repeater", key="", chat_node_id=rep.ofile.key_id(SEED)))
    assert [c["name"] for c in alone["identities"]["companions"]] == ["Mine"]
