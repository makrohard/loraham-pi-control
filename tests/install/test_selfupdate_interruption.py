"""A self-update interrupted at every durable write is recovered by the next apply, and no update
changes saved configuration.

The self-update transaction (`service_selfupdate._self_update_locked`, `selfupdate.py`) takes the
controller admission marker, marks the firewall and proxy follow-ups BEFORE the checkout moves,
advances the checkout and refreshes the status cache. The markers and the cache go through
`runtime_fs`; the advance is a git call, gated as one point. Each case fails ONE write (disk full,
I/O error, Ctrl-C) during an update whose new manifest changes a default the saved config holds,
and proves: the next apply — the recovery path — ends at the new commit with the saved value as
saved, no migration journal and no git anchor.
"""
from __future__ import annotations

import types

import pytest

import gitrepo
from selfupdate_fakes import WEB_INACTIVE
from interrupts import FAILURES, durable_writes, run_interrupted
from lhpc.core import config as cfgmod
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import RealSystem
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.safety("selfupdate-transaction")

MANIFEST = ('[[stack]]\nid="s"\nname="S"\nmain="c"\n'
            '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\n'
            'readiness="process"\n'
            '  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{}"\n')
# The checkout's advance (`merge --ff-only`) is a git call among many reads: routed through its
# own name so the harness gates it, and only it, as one point.
GIT = types.SimpleNamespace(__name__="git", advance=None)
RAW = [(GIT, "advance")]
# The apply's durable writes, in order.
POINTS = [
    ("write_marker", "state/locks/controller-task-admission.owner"),
    ("write_marker", "state/firewall-postupdate.pending"),   # the firewall follow-up, before any move
    ("write_marker", "state/webserver-render.pending"),      # the proxies' re-render, before any move
    ("git.advance", "merge"),                                # the checkout moves
    ("write_marker", "state/selfupdate.json"),               # status cache
    ("unlink", "state/locks/controller-task-admission.owner"),
]


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A self-hosted checkout at a manifest whose `ropt` defaults to OLD, the saved config holding
    OLD, and an upstream commit changing that default to NEW. Returns (service factory, work, rt)."""
    _origin, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    (work / "manifest.toml").write_text(MANIFEST.format("OLD"))
    gitrepo.git(work, "add", "manifest.toml")
    gitrepo.git(work, "commit", "-qm", "manifest")
    gitrepo.git(work, "push", "-q", "origin", "main")
    gitrepo.git(up, "fetch", "-q", "origin")
    gitrepo.git(up, "reset", "-q", "--hard", "origin/main")
    (up / "manifest.toml").write_text(MANIFEST.format("NEW"))
    gitrepo.git(up, "commit", "-qam", "new default")
    gitrepo.git(up, "push", "-q", "origin", "HEAD:main")

    real_git = selfupdate._git
    monkeypatch.setattr(GIT, "advance", real_git)
    monkeypatch.setattr(selfupdate, "_git", lambda system, root, args, timeout: (
        GIT.advance if args[0] == "merge" else real_git)(system, root, args, timeout))
    rt = tmp_path / "rt"
    (rt / "config" / "stacks").mkdir(parents=True)

    def svc():          # a fresh service: what the next process sees
        return ControllerService(manifest_path=work / "manifest.toml", system=RealSystem(),
                                 paths=Paths(runtime_root=rt))
    from stores_fixture import inst_of                      # K6: the instance's own file (the baseline list's r1)
    cfgmod.save_stack_config(svc()._paths, "s", {"ropt": "OLD"}, inst=inst_of(svc(), "s"))
    return svc, work, rt


def test_the_apply_writes_exactly_the_pinned_points(box):
    svc, _work, rt = box
    with durable_writes(rt, raw=RAW) as log:
        assert svc().self_update_apply().ok
    assert [(w, "merge" if w == "git.advance" else p) for w, p in log] == POINTS


def _kept_as_saved(svc, rt, work):
    """The saved `ropt` is still OLD, though the new manifest's default is NEW; no migration journal
    and no git anchor were written."""
    from stores_fixture import inst_of
    assert cfgmod.load_stack_config(svc()._paths, "s", inst=inst_of(svc(), "s"))["ropt"] == "OLD"
    assert svc().stack_config("s")["ropt"] == "OLD"
    assert not (rt / "state" / "selfupdate-migrate.json").exists()
    assert gitrepo.git(work, "for-each-ref", "refs/lhpc") == ""


def test_an_update_keeps_a_value_saved_at_the_old_default(box):
    """The update moves the checkout to a manifest whose default changed; the value saved equal to
    the old default stays saved, and the stack keeps it (no config default migration)."""
    svc, work, rt = box
    res = svc().self_update_apply()
    assert res.ok, res.summary
    assert gitrepo.git(work, "rev-parse", "HEAD") == gitrepo.git(work, "rev-parse", "origin/main")
    assert not any("migrat" in d for d in res.details), res.details
    _kept_as_saved(svc, rt, work)


@pytest.mark.parametrize("journal", ["{not json", '{"completed": null, "prepared": null, "v": 3}'])
def test_a_left_journal_and_anchor_are_not_read(box, journal):
    """An older release's migration journal (damaged, or well formed) and its git anchor are left as
    they are: the check and the apply neither block on them nor change them."""
    svc, work, rt = box
    (rt / "state").mkdir(parents=True, exist_ok=True)
    left = rt / "state" / "selfupdate-migrate.json"
    left.write_text(journal)
    head = gitrepo.git(work, "rev-parse", "HEAD")
    gitrepo.git(work, "update-ref", "refs/lhpc/selfupdate/" + "a" * 32, head)
    assert svc().self_update_check().ok
    res = svc().self_update_apply()
    assert res.ok, res.summary
    assert gitrepo.git(work, "rev-parse", "HEAD") == gitrepo.git(work, "rev-parse", "origin/main")
    assert left.read_text() == journal
    assert gitrepo.git(work, "for-each-ref", "--format=%(refname)", "refs/lhpc") == (
        "refs/lhpc/selfupdate/" + "a" * 32)
    from stores_fixture import inst_of
    assert cfgmod.load_stack_config(svc()._paths, "s", inst=inst_of(svc(), "s"))["ropt"] == "OLD"


CASES = [pytest.param(k, f, id=f"{w}:{p}-{f}")
         for k, (w, p) in enumerate(POINTS) for f in sorted(FAILURES)]


@pytest.mark.parametrize(("point", "failure"), CASES)
def test_an_interrupted_apply_is_finished_by_the_next(box, point, failure):
    svc, work, rt = box
    first = svc()
    run_interrupted(rt, first.self_update_apply, fail_at=point, exc=FAILURES[failure], raw=RAW)

    # the next apply recovers and finishes: new commit, the saved value as saved, no journal/anchor
    nxt = svc()
    assert nxt.self_update_apply().ok
    assert gitrepo.git(work, "rev-parse", "HEAD") == gitrepo.git(work, "rev-parse", "origin/main")
    _kept_as_saved(svc, rt, work)
    assert not (rt / "state" / "locks" / "controller-task-admission.owner").exists()
    # (d) the checkout moved, so its firewall follow-up is recorded for the next console start
    assert (rt / "state" / "firewall-postupdate.pending").exists()


@pytest.mark.parametrize("path", ["apply", "one-click"])
def test_an_interrupted_sync_to_refresh_gap_keeps_the_record(op_svc, monkeypatch, tmp_path, path):
    """`venv-unsynced` is recorded and the next run syncs the venv; an interruption before the unit
    refresh finishes must leave the record, so the next `--apply` resumes the refresh too — the
    record is cleared only with the units refreshed."""
    import json
    from lhpc.core.services import ActionResult, ControllerService
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False, stop_console=None:
                        ActionResult(True, "Already up to date.", data={"already": True}))
    svc, _fake = (op_svc(dict(WEB_INACTIVE), invocation=False) if path == "apply"
                  else op_svc(units=True, invocation=True))
    rec = svc._incomplete_path()
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"state": "venv-unsynced", "detail": "the venv sync FAILED"}))

    def interrupted(self):
        raise KeyboardInterrupt
    # Stubs the collaborator, systemd: the unit refresh is interrupted (a stop between the sync
    # and the refresh).
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update", interrupted)
    if path == "one-click":
        (svc._paths.runtime_root / "state" / "selfupdate.request").write_text("normal\n")
    with pytest.raises(KeyboardInterrupt):
        svc.self_update_apply_operator() if path == "apply" else svc.self_update_run_service()
    assert svc.self_update_incomplete()[0] == "venv-unsynced"


def test_a_firewall_marker_that_cannot_be_written_stops_the_update_before_it_moves(
        box, monkeypatch):
    """The firewall follow-up is recorded before the checkout moves; when it cannot be, the
    update stops there: nothing moved, no journal, no anchor, and the result says why."""
    from lhpc.core import runtime_fs
    svc, work, rt = box
    head = gitrepo.git(work, "rev-parse", "HEAD")
    real = runtime_fs.write_marker

    def full_disk(paths, path, *a, **k):
        if str(path).endswith("firewall-postupdate.pending"):
            raise OSError(28, "No space left on device")
        return real(paths, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "write_marker", full_disk)
    res = svc().self_update_apply()
    assert not res.ok and res.data.get("fw_mark_failed")
    assert "state/firewall-postupdate.pending" in res.details[0]     # the cause names its marker
    assert res.next_commands == ["lhpc self-update --apply"]
    assert gitrepo.git(work, "rev-parse", "HEAD") == head
    _kept_as_saved(svc, rt, work)


def test_a_render_marker_that_cannot_be_written_stops_the_update_before_it_moves(
        box, monkeypatch):
    """The proxies' re-render (CO-14) is recorded before the checkout moves; when it cannot be,
    the update stops there: nothing moved, no journal, no anchor, and the result says why."""
    from lhpc.core import runtime_fs
    svc, work, rt = box
    head = gitrepo.git(work, "rev-parse", "HEAD")
    real = runtime_fs.write_marker

    def full_disk(paths, path, *a, **k):
        if str(path).endswith("webserver-render.pending"):
            raise OSError(28, "No space left on device")
        return real(paths, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "write_marker", full_disk)
    res = svc().self_update_apply()
    assert not res.ok and res.data.get("fw_mark_failed")   # the one follow-up branch
    assert "state/webserver-render.pending" in res.details[0]
    assert gitrepo.git(work, "rev-parse", "HEAD") == head
    _kept_as_saved(svc, rt, work)
