"""The binary install reads the decompressed artifact to its end. The tar end marker ends the member
loop, not the stream: tar pads its last record with zero bytes after it. Closing zstd's pipe at the
marker could cut zstd off mid-write (EPIPE: a nonzero exit, "artifact decompression failed (zstd)",
on an intact artifact); with more padding than a pipe holds, it always did. What follows the marker
must be zero bytes, and it counts toward the expansion limit."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
import zstandard

from lhpc.core import binary_install as bi
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

BIN = "src/loraham-daemon/loraham_daemon/loraham_daemon"
LIB = "src/loraham-daemon/loraham_daemon/lib/libloraham-ch341.so"
ROOTS = (BIN, LIB)
BIG_PAD = 1 << 20          # far more than a pipe buffer (64 KiB)


def _artifact(path, tail=b"", poke=None):
    """The daemon's two members as tarfile writes them, then `tail`; `poke` puts an "x" that many
    bytes before the tar's own end (inside its last record's padding)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for rel, data in ((BIN, b"ELF-bin"), (LIB, b"ELF-lib")):
            info = tarfile.TarInfo(rel)
            info.mode = 0o755
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tar = bytearray(buf.getvalue())
    if poke:
        tar[-poke] = ord("x")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zstandard.ZstdCompressor().compress(bytes(tar) + tail))
    return path


@pytest.mark.requires_zstd
def test_an_intact_artifact_with_more_zero_padding_than_a_pipe_holds_installs(tmp_path, monkeypatch):
    # The install path itself: only the network seams and the probe are stubbed.
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    blob = _artifact(tmp_path / "art" / "a.tar.zst", b"\0" * BIG_PAD).read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    fn = f"daemon-{sha}.tar.zst"
    entry = {"filename": fn, "url": f"https://example.invalid/{fn}", "sha256": sha, "size": len(blob),
             "built_from": "b" * 40, "components": dict(svc._binary_pins("daemon")),
             "lhpc_commit": "c" * 40, "builder_commit": "d" * 40, "target": "aarch64-trixie",
             "os": "trixie", "container_digest": "debian@sha256:" + "e" * 64, "runtime_deps": [],
             "smoke": {"mode": "mandatory", "result": "passed"}, "extract_to": "runtime-root",
             "inputs": {}, "published_at": "2026-11-25T03:00:00Z"}
    index = {"schema": 3, "stacks": {"daemon": [entry]}}
    monkeypatch.setattr(bi, "_http_get", lambda url, max_bytes: json.dumps(index).encode())
    monkeypatch.setattr(bi, "download_artifact", lambda e, path: Path(path).write_bytes(blob))
    res = svc.binary_install("daemon", apply=True)
    assert res.ok, res.summary
    assert (tmp_path / BIN).read_bytes() == b"ELF-bin" and (tmp_path / LIB).read_bytes() == b"ELF-lib"


@pytest.mark.requires_zstd
def test_data_after_the_tar_end_marker_is_refused(tmp_path):
    tar = _artifact(tmp_path / "a.tar.zst", b"\0" * BIG_PAD + b"x")
    with pytest.raises(bi.BinaryInstallError, match="data after the tar end marker"):
        bi.validate_and_extract(tar, tmp_path / "stage", ROOTS)


@pytest.mark.requires_zstd
def test_a_nonzero_byte_inside_the_last_records_padding_is_refused(tmp_path):
    # tarfile has already buffered this byte when it meets the marker: the check reads its stream
    tar = _artifact(tmp_path / "a.tar.zst", poke=100)
    with pytest.raises(bi.BinaryInstallError, match="data after the tar end marker"):
        bi.validate_and_extract(tar, tmp_path / "stage", ROOTS)


@pytest.mark.requires_zstd
def test_zero_padding_counts_toward_the_expansion_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "_MAX_TOTAL_BYTES", BIG_PAD)      # the members alone are 14 bytes
    tar = _artifact(tmp_path / "a.tar.zst", b"\0" * BIG_PAD)
    with pytest.raises(bi.BinaryInstallError, match="size limit"):
        bi.validate_and_extract(tar, tmp_path / "stage", ROOTS)
