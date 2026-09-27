"""R21: the MeshCom node runs on its own flash image under state/, so an update or rebuild keeps it.

Before, run.sh booted the build output inside the checkout's publish root, which every binary
install/update and `lhpc build meshcom` replaces — the node lost every setting and rewound its message
counter."""
import pytest

from lhpc.core import commands
from lhpc.core.paths import Paths
from lhpc.core.services import ControllerService
from lhpc.core.probes.backends import FakeSystem


def _argv(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = svc.stack("meshcom").component("meshcom-qemu")
    base = {p.name: p.default for p in comp.run_params}
    return svc, comp, commands.expand_argv(comp.run_argv, comp, base, svc.config().operator,
                                           "/rt", "/src", "")


def test_meshcom_boots_its_node_image_under_state(tmp_path):
    _svc, _comp, argv = _argv(tmp_path)
    assert argv[argv.index("--node-image") + 1] == "/rt/state/meshcom/node-flash.bin"


def test_the_node_image_is_outside_every_binary_publish_root(tmp_path):
    svc, _comp, argv = _argv(tmp_path)
    node = argv[argv.index("--node-image") + 1][len("/rt/"):]
    for root in svc.binary_spec("meshcom").publish_roots:
        assert not (node == root or node.startswith(root.rstrip("/") + "/")), root


def _seed_node_image(tmp_path):
    d = tmp_path / "state" / "meshcom"
    d.mkdir(parents=True)
    (d / "node-flash.bin").write_bytes(b"\xff" * 16)
    return d


def test_clean_purge_removes_the_node_image(tmp_path):
    """Maintainer's decision (2026-09-26): "Clean all" means a fresh node — the node image and
    with it the node's settings go; the dry run says so."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    d = _seed_node_image(tmp_path)
    plan = svc.clean("meshcom")
    assert any("[remove] state/meshcom" in ln for ln in plan.details), plan.details
    res = svc.clean("meshcom", apply=True, purge=True)
    assert not d.exists(), res.details
    assert any("[removed] state/meshcom" in ln for ln in res.details), res.details


def test_uninstall_keeps_the_node_image(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    d = _seed_node_image(tmp_path)
    svc.uninstall("meshcom", apply=True)
    assert (d / "node-flash.bin").exists()


@pytest.mark.parametrize("bad", ["state", "state/../x", "config/meshcom", "state/a/b", "/state/x"])
def test_state_root_must_be_one_state_directory(bad):
    from lhpc.core.manifest import ManifestError, _state_root
    with pytest.raises(ManifestError):
        _state_root({"id": "x", "state_root": bad})
    assert _state_root({"id": "x", "state_root": "state/meshcom"}) == "state/meshcom"
