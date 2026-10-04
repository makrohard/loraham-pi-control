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
    ("os.mkdir", CANDIDATE),                                       # the local fallback restages
    ("OwnedMarker.rewrite", STAGING),
    ("shutil.copytree", "local/app/.git"),
    ("shutil.copy2", "local/app/file.txt"),
    ("open_marker_excl", "state/source-txn/app-*.json"),           # the activation journal
    ("source_fs._rename_noreplace_at", "app"),           # active -> .app.prev
    ("OwnedMarker.rewrite", "app-*.json"),                         # prior-archived
    ("source_fs.carry_extras", "?"),
    ("source_fs._rename_noreplace_at", CANDIDATE),       # candidate -> active
    ("OwnedMarker.rewrite", "app-*.json"),                         # activated
    ("write_marker", "state/source-registry/app-*.json"),          # the ownership record
    ("source_fs._rename_noreplace_at", ".app.prev"),     # prior -> quarantine
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


# Known recovery defects (code-review/code-report-T2.md), each a strict xfail: a fix turns it
# into an XPASS, which fails until the mark is removed.
KD_S1 = ("KD-S1: leaving the staging block by an exception or a failed cleanup removes the "
         "staging record (install.py `_staged_clone_record` finally) while the candidate stays; "
         "no recovery path names it again")
KD_S2 = ("KD-S2: carrying a local file changes the candidate's recorded ctime; a crash between "
         "the carry and the activation leaves no active source and recovery refuses")
KD_S3 = ("KD-S3: a failed removal of the quarantined prior leaves `..app.prev.quarantine-*` after "
         "the journal is closed; no recovery removes it and uninstall/clean refuse until it is "
         "removed by hand")
KD_S4 = ("KD-S4: a Ctrl-C inside the atomic-rename probe leaves `src/.lhpc-atomic-probe-*`; "
         "source_fs.py `require_atomic_rename` removes it only on an OSError")
ALL = tuple(sorted(FAILURES))
KNOWN = {
    "update": {**{(k, f): KD_S1 for k in (5, 8, 9, 10) for f in ("KeyboardInterrupt",)},
               **{(k, f): KD_S1 for k in (6, 11) for f in ALL},
               (15, "KeyboardInterrupt"): KD_S2,
               **{(19, f): KD_S3 for f in ALL}},
    "adopt": {**{(k, "KeyboardInterrupt"): KD_S4 for k in (4, 5)},
              **{(k, "KeyboardInterrupt"): KD_S1 for k in (8, 11, 12, 13)},
              **{(k, f): KD_S1 for k in (9, 14) for f in ALL}},
}
CASES = [pytest.param(op, k, f, id=f"{op}-{w}:{p}-{f}",
                      marks=[pytest.mark.xfail(strict=True, reason=KNOWN[op][k, f])]
                      if (k, f) in KNOWN[op] else [])
         for op, points in (("adopt", ADOPT), ("update", UPDATE))
         for k, (w, p) in enumerate(points) for f in ALL]


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
