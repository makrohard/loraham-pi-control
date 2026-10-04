"""A source adoption or update interrupted at every durable write is recovered and can be retried.

The source transaction (`install.py` `adopt_source`, `source_fs.py`) records its candidate in a
`state/source-txn/*.staging` record before the clone exists, stages the new tree in a
`.<name>.candidate-*` sibling, journals the activation (`state/source-txn/<stem>.json`), archives
the active source to `.<name>.prev`, renames the candidate into place, writes the ownership
record, removes the archived prior and closes journal and staging record. The journal and records
go through `runtime_fs`; the tree moves are descriptor-relative calls in `source_fs` (and the
local-fallback copy `shutil` in `install.py`), which the harness gates the same way. Each case
fails ONE write (disk full, I/O error, Ctrl-C) during a first adoption and during an update
v1 → v2 over an operator's own added file, and proves: recovery (`recover_source_activations`,
which the next writer runs first) resolves what is left, the box then holds no source or one
whole version with a record naming that commit and the added file intact — no candidate,
archived prior, journal or staging record — and the same operation succeeds.
"""
from __future__ import annotations

import pytest

from interrupts import FAILURES, durable_writes, run_interrupted
from lhpc.core import install as installmod
from lhpc.core import source_fs, source_registry
from lhpc.core.model import Component, ComponentKind, SourceSpec

pytestmark = pytest.mark.safety("source-transaction")

COMP = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                 source=SourceSpec(path="src/app", local_dir="app"))
RAW = [(source_fs, "os.mkdir"), (source_fs, "os.rename"), (source_fs, "os.rmdir"),
       (source_fs, "os.unlink"), (source_fs, "_rename_noreplace_at"),
       (source_fs, "remove_bound", lambda e: (False, str(e))),     # reports, never raises
       (source_fs, "carry_extras", lambda e: f"notes.txt: could not be written ({e})"), (installmod, "shutil.copytree"), (installmod, "shutil.copy2"),
       (installmod, "shutil.rmtree")]
CANDIDATE = ".app.candidate-*-*"
STAGING = "app-*.app.candidate-*-*.staging"
# A first adoption's durable writes, in order (install.py `adopt_source` → `_activate_held`).
ADOPT = [
    ("write_marker", "state/locks/source-txn-index.owner"),
    ("write_marker", "state/locks/source.src-app.owner"),
    ("ensure_dir", "src"),
    ("os.mkdir", ".lhpc-atomic-probe-*-*"),                       # the filesystem's renameat2
    ("source_fs._rename_noreplace_at", ".lhpc-atomic-probe-*-*"),  # probe, once per device
    ("os.rmdir", ".lhpc-atomic-probe-*-*-b"),
    ("open_marker_excl", "state/source-txn/" + STAGING),
    ("os.mkdir", CANDIDATE),
    ("OwnedMarker.rewrite", STAGING),
    ("source_fs.remove_bound", CANDIDATE),
    ("OwnedMarker.rewrite", STAGING),                              # no inode until the next
    ("os.mkdir", CANDIDATE),
    ("OwnedMarker.rewrite", STAGING),
    ("shutil.copytree", "local/app/.git"),
    ("shutil.copy2", "local/app/file.txt"),
    ("open_marker_excl", "state/source-txn/app-*.json"),
    ("source_fs._rename_noreplace_at", CANDIDATE),                # candidate -> active
    ("OwnedMarker.rewrite", "app-*.json"),                         # activated
    ("write_marker", "state/source-registry/app-*.json"),
    ("OwnedMarker.remove", "app-*.json"),
    ("OwnedMarker.remove", STAGING),
    ("unlink", "state/locks/source.src-app.owner"),
    ("unlink", "state/locks/source-txn-index.owner"),
]
# The update's durable writes, in order: the same, with the prior archived and removed.
UPDATE = [
    ("write_marker", "state/locks/source-txn-index.owner"),
    ("write_marker", "state/locks/source.src-app.owner"),
    ("ensure_dir", "src"),
    ("open_marker_excl", "state/source-txn/" + STAGING),           # the staging record
    ("os.mkdir", CANDIDATE),
    ("OwnedMarker.rewrite", STAGING),                              # its identity
    ("source_fs.remove_bound", CANDIDATE),               # no remote: clone fails,
    ("OwnedMarker.rewrite", STAGING),                              # no inode recorded until
    ("os.mkdir", CANDIDATE),                                       # the local fallback restages
    ("OwnedMarker.rewrite", STAGING),
    ("shutil.copytree", "local/app/.git"),
    ("shutil.copy2", "local/app/file.txt"),
    ("open_marker_excl", "state/source-txn/app-*.json"),           # the activation journal
    ("source_fs._rename_noreplace_at", "app"),           # active -> .app.prev
    ("OwnedMarker.rewrite", "app-*.json"),                         # prior-archived
    ("source_fs.carry_extras", "?"),
    ("OwnedMarker.rewrite", "app-*.json"),                         # the candidate after the carry
    ("source_fs._rename_noreplace_at", CANDIDATE),       # candidate -> active
    ("OwnedMarker.rewrite", "app-*.json"),                         # activated
    ("write_marker", "state/source-registry/app-*.json"),          # the ownership record
    ("source_fs._rename_noreplace_at", ".app.prev"),     # prior -> quarantine
    ("OwnedMarker.rewrite", "app-*.json"),                         # the quarantine's identity
    ("source_fs.remove_bound", "..app.prev.quarantine-*-*"),
    ("OwnedMarker.remove", "app-*.json"),
    ("OwnedMarker.remove", STAGING),
    ("unlink", "state/locks/source.src-app.owner"),
    ("unlink", "state/locks/source-txn-index.owner"),
]


@pytest.fixture
def box(git, make_repo, installer, monkeypatch):
    """`box(root, op) -> (inst, run, versions)`: the local fallback at v1; for "update", `app`
    adopted at v1, the operator's `notes.txt` added to it and the local advanced to v2. `run()`
    is the operator's adoption or update; `versions` maps each commit to its `file.txt`."""
    monkeypatch.setattr(source_fs, "_ATOMIC_OK_DEVS", set())     # the probe runs once per test

    def make(root, op):
        local = root / "local" / "app"
        versions = {make_repo(local, {"file.txt": "v1\n"}): b"v1\n"}
        inst = installer(COMP, root=root)
        if op == "adopt":
            return inst, lambda: inst.adopt_source(COMP, source="dev"), versions
        assert inst.adopt_source(COMP, source="dev").status == "done"
        (root / "src" / "app" / "notes.txt").write_text("mine\n")     # the operator's own file
        (local / "file.txt").write_text("v2\n")
        git(local, "commit", "-qam", "v2")
        versions[git(local, "rev-parse", "HEAD")] = b"v2\n"
        return inst, lambda: inst.adopt_source(COMP, force=True, source="dev"), versions
    return make


def _copy_run_sorted(writes):
    """The writes in order, except the copy of the local tree: `copytree` visits its entries in
    the filesystem's directory order (tmpfs and ext4 differ), so that run is compared sorted."""
    copies = ("shutil.copytree", "shutil.copy2")
    i = next((n for n, w in enumerate(writes) if w[0] in copies), len(writes))
    j = i
    while j < len(writes) and writes[j][0] in copies:
        j += 1
    return writes[:i] + sorted(writes[i:j]) + writes[j:]


@pytest.mark.parametrize("op", ["adopt", "update"])
def test_the_operation_writes_exactly_the_pinned_points(tmp_path, box, op):
    inst, run, _ = box(tmp_path / "rt", op)
    with durable_writes(inst.paths.runtime_root, raw=RAW) as log:
        assert run().status == "done"
    assert _copy_run_sorted(log) == _copy_run_sorted({"adopt": ADOPT, "update": UPDATE}[op])


# The journal write that names the renamed-aside prior by its full identity. A stop between that
# rename and this write is the one point recovery cannot prove (the rename moved the ctime the
# journal holds): it is retained, not resumed — its own test below.
QUARANTINE_RECORD = UPDATE.index(("source_fs._rename_noreplace_at", ".app.prev")) + 1
CASES = [pytest.param(op, k, f, id=f"{op}-{w}:{p}-{f}")
         for op, points in (("adopt", ADOPT), ("update", UPDATE))
         for k, (w, p) in enumerate(points) for f in sorted(FAILURES)
         if (op, k, f) != ("update", QUARANTINE_RECORD, "KeyboardInterrupt")]


@pytest.mark.parametrize(("op", "point", "failure"), CASES)
def test_an_interrupted_operation_recovers_and_retries(tmp_path, box, git, op, point, failure):
    root = tmp_path / "rt"
    inst, run, versions = box(root, op)
    run_interrupted(root, run, fail_at=point, exc=FAILURES[failure], raw=RAW)

    # (a) recovery resolves what is left: nothing blocks the next source mutation
    inst.recover_source_activations()
    assert not inst._pending_journals()
    # (b) no source (a first adoption undone) or one whole version with its record; no sibling,
    # journal or staging record; the operator's added file intact
    txn = root / "state" / "source-txn"
    assert not txn.exists() or list(txn.iterdir()) == []
    held = _assert_one_whole_source(root, inst, git, versions, op, allow_absent=(op == "adopt"))
    # (c) the retry lands on the newest version (an adoption that already completed is skipped)
    assert run().status == ("skipped" if op == "adopt" and held else "done")
    newest = _assert_one_whole_source(root, inst, git, versions, op, allow_absent=False)
    assert newest == max(versions.values())


def test_a_stop_before_the_quarantine_is_recorded_retains_it(tmp_path, box):
    """The process stops right after the archived prior was renamed to its quarantine, before the
    journal names it: the journal still holds the prior's identity from before the rename, whose
    ctime the rename moved. Recovery cannot tell that tree from a directory on a recycled inode,
    so it retains it, with the one by-hand text `lhpc status` shows too."""
    root = tmp_path / "rt"
    inst, run, _ = box(root, "update")
    run_interrupted(root, run, fail_at=QUARANTINE_RECORD, exc=KeyboardInterrupt, raw=RAW)
    [q] = (root / "src").glob("..app.prev.quarantine-*-*")
    msgs = inst.recover_source_activations()
    text = (f"the removal of the archived prior was interrupted and {q} cannot be finished (not "
            f"provably the archived prior) — retained; inspect it, then remove it by hand (rm -rf "
            f"{q}) and the journal under state/source-txn")
    assert f"recovery-required for app: {text}" in msgs, msgs
    assert q.is_dir() and (q / "notes.txt").read_text() == "mine\n" and inst._pending_journals()
    assert [(w, h) for _r, w, h in inst.pending_states()] == [("recovery-required", text)]


def _assert_one_whole_source(root, inst, git, versions, op, *, allow_absent):
    """The source tree is `app` alone (or nothing, when allowed); returns its `file.txt`."""
    names = sorted(p.name for p in (root / "src").iterdir()) if (root / "src").exists() else []
    if allow_absent and names == []:
        assert source_registry.read_record(inst.paths, "src/app") is None
        return None
    assert names == ["app"]
    head = git(root / "src" / "app", "rev-parse", "HEAD")
    assert (root / "src" / "app" / "file.txt").read_bytes() == versions[head]
    assert source_registry.read_record(inst.paths, "src/app").resolved_commit == head
    if op == "update":
        assert (root / "src" / "app" / "notes.txt").read_text() == "mine\n"   # never collateral
    return versions[head]


class _PowerLoss(BaseException):
    """The process stops: no `except` clause of the operation runs (only the `finally` blocks that
    close descriptors — what a power loss leaves on disk is the same)."""


def test_a_power_loss_after_the_carry_wrote_is_recovered_and_retried(tmp_path, box, git,
                                                                      monkeypatch):
    # The carry has written the operator's file into the candidate (which moves the candidate's
    # ctime) and the process stops before the journal records it again. The candidate no longer
    # matches its full recorded identity, so recovery retains everything and names it; once the
    # operator has removed it, the next recovery puts the prior back with the file and the retry
    # lands on v2.
    import shutil
    root = tmp_path / "rt"
    inst, run, versions = box(root, "update")
    real = source_fs.carry_extras

    def carry_then_power_loss(src_fd, dst_fd, rels):
        assert real(src_fd, dst_fd, rels) == ""
        raise _PowerLoss()
    monkeypatch.setattr(source_fs, "carry_extras", carry_then_power_loss)
    with pytest.raises(_PowerLoss):
        run()
    monkeypatch.setattr(source_fs, "carry_extras", real)
    assert inst._pending_journals()                          # the crash left the transaction open
    [cand] = (root / "src").glob(CANDIDATE)
    msgs = inst.recover_source_activations()
    assert any(f"the staged candidate {cand} is not provably the one the journal recorded" in m
               and "lhpc update app --yes" in m for m in msgs), msgs
    assert cand.is_dir() and (root / "src" / ".app.prev" / "notes.txt").is_file()
    assert inst._pending_journals()
    shutil.rmtree(cand)                                      # the operator, after checking
    inst.recover_source_activations()
    assert not inst._pending_journals()
    txn = root / "state" / "source-txn"
    assert not txn.exists() or list(txn.iterdir()) == []
    assert _assert_one_whole_source(root, inst, git, versions, "update", allow_absent=False) == b"v1\n"
    assert run().status == "done"
    assert _assert_one_whole_source(root, inst, git, versions, "update", allow_absent=False) == b"v2\n"


def test_a_directory_on_a_recycled_inode_in_carrying_is_never_removed(tmp_path, box, monkeypatch):
    # The journal says `carrying`; at the candidate's name is a directory with the recorded dev and
    # inode but another ctime — what inode recycling hands a different directory. It is not
    # removed: dev+ino is no proof. Everything is retained and the path named.
    import json
    root = tmp_path / "rt"
    inst, run, _ = box(root, "update")

    def power_loss(src_fd, dst_fd, rels):
        raise _PowerLoss()                                   # stops before the carry writes
    monkeypatch.setattr(source_fs, "carry_extras", power_loss)
    with pytest.raises(_PowerLoss):
        run()
    monkeypatch.undo()
    jf = next(p for p in (root / "state" / "source-txn").iterdir() if p.suffix == ".json")
    j = json.loads(jf.read_text())
    assert j["state"] == "carrying"
    [cand] = (root / "src").glob(CANDIDATE)
    (cand / "theirs.txt").write_text("not this transaction's\n")    # another ctime, same inode
    msgs = inst.recover_source_activations()
    assert (cand / "theirs.txt").read_text() == "not this transaction's\n"
    assert any(f"the staged candidate {cand} is not provably" in m for m in msgs), msgs
    assert jf.exists() and (root / "src" / ".app.prev").is_dir()
