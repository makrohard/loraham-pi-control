"""P1.8: `install --source pinned` over a checkout adopted from `dev` says so instead of a silent
"Nothing to do" (install never changes an installed source; `update --source` does)."""
import time

from lhpc.core import source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_KISS = ("loraham-kiss-tnc", "loraham-kiss-serial")


def _svc_with_kiss(tmp_path, selector):
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    assert source_registry.write_record(Paths(runtime_root=tmp_path), source_registry.RegistryRecord(
        "src/loraham-kiss-tnc", "https://github.com/makrohard/loraham-kiss-tnc.git", selector,
        "c" * 40, time.time(), "", _KISS))
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_different_recorded_selector_is_named_with_the_switch_command(tmp_path):
    r = _svc_with_kiss(tmp_path, "dev").install("kiss", apply=False, source="pinned")
    note = [d for d in r.details if "installed from 'dev'" in d]
    assert note and "install does not change an installed source" in note[0]
    assert "lhpc update kiss --source pinned --yes" in r.next_commands
    assert int(r.data.get("changes", 0)) == 0                      # "Nothing to do" stays true


def test_no_note_when_the_selector_already_matches(tmp_path):
    r = _svc_with_kiss(tmp_path, "pinned").install("kiss", apply=False, source="pinned")
    assert not any("installed from" in d for d in r.details)
    assert not any(c.startswith("lhpc update") for c in r.next_commands)


def test_the_applied_install_says_it_too(tmp_path):
    # The console runs install as a job (apply=True); its log carries the same note.
    r = _svc_with_kiss(tmp_path, "dev").install("kiss", apply=True, source="pinned")
    assert any("installed from 'dev'" in d for d in r.details)
    assert "lhpc update kiss --source pinned --yes" in r.next_commands


def test_install_still_drops_the_memoized_snapshot(tmp_path):
    # The helper was first inserted between `@invalidates_snapshot` and `def install`, which moved
    # the decorator off the public entry: a snapshot memoized before the install was then served
    # after it.
    svc = _svc_with_kiss(tmp_path, "dev")
    before = svc.build_snapshot()
    assert svc.build_snapshot() is before                          # memoized
    assert svc.install("kiss", apply=False, source="pinned").ok
    assert svc.build_snapshot() is not before                      # recomputed after the install


def test_no_commit_shown_when_none_was_recorded(tmp_path):
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    assert source_registry.write_record(Paths(runtime_root=tmp_path), source_registry.RegistryRecord(
        "src/loraham-kiss-tnc", "https://github.com/makrohard/loraham-kiss-tnc.git", "dev",
        "", time.time(), "", _KISS))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    note = [d for d in svc.install("kiss", apply=False, source="pinned").details if "installed from" in d]
    assert note and "commit" not in note[0]


def test_no_note_when_the_recorded_checkout_is_gone(tmp_path):
    # P1.24 (audit P1.8, the note): a valid record whose checkout was removed by hand describes
    # nothing installed; the plan adopts the source, so neither the note nor the update command.
    svc = _svc_with_kiss(tmp_path, "dev")
    (tmp_path / "src" / "loraham-kiss-tnc").rmdir()
    r = svc.install("kiss", apply=False, source="pinned")
    assert not any("installed from" in d for d in r.details)
    assert not any(c.startswith("lhpc update") for c in r.next_commands)
    assert int(r.data.get("changes", 0)) > 0                        # the adoption is planned
