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
    # a stack that substitutes {callsign} (iGate) DOES expose the operator section
    # and still splits its run params into basic/advanced.
    igate = svc.config_view("igate")
    assert igate["operator"] is not None
    params = igate["components"][0]["params"]
    assert any(p.advanced for p in params) and any(not p.advanced for p in params)


def test_save_config_writes_operator_and_params(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    r = svc.save_config("igate", {"tx_freq": "434.000"}, callsign="oe1abc", locator="JN88")
    assert r.ok
    view = svc.config_view("igate")
    assert view["operator"]["callsign"] == "OE1ABC"   # normalised upper-case
    assert view["values"]["tx_freq"] == "434.000"


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
    cmd = c.run_cmd
    for p in c.run_params:
        cmd = cmd.replace("{" + p.name + "}", emit_param(p, vals[p.name]))
    assert "--port 7001" in cmd and "--backend fake" in cmd


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
