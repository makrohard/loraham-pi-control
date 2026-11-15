"""The lab's helpers look where a LISTED instance keeps its state: a MeshCore plugin manager and its marker live
under `state/<rid>/openhop` (the repeater's state_dir default `{runtime}/state/{instance_dir}openhop`), and the
lab's reset seeds a node identity into every instance's own settings (K6). A helper that looked only at the
template's paths found nothing whether a manager ran or not, and a start of the instance the lab names was refused
for a missing identity."""
from __future__ import annotations

import subprocess
import sys

import pytest
from lhpc_testlab import ops
from lhpc_testlab.release import (
    assert_plugin_manager_running,
    plugin_manager_marker,
    plugin_manager_markers,
    plugin_manager_pids,
    plugin_managers,
)

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _manager(plugins_root):
    """A stand-in process whose argv is the manager's (`repeater.plugins --plugins-root <root>`)."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "repeater.plugins",
                             "--plugins-root", str(plugins_root)], start_new_session=True)


@pytest.mark.parametrize("state_dir", ["r2/openhop", "openhop"])          # a listed instance's, the template's
def test_a_managers_root_is_found_and_another_labs_is_not(tmp_path, state_dir):
    mine = _manager(tmp_path / "lab" / "state" / state_dir / "plugins")
    other = _manager(tmp_path / "other-lab" / "state" / state_dir / "plugins")
    prefixed = _manager(f"/y{tmp_path}/lab/state/{state_dir}/plugins")      # a lab at /y<root>: never this root
    try:
        assert plugin_manager_pids(tmp_path / "lab") == [mine.pid]
    finally:
        for p in (mine, other, prefixed):
            p.kill()
            p.wait(timeout=5)
    assert plugin_manager_pids(tmp_path / "lab") == []


@pytest.mark.parametrize("state_dir", ["r2/openhop", "openhop"])          # a listed instance's, the template's
def test_a_plugin_manager_marker_is_found(tmp_path, state_dir):
    assert plugin_manager_markers(tmp_path) == []
    marker = tmp_path / "state" / state_dir / ".lhpc-plugin-manager-active"
    marker.parent.mkdir(parents=True)
    marker.write_text("")
    assert plugin_manager_markers(tmp_path) == [marker]


def test_the_lab_seeds_a_node_identity_into_every_instances_own_settings(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "radios.toml").write_text(radios.render((radios.Radio("r1", "low", "loraham", "433"),
                                                                     radios.Radio("r2", "high", "loraham", "868"))))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    seeded, failed = ops._seed_node_identities(svc)
    assert failed is None and "meshtastic.node_name" in seeded, (seeded, failed)
    for rid in ("r1", "r2"):
        store = instances.store(svc, "meshtastic", "", instances.copy_of(svc, "meshtastic", rid)[0])
        assert store.get("node_name") == "LHPC Lab" and store.get("node_short") == "LAB", (rid, store)


def test_a_running_managers_marker_is_its_own_never_another_instances(tmp_path):
    # G1-RVH-CODE BLOCKING: r1's manager runs without its marker; a stale marker of r2 must not stand in for it
    plugins = tmp_path / "state" / "r1" / "openhop" / "plugins"
    stale = tmp_path / "state" / "r2" / "openhop" / ".lhpc-plugin-manager-active"
    stale.parent.mkdir(parents=True)
    stale.write_text("")
    m = _manager(plugins)
    try:
        with pytest.raises(AssertionError, match="marker missing while it runs"):
            assert_plugin_manager_running(tmp_path)                       # r2's stale marker does not count
        own = tmp_path / "state" / "r1" / "openhop" / ".lhpc-plugin-manager-active"   # where the host writes it
        own.parent.mkdir(parents=True)
        own.write_text("")
        assert plugin_manager_marker(plugins) == own
        assert_plugin_manager_running(tmp_path)                           # r1's own does
    finally:
        m.kill()
        m.wait(timeout=5)


def test_a_root_with_whitespace_keeps_its_managers_path(tmp_path):
    # G1-RVH-CODE round 2 BLOCKING: a split `pgrep -a` line cut the --plugins-root at the space
    root = tmp_path / "lab run"
    plugins = root / "state" / "r1" / "openhop" / "plugins"
    own = root / "state" / "r1" / "openhop" / ".lhpc-plugin-manager-active"
    own.parent.mkdir(parents=True)
    own.write_text("")
    m = _manager(plugins)
    try:
        assert_plugin_manager_running(root)                               # its own marker, found at the whole path
        assert plugin_managers(root) == [(m.pid, plugins)]
    finally:
        m.kill()
        m.wait(timeout=5)
