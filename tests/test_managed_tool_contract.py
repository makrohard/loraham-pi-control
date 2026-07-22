"""Deterministic (no-network) manifest contract for the MeshCom managed-tool pipeline.

Guards the exact failure classes this batch repaired:
  * every consumer of a SHARED source must pin ONE identical full commit SHA (a split pin shipped a
    fresh install against a commit missing the managed build scripts);
  * the managed-tool provisioning steps + their in-root PIO / PLATFORMIO_CORE_DIR environment must be
    present, so a fresh/pinned build provisions PlatformIO + the source-built QEMU inside the runtime
    root by absolute path (CLI and web builds share the same in-root cache).
"""

import tomllib

from lhpc.core.config import asset_path


def _manifest():
    return tomllib.load(open(asset_path("manifest.example.toml"), "rb"))


def _components():
    return [c for st in _manifest()["stack"] for c in st.get("component", [])]


def _source_of(comp):
    return comp.get("source") or {}


def test_shared_source_consumers_pin_one_identical_full_sha():
    # Group pinned components by source path; every consumer of a shared path must agree on the SAME
    # full 40-hex pin_commit AND pin_tag (no split pin can ship again).
    by_path: dict = {}
    for c in _components():
        src = _source_of(c)
        if src.get("path") and src.get("pin_commit"):
            by_path.setdefault(src["path"], []).append((c["id"], src["pin_commit"], src.get("pin_tag", "")))
    shared = {p: v for p, v in by_path.items() if len(v) > 1}
    assert "src/meshcom-qemu-raspi" in shared, "meshcom-qemu-raspi should have >1 pinned consumer"
    for path, consumers in shared.items():
        commits = {commit for _id, commit, _tag in consumers}
        tags = {tag for _id, _commit, tag in consumers}
        assert len(commits) == 1, f"{path}: split pin_commit across {[c[0] for c in consumers]}: {commits}"
        assert len(tags) == 1, f"{path}: split pin_tag across {[c[0] for c in consumers]}: {tags}"
        (only,) = commits
        assert len(only) == 40 and all(ch in "0123456789abcdef" for ch in only), \
            f"{path}: pin_commit must be a full 40-hex SHA, got {only!r}"


def test_meshcom_qemu_provisions_managed_tools_by_absolute_path():
    comp = next(c for c in _components() if c["id"] == "meshcom-qemu")
    steps = comp["build_steps"]
    argv0 = [s["argv"][0] for s in steps]
    joined = [" ".join(str(t) for t in s.get("argv", [])) for s in steps]
    # managed PlatformIO venv, pinned pio, and the source-built (link-gated) qemu — all in-root
    assert argv0[0] == "python3" and "build/tools/platformio/.venv" in joined[0]
    assert any("platformio==" in a and "/pip" in a for a in joined), "PlatformIO must be pinned into the venv"
    assert any("scripts/build-qemu.sh" in a and "build/tool-cache/qemu-xtensa" in a for a in joined)
    # prepare-openeth + build carry the in-root PIO (absolute .venv/bin/pio) and a runtime-owned
    # PLATFORMIO_CORE_DIR, so a CLI build and a web-service build share the same in-root package cache.
    for name in ("scripts/prepare-openeth.sh", "scripts/build.sh"):
        step = next(s for s in steps if s["argv"][0] == name)
        env = step.get("env", {})
        assert env.get("PIO", "").endswith("platformio/.venv/bin/pio"), f"{name}: PIO by abs path"
        assert "{runtime}/build/tools/platformio" in env.get("PLATFORMIO_CORE_DIR", ""), \
            f"{name}: runtime-owned PLATFORMIO_CORE_DIR"
