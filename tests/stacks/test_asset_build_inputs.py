"""Packaged assets a build step consumes are build inputs (0.7.0).

A build step that bakes an lhpc-shipped asset into the built tree (`pip install {asset}/meshcore_host`,
a patch, a fetch script) makes the built tree depend on that asset, yet the component's own source is
pinned and does not move when the asset does. Found on a box that UPDATED to 0.7.0: the source tree
carried the passive MeshCore poller, the venv still ran the scanning one. The asset's content digest
is now part of the build-input sidecar, so a changed asset reads NOT built until rebuilt."""
from __future__ import annotations

import os
import re
import shutil

import pytest

from lhpc.core import assets
from lhpc.core import manifest as manifest_mod
from lhpc.core.lifecycle import BUILD_MARKER_TEXT
from lhpc.core.manifest import ManifestError
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# The shipped recipe, by hand: a new consumer joins by being listed here, a dropped one by being
# removed — the point is that the extraction sees every spelling the manifest uses. meshchat
# declares its steps as `[[...build_steps]]` tables where the others use the inline array; a regex
# over the file missed it, the parsed recipe must not.
EXPECTED = {
    "graywolf": ("scripts/graywolf-fetch.sh",),
    "meshtastic": ("scripts/meshtastic-link-gate.sh", "scripts/meshtastic-web-assets.sh"),
    "meshcom-qemu": ("scripts/meshtastic-link-gate.sh",),
    "meshcore-node": ("meshcore_host", "openhop-repeater-constraints.txt"),
    "meshcore-webui": ("meshcore-webui-constraints.txt",
                       "patches/meshcore-webui-lhpc-guards.patch",
                       "scripts/openhop-apply-patch.sh"),
    "meshchat": ("meshchat-constraints.txt", "meshchat-dist", "scripts/meshchat-assets.sh"),
}


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _comp(svc, cid):
    return next(c for st in svc.stacks() for c in st.components if c.id == cid)


def _stamp_built(svc, c):
    """What a real build writes: the marker in the checkout, the sidecar beside the artifact."""
    src = svc._lifecycle().source_dir(c)
    marker = src / c.build_marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(c))
    side = svc.build_inputs_path(c)
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(svc.build_inputs_text(c))


def test_every_asset_a_build_step_consumes_is_an_implicit_input(tmp_path):
    svc = _svc(tmp_path)
    got = {c.id: c.asset_inputs for st in svc.stacks() for c in st.components if c.asset_inputs}
    assert got == EXPECTED
    for cid in EXPECTED:
        c = _comp(svc, cid)
        assert c.build_marker, f"{cid}: an asset consumer without a marker could never read stale"
        text = svc.build_inputs_text(c)
        for rel in c.asset_inputs:
            assert re.search(rf"^asset {re.escape(rel)} [0-9a-f]{{64}}$", text, re.M), (cid, rel, text)
    # post_steps / run / config bases are NOT build inputs: graywolf's provision script is a
    # post_step and resolves fresh at run time.
    assert "scripts/graywolf-provision.py" not in _comp(svc, "graywolf").asset_inputs


def test_the_sidecar_sits_beside_the_artifact_the_env_selects(tmp_path):
    # meshcom-qemu's `bin` carries {env}; the sidecar goes where the artifact really is, never
    # into a literal "{env}" directory.
    p = str(_svc(tmp_path).build_inputs_path(_comp(_svc(tmp_path), "meshcom-qemu")))
    assert "{" not in p
    assert p.endswith(".work/MeshCom-Firmware/.pio/build/qemu-headless-extradio-gpsd/.lhpc-build-inputs")


def test_asset_digest_is_content_not_metadata(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "pkg" / "__pycache__").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    (root / "pkg" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"junk")
    (root / "f.txt").write_text("one\n")
    monkeypatch.setattr(assets, "asset_path", lambda name: root / name)
    assets.clear_digest_cache()
    d_pkg, d_f = assets.asset_digest("pkg"), assets.asset_digest("f.txt")
    assert d_pkg == assets.asset_digest("pkg") and len(d_pkg) == 64
    (root / "pkg" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"other")
    assets.clear_digest_cache()
    assert assets.asset_digest("pkg") == d_pkg                       # bytecode is not the asset
    (root / "pkg" / "a.py").write_text("x = 2\n")
    assets.clear_digest_cache()
    d2 = assets.asset_digest("pkg")
    assert d2 != d_pkg                                               # a changed byte
    (root / "pkg" / "a.py").rename(root / "pkg" / "b.py")
    assets.clear_digest_cache()
    assert assets.asset_digest("pkg") not in (d_pkg, d2)             # a renamed file
    (root / "f.txt").write_text("two\n")
    assets.clear_digest_cache()
    assert assets.asset_digest("f.txt") != d_f
    with pytest.raises(FileNotFoundError):
        assets.asset_digest("nope")


def test_what_pip_leaves_in_the_asset_is_not_the_asset(tmp_path, monkeypatch):
    # Caught by the testlab on the first dev push: `pip install {asset}/meshcore_host` builds IN
    # PLACE and leaves build/ and meshcore_host.egg-info/ inside the package data, so the digest
    # recorded before the install never matched the one recomputed after it — every meshcore
    # build read NOT built the moment it finished.
    root = tmp_path / "data"
    (root / "meshcore_host" / "meshcore_host").mkdir(parents=True)
    (root / "meshcore_host" / "meshcore_host" / "__init__.py").write_text("")
    (root / "meshcore_host" / "pyproject.toml").write_text("[project]\nname='meshcore_host'\n")
    monkeypatch.setattr(assets, "asset_path", lambda name: root / name)
    assets.clear_digest_cache()
    before = assets.asset_digest("meshcore_host")
    # exactly what the reproduction on this PC showed pip leaving behind:
    (root / "meshcore_host" / "build" / "lib" / "meshcore_host").mkdir(parents=True)
    (root / "meshcore_host" / "build" / "lib" / "meshcore_host" / "__init__.py").write_text("")
    (root / "meshcore_host" / "meshcore_host.egg-info").mkdir()
    (root / "meshcore_host" / "meshcore_host.egg-info" / "PKG-INFO").write_text("Name: x\n")
    (root / "meshcore_host" / "meshcore_host" / "__pycache__").mkdir()
    (root / "meshcore_host" / "meshcore_host" / "__pycache__" / "a.pyc").write_bytes(b"x")
    assets.clear_digest_cache()
    assert assets.asset_digest("meshcore_host") == before
    (root / "meshcore_host" / "meshcore_host" / "__init__.py").write_text("changed = True\n")
    assets.clear_digest_cache()
    assert assets.asset_digest("meshcore_host") != before             # real content still counts


def test_the_digest_cache_follows_the_stat_fingerprint(tmp_path, monkeypatch):
    # The cache is what keeps a 12 MB dist off the rendered-page path; it must still notice a
    # changed asset — an update replaces files, so sizes/mtimes move.
    root = tmp_path / "data"
    (root / "d").mkdir(parents=True)
    f = root / "d" / "a.txt"
    f.write_text("aaaa\n")
    monkeypatch.setattr(assets, "asset_path", lambda name: root / name)
    assets.clear_digest_cache()
    d1 = assets.asset_digest("d")
    f.write_text("bbbb\n")                                           # same size on purpose
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 2_000_000_000))
    assert assets.asset_digest("d") != d1                            # no clear_digest_cache()


def test_a_changed_asset_reads_not_built(tmp_path, monkeypatch):
    # The finding itself, on the real meshcore_host package: built against today's asset, then the
    # asset changes under the built venv (an lhpc update) -> NOT built -> rebuilt -> built.
    data = tmp_path / "data"
    shutil.copytree(assets.asset_path("meshcore_host"), data / "meshcore_host",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(assets.asset_path("openhop-repeater-constraints.txt"), data)
    monkeypatch.setattr(assets, "asset_path", lambda name: data / name)
    assets.clear_digest_cache()
    svc = _svc(tmp_path)
    c = _comp(svc, "meshcore-node")
    assert svc.is_built(c) is False
    _stamp_built(svc, c)
    assert svc.is_built(_comp(_svc(tmp_path), "meshcore-node")) is True
    poller = next(data.rglob("loraham_radio.py"))
    poller.write_text(poller.read_text().replace("GET CHANNEL NOSCAN", "GET CHANNEL"))
    assets.clear_digest_cache()
    fresh = _svc(tmp_path)                                           # a new request: no memoised digest
    assert fresh.is_built(_comp(fresh, "meshcore-node")) is False, \
        "the venv still runs the old poller and lhpc would report it built"
    svc2 = _svc(tmp_path)
    _stamp_built(svc2, _comp(svc2, "meshcore-node"))                 # `lhpc build meshcore`
    assert svc2.is_built(_comp(_svc(tmp_path), "meshcore-node")) is True


def test_a_sidecar_from_before_assets_were_recorded_reads_not_built(tmp_path):
    # A box built by an older controller has a sidecar with the declared inputs only. Unknown is
    # NOT built — the same rule as an unreadable consumed-source SHA — so the operator is told once.
    svc = _svc(tmp_path)
    c = _comp(svc, "meshtastic")
    _stamp_built(svc, c)
    svc.build_inputs_path(c).write_text("".join(f"input {n} {v}\n" for n, v in c.build_inputs))
    assert svc.is_built(_comp(_svc(tmp_path), "meshtastic")) is False


def test_an_asset_build_step_without_a_marker_is_refused():
    steps = [{"argv": ["bash", "{asset}/scripts/x.sh", "{runtime}/build/x"]}]
    with pytest.raises(ManifestError, match="without a build_marker"):
        manifest_mod._asset_inputs({"id": "x", "build_steps": steps})
    assert manifest_mod._asset_inputs({"id": "x", "build_steps": steps,
                                       "build_marker": ".done"}) == ("scripts/x.sh",)
    assert manifest_mod._asset_inputs({"id": "x", "build_marker": ".done",
                                       "post_steps": steps}) == ()
