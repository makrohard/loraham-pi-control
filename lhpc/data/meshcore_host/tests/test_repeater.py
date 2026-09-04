"""The repeater roles' translation of LHPC's TOML into upstream's in-memory config, and the bits of
lifecycle LHPC owns around the injected radio. Pure-Python: no upstream import needed except where
marked (skipped when openhop_repeater is not installed in this interpreter)."""
from __future__ import annotations

import pytest

from meshcore_host import repeater as rep
from meshcore_host.config import HostConfig, load_config

SEED = "11" * 32
KEY64 = "22" * 64


def _cfg(**over) -> HostConfig:
    cfg = HostConfig(name="Chat 1", allow="127.0.0.1", bind="127.0.0.1", port=5000, key=SEED,
                     frequency=869618000, bandwidth=62500, spreading_factor=8, coding_rate=8,
                     txpower=14, preamble=16, airtime=10.0, mode="chat+repeater",
                     repeater_name="Relay 1", repeater_key=KEY64, repeater_behaviour="forward",
                     dashboard_password="x" * 24, repeater_state_dir="/rt/state/openhop")
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def test_identity_is_passed_as_bytes_of_the_right_length():
    conf = rep.build_upstream_config(_cfg())
    key = conf["repeater"]["identity_key"]
    assert isinstance(key, bytes) and len(key) == 64
    assert isinstance(rep.build_upstream_config(_cfg(repeater_key=SEED))["repeater"]["identity_key"], bytes)
    with pytest.raises(Exception):
        rep.seed_bytes("zz")                          # not hex, not a valid length -> fail closed


def test_upstream_config_is_translated_from_lhpcs_one_file():
    conf = rep.build_upstream_config(_cfg())
    assert conf["repeater"]["node_name"] == "Relay 1"
    assert conf["repeater"]["mode"] == "forward"
    assert conf["repeater"]["security"]["admin_password"] == "x" * 24
    assert conf["radio_type"] == "loraham"                        # non-disabled: no setup wizard
    assert conf["radio"]["preamble_length"] == 16                 # upstream's name for `preamble`
    assert conf["radio"]["frequency"] == 869618000 and conf["radio"]["tx_power"] == 14
    assert conf["duty_cycle"]["max_airtime_per_minute"] == 6000   # 10 % of a minute, in ms
    assert conf["http"] == {"enabled": True, "host": "127.0.0.1", "port": 8000}
    assert conf["storage"]["storage_dir"] == "/rt/state/openhop"
    assert conf["mqtt_brokers"] == {} and conf["glass"] == {"enabled": False}
    assert conf["gps"] == {"enabled": False, "time_sync_enabled": False}


def test_the_chat_node_is_the_one_hosted_companion_only_in_chat_plus_repeater():
    both = rep.build_upstream_config(_cfg())["identities"]["companions"]
    assert len(both) == 1
    c = both[0]
    assert c["name"] == "Chat 1" and c["identity_key"] == SEED
    assert c["settings"] == {"node_name": "Chat 1", "bind_address": "127.0.0.1",
                             "tcp_port": 5000, "tcp_timeout": 0}
    assert rep.build_upstream_config(_cfg(mode="repeater"))["identities"]["companions"] == []


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
name = "%s"
key = "{KEY64}"
behaviour = "forward"
admin_password = "{'x' * 24}"
state_dir = "/rt/state/openhop"
'''
    chat_key = f'key = "{SEED}"'
    p = tmp_path / "c.toml"
    p.write_text(base % (chat_key, "chat", ""))
    assert load_config(p).mode == "chat"                          # chat ignores the repeater rows
    p.write_text(base % ("", "repeater", ""))
    with pytest.raises(Exception, match="name is required"):
        load_config(p)
    p.write_text(base % ("", "repeater", "Relay 1"))
    cfg = load_config(p)
    assert cfg.repeater_on and not cfg.companion_on and cfg.repeater_name == "Relay 1"
    p.write_text(base % ("", "bogus", "Relay 1"))
    with pytest.raises(Exception, match="role must be one of"):
        load_config(p)
    # The chat identity follows the Companion: required (exactly one of key/key_file) wherever
    # the Companion runs, refused in the pure repeater, which has no Companion to own it.
    for role in ("chat", "chat+repeater"):
        p.write_text(base % ("", role, "Relay 1"))
        with pytest.raises(Exception, match="exactly one of key_file or key"):
            load_config(p)
        p.write_text(base % (chat_key, role, "Relay 1"))
        assert load_config(p).companion_on
    p.write_text(base % (chat_key, "repeater", "Relay 1"))
    with pytest.raises(Exception, match="not used in the repeater role"):
        load_config(p)
    p.write_text(base % ('key_file = "/x/k"', "repeater", "Relay 1"))
    with pytest.raises(Exception, match="not used in the repeater role"):
        load_config(p)


def test_the_daemon_gets_no_config_path_and_the_radio_hooks(monkeypatch):
    pytest.importorskip("repeater.main")
    host = rep._Host(_cfg())
    assert host.daemon.config_path is None                        # explicit: never /etc
    assert host.daemon.radio is host.radio
    assert host.daemon.radio_status == "degraded"
    host.radio.on_link_state(True, True)
    assert host.daemon.radio_status == "ok"
    host.radio.on_link_state(False, False)
    assert host.daemon.radio_status == "degraded"


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
