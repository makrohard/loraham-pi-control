"""The daemon artifact carries the Meshstick library (daemon >= 1.3.0 `lib/libloraham-ch341.so`,
dlopen()ed from the binary's own directory): the binary channel ALWAYS has it — it covers the
pine64 source, extracts the library beside the binary and needs both files to call the install
complete. On a source build it is optional and included by default: install adopts the pine64
clone, and the daemon builds without it when it is absent."""
import io
import tarfile
from pathlib import PurePosixPath

import pytest
import zstandard

from lhpc.core import binary_install as bi
from lhpc.core.manifest import load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

BIN = "src/loraham-daemon/loraham_daemon/loraham_daemon"
LIB = "src/loraham-daemon/loraham_daemon/lib/libloraham-ch341.so"
# The daemon's publish roots up to 0.13.3: the binary alone.
ROOTS_BEFORE = (BIN,)


def _daemon():
    return next(s for s in load_manifest() if s.id == "daemon")


def _tar(tmp_path, members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for rel, data in members.items():
            info = tarfile.TarInfo(rel)
            info.mode, info.size = 0o644, len(data)
            tf.addfile(info, io.BytesIO(data))
    out = tmp_path / "daemon.tar.zst"
    out.write_bytes(zstandard.ZstdCompressor().compress(buf.getvalue()))
    return out


def test_the_pine64_source_is_a_pinned_optional_checkout_beside_the_daemon():
    st = _daemon()
    lib = next(c for c in st.components if c.id == "libch341")
    assert lib.source.remote == "https://github.com/pine64/libch341-spi-userspace"
    assert lib.source.branch == "main" and lib.source.path == "src/libch341-spi-userspace"
    assert len(lib.source.pin_commit) == 40 and not lib.build_steps and lib.optional
    assert [r.check_file for r in lib.requires] == ["/usr/include/libusb-1.0/libusb.h"]
    assert "libusb-1.0-0-dev" in lib.requires[0].install
    daemon = next(c for c in st.components if c.id == "loraham-daemon")
    # USB support is optional on a source build: no build dependency and no PINEDIO_DIR (a set but
    # absent one fails build.sh). build.sh finds the clone as its checkout's sibling instead.
    assert "libch341" not in daemon.build_requires
    assert "PINEDIO_DIR" not in daemon.build_steps[0].get("env", {})
    assert PurePosixPath(lib.source.path).parent == PurePosixPath(daemon.source.path).parent
    assert PurePosixPath(lib.source.path).name == "libch341-spi-userspace"


def test_install_adopts_the_pine64_source_by_default(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    plan = svc.install("daemon", apply=False, source="pinned")
    assert plan.ok
    assert any("libch341: adopt" in d for d in plan.details), plan.details


def test_a_source_build_without_the_pine64_source_is_not_refused(tmp_path):
    for d in ("src/loraham-daemon", "src/RadioLib"):
        (tmp_path / d).mkdir(parents=True)
    fs = FakeSystem()
    svc = ControllerService(system=fs.system, paths=Paths(runtime_root=tmp_path))
    res = svc.build("daemon", apply=True)
    text = "\n".join((res.summary, *res.details))
    assert "Refusing" not in text and "libch341" not in text, text     # neither refused nor required
    # the build reached the daemon's own build.sh (which then builds without the library)
    assert ["loraham_daemon/build.sh"] in fs.system.runner.calls, fs.system.runner.calls


def test_the_binary_channel_covers_the_library_and_proves_it_second():
    b = _daemon().binary
    assert "libch341" in b.covers
    assert LIB in b.publish_roots and BIN in b.publish_roots       # exact files, not a directory
    assert b.proof_paths == (BIN, LIB)                             # the binary stays first


@pytest.mark.requires_zstd
def test_the_shipped_roots_extract_the_library_beside_the_binary(tmp_path):
    tar = _tar(tmp_path, {BIN: b"ELF-bin", LIB: b"ELF-lib"})
    stage = tmp_path / "stage"
    stage.mkdir()
    assert bi.validate_and_extract(tar, stage, _daemon().binary.publish_roots) == sorted([BIN, LIB])
    assert (stage / LIB).read_bytes() == b"ELF-lib"


@pytest.mark.requires_zstd
def test_the_roots_before_refuse_the_library_member(tmp_path):
    # Why the publish waits for this controller: an older one refuses the member outright.
    tar = _tar(tmp_path, {BIN: b"ELF-bin", LIB: b"ELF-lib"})
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(bi.BinaryInstallError, match="outside this stack's declared publish"):
        bi.validate_and_extract(tar, stage, ROOTS_BEFORE)


def test_an_entry_that_records_no_libch341_commit_is_another_composition():
    pins = {c.id: c.source.pin_commit for c in _daemon().components if c.id in _daemon().binary.covers}
    assert set(pins) == {"loraham-daemon", "radiolib", "libch341"}
    old = bi.IndexEntry(stack="daemon", filename="daemon-x.tar.zst", url="https://example.invalid/x",
                        sha256="0" * 64, size=1, target="aarch64-trixie", runtime_deps=("libc6",),
                        components={k: v for k, v in pins.items() if k != "libch341"},
                        provenance={"smoke": {"mode": "mandatory", "result": "passed"}})
    with pytest.raises(bi.BinaryNoneCompatible, match="does not record commits for libch341"):
        bi.check_pins(old, pins)


def test_missing_libusb_headers_never_block_the_default_adoption(tmp_path, monkeypatch):
    # The pine64 clone's require (libusb.h) is reported, not enforced at install: `lhpc install
    # daemon` still adopts it on a box without libusb-1.0-0-dev (FakeSystem has no such file).
    from lhpc.core.install import Installer, PlanAction
    adopted = []

    def adopt(self, comp, **_kw):
        adopted.append(comp.id)
        (tmp_path / comp.source.path).mkdir(parents=True, exist_ok=True)
        return PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="(fake)")
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.install("daemon", apply=True, source="pinned").ok
    assert "libch341" in adopted
    unmet = [d.label for d in svc.deps_report("daemon")["system"] if not d.satisfied]
    assert "libusb development headers (the Meshstick library)" in unmet
