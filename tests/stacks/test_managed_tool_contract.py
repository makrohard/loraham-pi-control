"""Deterministic (no-network) manifest contract for the MeshCom managed-tool pipeline.

Pin AGREEMENT across consumers of a shared source has one owner, and it is not here:
`tests/install/test_pin_consistency.py`. What this file guards is the provisioning half —
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
