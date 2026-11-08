"""L1 (DEC-W339): with LHPC_BINARY_MIRROR=<dir> (set by the test lab only) the binary channel reads the index and its
artifacts from the mirror and never opens the network; the mirror fails closed (a URL outside the one release, a
name it lacks, a traversal, an artifact URL whose name is not the entry's file name); its files are still checked by
the index's sha256 and size. Unset, the network door is used as before.

The suite's autouse guard makes the network door `_open_stream` raise, so a mirror-mode test that reached the
network would fail."""
import hashlib
import json

import pytest

from lhpc.core import binary_install as bi

_REAL_HTTP_GET = bi._http_get        # captured at import, before the conftest's network stub
REL = "https://github.com/makrohard/lhpc-binaries/releases/download/binaries/"
DATA = b"artifact-bytes" * 100
SHA = hashlib.sha256(DATA).hexdigest()
NAME = f"demo-{SHA}.tar.zst"
INDEX = {"schema": 3, "stacks": {}}
ENV = "LHPC_BINARY_MIRROR"        # the name the lab sets (literal: the contract, not the module constant)


def _entry(url=REL + NAME, filename=NAME):
    return bi.IndexEntry(stack="demo", filename=filename, url=url, sha256=SHA, size=len(DATA), components={},
                         runtime_deps=(), target="t", provenance={})


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    d = tmp_path / "binmirror"
    d.mkdir()
    (d / "index-v3.json").write_text(json.dumps(INDEX))
    (d / NAME).write_bytes(DATA)
    monkeypatch.setenv(ENV, str(d))
    monkeypatch.setattr(bi, "_http_get", _REAL_HTTP_GET)
    return d


def test_the_index_and_an_artifact_come_from_the_mirror_without_the_network(mirror, tmp_path):
    assert bi.fetch_index(REL + "index-v3.json") == INDEX
    bi.download_artifact(_entry(), tmp_path / "dl")
    assert (tmp_path / "dl").read_bytes() == DATA


@pytest.mark.parametrize("url", [
    "https://github.com/makrohard/lhpc-binaries/releases/download/other/" + NAME,
    "https://github.com/someone/lhpc-binaries/releases/download/binaries/" + NAME,
    "https://api.github.com/repos/makrohard/lhpc-binaries/releases/assets/1",
    REL + "sub/" + NAME, REL + "..", REL],
    ids=["other-release", "other-repo", "api", "subdir", "dotdot", "no-name"])
def test_a_url_outside_the_one_release_is_refused_never_fetched(mirror, url, tmp_path):
    with pytest.raises(bi.BinaryInstallError, match="serves only"):
        bi.fetch_index(url)
    with pytest.raises(bi.BinaryInstallError, match="serves only|only at its own name"):
        bi.download_artifact(_entry(url=url, filename=url.rsplit("/", 1)[-1]), tmp_path / "dl")


def test_a_name_missing_from_the_mirror_is_refused_naming_the_mirror(mirror, tmp_path):
    (mirror / NAME).unlink()
    with pytest.raises(bi.BinaryInstallError, match=f"{NAME} is not in the binary mirror {mirror}"):
        bi.download_artifact(_entry(), tmp_path / "dl")


def test_a_tampered_mirror_file_is_refused_by_its_sha256(mirror, tmp_path):
    (mirror / NAME).write_bytes(DATA[:-1] + b"X")
    with pytest.raises(bi.BinaryInstallError, match="sha256 mismatch"):
        bi.download_artifact(_entry(), tmp_path / "dl")
    assert not (tmp_path / "dl").exists()


def test_an_artifact_url_not_named_as_its_entry_is_refused(mirror, tmp_path):
    (mirror / "other.tar.zst").write_bytes(DATA)          # served, but not the file the entry names
    with pytest.raises(bi.BinaryInstallError, match=f"serves {NAME} only at its own name"):
        bi.download_artifact(_entry(url=REL + "other.tar.zst"), tmp_path / "dl")


def test_unset_the_network_door_is_used(monkeypatch, tmp_path):
    import io
    monkeypatch.delenv(ENV, raising=False)
    opened = []

    def stream(url, timeout):
        opened.append(url)
        return io.BytesIO(DATA)
    monkeypatch.setattr(bi, "_open_stream", stream)
    bi.download_artifact(_entry(), tmp_path / "dl")
    assert opened == [REL + NAME]
