"""The MeshCore stack's ONE mode decision (core/meshcore_mode.py) at every seam that depends on it:
expected endpoints for start readiness AND ongoing status, chat-identity enforcement, the optional
Companion clients, the repeater's own name, and the two controller-minted repeater secrets."""
from __future__ import annotations

import pytest

from lhpc.core import meshcore_identity as mi
from lhpc.core import meshcore_mode as mm
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService
from lhpc.core.status import StatusProber


def _svc(tmp_path, listeners=()):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    fake = FakeSystem(listeners=[Listener(**l) for l in listeners])
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def _set_mode(svc, mode, name="Relay"):
    # File params are addressed as `file_<name>` through the config API (see save_config_bundle).
    r = svc.save_config(mm.STACK_ID, {"file_mode": mode, "file_repeater_name": name})
    assert r.ok, (r.summary, r.details)
    svc._invalidate_config()


def _node(svc):
    return svc.stack(mm.STACK_ID).component(mm.NODE_ID)


# --- the helper itself -------------------------------------------------------------------------

def test_the_truth_table():
    assert mm.normalize("nonsense") == "chat" and mm.normalize(None) == "chat"
    for mode, chat, rep, clients in (("chat", True, False, True),
                                     ("chat+repeater", True, True, True),
                                     ("repeater", False, True, False)):
        assert mm.chat_identity_required(mode) is chat
        assert mm.repeater_on(mode) is rep
        assert mm.clients_available(mode) is clients


def test_expected_endpoints_follow_the_mode_for_the_node_only(tmp_path):
    svc = _svc(tmp_path)
    node = _node(svc)
    ports = lambda eps: sorted(mm._port(e) for e in eps)
    assert ports(mm.expected_endpoints(node, "chat")) == [5000]
    assert ports(mm.expected_endpoints(node, "chat+repeater")) == [5000, 8000]
    assert ports(mm.expected_endpoints(node, "repeater")) == [8000]
    webui = svc.stack(mm.STACK_ID).component("meshcore-webui")
    assert mm.expected_endpoints(webui, "repeater") == list(webui.endpoints)   # untouched


def test_the_service_reads_the_saved_mode_and_defaults_to_chat(tmp_path):
    svc = _svc(tmp_path)
    assert svc.meshcore_mode() == "chat"
    _set_mode(svc, "repeater")
    assert svc.meshcore_mode() == "repeater"


# --- start readiness and ongoing status use the SAME selection --------------------------------------

@pytest.mark.parametrize("mode,live,ready", [
    ("chat", [5000], True), ("chat", [8000], False),
    ("chat+repeater", [5000], False), ("chat+repeater", [5000, 8000], True),
    ("repeater", [8000], True), ("repeater", [5000], False),
])
def test_start_readiness_and_status_agree_per_mode(tmp_path, mode, live, ready):
    listeners = [{"family": "ipv4", "ip": "127.0.0.1", "port": p, "inode": p} for p in live]
    svc = _svc(tmp_path, listeners)
    _set_mode(svc, mode)
    node = _node(svc)
    # Ongoing status: the prober is handed the same mode the service computes.
    prober = StatusProber(svc._system, svc._paths, meshcore_mode=svc.meshcore_mode())
    _eps, all_ready, _any, has_expected = prober._assess_endpoints(node)
    assert has_expected and all_ready is ready
    # Start readiness selects the same endpoints (an absent endpoint would make this poll for
    # the node's readiness window, so only the present cases are driven through it).
    if ready:
        ok, _ev = svc._ready_endpoints_present(node)
        assert ok


# --- identity enforcement ---------------------------------------------------------------------------

def test_chat_identity_is_not_demanded_in_repeater_only_mode(tmp_path):
    svc = _svc(tmp_path)
    names = lambda: [r["name"] for r in svc._identity_fields(mm.STACK_ID)]
    assert "node_name" in names()                                  # chat: the companion's name
    _set_mode(svc, "chat+repeater")
    assert "node_name" in names()                                  # companion still runs
    _set_mode(svc, "repeater")
    assert "node_name" not in names()                              # no companion, no demand


def test_a_repeater_mode_without_the_repeaters_name_is_refused_at_save_time(tmp_path):
    # A saved repeater mode without a name would refuse every later start (boot restore
    # included), so the SAVE refuses it — the same rule the host and the start apply.
    svc = _svc(tmp_path)
    r = svc.save_config(mm.STACK_ID, {"file_mode": "repeater"})
    assert not r.ok and any("repeater's own node name" in d for d in [r.summary, *r.details])
    assert svc.meshcore_mode() == "chat"                           # nothing persisted
    r = svc.save_config(mm.STACK_ID, {"file_mode": "repeater", "file_repeater_name": "Relay 1"})
    assert r.ok, (r.summary, r.details)


def test_a_stale_repeater_mode_without_a_name_still_refuses_the_start(tmp_path):
    # Defence in depth for a hand-edited stack file: the start (and every restart path) refuses
    # with the exact remedy instead of launching a host that would exit 2.
    svc = _svc(tmp_path)
    (tmp_path / "config" / "stacks" / "meshcore.toml").write_text('file_mode = "repeater"\n')
    svc._invalidate_config()
    r = svc._meshcore_mode_refusal(mm.STACK_ID)
    assert r is not None and "repeater's own node name" in r.summary
    assert any("repeater_name" in d for d in r.details)
    assert svc._meshcore_mode_refusal("daemon") is None            # other stacks: not our business
    assert svc.restart(mm.STACK_ID, apply=True).ok is False       # refused BEFORE any stop


# --- the optional Companion clients -----------------------------------------------------------------

def test_companion_clients_are_refused_and_not_seeded_without_a_companion(tmp_path):
    svc = _svc(tmp_path)
    svc.save_config(mm.STACK_ID, {"autostart_meshcore-webui": "on"})
    svc._invalidate_config()
    assert "meshcore-webui" in [c.id for _s, c in svc._run_order(mm.STACK_ID)]
    assert svc._meshcore_mode_refusal("meshcore-webui") is None
    _set_mode(svc, "repeater")
    assert "meshcore-webui" not in [c.id for _s, c in svc._run_order(mm.STACK_ID)]
    for client in mm.CLIENT_IDS:
        r = svc._meshcore_mode_refusal(client)
        assert r is not None and "repeater-only" in r.summary
    _set_mode(svc, "chat+repeater")
    assert svc._meshcore_mode_refusal("meshcore-webui") is None


# --- the repeater's secrets -------------------------------------------------------------------------

def test_a_mode_change_is_saved_only_and_status_follows_the_running_launch(tmp_path):
    svc = _svc(tmp_path)
    _set_mode(svc, "chat+repeater", name="Relay 1")
    # per-launch overrides of the mode rows are refused — an echo of the saved value too (no
    # surface posts them for a launch; the confirm page shows them as pills)
    for val in ("repeater", "chat+repeater"):
        _clean, err = svc._normalize_file_overrides(mm.STACK_ID, {"mode": val})
        assert "cannot be changed for a single start" in err
    # the RUNNING mode is the generated config's role: nothing generated yet -> the saved mode
    assert svc.meshcore_running_mode() == "chat+repeater"
    svc.write_config_files(mm.STACK_ID)                           # what a start renders
    _set_mode(svc, "repeater", name="Relay 1")                    # saved, not restarted
    assert svc.meshcore_mode() == "repeater"
    assert svc.meshcore_running_mode() == "chat+repeater"         # status keeps the launch's truth
    node = _node(svc)
    assert [mm._port(e) for e in svc._lifecycle().expected_endpoints(node)] == [5000, 8000]


def test_chat_mode_mints_no_repeater_secrets(tmp_path):
    svc = _svc(tmp_path)
    svc.write_config_files(mm.STACK_ID)
    secrets = tmp_path / "config" / "secrets"
    assert (secrets / mi.IDENTITY_FILENAME).exists()
    assert not (secrets / mi.REPEATER_IDENTITY_FILENAME).exists()
    assert not (secrets / mi.REPEATER_ADMIN_FILENAME).exists()
    import tomllib
    rep_tbl = tomllib.loads((tmp_path / "config" / "files" / "meshcore.toml").read_text())["repeater"]
    assert "key" not in rep_tbl and "admin_password" not in rep_tbl and rep_tbl["role"] == "chat"


def test_a_build_needs_the_repeater_checkout_and_names_the_remedy(tmp_path):
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "openhop-core"
    (src / ".venv" / "bin").mkdir(parents=True)
    r = svc.build(mm.STACK_ID, apply=True)
    assert not r.ok and "openhop-repeater-src is not installed" in r.summary
    assert f"lhpc install {mm.STACK_ID}" in r.next_commands


def test_the_two_repeater_secrets_are_minted_once_and_kept_0600(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    key1 = mi.ensure_identity(paths, (), filename=mi.REPEATER_IDENTITY_FILENAME)
    key2 = mi.ensure_identity(paths, (), filename=mi.REPEATER_IDENTITY_FILENAME)
    assert key1 == key2 and len(key1) == 64                       # a 32-byte seed, hex
    assert key1 != mi.ensure_identity(paths, ())                  # distinct from the chat node's
    pw1 = mi.ensure_password(paths, mi.REPEATER_ADMIN_FILENAME)
    pw2 = mi.ensure_password(paths, mi.REPEATER_ADMIN_FILENAME)
    assert pw1 == pw2 and 16 <= len(pw1) <= 128 and " " not in pw1
    for fn in (mi.REPEATER_IDENTITY_FILENAME, mi.REPEATER_ADMIN_FILENAME, mi.IDENTITY_FILENAME):
        assert (tmp_path / "config" / "secrets" / fn).stat().st_mode & 0o777 == 0o600


def test_a_lax_or_garbled_password_file_blocks_instead_of_being_replaced(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    pw = mi.ensure_password(paths, mi.REPEATER_ADMIN_FILENAME)
    f = tmp_path / "config" / "secrets" / mi.REPEATER_ADMIN_FILENAME
    f.write_text("short\n")
    with pytest.raises(mi.MeshCoreIdentityError):
        mi.ensure_password(paths, mi.REPEATER_ADMIN_FILENAME)
    f.write_text(pw + "\n"); f.chmod(0o644)
    with pytest.raises(mi.MeshCoreIdentityError, match="readable by group/other"):
        mi.ensure_password(paths, mi.REPEATER_ADMIN_FILENAME)


def test_generation_renders_the_repeater_table_from_one_file(tmp_path):
    # The generated meshcore.toml carries the role, the repeater's key + password (minted) and its
    # state dir alongside the chat rows — one file, one card, every role.
    svc = _svc(tmp_path)
    _set_mode(svc, "chat+repeater", name="Relay 1")
    r = svc.save_config(mm.STACK_ID, {"file_node_name": "Chat 1"})
    assert r.ok, (r.summary, r.details)
    svc.write_config_files(mm.STACK_ID)                           # generation = the one mint site
    import tomllib
    doc = tomllib.loads((tmp_path / "config" / "files" / "meshcore.toml").read_text())
    rep = doc["repeater"]
    assert rep["role"] == "chat+repeater" and rep["name"] == "Relay 1"
    assert rep["behaviour"] == "forward"
    assert len(rep["key"]) == 64 and rep["key"] != doc["identity"]["key"]
    assert 16 <= len(rep["admin_password"]) <= 128
    assert rep["state_dir"] == str(tmp_path / "state" / "openhop")
    assert doc["companion"]["name"] == "Chat 1"


# --- the box's position ------------------------------------------------------------------------------

def test_repeater_only_consumes_no_position(tmp_path, monkeypatch):
    """The Companion is the only reader of the box's position; the repeater's config pins GPS
    off. So the feed, the receiver claim, the GPS gate and the start's position snapshot all
    follow the mode: chat and chat+repeater keep today's behaviour, repeater-only touches none
    of it — it must not start a `meshcore-gps` it never reads, take the receiver from a stack
    that would, or be refused over a position setting it does not consult."""
    from lhpc.core.config import save_gps
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    save_gps(svc._paths, source="nmea", device="/dev/null")      # a live source that claims
    svc._invalidate_config()
    assert mm.position_consumed("chat") and mm.position_consumed("chat+repeater")
    assert not mm.position_consumed("repeater")
    for mode in ("chat", "chat+repeater"):
        _set_mode(svc, mode)
        assert svc._gps_components_for(mm.STACK_ID) == {"meshcore-gps"}
        assert "meshcore-gps" in [c.id for _s, c in svc._run_order(mm.STACK_ID)]
        assert svc._gps_run_order_uses_position(mm.STACK_ID)
        assert svc._gps_device_claim(mm.STACK_ID).startswith("gps.")
    _set_mode(svc, "repeater")
    assert svc._gps_components_for(mm.STACK_ID) == set()
    order = [c.id for _s, c in svc._run_order(mm.STACK_ID)]
    assert mm.NODE_ID in order and "meshcore-gps" not in order
    assert not svc._gps_run_order_uses_position(mm.STACK_ID)
    assert svc._gps_device_claim(mm.STACK_ID) == ""
    assert svc.gps_block(mm.STACK_ID) == ("", [])
    assert not [b for b in svc.run_blockers(mm.STACK_ID)
                if str(b.get("resource", "")).startswith("gps.")]
    # an unusable [gps] table refuses a Companion start, never a repeater-only one
    (tmp_path / "config" / "local.toml").write_text('[gps]\nsource = "banana"\n')
    svc._invalidate_config()
    assert svc.meshcore_position(mm.STACK_ID) == ({}, "")
    _set_mode(svc, "chat")
    position, why = svc.meshcore_position(mm.STACK_ID)
    assert position is None and "position source" in why
    # a LIVE repeater-only node does not block a change of the global position source
    _set_mode(svc, "repeater")
    svc.write_config_files(mm.STACK_ID)                  # the running launch's mode
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == mm.STACK_ID:
            ss.components[mm.NODE_ID].run_state = RunState.RUNNING
    monkeypatch.setattr(type(svc), "build_snapshot", lambda self, *a, **k: snap)
    assert svc.gps_liveness_blockers([mm.STACK_ID]) == []
    _set_mode(svc, "chat")
    svc.write_config_files(mm.STACK_ID)
    assert svc.gps_liveness_blockers([mm.STACK_ID]) == [mm.NODE_ID]


# --- the Companion's identity ------------------------------------------------------------------------

def test_repeater_only_neither_mints_nor_depends_on_the_chat_identity(tmp_path):
    """The chat identity belongs to the Companion. Where the Companion runs (chat, chat+repeater)
    generation mints/adopts it and fails closed on a damaged or lax file, as today. The pure
    repeater runs no Companion: generation neither mints a key it will never use nor can be
    blocked by a fault in one — the same rule as the chat name, GPS and the clients."""
    key = tmp_path / "config" / "secrets" / mi.IDENTITY_FILENAME
    gen = tmp_path / "config" / "files" / "meshcore.toml"

    def identity_line():
        text = gen.read_text()
        block = text.split("[identity]", 1)[1].split("\n[", 1)[0]
        return [l for l in block.splitlines() if l.strip().startswith("key")]

    svc = _svc(tmp_path)
    _set_mode(svc, "repeater")
    assert not key.exists()
    writes = svc.write_config_files(mm.STACK_ID)
    assert all(w.status != "failed" for w in writes), [(w.component, w.status, w.detail) for w in writes]
    assert not key.exists(), "a pure repeater must not mint the Companion's key"
    assert identity_line() == [], "no [identity] key is written for the pure repeater"
    assert (tmp_path / "config" / "secrets" / mi.REPEATER_IDENTITY_FILENAME).exists()
    # a damaged, world-readable Companion key: irrelevant to the pure repeater ...
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("not-a-key\n")
    key.chmod(0o644)
    writes = svc.write_config_files(mm.STACK_ID)
    assert all(w.status != "failed" for w in writes), [(w.component, w.status, w.detail) for w in writes]
    assert key.read_text() == "not-a-key\n", "never repaired or replaced behind the operator's back"
    # ... and fail-closed wherever the Companion runs
    for mode in ("chat", "chat+repeater"):
        _set_mode(svc, mode)
        writes = svc.write_config_files(mm.STACK_ID)
        node = next(w for w in writes if w.component == mm.NODE_ID)
        assert node.status == "failed" and mi.IDENTITY_FILENAME in node.detail, (node.status, node.detail)
    key.unlink()
    _set_mode(svc, "chat")
    writes = svc.write_config_files(mm.STACK_ID)
    assert all(w.status != "failed" for w in writes)
    assert key.exists() and identity_line(), "the Companion's key is minted where the Companion runs"


# --- where the mode is seen and switched -------------------------------------------------------------

def test_the_mode_is_shown_where_the_operator_looks(tmp_path, monkeypatch):
    """Confirm page: the Settings card's "Repeater" heading appears there too (rows carry their
    manifest group). `lhpc status meshcore`: the saved mode, plus the running one while they differ."""
    svc = _svc(tmp_path)
    headers = [g["header"] for g in svc.stack_start_param_groups(mm.STACK_ID)]
    rep = next(g for g in svc.stack_start_param_groups(mm.STACK_ID) if g["header"].endswith("— Repeater"))
    assert rep["header"].startswith(svc.stack(mm.STACK_ID).component(mm.NODE_ID).name)
    assert {r["name"] for r in rep["rows"]} >= {"mode", "repeater_name", "repeater_mode"}
    mode_row = next(r for r in rep["rows"] if r["name"] == "mode")
    assert mode_row["locked"] and "Mode switch" in mode_row["locked_hint"]   # saved-only, no input
    assert headers.index(rep["header"]) == 1                     # right after "Required"
    # the mode is NOT a start submission: a change inside a start would re-classify identities
    key = svc._param_key(mm.STACK_ID, "file", mm.NODE_ID, "mode")
    _p, fo, sub = svc.extract_identity_submission(mm.STACK_ID, {}, {key: "repeater"})
    assert not sub and fo == {key: "repeater"}
    _clean, err = svc._normalize_file_overrides(mm.STACK_ID, {key: "repeater"})
    assert "cannot be changed for a single start" in err
    text = "\n".join(svc.status(mm.STACK_ID).details)
    assert "  mode: chat" in text and "restart to apply" not in text
    _set_mode(svc, "chat+repeater")
    svc.write_config_files(mm.STACK_ID)                     # what a start renders
    _set_mode(svc, "repeater")
    text = "\n".join(svc.status(mm.STACK_ID).details)
    assert "  mode: repeater" in text and "restart to apply" not in text   # stopped: nothing to restart
    # a RUNNING node launched with another mode: status names both and says what to do
    from lhpc.core.model import RunState
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == mm.STACK_ID:
            ss.components[mm.NODE_ID].run_state = RunState.RUNNING
    monkeypatch.setattr(type(svc), "build_snapshot", lambda self, *a, **k: snap)
    text = "\n".join(svc.status(mm.STACK_ID).details)
    assert "  mode: repeater  (running: chat+repeater — restart to apply)" in text


# --- the optional Companion clients as an auto-start choice ------------------------------------------

def test_the_cli_is_never_an_auto_start_choice_on_either_surface(tmp_path):
    """The MeshCore CLI is a REPL run on demand: the Settings card and the confirm page offer the
    SAME optional set (one predicate), and a stale saved tick never seeds it into the start."""
    svc = _svc(tmp_path)
    settings = svc.config_view(mm.STACK_ID)["optional"]
    confirm = svc.optional_start_components(mm.STACK_ID)
    for listed in (settings, confirm):                   # both LIST the CLI ...
        assert {o["id"] for o in listed} == {"meshcore-webui", "meshcore-cli"}
        assert {o["id"] for o in listed if o["startable"]} == {"meshcore-webui"}   # ... no tick
    svc.save_config(mm.STACK_ID, {"autostart_meshcore-cli": "on", "autostart_meshcore-webui": "on"})
    svc._invalidate_config()
    order = [c.id for _s, c in svc._run_order(mm.STACK_ID)]
    assert "meshcore-webui" in order and "meshcore-cli" not in order
    assert svc._run_order("meshcore-cli"), "an explicit run by name is still possible"


# --- read-only surfaces survive a broken MeshCore stack file ------------------------------------------

def test_a_malformed_meshcore_stack_file_breaks_neither_status_nor_stop_verification(tmp_path):
    """Status/list, the console and stop verification read the RUNNING mode; a broken saved file
    must not take every stack down with it (the START path fails closed on that file itself)."""
    from lhpc.core.config import ConfigError
    svc = _svc(tmp_path)
    (tmp_path / "config" / "stacks" / "meshcore.toml").write_text('file_mode = "chat\n')  # unterminated
    svc._invalidate_config()
    with pytest.raises(ConfigError):
        svc.meshcore_mode()                                   # the start-side contract: fail closed
    assert svc.meshcore_mode_display() == ""
    assert svc.meshcore_running_mode() == mm.DEFAULT_MODE     # nothing generated: the safe default
    snap = svc.build_snapshot()                               # read-only: must not raise
    assert any(ss.stack.id == mm.STACK_ID for ss in snap.stacks)
    assert svc.status("graywolf").ok and svc.status(mm.STACK_ID).ok
    assert "(unreadable stack config)" in "\n".join(svc.status(mm.STACK_ID).details)
    node = _node(svc)
    assert [mm._port(e) for e in svc._lifecycle().expected_endpoints(node)] == [5000]
    assert svc.page_mode_note(svc.web_page("meshcore-meshcore-node")) == ""


# --- a Companion-client start follows the RUNNING node -------------------------------------------------

def test_a_client_start_is_judged_by_the_mode_the_running_node_was_launched_with(tmp_path, monkeypatch):
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    _set_mode(svc, "repeater")
    svc.write_config_files(mm.STACK_ID)                        # the launch: role = repeater
    _set_mode(svc, "chat")                                     # saved later, not restarted
    assert svc._meshcore_mode_refusal("meshcore-webui") is None   # node not running: saved mode rules
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == mm.STACK_ID:
            ss.components[mm.NODE_ID].run_state = RunState.RUNNING
    monkeypatch.setattr(type(svc), "build_snapshot", lambda self, *a, **k: snap)
    r = svc._meshcore_mode_refusal("meshcore-webui")
    assert r is not None and "repeater-only" in r.summary      # the RUNNING node has no 5000
    _set_mode(svc, "chat+repeater")
    svc.write_config_files(mm.STACK_ID)                        # relaunched with 5000
    _set_mode(svc, "repeater")                                 # saved, not restarted
    assert svc._meshcore_mode_refusal("meshcore-webui") is None   # 5000 is live: allowed


# --- an interactive SERVICE keeps its auto-start tick (0.2.7 behaviour) --------------------------------

def test_an_interactive_service_keeps_its_settings_tick_but_gets_no_confirm_checkbox(tmp_path):
    svc = _svc(tmp_path)
    settings = {o["id"]: o for o in svc.config_view("reticulum")["optional"]}
    confirm = {o["id"]: o for o in svc.optional_start_components("reticulum")}
    assert settings["nomadnet"]["startable"] is True           # Settings tick, as in 0.2.7
    assert confirm["nomadnet"]["startable"] is False           # listed, no "Start nomadnet" promise
    svc.save_config("reticulum", {"autostart_nomadnet": "on"})
    svc._invalidate_config()
    assert "nomadnet" in [c.id for _s, c in svc._run_order("reticulum")]   # planned (MANUAL_REQUIRED)
