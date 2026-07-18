"""Lifecycle serialization for applied update/uninstall/clean: config-stable -> source-txn
index -> ALL source-path locks -> FRESH runtime recheck -> plan/mutation -> candidate
retirement, with zero mutation on a post-lock refusal. Races are injected deterministically
through the `_op_seam` hook using separate service instances over one runtime root."""

import json
import threading
import time

from lhpc.core import known_working, source_registry
from lhpc.core.install import Installer, PlanAction
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_KISS_REMOTE = "https://github.com/makrohard/loraham-kiss-tnc.git"


def _svc(tmp_path, cmdlines=None):
    fake = FakeSystem(cmdlines_data=cmdlines or {})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    svc._fake = fake                      # direct handle for deterministic race injection
    return svc


def _seed_kiss_env(tmp_path):
    """Installed kiss checkout + registry record + candidate marker + config/log/store."""
    paths = Paths(runtime_root=tmp_path)
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    dest.mkdir(parents=True)
    (dest / "code.c").write_text("v1")
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/loraham-kiss-tnc", "", "legacy", "", time.time(), "", "",
        ("loraham-kiss-tnc", "loraham-kiss-serial")))
    assert known_working.write_candidate(
        paths, "kiss", {"loraham-kiss-tnc": {"commit": "a" * 40, "selector": "dev",
                                             "remote": "", "source_rel":
                                             "src/loraham-kiss-tnc", "strategy": ""}}, "433")
    known_working.record(paths, "kiss",
                         {"loraham-kiss-tnc": {"commit": "a" * 40, "selector": "dev",
                                               "remote": "",
                                               "source_rel": "src/loraham-kiss-tnc",
                                               "strategy": ""}}, {"confirmed_at": 1.0})
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "stacks" / "kiss.toml").write_text('x = "1"\n')
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "build-loraham-kiss-tnc.log").write_text("log")
    return paths, dest


def _snapshot_state(tmp_path, paths):
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    return {
        "source": (dest / "code.c").read_text() if (dest / "code.c").exists() else None,
        "record": source_registry.read_record(paths, "src/loraham-kiss-tnc"),
        "candidate": known_working.read_candidate(paths, "kiss"),
        "store": known_working.load(paths, "kiss"),
        "config": (tmp_path / "config" / "stacks" / "kiss.toml").exists(),
        "log": (tmp_path / "logs" / "build-loraham-kiss-tnc.log").exists(),
        "journals": sorted(p.name for p in
                           (tmp_path / "state" / "source-txn").glob("*.json"))
        if (tmp_path / "state" / "source-txn").exists() else [],
    }


def _inject_running_at(monkeypatch, svc, point: str):
    """At seam `point`, a kiss component STARTS (appears in the process table) — exactly
    the window between an operation's preflight and its lock acquisition."""
    fired = {"done": False}
    real = svc._op_seam
    def seam(p):
        if p == point and not fired["done"]:
            fired["done"] = True
            svc._fake.cmdlines_data[555] = ["loraham_kiss_tnc"]
        return real(p)
    monkeypatch.setattr(svc, "_op_seam", seam)
    return fired


def test_update_rechecks_running_after_locks(tmp_path, monkeypatch):
    paths, dest = _seed_kiss_env(tmp_path)
    svc = _svc(tmp_path)
    before = _snapshot_state(tmp_path, paths)
    fired = _inject_running_at(monkeypatch, svc, "update-preflight")
    res = svc.update("kiss", apply=True, source="dev")
    assert fired["done"]
    assert not res.ok and "started while" in res.summary
    assert _snapshot_state(tmp_path, paths) == before      # ZERO mutation, marker preserved


def test_uninstall_rechecks_running_after_locks(tmp_path, monkeypatch):
    paths, dest = _seed_kiss_env(tmp_path)
    svc = _svc(tmp_path)
    before = _snapshot_state(tmp_path, paths)
    fired = _inject_running_at(monkeypatch, svc, "uninstall-preflight")
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok and "started while" in res.summary
    assert _snapshot_state(tmp_path, paths) == before      # sources/config/markers untouched


def test_clean_rechecks_running_after_locks(tmp_path, monkeypatch):
    paths, dest = _seed_kiss_env(tmp_path)
    svc = _svc(tmp_path)
    before = _snapshot_state(tmp_path, paths)
    fired = _inject_running_at(monkeypatch, svc, "clean-preflight")
    res = svc.clean("kiss", apply=True, purge=True)
    assert fired["done"]
    assert not res.ok and "started while" in res.summary
    assert _snapshot_state(tmp_path, paths) == before      # even config/log/markers untouched


def test_multi_source_update_holds_locks_across_groups(tmp_path, monkeypatch):
    # daemon stack updates TWO source groups (loraham-daemon + RadioLib). A Start injected
    # BETWEEN the groups contends on the still-held source locks: typed refusal, nothing
    # starts, and the second source is mutated only under the same original boundary.
    svc = _svc(tmp_path)
    start_results = []
    monkeypatch.setattr(
        Installer, "_adopt_locked",
        lambda self, comp, spec, dest, action, force, source, pinned_expected=None:
        PlanAction("adopt", str(dest), f"adopt {comp.id}", status="done", detail="(fake)"))
    real = svc._op_seam
    fired = {"n": 0}
    def seam(p):
        if p == "update-between-groups" and fired["n"] == 0:
            fired["n"] = 1
            svc2 = _svc(tmp_path)                          # separate service, same root
            start_results.append(svc2.start("daemon", apply=True))
        return real(p)
    monkeypatch.setattr(svc, "_op_seam", seam)
    res = svc.update("daemon", apply=True, source="dev")
    assert fired["n"] == 1
    assert res.ok, res.details                             # update completed both groups
    assert start_results and not start_results[0].ok      # the injected Start was refused
    blob = (start_results[0].summary + " ".join(start_results[0].details)).lower()
    assert "progress" in blob or "busy" in blob or "block" in blob or "lock" in blob
    assert not svc._fake.cmdlines_data                   # nothing actually started


def test_fresh_candidate_after_release_is_preserved(tmp_path, monkeypatch):
    # Retirement happens BEFORE the locks release, so only the STALE pre-update marker is
    # retired; a fresh marker written by a Start AFTER release persists untouched.
    paths, dest = _seed_kiss_env(tmp_path)
    other = {"chat": known_working.write_candidate(
        paths, "chat", {"loraham-chat": {"commit": "c" * 40, "selector": "dev", "remote": "",
                                         "source_rel": "src/LoRaHAM_Daemon",
                                         "strategy": ""}}, "433")}
    assert other["chat"]
    monkeypatch.setattr(
        Installer, "_adopt_locked",
        lambda self, comp, spec, dest_, action, force, source, pinned_expected=None:
        PlanAction("adopt", str(dest_), f"adopt {comp.id}", status="done", detail="(fake)"))
    svc = _svc(tmp_path)
    res = svc.update("kiss", apply=True, source="dev")
    assert res.ok, res.details
    assert known_working.read_candidate(paths, "kiss") is None      # STALE marker retired
    assert known_working.read_candidate(paths, "chat") is not None  # other stack untouched
    # a fresh healthy Start after the update writes a NEW candidate — preserved
    fresh = {"loraham-kiss-tnc": {"commit": "b" * 40, "selector": "dev", "remote": "",
                                  "source_rel": "src/loraham-kiss-tnc", "strategy": ""}}
    assert known_working.write_candidate(paths, "kiss", fresh, "433")
    assert known_working.read_candidate(paths, "kiss")["entries"] == fresh


def test_concurrent_remote_save_blocks_behind_update(tmp_path, monkeypatch):
    # A remote override save (EXCLUSIVE config lock) must WAIT while an applied update
    # holds config-stable (SHARED) — the update's identity/plan work never races a config
    # change, and the save lands coherently afterwards.
    paths, dest = _seed_kiss_env(tmp_path)
    svc = _svc(tmp_path)
    monkeypatch.setattr(
        Installer, "_adopt_locked",
        lambda self, comp, spec, dest_, action, force, source, pinned_expected=None:
        PlanAction("adopt", str(dest_), f"adopt {comp.id}", status="done", detail="(fake)"))
    save_done = threading.Event()
    save_result = []

    def do_save():
        svc2 = _svc(tmp_path)
        save_result.append(svc2.save_component_remote("loraham-kiss-tnc", _KISS_REMOTE))
        save_done.set()
    real = svc._op_seam
    state = {"blocked_while_locked": None}
    def seam(p):
        if p == "update-locked" and state["blocked_while_locked"] is None:
            t = threading.Thread(target=do_save, daemon=True)
            t.start()
            # while update holds config-stable, the exclusive save must NOT complete
            state["blocked_while_locked"] = not save_done.wait(0.4)
        return real(p)
    monkeypatch.setattr(svc, "_op_seam", seam)
    res = svc.update("kiss", apply=True, source="dev")
    assert res.ok, res.details
    assert state["blocked_while_locked"] is True           # save waited behind the update
    assert save_done.wait(5.0)                             # ...and completed after release
    assert save_result and save_result[0].ok
    cfg = _svc(tmp_path).config()                          # coherent shared-group result
    assert cfg.remotes.get("loraham-kiss-tnc") == _KISS_REMOTE
    assert cfg.remotes.get("loraham-kiss-serial") == _KISS_REMOTE
