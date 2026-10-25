"""Issue #5: the five one-store repeater settings live only in openHop's own file
(core/openhop_store). The stack-venv helper is replaced by an in-memory stand-in that keeps the same
contract (dotted keys, `get` answers None for an absent key); the helper's own file handling is
tested in the host suite (lhpc/data/meshcore_host/tests/test_openhop_file.py)."""
from __future__ import annotations

import json
import re
import os
import sys
from pathlib import Path

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import lifecycle as lcmod
from lhpc.core import meshcore_identity as mi
from lhpc.core import meshcore_mode as mm
from lhpc.core import openhop_store as ops
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

HOST = Path(__file__).resolve().parents[2] / "lhpc" / "data" / "meshcore_host"
sys.path.insert(0, str(HOST))
from meshcore_host import openhop_file as ofile  # noqa: E402  (the host's one rule, see "check")


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
        if verb == "check":                          # the rule only; upstream's loader is the host suite's
            nested = {}
            for k, v in conf.items():
                ofile.put(nested, k, bytes.fromhex(v) if k == "repeater.identity_key" else v)
            bad = ofile.unusable(nested)
            if bad:
                raise ops.OneStoreError(f"openhop_file check: {bad[0]} in openHop's file {bad[1]}")
            return ""
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
    monkeypatch.setattr(ofile, "_duty_runs", lambda conf: True)   # runs upstream's airtime code: the host suite's
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


def _seed_legacy(svc):
    """gate 2 r2b sweep: a 0.12.x box's legacy one-store values, stored where LHPC kept them (the stack
    config and both secret files), so a test can observe whether a migration step kept or removed them."""
    _secret(svc, mi.REPEATER_IDENTITY_FILENAME, KEY)
    _secret(svc, mi.REPEATER_ADMIN_FILENAME, "Legacy-Pass-1234")
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Relay 1", "file_repeater_mode": "monitor",
                                      "file_airtime": "5"})
    assert r.ok, (r.summary, r.details)
    svc._invalidate_config()


def _legacy_sources(svc):
    """The bytes of every legacy source (absent = None), for a byte-for-byte kept/removed check."""
    stack = svc._paths.runtime_root / "config" / "stacks" / f"{mm.STACK_ID}.toml"
    files = [stack] + [mi.secret_path(svc._paths, f) for f in (mi.REPEATER_IDENTITY_FILENAME,
                                                                mi.REPEATER_ADMIN_FILENAME)]
    return [f.read_bytes() if f.exists() else None for f in files]


def _legacy_removed(svc):
    """Every legacy source gone from the bytes: both secret files absent and none of the three keys left in the
    stack config (gate 2 r2b, the second reader's F1: a value check cannot see what was never seeded)."""
    import tomllib
    stack = svc._paths.runtime_root / "config" / "stacks" / f"{mm.STACK_ID}.toml"
    keys = list(tomllib.loads(stack.read_text())) if stack.exists() else []
    return (not any(re.search(r"(^|_)(repeater_name|repeater_mode|airtime)$", k) for k in keys)
            and not any(mi.secret_path(svc._paths, f).exists()
                        for f in (mi.REPEATER_IDENTITY_FILENAME, mi.REPEATER_ADMIN_FILENAME)))


def test_rows_scale_between_lhpc_and_openhop():
    assert ops.to_file("airtime", "10") == 6000 and ops.to_row("airtime", 6000) == "10"
    assert ops.to_file("repeater_name", "Relay") == "Relay"
    assert ops.to_row("repeater_name", None) == ""


def test_ensure_moves_the_legacy_values_then_removes_them(env):
    """gate 2 r2b sweep S1: the legacy values are STORED (stack config + secrets), read by ensure itself
    under its lock, and every stored copy is gone afterwards (TOML keys and secret files)."""
    svc, helper = env
    _seed_legacy(svc)
    ops.ensure(svc, None, "10")
    assert helper.file() == {
        "repeater.node_name": "Relay 1", "repeater.mode": "monitor",
        "duty_cycle.max_airtime_per_minute": 3000,
        "repeater.security.admin_password": "Legacy-Pass-1234", "repeater.identity_key": KEY}
    assert helper.calls[-3:] == ["set", "sync", "check"]   # durable + usable before any legacy goes
    assert _legacy_removed(svc)


def test_ensure_mints_what_the_file_lacks_and_never_overwrites(env):
    svc, helper = env
    assert svc.save_config(mm.STACK_ID, {"file_repeater_name": "Relay"}).ok     # stored: the production branch
    svc._invalidate_config()
    ops.ensure(svc, None, "10")
    first = helper.file()
    assert len(first["repeater.identity_key"]) == 64
    assert len(first["repeater.security.admin_password"]) == 15
    assert first["repeater.mode"] == "forward"
    assert first["duty_cycle.max_airtime_per_minute"] == 6000
    ops.ensure(svc, {"repeater_name": "Other", "airtime": "50"}, "10")   # idempotent
    assert helper.file() == first
    assert helper.calls.count("set") == 1


def test_ensure_without_a_name_refuses_and_writes_nothing(env):
    """gate 2 r2b sweep S2: stored legacy values (all but the name) stay byte-identical."""
    svc, helper = env
    _seed_legacy(svc)
    assert svc.save_config(mm.STACK_ID, {"file_repeater_name": ""}).ok
    svc._invalidate_config()
    before = _legacy_sources(svc)
    with pytest.raises(ops.OneStoreError, match="repeater name"):
        ops.ensure(svc, None, "10")
    assert "set" not in helper.calls and not ops.file_path(svc._paths).exists()
    assert _legacy_sources(svc) == before


@pytest.mark.parametrize("verb", ["set", "sync"])
def test_a_failed_write_or_sync_keeps_every_legacy_copy(env, monkeypatch, verb):
    svc, helper = env
    monkeypatch.setattr(ops, "_helper", helper)           # restored after the test (the retry swaps it)
    _seed_legacy(svc)
    before = _legacy_sources(svc)
    helper.fail = verb
    with pytest.raises(ops.OneStoreError, match="disk full"):
        ops.ensure(svc, None, "10")
    assert _legacy_sources(svc) == before
    helper.fail = ""                                     # the retry completes and then cleans up
    kept = _legacy_sources(svc)
    seen = []

    def watch(svc_, verb, path, *keys, stdin=""):
        if verb == "sync":                               # gate 2 r2b F2: the retry syncs AGAIN, with
            seen.append(_legacy_sources(svc) == kept)    # every legacy source still untouched
        return helper(svc_, verb, path, *keys, stdin=stdin)
    ops._helper = watch
    ops.ensure(svc, None, "10")
    assert seen == [True]                                # a retry that skipped the sync fails here
    assert helper.calls[-2:] == ["sync", "check"]
    assert _legacy_removed(svc)


def test_writes_are_refused_while_running_or_with_an_unverified_stop(env, monkeypatch):
    svc, helper = env
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    monkeypatch.setattr(svc, "stack_running", lambda sid: sid == mm.STACK_ID)
    assert ops.save_rows(svc, {"repeater_name": "New"}).endswith(ops.RUNNING)
    with pytest.raises(ops.OneStoreError, match="running"):
        ops.ensure(svc, {}, "10")
    monkeypatch.setattr(svc, "stack_running", lambda sid: False)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-now")
    monkeypatch.setattr(type(svc._lifecycle()), "owned_records",
                        lambda self, cid: [{"component": cid, "boot_id": "boot-now"}])
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
    """gate 1 delta F4 (by name): both stored forms of each named key are seeded (flat `file_<n>` and
    component-scoped `__f__<comp>__<n>`) and both go; every key not named stays with its value (a parsed
    comparison: the removal re-renders the file, so its bytes are not the claim)."""
    import tomllib
    svc, _helper = env
    stack = svc._paths.runtime_root / "config" / "stacks" / f"{mm.STACK_ID}.toml"
    c = mm.NODE_ID
    stack.write_text(f'file_repeater_name = "Relay"\n"__f__{c}__repeater_name" = "Relay"\nfile_airtime = "5"\n'
                     f'"__f__{c}__airtime" = "5"\nfile_mode = "repeater"\nfile_repeater_mode = "monitor"\n')
    with cfgmod.config_lock(svc._paths):
        cfgmod.remove_stack_keys(svc._paths, mm.STACK_ID, mm.NODE_ID, ["repeater_name", "airtime"])
    assert tomllib.loads(stack.read_text()) == {"file_mode": "repeater", "file_repeater_mode": "monitor"}


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
    ops.ensure(svc, None, "10")
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
    ops.ensure(svc, None, "10")
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
    ops.ensure(svc, None, "10")
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
    ops.ensure(svc, None, "10")
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

def test_a_mixed_save_validates_the_name_it_sets(env):
    """gate 2 r1 F3: with openHop's file in place, a save that sets a valid repeater name AND keeps
    a repeater mode is judged on that name, not on the file's old (here empty) one: the recovery
    the start refusal names (set the name in LHPC Settings) works."""
    svc, helper = env
    _secret(svc, mi.IDENTITY_FILENAME, KEY)
    _set_mode_repeater(svc, "Relay")
    ops.ensure(svc, None, "10")
    ops._set(svc, {"repeater.node_name": ""})
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Fixed", "file_mode": "chat+repeater"})
    assert r.ok, (r.summary, r.details)
    assert helper.file()["repeater.node_name"] == "Fixed"

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


def test_an_unusable_destination_keeps_every_legacy_copy(env):
    """gate 1 5m F2 / 5n R3-F1: when the helper's `check` (upstream's loader, then the one rule; its real
    behaviour is tested in the host suite against the pinned upstream) refuses openHop's file, `ensure`
    raises after the durability steps and before ANY legacy copy is removed."""
    svc, helper = env
    _seed_legacy(svc)                                     # gate 2 r2b F1: the stack config's values too
    before = _legacy_sources(svc)
    helper.fail = "check"
    with pytest.raises(ops.OneStoreError, match="check"):
        ops.ensure(svc, None, "10")
    assert helper.calls[-3:] == ["set", "sync", "check"]
    assert _legacy_sources(svc) == before                 # TOML keys and both secrets, byte for byte


def test_settings_refuse_an_airtime_of_zero(env):
    """5p R5-F1: LHPC never writes a duty cycle upstream divides by zero with (the row's minimum is 1 %)."""
    svc, _helper = env
    r = svc.save_config(mm.STACK_ID, {"file_airtime": "0"})
    assert not r.ok
    assert svc.save_config(mm.STACK_ID, {"file_airtime": "1"}).ok


def test_a_legacy_airtime_of_zero_names_the_lhpc_setting_and_writes_nothing(env):
    """A 0.12.x box could store airtime 0 % (its old minimum). The migration refuses before writing anything,
    naming LHPC's own setting (not openHop's key); it never mints the 10 % default over a stored 0."""
    svc, helper = env
    _seed_legacy(svc)                                     # gate 2 r2b: a STORED 0 (Settings refuse 0 now,
    stack = svc._paths.runtime_root / "config" / "stacks" / f"{mm.STACK_ID}.toml"   # so it is written as 0.12.x did)
    stack.write_text(stack.read_text().replace('file_airtime = "5"', 'file_airtime = "0"'))
    svc._invalidate_config()
    before = _legacy_sources(svc)
    with pytest.raises(ops.OneStoreError, match="Duty-cycle limit is 0 %"):
        ops.ensure(svc, None, "10")                       # the production branch: read under the lock
    assert "set" not in helper.calls and not ops.file_path(svc._paths).exists()
    assert _legacy_sources(svc) == before


@pytest.mark.parametrize("stored, shown", [(6000, "10"), (-6000, "-10"), (float("nan"), "nan"),
                                           (float("inf"), "inf"), (True, "True")])
def test_the_airtime_row_shows_any_duty_cycle_the_start_accepts(env, monkeypatch, stored, shown):
    """5s R8-F2: openHop's file may hold any duty cycle its own code runs with (a negative, NaN, +inf, true);
    the stopped Settings row renders each, scaled where it can be, else as the file holds it."""
    svc, _helper = env
    monkeypatch.setattr(ops, "_rows", lambda _svc: {"duty_cycle.max_airtime_per_minute": stored})
    assert ops.row_value(svc, "airtime", "7") == shown


def test_a_first_start_between_the_decision_and_the_lock_refuses_the_save(env, monkeypatch):
    """5u R10-F1: "file exists?" is decided before the save's exclusive lock; a first start (ensure,
    the same lock) that creates the file in between must not leave a legacy copy openHop never
    reads. The barrier runs ensure exactly there; the save refuses and writes nothing."""
    svc, helper = env
    _seed_legacy(svc)                                     # every legacy source stored
    _set_mode_repeater(svc, "Relay")
    split = ops.split_saves

    def barrier(*args):
        out = split(*args)
        ops.ensure(svc, None, "10")                  # the first start wins the race
        svc._invalidate_config()
        return out
    monkeypatch.setattr(ops, "split_saves", barrier)
    r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Late"})
    assert not r.ok and "created or removed during this save" in " ".join([r.summary, *r.details])
    svc._invalidate_config()
    assert _legacy_removed(svc)                              # no legacy copy openHop never reads, from the bytes
    assert helper.file() == {                                # the migrated values, the refused save nowhere
        "repeater.node_name": "Relay", "repeater.mode": "monitor", "duty_cycle.max_airtime_per_minute": 3000,
        "repeater.security.admin_password": "Legacy-Pass-1234", "repeater.identity_key": KEY}


def test_a_save_committed_before_the_first_starts_lock_is_migrated_not_lost(env, monkeypatch):
    """Sweep C1.2: the first start read the legacy values before ensure's lock, so a save committed
    in between was removed with the legacy keys and never migrated. The barrier commits a save
    exactly there; the start migrates the newer value."""
    svc, helper = env
    _seed_legacy(svc)                                     # gate 1 delta F4: every legacy source stored
    _set_mode_repeater(svc, "Relay")
    run, seen = ops.ensure, []

    def barrier(svc_, legacy, airtime_default):
        seen.append(legacy)                               # the production branch: read under the lock
        r = svc.save_config(mm.STACK_ID, {"file_repeater_name": "Newer", "file_airtime": "7"})   # legacy store
        assert r.ok, (r.summary, r.details)
        return run(svc_, legacy, airtime_default)
    monkeypatch.setattr(ops, "ensure", barrier)
    ops.start_ensure(svc, mm.STACK_ID)
    assert seen == [None]
    assert helper.file() == {                             # the committed save AND every other legacy value
        "repeater.node_name": "Newer", "duty_cycle.max_airtime_per_minute": 4200, "repeater.mode": "monitor",
        "repeater.security.admin_password": "Legacy-Pass-1234", "repeater.identity_key": KEY}
    assert _legacy_removed(svc)                           # migrated, so gone from the bytes


def test_config_lock_under_a_held_guard_is_refused_at_once(env, monkeypatch):
    """Sweep M2: `config_lock` opens a second descriptor, so under this thread's own config-stability
    guard it would wait for itself and give up after the bounded timeout. It is refused before it opens
    the lock file at all (a sentinel there fails the test), typed, naming its call site; outside the
    guard it is free, and a write under an EXCLUSIVE guard still reuses the held lock."""
    from lhpc.core import runtime_fs
    svc, _helper = env

    def sentinel(*_a, **_k):
        raise AssertionError("config_lock opened the lock file under the held guard (it would poll)")
    with svc._config_stable():
        with monkeypatch.context() as m:
            m.setattr(runtime_fs, "open_lock", sentinel)
            with pytest.raises(cfgmod.ConfigLockHeld) as held:
                with cfgmod.config_lock(svc._paths):
                    pass
    assert held.value.caller.endswith(".test_config_lock_under_a_held_guard_is_refused_at_once")
    assert held.value.caller in str(held.value)
    with cfgmod.config_lock(svc._paths):
        pass
    with svc._config_stable(exclusive=True):
        assert svc.save_config(mm.STACK_ID, {"file_airtime": "12"}).ok


def _restart_without_processes(monkeypatch, svc):
    """The public, locked `restart` with its real admission, config-stability and lifecycle locks and
    the real openHop-file step; only the two process legs are stubbed (they would spawn and kill real
    processes) and the outer position query (gpsd)."""
    from lhpc.core.service_base import ActionResult
    T, calls = type(svc), []

    def leg(name, ok_text):
        def run(self, t, **k):
            if k.get("apply"):
                calls.append((name, t))
            return ActionResult(True, ok_text, data={"changes": 1})        # a plan with one change
        return run
    monkeypatch.setattr(T, "stop", leg("stop", "stopped"))
    monkeypatch.setattr(T, "_start_impl", leg("start", "started"))
    monkeypatch.setattr(T, "_start_outer_refusal", lambda self, *a, **k: (None, None, ""))
    monkeypatch.setattr(T, "_position_refusal", lambda self, t, op: (None, None, ""))
    return calls


@pytest.mark.parametrize("mode", ["repeater", "chat+repeater"])
@pytest.mark.parametrize("migrated", [False, True])
def test_a_repeater_restart_ends_ok_with_the_real_config_lock(env, monkeypatch, mode, migrated):
    """The live proof's R4 defect: restart() holds the shared config-stability guard across stop→start,
    and the nested start's openHop-file step took the exclusive config lock on a second descriptor
    (ConfigLockBusy, the repeater left stopped). Now the step runs before the guard."""
    svc, helper = env
    _seed_legacy(svc)                                     # gate 2 r2b F1: every legacy source stored
    r = svc.save_config(mm.STACK_ID, {"file_mode": mode, "file_repeater_name": "Relay", "file_node_name": "Chat"})
    assert r.ok, (r.summary, r.details)
    svc._invalidate_config()
    if migrated:
        ops.ensure(svc, None, "10")
        svc._invalidate_config()
        assert _legacy_removed(svc)                       # migrated before the restart
    calls = _restart_without_processes(monkeypatch, svc)
    res = svc.restart(mm.STACK_ID, apply=True)
    assert res.ok, (res.summary, res.details)
    assert calls == [("stop", mm.STACK_ID), ("start", mm.STACK_ID)]
    assert helper.file()["repeater.node_name"] == "Relay"
    assert _legacy_removed(svc)                           # gate 2 r2b F1: every source gone, from the bytes


def test_a_restart_that_would_migrate_under_a_running_node_is_refused_before_the_stop(env, monkeypatch):
    svc, helper = env
    _seed_legacy(svc)                                     # gate 2 r2b F1: every legacy source stored
    assert svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": "Relay"}).ok
    svc._invalidate_config()
    calls = _restart_without_processes(monkeypatch, svc)
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    before = _legacy_sources(svc)
    res = svc.restart(mm.STACK_ID, apply=True)
    assert not res.ok and "stop the stack, then start it" in " ".join([res.summary, *res.details])
    assert calls == [] and not ops.file_path(svc._paths).exists()
    assert _legacy_sources(svc) == before                 # gate 2 r2b sweep S5: nothing legacy moved


def test_the_cli_restart_of_a_repeater_ends_ok_with_the_real_config_lock(env, monkeypatch, capsys):
    """The same through the CLI entry (`lhpc stack restart meshcore --yes`, what openHop's restart,
    reboot and Glass restart run)."""
    from lhpc.adapters.cli import main as cli
    svc, helper = env
    _secret(svc, mi.REPEATER_IDENTITY_FILENAME, KEY)
    assert svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": "Relay"}).ok
    svc._invalidate_config()
    ops.ensure(svc, None, "10")                       # a running repeater has its file
    svc._invalidate_config()
    calls = _restart_without_processes(monkeypatch, svc)
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc)
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: sid == mm.STACK_ID and not calls)
    assert cli.main(["stack", "restart", mm.STACK_ID, "--yes"]) == 0, capsys.readouterr()
    assert calls == [("stop", mm.STACK_ID), ("start", mm.STACK_ID)]
    assert helper.file()["repeater.node_name"] == "Relay"


def test_a_repeater_start_migrates_with_the_real_config_lock(env, monkeypatch):
    """The plain start's openHop-file step runs after admission and before the shared guard, with the
    real config lock (the Settings save's real-lock path: test_settings_saves_go_to_openhops_file_once_it_exists)."""
    svc, helper = env
    _seed_legacy(svc)                                     # gate 2 r2b F1: every legacy source stored
    assert svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": "Relay"}).ok
    svc._invalidate_config()
    calls = _restart_without_processes(monkeypatch, svc)
    res = svc.start(mm.STACK_ID, apply=True)
    assert res.ok, (res.summary, res.details)
    assert calls == [("start", mm.STACK_ID)] and helper.file()["repeater.node_name"] == "Relay"
    assert _legacy_removed(svc)                           # gate 2 r2b F1: every source gone, from the bytes


def test_a_restart_of_an_unusable_migrated_file_is_refused_before_the_stop(env, monkeypatch):
    """The second reader's F1 (v18): the restart's fast path (file complete) still runs openHop's own
    check, read-only and before the stop, as a plain start does; else the stop ran and the host refused."""
    svc, helper = env
    _secret(svc, mi.REPEATER_IDENTITY_FILENAME, KEY)
    assert svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": "Relay"}).ok
    svc._invalidate_config()
    ops.ensure(svc, None, "10")
    ops._set(svc, {"repeater.mode": "bogus"})                     # unusable, every key present
    calls = _restart_without_processes(monkeypatch, svc)
    res = svc.restart(mm.STACK_ID, apply=True)
    assert not res.ok and "repeater.mode" in " ".join([res.summary, *res.details])
    assert calls == []


def test_a_nested_repeater_start_without_the_restarts_step_is_refused(env, monkeypatch):
    """The second reader's F2 (v18): only a restart that ran its openHop-file step before the guard may
    start the repeater under the guard; any other nested start fails closed."""
    svc, helper = env
    _secret(svc, mi.REPEATER_IDENTITY_FILENAME, KEY)
    assert svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": "Relay"}).ok
    svc._invalidate_config()
    ops.ensure(svc, None, "10")
    calls = _restart_without_processes(monkeypatch, svc)
    with svc._config_stable():
        res = svc.start(mm.STACK_ID, apply=True)
    assert not res.ok and "before the config-stability guard" in " ".join([res.summary, *res.details])
    assert calls == []


def _records(svc, monkeypatch, recs, boot="boot-now"):
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: boot)
    monkeypatch.setattr(type(svc._lifecycle()), "owned_records", lambda self, cid: list(recs))


def test_a_record_of_an_earlier_boot_does_not_block_the_start(env, monkeypatch):
    """CO-18: after a reboot the repeater's record of the earlier boot is boot restore's
    evidence; counting it as an unverified stop refused every restore and every start of a
    repeater-mode node until it was removed by hand."""
    svc, helper = env
    _records(svc, monkeypatch, [{"component": mm.NODE_ID, "boot_id": "boot-before"}])
    assert ops.blocked(svc) == ""
    ops.ensure(svc, {"repeater_name": "Relay"}, "10")
    assert helper.file()["repeater.node_name"] == "Relay"


@pytest.mark.parametrize("rec, boot", [({"boot_id": "boot-now"}, "boot-now"),    # same boot: unverified stop
                                       ({"boot_id": ""}, "boot-now"),            # unstamped: unprovable
                                       ({}, "boot-now"),
                                       ({"boot_id": "boot-before"}, "")])         # boot id unreadable
def test_records_that_may_still_be_live_keep_blocking(env, monkeypatch, rec, boot):
    svc, helper = env
    _records(svc, monkeypatch, [{"component": mm.NODE_ID, **rec}], boot=boot)
    assert ops.blocked(svc) == ops.UNVERIFIED
    with pytest.raises(ops.OneStoreError, match="not verified"):
        ops.ensure(svc, {"repeater_name": "Relay"}, "10")


def _repeater_start(svc, monkeypatch, recs):
    _secret(svc, mi.REPEATER_IDENTITY_FILENAME, KEY)
    assert svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": "Relay"}).ok
    svc._invalidate_config()
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-now")
    monkeypatch.setattr(type(svc._lifecycle()), "owned_records",          # the node's records only
                        lambda self, cid, band=None, role="": list(recs) if cid == mm.NODE_ID else [])
    res = svc.start(mm.STACK_ID, apply=True)
    return res, " ".join([res.summary, *res.details])


def test_co12_an_earlier_boots_record_does_not_refuse_the_instances_start(env, monkeypatch):
    """T-CO12 (A §2.3, RK12): 0.13.3's rule (CO-18) holds on the start path at L3, where the
    instance is the band: a record of an earlier boot is boot restore's evidence, not a block."""
    svc, _helper = env
    _res, text = _repeater_start(svc, monkeypatch, [{"component": mm.NODE_ID, "band": "868",
                                                     "boot_id": "boot-before"}])
    assert "not verified" not in text, text


def test_co12_a_same_boot_record_refuses_the_instances_start(env, monkeypatch):
    svc, _helper = env
    res, text = _repeater_start(svc, monkeypatch, [{"component": mm.NODE_ID, "band": "868",
                                                    "boot_id": "boot-now"}])
    assert not res.ok and "not verified" in text, text
