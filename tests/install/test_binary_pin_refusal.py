"""P1.15: the pin-mismatch refusal of a binary install names self-update first, source second."""
import pytest

from lhpc.core import binary_install as bi
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc_with_index(tmp_path, monkeypatch, error):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    monkeypatch.setattr(bi, "require_zstd", lambda: None)
    monkeypatch.setattr(bi, "fetch_index", lambda url: {})
    monkeypatch.setattr(bi, "index_entry",
                        lambda idx, sid: type("E", (), {"sha256": "a" * 64})())
    monkeypatch.setattr(bi, "check_target", lambda e, t: None)

    def _pins(entry, pins):
        raise error
    monkeypatch.setattr(bi, "check_pins", _pins)
    return svc


@pytest.mark.parametrize("apply", [False, True])
def test_a_pin_mismatch_names_self_update_first_then_source(tmp_path, monkeypatch, apply):
    svc = _svc_with_index(tmp_path, monkeypatch, bi.BinaryPinMismatch(
        "the published binary was built from different commits than this lhpc pins (x); "
        "the index serves the latest release's binary",
        mismatch={"loraham-daemon": ("1" * 40, "2" * 40)}))
    r = svc.binary_install("daemon", apply=apply)
    assert not r.ok and "different commits" in r.summary
    # A3 adds a third way (the override) AFTER the two P1.15 named, in the same order.
    assert r.next_commands[:2] == ["lhpc self-update --apply",
                                   "lhpc install daemon --source pinned --yes"]
    assert r.data.get("pin_mismatch") == {"loraham-daemon": ["1" * 40, "2" * 40]}
    assert r.details[0].strip().startswith("1. Update LHPC first")
    assert r.details[1].strip().startswith("2. Or build from source")


def test_other_refusals_keep_the_source_offer_only(tmp_path, monkeypatch):
    svc = _svc_with_index(tmp_path, monkeypatch, bi.BinaryInstallError("the index is unreachable"))
    r = svc.binary_install("daemon", apply=False)
    assert not r.ok and r.next_commands == ["lhpc install daemon --source pinned --yes"]
    assert "pin_mismatch" not in r.data


def test_check_pins_raises_the_typed_mismatch():
    class _E:
        components = {"a": "1" * 40}
    with pytest.raises(bi.BinaryPinMismatch):
        bi.check_pins(_E(), {"a": "2" * 40})
