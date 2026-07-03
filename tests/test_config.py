"""Tests for layered configuration (defaults + runtime-local overrides + secrets)."""

from __future__ import annotations

from pathlib import Path

from lhpc.core.config import load_config, load_secrets
from lhpc.core.paths import Paths


def _paths(tmp_path: Path) -> Paths:
    return Paths(runtime_root=tmp_path)


def test_defaults_loaded(tmp_path):
    cfg = load_config(_paths(tmp_path))
    assert cfg.get("web", "port") == 8770
    assert cfg.get("install", "source_strategy") == "adopt"


def test_operator_absent_by_default(tmp_path):
    cfg = load_config(_paths(tmp_path))
    assert not cfg.operator.configured
    assert cfg.operator.callsign == ""


def test_local_overrides_merge(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text(
        '[operator]\ncallsign = "OE1XYZ"\nlocator = "JN88"\n[web]\nport = 9999\n'
    )
    cfg = load_config(_paths(tmp_path))
    assert cfg.operator.callsign == "OE1XYZ" and cfg.operator.locator == "JN88"
    assert cfg.operator.configured
    assert cfg.get("web", "port") == 9999          # override wins
    assert cfg.get("install", "source_strategy") == "adopt"  # default preserved


def test_stack_config_roundtrip(tmp_path):
    from lhpc.core.config import load_stack_config, save_stack_config
    paths = _paths(tmp_path)
    save_stack_config(paths, "daemon", {"radio": "433", "cadrssi_433": "-95"})
    loaded = load_stack_config(paths, "daemon")
    assert loaded["radio"] == "433" and loaded["cadrssi_433"] == "-95"


def test_save_stack_config_validates(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    bad = svc.save_stack_config("daemon", {"cadrssi_433": "999"})   # out of range
    assert not bad.ok
    good = svc.save_stack_config("daemon", {"radio": "868", "cadrssi_433": "-100"})
    assert good.ok and svc.stack_config("daemon")["radio"] == "868"


def test_save_operator_preserves_callsign_locally(tmp_path):
    from lhpc.core.config import save_operator_config
    paths = _paths(tmp_path)
    save_operator_config(paths, "n0call-10", "AA00aa")
    cfg = load_config(paths)
    assert cfg.operator.callsign == "n0call-10" and cfg.operator.locator == "AA00aa"


def test_config_view_splits_basic_advanced_and_operator(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    view = svc.config_view("daemon")
    assert view["operator"] is None          # daemon does not consume callsign/locator
    # The daemon's start options (radio/tx/CAD/…) are NOT on the Config page — they
    # are chosen on confirm:start. The page carries the live tuning settings instead.
    assert view["components"] == []
    assert "live_settings" in view
    # a stack that still shows the shared Operator box (voice substitutes {callsign}).
    assert svc.config_view("voice")["operator"] is not None
    # iGate now edits its callsign in its own config -> no shared Operator box, but its run
    # params still split into basic/advanced.
    igate = svc.config_view("igate")
    assert igate["operator"] is None
    params = igate["components"][0]["params"]
    assert any(p.advanced for p in params) and any(not p.advanced for p in params)


def test_save_config_writes_operator_and_params(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    r = svc.save_config("igate", {"tx_freq": "434.000"}, callsign="oe1abc", locator="JN88")
    assert r.ok
    assert svc.config().operator.callsign == "OE1ABC"   # global operator saved (normalised upper)
    assert svc.config_view("igate")["values"]["tx_freq"] == "434.000"


def test_save_warns_apply_workflow_and_reset(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    r = svc.save_config("igate", {"tx_freq": "434.000"})    # start-time change
    assert r.ok and any("Run" in d or "Restart" in d for d in r.details)
    assert svc.stack_config("igate")["tx_freq"] == "434.000"
    rr = svc.reset_config("igate")                          # back to running defaults
    assert rr.ok and svc.stack_config("igate")["tx_freq"] == "433.900"


def test_igate_params_match_source_options(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    names = {p.name for p in svc.run_params_for("igate")}
    # only real iGate options are exposed (verified against loraham_iGate_106.c)
    assert {"tx_freq", "rx_freq", "lat", "lon", "symbol", "digipeat"} <= names
    assert {"is_interval", "rf_interval", "relay", "repeater"} <= names


def test_remaining_stacks_expose_real_cli_options(tmp_path):
    from lhpc.core.model import emit_param
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))

    def names(stack, comp):
        return {p.name for p in svc.stack(stack).component(comp).run_params}

    # only the verified-real CLI options are exposed
    assert {"kiss_port", "rx_freq", "tx_freq", "data_socket", "conf_socket"} <= names("kiss", "loraham-kiss-tnc")
    assert {"port", "bind", "backend"} <= names("meshcom", "meshcom-bridge")
    assert {"host", "port"} <= names("meshcore", "meshcore-cli")
    assert {"env"} <= names("meshcom", "meshcom-qemu")

    # a saved value flows into the effective run command
    svc.save_config("meshcom", {"port": "7001", "backend": "fake"})
    c = svc.stack("meshcom").component("meshcom-bridge")
    vals = svc.stack_config("meshcom")
    # emit_param now returns argv TOKENS (option and value are separate entries).
    tokens = []
    for p in c.run_params:
        tokens += emit_param(p, vals[p.name])
    assert tokens[tokens.index("--port") + 1] == "7001"
    assert tokens[tokens.index("--backend") + 1] == "fake"


def test_update_toml_uncomments_sets_and_skips_blank():
    from lhpc.core.config import update_toml
    from lhpc.core.model import FileParam
    base = '[interface.x]\npreset = "a"\n# txpower = 14\n[device.y]\nname = "old"\n'
    params = [
        FileParam("preset", "preset", "interface.x", kind="enum", default="a"),
        FileParam("txpower", "txpower", "interface.x", kind="int", default=""),
        FileParam("node", "name", "device.y", kind="str", default="old"),
    ]
    out = update_toml(base, params, {"preset": "b", "txpower": "", "node": "new"}, lambda s: s)
    assert 'preset = "b"' in out          # enum updated
    assert "# txpower = 14" in out        # blank -> base/commented left as-is
    assert 'name = "new"' in out          # nested-section key updated
    out2 = update_toml(base, params, {"txpower": "17"}, lambda s: s)
    assert "txpower = 17" in out2 and "# txpower" not in out2   # set -> uncommented


def test_render_keyval_file():
    from lhpc.core.config import render_keyval
    from lhpc.core.model import FileParam
    params = [FileParam("call", "CALL", kind="str", default="N0CALL"),
              FileParam("dbg", "DEBUG", kind="flag", default="on")]
    text = render_keyval(params, {"call": "N0CALL-10"}, lambda s: s)
    assert "CALL = N0CALL-10" in text and "DEBUG = 1" in text


def test_meshcore_file_config_exposed(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    names = {p.name for p in svc.config_view("meshcore")["file_params"]}
    assert {"preset", "enable_tx", "node_name", "txpower"} <= names


def test_secrets_loaded_separately(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "secrets.toml").write_text('[meshcom]\nbridge_password = "x"\n')
    secrets = load_secrets(_paths(tmp_path))
    assert secrets["meshcom"]["bridge_password"] == "x"
    # Secrets never leak into the effective config.
    cfg = load_config(_paths(tmp_path))
    assert "meshcom" not in cfg.values


def test_run_param_default_uses_operator_callsign(tmp_path):
    # The Start-page default for an operator-token run-param (igate 'call' = '{callsign}')
    # must resolve to the configured operator callsign — matching the Config page — not
    # show the literal placeholder. A SAVED value is used verbatim.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.config import save_operator_config, save_stack_config
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    save_operator_config(svc._paths, "DL1ABC", "JO31"); svc._config = None
    assert svc.stack_config("igate")["call"] == "DL1ABC"      # default substituted, not '{callsign}'
    # an explicitly saved value is NOT re-substituted
    save_stack_config(svc._paths, "igate", {"call": "DK0XYZ"})
    assert svc.stack_config("igate")["call"] == "DK0XYZ"


def test_run_param_default_empty_when_operator_unset(tmp_path):
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.stack_config("igate")["call"] == ""           # no '{callsign}' literal leaks


def test_load_config_ignores_symlinked_local_toml(tmp_path):
    # A symlinked runtime local.toml must never contribute data from outside the root.
    import os
    from lhpc.core.config import load_config
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir()
    outside = tmp_path / "evil.toml"; outside.write_text('[operator]\ncallsign = "EVIL"\n')
    os.symlink(outside, tmp_path / "config" / "local.toml")
    cfg = load_config(Paths(runtime_root=tmp_path))
    assert cfg.operator.callsign != "EVIL"          # symlinked-out data never contributes
    assert cfg.diagnostics                          # surfaced as a diagnostic, not a crash


def test_known_working_store_skips_symlinked_leaf(tmp_path):
    import os
    from lhpc.core import known_working
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    sp = known_working.store_path(paths, "s"); sp.parent.mkdir(parents=True)
    outside = tmp_path / "evil.json"
    outside.write_text('{"version": 1, "compositions": []}')
    os.symlink(outside, sp)                         # symlinked store leaf
    assert known_working.load(paths, "s") == []     # contributes nothing


def test_known_working_symlinked_dir_is_empty(tmp_path):
    import os
    from lhpc.core import known_working
    from lhpc.core.paths import Paths
    rt = tmp_path / "rt"; rt.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "known-working").mkdir()
    os.symlink(outside, rt / "profiles")            # profiles/ -> outside the runtime root
    assert known_working.load(Paths(runtime_root=rt), "s") == []


# --- update_stack_config: preserve manual typed scalars during daemon-param saves ----------

def _seed_stack_toml(paths, stack_id, raw, band=""):
    from lhpc.core.config import _stack_config_path
    p = _stack_config_path(paths, stack_id, band)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw)
    return p


def test_update_preserves_manual_bool_int_float(tmp_path):
    from lhpc.core.config import update_stack_config, load_stack_config
    paths = _paths(tmp_path)
    _seed_stack_toml(paths, "meshcom", 'my_flag = true\nmy_int = 42\nmy_float = 1.5\n')
    update_stack_config(paths, "meshcom", {"dp_433_CADIDLE": "40"})
    cfg = load_stack_config(paths, "meshcom")
    assert cfg["my_flag"] is True                                         # bool kept
    assert cfg["my_int"] == 42 and type(cfg["my_int"]) is int             # int kept (not bool/str)
    assert cfg["my_float"] == 1.5 and type(cfg["my_float"]) is float      # finite float kept
    assert cfg["dp_433_CADIDLE"] == "40"                                  # daemon param stays str


def test_update_preserves_unrelated_strings_and_other_band(tmp_path):
    from lhpc.core.config import update_stack_config, load_stack_config
    paths = _paths(tmp_path)
    _seed_stack_toml(paths, "voice", 'autostart_x = "on"\nc_foo = "bar"\ndp_868_CADIDLE = "77"\n')
    update_stack_config(paths, "voice", {"dp_433_CADIDLE": "40"})
    cfg = load_stack_config(paths, "voice")
    assert cfg["autostart_x"] == "on" and cfg["c_foo"] == "bar"           # unrelated strings kept
    assert cfg["dp_868_CADIDLE"] == "77" and cfg["dp_433_CADIDLE"] == "40"  # other band kept


def test_update_clear_removes_only_requested_key(tmp_path):
    from lhpc.core.config import update_stack_config, load_stack_config
    paths = _paths(tmp_path)
    _seed_stack_toml(paths, "voice",
                     'my_flag = true\ndp_433_CADIDLE = "40"\ndp_868_CADIDLE = "77"\n')
    update_stack_config(paths, "voice", {"dp_433_CADIDLE": ""})           # "" clears 433 only
    cfg = load_stack_config(paths, "voice")
    assert "dp_433_CADIDLE" not in cfg
    assert cfg["dp_868_CADIDLE"] == "77" and cfg["my_flag"] is True       # everything else kept


def test_update_rejects_list_value_and_leaves_file_unchanged(tmp_path):
    import pytest
    from lhpc.core.config import update_stack_config, ConfigError
    paths = _paths(tmp_path)
    p = _seed_stack_toml(paths, "meshcom", 'bad = [1, 2]\nkeep = "x"\n')
    before = p.read_text()
    with pytest.raises(ConfigError):
        update_stack_config(paths, "meshcom", {"dp_433_CADIDLE": "40"})
    assert p.read_text() == before                                       # original untouched


def test_update_rejects_table_value_and_leaves_file_unchanged(tmp_path):
    import pytest
    from lhpc.core.config import update_stack_config, ConfigError
    paths = _paths(tmp_path)
    p = _seed_stack_toml(paths, "meshcom", '[nested]\nx = 1\n')
    before = p.read_text()
    with pytest.raises(ConfigError):
        update_stack_config(paths, "meshcom", {"dp_433_CADIDLE": "40"})
    assert p.read_text() == before


def test_update_rejects_nan_and_inf(tmp_path):
    import pytest
    from lhpc.core.config import update_stack_config, ConfigError
    paths = _paths(tmp_path)
    for raw in ("bad = nan\n", "bad = inf\n", "bad = -inf\n"):
        p = _seed_stack_toml(paths, "meshcom", raw)
        before = p.read_text()
        with pytest.raises(ConfigError):
            update_stack_config(paths, "meshcom", {"dp_433_CADIDLE": "40"})
        assert p.read_text() == before


def test_update_string_only_config_behavior_unchanged(tmp_path):
    from lhpc.core.config import update_stack_config, load_stack_config
    paths = _paths(tmp_path)
    _seed_stack_toml(paths, "kiss", 'radio = "433"\nautostart_x = "on"\n')
    update_stack_config(paths, "kiss", {"dp_433_CADWAIT": "1200"})
    cfg = load_stack_config(paths, "kiss")
    assert cfg == {"radio": "433", "autostart_x": "on", "dp_433_CADWAIT": "1200"}   # all strings


def test_manual_bool_survives_daemon_param_save_end_to_end(tmp_path):
    # The full path: services.save_daemon_params -> update_stack_config keeps a manual bool.
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.config import load_stack_config
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    _seed_stack_toml(svc._paths, "meshcom", 'operator_ready = true\nretries = 3\n')
    assert svc.save_daemon_params("meshcom", "433", {"CADIDLE": "40"}).ok
    cfg = load_stack_config(svc._paths, "meshcom")
    assert cfg["operator_ready"] is True and cfg["retries"] == 3          # typed values survive
    assert cfg["dp_433_CADIDLE"] == "40"


# --- Area 1: normal-config vs daemon-profile ownership (merge, never full-replace) ------------

def _svc(tmp_path):
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    return ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))


def test_daemon_override_survives_save_config(tmp_path):
    from lhpc.core.config import load_stack_config
    svc = _svc(tmp_path)
    svc.save_daemon_params("meshcom", "433", {"CADIDLE": "40"})
    assert svc.save_config("meshcom", {}).ok                          # normal save (no run change)
    assert load_stack_config(svc._paths, "meshcom")["dp_433_CADIDLE"] == "40"


def test_daemon_override_survives_save_config_bundle(tmp_path):
    from lhpc.core.config import load_stack_config
    svc = _svc(tmp_path)
    svc.save_daemon_params("meshcom", "433", {"CADIDLE": "40"})
    assert svc.save_config_bundle("meshcom", values={}, remotes={}).ok
    assert load_stack_config(svc._paths, "meshcom")["dp_433_CADIDLE"] == "40"


def test_daemon_override_survives_public_save_stack_config(tmp_path):
    from lhpc.core.config import load_stack_config, _stack_config_path
    svc = _svc(tmp_path)
    p = _stack_config_path(svc._paths, "daemon", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('dp_433_CADIDLE = "40"\n')
    assert svc.save_stack_config("daemon", {"radio": "868"}).ok       # a normal run param
    stored = load_stack_config(svc._paths, "daemon")
    assert stored["radio"] == "868" and stored["dp_433_CADIDLE"] == "40"


def test_daemon_override_survives_normal_reset(tmp_path):
    from lhpc.core.config import load_stack_config, _stack_config_path
    svc = _svc(tmp_path)
    p = _stack_config_path(svc._paths, "daemon", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('radio = "868"\ndp_433_CADIDLE = "40"\n')
    assert svc.reset_config("daemon").ok
    stored = load_stack_config(svc._paths, "daemon")
    assert "radio" not in stored and stored["dp_433_CADIDLE"] == "40"


def test_normal_and_autostart_survive_daemon_save_and_reset(tmp_path):
    from lhpc.core.config import load_stack_config, _stack_config_path
    svc = _svc(tmp_path)
    p = _stack_config_path(svc._paths, "meshcom", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('autostart_meshcom-gps-relay = "on"\nfile_x = "y"\ndp_868_CADIDLE = "77"\n')
    svc.save_daemon_params("meshcom", "433", {"CADIDLE": "40"})
    st = load_stack_config(svc._paths, "meshcom")
    assert st["autostart_meshcom-gps-relay"] == "on" and st["file_x"] == "y"
    assert st["dp_868_CADIDLE"] == "77" and st["dp_433_CADIDLE"] == "40"   # other band survives
    svc.reset_daemon_params("meshcom", "433")
    st = load_stack_config(svc._paths, "meshcom")
    assert st["dp_868_CADIDLE"] == "77" and "dp_433_CADIDLE" not in st     # only 433 cleared
    assert st["autostart_meshcom-gps-relay"] == "on"                      # normal untouched


def test_bundle_transaction_failure_preserves_both_files(tmp_path):
    # A stack-file merge that raises (unsupported manual value already in the file) rolls the whole
    # transaction back — local.toml AND the stack file keep their prior bytes.
    from lhpc.core.config import _stack_config_path
    svc = _svc(tmp_path)
    local = svc._paths.runtime_root / "config" / "local.toml"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text('[operator]\ncallsign = "N0AAA"\n')
    sp = _stack_config_path(svc._paths, "meshcom", "")
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text('bad = [1, 2]\n')                                   # unsupported -> render raises
    local_before, stack_before = local.read_text(), sp.read_text()
    res = svc.save_config_bundle("meshcom", values={}, remotes={"meshcom-bridge": "https://x/y.git"})
    assert not res.ok
    assert local.read_text() == local_before and sp.read_text() == stack_before   # both intact


# --- Area 3: complete TOML string/key round-trip + parse-before-write -------------------------

def test_toml_control_char_and_unicode_round_trip(tmp_path):
    from lhpc.core.config import render_stack_config
    import tomllib
    vals = {"s": "a\tb\nc\r\\\"\x00\x08\x0c\x1f\x7fé中"}
    assert tomllib.loads(render_stack_config("t", vals)) == vals      # exact round-trip


def test_toml_tricky_keys_stay_flat(tmp_path):
    from lhpc.core.config import render_stack_config
    import tomllib
    vals = {"custom.key": "x", "spaced key": "y", "a#b": "z", "bare-_1": "w"}
    back = tomllib.loads(render_stack_config("t", vals))
    assert back == vals and set(back) == set(vals)                    # no nesting/dotting


def test_toml_rejects_control_char_key(tmp_path):
    from lhpc.core.config import render_stack_config, ConfigError
    import pytest
    with pytest.raises(ConfigError):
        render_stack_config("t", {"bad\x01key": "x"})


def test_update_rejects_control_key_leaves_file_unchanged(tmp_path):
    import pytest
    from lhpc.core.config import update_stack_config, ConfigError, _stack_config_path
    paths = _paths(tmp_path)
    p = _stack_config_path(paths, "meshcom", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('keep = "x"\n')
    before = p.read_text()
    with pytest.raises(ConfigError):
        update_stack_config(paths, "meshcom", {"bad\x01k": "v"})
    assert p.read_text() == before
