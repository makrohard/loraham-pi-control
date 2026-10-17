"""Issue #5: the five one-store repeater settings live only in openHop's own file
(core/openhop_store). The stack-venv helper is replaced by an in-memory stand-in that keeps the same
contract (dotted keys, `get` answers None for an absent key); the helper's own file handling is
tested in the host suite (lhpc/data/meshcore_host/tests/test_openhop_file.py)."""
from __future__ import annotations

import json
import os

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import meshcore_identity as mi
from lhpc.core import meshcore_mode as mm
from lhpc.core import openhop_store as ops
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


class _Helper:
    """The `python -m meshcore_host.openhop_file` stand-in: a JSON dict at the real path."""

    def __init__(self, svc):
        self.svc, self.calls, self.fail = svc, [], ""

    def _load(self):
        p = ops.file_path(self.svc._paths)
        return json.loads(p.read_text()) if p.exists() else {}

    def __call__(self, svc, verb, path, *keys, stdin=""):
        self.calls.append(verb)
        if verb == self.fail:
            raise ops.OneStoreError(f"openhop_file {verb}: disk full")
        conf = self._load()
        if verb == "get":
            return json.dumps({k: conf.get(k) for k in keys})
        if verb in ("set", "render"):
            conf.update(json.loads(stdin))
            if verb == "render":                     # the text; the controller's transaction writes it
                return json.dumps(conf)
            p = ops.file_path(self.svc._paths)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(conf))
        return ""

    def file(self):
        return self._load()


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    helper = _Helper(svc)
    monkeypatch.setattr(ops, "_helper", helper)
    return svc, helper


def _secret(svc, filename, value):
    p = mi.secret_path(svc._paths, filename)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")


def _stored(svc):
    return {n: svc._stored_param_value(mm.STACK_ID, "file", mm.NODE_ID, n)
            for n in ("repeater_name", "repeater_mode", "airtime")}


KEY = "ab" * 32


def test_rows_scale_between_lhpc_and_openhop():
    assert ops.to_file("airtime", "10") == 6000 and ops.to_row("airtime", 6000) == "10"
    assert ops.to_file("repeater_name", "Relay") == "Relay"
    assert ops.to_row("repeater_name", None) == ""


def test_ensure_moves_the_legacy_values_then_removes_them(env):
    svc, helper = env
    _secret(svc, mi.REPEATER_IDENTITY_FILENAME, KEY)
    _secret(svc, mi.REPEATER_ADMIN_FILENAME, "Secret-Pass-123")
    ops.ensure(svc, {"repeater_name": "Relay 1", "repeater_mode": "monitor", "airtime": "5"}, "10")
    assert helper.file() == {
        "repeater.node_name": "Relay 1", "repeater.mode": "monitor",
        "duty_cycle.max_airtime_per_minute": 3000,
        "repeater.security.admin_password": "Secret-Pass-123", "repeater.identity_key": KEY}
    assert helper.calls[-2:] == ["set", "sync"]        # durable before any legacy copy goes
    for f in (mi.REPEATER_IDENTITY_FILENAME, mi.REPEATER_ADMIN_FILENAME):
        assert not mi.secret_path(svc._paths, f).exists()


def test_ensure_mints_what_the_file_lacks_and_never_overwrites(env):
    svc, helper = env
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    first = helper.file()
    assert len(first["repeater.identity_key"]) == 64
    assert len(first["repeater.security.admin_password"]) == 15
    assert first["repeater.mode"] == "forward"
    assert first["duty_cycle.max_airtime_per_minute"] == 6000
    ops.ensure(svc, {"repeater_name": "Other", "airtime": "50"}, "10")   # idempotent
    assert helper.file() == first
    assert helper.calls.count("set") == 1


def test_ensure_without_a_name_refuses_and_writes_nothing(env):
    svc, helper = env
    _secret(svc, mi.REPEATER_ADMIN_FILENAME, "Secret-Pass-123")
    with pytest.raises(ops.OneStoreError, match="repeater name"):
        ops.ensure(svc, {}, "10")
    assert "set" not in helper.calls
    assert mi.secret_path(svc._paths, mi.REPEATER_ADMIN_FILENAME).exists()


@pytest.mark.parametrize("verb", ["set", "sync"])
def test_a_failed_write_or_sync_keeps_every_legacy_copy(env, verb):
    svc, helper = env
    _secret(svc, mi.REPEATER_IDENTITY_FILENAME, KEY)
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Relay"})
    assert r.ok, (r.summary, r.details)
    helper.fail = verb
    with pytest.raises(ops.OneStoreError, match="disk full"):
        ops.ensure(svc, _stored(svc), "10")
    assert mi.secret_path(svc._paths, mi.REPEATER_IDENTITY_FILENAME).exists()
    svc._invalidate_config()
    assert _stored(svc)["repeater_name"] == "Relay"
    helper.fail = ""                                     # the retry completes and then cleans up
    ops.ensure(svc, _stored(svc), "10")
    svc._invalidate_config()
    assert _stored(svc)["repeater_name"] == ""
    assert not mi.secret_path(svc._paths, mi.REPEATER_IDENTITY_FILENAME).exists()


def test_writes_are_refused_while_running_or_with_an_unverified_stop(env, monkeypatch):
    svc, helper = env
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    monkeypatch.setattr(svc, "stack_running", lambda sid: sid == mm.STACK_ID)
    assert ops.save_rows(svc, {"repeater_name": "New"}).endswith(ops.RUNNING)
    with pytest.raises(ops.OneStoreError, match="running"):
        ops.ensure(svc, {}, "10")
    monkeypatch.setattr(svc, "stack_running", lambda sid: False)
    monkeypatch.setattr(type(svc._lifecycle()), "owned_records", lambda self, cid: ["rec"])
    assert ops.save_rows(svc, {"repeater_name": "New"}).endswith(ops.UNVERIFIED)
    assert helper.file()["repeater.node_name"] == "Relay"


def test_save_rows_writes_exactly_the_named_keys(env):
    svc, helper = env
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    before = helper.file()
    assert ops.save_rows(svc, {"airtime": "20"}) == ""
    after = helper.file()
    assert after.pop("duty_cycle.max_airtime_per_minute") == 12000
    before.pop("duty_cycle.max_airtime_per_minute")
    assert after == before


def test_row_value_reads_the_file_else_the_legacy_store(env):
    svc, helper = env
    assert ops.row_value(svc, "repeater_name", "Legacy") == "Legacy"      # no file yet (F5)
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    assert ops.row_value(svc, "repeater_name", "Legacy") == "Relay"
    assert ops.row_value(svc, "airtime", "") == "10"
    helper.fail = "get"
    assert ops.row_value(svc, "repeater_name", "Legacy") == "Relay"      # unchanged file: no read
    ops.file_path(svc._paths).write_text(ops.file_path(svc._paths).read_text() + " ")
    assert ops.row_value(svc, "repeater_name", "Legacy") == "Legacy"     # changed + unreadable


def test_remove_stack_keys_drops_both_stored_forms_only(env):
    svc, _helper = env
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Relay", "file_mode": "repeater"})
    assert r.ok, (r.summary, r.details)
    with cfgmod.config_lock(svc._paths):
        cfgmod.remove_stack_keys(svc._paths, mm.STACK_ID, mm.NODE_ID, ["repeater_name", "airtime"])
    svc._invalidate_config()
    assert _stored(svc)["repeater_name"] == ""
    assert svc.meshcore_mode() == "repeater"


def test_the_password_section_reads_openhops_file_once_it_exists(env):
    svc, helper = env
    _secret(svc, mi.REPEATER_ADMIN_FILENAME, "Legacy-Pass-1234")
    assert svc.ui_credentials(mm.STACK_ID, mm.NODE_ID)["value"] == "Legacy-Pass-1234"   # before
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    creds = svc.ui_credentials(mm.STACK_ID, mm.NODE_ID)
    assert creds["value"] == "Legacy-Pass-1234" and creds["edit_command"] == ""
    assert creds["path"] == str(ops.file_path(svc._paths))
    helper.fail = "get"
    creds = svc.ui_credentials(mm.STACK_ID, mm.NODE_ID)
    assert creds["value"] is None and "unreadable" in creds["reason"]


def test_settings_saves_go_to_openhops_file_once_it_exists(env, monkeypatch):
    svc, helper = env
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Before"})    # no file: legacy store
    assert r.ok and helper.calls == []
    ops.ensure(svc, _stored(svc), "10")
    svc._invalidate_config()
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "After", "file_airtime": "25"})
    assert r.ok, (r.summary, r.details)
    assert helper.file()["repeater.node_name"] == "After"
    assert helper.file()["duty_cycle.max_airtime_per_minute"] == 15000
    assert _stored(svc)["repeater_name"] == ""                            # never the legacy store
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": " "})
    assert not r.ok and "cannot be cleared" in r.details[0]
    monkeypatch.setattr(svc, "stack_running", lambda sid: True)
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Live"})
    assert not r.ok and r.details[0].endswith(ops.RUNNING)
    assert helper.file()["repeater.node_name"] == "After"


def test_start_warns_about_upstream_values_lhpc_would_not_accept(env):
    svc, helper = env
    assert ops.start_warnings(svc, mm.STACK_ID) == []                     # no file yet
    r = svc.set_operator_identity("DL1ABC")
    assert r.ok, (r.summary, r.details)
    _set_mode_repeater(svc, "Relay")
    ops.ensure(svc, _stored(svc), "10")
    assert ops.start_warnings(svc, mm.STACK_ID) == []
    ops._set(svc, {"repeater.node_name": "dl1abc", "repeater.security.admin_password": "short8ch"})
    lines = ops.start_warnings(svc, mm.NODE_ID)
    assert len(lines) == 2 and "callsign" in lines[0] and "15-128" in lines[1]
    assert ops.start_warnings(svc, "graywolf") == []
    helper.fail = "get"
    assert ops.start_warnings(svc, mm.STACK_ID) == []


def _set_mode_repeater(svc, name):
    r = svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": name})
    assert r.ok, (r.summary, r.details)
    svc._invalidate_config()


def test_openhop_warnings_reach_the_start_note(env):
    from lhpc.core.service_base import ActionResult
    svc, _helper = env
    res = ActionResult(True, "started", data={"openhop_warnings": ["w1", "w2"]})
    assert svc.start_notes(res) == ["w1", "w2"]


def test_a_chat_name_another_companion_carries_is_refused_at_save(env):
    svc, helper = env
    _secret(svc, mi.IDENTITY_FILENAME, KEY)
    _set_mode_repeater(svc, "Relay")
    ops.ensure(svc, _stored(svc), "10")
    ops._set(svc, {"identities.companions": [{"name": "Mine", "identity_key": KEY.upper()},
                                             {"name": "Taken", "identity_key": "cd" * 32}]})
    r = svc.save_config(mm.STACK_ID, {"file_mode": "chat+repeater", "file_node_name": "Taken"})
    assert not r.ok and ops.CHAT_NAME_TAKEN in " ".join([r.summary, *r.details])
    r = svc.save_config(mm.STACK_ID, {"file_mode": "chat+repeater", "file_node_name": "Mine"})
    assert r.ok, (r.summary, r.details)                    # its own entry (by key) is no collision
    r = svc.save_config(mm.STACK_ID, {"file_node_name": "Free"})
    assert r.ok, (r.summary, r.details)
    r = svc.save_config(mm.STACK_ID, {"file_mode": "chat", "file_node_name": "Taken"})
    assert r.ok, (r.summary, r.details)                    # chat mode: not in openHop's list
    helper.fail = "get"                                    # unreadable: refused, never deferred
    assert "could not be read" in ops.chat_name_refusal(
        svc, {"file_mode": "chat"}, {"file_mode": "chat+repeater", "file_node_name": "Taken"})



def test_a_refused_mixed_save_leaves_both_stores_byte_identical(env):
    """gate 1 5l F1: a changed one-store row commits in the SAME transaction as the stack config,
    so a refusal anywhere in the save (here the chat-name collision) writes neither store."""
    svc, helper = env
    _secret(svc, mi.IDENTITY_FILENAME, KEY)
    _set_mode_repeater(svc, "Relay")
    ops.ensure(svc, _stored(svc), "10")
    ops._set(svc, {"identities.companions": [{"name": "Taken", "identity_key": "cd" * 32}]})
    stack_file = svc._paths.runtime_root / "config" / "stacks" / f"{mm.STACK_ID}.toml"
    before = (stack_file.read_bytes(), ops.file_path(svc._paths).read_bytes())
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Changed", "file_mode": "chat+repeater",
                                      "file_node_name": "Taken"})
    assert not r.ok and ops.CHAT_NAME_TAKEN in " ".join(r.details)
    assert (stack_file.read_bytes(), ops.file_path(svc._paths).read_bytes()) == before
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Changed", "file_mode": "chat+repeater",
                                      "file_node_name": "Free"})
    assert r.ok, (r.summary, r.details)                    # the same bundle without the collision
    assert helper.file()["repeater.node_name"] == "Changed" and svc.meshcore_mode() == "chat+repeater"

# --- plan §4: a one-store save and a start never interleave (the existing config lock) ----------

_HELD_START_MANIFEST = (
    '[[stack]]\nid = "s"\nname = "s"\nmain = "m"\n'
    '[[stack.component]]\nid = "m"\nname = "m"\nkind = "service"\nrun_argv = ["sleep", "60"]\n'
    'readiness = "endpoint"\n'
    '  [[stack.component.endpoint]]\n  kind = "tcp"\n  address = "127.0.0.1:18999"\n'
    '  ready = true\n  role = "listener"\n')


def _held_start_svc(tmp_path, monkeypatch):
    """A real start whose endpoint-readiness wait (inside the start's SHARED config guard) is
    held at a barrier; the one-store helper is the in-memory stand-in."""
    import threading

    from lhpc.core.probes import RealSystem
    manifest = tmp_path / "m.toml"
    manifest.write_text(_HELD_START_MANIFEST)
    svc = ControllerService(manifest_path=manifest, system=RealSystem(),
                            paths=Paths(runtime_root=tmp_path / "rt"))
    helper = _Helper(svc)
    monkeypatch.setattr(ops, "_helper", helper)
    entered, release = threading.Event(), threading.Event()

    def held_readiness(self, comp):
        entered.set()
        assert release.wait(30)
        return True, ["held by the test"]
    monkeypatch.setattr(ControllerService, "_ready_endpoints_present", held_readiness)
    return svc, helper, entered, release


@pytest.mark.needs_session
def test_a_save_during_a_start_waits_then_refuses_as_running(tmp_path, monkeypatch):
    import threading
    svc, helper, entered, release = _held_start_svc(tmp_path, monkeypatch)
    # The repeater counts as running from the moment it was spawned (the barrier is after spawn).
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: entered.is_set())
    out = {}
    start = threading.Thread(target=lambda: out.update(res=svc.start("s", apply=True)), daemon=True)
    start.start()
    try:
        assert entered.wait(30), "the start never reached its readiness wait"
        save = threading.Thread(target=lambda: out.update(
            why=ops.save_rows(svc, {"repeater_name": "Late"})), daemon=True)
        save.start()
        save.join(2)
        assert save.is_alive() and "render" not in helper.calls, "the save wrote during the start"
        release.set()
        save.join(30)
        start.join(30)
        assert out["why"].endswith(ops.RUNNING) and "render" not in helper.calls
    finally:
        release.set()
        start.join(30)
        svc.stop("s", apply=True)


@pytest.mark.needs_session
def test_a_start_during_a_save_waits_for_it(tmp_path, monkeypatch):
    import threading
    svc, helper, entered, release = _held_start_svc(tmp_path, monkeypatch)
    release.set()                                   # readiness answers at once here
    in_save, let_go = threading.Event(), threading.Event()
    real_helper = ops._helper

    def held_render(svc_, verb, *args, **kw):
        if verb == "render":                       # inside the save's exclusive lock
            in_save.set()
            assert let_go.wait(30)
        return real_helper(svc_, verb, *args, **kw)
    monkeypatch.setattr(ops, "_helper", held_render)
    out = {}
    save = threading.Thread(target=lambda: out.update(
        why=ops.save_rows(svc, {"repeater_name": "Stopped"})), daemon=True)
    save.start()
    try:
        assert in_save.wait(30)
        start = threading.Thread(target=lambda: out.update(res=svc.start("s", apply=True)),
                                 daemon=True)
        start.start()
        start.join(2)
        assert start.is_alive() and not entered.is_set(), "the start ran inside the save"
        let_go.set()
        save.join(30)
        start.join(30)
        assert out["why"] == "" and helper.file()["repeater.node_name"] == "Stopped"
        assert out["res"].ok, (out["res"].summary, out["res"].details)
    finally:
        let_go.set()
        svc.stop("s", apply=True)


def test_the_rows_are_read_in_one_helper_call_until_the_file_changes(env):
    """gate 1 5l N3/N4: a Settings render reads five rows with ONE subprocess; a write re-reads."""
    svc, helper = env
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    helper.calls.clear()
    assert [ops.row_value(svc, n, "") for n in ("repeater_name", "repeater_mode", "airtime")] == [
        "Relay", "forward", "10"]
    assert helper.calls.count("get") == 1
    assert ops.save_rows(svc, {"repeater_name": "Renamed"}) == ""
    assert ops.row_value(svc, "repeater_name", "") == "Renamed"
    assert helper.calls.count("get") == 2
