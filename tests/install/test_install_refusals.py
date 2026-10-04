"""`lhpc install` refusals name their remedy: a command to run, or "nothing to run here — …" with what
the operator fixes first (the refusal-remedy contract of tests/repo/test_refusal_remedy.py, which
reads the same sites)."""

import types

from lhpc.core import binary_install as bi
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

NOTHING = "nothing to run here — "
RADIOLIB_PATH = "src/RadioLib"


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _binary_svc(tmp_path, monkeypatch):
    # Stubs the collaborator, the host: the binary channel is published for this target.
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return _svc(tmp_path)


def _nothing(res):
    return any(NOTHING in d for d in res.details)


def test_an_install_with_inconsistent_shared_remotes_names_the_fix_and_the_retry(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text(
        '[remotes]\nloraham-kiss-tnc = "https://github.com/fork-a/loraham-kiss-tnc.git"\n'
        'loraham-kiss-serial = "https://github.com/fork-b/loraham-kiss-tnc.git"\n')
    res = _svc(tmp_path).install("kiss", apply=True, source="dev")
    assert not res.ok and _nothing(res)
    # The retry repeats the channel asked for (finding 130): without it, it installs another one.
    assert res.next_commands == ["lhpc install kiss --source dev --yes"]


def test_an_install_under_a_foreign_auto_install_context_says_to_start_again(tmp_path):
    res = _svc(tmp_path).install("kiss", apply=True, auto_install_ctx=object())
    assert not res.ok and _nothing(res)


def test_an_install_whose_shared_consumers_disagree_names_one_selection(tmp_path, monkeypatch):
    # Stubs the collaborator, the source planner: two consumers of one checkout disagree.
    monkeypatch.setattr(ControllerService, "_plan_source_groups",
                        lambda self, items, source, **k: (None, ["shared source conflict"]))
    res = _svc(tmp_path).install("kiss", apply=True)
    assert not res.ok
    assert res.next_commands == ["lhpc install kiss --source pinned --yes"]


def test_the_shared_checkout_conflict_names_each_consumer_with_its_selection(tmp_path):
    svc = _svc(tmp_path)
    kiss = svc.stack("kiss")
    comp = next(c for c in kiss.components if c.source)
    other = types.SimpleNamespace(id="other", components=kiss.components)
    groups, conflicts = svc._plan_source_groups(
        [(kiss, comp), (other, comp)], lambda sid: "pinned" if sid == "kiss" else "dev")
    assert groups is None and len(conflicts) == 1
    assert f"kiss/{comp.id} (pinned)" in conflicts[0] and f"other/{comp.id} (dev)" in conflicts[0]


def test_an_install_whose_meshcore_identity_cannot_be_kept_says_what_to_check(tmp_path,
                                                                            monkeypatch):
    # Stubs the collaborator, the identity copy: it fails.
    monkeypatch.setattr(ControllerService, "meshcore_identity_guard",
                        lambda self, comps: "the identity file could not be copied")
    res = _svc(tmp_path).install("kiss", apply=True)
    assert not res.ok and _nothing(res)


def test_a_switch_whose_journal_cannot_be_written_names_the_retry(tmp_path, monkeypatch,
                                                                  binary_receipt):
    svc = _binary_svc(tmp_path, monkeypatch)
    binary_receipt(svc)

    def full_disk(*a, **k):
        raise bi.BinaryInstallError("could not write the binary-install journal")
    # Stubs the collaborator, the journal write: the disk is full.
    monkeypatch.setattr(bi, "open_txn", full_disk)
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and _nothing(res)
    assert res.next_commands == ["lhpc install daemon --source pinned --yes"]


def test_a_switch_whose_checkout_cannot_be_set_aside_names_the_retry(tmp_path, monkeypatch,
                                                                     binary_receipt, stub_adopt):
    svc = _binary_svc(tmp_path, monkeypatch)
    binary_receipt(svc)
    stub_adopt(svc)
    # Stubs the collaborators: RadioLib's checkout must be replaced, and setting it aside fails.
    monkeypatch.setattr(ControllerService, "switch_source_plan",
                        lambda self, groups, owned_files=(): ({RADIOLIB_PATH}, []))
    monkeypatch.setattr(ControllerService, "_preserve_replaced_source",
                        lambda self, txn, rel: "the switch transaction is not writable")
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok
    assert res.next_commands == ["lhpc install daemon --source pinned --yes"]

