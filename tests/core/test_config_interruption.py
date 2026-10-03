"""A Settings save interrupted at every durable write is recovered and can be retried.

The config transaction (`config.apply_config_transaction`) journals the pre-images in
`state/config-txn.json`, replaces each target, then removes the journal. Each case fails ONE of
those writes (disk full, I/O error, Ctrl-C) and proves: what is left is recognised by the
recovery path — the eager one every `lhpc` process runs at start, or the next writer's
`config_lock` — recovery returns the files to their pre-save bytes with no journal left, and
the same save then succeeds to the bytes an uninterrupted save writes.
"""
from __future__ import annotations

import pytest

from interrupts import FAILURES, durable_writes, run_interrupted, tree
from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.safety("config-transaction")

# The save's durable writes, in order (config.py `_apply_config_transaction_locked`).
POINTS = [
    ("atomic_write", "state/config-txn.json"),       # the journal with every pre-image
    ("atomic_write", "config/local.toml"),
    ("atomic_write", "config/stacks/daemon.toml"),
    ("unlink", "state/config-txn.json"),             # commit
]
SKIP = ("config/.lock", "state/locks")               # lock files, not transaction state


def _svc(root):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))


def _seeded(root):
    svc = _svc(root)
    assert svc.save_config_bundle("daemon", values={"radio": "868"},
                                  remotes={"loraham-daemon": "", "radiolib": ""}).ok
    return svc


def _save(svc):
    return svc.save_config_bundle("daemon", values={"radio": "433"},
                                  remotes={"loraham-daemon": "https://example.invalid/d.git",
                                           "radiolib": ""})


def test_the_save_writes_exactly_the_pinned_points(tmp_path):
    """The parametrisation below covers every write: a write added to the save fails here first."""
    svc = _seeded(tmp_path)
    with durable_writes(tmp_path) as log:
        assert _save(svc).ok
    assert log == POINTS


@pytest.mark.parametrize("recovery", ["startup", "next-writer"])
@pytest.mark.parametrize("failure", sorted(FAILURES))
@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
def test_an_interrupted_save_recovers_and_retries(tmp_path, point, failure, recovery):
    done_root = tmp_path / "uninterrupted"
    done = _seeded(done_root)
    assert _save(done).ok
    want_after = tree(done_root, skip=SKIP)

    root = tmp_path / "interrupted"
    svc = _seeded(root)
    before = tree(root, skip=SKIP)
    _, outcome = run_interrupted(root, lambda: _save(svc), fail_at=point, exc=FAILURES[failure])
    assert not getattr(outcome, "ok", False), "an interrupted save must not report success"

    paths = Paths(runtime_root=root)
    journal = (root / "state" / "config-txn.json").exists()
    if recovery == "startup":
        note = cfgmod.recover_config_journal_at_startup(paths)
        # (a) a journal left behind is recognised and finished; without one there is nothing to do
        assert bool(note) == journal
        # (b) the pre-save bytes, no journal, no stray temp leaf
        assert tree(root, skip=SKIP) == before
    # (c) the retry (for "next-writer" its config_lock finishes the journal first) succeeds
    assert _save(svc).ok
    assert tree(root, skip=SKIP) == want_after
