"""Tests for layered configuration (defaults + runtime-local overrides + secrets)."""


from __future__ import annotations
import json
import tomllib
import pytest
import os
import threading
from pathlib import Path
from lhpc.core.config import load_config, load_secrets, ConfigError, load_stack_config, _stack_config_path, save_operator_config, save_stack_config, _atomic_write, config_lock, ConfigLockBusy
from lhpc.core.paths import Paths, PathContainmentError
from lhpc.core.services import ControllerService
from lhpc.core import config as cfgmod
from lhpc.core.model import Component, ComponentKind, SourceSpec, FileConfig
from lhpc.core.probes.backends import FakeSystem


# ===== merged from test_config.py =====
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
        '[operator]\ncallsign = "OE1XYZ"\n[web]\nport = 9999\n'
    )
    cfg = load_config(_paths(tmp_path))
    assert cfg.operator.callsign == "OE1XYZ"
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


@pytest.mark.contract
def test_save_operator_writes_callsign_locally(tmp_path):
    from lhpc.core.config import save_operator_config
    paths = _paths(tmp_path)
    save_operator_config(paths, "n0call-10")           # fresh file: writes ONLY callsign
    cfg = load_config(paths)
    assert cfg.operator.callsign == "n0call-10"
    import dataclasses
    assert {f.name for f in dataclasses.fields(cfg.operator)} == {"callsign"}   # only callsign remains


def test_save_operator_preserves_unrelated_keys(tmp_path):
    # A callsign save patches ONLY callsign — any other [operator] scalar and other tables survive.
    import tomllib
    from lhpc.core.config import save_operator_config
    paths = _paths(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text(
        '[operator]\ncallsign = "OLD"\nnote = "keep"\n[web]\nport = 8770\n')
    save_operator_config(paths, "W1ABC")
    data = tomllib.loads((tmp_path / "config" / "local.toml").read_text())
    assert data["operator"]["callsign"] == "W1ABC"
    assert data["operator"]["note"] == "keep"          # unrelated [operator] key preserved
    assert data["web"]["port"] == 8770                 # unrelated table preserved


def test_config_view_splits_basic_advanced_and_operator(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    view = svc.config_view("daemon")
    assert view["operator"] is None          # daemon does not consume callsign
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
    r = svc.save_config("igate", {"tx_freq": "434.000"}, callsign="oe1abc")
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
    secrets_file = tmp_path / "config" / "secrets.toml"
    secrets_file.write_text('[meshcom]\nbridge_password = "x"\n')
    # 0600 is the contract: `lhpc install` writes it that way (install.py), and
    # load_secrets refuses anything group/other-readable.
    secrets_file.chmod(0o600)
    secrets = load_secrets(_paths(tmp_path))
    assert secrets["meshcom"]["bridge_password"] == "x"
    # Secrets never leak into the effective config.
    cfg = load_config(_paths(tmp_path))
    assert "meshcom" not in cfg.values


def test_a_group_readable_secrets_file_is_refused(tmp_path):
    """A 0600 generated config is pointless if the source of the key is world-readable.
    The refusal is typed (ConfigError) so callers turn it into a blocked start rather
    than a traceback."""
    from lhpc.core.config import ConfigError

    (tmp_path / "config").mkdir()
    secrets_file = tmp_path / "config" / "secrets.toml"
    secrets_file.write_text('[meshcom]\nbridge_password = "x"\n')
    for mode in (0o644, 0o640, 0o604):
        secrets_file.chmod(mode)
        with pytest.raises(ConfigError, match="readable beyond its owner"):
            load_secrets(_paths(tmp_path))
    secrets_file.chmod(0o600)
    assert load_secrets(_paths(tmp_path))["meshcom"]["bridge_password"] == "x"


def test_run_param_default_uses_operator_callsign(tmp_path):
    # The Start-page default for an operator-token run-param (igate 'call' = '{callsign}')
    # must resolve to the configured operator callsign — matching the Config page — not
    # show the literal placeholder. A SAVED value is used verbatim.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.config import save_operator_config, save_stack_config
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    save_operator_config(svc._paths, "DL1ABC"); svc._config = None
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


@pytest.mark.parametrize("seed", [
    pytest.param('bad = [1, 2]\nkeep = "x"\n', id="list-value"),
    pytest.param('[nested]\nx = 1\n', id="table-value"),
])
def test_update_rejects_non_scalar_value_and_leaves_file_unchanged(tmp_path, seed):
    from lhpc.core.config import update_stack_config, ConfigError
    paths = _paths(tmp_path)
    p = _seed_stack_toml(paths, "meshcom", seed)
    before = p.read_text()
    with pytest.raises(ConfigError):
        update_stack_config(paths, "meshcom", {"dp_433_CADIDLE": "40"})
    assert p.read_text() == before                                       # original untouched


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


# ===== merged from test_config_bundle.py =====
def _svc_config_bundle(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _snapshot(tmp_path):
    out = {}
    for p in (tmp_path / "config").rglob("*.toml"):
        out[str(p)] = p.read_text()
    return out


def _seed(svc, tmp_path):
    # A known-good baseline (local.toml + stacks/daemon.toml). `radio=868` is a non-default single
    # band (the daemon `radio` param no longer offers `both`).
    r = svc.save_config_bundle("daemon", values={"radio": "868"},
                               callsign="N0CALL",
                               remotes={"loraham-daemon": "", "radiolib": ""})
    assert r.ok
    return _snapshot(tmp_path)


def test_valid_first_remote_invalid_second_changes_nothing(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    before = _seed(svc, tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "433"},
                               remotes={"loraham-daemon": "https://github.com/x/y.git",
                                        "radiolib": "--upload-pack=evil"})
    assert not r.ok
    assert _snapshot(tmp_path) == before          # neither file changed


def test_valid_operator_invalid_stack_setting_changes_nothing(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    before = _seed(svc, tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "999"},   # invalid enum
                               callsign="N0CALL-7")
    assert not r.ok and _snapshot(tmp_path) == before


def test_unknown_field_rejected_zero_mutation(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    before = _seed(svc, tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "433", "bogus_key": "x"})
    assert not r.ok and any("unknown config field" in d for d in r.details)
    assert _snapshot(tmp_path) == before


def test_failure_after_first_replacement_restores_all(tmp_path, monkeypatch):
    svc = _svc_config_bundle(tmp_path)
    before = _seed(svc, tmp_path)
    # Fail the SECOND target write; the transaction must roll the first back.
    real = cfgmod._atomic_write
    calls = {"n": 0}
    def flaky(paths, path, text, mode=0o644):
        calls["n"] += 1
        # journal write is first; then target writes — fail the 2nd target write.
        if calls["n"] == 3:
            raise OSError("simulated mid-transaction failure")
        return real(paths, path, text, mode)
    monkeypatch.setattr(cfgmod, "_atomic_write", flaky)
    r = svc.save_config_bundle("daemon", values={"radio": "433"},
                               callsign="N0CALL", remotes={"loraham-daemon": "", "radiolib": ""})
    assert not r.ok
    monkeypatch.undo()
    assert _snapshot(tmp_path) == before          # both files restored
    assert not (tmp_path / "state" / "config-txn.json").exists()   # journal cleared


def test_pending_journal_is_recovered_before_next_save(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    _seed(svc, tmp_path)
    stack_file = tmp_path / "config" / "stacks" / "daemon.toml"
    # Simulate a crash: tamper a file and leave a journal with its pre-image.
    pre = stack_file.read_text()
    stack_file.write_text("# CORRUPT partial write\n")
    journal = tmp_path / "state" / "config-txn.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"version": 1, "targets": [
        {"kind": "stack", "rel": "config/stacks/daemon.toml", "pre": pre,
         "existed": True, "mode": 0o644}]}))
    # The next bundle must recover (restore pre-image) before applying.
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert r.ok
    assert not journal.exists()
    assert 'radio = "868"' in stack_file.read_text()   # new value applied after recovery


def _write_journal(tmp_path, obj):
    j = tmp_path / "state" / "config-txn.json"
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return j


@pytest.mark.parametrize("obj", [
    "{ this is not json",                                            # malformed
    {"targets": [{"path": "/etc/passwd"}]},                          # wrong schema (no version)
    {"version": 1, "targets": [{"kind": "evil", "rel": "config/x"}]},  # unknown kind
    {"version": 1, "targets": [{"kind": "local", "rel": "/etc/passwd"}]},  # absolute
    {"version": 1, "targets": [{"kind": "stack", "rel": "../../etc/x.toml"}]},  # traversal
    {"version": 1, "targets": [                                       # duplicate target
        {"kind": "stack", "rel": "config/stacks/daemon.toml", "existed": False},
        {"kind": "stack", "rel": "config/stacks/daemon.toml", "existed": False}]},
])
def test_malicious_or_malformed_journal_blocks(tmp_path, obj):
    svc = _svc_config_bundle(tmp_path)
    before = _seed(svc, tmp_path)
    _write_journal(tmp_path, obj)
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r.ok and any("recovery-required" in d for d in r.details)
    assert (tmp_path / "state" / "config-txn.json").exists()   # journal retained
    assert _snapshot(tmp_path) == before                       # nothing mutated


def test_journal_absolute_target_not_touched(tmp_path):
    # An arbitrary absolute path in the journal must never be written/deleted.
    svc = _svc_config_bundle(tmp_path)
    _seed(svc, tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT TOUCH")
    _write_journal(tmp_path, {"version": 1, "targets": [
        {"kind": "local", "rel": str(victim), "existed": False}]})
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r.ok
    assert victim.read_text() == "DO NOT TOUCH"                # untouched


def test_rollback_failure_retains_journal_and_blocks_later(tmp_path, monkeypatch):
    svc = _svc_config_bundle(tmp_path)
    _seed(svc, tmp_path)
    real = cfgmod._atomic_write
    stack_file = str(tmp_path / "config" / "stacks" / "daemon.toml")
    def fail_stack(paths, path, text, mode=0o644):
        if str(path) == stack_file:        # both the write AND its rollback fail
            raise OSError("simulated disk failure on stack file")
        return real(paths, path, text, mode)
    monkeypatch.setattr(cfgmod, "_atomic_write", fail_stack)
    r = svc.save_config_bundle("daemon", values={"radio": "433"})
    assert not r.ok and any("recovery-required" in d for d in r.details)
    assert (tmp_path / "state" / "config-txn.json").exists()       # journal retained
    # A later mutation must stay blocked until recovery can complete.
    r2 = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r2.ok and any("recovery-required" in d for d in r2.details)


def test_symlinked_config_txn_journal_blocks_recovery(tmp_path):
    # A symlinked transaction journal must not be read/followed -> recovery BLOCKS ("").
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    outside = tmp_path / "evil.json"
    outside.write_text('{"version": 1, "targets": [{"kind": "local", "rel": "x", "pre": "P", "existed": true, "mode": 420}]}')
    os.symlink(outside, cfgmod._txn_journal(paths))     # symlinked journal
    assert cfgmod.recover_config_transaction(paths) == ""   # blocked, never followed


def test_dangling_internal_journal_symlink_blocks_not_absent(tmp_path):
    # A journal that is a DANGLING symlink (to a nonexistent path INSIDE the root) must
    # NOT read as absent: Path.exists() follows the link and would return None (absent),
    # so recovery uses a no-follow presence check and BLOCKS instead.
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    # target stays inside the root (so _txn_journal/under does not raise) but does NOT exist
    os.symlink(tmp_path / "state" / "ghost.json", cfgmod._txn_journal(paths))
    assert not (tmp_path / "state" / "ghost.json").exists()          # genuinely dangling
    assert cfgmod.recover_config_transaction(paths) == ""            # BLOCK, not None

    # save_config_bundle must refuse while that journal entry is present.
    svc = _svc_config_bundle(tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r.ok and any("recovery-required" in d for d in r.details)


def test_external_journal_symlink_blocks_not_raises(tmp_path):
    # A journal symlink whose target ESCAPES the runtime root makes Paths.under() (via
    # realpath) raise PathContainmentError while locating the journal — recovery must
    # convert that into a clean BLOCK, never an uncaught exception or "absent".
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    outside = tmp_path.parent / "evil_external_journal.json"        # OUTSIDE the runtime root
    outside.write_text('{"version": 1, "targets": [{"kind": "local", "rel": "config/local.toml", "pre": "P", "existed": true, "mode": 420}]}')
    os.symlink(outside, tmp_path / "state" / "config-txn.json")     # escaping journal symlink
    try:
        assert cfgmod.recover_config_transaction(paths) == ""       # BLOCK, no exception
        svc = _svc_config_bundle(tmp_path)
        r = svc.save_config_bundle("daemon", values={"radio": "868"})
        assert not r.ok and any("recovery-required" in d for d in r.details)
        assert outside.read_text().startswith('{"version"')         # external file untouched
    finally:
        outside.unlink(missing_ok=True)


def test_save_config_bundle_refuses_symlinked_local(tmp_path):
    # The bundle's local.toml pre-read must go through the no-follow runtime reader: a
    # symlinked/escaping local.toml is refused (ConfigError -> bundle fails), never read
    # through. Operator change triggers the local.toml read path.
    import os
    svc = _svc_config_bundle(tmp_path)
    _seed(svc, tmp_path)
    local = tmp_path / "config" / "local.toml"
    outside = tmp_path.parent / "evil_local.toml"; outside.write_text("[operator]\ncallsign='X'\n")
    local.unlink()
    os.symlink(outside, local)                                     # symlinked leaf
    try:
        r = svc.save_config_bundle("daemon", values={"radio": "868"}, callsign="N0CALL-9")
        assert not r.ok
        assert outside.read_text() == "[operator]\ncallsign='X'\n"  # never written through
    finally:
        outside.unlink(missing_ok=True)


def _local(tmp_path):
    return tmp_path / "config" / "local.toml"


def test_local_root_scalars_and_types_survive_bundle_save(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('rootstr = "hi"\nenabled = true\nlimit = 5\nratio = 1.25\n'
                 '"quoted.key" = "q"\n[operator]\ncallsign = "OLD"\n[extra]\nflag = false\nn = 9\n')
    assert svc.save_config_bundle("meshcom", values={}, callsign="DK0ABC").ok
    d = tomllib.loads(p.read_text())
    assert d["rootstr"] == "hi" and d["enabled"] is True and d["limit"] == 5 and d["ratio"] == 1.25
    assert d["quoted.key"] == "q"                              # quoted root key stays literal
    assert d["extra"]["flag"] is False and d["extra"]["n"] == 9   # unrelated table types exact
    assert d["operator"]["callsign"] == "DK0ABC"


def test_local_control_and_multiline_strings_round_trip(tmp_path):
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text("")
    cfgmod._write_local_tables(_svc_config_bundle(tmp_path)._paths, p, {"t": {"s": "a\tb\nc\r\\\"\x00é中"}})
    assert tomllib.loads(p.read_text())["t"]["s"] == "a\tb\nc\r\\\"\x00é中"


@pytest.mark.parametrize("bad", ['arr = [1, 2]\n', '[a.b]\nx = 1\n',
                                 'when = 2020-01-01T00:00:00\n'])
def test_local_unsupported_structures_block_and_preserve(tmp_path, bad):
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(bad)
    before = p.read_text()
    r = svc.save_config_bundle("meshcom", values={}, callsign="DK0ABC")
    assert not r.ok and p.read_text() == before                # refused, byte-for-byte preserved


def test_operator_and_component_remote_use_safe_renderer(tmp_path):
    # save_operator_config / save_component_remote must preserve unrelated root scalars + types.
    paths = _svc_config_bundle(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('keepme = 42\nenabled = true\n')
    cfgmod.save_operator_config(paths, "DL1ABC")
    cfgmod.save_component_remote(paths, "loraham-daemon", "https://x/y.git")
    d = tomllib.loads(p.read_text())
    assert d["keepme"] == 42 and d["enabled"] is True          # unrelated root scalars/types kept
    assert d["operator"]["callsign"] == "DL1ABC"
    assert d["remotes"]["loraham-daemon"] == "https://x/y.git"


def test_operator_save_refuses_when_local_has_unsupported(tmp_path):
    paths = _svc_config_bundle(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('arr = [1, 2]\n'); before = p.read_text()
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.save_operator_config(paths, "DL1ABC")
    assert p.read_text() == before                              # preserved, not mutated


def test_remote_patch_rejects_foreign_component(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcore-pi" = "https://b/mc.git"\n'); before = p.read_text()
    # meshcore-pi is NOT a component of meshcom -> reject, zero mutation
    r = svc.save_config_bundle("meshcom", values={}, remotes={"meshcore-pi": "https://evil/x.git"})
    assert not r.ok and p.read_text() == before


def test_remote_patch_own_component_preserves_others(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcore-pi" = "https://b/mc.git"\n')
    assert svc.save_config_bundle("meshcom", values={},
                                  remotes={"meshcom-bridge": "https://c/br.git"}).ok
    rem = tomllib.loads(p.read_text())["remotes"]
    assert rem["meshcom-bridge"] == "https://c/br.git" and rem["meshcore-pi"] == "https://b/mc.git"


def test_remote_clear_own_preserves_other_components(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcom-bridge" = "https://c/br.git"\n"meshcore-pi" = "https://b/mc.git"\n')
    assert svc.save_config_bundle("meshcom", values={}, remotes={"meshcom-bridge": ""}).ok
    rem = tomllib.loads(p.read_text())["remotes"]
    assert "meshcom-bridge" not in rem and rem["meshcore-pi"] == "https://b/mc.git"


def test_save_operator_config_patches_and_preserves_extra_keys(tmp_path):
    from lhpc.core import config as cfg
    paths = _svc_config_bundle(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('rootn = 3\n[operator]\ncallsign = "OLD"\nlegacy = "AA00"\n'
                 'note = "portable profile"\nenabled = true\ncount = 5\n[extra]\nx = 1\n')
    cfg.save_operator_config(paths, "DJ0CHE")
    d = tomllib.loads(p.read_text())
    assert d["operator"]["callsign"] == "DJ0CHE"
    assert d["operator"]["legacy"] == "AA00"                   # an unrelated [operator] scalar is left untouched
    assert d["operator"]["note"] == "portable profile"        # extra string preserved
    assert d["operator"]["enabled"] is True and d["operator"]["count"] == 5   # bool/int types kept
    assert d["rootn"] == 3 and d["extra"]["x"] == 1            # unrelated root scalar + table kept


def test_bundle_operator_update_preserves_extra_operator_keys(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[operator]\ncallsign = "OLD"\nlegacy = "AA00"\nnote = "keep"\nflag = false\n')
    assert svc.save_config_bundle("meshcom", values={}, callsign="DK0ABC").ok
    op = tomllib.loads(p.read_text())["operator"]
    assert op["callsign"] == "DK0ABC" and op["note"] == "keep" and op["flag"] is False
    assert op["legacy"] == "AA00"                               # an unrelated [operator] scalar is left untouched


def test_scalar_operator_shape_rejects_operator_save(tmp_path):
    from lhpc.core import config as cfg
    paths = _svc_config_bundle(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('operator = "manual text"\n'); before = p.read_text()
    with pytest.raises(cfg.ConfigError):
        cfg.save_operator_config(paths, "DJ0CHE")
    assert p.read_text() == before                            # byte-for-byte preserved


def test_scalar_remotes_shape_rejects_bundle_remote_save(tmp_path):
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('remotes = "not a table"\n'); before = p.read_text()
    r = svc.save_config_bundle("meshcom", values={}, remotes={"meshcom-bridge": "https://c/br.git"})
    assert not r.ok and p.read_text() == before


def test_scalar_remotes_via_component_remote_is_controlled_failure(tmp_path):
    # No raw ValueError/TypeError from dict("string") — a normal failed ActionResult, file intact.
    svc = _svc_config_bundle(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('remotes = "x"\n'); before = p.read_text()
    r = svc.save_component_remote("loraham-daemon", "https://x/y.git")
    assert not r.ok and p.read_text() == before


def test_component_remote_set_and_clear_preserve_others(tmp_path):
    from lhpc.core import config as cfg
    paths = _svc_config_bundle(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcore-pi" = "https://b/mc.git"\n')
    cfg.save_component_remote(paths, "loraham-daemon", "https://x/y.git")
    rem = tomllib.loads(p.read_text())["remotes"]
    assert rem["loraham-daemon"] == "https://x/y.git" and rem["meshcore-pi"] == "https://b/mc.git"
    cfg.save_component_remote(paths, "loraham-daemon", "")     # clear
    rem = tomllib.loads(p.read_text())["remotes"]
    assert "loraham-daemon" not in rem and rem["meshcore-pi"] == "https://b/mc.git"


def test_audit_config_lock_is_bounded(tmp_path):
    # AUDIT CC1: a held exclusive config lock must make a second acquire fail fast with
    # ConfigLockBusy, not block forever (which would wedge the fixed web thread pool).
    import threading, time
    from lhpc.core import config as cfg
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir()
    paths = Paths(runtime_root=tmp_path)
    held, release = threading.Event(), threading.Event()
    def holder():
        with cfg.config_lock(paths):
            held.set(); release.wait(10)
    threading.Thread(target=holder, daemon=True).start()
    assert held.wait(5)
    t0 = time.monotonic()
    try:
        with cfg.config_lock(paths, timeout=0.5):
            assert False, "should not have acquired"
    except cfg.ConfigLockBusy:
        pass
    assert time.monotonic() - t0 < 3.0                # bounded, not wedged
    assert isinstance(cfg.ConfigLockBusy("x"), cfg.ConfigError)   # caught by existing handlers
    release.set()


def test_audit_deep_toml_is_diagnostic_not_crash(tmp_path):
    # AUDIT IN2: pathologically deep inline-table nesting -> ConfigError, never RecursionError.
    from lhpc.core import config as cfg
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text("a = " + "{x = " * 3000 + "1" + "}" * 3000)
    paths = Paths(runtime_root=tmp_path)
    cfgobj = cfg.load_config(paths)                   # must not raise RecursionError
    assert cfgobj.diagnostics                         # surfaced as a diagnostic


# ===== merged from test_config_containment.py =====
def _svc_config_containment(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _comp():
    return Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"),
                     config_file=FileConfig(path="conf/app.toml", fmt="keyval", params=()))


def test_runtime_destination_policy(tmp_path):
    dest = _svc_config_containment(tmp_path)._resolve_config_dest(_comp(), "{runtime}/config/files/x.conf")
    assert dest.status == "ok" and dest.policy == "runtime"


def test_relative_source_destination_policy(tmp_path):
    dest = _svc_config_containment(tmp_path)._resolve_config_dest(_comp(), "conf/x.conf")
    assert dest.status == "ok" and dest.policy == "source"


def test_arbitrary_absolute_rejected(tmp_path):
    for raw in ("/etc/passwd", "{home}/x", "../../escape/x.conf"):
        dest = _svc_config_containment(tmp_path)._resolve_config_dest(_comp(), raw)
        assert dest.status == "failed" and dest.policy == "reject"


def test_linked_source_is_readonly(tmp_path, monkeypatch):
    from lhpc.core.lifecycle import Lifecycle
    monkeypatch.setattr(Lifecycle, "is_linked_source", lambda self, c: True)
    dest = _svc_config_containment(tmp_path)._resolve_config_dest(_comp(), "conf/x.conf")
    assert dest.status == "linked-readonly"


def test_source_config_through_symlinked_parent_rejected(tmp_path):
    svc = _svc_config_containment(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, src / "conf")               # parent symlink escapes source root
    with pytest.raises(PathContainmentError):
        svc._write_source_config(_comp(), src / "conf" / "app.toml", "data")
    assert not (outside / "app.toml").exists()


def test_runtime_config_through_symlinked_parent_rejected(tmp_path):
    rt = tmp_path / "rt"; rt.mkdir()
    svc = _svc_config_containment(rt)
    (rt / "config").mkdir()
    outside = tmp_path / "outside"; outside.mkdir()       # OUTSIDE the runtime root (rt)
    os.symlink(outside, rt / "config" / "files")          # symlink escapes runtime root
    dest = svc._resolve_config_dest(_comp(), "{runtime}/config/files/x.conf")
    assert dest.status == "failed" and "escapes" in dest.detail


def test_base_file_escape_rejected_at_manifest_parse(tmp_path):
    from lhpc.core.manifest import _parse_file_config, ManifestError
    with pytest.raises(ManifestError):
        _parse_file_config({"path": "conf/x.toml", "fmt": "toml-update", "base": "/etc/hosts"})


def test_normal_runtime_config_writes(tmp_path):
    svc = _svc_config_containment(tmp_path)
    res = svc.write_config_files("voice")           # {runtime}/config/files/... (shipped)
    assert any(w.status == "written" and "/config/files/" in w.path for w in res)


def test_nested_symlink_escape_creates_nothing(tmp_path):
    # source/conf -> outside ; config output conf/newdir/app.toml must create NEITHER
    # outside/newdir NOR a config file (containment proven before any mkdir).
    svc = _svc_config_containment(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, src / "conf")                 # source/conf -> outside
    with pytest.raises(PathContainmentError):
        svc._write_source_config(_comp(), "conf/newdir/app.toml", "data=1")
    assert not (outside / "newdir").exists()          # no dir created through the symlink
    assert not any(outside.rglob("*.toml"))           # no config file created


def test_intermediate_dirs_created_safely(tmp_path):
    # A legitimate nested relative path creates real intermediate dirs under the source.
    svc = _svc_config_containment(tmp_path)
    (tmp_path / "src" / "app").mkdir(parents=True)
    svc._write_source_config(_comp(), "a/b/app.toml", "data=1")
    leaf = tmp_path / "src" / "app" / "a" / "b" / "app.toml"
    assert leaf.read_text() == "data=1"
    assert (tmp_path / "src" / "app" / "a").is_dir() and not (tmp_path / "src" / "app" / "a").is_symlink()


def test_descriptor_walk_refuses_swapped_symlink_component(tmp_path):
    # A multi-level path where an intermediate dir is a symlink (as a swap-after-check
    # would produce) must be refused at the syscall — nothing created in the target.
    svc = _svc_config_containment(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, src / "a")                  # intermediate component is a symlink
    with pytest.raises(PathContainmentError):
        svc._write_source_config(_comp(), "a/b/c.toml", "x")
    assert not (outside / "b").exists()             # never descended through the symlink


def test_descriptor_walk_refuses_symlink_leaf(tmp_path):
    svc = _svc_config_containment(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "secret.toml"; outside.write_text("orig")
    os.symlink(outside, src / "app.toml")           # leaf is a symlink
    # The write now routes through runtime_fs.atomic_write, which refuses a symlink leaf
    # with the typed PathContainmentError (was a bare OSError from the hand-rolled writer).
    from lhpc.core.paths import PathContainmentError
    with pytest.raises((OSError, PathContainmentError)):
        svc._write_source_config(_comp(), "app.toml", "x")
    assert outside.read_text() == "orig"            # not clobbered through the link


def test_source_base_read_refuses_symlink_leaf(tmp_path):
    svc = _svc_config_containment(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "secret"; outside.write_text("SECRET")
    os.symlink(outside, src / "base.toml")          # base file swapped to a symlink
    with pytest.raises(OSError):
        svc._read_source_base(_comp(), "base.toml")


def test_source_base_read_refuses_symlinked_parent(tmp_path):
    svc = _svc_config_containment(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "base.toml").write_text("X")
    os.symlink(outside, src / "sub")                # parent swapped to a symlink
    with pytest.raises(PathContainmentError):
        svc._read_source_base(_comp(), "sub/base.toml")


def test_source_base_read_reads_real_file(tmp_path):
    svc = _svc_config_containment(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    (src / "base.toml").write_text("k = 1\n")
    assert svc._read_source_base(_comp(), "base.toml") == "k = 1\n"


def test_stack_config_path_escaping_stacks_dir_raises_validationerror(tmp_path):
    # F-9: config/stacks/ swapped to a symlink pointing OUTSIDE the runtime root is refused by the
    # house no-follow containment (paths.under) and surfaced as the ValidationError the service layer
    # already catches — a bare PathContainmentError would escape callers as a 500.
    from lhpc.core import config
    from lhpc.core.validators import ValidationError
    rt = tmp_path / "rt"; (rt / "config").mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, rt / "config" / "stacks")           # stacks/ escapes the runtime root
    paths = Paths(runtime_root=rt)
    with pytest.raises(ValidationError):
        config._stack_config_path(paths, "voice")
    with pytest.raises(ValidationError):                    # public entry routes through the same helper
        config.load_stack_config(paths, "voice")


# ===== merged from test_config_failclosed.py =====
def _write_malformed(paths, stack_id, band=""):
    p = _stack_config_path(paths, stack_id, band)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("this is not = valid toml [[[\n")
    return p


def _svc_config_failclosed(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))


def test_absent_stack_config_is_defaults(tmp_path):
    assert load_stack_config(Paths(runtime_root=tmp_path), "daemon") == {}


def test_malformed_stack_config_raises_and_is_preserved(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    bad = _write_malformed(paths, "daemon")
    with pytest.raises(ConfigError):
        load_stack_config(paths, "daemon")
    assert bad.read_text().startswith("this is not")     # left untouched for diagnosis


def test_stack_config_funnel_fails_closed(tmp_path):
    svc = _svc_config_failclosed(tmp_path)
    _write_malformed(svc._paths, "daemon")
    with pytest.raises(ConfigError):
        svc.stack_config("daemon")


def test_config_view_fails_closed(tmp_path):
    svc = _svc_config_failclosed(tmp_path)
    _write_malformed(svc._paths, "kiss")
    with pytest.raises(ConfigError):
        svc.config_view("kiss")


def test_cli_config_reports_typed_failure_without_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    from lhpc.adapters.cli import main as cli
    cli.main(["bootstrap", "--yes"]); capsys.readouterr()
    paths = Paths(runtime_root=tmp_path)
    bad = [_write_malformed(paths, "kiss", b) for b in ("", "868", "433")]
    rc = cli.main(["config", "kiss", "list"])            # reads stored values through the funnel
    err = capsys.readouterr().err
    assert rc == 1
    assert "malformed" in err.lower() or "unreadable" in err.lower()
    assert all(p.exists() for p in bad)                  # no side effects; files preserved


def test_web_returns_409_on_malformed_config(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc_config_failclosed(tmp_path)
    _write_malformed(svc._paths, "daemon")
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    # A page that reads the daemon's stored config must 409, not 200-with-defaults and not 500.
    r = c.get("/stacks/daemon/body")
    assert r.status_code == 409
    assert b"Traceback" not in r.data and b"this is not" not in r.data   # no traceback, no echo


# ===== merged from test_config_safety.py =====
def test_stack_config_write_is_atomic_and_no_temp_left(tmp_path):
    p = save_stack_config(Paths(runtime_root=tmp_path), "kiss", {"a": "1"}, "868")
    assert p.exists() and p.read_text().strip().endswith('a = "1"')
    # no leftover temp files in the directory
    assert not [f for f in p.parent.iterdir() if f.name.endswith(".tmp")]


def test_local_config_is_mode_0600(tmp_path):
    p = save_operator_config(Paths(runtime_root=tmp_path), "N0CALL")
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_stack_config_is_mode_0644(tmp_path):
    p = save_stack_config(Paths(runtime_root=tmp_path), "kiss", {"a": "1"}, "868")
    assert oct(p.stat().st_mode & 0o777) == "0o644"


def test_malformed_local_config_is_a_diagnostic_not_a_crash(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text("this = is = not valid toml [[[")
    cfg = load_config(Paths(runtime_root=tmp_path))     # must NOT raise
    assert cfg.diagnostics and "malformed" in cfg.diagnostics[0]
    assert cfg.operator.callsign == ""                  # fell back to defaults


def test_atomic_write_replaces_without_partial(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    target = tmp_path / "x.txt"
    _atomic_write(paths, target, "first")
    _atomic_write(paths, target, "second")
    assert target.read_text() == "second"


def test_config_lock_serializes_without_deadlock(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    with config_lock(paths):
        pass
    with config_lock(paths):     # a second acquisition after release must not block
        pass


def test_runtime_config_write_goes_through_runtime_fs(tmp_path, monkeypatch):
    # config._atomic_write must delegate to runtime_fs.atomic_write (containment + fsync).
    from lhpc.core import config as cfgmod, runtime_fs
    seen = {}
    real = runtime_fs.atomic_write
    def spy(paths, path, text, mode=0o644):
        seen["called"] = str(path)
        return real(paths, path, text, mode)
    monkeypatch.setattr(runtime_fs, "atomic_write", spy)
    paths = Paths(runtime_root=tmp_path)
    cfgmod.save_operator_config(paths, "N0CALL")
    assert seen.get("called", "").endswith("config/local.toml")


def test_save_operator_config_refuses_symlinked_local(tmp_path):
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import PathContainmentError
    (tmp_path / "config").mkdir(parents=True)
    outside = tmp_path / "evil.toml"; outside.write_text("")
    os.symlink(outside, tmp_path / "config" / "local.toml")     # symlinked leaf
    paths = Paths(runtime_root=tmp_path)
    # A symlinked runtime config is refused at the no-follow READ (ConfigError) before any
    # write, OR at the no-follow write — either way it is never followed/written through.
    with pytest.raises((OSError, PathContainmentError, cfgmod.ConfigError)):
        cfgmod.save_operator_config(paths, "N0CALL")
    assert outside.read_text() == ""                            # never written through


def test_known_working_record_is_contained(tmp_path):
    import os
    from lhpc.core import known_working
    paths = Paths(runtime_root=tmp_path)
    sp = known_working.store_path(paths, "s"); sp.parent.mkdir(parents=True)
    outside = tmp_path / "p.json"; outside.write_text("orig")
    os.symlink(outside, sp)                          # symlinked store leaf
    ok, msg = known_working.record(
        paths, "s", {"c": {"commit": "a" * 40, "selector": "dev", "remote": "",
                           "source_rel": "src/c"}}, {"confirmed_at": 1.0})
    assert not ok                                    # refused, never through the symlink
    assert outside.read_text() == "orig"


def test_reset_config_preserves_daemon_profile_and_unrelated(tmp_path):
    # reset_config owns ONLY normal Config-page keys (run/file/autostart). Daemon-profile dp_*
    # overrides and unrelated manual scalars are PRESERVED (removed via the locked safe merge).
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core import config as cfgmod
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    p = cfgmod._stack_config_path(svc._paths, "daemon", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('radio = "868"\ndp_433_CADIDLE = "40"\nmanual = 7\n')   # normal + dp_ + unrelated
    assert svc.reset_config("daemon").ok
    stored = cfgmod.load_stack_config(svc._paths, "daemon")
    assert "radio" not in stored                    # normal run-param reset to default
    assert stored["dp_433_CADIDLE"] == "40"          # daemon-profile override preserved
    assert stored["manual"] == 7                     # unrelated manual scalar preserved


# ===== merged from test_config_stable.py =====
def _svc_config_stable(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    return svc


def test_exclusive_guard_holds_and_save_reuses_it(tmp_path):
    svc = _svc_config_stable(tmp_path)
    assert not svc._holds_config_exclusive()
    with svc._config_stable(exclusive=True):
        assert svc._holds_config_exclusive()
        # A config write INSIDE the exclusive boundary uses the module-private locked path — it must
        # succeed, NOT fail "config busy" by contending on a second descriptor. (`port`, a plain
        # run-param — NOT the HMAC-managed `password_file`, which generic config rejects.)
        r = svc.save_config_bundle("meshcom", values={"port": "7100"})
        assert r.ok, r.details
    assert not svc._holds_config_exclusive()                 # cleared on exit


def test_nested_modes_never_convert_the_outer(tmp_path):
    svc = _svc_config_stable(tmp_path)
    with svc._config_stable(exclusive=True):
        with svc._config_stable():                           # SHARED under EXCLUSIVE — depth-only, allowed
            assert svc._holds_config_exclusive()             # outer mode UNCHANGED
        with svc._config_stable(exclusive=True):             # EXCLUSIVE under EXCLUSIVE — allowed
            assert svc._holds_config_exclusive()
        assert svc._holds_config_exclusive()
    # EXCLUSIVE beneath a SHARED guard is REJECTED (never a SH→EX conversion)
    with svc._config_stable():
        assert not svc._holds_config_exclusive()
        with pytest.raises(RuntimeError):
            with svc._config_stable(exclusive=True):
                pass


def test_state_cleared_on_exceptional_exit(tmp_path):
    svc = _svc_config_stable(tmp_path)
    with pytest.raises(ValueError):
        with svc._config_stable(exclusive=True):
            raise ValueError("boom")
    assert not svc._holds_config_exclusive()
    st = svc._cfg_stable_state
    assert getattr(st, "depth", 0) == 0 and getattr(st, "fh", None) is None


def test_another_writer_blocked_throughout_the_exclusive_boundary(tmp_path):
    # While the exclusive guard is held, an independent config writer (a fresh descriptor requesting
    # LOCK_EX) is blocked for the WHOLE boundary — proving there is no temporary unlock/conversion window.
    svc = _svc_config_stable(tmp_path)
    outcome = []

    def other_writer():
        try:
            with config_lock(svc._paths, timeout=0.3):       # bounded → busy while EX is held
                outcome.append("acquired")
        except ConfigLockBusy:
            outcome.append("busy")

    with svc._config_stable(exclusive=True):
        t = threading.Thread(target=other_writer)
        t.start()
        t.join()
    assert outcome == ["busy"]                               # blocked throughout, never acquired
    # released after the boundary: a writer now succeeds
    with config_lock(svc._paths, timeout=1.0):
        pass


def test_save_under_shared_guard_takes_the_normal_path(tmp_path):
    # Under a SHARED guard `_holds_config_exclusive()` is False, so save acquires the lock normally (it must
    # NOT take the locked-bypass path, which is asserted-guarded to the exclusive mode).
    svc = _svc_config_stable(tmp_path)
    with svc._config_stable():
        assert not svc._holds_config_exclusive()


# ===== merged from test_config_typed.py =====
def _svc_config_typed(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_saved_operator_is_visible_immediately(tmp_path):
    svc = _svc_config_typed(tmp_path)
    assert svc.config().operator.callsign == ""            # primes the cache
    r = svc.save_config_bundle("daemon", callsign="DJ0CHE-7")
    assert r.ok
    assert svc.config().operator.callsign == "DJ0CHE-7"    # NOT the stale cache


def test_saved_remote_is_visible_immediately(tmp_path):
    svc = _svc_config_typed(tmp_path)
    _ = svc.config()                                       # prime cache
    r = svc.save_component_remote("loraham-daemon", "https://github.com/x/y.git")
    assert r.ok
    assert svc.config().remotes.get("loraham-daemon") == "https://github.com/x/y.git"


def test_reset_config_reloads_fresh(tmp_path):
    svc = _svc_config_typed(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "433"})
    _ = svc.config()
    assert svc.reset_config("daemon").ok
    assert svc.config() is not None                        # fresh read, no crash


def test_operator_wrong_type_is_diagnostic_not_crash(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('operator = "x"\n')
    cfg = load_config(Paths(runtime_root=tmp_path))        # must NOT raise
    assert cfg.operator.callsign == ""
    assert any("operator" in d for d in cfg.diagnostics)


def test_remotes_wrong_type_is_diagnostic_not_crash(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('remotes = "x"\n')
    cfg = load_config(Paths(runtime_root=tmp_path))
    assert cfg.remotes == {} and any("remotes" in d for d in cfg.diagnostics)


def test_non_string_remote_value_dropped(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text(
        '[remotes]\ngood = "https://github.com/x/y.git"\nbad = 123\n')
    cfg = load_config(Paths(runtime_root=tmp_path))
    assert cfg.remotes == {"good": "https://github.com/x/y.git"}
    assert any("bad" in d for d in cfg.diagnostics)


def test_operator_non_string_field_is_unset(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('[operator]\ncallsign = 12345\n')
    cfg = load_config(Paths(runtime_root=tmp_path))
    assert cfg.operator.callsign == "" and any("callsign" in d for d in cfg.diagnostics)


def test_config_get_non_table_section_returns_default():
    from lhpc.core.config import Config
    cfg = Config(values={"install": "oops-a-string", "ok": {"k": "v"}})
    assert cfg.get("install", "adopt_search_root", "~/src") == "~/src"   # no AttributeError
    assert cfg.get("ok", "k") == "v"
    assert cfg.get("missing", "k", 42) == 42


def test_changed_param_produces_apply_hint(tmp_path):
    svc = _svc_config_typed(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "433"})           # baseline
    r = svc.save_config_bundle("daemon", values={"radio": "868"})       # CHANGE (restart)
    assert r.ok and any("Start-time change" in d or "Restart" in d for d in r.details)


def test_unchanged_param_produces_no_apply_hint(tmp_path):
    svc = _svc_config_typed(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "433"})
    r = svc.save_config_bundle("daemon", values={"radio": "433"})       # no change
    assert r.ok and not any("Start-time change" in d or "Restart" in d for d in r.details)


def test_reset_config_refuses_symlinked_leaf(tmp_path):
    import os
    from lhpc.core.config import _stack_config_path
    svc = _svc_config_typed(tmp_path)
    cfg_band = svc._config_band("daemon", "")
    p = _stack_config_path(svc._paths, "daemon", cfg_band)
    p.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "evil.toml"; outside.write_text("x=1")
    os.symlink(outside, p)                        # symlinked config leaf
    r = svc.reset_config("daemon")
    assert not r.ok and "unsafe" in r.summary     # typed block, no crash / no 500
    assert outside.read_text() == "x=1"           # never unlinked through the symlink


def test_reset_config_normal_then_idempotent(tmp_path):
    svc = _svc_config_typed(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "868"})   # non-default (default is 433)
    r = svc.reset_config("daemon")
    assert r.ok and "reset to defaults" in r.summary
    r2 = svc.reset_config("daemon")
    assert r2.ok and "already at defaults" in r2.summary
