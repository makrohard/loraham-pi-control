"""P1.15: no published binary this lhpc can install (`none_compatible`) names self-update first,
source second, and nothing else."""
import pytest

from lhpc.core import binary_install as bi
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc_with_index(tmp_path, monkeypatch, error):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    monkeypatch.setattr(bi, "require_zstd", lambda: None)
    monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 3, "stacks": {}})
    monkeypatch.setattr(bi, "index_entries", lambda idx, sid: [])

    def _newest(entries, target, covers):
        raise error
    monkeypatch.setattr(bi, "newest_entry", _newest)
    return svc


@pytest.mark.parametrize("apply", [False, True])
def test_none_compatible_names_self_update_first_then_source(tmp_path, monkeypatch, apply):
    svc = _svc_with_index(tmp_path, monkeypatch, bi.BinaryNoneCompatible(
        "no published binary covers radiolib yet: install from source"))
    r = svc.binary_install("daemon", apply=apply)
    assert not r.ok and "covers radiolib" in r.summary
    assert r.next_commands == ["lhpc self-update --apply",
                               "lhpc install daemon --source pinned --yes"]
    assert r.data.get("pin_mismatch") == {}
    assert r.data.get("binary_refusal") == "none_compatible"
    assert r.details[0].strip().startswith("1. Update LHPC first")
    assert r.details[1].strip().startswith("2. Or build from source")


def test_other_refusals_keep_the_source_offer_only(tmp_path, monkeypatch):
    svc = _svc_with_index(tmp_path, monkeypatch, bi.BinaryInstallError("the index is unreachable"))
    r = svc.binary_install("daemon", apply=False)
    assert not r.ok and r.next_commands == ["lhpc install daemon --source pinned --yes"]
    assert "pin_mismatch" not in r.data and "binary_refusal" not in r.data
