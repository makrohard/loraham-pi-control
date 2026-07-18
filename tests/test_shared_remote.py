"""One effective remote per shared source path: a checkout is ONE clone with ONE origin.
Saves atomically propagate a remote to every consumer (disclosed); conflicting submissions
are rejected; legacy divergent configuration blocks update/uninstall/clean/known-working
confirmation with a typed result and zero source mutation."""

import time

from lhpc.core import known_working, source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_URL_A = "https://github.com/fork-a/LoRaHAM_Daemon.git"
_URL_B = "https://github.com/fork-b/LoRaHAM_Daemon.git"


def _svc(tmp_path, cmdlines=None):
    return ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                             paths=Paths(runtime_root=tmp_path))


def _write_conflicting_overrides(tmp_path, a=_URL_A, b=_URL_B):
    """LEGACY hand-edited divergence: chat and igate (DIFFERENT stacks) share
    src/LoRaHAM_Daemon but point at different forks."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(
        f'[remotes]\nloraham-chat = "{a}"\nloraham-igate = "{b}"\n')


# --- save-time enforcement --------------------------------------------------------------------

def test_single_remote_save_propagates_to_all_consumers_and_discloses(tmp_path):
    svc = _svc(tmp_path)
    res = svc.save_component_remote("loraham-chat", _URL_A)
    assert res.ok
    assert any("loraham-igate" in d for d in res.details)              # disclosed propagation
    cfg = _svc(tmp_path).config()                                      # fresh read
    assert cfg.remotes.get("loraham-chat") == _URL_A
    assert cfg.remotes.get("loraham-igate") == _URL_A                  # same checkout, same remote
    # clearing propagates too
    res2 = svc.save_component_remote("loraham-igate", "")
    assert res2.ok
    cfg2 = _svc(tmp_path).config()
    assert "loraham-chat" not in cfg2.remotes and "loraham-igate" not in cfg2.remotes


def test_bundle_save_rejects_conflicting_shared_remotes(tmp_path):
    # kiss-tnc and kiss-serial (ONE stack) share src/loraham-kiss-tnc: a submission giving
    # them different remotes is rejected whole — zero mutation.
    svc = _svc(tmp_path)
    res = svc.save_config_bundle("kiss", values={}, remotes={
        "loraham-kiss-tnc": _URL_A, "loraham-kiss-serial": _URL_B})
    assert not res.ok
    assert any("conflicting remotes" in d for d in res.details)
    assert not (tmp_path / "config" / "local.toml").exists() or \
        "fork-a" not in (tmp_path / "config" / "local.toml").read_text()


def test_bundle_save_coherent_shared_remote_applies_to_both(tmp_path):
    svc = _svc(tmp_path)
    res = svc.save_config_bundle("kiss", values={}, remotes={
        "loraham-kiss-tnc": _URL_A, "loraham-kiss-serial": _URL_A})
    assert res.ok, res.details
    cfg = _svc(tmp_path).config()
    assert cfg.remotes.get("loraham-kiss-tnc") == _URL_A
    assert cfg.remotes.get("loraham-kiss-serial") == _URL_A


def test_bundle_save_expands_single_value_to_shared_group(tmp_path):
    # Submitting the remote for ONE consumer of the shared path applies it to the whole
    # group atomically, with an explicit disclosure in the result.
    svc = _svc(tmp_path)
    res = svc.save_config_bundle("kiss", values={},
                                 remotes={"loraham-kiss-tnc": _URL_A})
    assert res.ok, res.details
    assert any("shared checkout" in d and "loraham-kiss-serial" in d for d in res.details)
    cfg = _svc(tmp_path).config()
    assert cfg.remotes.get("loraham-kiss-serial") == _URL_A


# --- mutation gates on legacy divergent configuration -----------------------------------------

def test_update_blocked_by_conflicting_shared_remotes(tmp_path):
    _write_conflicting_overrides(tmp_path)
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    svc = _svc(tmp_path)
    res = svc.update("loraham-chat", apply=True)
    assert not res.ok
    assert "inconsistent" in res.summary or any("conflicting effective remotes" in d
                                                for d in res.details)
    assert (tmp_path / "src" / "LoRaHAM_Daemon").exists()              # zero mutation


def test_uninstall_and_clean_blocked_by_conflicting_shared_remotes(tmp_path):
    # kiss pair with divergent overrides: the shared path reaches removal (both consumers
    # targeted) and the coherence gate refuses before any capture/detach.
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(
        f'[remotes]\nloraham-kiss-tnc = "{_URL_A}"\nloraham-kiss-serial = "{_URL_B}"\n')
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    dest.mkdir(parents=True)
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "backfilled", "", time.time(),
                                       "", "", ("loraham-kiss-tnc", "loraham-kiss-serial")))
    res = _svc(tmp_path).uninstall("kiss", apply=True)
    assert not res.ok
    assert any("conflicting effective remotes" in d for d in res.details)
    assert dest.exists()                                               # zero mutation
    res2 = _svc(tmp_path).clean("kiss", apply=True, purge=True)
    assert not res2.ok
    assert any("conflicting effective remotes" in d for d in res2.details)
    assert dest.exists()


def test_confirm_known_working_blocked_by_conflicting_shared_remotes(tmp_path):
    _write_conflicting_overrides(tmp_path)
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    entries = {"loraham-chat": {"commit": "a" * 40, "selector": "dev", "remote": _URL_A,
                                "source_rel": "src/LoRaHAM_Daemon", "strategy": ""}}
    assert known_working.write_candidate(paths, "chat", entries, "433")
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/LoRaHAM_Daemon", _URL_A, "dev", "a" * 40, time.time(), "", "",
        ("loraham-chat", "loraham-igate")))
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})             # chat RUNNING
    res = svc.confirm_known_working("chat")
    assert not res.ok
    assert "conflicting effective remotes" in res.summary
    assert known_working.load(paths, "chat") == []                     # nothing recorded


def test_coherent_shared_remote_update_reaches_adoption(tmp_path):
    # With ONE coherent override for the whole group, the gate passes and the update
    # proceeds to the (FakeSystem-refused) adoption step — not the coherence refusal.
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(
        f'[remotes]\nloraham-chat = "{_URL_A}"\nloraham-igate = "{_URL_A}"\n')
    svc = _svc(tmp_path)
    res = svc.update("loraham-chat", apply=True)
    assert "inconsistent" not in res.summary
    assert not any("conflicting effective remotes" in d for d in res.details)


# --- FINAL M1: install gated on shared-source remote coherence -------------------------------

def test_install_blocked_by_conflicting_shared_remotes_zero_mutation(tmp_path):
    _write_conflicting_overrides(tmp_path)
    for sub in ("bin", "src", "build", "start", "config", "profiles", "systemd", "state",
                "logs", "docs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    svc = _svc(tmp_path)
    for apply in (False, True):                        # planning AND mutation are gated
        res = svc.install("chat", apply=apply)
        assert not res.ok
        assert "inconsistent" in res.summary
    assert not (tmp_path / "src" / "LoRaHAM_Daemon").exists()         # zero source mutation
    assert not list((tmp_path / "state").glob("source-registry/*"))   # zero registry mutation
    assert not list((tmp_path / "state").glob("source-txn/*"))        # zero journal mutation


def test_install_adopts_each_coherent_shared_group_once(tmp_path, monkeypatch):
    # ONE adoption per shared source path — not one attempt per consumer component.
    from lhpc.core.install import Installer
    for sub in ("bin", "src", "build", "start", "config", "profiles", "systemd", "state",
                "logs", "docs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    calls = []
    def fake_adopt(self, comp, force=False, source="pinned", pinned_expected=None,
                   locked=False):
        calls.append(comp.source.path)
        from lhpc.core.install import PlanAction
        return PlanAction("adopt", "", f"adopt {comp.id}", status="failed", detail="(fake)")
    monkeypatch.setattr(Installer, "adopt_source", fake_adopt)
    svc = _svc(tmp_path)
    svc.install("kiss", apply=True)                    # kiss-tnc + kiss-serial share ONE path
    assert calls.count("src/loraham-kiss-tnc") == 1    # adopted once per coherent group
