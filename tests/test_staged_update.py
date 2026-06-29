"""P0.4 — updates stage into a candidate dir and activate atomically; a failed
acquisition never destroys the active source; dirty/linked trees are not overwritten."""

import os

from lhpc.core.install import Installer
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
from lhpc.core.config import Config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


def _stack(comp):
    return (Stack(id="s", name="s", main=comp.id, components=(comp,)),)


def _installer(rt, stacks, runner=None, local=None):
    sys = FakeSystem().system
    if runner is not None:
        sys.runner = runner
    cfg = Config(values={"install": {"adopt_search_root": str(local or rt / "nolocal")}})
    return Installer(Paths(runtime_root=rt), stacks, cfg, sys)


class _Runner:
    """Scripted git runner. `clone_ok` controls whether a `git clone` 'succeeds'
    (and creates the target dir); other git commands return success."""
    def __init__(self, clone_ok=True):
        self.clone_ok = clone_ok
    def run(self, argv, timeout=None, *a, **k):
        from lhpc.core.probes.backends import CommandResult
        if argv[:2] == ["git", "clone"]:
            target = argv[-1]
            if self.clone_ok:
                p = __import__("pathlib").Path(target)
                (p / ".git").mkdir(parents=True, exist_ok=True)
                (p / "file.txt").write_text("new")
                return CommandResult(0, "", "")
            return CommandResult(1, "", "fatal: could not connect")
        if "status" in argv:
            return CommandResult(0, "", "")          # clean working tree
        return CommandResult(0, "abc123\n", "")       # rev-parse / checkout / describe


def _comp():
    return Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/repo", local_dir="repo",
                                       remote="https://example/repo.git", branch="main"))


def test_failed_clone_leaves_active_source_intact(tmp_path):
    rt = tmp_path / "rt"
    active = rt / "src" / "repo"
    active.mkdir(parents=True)
    (active / "file.txt").write_text("ACTIVE")          # the installed, working source
    comp = _comp()
    inst = _installer(rt, _stack(comp), runner=_Runner(clone_ok=False))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed"
    assert active.exists() and (active / "file.txt").read_text() == "ACTIVE"
    assert not (rt / "src" / ".repo.candidate").exists()   # staging cleaned up


def test_successful_update_activates_and_removes_prior(tmp_path):
    # A successful activation leaves NO transaction artifacts: candidate is active, the
    # journal is cleared, and `.prev` (a transaction artifact, not a permanent backup) is
    # removed — so it can't block the next update.
    rt = tmp_path / "rt"
    active = rt / "src" / "repo"
    active.mkdir(parents=True)
    (active / "file.txt").write_text("OLD")
    comp = _comp()
    inst = _installer(rt, _stack(comp), runner=_Runner(clone_ok=True))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status != "failed"
    assert (active / "file.txt").read_text() == "new"          # candidate activated
    assert not (rt / "src" / ".repo.prev").exists()            # prior removed (no orphan)
    assert not inst._journal_path(active).exists()             # journal cleared


def test_two_consecutive_force_updates_both_succeed(tmp_path):
    # Repeatability: a managed source must be updateable more than once. The first
    # update must not leave a `.prev` that the second treats as an unowned orphan.
    rt = tmp_path / "rt"
    active = rt / "src" / "repo"
    active.mkdir(parents=True)
    (active / "file.txt").write_text("OLD")
    comp = _comp()
    inst = _installer(rt, _stack(comp), runner=_Runner(clone_ok=True))
    for _ in range(2):
        action = inst.adopt_source(comp, force=True, source="dev")
        assert action.status != "failed", action.detail
        assert active.is_dir() and (active / "file.txt").read_text() == "new"
        assert not (rt / "src" / ".repo.prev").exists()
        assert not inst._journal_path(active).exists()


def test_dirty_source_is_not_overwritten(tmp_path):
    rt = tmp_path / "rt"
    active = rt / "src" / "repo"
    (active / ".git").mkdir(parents=True)
    (active / "file.txt").write_text("LOCAL EDIT")
    comp = _comp()

    class Dirty(_Runner):
        def run(self, argv, timeout=None, *a, **k):
            from lhpc.core.probes.backends import CommandResult
            if "status" in argv:
                return CommandResult(0, " M file.txt\n", "")   # dirty
            return super().run(argv, timeout, *a, **k)

    inst = _installer(rt, _stack(comp), runner=Dirty())
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "local modifications" in action.detail
    assert (active / "file.txt").read_text() == "LOCAL EDIT"
