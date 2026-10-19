"""openHop's own config file: the marker, the durable atomic write, the one-store key access the
controller uses (`python -m meshcore_host.openhop_file`)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from meshcore_host import openhop_file as ofile


def test_set_keys_changes_only_the_named_keys_and_puts_the_marker_first(tmp_path):
    path = tmp_path / "state" / "openhop" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"mesh": {"default_region": "eu"}, "repeater": {"mode": "forward"}}))
    ofile.set_keys(path, {"repeater.node_name": "Relay 1"})
    doc = yaml.safe_load(path.read_text())
    assert list(doc)[0] == ofile.MARKER_KEY
    assert doc["mesh"] == {"default_region": "eu"} and doc["repeater"]["mode"] == "forward"
    assert doc["repeater"]["node_name"] == "Relay 1"
    assert doc[ofile.MARKER_KEY]["lhpc_one_store"] == list(ofile.ONE_STORE)


def test_the_identity_is_stored_as_bytes_and_read_back_as_hex(tmp_path):
    path = tmp_path / "config.yaml"
    ofile.set_keys(path, {"repeater.identity_key": "ab" * 32})
    assert "!!binary" in path.read_text()                  # upstream's LocalIdentity takes bytes
    assert yaml.safe_load(path.read_text())["repeater"]["identity_key"] == bytes.fromhex("ab" * 32)
    assert ofile.get_keys(path, ["repeater.identity_key"]) == {"repeater.identity_key": "ab" * 32}


def test_a_new_file_and_directory_are_private_and_every_new_parent_is_synced(tmp_path, monkeypatch):
    synced = []
    real = ofile._fsync_dir
    monkeypatch.setattr(ofile, "_fsync_dir", lambda p: (synced.append(p), real(p)))
    path = tmp_path / "a" / "b" / "config.yaml"
    ofile.set_keys(path, {"repeater.mode": "monitor"})
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    # each created directory's parent, then the file's directory after the rename
    assert synced == [tmp_path, tmp_path / "a", tmp_path / "a" / "b"]


def test_a_failing_write_leaves_the_old_file_and_no_temp(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    ofile.set_keys(path, {"repeater.mode": "forward"})
    before = path.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        ofile.set_keys(path, {"repeater.mode": "monitor"})
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_a_failing_directory_sync_raises_after_the_rename(tmp_path, monkeypatch):
    """The caller (the controller's one-store step) removes legacy copies only after a clean
    return: a directory fsync that fails must raise, not pass silently."""
    path = tmp_path / "config.yaml"
    ofile.set_keys(path, {"repeater.mode": "forward"})

    def boom(p):
        assert "mode: monitor" in path.read_text()     # gate 2 r2a N2: the rename happened first
        raise OSError("EIO")
    monkeypatch.setattr(ofile, "_fsync_dir", boom)
    with pytest.raises(OSError, match="EIO"):
        ofile.set_keys(path, {"repeater.mode": "monitor"})


def test_the_marker_names_gps_only_while_lhpc_owns_it():
    assert "gps" in ofile.marker(True)["keys"]
    assert "gps" not in ofile.marker(False)["keys"] and "gps.time_sync_enabled" in ofile.marker(False)["keys"]


def test_the_command_line_round_trip(tmp_path, capsys, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"duty_cycle": {
        "enforcement_enabled": True, "max_airtime_per_minute": 6000}})))
    assert ofile.main(["set", str(path)]) == 0
    assert ofile.main(["get", str(path), "duty_cycle", "repeater.node_name"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "duty_cycle": {"enforcement_enabled": True, "max_airtime_per_minute": 6000},
        "repeater.node_name": None}
    assert ofile.main(["sync", str(path)]) == 0
    assert ofile.main(["bogus"]) == 2


def test_get_prints_nested_bytes_as_hex(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    ofile.set_keys(path, {"identities.companions": [{"name": "A", "identity_key": b"\xab" * 32}]})
    assert ofile.main(["get", str(path), "identities.companions"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "identities.companions": [{"name": "A", "identity_key": "ab" * 32}]}


def test_a_failing_parent_sync_for_a_new_directory_raises_before_any_file(tmp_path, monkeypatch):
    def boom(p):
        raise OSError("EIO")
    monkeypatch.setattr(ofile, "_fsync_dir", boom)
    path = tmp_path / "new" / "config.yaml"
    with pytest.raises(OSError):
        ofile.set_keys(path, {"repeater.mode": "monitor"})
    assert not path.exists()


def test_render_prints_what_set_would_write_and_writes_nothing(tmp_path, capsys, monkeypatch):
    import io
    path = tmp_path / "config.yaml"
    ofile.set_keys(path, {"repeater.mode": "forward"})
    before = path.read_bytes()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"repeater.mode": "monitor"})))
    assert ofile.main(["render", str(path)]) == 0
    assert capsys.readouterr().out == ofile.updated(path, {"repeater.mode": "monitor"})
    assert path.read_bytes() == before
    assert "mode: monitor" in ofile.updated(path, {"repeater.mode": "monitor"})


def test_check_names_the_first_value_upstream_cannot_run_with(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    ofile.set_keys(path, {"repeater.node_name": "Relay", "repeater.mode": "forward",
                          "repeater.security.admin_password": "p" * 15, "repeater.identity_key": "ab" * 32})
    assert ofile.main(["check", str(path)]) == 0
    ofile.set_keys(path, {"repeater.identity_key": "ab" * 10})
    assert ofile.main(["check", str(path)]) == 1
    assert "repeater.identity_key" in capsys.readouterr().err


CHECK_S = 120.0          # gate 2 r2a N3: a named bound for one `check` process (cold upstream imports)


@pytest.mark.parametrize("extra, rc, says", [
    ({}, 0, ""),
    ({"repeater.security.admin_password": "x"}, 0, ""),        # upstream's loader + runtime accept it
    ({"storage": "x"}, 1, "own loader cannot load"),           # yaml-readable, upstream's loader raises
    ({"repeater.identity_key": "ab" * 10}, 1, "repeater.identity_key"),
    ({"repeater.security.admin_password": 12345678}, 1, "admin_password"),   # 5o R4-F1: not text
    ({"repeater.security.admin_password": ["x"]}, 1, "admin_password"),
    ({"duty_cycle": {"max_airtime_per_minute": "lots"}}, 1, "duty_cycle"),
    ({"duty_cycle": "8%"}, 1, "duty_cycle"),
    ({"duty_cycle": {"max_airtime_per_minute": 0}}, 1, "duty_cycle"),          # 5p R5-F1: upstream divides by it
    ({"duty_cycle": {"max_airtime_per_minute": -6000}}, 0, ""),             # 5r R7-F1: upstream divides fine
    ({"duty_cycle": {"max_airtime_per_minute": False}}, 1, "duty_cycle"),   # false is 0: the division fails
    ({"duty_cycle": {"max_airtime_per_minute": 10 ** 1000}}, 1, "duty_cycle"),   # 5s R8-F1: float division overflows
    ({"duty_cycle": None}, 1, "duty_cycle"),
    ({"duty_cycle": {"max_airtime_per_minute": float("inf")}}, 0, ""),      # 5q R6-F1: upstream runs with it
    ({"duty_cycle": {"max_airtime_per_minute": True}}, 0, ""),              # YAML true: compares and divides as 1
    ({"duty_cycle": {"max_airtime_per_minute": float("nan")}}, 0, ""),      # 5r R7-F1: NaN runs (NaN results)
    ({"duty_cycle": {"max_airtime_per_minute": 4800, "enforcement_enabled": "yes"}}, 0, ""),   # a truth value
    ({"duty_cycle": {"max_airtime_per_minute": 4800, "enforcement_enabled": False}}, 0, ""),
])
def test_check_is_upstreams_loader_then_the_rule(tmp_path, extra, rc, says):
    """gate 1 5n R3-F1: the helper's `check` (the controller's migration gate) runs the same
    load_checked as the host's start: upstream's pinned loader, then `unusable`, as a real process."""
    pytest.importorskip("repeater.config")
    import subprocess
    import sys
    path = tmp_path / "config.yaml"
    values = {"repeater.node_name": "Relay", "repeater.mode": "forward",
              "repeater.security.admin_password": "p" * 15, "repeater.identity_key": "ab" * 32}
    ofile.set_keys(path, {**values, **extra})
    p = subprocess.run([sys.executable, "-m", "meshcore_host.openhop_file", "check", str(path)],
                       capture_output=True, text=True, cwd=str(Path(ofile.__file__).parents[1]),
                       timeout=CHECK_S, check=False)
    assert p.returncode == rc and says in p.stderr


def test_the_host_start_and_check_share_load_checked(tmp_path, monkeypatch):
    from meshcore_host import repeater as rep
    seen = []
    monkeypatch.setattr(ofile, "load_checked", lambda path: (seen.append(str(path)), ({}, None))[1])
    cfg = rep.HostConfig(repeater_state_dir=str(tmp_path))
    (tmp_path / "config.yaml").write_text("{}")
    rep.load_openhop_config(cfg)
    assert ofile.main(["check", str(tmp_path / "config.yaml")]) == 0     # gate 2 r2a N5: the CLI too
    assert seen == [str(tmp_path / "config.yaml")] * 2


def test_unusable_names_only_keys_of_the_refusal_table():
    """The documented refusal list (REFUSALS, checked against the guide by a repo test) covers every
    refusal `unusable` can return: each bad value below is refused under a key of the table."""
    good = {"repeater": {"node_name": "R", "mode": "forward", "identity_key": b"\xab" * 32,
                         "security": {"admin_password": "p" * 15}}, "duty_cycle": {"max_airtime_per_minute": 6000}}
    assert ofile.unusable(good) is None
    bad = [("repeater", "node_name", ""), ("repeater", "mode", "relay"), ("repeater", "identity_key", b"x"),
           ("security", "admin_password", 5), ("duty_cycle", "max_airtime_per_minute", "x"),
           ("duty_cycle", "max_airtime_per_minute", 0)]
    keys = {k for k, _phrase in ofile.REFUSALS}
    for section, field, value in bad:
        conf = json.loads(json.dumps(good, default=lambda b: b.hex()))
        conf["repeater"]["identity_key"] = good["repeater"]["identity_key"]
        target = conf["repeater"]["security"] if section == "security" else conf[section]
        target[field] = value
        refused = ofile.unusable(conf)
        assert refused and refused[0] in keys, (section, field, refused)


def test_a_refusal_outside_the_table_cannot_be_returned():
    """5p N1: `_refused` takes only a key of REFUSALS, so a new refusal fails at once until it is documented."""
    with pytest.raises(KeyError):
        ofile._refused("repeater.new_rule", "anything")
    assert ofile._refused("duty_cycle", "m") == ("duty_cycle", "m")
