"""openHop's own config file: the marker, the durable atomic write, the one-store key access the
controller uses (`python -m meshcore_host.openhop_file`)."""
from __future__ import annotations

import json
import os

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
        raise OSError("EIO")
    monkeypatch.setattr(ofile, "_fsync_dir", boom)
    with pytest.raises(OSError):
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
