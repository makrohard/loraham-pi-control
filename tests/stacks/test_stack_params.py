"""Start-confirm 'Stack parameters' panel + CALL/node enforcement + ephemeral file overrides."""

from __future__ import annotations


import pytest

from lhpc.core.config import save_operator_config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

import repo_paths
from seams import LifecycleSeam


def _svc(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


# --- identity detection -------------------------------------------------------

def test_identity_field_map(tmp_path):
    svc = _svc(tmp_path)
    exp = {"graywolf": ("call", "run", "licensed"), "chat": ("call", "file", "licensed"),
           "voice": ("callsign", "file", "licensed"), "meshcom": ("mc_callsign", "run", "licensed"),
           "meshtastic": ("node_name", "run", "unlicensed"),
           "meshcore": ("node_name", "file", "unlicensed")}
    for tgt, (name, kind, enforce) in exp.items():
        idf = svc._identity_field(tgt)
        assert idf and (idf["name"], idf["kind"], idf["enforce"]) == (name, kind, enforce)
    assert svc._identity_field("kiss") is None            # no callsign/node
    assert svc._identity_field("daemon") is None


# --- enforcement --------------------------------------------------------------

def _seed_raw(svc, stack_id, values, band=None):
    """Write SAVED values past the validating bundle (a hand-edited store) — what the SAVED-config
    identity gate must judge. Lands in the stack's per-band store when it has one."""
    from lhpc.core import config as cfgmod
    if band is None:
        band = svc._config_band(stack_id, "")
    cfgmod.save_stack_config(svc._paths, stack_id, values, band)
    svc._invalidate_config()


def test_licensed_refuses_empty_and_n0call(tmp_path):
    svc = _svc(tmp_path)
    assert svc.enforce_identity("chat")[0] is False                        # empty operator callsign
    for bad in ("", "N0CALL", "n0call-1"):
        _seed_raw(svc, "chat", {"file_call": bad})
        assert svc.enforce_identity("chat")[0] is False, bad
    _seed_raw(svc, "chat", {"file_call": "XX0XXA-10"})
    assert svc.enforce_identity("chat")[0] is True


def test_licensed_default_uses_operator_callsign(tmp_path):
    svc = _svc(tmp_path)
    save_operator_config(svc._paths, "XX0XXA"); svc._invalidate_config()
    assert svc.enforce_identity("chat")[0] is True        # default {callsign} -> XX0XXA


def test_identity_hint_leads_with_the_local_field(tmp_path):
    # callsign-identities: the refusal remedy leads with the RELEVANT LOCAL field (the
    # operator asked for THIS stack); the optional global fallback is a trailing note for
    # licensed stacks, never the first answer. Command derived from the param model.
    svc = _svc(tmp_path)
    for target, frag in (("meshcom", "lhpc config meshcom mc_callsign"),
                         ("chat", "lhpc config chat call"),
                         ("voice", "lhpc config voice callsign"),
                         ("graywolf", "lhpc config graywolf call")):
        hint = svc._identity_config_hints(target)[0]
        assert hint.startswith(frag), (target, hint)
        assert "lhpc config operator --callsign" in hint, (target, hint)   # the global option
    # Unlicensed: local field only — no operator-command mention at all.
    hint = svc._identity_config_hints("meshtastic")[0]
    assert hint.startswith("lhpc config meshtastic node_name"), hint
    assert "operator" not in hint
    assert svc._identity_config_hints("meshcore")[0].startswith("lhpc config meshcore node_name")


def test_start_refusal_text_carries_the_local_command(tmp_path):
    # End-to-end: the START refusal carries the exact local command in next_commands.
    svc = _svc(tmp_path)
    res = svc.run_action("start", "meshcom", apply=True)
    assert not res.ok
    assert any(c.startswith("lhpc config meshcom mc_callsign")
               for c in (res.next_commands or [])), (res.summary, res.next_commands)


def test_unlicensed_requires_deliberate_local_names(tmp_path, set_call):
    svc = _svc(tmp_path)
    # No generic first-start identity any more: a fresh meshtastic is REFUSED until both
    # local names are deliberately configured — and never inherits the global callsign.
    ok, fields, msg = svc.enforce_identity("meshtastic")
    assert ok is False and len(fields) == 2, (fields, msg)      # long AND short marked
    set_call(svc)                                               # a global callsign changes nothing
    assert svc.enforce_identity("meshtastic")[0] is False
    _seed_raw(svc, "meshtastic", {"node_name": "Field Node", "node_short": "FN1"})
    assert svc.enforce_identity("meshtastic")[0] is True
    # a retired generic default does not count as deliberately configured
    _seed_raw(svc, "meshtastic", {"node_name": "LoRaHAM Pi", "node_short": "FN1"})
    assert svc.enforce_identity("meshtastic")[0] is False


def test_meshcore_file_node_uses_the_saved_value(tmp_path):
    svc = _svc(tmp_path)
    assert svc.enforce_identity("meshcore")[0] is False                    # default {callsign} empty
    assert svc.save_config_bundle("meshcore", values={"file_node_name": "MyNode"}, band="868").ok
    assert svc.enforce_identity("meshcore")[0] is True


# --- the panel view -----------------------------------------------------------

def test_identity_fields_name_the_settings_row(tmp_path):
    # `field` is the Settings form field (`c_`/`f_`) — the row a refused start highlights.
    svc = _svc(tmp_path)
    idr = svc._identity_fields("graywolf")
    assert len(idr) == 1 and idr[0]["name"] == "call" and idr[0]["field"] == "c_call"
    assert svc._identity_fields("daemon") == []                            # daemon exempt
    vrows = svc._identity_fields("voice")
    assert any(r["field"] == "f_callsign" and r["kind"] == "file" for r in vrows)


# --- ephemeral file-override normalization + precedence -----------------------

def test_meshcore_preset_owns_frequency_for_all_presets(tmp_path):
    """A blank `frequency` must let the selected preset own it — writing a stale explicit
    frequency alongside a changed preset put the daemon 93 kHz off the passband."""
    import tomllib
    svc = _svc(tmp_path)
    gen = tmp_path / "config" / "files" / "meshcore.toml"
    for preset in ("eu_uk_long", "eu_uk_medium", "eu_uk_narrow"):
        res = svc.write_config_files("meshcore", overrides={"preset": preset})
        assert any(w.status == "written" for w in res), [(w.component, w.status, w.detail) for w in res]
        doc = tomllib.loads(gen.read_text())
        assert doc["radio"]["preset"] == preset
        assert "frequency" not in doc["radio"], \
            "a blank frequency must not materialise as an explicit override"

def test_start_blocks_licensed_without_call_backstop(tmp_path):
    # Direct/CLI start (authoritative) refuses a licensed stack with no callsign, carrying the
    # field(s) to highlight; nothing is launched.
    svc = _svc(tmp_path)
    res = svc.start("graywolf", apply=True)
    assert not res.ok and "callsign" in res.summary.lower()
    assert res.data.get("enforce_fields") == ["c_call"]


def test_start_licensed_with_call_passes_enforcement(tmp_path, set_call):
    svc = _svc(tmp_path)
    set_call(svc)
    res = svc.start("graywolf", apply=True)                                   # not blocked by enforcement
    assert "callsign is required" not in res.summary


def test_dependency_param_override_channel(tmp_path):
    """A dependency's configuration lives in the DEPENDENCY's own stack: the target's bundle
    refuses a dependency key with a pointer at the right stack."""
    svc = _svc(tmp_path)
    # routing of launch-time values (today only the inherited identity): each component receives
    # ONLY its own subset — no cross-leak either way
    over = {"loraham-kiss-tnc.tx_freq": "433.900", "tnc_port": "8123"}
    assert svc._overrides_for_comp("graywolf", "run", over, "loraham-kiss-tnc") \
        == {"tx_freq": "433.900"}
    assert svc._overrides_for_comp("graywolf", "run", over, "graywolf") == {"tnc_port": "8123"}
    # persisted saves stay in the owning stack: the target's bundle refuses a dep key with a
    # pointer at the right stack; the dependency's own bundle accepts the same key
    r = svc.save_config_bundle("graywolf", values={"loraham-kiss-tnc.tx_freq": "433.900"})
    assert not r.ok and any("save it on its own stack" in d for d in r.details)
    assert svc.stack_config("graywolf").get("tx_freq") is None             # nothing leaked
    r2 = svc.save_config_bundle("kiss", values={"loraham-kiss-tnc.tx_freq": "433.900"})
    assert r2.ok and svc.stack_config("kiss").get("tx_freq") == "433.900"


@pytest.mark.needs_session  # spawns a real process; identity_complete needs sid>0 (skips under sid==0)
def test_component_booting_tracks_live_post_runner(tmp_path):
    # A running component reads 'booting' while its post-start (--setcall) runner is still alive,
    # then flips to normal once the runner finishes.
    import subprocess, time
    svc = _svc(tmp_path)
    life = svc._lifecycle()
    assert svc._component_booting("meshcom-qemu") is False           # no runner
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        for _ in range(50):
            idn = life._capture_identity(p.pid)
            if idn and idn.get("exec") == "sleep":
                break
            time.sleep(0.05)
        comp, stack = svc.stack("meshcom").component("meshcom-qemu"), svc.stack("meshcom")
        life.record_launch(stack, comp, p.pid, "", ident=life._capture_identity(p.pid), role="post")
        assert svc._component_booting("meshcom-qemu") is True          # runner alive -> booting
    finally:
        p.terminate(); p.wait()
    time.sleep(0.3)
    assert svc._component_booting("meshcom-qemu") is False             # runner gone -> ready


# --- direct-component identity/config scope + ephemeral run-param normalization --------------

def _seam_svc(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise LifecycleSeam()
    monkeypatch.setattr(svc, "_ensure_daemon", seam)
    monkeypatch.setattr(svc, "write_config_files", seam)
    return svc


@pytest.mark.parametrize("call", ["", "N0CALL", "N0CALL-1"])
def test_direct_licensed_component_rejects_bad_call_before_side_effects(tmp_path, monkeypatch, call):
    svc = _seam_svc(tmp_path, monkeypatch)
    _seed_raw(svc, "meshcom", {"mc_callsign": call})                       # the SAVED value
    res = svc._start_impl("meshcom-qemu", apply=True)                       # no LifecycleSeam
    assert not res.ok
    assert "callsign" in (res.summary + str(res.details)).lower()
    assert res.data.get("enforce_fields") == ["c_mc_callsign"]              # the Settings row


def test_direct_unlicensed_component_rejects_empty_node_before_side_effects(tmp_path, monkeypatch, set_call):
    svc = _seam_svc(tmp_path, monkeypatch)
    # MeshCore's node name is a REQUIRED local identity (default "", never {callsign}):
    # a fresh config is blocked before any side effect / LifecycleSeam — with or without a global
    # operator callsign, which unlicensed stacks never inherit.
    res = svc._start_impl("meshcore-node", apply=True)
    assert not res.ok and res.data.get("enforce_fields") == ["f_node_name"]
    set_call(svc)
    res = svc._start_impl("meshcore-node", apply=True)
    assert not res.ok, "a global callsign must not satisfy an unlicensed node identity"


def test_direct_valid_identity_reaches_start_seam(tmp_path, monkeypatch):
    svc = _seam_svc(tmp_path, monkeypatch)
    assert svc.save_config_bundle("meshcom", values={"mc_callsign": "XX0XXA-3"}).ok
    with pytest.raises(LifecycleSeam):                                             # enforcement passed
        svc._start_impl("meshcom-qemu", apply=True)


def test_direct_file_identity_uses_the_owner_stack_store(tmp_path):
    svc = _svc(tmp_path)
    svc.save_config_bundle("meshcore", values={"file_node_name": "SavedNode"}, band="868")
    rows = svc.identity_resolution("meshcore-node", "868")
    assert rows[0]["effective"] == "SavedNode" and rows[0]["source"] == "local"
    assert svc.enforce_identity("meshcore-node", "868")[0] is True


def test_stack_target_and_daemon_behavior_unchanged(tmp_path):
    # Stack targets keep whole-stack scope; the daemon stays identity-exempt.
    svc = _svc(tmp_path)
    assert svc._identity_field("daemon") is None
    assert svc._identity_field("meshcom") == svc._identity_field("meshcom-qemu")   # same field
    assert {r["name"] for r in svc._identity_fields("meshcom")} >= \
           {r["name"] for r in svc._identity_fields("meshcom-qemu")}               # stack ⊇ component


# --- Area 1: run-param normalization BEFORE public start lock planning -----------------------

def _lock_seam_svc(tmp_path, monkeypatch):
    """A service whose lock-planning seams (`_daemon_needs`, `_lifecycle_guard`) raise — so a start
    that reaches lock/radio planning trips the seam, and one blocked earlier does not."""
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise LifecycleSeam()
    monkeypatch.setattr(svc, "_daemon_needs", seam)
    monkeypatch.setattr(svc, "_lifecycle_guard", seam)
    return svc


def test_public_start_valid_params_reach_lock_seam(tmp_path, monkeypatch):
    svc = _lock_seam_svc(tmp_path, monkeypatch)
    with pytest.raises(LifecycleSeam):                                             # the band -> planning
        svc.start("daemon", apply=True, band="433")


# --- Area 1: restart preflight BEFORE lock planning / stop ----------------------------------

def _restart_lock_seam(tmp_path, monkeypatch):
    """Seams for the public restart lock-planning path (`_daemon_needs`, `_lifecycle_guard`)."""
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise LifecycleSeam()
    monkeypatch.setattr(svc, "_daemon_needs", seam)
    monkeypatch.setattr(svc, "_lifecycle_guard", seam)
    return svc


def test_public_restart_invalid_identity_no_lock(tmp_path, monkeypatch):
    svc = _restart_lock_seam(tmp_path, monkeypatch)
    # a SAVED N0CALL / an empty local with no global: the preflight refuses before any lock/stop
    # side effect (no LifecycleSeam raised), naming the Settings row.
    _seed_raw(svc, "graywolf", {"call": "N0CALL"})
    res = svc.restart("graywolf", apply=True)
    assert res.ok is False and "callsign" in (res.summary + str(res.details)).lower()
    assert res.data.get("enforce_fields") == ["c_call"]
    _seed_raw(svc, "graywolf", {"call": ""})
    res = svc.restart("graywolf", apply=True)
    assert res.ok is False and res.data.get("enforce_fields") == ["c_call"]


@pytest.mark.contract
def test_public_restart_valid_reaches_lock_seam(tmp_path, monkeypatch):
    svc = _restart_lock_seam(tmp_path, monkeypatch)
    assert svc.save_config_bundle("graywolf", values={"call": "XX0XXA-10"}).ok
    with pytest.raises(LifecycleSeam):                                             # preflight passed
        svc.restart("graywolf", apply=True)


def test_restart_impl_validates_before_its_stop(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    def seam(*a, **k):
        raise LifecycleSeam()
    monkeypatch.setattr(svc, "stop", seam)                                 # stop() is the seam
    # an unusable SAVED identity -> typed failure BEFORE stop()
    _seed_raw(svc, "graywolf", {"call": "N0CALL"})
    assert svc._restart_impl("graywolf", apply=True).ok is False
    assert svc.save_config_bundle("graywolf", values={"call": "XX0XXA-10"}).ok
    with pytest.raises(LifecycleSeam):                                             # valid -> reaches stop()
        svc._restart_impl("graywolf", apply=True)


# --- Area 2: direct component targets use the OWNER stack for persistence --------------------

def test_direct_component_daemon_params_use_owner_stack(tmp_path):
    from lhpc.core import config as cfgmod, daemon_params as dp
    svc = _svc(tmp_path)
    assert svc._has_daemon_params("meshcom-qemu") and svc._has_daemon_params("meshcore-node")
    nd = "9" if dp.default_value("meshcom", "433", "SF") != "9" else "8"
    assert svc.save_daemon_params("meshcom-qemu", "433", {"SF": nd}).ok
    assert cfgmod.load_stack_config(svc._paths, "meshcom").get("dp_433_SF") == nd   # OWNER stack
    assert cfgmod.load_stack_config(svc._paths, "meshcom-qemu") == {}               # NOT the component
    assert svc._daemon_param_overrides("meshcom-qemu", "433") == {"SF": nd}         # read back via comp
    sf = next(r for r in svc.daemon_params_view("meshcom-qemu", "433")["rows"] if r["name"] == "SF")
    assert sf["value"] == nd                                                        # panel shows owner value
    assert svc.save_daemon_params("meshcore-node", "868", {"CADWAIT": "1234"}).ok
    assert cfgmod.load_stack_config(svc._paths, "meshcore").get("dp_868_CADWAIT") == "1234"


def test_direct_component_config_save_owner_scope(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("meshcom-qemu", values={"mc_callsign": "XX0XXA-3"}).ok
    owner_cfg = cfgmod.load_stack_config(svc._paths, "meshcom")                              # OWNER file
    assert owner_cfg.get("__r__meshcom-qemu__mc_callsign") == "XX0XXA-3"                     # COMPONENT-scoped key
    assert "mc_callsign" not in owner_cfg                                                    # not a flat key
    assert cfgmod.load_stack_config(svc._paths, "meshcom-qemu") == {}                        # NOT component file
    assert svc.stack_config("meshcom-qemu").get("mc_callsign") == "XX0XXA-3"                 # later read resolves
    # a direct component may edit ONLY its own fields — sibling/unknown/autostart/remotes rejected
    assert svc.save_config_bundle("meshcom-qemu", values={"port": "7000"}).ok is False       # sibling field
    assert svc.save_config_bundle("meshcom-qemu",
                                  values={"autostart_meshcom-gps-relay": "on"}).ok is False
    assert svc.save_config_bundle("meshcom-qemu", values={},
                                  remotes={"meshcom-qemu": "https://x/y.git"}).ok is False
    assert svc.save_config_bundle("meshcom",                                                 # stack: unchanged
                                  values={"autostart_meshcom-gps-relay": "on"}).ok


# --- Area 2: direct component file-config generation stays COMPONENT-scoped ------------------

_SCOPE_MANIFEST = '''
[[stack]]
id = "ostack"
name = "Owner Stack"
main = "tgt"
[[stack.component]]
id = "tgt"
name = "Target"
kind = "service"
run = "true"
readiness = "process"
depends_on = ["dep"]
  [[stack.component.param]]
  name = "shared"
  kind = "str"
  default = "tgt-run"
  [stack.component.config_file]
  path = "{runtime}/config/files/tgt.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "tval"
    key = "TVAL"
    default = "tdefault"
[[stack.component]]
id = "dep"
name = "Dependency"
kind = "service"
run = "true"
readiness = "process"
  [[stack.component.param]]
  name = "shared"
  kind = "str"
  default = "dep-run"
  [stack.component.config_file]
  path = "{runtime}/config/files/dep.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "dval"
    key = "DVAL"
    default = "ddefault"
[[stack.component]]
id = "sib"
name = "Sibling"
kind = "service"
run = "true"
readiness = "process"
optional = true
  [stack.component.config_file]
  path = "{runtime}/config/files/sib.conf"
  fmt = "env"
    [[stack.component.config_file.param]]
    name = "sval"
    key = "SVAL"
    default = "sdefault"
'''


def _scope_svc(tmp_path):
    m = tmp_path / "scope.toml"; m.write_text(_SCOPE_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "files").mkdir(parents=True, exist_ok=True)
    return ControllerService(manifest_path=m, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))


def _run_scoped_start(svc, target, monkeypatch, **kw):
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    # Stub the actual launch (no real process) so the start exercises config generation only.
    monkeypatch.setattr(Lifecycle, "start",
                        lambda self, stack, comp, cfg, band="", **_scope: StartLaunch(True, "log", ""))
    return svc.start(target, apply=True, **kw)


def test_direct_start_generates_only_started_components_scoped(tmp_path, monkeypatch):
    svc = _scope_svc(tmp_path)
    assert svc.save_config_bundle("tgt", values={"file_tval": "TXX"}).ok      # the SAVED value
    _run_scoped_start(svc, "tgt", monkeypatch)
    files = tmp_path / "config" / "files"
    assert (files / "tgt.conf").exists()                          # target config generated
    assert (files / "dep.conf").exists()                          # dependency config generated
    assert not (files / "sib.conf").exists()                      # sibling NEVER written
    assert "TVAL=TXX" in (files / "tgt.conf").read_text()         # target's saved value reaches target
    dep_txt = (files / "dep.conf").read_text()
    assert "DVAL=ddefault" in dep_txt                             # dependency uses its OWN default
    assert "TVAL" not in dep_txt and "TXX" not in dep_txt         # target value never leaks to dep


def test_stack_start_keeps_whole_stack_generation(tmp_path, monkeypatch):
    svc = _scope_svc(tmp_path)
    assert svc.save_config_bundle("ostack", values={"file_tval": "TZZ"}).ok
    _run_scoped_start(svc, "ostack", monkeypatch)
    files = tmp_path / "config" / "files"
    # a stack start includes the non-optional components (tgt + dep), each with its saved values
    assert "TVAL=TZZ" in (files / "tgt.conf").read_text()
    assert (files / "dep.conf").exists()


def test_direct_persistence_writes_only_allowed_owner_fields(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _scope_svc(tmp_path)
    assert svc.save_config_bundle("tgt", values={"shared": "keepme"}).ok
    owner_cfg = cfgmod.load_stack_config(svc._paths, "ostack")                       # owner store
    assert owner_cfg.get("__r__tgt__shared") == "keepme"                             # component-scoped key
    assert "shared" not in owner_cfg                                                 # never a flat key
    assert cfgmod.load_stack_config(svc._paths, "tgt") == {}                         # not component-named
    assert svc.save_config_bundle("tgt", values={"dval": "x"}).ok is False           # sibling field rejected


# --- Component-scoped persisted run/file keys (collision-free) -------------------------------

_SCOPE2_MANIFEST = (repo_paths.DATA
                    / "scope2_manifest.toml").read_text()


def _scope2_svc(tmp_path):
    m = tmp_path / "scope2.toml"; m.write_text(_SCOPE2_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "files").mkdir(parents=True, exist_ok=True)
    return ControllerService(manifest_path=m, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))


def _capture_start(svc, monkeypatch):
    """Stub the launch to capture each component's resolved launch config; config files still get
    generated for real."""
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    seen = {}
    def stub(self, stack, comp, cfg, band="", **_scope):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    return seen


def _seed_flat(svc, stack_id, values):
    from lhpc.core import config as cfgmod
    cfgmod.update_stack_config(svc._paths, stack_id, values)


def test_scoped_values_isolate_target_and_dependency(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    # distinct component-scoped values for target and dependency (same param names)
    assert svc.save_config_bundle("tgt", values={"rp": "RP-T", "file_fp": "FP-T"}).ok
    assert svc.save_config_bundle("dep", values={"rp": "RP-D", "file_fp": "FP-D"}).ok
    seen = _capture_start(svc, monkeypatch)
    svc.start("tgt", apply=True)                                  # direct start of tgt (+dep)
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"   # argv per component
    files = tmp_path / "config" / "files"
    assert "FP=FP-T" in (files / "tgt.conf").read_text()
    assert "FP=FP-D" in (files / "dep.conf").read_text()
    assert not (files / "sib.conf").exists()                     # sibling never generated


def test_only_target_scoped_dependency_uses_defaults(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    assert svc.save_config_bundle("tgt", values={"rp": "RP-T", "file_fp": "FP-T"}).ok
    seen = _capture_start(svc, monkeypatch)
    svc.start("tgt", apply=True)
    assert seen["tgt"]["rp"] == "RP-T"
    assert seen["dep"]["rp"] == "rp-dep"                         # dependency DEFAULT, never target's
    assert "FP=fp-dep" in (tmp_path / "config" / "files" / "dep.conf").read_text()


def test_stack_start_honors_scoped_and_unique_flat(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    assert svc.save_config_bundle("tgt", values={"rp": "RP-T"}).ok       # scoped (direct)
    assert svc.save_config_bundle("dep", values={"rp": "RP-D"}).ok       # scoped (direct)
    assert svc.save_config_bundle("ostack2", values={"uniq": "U-FLAT"}).ok   # stack -> flat legacy
    assert _cfg_has_flat(svc, "uniq")                                    # stack save stays flat
    seen = _capture_start(svc, monkeypatch)
    svc.start("ostack2", apply=True)                                     # whole-stack start
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"   # scoped honored per component
    assert seen["tgt"]["uniq"] == "U-FLAT"                               # unique flat legacy honored


def test_ambiguous_flat_legacy_fails_typed_before_any_seam(tmp_path, monkeypatch):
    from lhpc.core.lifecycle import Lifecycle
    svc = _scope2_svc(tmp_path)
    _seed_flat(svc, "ostack2", {"rp": "LEGACY"})                 # rp declared by tgt AND dep -> ambiguous
    def boom(*a, **k):
        raise LifecycleSeam()
    monkeypatch.setattr(svc, "write_config_files", boom)         # config-write seam
    monkeypatch.setattr(Lifecycle, "start", boom)               # spawn seam
    res = svc.start("tgt", apply=True)                           # must NOT raise
    assert res.ok is False and "ambiguous" in res.summary        # typed failure before any seam


def test_unique_flat_legacy_is_backward_compatible(tmp_path, monkeypatch):
    svc = _scope2_svc(tmp_path)
    _seed_flat(svc, "ostack2", {"uniq": "LEGACY-U"})            # uniq declared only by tgt -> unique
    seen = _capture_start(svc, monkeypatch)
    svc.start("tgt", apply=True)
    assert seen["tgt"]["uniq"] == "LEGACY-U"                     # unique flat legacy still applied


def _cfg_has_flat(svc, key):
    from lhpc.core import config as cfgmod
    return key in cfgmod.load_stack_config(svc._paths, "ostack2")


# --- component identity through the whole stack-target parameter pipeline --------------------

_ID_COLLIDE_MANIFEST = '''
[[stack]]
id = "ids"
name = "Id Stack"
main = "tgt"
[[stack.component]]
id = "tgt"
name = "Target"
kind = "service"
run = "true"
readiness = "process"
depends_on = ["dep"]
  [[stack.component.param]]
  name = "call"
  kind = "str"
  validator = "callsign"
  default = ""
[[stack.component]]
id = "dep"
name = "Dependency"
kind = "service"
run = "true"
readiness = "process"
  [[stack.component.param]]
  name = "call"
  kind = "str"
  validator = "callsign"
  default = ""
'''


def _id_collide_svc(tmp_path):
    m = tmp_path / "ids.toml"; m.write_text(_ID_COLLIDE_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(manifest_path=m, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))


def test_identity_selected_component_not_masked_by_sibling(tmp_path, monkeypatch):  # (5)
    from lhpc.core.lifecycle import Lifecycle
    svc = _id_collide_svc(tmp_path)
    def boom(*a, **k):
        raise LifecycleSeam()
    monkeypatch.setattr(svc, "write_config_files", boom)
    monkeypatch.setattr(Lifecycle, "start", boom)
    # the SELECTED licensed field (tgt.call) is EMPTY (no global set); a later same-named
    # component (dep.call) is valid — the start must still BLOCK on tgt, before any
    # lifecycle side effect (no LifecycleSeam), never masked by the sibling's valid value.
    assert svc.save_config_bundle("ids", values={"dep.call": "XX0XXA-1"}).ok
    res = svc.start("ids", apply=True)
    assert res.ok is False and "callsign" in res.summary.lower()
    assert "c_tgt__call" in (res.data.get("enforce_fields") or [])               # selected component's field


def test_qualified_identity_valid_reaches_start_seam(tmp_path, monkeypatch):     # (6)
    from lhpc.core.lifecycle import Lifecycle
    svc = _id_collide_svc(tmp_path)
    def boom(*a, **k):
        raise LifecycleSeam()
    monkeypatch.setattr(Lifecycle, "start", boom)                               # controlled non-hardware seam
    assert svc.save_config_bundle("ids", values={"tgt.call": "XX0XXA-5", "dep.call": "XX0XXA-6"}).ok
    with pytest.raises(LifecycleSeam):
        svc.start("ids", apply=True)


def test_unique_name_stack_stays_bare_no_regression(tmp_path):                   # (7)
    svc = _svc(tmp_path)
    rows = svc.config_param_fields("voice")                                      # voice: unique names
    assert rows
    for r in rows:
        assert "__" not in r["field"] and "." not in r["key"]                    # bare fields/keys preserved


# --- permanent Config page: component-aware (collision fixture) ------------------------------

def test_config_view_identity_and_values_per_component(tmp_path):
    svc = _id_collide_svc(tmp_path)
    svc.save_config_bundle("ids", values={"tgt.call": "XX0XXA-2", "dep.call": "XX0XXA-1"})
    cv = svc.config_view("ids")
    tgt = next(c for c in cv["components"] if c["id"] == "tgt")
    dep = next(c for c in cv["components"] if c["id"] == "dep")
    # each component's own value is shown independently of its same-named sibling
    assert tgt["values"]["call"] == "XX0XXA-2" and dep["values"]["call"] == "XX0XXA-1"
    assert tgt["fields"]["call"] == "c_tgt__call" and dep["fields"]["call"] == "c_dep__call"


def test_config_unique_fields_stay_bare_and_flat(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    for f in svc.config_param_fields("kiss"):
        assert "__" not in f["field"] and "." not in f["key"]        # bare fields/keys preserved
    assert svc.save_stack_config("kiss", {"kiss_port": "8002"}).ok    # canonical delegate
    assert cfgmod.load_stack_config(svc._paths, "kiss", svc._config_band("kiss", "")).get("kiss_port") == "8002"   # flat key


def test_save_stack_config_rejects_unqualified_dup_and_unknown(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _scope2_svc(tmp_path)
    r1 = svc.save_stack_config("ostack2", {"rp": "X"})               # unqualified duplicate
    assert r1.ok is False and "multiple components" in "; ".join(r1.details)
    r2 = svc.save_stack_config("ostack2", {"nope": "Y"})            # unknown field
    assert r2.ok is False and "unknown config field" in "; ".join(r2.details)
    assert cfgmod.load_stack_config(svc._paths, "ostack2") == {}     # NO mutation on rejection
    assert svc.save_stack_config("ostack2", {"tgt.rp": "RP-T"}).ok  # valid qualified persists scoped
    assert cfgmod.load_stack_config(svc._paths, "ostack2").get("__r__tgt__rp") == "RP-T"


# --- overrides-only config storage (enables automatic self-update config preservation) ------

def test_config_stores_overrides_only(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    p = next(pp for pp in svc.run_params_for("kiss") if pp.name == "tx_freq")
    default = svc._param_default_canon(p, "", "")
    # saving the current default persists NOTHING, yet the effective value is still the default
    assert svc.save_config_bundle("kiss", values={"tx_freq": default}).ok
    assert "tx_freq" not in cfgmod.load_stack_config(svc._paths, "kiss", svc._config_band("kiss", ""))
    assert svc.stack_config("kiss")["tx_freq"] == default
    # saving a real override persists it and survives reload
    assert svc.save_config_bundle("kiss", values={"tx_freq": "434.500"}).ok
    assert cfgmod.load_stack_config(svc._paths, "kiss", svc._config_band("kiss", "")).get("tx_freq") == "434.500"
    assert svc.stack_config("kiss")["tx_freq"] == "434.500"
    # saving it back to the default clears the stored override again
    assert svc.save_config_bundle("kiss", values={"tx_freq": default}).ok
    assert "tx_freq" not in cfgmod.load_stack_config(svc._paths, "kiss", svc._config_band("kiss", ""))


def test_value_at_old_default_follows_new_default(tmp_path):
    # Simulate a self-update that changes a manifest default: a value stored while it equalled the
    # OLD default must resolve to the NEW default; a genuine override must be preserved.
    from lhpc.core import config as cfgmod
    man = tmp_path / "m.toml"
    man.write_text(
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="opt"\n  kind="str"\n  default="OLD"\n'
    )
    svc = ControllerService(manifest_path=man, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    # user leaves it at the (old) default -> nothing stored
    assert svc.save_config_bundle("s", values={"opt": "OLD"}).ok
    assert cfgmod.load_stack_config(svc._paths, "s") == {}
    # "update" to a manifest whose default changed OLD -> NEW
    man.write_text(man.read_text().replace('default="OLD"', 'default="NEW"'))
    svc2 = ControllerService(manifest_path=man, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))
    assert svc2.stack_config("s")["opt"] == "NEW"                 # at-old-default -> follows new default
    # a genuine override is preserved across the same update
    assert svc2.save_config_bundle("s", values={"opt": "MINE"}).ok
    man.write_text(man.read_text().replace('default="NEW"', 'default="NEWER"'))
    svc3 = ControllerService(manifest_path=man, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))
    assert svc3.stack_config("s")["opt"] == "MINE"               # override preserved


def test_empty_non_default_override_is_kept(tmp_path):
    # A value that DIFFERS from a non-empty default but is empty ("unset") is a genuine override and
    # must still persist (two-phase write must not drop it).
    from lhpc.core import config as cfgmod
    man = tmp_path / "m.toml"
    man.write_text(
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="opt"\n  kind="str"\n  default="D"\n'
    )
    svc = ControllerService(manifest_path=man, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    assert svc.save_config_bundle("s", values={"opt": ""}).ok
    assert cfgmod.load_stack_config(svc._paths, "s").get("opt") == ""   # empty override persisted
    assert svc.stack_config("s")["opt"] == ""


# ---- an invalid SAVED launch value refuses before any mutation -------------------

def _spy_stops(monkeypatch):
    from lhpc.core.services import ControllerService as _CS
    calls = []
    orig = _CS.stop
    monkeypatch.setattr(_CS, "stop", lambda self, t, *a, **k: calls.append(t) or orig(self, t, *a, **k))
    return calls


def test_an_invalid_saved_run_param_refuses_plan_apply_and_restart_before_the_stop(tmp_path, monkeypatch, set_call):
    """The launch validates argv values (`expand_argv`); a stored value that no longer passes —
    an obsolete rule, a hand edit — used to surface only there, AFTER a restart had stopped the
    running stack. The saved-config preflight dry-expands the argv first, on the plan and the
    apply alike, so the refusal comes before owner stops, config generation or the stop leg."""
    svc = _svc(tmp_path)
    set_call(svc)
    _seed_raw(svc, "kiss", {"tx_freq": "banana"})                  # past the validating bundle
    stops = _spy_stops(monkeypatch)
    plan = svc.start("kiss", apply=False)
    assert not plan.ok and "invalid saved configuration for loraham-kiss-tnc" in plan.summary
    assert "banana" in plan.summary and plan.next_commands == ["lhpc config kiss"]
    res = svc.start("kiss", apply=True, stop_owners=True)
    assert not res.ok and "invalid saved configuration" in res.summary
    res = svc.restart("kiss", apply=True)
    assert not res.ok and "Cannot restart 'kiss': invalid saved configuration" in res.summary
    assert stops == []                                               # nothing was ever stopped
    # a valid value passes the same seam
    _seed_raw(svc, "kiss", {"tx_freq": "434.500"})
    assert svc._saved_launch_refusal("kiss", "", "start") is None
    # the daemon's stored file is NOT a launch input (its argv is rebuilt per band at spawn): a
    # stale key there (a `radio` choice the manifest no longer has) refuses no client start
    _seed_raw(svc, "daemon", {"radio": "both"})
    assert svc._saved_launch_refusal("kiss", "", "start") is None
    assert svc.start("kiss", apply=False).ok


def test_an_invalid_target_start_with_a_conflicting_owner_never_stops_the_owner(tmp_path, monkeypatch, set_call):
    # meshtastic (marked running on 433) owns that radio; kiss has an invalid saved value: the
    # owner stop that `stop_owners=True` would run must not happen for a start that cannot launch.
    from lhpc.core import config as _cfg
    svc = ControllerService(system=FakeSystem(cmdlines_data={200: ["meshtasticd"]}).system,
                            paths=Paths(runtime_root=tmp_path))
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    _cfg.save_hardware_setup(svc._paths, "loraham"); svc._invalidate_config()
    set_call(svc)
    svc._set_running_band("meshtastic", "433")
    assert [b.get("holder_stack") for b in svc.run_blockers("kiss")], "precondition: an owner"
    _seed_raw(svc, "kiss", {"tx_freq": "banana"})
    stops = _spy_stops(monkeypatch)
    res = svc.start("kiss", apply=True, stop_owners=True)
    assert not res.ok and "invalid saved configuration" in res.summary, res.summary
    assert stops == []
