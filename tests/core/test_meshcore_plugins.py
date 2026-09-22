"""The controller's side of the MeshCore plugin-manager marker: the verdict function (in
lockstep with the host's copy), and the gates it drives — controller uninstall-prep (after the
client stops, before the shared daemon), and `update` / `uninstall` / `clean` for anything that
reaches the MeshCore stack, after their authoritative running rechecks."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

from lhpc.core import meshcore_plugins as mp
from lhpc.core import source_registry
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import RunState
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from repo_paths import REPO

BOOT_A = "aaaaaaaa-0000-0000-0000-000000000001"
BOOT_B = "bbbbbbbb-0000-0000-0000-000000000002"


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    svc.bootstrap(apply=True)
    return svc


def _boot(monkeypatch, tmp_path, value):
    f = tmp_path / "boot_id"
    f.write_text(value)
    monkeypatch.setenv("LHPC_BOOT_ID_FILE", str(f))


def _write_marker(tmp_path, boot):
    p = mp.marker_path(Paths(runtime_root=tmp_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "boot_id": boot}))
    return p


# --- the verdict, in lockstep with the host -----------------------------------------------------

def _host_verdict():
    spec = importlib.util.spec_from_file_location(
        "host_plugin_manager",
        Path(REPO) / "lhpc" / "data" / "meshcore_host" / "meshcore_host" / "plugin_manager.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.marker_verdict


FIXTURES = [
    ("absent", None, BOOT_A, "absent"),
    ("same boot", json.dumps({"version": 1, "boot_id": BOOT_A}), BOOT_A, "unsafe"),
    ("other boot", json.dumps({"version": 1, "boot_id": BOOT_B}), BOOT_A, "safe"),
    ("not json", "not json", BOOT_A, "unprovable"),
    ("a list", "[]", BOOT_A, "unprovable"),
    ("version 2", json.dumps({"version": 2, "boot_id": BOOT_A}), BOOT_A, "unprovable"),
    ("no boot id", json.dumps({"version": 1}), BOOT_A, "unprovable"),
    ("empty boot id", json.dumps({"version": 1, "boot_id": ""}), BOOT_A, "unprovable"),
    ("current boot unknown", json.dumps({"version": 1, "boot_id": BOOT_B}), "", "unprovable"),
]


@pytest.mark.parametrize("label,content,current,expect", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_verdicts_and_the_host_copy_agree(tmp_path, label, content, current, expect):
    p = tmp_path / ".lhpc-plugin-manager-active"
    if content is not None:
        p.write_text(content)
    assert mp.marker_verdict(p, current) == expect
    assert _host_verdict()(p, current) == expect            # the lockstep copy, same fixture


def test_unreadable_marker_is_unprovable(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    p = tmp_path / "m"
    p.write_text(json.dumps({"version": 1, "boot_id": BOOT_B}))
    p.chmod(0)
    try:
        assert mp.marker_verdict(p, BOOT_A) == "unprovable"
        assert _host_verdict()(p, BOOT_A) == "unprovable"
    finally:
        p.chmod(0o600)


def test_the_two_verdict_functions_are_textually_identical():
    """Not just behaviourally: a divergence in one copy must show up in the diff review."""
    import inspect
    here = inspect.getsource(mp.marker_verdict)
    host = inspect.getsource(_host_verdict())
    assert here == host


def test_unclean_refusal_only_for_unsafe_and_unprovable(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    _boot(monkeypatch, tmp_path, BOOT_A)
    assert mp.unclean_refusal(paths, "x") is None                       # absent
    _write_marker(tmp_path, BOOT_B)
    assert mp.unclean_refusal(paths, "x") is None                       # other boot
    _write_marker(tmp_path, BOOT_A)
    r = mp.unclean_refusal(paths, "update 'meshcore'")
    assert r is not None and not r.ok and "reboot" in r.summary and "update 'meshcore'" in r.summary
    assert r.data["reason"] == "meshcore-plugins"
    mp.marker_path(paths).write_text("garbage")
    r = mp.unclean_refusal(paths, "x")
    assert r is not None and "cannot prove" in r.summary
    _boot(monkeypatch, tmp_path, "")
    _write_marker(tmp_path, BOOT_B)
    assert mp.unclean_refusal(paths, "x") is not None                   # no boot id: unprovable


def test_touches_meshcore_covers_stack_components_and_nothing_else(tmp_path):
    svc = _svc(tmp_path)
    assert mp.touches_meshcore(svc, {"meshcore"})
    assert mp.touches_meshcore(svc, {"meshcore-node"})
    assert mp.touches_meshcore(svc, {"openhop-repeater-src", "kiss"})
    assert not mp.touches_meshcore(svc, {"kiss", "loraham-kiss-tnc", "graywolf"})
    assert not mp.touches_meshcore(svc, set())


# --- controller uninstall-prep ----------------------------------------------------------------------

def _clean_stop(life, comp, band=""):
    return CompResult(component=comp.id, stack=comp.id, action="stop",
                      outcome=Outcome.STOPPED, summary="stopped")


def _running_then_clean(svc, running_ids):
    base = svc.build_snapshot(fresh=True)
    running = svc.build_snapshot(fresh=True)
    for ss in running.stacks:
        for cid, cs in ss.components.items():
            if cid in running_ids:
                cs.run_state = RunState.RUNNING
    calls = {"n": 0}

    def snap(self, *, fresh=False):
        calls["n"] += 1
        return running if calls["n"] == 1 else base
    return snap


def test_prep_healthy_running_meshcore_clears_its_marker_on_the_stop_and_succeeds(tmp_path, monkeypatch):
    """A healthy manager carries a current-boot marker; the prep's clean stop of MeshCore clears
    it (simulated here the way the host does it), so the check — placed AFTER the client stops —
    passes and the prep is quiescent."""
    svc = _svc(tmp_path)
    _boot(monkeypatch, tmp_path, BOOT_A)
    marker = _write_marker(tmp_path, BOOT_A)
    monkeypatch.setattr(ControllerService, "build_snapshot",
                        _running_then_clean(svc, {"meshcore-node"}))

    def stop_clears(life, comp, band=""):
        if comp.id == "meshcore-node":
            marker.unlink()                                 # the host's rc == 0 path
        return _clean_stop(life, comp, band)
    monkeypatch.setattr(Lifecycle, "stop", stop_clears)
    r = svc.controller_uninstall_prep()
    assert r.ok and r.data.get("quiescent"), r.summary


def test_prep_orphan_marker_refuses_after_the_client_stops_and_before_the_daemon(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)                           # MeshCore "stopped", marker stays
    daemon_ids = {cid for ss in svc.build_snapshot(fresh=True).stacks
                  if ss.stack.main == svc.DAEMON_ID for cid in ss.components}
    monkeypatch.setattr(ControllerService, "build_snapshot",
                        _running_then_clean(svc, daemon_ids | {"loraham-kiss-tnc"}))
    stopped = []

    def rec(life, comp, band=""):
        stopped.append(comp.id)
        return _clean_stop(life, comp, band)
    monkeypatch.setattr(Lifecycle, "stop", rec)
    r = svc.controller_uninstall_prep()
    assert not r.ok and r.data.get("prep_blocked") == "meshcore_plugins"
    assert "reboot" in r.summary
    assert "loraham-kiss-tnc" in stopped                      # the client stop ran ...
    assert not (daemon_ids & set(stopped))                    # ... the shared daemon was NOT stopped


@pytest.mark.parametrize("case", ["other-boot", "absent"])
def test_prep_is_quiescent_with_no_or_an_old_marker(tmp_path, monkeypatch, case):
    svc = _svc(tmp_path)
    _boot(monkeypatch, tmp_path, BOOT_A)
    if case == "other-boot":
        _write_marker(tmp_path, BOOT_B)
    r = svc.controller_uninstall_prep()
    assert r.ok and r.data.get("quiescent")


@pytest.mark.parametrize("case", ["malformed", "no-boot-id"])
def test_prep_refuses_when_the_marker_cannot_be_proven(tmp_path, monkeypatch, case):
    svc = _svc(tmp_path)
    if case == "malformed":
        _boot(monkeypatch, tmp_path, BOOT_A)
        mp.marker_path(svc._paths).parent.mkdir(parents=True, exist_ok=True)
        mp.marker_path(svc._paths).write_text("{not json")
    else:
        _boot(monkeypatch, tmp_path, "")
        _write_marker(tmp_path, BOOT_B)
    r = svc.controller_uninstall_prep()
    assert not r.ok and r.data.get("prep_blocked") == "meshcore_plugins"


# --- update / uninstall / clean -----------------------------------------------------------------------

def _mksrc(tmp_path, *rel):
    for r in rel:
        (tmp_path / "src" / r).mkdir(parents=True, exist_ok=True)


def _own(tmp_path, rel, comps):
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord(f"src/{rel}", "", "pinned", "", time.time(), "",
                                       tuple(comps)))


def _svc_plain(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


@pytest.mark.parametrize("target", ["meshcore", "meshcore-node", ""])
def test_uninstall_refuses_meshcore_targets_and_bulk_on_a_current_boot_marker(tmp_path, monkeypatch, target):
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    res = svc.uninstall(target, apply=True)
    assert not res.ok and "reboot" in res.summary and res.data.get("reason") == "meshcore-plugins"
    assert (tmp_path / "src" / "openhop-core").exists()     # zero mutation


def test_uninstall_of_another_stack_is_not_gated(tmp_path, monkeypatch):
    _mksrc(tmp_path, "loraham-kiss-tnc")
    _own(tmp_path, "loraham-kiss-tnc", ("loraham-kiss-tnc",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    res = svc.uninstall("kiss", apply=True)
    assert res.data.get("reason") != "meshcore-plugins" and "reboot" not in res.summary


def test_uninstall_proceeds_on_an_old_marker(tmp_path, monkeypatch):
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_B)
    svc = _svc_plain(tmp_path)
    res = svc.uninstall("meshcore", apply=True)
    assert res.data.get("reason") != "meshcore-plugins"


def test_clean_purge_refuses_on_a_current_boot_marker_and_leaves_state_alone(tmp_path, monkeypatch):
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    marker = _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    res = svc.clean("meshcore", apply=True, purge=True)
    assert not res.ok and "reboot" in res.summary
    assert marker.exists() and (tmp_path / "src" / "openhop-core").exists()
    # kiss is never gated by MeshCore's marker
    _mksrc(tmp_path, "loraham-kiss-tnc")
    _own(tmp_path, "loraham-kiss-tnc", ("loraham-kiss-tnc",))
    res = svc.clean("kiss", apply=True, purge=True)
    assert "reboot" not in res.summary


def test_clean_purge_of_meshcore_keeps_state_openhop_when_it_proceeds(tmp_path, monkeypatch):
    """`lhpc clean meshcore --purge` removes sources, config, markers and logs — NOT the repeater's
    application state under state/openhop (the plugins and the marker live there)."""
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    marker = _write_marker(tmp_path, BOOT_B)                  # an old boot: not a gate
    (tmp_path / "state" / "openhop" / "plugins" / "demo").mkdir(parents=True)
    svc = _svc_plain(tmp_path)
    res = svc.clean("meshcore", apply=True, purge=True)
    assert "reboot" not in res.summary
    assert marker.exists() and (tmp_path / "state" / "openhop" / "plugins" / "demo").exists()


@pytest.mark.parametrize("target", ["meshcore", "meshcore-node", ""])
def test_update_refuses_meshcore_targets_and_bulk_on_a_current_boot_marker(tmp_path, monkeypatch, target):
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    if target and svc.on_binary_channel(target):
        pytest.skip("binary-channel target: update is a binary install, no source path")
    res = svc.update(target, apply=True)
    assert not res.ok and "reboot" in res.summary and res.data.get("reason") == "meshcore-plugins"


def test_update_of_kiss_is_not_gated(tmp_path, monkeypatch):
    _mksrc(tmp_path, "loraham-kiss-tnc")
    _own(tmp_path, "loraham-kiss-tnc", ("loraham-kiss-tnc",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    res = svc.update("kiss", apply=True)
    assert res.data.get("reason") != "meshcore-plugins"


def test_the_dry_runs_are_not_gated(tmp_path, monkeypatch):
    """The gate sits after the lock-held running recheck on the APPLY path; a dry run returns
    before it — by design (no dry-run machinery exists for it)."""
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    for res in (svc.uninstall("meshcore", apply=False), svc.clean("meshcore", apply=False)):
        assert res.data.get("reason") != "meshcore-plugins"


def test_boot_id_seam_matches_the_lifecycles(tmp_path, monkeypatch):
    """The host's `boot_id()` copy honours the same LHPC_BOOT_ID_FILE seam as the controller."""
    from lhpc.core.lifecycle import current_boot_id
    spec = importlib.util.spec_from_file_location(
        "host_plugin_manager",
        Path(REPO) / "lhpc" / "data" / "meshcore_host" / "meshcore_host" / "plugin_manager.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _boot(monkeypatch, tmp_path, "  " + BOOT_A + "\n")
    assert mod.boot_id() == current_boot_id() == BOOT_A
    monkeypatch.setenv("LHPC_BOOT_ID_FILE", str(tmp_path / "missing"))
    assert mod.boot_id() == current_boot_id()               # both fall back to the kernel's
    assert sys.platform != "linux" or mod.boot_id()


@pytest.mark.parametrize("target", ["meshcore", "meshcore-node"])       # build has no bulk form
def test_build_refuses_meshcore_targets_on_a_current_boot_marker(tmp_path, monkeypatch, target):
    """A MeshCore build recreates src/openhop-core/.venv — the interpreter an orphaned plugin
    manager may still run from. The gate sits under the build's source lock, before any step."""
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    steps = []
    monkeypatch.setattr(Lifecycle, "build",
                        lambda self, comp, *a, **k: steps.append(comp.id) or None)
    res = svc.build(target, apply=True)
    assert not res.ok and "reboot" in res.summary and res.data.get("reason") == "meshcore-plugins"
    assert steps == []                                      # zero mutation


def test_build_of_kiss_is_not_gated_and_an_old_marker_lets_meshcore_build(tmp_path, monkeypatch):
    _mksrc(tmp_path, "openhop-core", "openhop-repeater", "loraham-kiss-tnc")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _own(tmp_path, "loraham-kiss-tnc", ("loraham-kiss-tnc",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    res = svc.build("kiss", apply=True)
    assert res.data.get("reason") != "meshcore-plugins"
    _write_marker(tmp_path, BOOT_B)
    res = svc.build("meshcore", apply=True)
    assert res.data.get("reason") != "meshcore-plugins"
    assert svc.build("meshcore", apply=False).data.get("reason") != "meshcore-plugins"   # dry run


def test_build_refusal_names_the_stop_when_meshcore_is_healthy_and_running(tmp_path, monkeypatch):
    """A healthy repeater carries a current-boot marker too; `build` has no running check, so its
    refusal must name the plain remedy (stop the stack), not a reboot."""
    _mksrc(tmp_path, "openhop-core", "openhop-repeater")
    _own(tmp_path, "openhop-core", ("meshcore-node",))
    _own(tmp_path, "openhop-repeater", ("openhop-repeater-src",))
    _boot(monkeypatch, tmp_path, BOOT_A)
    _write_marker(tmp_path, BOOT_A)
    svc = _svc_plain(tmp_path)
    snap = svc.build_snapshot(fresh=True)
    for ss in snap.stacks:
        for cid, cs in ss.components.items():
            if cid == "meshcore-node":
                cs.run_state = RunState.RUNNING
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *, fresh=False: snap)
    monkeypatch.setattr(Lifecycle, "build", lambda self, comp, *a, **k: None)
    res = svc.build("meshcore", apply=True)
    assert not res.ok and "stop the stack first" in res.summary and "reboot" not in res.summary
    assert res.next_commands == ["lhpc stack stop meshcore --yes"]
    assert res.data.get("reason") == "meshcore-plugins"
