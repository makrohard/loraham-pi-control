"""M6 — durable restart-required state: written ATOMICALLY with the config save that
changed restart/build-mode params while the stack runs (config-txn target kind "state"),
truthful on failure (whole save rolls back), cleared on verified stop / successful start,
surfaced on the stack page + dashboard + CLI status."""

import json
import os

import pytest

from lhpc.core.config import ConfigError, apply_config_transaction
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, cmdlines=None):
    return ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                             paths=Paths(runtime_root=tmp_path))


def _marker(tmp_path, sid="chat"):
    p = tmp_path / "state" / "restart-required" / f"{sid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_marker_set_when_running_param_changes(tmp_path):
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})           # chat RUNNING
    res = svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"})
    assert res.ok, res.details
    m = _marker(tmp_path)
    assert m and m["stack"] == "chat" and m["mode"] == "restart"
    assert "tx_freq" in m["params"]
    assert svc.restart_required("chat") is not None
    assert "chat" in svc.restart_required_stacks()


def test_marker_not_set_when_stopped_or_unchanged(tmp_path):
    svc = _svc(tmp_path)                                             # nothing running
    assert svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}).ok
    assert _marker(tmp_path) is None                                 # stopped -> no marker
    svc2 = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    assert svc2.save_config_bundle("chat", values={"file_tx_freq": "434.500"}).ok
    assert _marker(tmp_path) is None                                 # unchanged value -> no marker


def test_marker_write_failure_rolls_back_whole_save(tmp_path):
    # A symlinked marker leaf makes the transaction REFUSE — atomicity means the config
    # change is NOT applied either (never changed-settings-without-warning).
    from lhpc.core.config import load_stack_config
    d = tmp_path / "state" / "restart-required"
    d.mkdir(parents=True)
    (tmp_path / "evil.json").write_text("x")
    os.symlink(tmp_path / "evil.json", d / "chat.json")
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    res = svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"})
    assert not res.ok                                                # typed failure
    assert load_stack_config(svc._paths, "chat") == {}               # config unchanged
    assert (tmp_path / "evil.json").read_text() == "x"               # nothing through the symlink


def test_state_target_rolls_back_with_the_transaction(tmp_path):
    # Direct transaction-level check: a later target failing rolls the already-written
    # state marker back (pre-image = absent -> removed).
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True)
    marker = tmp_path / "state" / "restart-required" / "s.json"

    def boom(_paths):
        raise ValueError("later target fails")

    with pytest.raises(ConfigError, match="rolled back"):
        apply_config_transaction(paths, [
            ("state", marker, json.dumps({"version": 1, "stack": "s"}), 0o600),
            ("stack", tmp_path / "config" / "stacks" / "s.toml", boom, 0o644),
        ])
    assert not marker.exists()                                       # state target rolled back


def test_state_journal_kind_validated(tmp_path):
    # kind "state" is allowlisted ONLY for state/restart-required/<name>.json.
    paths = Paths(runtime_root=tmp_path)
    with pytest.raises(ConfigError, match="state journal target"):
        apply_config_transaction(paths, [
            ("state", tmp_path / "state" / "evil.json", "{}", 0o600)])


def test_cleared_on_verified_stop_and_next_start_hint(tmp_path):
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    assert svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}).ok
    assert _marker(tmp_path) is not None
    # verified stop of the (now faked-stopped) stack clears the marker
    svc2 = _svc(tmp_path)                                            # process gone
    assert svc2.stop("chat", apply=True).ok
    assert _marker(tmp_path) is None
    assert svc2.restart_required("chat") is None


def test_unsafe_marker_is_safe_side_tri_state(tmp_path):
    # A PRESENT but unreadable/malformed/symlinked/mismatched marker must NOT look like
    # "no restart required": the read is TRI-STATE and unsafe states surface a safe-side
    # warning ({"unsafe": True, ...}) without clearing the marker (GET non-mutating).
    svc = _svc(tmp_path)
    d = tmp_path / "state" / "restart-required"
    d.mkdir(parents=True)
    assert svc.restart_required("chat") is None                      # SAFELY absent -> None
    for setup in (
        lambda: (d / "chat.json").write_text("not json"),            # malformed
        lambda: os.symlink("real.json", d / "chat.json"),            # symlink (dangling)
        lambda: (d / "chat.json").mkdir(),                           # directory leaf
        lambda: (d / "chat.json").write_text(
            json.dumps({"version": 1, "stack": "OTHER"})),           # wrong stack
    ):
        setup()
        m = svc.restart_required("chat")
        assert m is not None and m.get("unsafe") is True             # SAFE-SIDE, never absent
        assert "chat" in svc.restart_required_stacks()
        assert (d / "chat.json").exists() or (d / "chat.json").is_symlink() \
            or (d / "chat.json").is_dir()                            # never silently cleared
        # cleanup for the next shape
        p = d / "chat.json"
        (p.rmdir() if p.is_dir() else p.unlink())
    assert svc.restart_required("chat") is None                      # absent again -> no warning


def test_unsafe_marker_renders_safe_side_on_stack_and_dashboard(tmp_path):
    d = tmp_path / "state" / "restart-required"
    d.mkdir(parents=True)
    (d / "chat.json").write_text("not json")                         # unsafe present marker
    from lhpc.adapters.web.app import create_app
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    c = create_app(service_factory=lambda: svc).test_client()
    body = c.get("/stacks?open=chat").get_data(as_text=True)     # banner now in the stack's (lazy) Install section
    assert "Restart required (safe-side)" in body and "Restart now" in body
    dash = c.get("/").get_data(as_text=True)
    assert "Restart required" in dash and "Restart chat now" in dash
    assert (d / "chat.json").read_text() == "not json"               # GET did not mutate


def test_dash_signature_includes_flags(tmp_path):
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    before = svc.dash_signature()
    assert svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}).ok
    after = svc.dash_signature()
    assert before != after and "RR:chat" in after


def test_cli_status_reports_restart_required(tmp_path):
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    assert svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}).ok
    res = svc.status()
    assert any("RESTART REQUIRED" in d and "chat" in d for d in res.details)
