"""The fail-closed existence probe: only ENOENT/ENOTDIR prove a path absent.

`os.path.lexists`/`exists`/`isfile` return False on ANY OSError, so an unreadable leaf read as
"gone" and a retire/clean/recover path proceeded. `runtime_fs.probe_exists` is the one probe a
safety decision uses; an error it cannot read past is "unknown", never "absent"."""

import errno
import json
import os
import stat

import pytest

from lhpc.core import binary_receipt as brx, boot_restore, config as cfgmod, known_working, runtime_fs
from lhpc.core.config import Config
from lhpc.core.install import Installer
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _failing(monkeypatch, path, err):
    """Make `os.lstat` AND `os.stat` raise `err` for exactly `path` (by string), so a site that
    used `exists`/`isfile` (stat) is caught as well as one that used `lexists` (lstat)."""
    target = os.fspath(path)
    for name in ("lstat", "stat"):
        real = getattr(os, name)

        def fake(p, *a, _real=real, **kw):
            if "dir_fd" not in kw and isinstance(p, (str, os.PathLike)) and os.fspath(p) == target:
                raise OSError(err, os.strerror(err), target)
            return _real(p, *a, **kw)
        monkeypatch.setattr(os, name, fake)


@pytest.mark.safety("existence-fail-closed")
def test_present_absent_and_a_missing_parent(tmp_path):
    leaf = tmp_path / "leaf"
    leaf.write_text("x")
    assert runtime_fs.probe_exists(leaf) == ("present", "")
    assert runtime_fs.probe_exists(tmp_path / "gone") == ("absent", "")
    assert runtime_fs.probe_exists(tmp_path / "gone" / "deeper") == ("absent", "")   # ENOENT parent
    assert runtime_fs.probe_exists(leaf / "below-a-file") == ("absent", "")           # ENOTDIR parent


@pytest.mark.safety("existence-fail-closed")
def test_a_dangling_symlink_is_present(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "nowhere")
    assert runtime_fs.probe_exists(link) == ("present", "")


@pytest.mark.safety("existence-fail-closed")
@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES, errno.ELOOP], ids=["EIO", "EACCES", "ELOOP"])
def test_any_other_error_is_unknown_with_the_error(tmp_path, monkeypatch, err):
    leaf = tmp_path / "leaf"
    leaf.write_text("x")
    _failing(monkeypatch, leaf, err)
    state, why = runtime_fs.probe_exists(leaf)
    assert state == "unknown"
    assert os.strerror(err) in why


@pytest.mark.safety("existence-fail-closed")
def test_an_unprobeable_path_is_unknown(tmp_path):
    state, why = runtime_fs.probe_exists(str(tmp_path) + "/a\0b")
    assert state == "unknown" and why


# ---- every safety site: an unexaminable path is never read as absent ------------------------
#
# Each case lays down the state the site must protect, makes `os.lstat`/`os.stat` fail for
# exactly that path, drives the site through its seam and asserts the refusal/keep AND that the
# protected file is still there.

def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _installer(tmp_path):
    return Installer(Paths(runtime_root=tmp_path), (), Config(values={}), FakeSystem().system)


def _git_tree(tmp_path):
    dest = tmp_path / "src" / "tree"
    (dest / ".git").mkdir(parents=True)
    (dest / "added.txt").write_text("operator file")
    return dest


def _dirty_report_is_dirty(tmp_path, monkeypatch, fail, restore, binary_receipt):
    dest = _git_tree(tmp_path)
    fail(dest / ".git")
    report = _installer(tmp_path).dirty_report(dest, "src/tree")
    assert report.blocks_update() and bool(report)


def _extra_files_cannot_be_taken(tmp_path, monkeypatch, fail, restore, binary_receipt):
    dest = _git_tree(tmp_path)
    fail(dest / ".git")
    assert _installer(tmp_path).extra_files(dest, "src/tree") is None


def _switch_does_not_take_an_unlisted_tree(tmp_path, monkeypatch, fail, restore, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    comp = next(c for st in svc.stacks() for c in st.components if c.id == "loraham-daemon")
    dest = tmp_path / comp.source.path
    dest.mkdir(parents=True)
    (dest / "artifact.bin").write_bytes(b"ELF")
    owned = (f"{comp.source.path}/artifact.bin",)
    assert svc.switch_source_plan([(comp.source.path, comp, "pinned", ("", ""))], owned) == (set(), [])
    fail(dest)
    _replace, refusals = svc.switch_source_plan([(comp.source.path, comp, "pinned", ("", ""))], owned)
    assert [r.split(":", 1)[0] for r in refusals] == [comp.source.path]


def _retire_keeps_the_receipt_for_a_file(tmp_path, monkeypatch, fail, restore, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    rec = binary_receipt(svc)
    victim = tmp_path / rec.files[0]
    fail(victim)
    res = svc.binary_retire("daemon", force=True)
    assert not res.ok and f"  still present: {rec.files[0]}" in res.details
    restore()
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid" and victim.read_bytes() == b"ELF"


def _retire_keeps_the_receipt_for_an_owned_dir(tmp_path, monkeypatch, fail, restore, binary_receipt):
    import dataclasses
    svc = _svc(tmp_path, monkeypatch)
    rec = binary_receipt(svc)
    venv = tmp_path / "build" / "tools" / "meshtastic-cli"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python3").write_bytes(b"x")
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, owned_dirs=("build/tools/meshtastic-cli",)))
    svc.invalidate_snapshot()
    fail(venv)
    res = svc.binary_retire("daemon")
    assert not res.ok and "build/tools/meshtastic-cli stays owned" in res.summary
    restore()
    assert brx.receipt_path(svc._paths, "daemon").exists()     # the dir stays owned
    assert (venv / "bin" / "python3").read_bytes() == b"x"


def _boot_marker_unsafe(which):
    def case(tmp_path, monkeypatch, fail, restore, binary_receipt):
        svc = _svc(tmp_path, monkeypatch)
        if which == "running-band":
            leaf = tmp_path / "state" / "running" / "kiss.band"
            leaf.parent.mkdir(parents=True)
            leaf.write_text("433")
        else:
            leaf = known_working.candidate_path(svc._paths, "kiss")
            leaf.parent.mkdir(parents=True)
            leaf.write_text(json.dumps({"band": "433", "started_at": 1.0}))
        fail(leaf)
        view = svc._boot_marker_view("kiss")
        state = view.running_band_state if which == "running-band" else view.last_start_state
        assert state == "unsafe"
    return case


def _boot_journal_unsafe(tmp_path, monkeypatch, fail, restore, binary_receipt):
    paths = Paths(runtime_root=tmp_path)
    jp = boot_restore.journal_path(paths)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text("{}")
    err = fail(jp)
    assert boot_restore.load_journal(paths) == (None, f"unsafe:unreadable ({err})")


def _config_journal_blocks(tmp_path, monkeypatch, fail, restore, binary_receipt):
    paths = Paths(runtime_root=tmp_path)
    jp = cfgmod._txn_journal(paths)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text("{}")
    fail(jp)
    assert cfgmod.recover_config_transaction(paths) == ""
    restore()
    assert jp.read_text() == "{}"


SITES = {
    "dirty-report": _dirty_report_is_dirty,
    "extra-files": _extra_files_cannot_be_taken,
    "switch-artifact-only": _switch_does_not_take_an_unlisted_tree,
    "retire-still-present": _retire_keeps_the_receipt_for_a_file,
    "retire-owned-dir": _retire_keeps_the_receipt_for_an_owned_dir,
    "boot-running-band": _boot_marker_unsafe("running-band"),
    "boot-last-start": _boot_marker_unsafe("last-start"),
    "boot-journal": _boot_journal_unsafe,
    "config-journal": _config_journal_blocks,
}


@pytest.mark.safety("existence-fail-closed")
@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
@pytest.mark.parametrize("site", sorted(SITES))
def test_an_unexaminable_path_is_never_absent(tmp_path, monkeypatch, binary_receipt, site, err):
    injected = pytest.MonkeyPatch()        # undone on its own: the case's other patches stay

    def fail(path):
        _failing(injected, path, err)
        return OSError(err, os.strerror(err), os.fspath(path))
    try:
        SITES[site](tmp_path, monkeypatch, fail, injected.undo, binary_receipt)
    finally:
        injected.undo()


# ---- the kind decision after the probe: lstat answers, stat fails ---------------------------
#
# `os.path.isdir` after a `present` probe read a stat error as "not a directory": the listing
# came back `[]` (so `all([])` judged the tree artifact-only) and retirement skipped the owned
# dir and dropped its receipt. The kind now comes from the probe's own stat result.

def _stat_failing(monkeypatch, path, err):
    """Make ONLY `os.stat` raise `err` for exactly `path`: `os.lstat` still answers."""
    target, real = os.fspath(path), os.stat

    def fake(p, *a, **kw):
        if "dir_fd" not in kw and isinstance(p, (str, os.PathLike)) and os.fspath(p) == target:
            raise OSError(err, os.strerror(err), target)
        return real(p, *a, **kw)
    monkeypatch.setattr(os, "stat", fake)


@pytest.mark.safety("existence-fail-closed")
def test_probe_stat_returns_the_kind_and_follows_on_request(tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    link = tmp_path / "link"
    link.symlink_to(d)
    state, why, st = runtime_fs.probe_stat(link)
    assert (state, why) == ("present", "") and stat.S_ISLNK(st.st_mode)
    state, why, st = runtime_fs.probe_stat(link, follow=True)
    assert (state, why) == ("present", "") and stat.S_ISDIR(st.st_mode)
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    assert runtime_fs.probe_stat(tmp_path / "dangling", follow=True) == ("absent", "", None)
    _stat_failing(monkeypatch, d, errno.EIO)
    state, why, st = runtime_fs.probe_stat(d, follow=True)
    assert state == "unknown" and os.strerror(errno.EIO) in why and st is None
    assert runtime_fs.probe_stat(d)[0] == "present"


@pytest.mark.safety("existence-fail-closed")
@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
def test_a_tree_whose_stat_fails_is_not_listed_as_empty(tmp_path, monkeypatch, err):
    svc = _svc(tmp_path, monkeypatch)
    comp = next(c for st in svc.stacks() for c in st.components if c.id == "loraham-daemon")
    dest = tmp_path / comp.source.path
    dest.mkdir(parents=True)
    (dest / "artifact.bin").write_bytes(b"ELF")
    owned = (f"{comp.source.path}/artifact.bin",)
    _stat_failing(monkeypatch, dest, err)
    assert runtime_fs.probe_exists(dest) == ("present", "")       # lstat answers
    with pytest.raises(OSError, match=os.strerror(err)):
        svc._rel_files_under(comp.source.path)
    _replace, refusals = svc.switch_source_plan([(comp.source.path, comp, "pinned", ("", ""))], owned)
    assert [r.split(":", 1)[0] for r in refusals] == [comp.source.path]
    assert (dest / "artifact.bin").read_bytes() == b"ELF"


@pytest.mark.safety("existence-fail-closed")
@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
def test_retire_keeps_an_owned_dir_whose_stat_fails(tmp_path, monkeypatch, binary_receipt, err):
    import dataclasses
    svc = _svc(tmp_path, monkeypatch)
    rec = binary_receipt(svc)
    venv = tmp_path / "build" / "tools" / "meshtastic-cli"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python3").write_bytes(b"x")
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, owned_dirs=("build/tools/meshtastic-cli",)))
    svc.invalidate_snapshot()
    injected = pytest.MonkeyPatch()
    try:
        _stat_failing(injected, venv, err)
        res = svc.binary_retire("daemon")
    finally:
        injected.undo()
    assert not res.ok and "build/tools/meshtastic-cli stays owned" in res.summary
    assert res.details == [f"  could not remove build/tools/meshtastic-cli "
                           f"([Errno {err}] {os.strerror(err)}: '{venv}')"]
    assert brx.receipt_path(svc._paths, "daemon").exists()     # the dir stays owned
    assert (venv / "bin" / "python3").read_bytes() == b"x"


# ---- a `.git` that is a symlink: resolved or not --------------------------------------------
#
# The fake answers `git status`/`ls-files` as a CLEAN enclosing repository would: real git,
# given a `.git` it cannot use, walks up to the first enclosing checkout and reports on that.

def _git_answers_clean(dest):
    from lhpc.core.probes.backends import CommandResult
    ok = CommandResult(returncode=0, stdout="", stderr="")
    return FakeSystem(commands={
        ("git", "-C", str(dest), "status", "--porcelain", "-z", "--untracked-files=all"): ok,
        ("git", "-C", str(dest), "ls-files", "-z", "--others"): ok})


@pytest.mark.safety("existence-fail-closed")
@pytest.mark.parametrize("kind", ["dangling", "loop"])
def test_a_git_symlink_that_does_not_resolve_is_never_clean(tmp_path, kind):
    from lhpc.core.install import DirtyReport
    dest = tmp_path / "src" / "tree"
    dest.mkdir(parents=True)
    (dest / "added.txt").write_text("operator file")
    (dest / ".git").symlink_to(tmp_path / "nowhere" if kind == "dangling" else dest / ".git")
    fake = _git_answers_clean(dest)
    inst = Installer(Paths(runtime_root=tmp_path), (), Config(values={}), fake.system)
    anomaly = {"dangling": ".git is a dangling symlink",
               "loop": (f".git is a symlink that cannot be followed ([Errno {errno.ELOOP}] "
                        f"{os.strerror(errno.ELOOP)}: '{dest / '.git'}')")}[kind]
    assert inst.dirty_report(dest, "src/tree") == DirtyReport(tracked=(f"({anomaly} — treating as dirty)",))
    assert inst.extra_files(dest, "src/tree") is None
    assert fake.calls == []                                   # never handed to git
    assert (dest / ".git").is_symlink() and (dest / "added.txt").read_text() == "operator file"


@pytest.mark.safety("existence-fail-closed")
def test_no_git_and_a_git_symlink_that_resolves_keep_their_reading(tmp_path):
    from lhpc.core.install import DirtyReport
    dest = tmp_path / "src" / "tree"
    dest.mkdir(parents=True)
    (dest / "added.txt").write_text("operator file")
    fake = _git_answers_clean(dest)
    inst = Installer(Paths(runtime_root=tmp_path), (), Config(values={}), fake.system)
    assert inst.dirty_report(dest, "src/tree") == DirtyReport()           # not a checkout: clean
    assert inst.extra_files(dest, "src/tree") == ()
    assert fake.calls == []
    (tmp_path / "gitdir").mkdir()
    (dest / ".git").symlink_to(tmp_path / "gitdir")
    assert inst.dirty_report(dest, "src/tree") == DirtyReport()           # git consulted
    assert inst.extra_files(dest, "src/tree") == ()
    assert [c[3] for c in fake.calls] == ["status", "ls-files"]
