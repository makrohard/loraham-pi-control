"""Getting a managed source onto the box and keeping it current: the source check and its
verdicts, the source-parent transaction and its filesystem primitives, ownership records, pins
and selectors, the activation transaction with its journal and crash recovery, race safety
against a concurrent substitution, and the dirty/carry rules for an operator's local additions.

Real git in temp directories (the `git`/`make_repo`/`installer` fixtures in conftest); the
controller-level cases inject a FakeSystem."""

from __future__ import annotations
import errno
import json
import os
import re
import shutil
import time
import pytest
from lhpc.core import stackupdates as su, source_fs, source_registry
from lhpc.core.paths import Paths, PathContainmentError
from lhpc.core.probes.backends import CommandResult as CR, FakeSystem, System, CommandResult
from lhpc.core.services import ControllerService
from pathlib import Path
from lhpc.core.config import Config
from lhpc.core.install import Installer
from lhpc.core.model import Component, ComponentKind, SourceSpec, SourceState
from lhpc.core.probes import RealSystem


def _bind_unix_socket(directory, name):
    """Create an AF_UNIX socket at `directory/name`, addressed through the directory's fd.

    `sun_path` is 108 bytes. The absolute path to a pytest temp directory is already long, and
    pytest-xdist adds a `popen-gwN/` level on top, so binding by absolute path made this test
    fail with "AF_UNIX path too long" under `-n` — for the length of a temp path, not for
    anything LHPC did. `/proc/self/fd/<n>/<name>` is short whatever the directory is called.
    """
    import socket
    fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
    try:
        s = socket.socket(socket.AF_UNIX)
        try:
            s.bind(f"/proc/self/fd/{fd}/{name}")
        finally:
            s.close()
    finally:
        os.close(fd)


# --- the source check: what "up to date" may and may not mean --------------------------------------
DAEMON_REMOTE = "https://github.com/makrohard/LoRaHAM_Daemon.git"


DAEMON_BRANCH = "main"


RADIOLIB_REMOTE = "https://github.com/jgromes/RadioLib"


A = "a" * 40


B = "b" * 40


def _install(tmp_path, rel):
    d = tmp_path / rel
    d.mkdir(parents=True, exist_ok=True)
    return d


def _svc(tmp_path, commands=None):
    return ControllerService(system=FakeSystem(commands=commands or {}).system,
                             paths=Paths(runtime_root=tmp_path))


def _ls_remote(remote, ref, sha):
    return {("git", "ls-remote", remote, ref): CR(0, f"{sha}\trefs/heads/{ref}\n", "")}


def _git_src(src, sha):
    """A clean git checkout at `sha`. `probe_source` needs both: a failing `status
    --porcelain=v2` makes it return UNKNOWN *without* a head, and the row then renders no @head."""
    a = str(src)
    return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),     # the source check's own read
            ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}


def test_uninstalled_source_is_unknown_and_makes_no_network_call(tmp_path):
    svc = _svc(tmp_path)                                   # no src/ dirs exist
    res = svc.source_check("daemon")
    assert not any("ls-remote" in " ".join(c) for c in svc._system.runner.calls)
    assert su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]["status"] == su.UNKNOWN
    # "nothing to compare" is NOT a passing check: never ok (green), never worded "up to date".
    assert not res.ok
    assert "up to date" not in res.summary
    assert "No installed/comparable sources could be checked" in res.summary


def test_all_unknown_never_reports_up_to_date(tmp_path):
    res = _svc(tmp_path).source_check()                     # whole box, nothing installed
    assert not res.ok and "up to date" not in res.summary
    assert res.data["counts"][su.UP_TO_DATE] == 0
    assert res.data["counts"][su.UNKNOWN] == res.data["checked"]


def test_mixed_up_to_date_and_unknown_is_qualified_and_not_green(tmp_path):
    # daemon installed and current; radiolib never installed -> unknown.
    ds = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(ds, A)}
    res = _svc(tmp_path, cmds).source_check("daemon")
    assert not res.ok                                       # a partial comparison is not a green
    assert res.data["counts"][su.UP_TO_DATE] == 1 and res.data["counts"][su.UNKNOWN] == 1
    assert res.data["checked"] == 2
    assert "All checked sources are up to date" not in res.summary


def test_unqualified_up_to_date_requires_every_source_comparable(tmp_path):
    ds = _install(tmp_path, "src/loraham-daemon")
    rl = _install(tmp_path, "src/RadioLib")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(ds, A),
            **_ls_remote(RADIOLIB_REMOTE, "master", A), **_git_src(rl, A)}
    res = _svc(tmp_path, cmds).source_check("daemon")
    assert res.ok
    assert res.data["counts"][su.UP_TO_DATE] == res.data["checked"] == 2
    assert res.data["counts"][su.UNKNOWN] == 0
    assert "up to date" in res.summary                      # the token that means green


def test_behind_with_an_unknown_sibling_is_qualified_and_not_green(tmp_path):
    ds = _install(tmp_path, "src/loraham-daemon")           # radiolib absent -> unknown
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(ds, A)}
    res = _svc(tmp_path, cmds).source_check("daemon")
    assert "1 of 2 source(s) behind" in res.summary and "1 not comparable" in res.summary
    assert not res.ok


def test_behind_records_both_heads(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(src, A)}
    svc = _svc(tmp_path, cmds)
    res = svc.source_check("loraham-daemon")
    assert res.ok and "1 of 1 source(s) behind" in res.summary
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.BEHIND
    assert e["local_head_at_check"] == A and e["upstream_head"] == B
    assert e["remote"] == DAEMON_REMOTE and e["source_path"] == "src/loraham-daemon"


def test_up_to_date_when_heads_match(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(src, A)}
    res = _svc(tmp_path, cmds).source_check("loraham-daemon")
    assert res.ok and "up to date" in res.summary
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.UP_TO_DATE


def test_failed_ls_remote_is_error_not_unknown(tmp_path):
    # An unreachable remote must NOT read like "nothing to compare" — and must not report ok.
    _install(tmp_path, "src/loraham-daemon")
    cmds = {("git", "ls-remote", DAEMON_REMOTE, DAEMON_BRANCH): CR(128, "", "could not resolve host")}
    res = _svc(tmp_path, cmds).source_check("loraham-daemon")
    assert not res.ok                                       # a failed check is not a clean bill
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.ERROR


def test_broken_checkout_is_error(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B),
            ("git", "-C", str(src), "rev-parse", "HEAD"): CR(128, "", "not a git repository")}
    res = _svc(tmp_path, cmds).source_check("loraham-daemon")
    assert not res.ok
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.ERROR


def test_stack_sweep_covers_non_runnable_library_components(tmp_path):
    # radiolib has a remote but no run_argv — `_resolve` would drop it; the sweep must not.
    ds = _install(tmp_path, "src/loraham-daemon")
    rl = _install(tmp_path, "src/RadioLib")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(ds, A),
            **_ls_remote(RADIOLIB_REMOTE, "master", B), **_git_src(rl, A)}
    _svc(tmp_path, cmds).source_check("daemon")
    comps = su.view(Paths(runtime_root=tmp_path))["components"]
    assert comps["loraham-daemon"]["status"] == su.UP_TO_DATE
    assert comps["radiolib"]["status"] == su.BEHIND


def test_unknown_target_errors_without_network(tmp_path):
    svc = _svc(tmp_path)
    res = svc.source_check("no-such-thing")
    assert not res.ok and "Unknown stack or component" in res.summary
    assert svc._system.runner.calls == []


def test_update_status_maps_behind_same_error_and_no_component(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    comp = _svc(tmp_path).stack("daemon").component("loraham-daemon")

    behind = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(src, A)}
    assert _svc(tmp_path, behind).update_status(comp) == "update-available"

    same = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(src, A)}
    assert _svc(tmp_path, same).update_status(comp) == "up-to-date"

    # a probe ERROR collapses back to "unknown" for the legacy callers (update()'s dry-run)
    fail = {("git", "ls-remote", DAEMON_REMOTE, DAEMON_BRANCH): CR(1, "", "boom")}
    assert _svc(tmp_path, fail).update_status(comp) == "unknown"
    assert _svc(tmp_path).update_status(None) == "unknown"


# --- the source-parent transaction and its filesystem primitives -----------------------------------
def _paths(tmp_path):
    root = tmp_path / "rt"
    root.mkdir()
    return Paths(runtime_root=root), root


def test_rmtree_removes_a_normal_tree(tmp_path):
    paths, root = _paths(tmp_path)
    t = root / "src" / "app"
    (t / "sub").mkdir(parents=True)
    (t / "f").write_text("x"); (t / "sub" / "g").write_text("y")
    source_fs.rmtree_at(paths, t)
    assert not t.exists()


def test_rmtree_missing_leaf_is_noop(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    source_fs.rmtree_at(paths, root / "src" / "gone")           # no error


def test_rmtree_unlinks_symlink_leaf_without_following(tmp_path):
    # A LINKED external source: uninstall/discard removes only the runtime symlink leaf.
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "external"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    link = root / "src" / "app"; os.symlink(outside, link)
    source_fs.rmtree_at(paths, link)
    assert not link.is_symlink() and not link.exists()          # symlink leaf gone
    assert (outside / "keep").read_text() == "KEEP"             # external target UNTOUCHED


def test_rmtree_does_not_follow_symlink_inside_tree(tmp_path):
    paths, root = _paths(tmp_path)
    t = root / "src" / "app"; t.mkdir(parents=True)
    outside = tmp_path / "victim"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    os.symlink(outside, t / "danger")                           # symlink INSIDE the tree
    source_fs.rmtree_at(paths, t)
    assert not t.exists()                                        # tree removed
    assert (outside / "keep").read_text() == "KEEP"             # symlink target UNTOUCHED


def test_rmtree_swapped_source_parent_blocks(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    os.symlink(outside, root / "src")                           # source PARENT is a symlink
    with pytest.raises(PathContainmentError):
        source_fs.rmtree_at(paths, root / "src" / "app")
    assert (outside / "keep").read_text() == "KEEP"             # nothing outside touched


def test_rmtree_refuses_special_leaf(tmp_path):
    paths, root = _paths(tmp_path)
    t = root / "src" / "app"; t.mkdir(parents=True)
    os.mkfifo(t / "pipe")                                       # a FIFO -> fail closed
    with pytest.raises(PathContainmentError):
        source_fs.rmtree_at(paths, t)
    assert (t / "pipe").exists()                                # evidence retained


def test_leaf_kind_classifies_no_follow(tmp_path):
    paths, root = _paths(tmp_path)
    d = root / "src"; d.mkdir(parents=True)
    (d / "f").write_text("x"); (d / "sub").mkdir(); os.symlink(d / "f", d / "ln")
    assert source_fs.leaf_kind(paths, d / "f") == "file"
    assert source_fs.leaf_kind(paths, d / "sub") == "dir"
    assert source_fs.leaf_kind(paths, d / "ln") == "symlink"    # not followed
    assert source_fs.leaf_kind(paths, d / "gone") == "absent"


_META = {"selector": "pinned", "resolved_commit": "", "remote": "", "components": ["app"],
         "had_prior": True}


def _activate(inst, dest: Path, staging: Path, verify_active=None) -> str:
    """Test entry to the activation transaction: open the source-parent transaction, capture
    the prior and candidate leaves, synthesize a minimal valid meta, and run `_activate_held`
    exactly as `install()` does."""
    prior = cand = None
    try:
        with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
            meta = {"selector": "pinned", "resolved_commit": "", "remote": "",
                    "components": [dest.name or "src"],
                    "had_prior": txn.leaf_kind(dest.name) != "absent"}
            try:
                if txn.leaf_kind(dest.name) == "dir":
                    prior = txn.capture_leaf(dest.name)
                if txn.leaf_kind(staging.name) == "dir":
                    cand = txn.capture_leaf(staging.name)
            except (OSError, PathContainmentError):
                return "recovery-required"
            return inst._activate_held(txn, dest, staging, meta, verify_active,
                                       handle=cand, prior=prior)
    except PathContainmentError:
        return "recovery-required"
    finally:
        for h in (prior, cand):
            if h is not None:
                h.close()


@pytest.mark.parametrize("cid", ["loraham-voice", "loraham-voice-cli"])
def test_voice_verifies_its_pin_like_any_other_source(tmp_path, make_repo, cid):
    """Voice is an ordinary pinned source: a local checkout that is NOT at the manifest pin
    cannot satisfy a `pinned` install.

    Both Voice components share one checkout, and NOTHING else in the suite would notice if only
    one of them lost `artifact = true` — the pin-consistency gate compares `pin_commit`/`pin_tag`
    across shared consumers and never looks at the flag. Hence both are driven here.

    With the flag this returned True for ANY tree, so the manifest pin was decorative and a
    release-bot hold on `src/LoRaHAM_Voice` could not be enforced.
    """
    from lhpc.core.manifest import load_manifest
    comp = next(c for st in load_manifest() for c in st.components if c.id == cid)
    assert comp.source.pin_commit, "the manifest must pin Voice for this to mean anything"
    paths, root = _paths(tmp_path)
    local = root / "voice"
    make_repo(local)                                    # a real repo whose HEAD is NOT the pin
    inst = Installer(paths, load_manifest(), Config(values={}), RealSystem())

    assert not inst._fallback_satisfies(comp.source, local, "pinned", "")


def test_real_git_clone_through_controller_pinned_path(tmp_path, git, make_repo):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    upstream = tmp_path / "upstream"; make_repo(upstream, {"MARK": "payload"})
    with source_fs.ManagedSourceTransaction(paths, root / "src") as txn:
        pin = txn.pinned_path()
        cand = f"{pin}/.app.candidate-x"
        git(tmp_path, "clone", "-q", f"file://{upstream}", cand)
        # Git verification/check-out through the SAME controller-pinned path
        git(tmp_path, "-C", cand, "rev-parse", "HEAD")
    # The candidate landed in the intended HELD source parent (real path)
    assert (root / "src" / ".app.candidate-x" / "MARK").read_text() == "payload"


def test_parent_swap_after_fd_cannot_redirect_clone_outside(tmp_path, git, make_repo):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    upstream = tmp_path / "upstream"; make_repo(upstream, {"MARK": "payload"})
    outside = tmp_path / "outside"; outside.mkdir()
    moved = tmp_path / "moved-src"
    with source_fs.ManagedSourceTransaction(paths, root / "src") as txn:
        pin = txn.pinned_path()
        # AFTER acquiring the held fd, move the real parent aside and point its path at
        # `outside` — the held fd still refers to the ORIGINAL inode (now at `moved`).
        os.rename(root / "src", moved)
        os.symlink(outside, root / "src")
        cand = f"{pin}/.app.candidate-x"
        git(tmp_path, "clone", "-q", f"file://{upstream}", cand)
    assert list(outside.iterdir()) == []                     # NOT redirected through the swap
    assert (moved / ".app.candidate-x" / "MARK").read_text() == "payload"   # landed in held inode


def test_transaction_renames_survive_parent_swap(tmp_path):
    # A parent-path swap AFTER opening the transaction cannot redirect later renames —
    # they keep hitting the ORIGINAL held inode, never the swapped-in path.
    paths, root = _paths(tmp_path)
    src = root / "src"; src.mkdir(parents=True)
    (src / "app").mkdir(); (src / "app" / "m").write_text("v")
    outside = tmp_path / "outside"; outside.mkdir()
    moved = tmp_path / "moved"
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        os.rename(src, moved); os.symlink(outside, src)      # swap parent path -> outside
        assert txn.leaf_kind("app") == "dir"                 # held fd still sees the original
        txn.rename("app", ".app.prev")                       # rename #1 (archive)
        txn.create_candidate(".app.candidate")               # exclusive create in held inode
        txn.rename(".app.candidate", "app")                  # rename #2 (activate)
    assert (moved / "app").is_dir()                          # activated within held inode
    assert (moved / ".app.prev" / "m").read_text() == "v"   # prior archived in held inode
    assert list(outside.iterdir()) == []                     # swapped path NEVER touched


def test_transaction_swapped_parent_blocks_at_enter(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "out"; outside.mkdir()
    os.symlink(outside, root / "src")                        # parent is a symlink at open time
    with pytest.raises(PathContainmentError):
        with source_fs.ManagedSourceTransaction(paths, root / "src"):
            pass


def test_transaction_rmtree_and_pinned_path(tmp_path):
    paths, root = _paths(tmp_path)
    src = root / "src"; src.mkdir(parents=True)
    (src / "cand").mkdir(); (src / "cand" / "f").write_text("x")
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        assert txn.pinned_path() == f"/proc/{os.getpid()}/fd/{txn.fd}"   # controller-pinned
        txn.rmtree("cand")
        txn.fsync()
    assert not (src / "cand").exists()


def _new_txn_candidate(tmp_path):
    paths, root = _paths(tmp_path)
    src = root / "src"; src.mkdir(parents=True)
    return paths, root, src


def test_candidate_handle_pinned_path_writes_into_held_inode(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        with open(f"{h.pinned_path()}/f", "w") as fh:
            fh.write("x")
    assert (src / ".app.candidate" / "f").read_text() == "x"    # landed in the candidate inode


def test_candidate_verify_detects_symlink_swap(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        shutil.rmtree(src / ".app.candidate")                  # remove the leaf entry
        os.symlink(outside, src / ".app.candidate")            # swap for a symlink to outside
        assert txn.verify_candidate(h) is False                # swap detected (not our inode)
        # the FD-pinned path STILL refers to the original (now-unlinked) inode — a write via
        # it either fails or lands in the held inode, NEVER through the swapped-in symlink.
        try:
            with open(f"{h.pinned_path()}/g", "w") as fh:
                fh.write("y")
        except OSError:
            pass
    assert not (outside / "g").exists()                        # never redirected outside
    assert (outside / "keep").read_text() == "KEEP"


def test_candidate_verify_detects_file_swap(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        shutil.rmtree(src / ".app.candidate")
        (src / ".app.candidate").write_text("evil")            # swap for a regular file
        assert txn.verify_candidate(h) is False


def test_candidate_verify_detects_replacement_directory(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        shutil.rmtree(src / ".app.candidate")
        (src / ".app.candidate").mkdir()                       # different-inode replacement dir
        assert txn.verify_candidate(h) is False                # inode differs -> refused


def _quarantine(tmp_path, populate, name="quarantine"):
    """(parent_fd, leaf_path, ident) for a POPULATED directory leaf, bound exactly the way the
    transaction binds `.prev`: the v5 identity is captured LAST (creating a child bumps the
    directory's ctime)."""
    parent = tmp_path / "src"
    parent.mkdir(exist_ok=True)
    leaf = parent / name
    leaf.mkdir()
    populate(leaf)
    fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY)
    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    return fd, leaf, [st.st_dev, st.st_ino, st.st_ctime_ns]


def test_remove_bound_clears_a_quarantine_holding_a_runtime_socket(tmp_path):
    """A checkout a stack RUNS FROM legitimately holds a runtime socket (meshcom's
    `.run/gps-uart1.sock`). Refusing it left the archive half-deleted: the partial removal
    bumped `.prev`'s ctime, its recorded identity could never be re-proven, and every source
    operation on the box stayed blocked (live-found on the Zero)."""
    def _populate(leaf):
        (leaf / ".run").mkdir()
        (leaf / "README.md").write_text("x")
        _bind_unix_socket(leaf / ".run", "gps-uart1.sock")
        os.mkfifo(leaf / ".run" / "fifo")
    fd, leaf, ident = _quarantine(tmp_path, _populate)
    try:
        ok, why = source_fs.remove_bound(fd, leaf.name, ident, allow_ipc=True)
        assert ok, why
        assert not leaf.exists()
    finally:
        os.close(fd)


def test_remove_bound_still_refuses_ipc_leaves_by_default(tmp_path):
    fd, leaf, ident = _quarantine(tmp_path, lambda leaf: os.mkfifo(leaf / "pipe"))
    try:
        ok, why = source_fs.remove_bound(fd, leaf.name, ident)
        assert not ok and "remainder retained" in why
        assert (leaf / "pipe").exists()               # evidence retained
    finally:
        os.close(fd)


def test_remove_bound_reports_a_refused_leaf_instead_of_raising(tmp_path):
    """The refusal must be a TYPED result: it used to escape as an exception that the caller
    reported as "managed source parent is unsafe (symlinked/swapped)" — a message that sent the
    operator looking for a symlink that was never there (live-found on the Zero)."""
    fd, leaf, ident = _quarantine(tmp_path, lambda leaf: os.mkfifo(leaf / "pipe"))
    try:
        ok, why = source_fs.remove_bound(fd, leaf.name, ident)   # must not raise
        assert not ok and "bound removal incomplete" in why
    finally:
        os.close(fd)


# --- ownership records, pins and selectors ------------------------------------------------------------
def _comp(path="src/app", local_dir="app", remote="", pin="", branch=""):
    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path=path, local_dir=local_dir, remote=remote,
                                       pin_commit=pin, branch=branch))


def _rec(inst, rel="src/app"):
    return source_registry.read_record(inst.paths, rel)


def test_record_roundtrip_and_remove(tmp_path):
    paths = Paths(runtime_root=tmp_path / "rt")
    (tmp_path / "rt").mkdir()
    rec = source_registry.RegistryRecord(
        source_rel="src/app", remote="https://github.com/x/y.git", selector="pinned",
        resolved_commit="a" * 40, adopted_at=1.0, txn_id="t" * 64, components=("app", "app2"))
    assert source_registry.write_record(paths, rec)
    got = source_registry.read_record(paths, "src/app")
    assert got == rec
    assert source_registry.read_record(paths, "src/other") is None      # distinct identity
    assert source_registry.remove_record(paths, "src/app")
    assert source_registry.read_record(paths, "src/app") is None
    assert source_registry.remove_record(paths, "src/app")              # missing = success


def test_malformed_and_symlinked_records_are_absent(tmp_path):
    paths = Paths(runtime_root=tmp_path / "rt")
    rp = source_registry.record_path(paths, "src/app")
    rp.parent.mkdir(parents=True)
    rp.write_text("not json {{{")
    assert source_registry.read_record(paths, "src/app") is None        # malformed
    rp.unlink()
    rp.write_text(json.dumps({"version": 99}))
    assert source_registry.read_record(paths, "src/app") is None        # wrong version
    rp.unlink()
    (rp.parent / "real.json").write_text(json.dumps({
        "version": 1, "source_rel": "src/app", "remote": "", "selector": "pinned",
        "resolved_commit": "", "adopted_at": 1.0, "txn_id": "", "strategy": "",
        "components": ["app"]}))
    os.symlink("real.json", rp)
    assert source_registry.read_record(paths, "src/app") is None        # symlink leaf refused
    # a record claiming a DIFFERENT source_rel than its filename identity is refused
    rp.unlink()
    rp.write_text(json.dumps({
        "version": 1, "source_rel": "src/evil", "remote": "", "selector": "pinned",
        "resolved_commit": "", "adopted_at": 1.0, "txn_id": "", "strategy": "",
        "components": ["app"]}))
    assert source_registry.read_record(paths, "src/app") is None


def test_adopt_writes_registry_record(tmp_path, make_repo, installer):
    head = make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    action = inst.adopt_source(comp, source="dev")                      # local fallback, no remote
    assert action.status == "done"
    rec = _rec(inst)
    assert rec is not None
    assert rec.selector == "dev" and rec.resolved_commit == head
    assert rec.components == ("app",) and rec.txn_id                    # txn-bound record
    # journal is gone (transaction committed)
    assert not inst._journal_path(inst.paths.under("src", "app")).exists()


def test_shared_source_record_lists_all_consumers(tmp_path, make_repo, installer):
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    sibling = Component(id="app2", name="app2", kind=ComponentKind.SERVICE,
                        source=SourceSpec(path="src/app", local_dir="app"))
    inst = installer(comp, extra=(sibling,))
    assert inst.adopt_source(comp, source="dev").status == "done"
    assert set(_rec(inst).components) == {"app", "app2"}


def test_failed_adoption_writes_no_record(tmp_path, installer):
    comp = _comp()                                                      # no remote, no local
    inst = installer(comp)
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed"
    assert _rec(inst) is None


def test_pinned_adopt_records_pin_commit(tmp_path, make_repo, installer):
    head = make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp(pin=head)
    inst = installer(comp)
    assert inst.adopt_source(comp, source="pinned").status == "done"
    rec = _rec(inst)
    assert rec.selector == "pinned" and rec.resolved_commit == head


def _advance_local(git, tmp_path, text="v2\n"):
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text(text)
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    return git(tmp_path / "rt" / "local" / "app", "rev-parse", "HEAD")


def _update_with_unwritable_record(git, make_repo, installer, monkeypatch):
    """v1 adopted and recorded, the local advanced to v2, and the registry write failing for as
    long as `fail["on"]` — the record writer is the collaborator stubbed here, because no injected
    System can make a runtime-root file unwritable for the process that owns it."""
    make_repo(installer().paths.runtime_root / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"       # v1 active + recorded
    old = _rec(inst)
    _advance_local(git, inst.paths.runtime_root.parent)
    real_write, fail = Installer._write_registry_record, {"on": True}
    monkeypatch.setattr(Installer, "_write_registry_record",
                        lambda self, *a, **k: False if fail["on"] else real_write(self, *a, **k))
    return comp, inst, old, fail


def test_record_write_failure_on_update_rolls_back_in_process(git, make_repo, installer, monkeypatch):
    # A registry-write failure during an UPDATE must not leave the new tree active under
    # old metadata: the activation ROLLS BACK to the verified `.prev`, the prior record
    # (never touched) still matches, and the journal is cleared (proven rollback).
    comp, inst, old, _fail = _update_with_unwritable_record(git, make_repo, installer, monkeypatch)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "rolled back" in action.detail
    dest = inst.paths.under("src", "app")
    assert (dest / "file.txt").read_text() == "hello\n"                 # PRIOR tree restored
    assert _rec(inst) == old                                            # prior record intact
    assert not inst._journal_path(dest).exists()                        # journal cleared
    assert not dest.with_name(".app.prev").exists()                     # no .prev orphan


def test_a_rolled_back_update_leaves_the_source_operable(git, make_repo, installer, monkeypatch):
    # Once the write works again, the same update goes through: nothing of the rollback lingers.
    comp, inst, _old, fail = _update_with_unwritable_record(git, make_repo, installer, monkeypatch)
    assert inst.adopt_source(comp, force=True, source="dev").status == "failed"
    fail["on"] = False
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "done", action.detail
    assert (inst.paths.under("src", "app") / "file.txt").read_text() == "v2\n"


def test_record_write_failure_on_fresh_install_undoes_in_process(tmp_path, monkeypatch, make_repo, installer):
    # Fresh install + persistent record-write failure: the promoted candidate is removed —
    # no active source, no record, no journal, never a success.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    # the record writer is the collaborator stubbed: no injected System makes a runtime-root
    # file unwritable for the process that owns it
    monkeypatch.setattr(Installer, "_write_registry_record", lambda *a, **k: False)
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "rolled back" in action.detail
    dest = inst.paths.under("src", "app")
    assert not dest.exists()                                            # no active source
    assert _rec(inst) is None                                           # no record
    assert not inst._journal_path(dest).exists()                        # no journal


def _crash_state_after_activation(git, tmp_path, inst, had_prior: bool, text="v2\n"):
    """Craft the post-crash state of an activation whose record write never happened:
    dest = the NEW tree, `.prev` = the prior tree (update only), journal state `activated`
    with v3 meta (new HEAD + had_prior)."""
    dest = inst.paths.under("src", "app")
    new_head = _advance_local(git, tmp_path, text)
    if had_prior:
        dest.rename(dest.with_name(".app.prev"))                        # archive the prior
    else:
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(tmp_path / "rt" / "local" / "app", dest, symlinks=True)    # the NEW tree at dest
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    staging = dest.with_name(".app.candidate-1-2")
    cand_rel = rel(staging)

    def ident(q):
        try:
            st = os.stat(q, follow_symlinks=False)
            return [st.st_dev, st.st_ino, st.st_ctime_ns]   # v5 ctime-hardened ident
        except OSError:
            return None
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "dev", "resolved_commit": new_head, "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": had_prior},
        "idents": {"candidate": ident(dest),           # dest IS the promoted candidate
                   "prev": ident(dest.with_name(".app.prev"))}}))
    return dest, new_head


def test_recovery_restores_prior_when_record_still_unwritable(tmp_path, monkeypatch, git, make_repo, installer):
    # CRASH between activation and record write, and the record STILL cannot persist during
    # recovery (one retry): recovery rolls back to `.prev`; the prior record still matches.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"       # v1 active + recorded
    old = _rec(inst)
    dest, _ = _crash_state_after_activation(git, tmp_path, inst, had_prior=True)
    monkeypatch.setattr(Installer, "_write_registry_record",       # still unwritable (see above)
                        lambda *a, **k: False)
    msgs = inst.recover_source_activations()
    assert any("rolled back" in m for m in msgs)
    assert (dest / "file.txt").read_text() == "hello\n"                 # prior tree restored
    assert _rec(inst) == old                                            # prior record intact
    assert not inst._journal_path(dest).exists()                        # journal cleared


def test_recovery_completes_the_record_when_it_can_be_written(tmp_path, git, make_repo, installer):
    # The same crash with the record write WORKING: recovery completes the record (normal path).
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest, new_head = _crash_state_after_activation(git, tmp_path, inst, had_prior=True)
    msgs = inst.recover_source_activations()
    assert any("recovered" in m for m in msgs)
    assert _rec(inst).resolved_commit == new_head                       # record completed
    assert not inst._journal_path(dest).exists()


def test_recovery_undoes_fresh_install_when_record_still_unwritable(tmp_path, monkeypatch, git, make_repo, installer):
    # CRASH after a FRESH install's activation; record write keeps failing: recovery removes
    # the tree — no active source, no record, no falsely successful state.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    # simulate: the record from the first install never existed (fresh-install crash)
    source_registry.remove_record(inst.paths, "src/app")
    dest, _ = _crash_state_after_activation(git, tmp_path, inst, had_prior=False)
    monkeypatch.setattr(Installer, "_write_registry_record",       # still unwritable (see above)
                        lambda *a, **k: False)
    msgs = inst.recover_source_activations()
    assert any("rolled back fresh install" in m for m in msgs)
    assert not dest.exists()                                            # no active source
    assert _rec(inst) is None                                           # no record
    assert not inst._journal_path(dest).exists()                        # no journal


def test_recovery_of_rolled_back_state_writes_no_record(tmp_path, make_repo, installer):
    # dest holds the (restored) PRIOR tree; a retained v3 journal claims a DIFFERENT commit.
    # Recovery must clear the journal WITHOUT re-registering the prior under the new metadata.
    head = make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    staging = dest.with_name(".app.candidate-1-2")
    cand_rel = rel(staging)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "stable", "resolved_commit": "f" * 40,
                 "remote": "", "strategy": "", "components": ["app"], "had_prior": True},
        "idents": {"candidate": None, "prev": None}}))
    msgs = inst.recover_source_activations()
    assert any("active source intact" in m for m in msgs)
    assert not inst._journal_path(dest).exists()                        # journal cleared
    rec = _rec(inst)
    assert rec.resolved_commit == head and rec.selector == "dev"        # prior record UNTOUCHED


def test_v3_journal_with_invalid_meta_is_retained(tmp_path, make_repo, installer):
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    cand_rel = rel(dest.with_name(".app.candidate-1-2"))
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "evil", "resolved_commit": 5},             # invalid meta
        "idents": {"candidate": None, "prev": None}}))
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "invalid" in m for m in msgs)
    assert inst._journal_path(dest).exists()                            # retained, blocks


def test_v2_journal_recovery_is_generation_blocked(tmp_path, make_repo, installer):
    # Legacy v2 journal (no identity evidence): automatic recovery REFUSES — nothing is
    # promoted, restored, or cleaned; the journal is retained with an operator diagnostic,
    # and further source mutation stays blocked.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    dest = inst.paths.under("src", "app")
    dest.mkdir(parents=True)
    (dest / "marker").write_text("LIVE")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    cand_rel = rel(dest.with_name(".app.candidate-1-2"))
    d = inst.paths.under("state", "source-txn")
    d.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 2, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel)}))
    msgs = inst.recover_source_activations()
    assert any("generation" in m and "recovery-required" in m for m in msgs)
    assert (dest / "marker").read_text() == "LIVE"                      # nothing touched
    assert inst._journal_path(dest).exists()                            # journal retained
    assert _rec(inst) is None                                           # no fabricated ownership
    blocked = inst.adopt_source(comp, force=True, source="dev")
    assert blocked.status == "failed" and "recovery-required" in blocked.detail


def _svc_bits(installer, remote):
    comp = _comp(remote=remote)
    inst = installer(comp)
    dest = inst.paths.under("src", "app")
    return comp, inst, dest


def test_absent_record_refuses_destructive_authorization(tmp_path, git, make_repo, installer):
    # A tree with no ownership record is not LHPC's, however well its origin matches the
    # configured remote: refused, nothing registered, nothing touched, no git run.
    comp, inst, dest = _svc_bits(installer, "https://github.com/x/y.git")
    make_repo(dest)
    git(dest, "remote", "add", "origin", "https://github.com/x/y.git")
    rec, why = source_registry.verify_identity(inst.paths, inst.system, inst.config,
                                               comp, dest, components=("app",))
    assert rec is None and "no ownership record" in why
    assert _rec(inst) is None                                           # nothing persisted
    assert (dest / ".git").exists()


def test_symlink_at_source_destination_is_refused(tmp_path, make_repo, installer):
    # CONTAINMENT: a managed source is a DIRECTORY under the runtime root. A symlink at its
    # destination is never an LHPC adoption: refused (nothing registered) without a record,
    # and refused as identity drift when a record for a managed directory exists. The
    # symlink and its target are never touched.
    comp, inst, dest = _svc_bits(installer, "https://github.com/x/y.git")
    external = tmp_path / "external"
    make_repo(external)
    dest.parent.mkdir(parents=True)
    os.symlink(str(external), dest)
    rec, why = source_registry.verify_identity(inst.paths, inst.system, inst.config,
                                               comp, dest)
    assert rec is None and "no ownership record" in why
    assert source_registry.read_record(inst.paths, "src/app") is None      # nothing registered
    assert source_registry.write_record(inst.paths, source_registry.RegistryRecord(
        "src/app", "https://github.com/x/y.git", "pinned", "", time.time(), "", ("app",)))
    rec2, why2 = source_registry.verify_identity(inst.paths, inst.system, inst.config,
                                                 comp, dest)
    assert rec2 is None and "identity drift" in why2 and "symlink" in why2
    assert dest.is_symlink() and os.readlink(dest) == str(external)         # untouched


def _tracked_edit(git, dest):
    (dest / "file.txt").write_text("edited\n")


def _untracked_file(git, dest):
    (dest / "notes.txt").write_text("operator notes")


def _regenerable_artifacts(git, dest):
    # ignore-dir names + the declared built binary
    (dest / "build").mkdir()
    (dest / "build" / "obj.o").write_text("obj")
    (dest / "__pycache__").mkdir()
    (dest / "__pycache__" / "m.pyc").write_text("pyc")
    (dest / "out").mkdir()
    (dest / "out" / "app.bin").write_text("ELF")                        # declared comp.bin


def _gitignored_file(git, dest):
    # untracked-files=normal honours .gitignore
    (dest / ".gitignore").write_text("*.log\n")
    git(dest, "add", ".gitignore"); git(dest, "commit", "-qm", "ignore")
    (dest / "run.log").write_text("log")


@pytest.mark.parametrize("change, dirty_as, named", [
    pytest.param(_tracked_edit, "tracked", "file.txt", id="tracked-edit"),
    pytest.param(_untracked_file, "untracked", "notes.txt", id="untracked-file"),
    pytest.param(_regenerable_artifacts, None, None, id="regenerable-artifacts"),
    pytest.param(_gitignored_file, None, None, id="gitignored-file"),
])
def test_dirty_report_names_edits_and_additions_but_not_artifacts(tmp_path, git, make_repo, installer,
                                                                   change, dirty_as, named):
    # A tracked modification and a plain untracked file are dirty (never silently discarded);
    # regenerable artifacts and .gitignore'd files are not.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE, bin="out/app.bin",
                     source=SourceSpec(path="src/app", local_dir="app"))
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    assert not inst.dirty_report(dest, "src/app")                       # clean after adopt
    change(git, dest)
    rep = inst.dirty_report(dest, "src/app")
    if dirty_as is None:
        assert not rep
    else:
        assert rep and any(named in p for p in getattr(rep, dirty_as))


@pytest.mark.contract
def test_update_preserves_an_added_file(tmp_path, git, make_repo, installer):
    """A stack that RUNS from its checkout writes into it (logs, generated settings). Those
    files are the operator's, not upstream's: an update replaces the tree around them and
    carries them across, byte-identical, instead of refusing."""
    repo = tmp_path / "rt" / "local" / "app"
    make_repo(repo)
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "precious.txt").write_text("operator work")                 # untracked, non-ignored
    (repo / "file.txt").write_text("v2\n")                              # something to update TO
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v2")

    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status != "failed", action.detail
    assert (dest / "file.txt").read_text() == "v2\n"                    # new upstream IS active
    assert (dest / "precious.txt").read_text() == "operator work"       # ...and the file survived


@pytest.mark.contract
@pytest.mark.parametrize("case", ["modified", "deleted", "staged", "git-added"])
def test_update_refuses_a_changed_upstream_source(tmp_path, case, git, make_repo, installer):
    """The other half of the rule: a change to the SOURCE ITSELF still refuses, and still names
    it — LHPC will not guess how to merge an operator's edit into a new version. `git add` puts
    even a brand-new file in that class: the checkout then differs from upstream by more than an
    addition an update could carry."""
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    if case == "modified":
        (dest / "file.txt").write_text("operator edited upstream\n")
    elif case == "deleted":
        (dest / "file.txt").unlink()
    elif case == "staged":
        (dest / "file.txt").write_text("edited\n")
        git(dest, "add", "file.txt")
    else:
        (dest / "notes.txt").write_text("mine")
        git(dest, "add", "notes.txt")

    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "local modifications" in action.detail
    assert ("notes.txt" if case == "git-added" else "file.txt") in action.detail   # itemized

    # the operator's change is exactly as they left it, and the refusal came before any staging
    if case == "modified":
        assert (dest / "file.txt").read_text() == "operator edited upstream\n"
    elif case == "deleted":
        assert not (dest / "file.txt").exists()
    elif case == "staged":
        assert (dest / "file.txt").read_text() == "edited\n"
        assert "file.txt" in git(dest, "diff", "--cached", "--name-only")
    else:
        assert (dest / "notes.txt").read_text() == "mine"
        assert "notes.txt" in git(dest, "diff", "--cached", "--name-only")
    assert not dest.with_name(".app.prev").exists()
    assert not inst._journal_path(dest).exists()


def _tagged_repo(git, make_repo, path: Path):
    """A repo with: version tags v0.9.0 < v1.2.0 (v1.2.0 on an OLDER commit than a
    non-version tag 'nightly' that is NEWEST by date) + a final untagged commit."""
    make_repo(path)
    git(path, "tag", "v0.9.0")
    (path / "file.txt").write_text("two\n")
    git(path, "add", "-A"); git(path, "commit", "-qm", "two")
    git(path, "tag", "v1.2.0")
    (path / "file.txt").write_text("three\n")
    git(path, "add", "-A"); git(path, "commit", "-qm", "three")
    git(path, "tag", "nightly")                       # newest by date, NOT version-shaped
    (path / "file.txt").write_text("four\n")
    git(path, "add", "-A"); git(path, "commit", "-qm", "four")


def test_stable_resolves_newest_version_tag(tmp_path, git, make_repo, installer):
    _tagged_repo(git, make_repo, tmp_path / "repo")
    inst = installer(_comp())
    tag = inst._resolve_stable_tag(str(tmp_path / "repo"))
    assert tag == "v1.2.0"                             # version tag beats newer-dated 'nightly'


@pytest.mark.parametrize("tags", [["alpha", "beta"], []], ids=["only-non-version-tags", "no-tags"])
def test_stable_ignores_non_version_tags_and_stays_on_head(tmp_path, git, make_repo, installer, tags):
    # only NON-version tags, or no tags at all -> "" (the caller stays on the default-branch HEAD).
    # A build-suffixed or code-named tag is a snapshot, not a release, and the remote freeze path
    # cannot see tag dates at all, so BOTH paths ignore it rather than disagreeing about it.
    repo = tmp_path / "r1"
    make_repo(repo)
    for n, tag in enumerate(tags):
        if n:
            # a DISTINCT, later committer date so `-creatordate` ordering is deterministic
            (repo / "file.txt").write_text("2\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "2", env={"GIT_COMMITTER_DATE": "2030-01-01T00:00:00",
                                                 "GIT_AUTHOR_DATE": "2030-01-01T00:00:00"})
        git(repo, "tag", tag)
    assert installer(_comp())._resolve_stable_tag(str(repo)) == ""


@pytest.mark.parametrize("name, shapes, expected", [
    # (upstream, its tag shapes, the release the rule must pick — "" means default-branch HEAD)
    ("loraham-daemon", ["v0.4.0", "v112"], "v112"),         # a bare numeric version IS a version
    ("kiss-tnc", ["v0.5.1"], "v0.5.1"),
    ("reticulum", ["1.5.2", "1.8.2-pre"], "1.5.2"),         # a prerelease is not a release
    ("meshcore-cli", ["v1.6.3", "v1.6.2"], "v1.6.3"),
    # build-suffixed snapshots are NOT releases: no version tag -> default-branch HEAD
    ("meshtastic", ["v2.7.26.54e0d8d", "v2.8.0.7239fe8"], ""),
    ("meshcom-firmware", ["v4.35p.08.29", "v4.35s"], ""),
    ("no-tags-at-all", [], ""),
], ids=lambda v: v if isinstance(v, str) and not v.startswith("v") and "." not in v else None)
def test_both_stable_paths_agree_on_the_real_manifest_tag_shapes(tmp_path, git, make_repo, installer,
                                                                  name, shapes, expected):
    """One selector, one commit, whichever PRODUCTION path the operator reaches it through.

    `lhpc install --source stable` resolves in a full clone (Installer._resolve_stable_tag);
    `lhpc auto-install --source stable` resolves remotely (ControllerService._frozen_ref over
    `git ls-remote --tags`). They once carried different regexes AND different fallbacks, so the
    same word installed different commits of one component. This drives BOTH production
    functions — not the shared helper — over the tag shapes each pinned upstream actually
    publishes, and asserts they land on the same commit.
    """
    # LOCAL path: a real repo carrying those tags.
    repo = tmp_path / "up"
    make_repo(repo)
    for tag in shapes:
        git(repo, "tag", tag)
    local_tag = installer(_comp())._resolve_stable_tag(str(repo))
    assert local_tag == expected, f"{name}: local picked {local_tag!r}, expected {expected!r}"

    # REMOTE path: the same tag names over a faked `ls-remote`, each ANNOTATED so the
    # peeled commit is what must be selected (the plain tag object sha must not be).
    head_sha = "9" * 40
    lines, peel = [], {}
    for i, tag in enumerate(shapes):
        tag_obj, peeled = f"{i:040x}", f"{i:039x}f"
        peel[tag] = peeled
        lines.append(f"{tag_obj}\trefs/tags/{tag}\n{peeled}\trefs/tags/{tag}^{{}}\n")
    svc = _svc(tmp_path / "svc", {
        ("git", "ls-remote", "--tags", DAEMON_REMOTE): CR(0, "".join(lines), ""),
        ("git", "ls-remote", DAEMON_REMOTE, "HEAD"): CR(0, f"{head_sha}\tHEAD\n", "")})
    comp_remote = svc.stack("daemon").component("loraham-daemon")
    (fz, why) = svc._frozen_ref(comp_remote, "stable")
    assert why == "", f"{name}: frozen resolution failed: {why}"
    remote_sha = fz[0]
    expected_remote = peel[expected] if expected else head_sha
    assert remote_sha == expected_remote, (
        f"{name}: remote picked {remote_sha!r}, expected {expected_remote!r}")

    # the two production paths agree on WHICH tag (their shas differ only because the
    # fixtures are different repositories)
    picked_remote = next((t for t, sha in peel.items() if sha == remote_sha), "")
    assert picked_remote == local_tag, (
        f"{name}: stable diverges — local {local_tag!r} vs remote {picked_remote!r}")


@pytest.mark.parametrize("sel", ["pinned", "dev", "stable"])
def test_artifact_source_same_for_every_selector(tmp_path, make_repo, installer, sel):
    # An artifact source adopts the SAME declared artifact for pinned/dev/stable — including
    # `pinned` with NO configured pin (no unverified-blocked for artifacts).
    head = make_repo(tmp_path / "rt" / "local" / "app")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app", artifact=True))
    inst = installer(comp)
    action = inst.adopt_source(comp, source=sel)
    assert action.status == "done", f"{sel}: {action.detail}"
    assert action.provenance == "artifact-head"
    assert _rec(inst).resolved_commit == head      # identical resolution


def test_dev_unavailable_branch_is_typed(tmp_path, make_repo, installer):
    # dev with a configured branch the local fallback is NOT on: the SELECTOR is unavailable —
    # never a silent adoption of a different ref.
    make_repo(tmp_path / "rt" / "local" / "app")             # on master/main, not 'feature/x'
    comp = _comp(branch="feature/x")
    inst = installer(comp)
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed"
    assert "selector unavailable" in action.detail and "feature/x" in action.detail
    assert _rec(inst) is None


def test_update_refuses_unknown_non_git_tree(tmp_path, make_repo, installer):
    # An existing CLEAN tree that is not a git checkout (and unregistered) is unknown —
    # update refuses and changes nothing.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    dest = inst.paths.under("src", "app")
    dest.mkdir(parents=True)
    (dest / "data.txt").write_text("operator data")
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "ownership/identity not proven" in action.detail
    assert (dest / "data.txt").read_text() == "operator data"           # tree unchanged


def test_update_refuses_wrong_origin(tmp_path, git, make_repo, installer):
    # An existing clean git tree whose origin differs from the configured remote is not
    # LHPC's adoption — update refuses, tree unchanged.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp(remote="https://github.com/x/y.git")
    inst = installer(comp)
    dest = inst.paths.under("src", "app")
    make_repo(dest)
    git(dest, "remote", "add", "origin", "https://github.com/OTHER/z.git")
    before = git(dest, "rev-parse", "HEAD")
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "ownership/identity not proven" in action.detail
    assert git(dest, "rev-parse", "HEAD") == before                    # tree unchanged


def test_update_refuses_registered_source_at_drifted_commit(tmp_path, git, make_repo, installer):
    # A registered source manually moved to a different CLEAN commit: update refuses.
    head1 = make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "file.txt").write_text("moved\n")
    git(dest, "add", "-A"); git(dest, "commit", "-qm", "moved")       # clean, NEW commit
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "identity drift" in action.detail
    assert git(dest, "rev-parse", "HEAD") != head1                     # tree left as found


@pytest.mark.parametrize("plant", [
    pytest.param(lambda d: os.symlink("does-not-exist", d), id="dangling-symlink"),
    pytest.param(lambda d: d.write_text("a file"), id="regular-file"),
    pytest.param(os.mkfifo, id="special"),
])
@pytest.mark.parametrize("force", [False, True], ids=["install", "update"])
def test_install_and_update_refuse_hostile_destination_leaves(tmp_path, make_repo, installer, plant, force):
    # A dangling symlink, a regular file, or a special leaf at the destination is NOT an
    # installable empty destination: refuse with ZERO rename/cleanup/deletion.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    dest = inst.paths.under("src", "app")
    dest.parent.mkdir(parents=True)
    plant(dest)
    action = inst.adopt_source(comp, force=force, source="dev")
    assert action.status == "failed", action.detail
    assert "refusing" in action.detail
    assert os.path.lexists(dest)                                        # leaf untouched
    assert not dest.with_name(".app.prev").exists()                     # zero rename
    assert _rec(inst) is None


def _mk_unsafe_registry(paths, rel, shape):
    rp = source_registry.record_path(paths, rel)
    rp.parent.mkdir(parents=True, exist_ok=True)
    if shape == "malformed":
        rp.write_text("not json {{{")
    elif shape == "symlinked":
        (rp.parent / "real.json").write_text("{}")
        os.symlink("real.json", rp)
    elif shape == "dangling":
        os.symlink("does-not-exist", rp)
    elif shape == "directory":
        rp.mkdir()
    elif shape == "special":
        os.mkfifo(rp)
    elif shape == "inaccessible":
        rp.write_text("{}")
        rp.chmod(0)
    return rp


@pytest.mark.parametrize("shape", [
    "malformed", "symlinked", "dangling", "directory", "special",
    pytest.param("inaccessible", marks=pytest.mark.needs_nonroot),   # chmod 0 does not bind for root
])
def test_unsafe_registry_states_block_everything(tmp_path, make_repo, installer, shape):
    # Every PRESENT-but-unsafe registry state blocks update/adopt-over-existing, and the
    # tri-state reader reports it distinctly ("unsafe", never "absent").
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"    # genuine install
    source_registry.remove_record(inst.paths, "src/app")
    _mk_unsafe_registry(inst.paths, "src/app", shape)
    state, rec, why = source_registry.record_state(inst.paths, "src/app")
    assert state == "unsafe" and rec is None and why
    action = inst.adopt_source(comp, force=True, source="dev")       # update blocked
    assert action.status == "failed"
    assert "unsafe" in action.detail or "malformed" in action.detail \
        or "unreadable" in action.detail or "validation" in action.detail
    dest = inst.paths.under("src", "app")
    assert (dest / "file.txt").exists()                              # zero source mutation


@pytest.mark.parametrize("op", ["uninstall", "clean"])
def test_unsafe_registry_blocks_uninstall_and_clean(tmp_path, op):
    paths = Paths(runtime_root=tmp_path)
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    dest.mkdir(parents=True)
    _mk_unsafe_registry(paths, "src/loraham-kiss-tnc", "malformed")
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    res = (svc.uninstall("kiss", apply=True) if op == "uninstall"
           else svc.clean("kiss", apply=True, purge=True))
    assert not res.ok and any("malformed" in d or "unsafe" in d for d in res.details)
    assert dest.exists()


def test_unsafe_registry_blocks_known_working_confirmation(tmp_path):
    from lhpc.core import known_working
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    _mk_unsafe_registry(paths, "src/LoRaHAM_Daemon", "malformed")
    entries = {"loraham-chat": {"commit": "a" * 40, "selector": "dev", "remote": "",
                                "source_rel": "src/LoRaHAM_Daemon", "strategy": ""}}
    assert known_working.write_candidate(paths, "chat", entries, "433")
    svc = ControllerService(system=FakeSystem(cmdlines_data={5: ["loraham_chat"]}).system,
                            paths=paths)
    assert not svc.confirm_known_working("chat").ok
    assert known_working.load(paths, "chat") == []


def test_a_path_never_touched_reads_absent_not_unsafe(tmp_path):
    # SAFELY ABSENT (as opposed to unsafe) is still reported for a path never touched
    state, rec, _why = source_registry.record_state(Paths(runtime_root=tmp_path), "src/never-touched")
    assert state == "absent" and rec is None


def test_non_git_directory_is_never_destructively_authorized(tmp_path, installer):
    # A registered path occupied by a clean NON-git directory with nothing provable
    # (no commit, no origin) is NOT ownership — refuse destructive authorization.
    comp, inst, dest = _svc_bits(installer, "")
    dest.mkdir(parents=True)
    (dest / "replaced.txt").write_text("manually placed")
    assert source_registry.write_record(inst.paths, source_registry.RegistryRecord(
        "src/app", "", "pinned", "", time.time(), "", ("app",)))
    rec, why = source_registry.verify_identity(inst.paths, inst.system, inst.config,
                                               comp, dest)
    assert rec is None and "unprovable" in why
    assert (dest / "replaced.txt").exists()                          # never deleted


def test_dirty_carveout_is_exact_leaf_only(tmp_path, make_repo, installer):
    # Only the EXACT declared generated binary is ignorable; sibling/nested/unusual
    # untracked files — including newline-containing names — block. NUL-safe parsing.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE, bin="out/app.bin",
                     source=SourceSpec(path="src/app", local_dir="app"))
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "out").mkdir()
    (dest / "out" / "app.bin").write_text("ELF")
    assert not inst.dirty_report(dest, "src/app")                    # exact leaf allowed
    # a SIBLING under the binary's parent blocks (the dir is not ignorable wholesale)
    (dest / "out" / "notes.txt").write_text("user data")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any("notes.txt" in p for p in rep.untracked)
    (dest / "out" / "notes.txt").unlink()
    # a NESTED file under the parent blocks too
    (dest / "out" / "deep").mkdir()
    (dest / "out" / "deep" / "x").write_text("x")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any("deep/x" in p for p in rep.untracked)
    shutil.rmtree(dest / "out" / "deep")
    # newline/quote names parse EXACTLY (NUL-safe) and block
    weird = dest / 'we"ird\nname.txt'
    weird.write_text("x")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any(p == 'we"ird\nname.txt' for p in rep.untracked)
    weird.unlink()
    assert not inst.dirty_report(dest, "src/app")                    # clean again


def test_a_final_0_2_10_record_and_journal_still_read_after_strategy_removal(tmp_path, installer):
    """0.2.10 wrote a `strategy` field into both the ownership record and the transaction
    journal's meta. The field no longer exists; a record or journal that still carries it must
    read as an ordinary unknown extra, so a clean or interrupted 0.2.10 install stays usable."""
    paths = Paths(runtime_root=tmp_path)
    rel = "src/app"
    rp = source_registry.record_path(paths, rel)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps({
        "version": 2, "source_rel": rel, "remote": "https://example.invalid/app.git",
        "selector": "pinned", "resolved_commit": "a" * 40, "adopted_at": 1700000000.0,
        "txn_id": "t" * 64, "strategy": "adopt", "components": ["app"],   # 0.2.10 shape
    }))
    state, rec, reason = source_registry.record_state(paths, rel)
    assert state == "valid", reason
    assert rec.selector == "pinned" and rec.components == ("app",)
    assert not hasattr(rec, "strategy")               # read, ignored, never resurrected

    inst = installer(_comp("src/app"), root=tmp_path)
    assert inst._valid_meta({"selector": "pinned", "resolved_commit": "a" * 40, "remote": "",
                             "strategy": "copy", "components": ["app"], "had_prior": True})


def test_unknown_selector_is_unsafe(tmp_path):
    # Only the four selectors current writers emit are valid; anything else (incl. a value an
    # old release wrote) reads as "unsafe", so the operator resolves the record by hand.
    paths = Paths(runtime_root=tmp_path)
    rel = "src/app"
    rp = source_registry.record_path(paths, rel)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps({
        "version": 2, "source_rel": rel, "remote": "https://example.invalid/app.git",
        "selector": "legacy", "resolved_commit": "", "adopted_at": 1700000000.0,
        "txn_id": "", "strategy": "adopt", "components": ["app"],
    }))
    state, rec, reason = source_registry.record_state(paths, rel)
    assert state == "unsafe" and rec is None and "strict validation" in reason


# --- selectors and remotes: what may reach git ------------------------------------------------------
def test_invalid_source_selector_rejected_not_dev(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ln, admission, reason = svc.spawn_web_job("install", "daemon", source="evil")
    assert ln is None and admission == "blocked" and "invalid source" in reason


def test_malformed_remote_never_reaches_git(tmp_path, installer):
    fake = FakeSystem()
    inst = installer(system=fake.system)
    spec = SourceSpec(path="src/x", remote="--upload-pack=evil")
    ok = inst._clone(spec, tmp_path / "dest", "dev", remote="--upload-pack=evil")
    assert ok is False
    assert not any("clone" in c for c in fake.calls)        # git clone NEVER invoked


def test_valid_remote_reaches_git(tmp_path, installer):
    fake = FakeSystem()
    inst = installer(system=fake.system)
    spec = SourceSpec(path="src/x", remote="https://github.com/x/y.git", branch="main")
    inst._clone(spec, tmp_path / "dest", "dev", remote="https://github.com/x/y.git")
    assert any("clone" in c for c in fake.calls)            # a valid remote does clone


def test_post_clone_failure_names_the_step_and_reason_in_the_log(tmp_path, installer):
    """A clone that SUCCEEDS and a later git step that times out must not read as a network
    fault: the caller can only say "clone failed", so the step and the reason belong in the
    adoption log (live-found — a switch failed right after "Resolving deltas: 100%")."""
    class _Runner:
        # a pattern stub rather than a FakeSystem table: the clone lands in a controller-pinned
        # /proc/<pid>/fd path, so the exact argv of the later steps is not knowable up front
        def run(self, argv, timeout=None, cwd=None, env=None):
            if "checkout" in argv:
                return CommandResult(124, "", "fatal: interrupted", timed_out=True)
            return CommandResult(0, "", "")
    log = tmp_path / "adopt.log"
    dest = tmp_path / "dest"
    dest.mkdir()
    fake = FakeSystem()
    inst = installer(system=System(runner=_Runner(), procfs=fake, fs=fake, unix=fake))
    with log.open("w") as fh:
        ok = inst._clone(SourceSpec(path="src/x", remote="https://github.com/x/y.git"),
                         dest, "pinned", remote="https://github.com/x/y.git",
                         expected_pin="a" * 40, log_fh=fh)
    assert ok is False
    body = log.read_text()
    assert "[fail] checkout aaaaaaaaaaaa" in body
    assert "timed out after 300s" in body
    assert "fatal: interrupted" in body


def test_run_action_rejects_invalid_source(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r = svc.run_action("install", "daemon", source="evil")
    assert not r.ok and "Invalid source" in r.summary          # never rewritten to 'dev'


def test_run_action_without_a_source_installs_the_pinned_selector(tmp_path):
    """Omitting `source` means `pinned`: the clone is the full one the pin is checked out from,
    not the shallow branch-tracking clone `dev` makes."""
    def clones(**kw):
        fake = FakeSystem()
        root = tmp_path / kw.get("source", "default")
        root.mkdir()
        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
        svc.run_action("install", "daemon", apply=True, **kw)
        return [c[:-1] for c in fake.calls if c[:2] == ["git", "clone"]]   # minus the fd-pinned dest
    default = clones()
    assert default and default == clones(source="pinned")
    assert default != clones(source="dev")


def test_update_status_malformed_remote_never_reaches_git(tmp_path, recording_system):
    sys, calls = recording_system
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    (tmp_path / "src" / "x").mkdir(parents=True)                # installed source dir
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('[remotes]\nx = "--upload-pack=evil"\n')
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/x", remote="https://github.com/a/b.git"))
    assert svc.update_status(comp) == "unknown"                 # blocked, no check
    assert not any("ls-remote" in c for c in calls)             # git ls-remote NEVER invoked


# --- the activation transaction: journal, crash recovery, and what recovery may never touch ------
# `installer(search_root=tmp_path / "rt")` declares src/app as a MANAGED source, so recovery
# accepts its journal: recovery only ever operates on manifest-declared managed-source
# destinations.


def _ident_of(p, *, ctime=True):
    try:
        st = os.stat(p, follow_symlinks=False)
    except OSError:
        return None
    # v5 idents carry ctime_ns; ctime=False yields the legacy v4 [dev, ino] shape.
    return [st.st_dev, st.st_ino, st.st_ctime_ns] if ctime else [st.st_dev, st.st_ino]


def _journal(inst, dest, prev, staging, state, version=5):
    # v5 journal (default) with LOGICAL runtime-relative names + ctime-hardened leaf-identity
    # evidence computed from the on-disk leaves the test just created. version=4 emits the legacy
    # [dev, ino]-only idents (now retained-as-unprovable); v2/v3 carry no idents (generation-blocked).
    d = inst.paths.under("state", "source-txn")
    d.mkdir(parents=True, exist_ok=True)
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    cand_rel = rel(staging)
    payload = {
        "version": version, "state": state, "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel)}
    if version in (4, 5):
        ct = version == 5
        payload["meta"] = {"selector": "pinned", "resolved_commit": "", "remote": "",
                           "strategy": "", "components": [dest.name], "had_prior": True}
        payload["idents"] = {"candidate": (_ident_of(staging, ctime=ct)
                                           or _ident_of(dest, ctime=ct)),
                             "prev": _ident_of(prev, ctime=ct)}
    inst._journal_path(dest).write_text(json.dumps(payload))


def _fin(inst, dest, prev, staging):
    """Open the journal as an OwnedMarker and drive _finish_or_rollback (recovery API),
    supplying v4-style leaf-identity evidence computed from the on-disk leaves."""
    from lhpc.core import runtime_fs
    jf = inst._journal_path(dest); jf.parent.mkdir(parents=True, exist_ok=True)
    if not jf.exists():
        jf.write_text("{}")
    m = runtime_fs.open_existing_marker(inst.paths, jf)
    try:
        return inst._finish_or_rollback(
            dest, prev, staging, m,
            meta=_META, txn_id="",
            # A real journal's candidate ident is refreshed by the promotion rename, so for an
            # already-activated tree it describes DEST. Falling back to it keeps these
            # hand-built states coherent with what recovery is entitled to assume.
            idents={"candidate": _ident_of(staging) or _ident_of(dest),
                    "prev": _ident_of(prev)})
    finally:
        m.close()


def _fail_noreplace(monkeypatch, suffixes=(".app.candidate-1-2", ".app.prev"),
                    plant_dangling=False):
    """Make the ATOMIC promotion primitive (`source_fs._rename_noreplace_at`, the one rename
    the activation uses) fail for the named leaves — the one seam through which a rename can
    fail part-way through a transaction, which no injected System can produce."""
    real = source_fs._rename_noreplace_at
    def failing(parent_fd, old, new):
        if any(old.endswith(sfx) for sfx in suffixes):
            if plant_dangling and old.endswith(".app.candidate-1-2"):
                os.symlink("gone", new, dir_fd=parent_fd)    # race: dangling symlink at dest
            raise OSError("simulated rename failure")
        return real(parent_fd, old, new)
    monkeypatch.setattr(source_fs, "_rename_noreplace_at", failing)


def test_recover_rolls_back_after_prior_archived(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    prev = src / ".app.prev"
    prev.mkdir(); (prev / "marker").write_text("PRIOR")     # active was archived, dest gone
    _journal(inst, dest, prev, src / ".app.candidate-1-2", "prior-archived")
    msgs = inst.recover_source_activations()
    assert dest.is_dir() and (dest / "marker").read_text() == "PRIOR"   # restored
    assert any("rolled back" in m for m in msgs)


def test_recover_completes_activation(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"
    staging.mkdir(); (staging / "marker").write_text("NEW")  # died before staging->dest
    _journal(inst, dest, src / ".app.prev", staging, "prior-archived")
    inst.recover_source_activations()
    assert dest.is_dir() and (dest / "marker").read_text() == "NEW"


def test_recover_leaves_active_intact(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "marker").write_text("LIVE")
    prev = src / ".app.prev"; prev.mkdir()
    _journal(inst, dest, prev, src / ".app.candidate-1-2", "prior-archived")
    inst.recover_source_activations()
    assert (dest / "marker").read_text() == "LIVE" and not prev.exists()  # prior cleaned


def test_recover_refuses_escaping_journal_path(tmp_path, installer):
    # A journal whose source_rel escapes the runtime root must be retained + blocked,
    # and never touch the outside path.
    inst = installer(search_root=tmp_path / "rt")
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived",
        "source_rel": "../outside/app", "prev_rel": "../outside", "candidate_rel": "../outside"}))
    msgs = inst.recover_source_activations()
    assert (outside / "keep").read_text() == "KEEP"
    assert any("invalid activation journal" in m for m in msgs)
    assert (d / "app.json").exists()                     # journal retained


def test_recover_refuses_non_controller_candidate_name(tmp_path, installer):
    # Even a contained journal is rejected if the candidate/prior names don't match the
    # controller's transaction naming (so an attacker can't point recovery at a victim).
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    (src / "victim").mkdir(); (src / "victim" / "x").write_text("V")
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    # Identity-bound filename for src/app (so it passes the filename check and REACHES the
    # non-controller candidate/prior name refusal — the point of this test).
    inst._journal_path(src / "app").write_text(json.dumps({
        "version": 2, "state": "prior-archived",
        "source_rel": "src/app", "prev_rel": "src/victim", "candidate_rel": "src/victim"}))
    msgs = inst.recover_source_activations()
    assert (src / "victim" / "x").read_text() == "V"     # victim untouched
    assert any("non-controller" in m for m in msgs)
    assert inst._journal_path(src / "app").exists()      # journal retained (evidence)


def test_shared_source_serializes_on_one_lock(tmp_path, installer):
    # kiss-tnc + kiss-serial share src/loraham-kiss-tnc; a held lock on that source path blocks
    # an update of EITHER consumer.
    from lhpc.core import reslock
    inst = installer(search_root=tmp_path / "rt")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    inst.paths.under("src", "app").mkdir(parents=True)               # overwrite target
    with reslock.operation_lock(inst.paths, inst._source_lock_key("src/app"), "update", "x"):
        action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "in progress" in action.detail


def test_fallback_pin_mismatch_blocks_activation(tmp_path, make_repo, installer):
    inst = installer(search_root=tmp_path / "rt")
    make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src",
                                       pin_commit="deadbeef" * 5))   # wrong pin
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed" and "does not satisfy" in action.detail
    assert not inst.paths.under("src", "app").exists()              # active source untouched


def test_fallback_pin_match_activates(tmp_path, make_repo, installer):
    inst = installer(search_root=tmp_path / "rt")
    head = make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done" and inst.paths.under("src", "app").is_dir()


def test_build_blocked_by_held_source_lock(tmp_path):
    from lhpc.core import reslock
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    # The stack must be INSTALLED, or `build()` refuses as not-installed before it ever
    # contends for the lock — and the lock contention is what this test is about.
    for c in svc.stack("daemon").components:
        if c.source:
            (tmp_path / c.source.path).mkdir(parents=True, exist_ok=True)
    svc._SELF_LOCK_WAIT_S = 0.2          # fast contention (default 5.0s just delays the refusal)
    with reslock.operation_lock(paths, reslock.source_lock_key("src/loraham-daemon"),
                                "update", "x"):
        res = svc.build("daemon", apply=True)
    assert not res.ok and "blocked" in res.summary.lower()


def test_uninstall_blocked_by_held_source_lock(tmp_path):
    from lhpc.core import reslock
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    svc._SELF_LOCK_WAIT_S = 0.2          # fast contention (default 5.0s just delays the refusal)
    src = paths.under("src", "loraham-daemon"); src.mkdir(parents=True)
    with reslock.operation_lock(paths, reslock.source_lock_key("src/loraham-daemon"),
                                "update", "x"):
        res = svc.uninstall("daemon", apply=True)
    assert not res.ok and "blocked" in res.summary.lower()      # atomic guard fails closed


def test_adopt_blocks_when_recovery_required(tmp_path, installer):
    # An unresolved/invalid journal for THIS source must block adopt/update before any
    # candidate creation.
    inst = installer(search_root=tmp_path / "rt")
    inst.paths.under("src", "app").mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({          # invalid -> retained -> blocks
        "version": 2, "state": "prior-archived",
        "source_rel": "../escape", "prev_rel": "../escape", "candidate_rel": "../escape"}))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (d / "app.json").exists()                  # journal retained, source untouched


def test_activate_failed_restore_retains_journal(tmp_path, monkeypatch, installer):
    # dest->prev archives, staging->dest fails, AND prev->dest restore fails ->
    # the journal MUST be retained (active source missing -> recovery-required).
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    _fail_noreplace(monkeypatch)              # the promotion primitive is the one seam (install.py renames through it, never os.rename)
    assert _activate(inst, dest, staging) == "recovery-required"
    assert inst._journal_path(dest).exists()             # journal RETAINED (recovery-required)


def test_adopt_blocked_by_filename_mismatch_journal(tmp_path, installer):
    # A journal named app.json but declaring a different source is invalid -> retained
    # under app.json -> adopt of app is blocked.
    inst = installer(search_root=tmp_path / "rt")
    inst.paths.under("src", "app").mkdir(parents=True)
    inst.paths.under("src", "other").mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived",
        "source_rel": "src/other", "prev_rel": "src/.other.prev",
        "candidate_rel": "src/.other.candidate-1-2"}))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "recovery-required" in action.detail


def test_fallback_stable_tag_mismatch_blocks(tmp_path, make_repo, installer):
    inst = installer(search_root=tmp_path / "rt")
    make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_tag="v9.9.9"))
    action = inst.adopt_source(comp, source="stable")
    assert action.status == "failed" and "does not satisfy" in action.detail


def test_fallback_dev_branch_mismatch_blocks(tmp_path, make_repo, installer):
    inst = installer(search_root=tmp_path / "rt")
    make_repo(tmp_path / "rt" / "app-src", {"f": "x"})          # default branch (master/main), not "nope"
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", branch="nope"))
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "does not satisfy" in action.detail


def test_host_test_blocked_by_held_source_lock(tmp_path):
    from lhpc.core import reslock
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    (tmp_path / "src" / "loraham-daemon").mkdir(parents=True)   # present, so the lock is what refuses
    svc._SELF_LOCK_WAIT_S = 0.2          # fast contention (default 5.0s just delays the refusal)
    with reslock.operation_lock(paths, reslock.source_lock_key("src/loraham-daemon"),
                                "update", "x"):
        res = svc.test("daemon", apply=True)          # host test (no --tx)
    assert not res.ok and "blocked" in res.summary.lower()


def test_unknown_prev_blocks_and_is_not_discarded(tmp_path, installer):
    # A pre-existing .app.prev with NO active journal is an unowned orphan: activation
    # must block and must NOT recursively discard it.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    orphan = src / ".app.prev"; orphan.mkdir(); (orphan / "keep").write_text("ORPHAN")
    staging = src / ".app.candidate-9-9"; staging.mkdir(); (staging / "m").write_text("NEW")
    assert _activate(inst, dest, staging) == "failed-clean"
    assert (orphan / "keep").read_text() == "ORPHAN"     # orphan untouched
    assert (dest / "m").read_text() == "LIVE"            # active source untouched


def test_malformed_journal_blocks_unrelated_source(tmp_path, installer):
    # A malformed journal with NO safely derivable source must block ALL source mutation,
    # even for an unrelated source.
    inst = installer(search_root=tmp_path / "rt")
    inst.paths.under("src", "app").mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.json").write_text("{ not valid json")          # unparseable -> retained
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (d / "garbage.json").exists()                         # retained, not discarded


def test_adopt_blocked_while_index_lock_held(tmp_path, installer):
    from lhpc.core import reslock
    inst = installer(search_root=tmp_path / "rt")
    inst.paths.under("src", "app").mkdir(parents=True)
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    with reslock.operation_lock(inst.paths, inst._index_key(), "recover", "x"):
        action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "in progress" in action.detail


@pytest.mark.parametrize("mode", [
    pytest.param("pinned", id="pinned-without-configured-pin"),   # repo has no pin_commit
    pytest.param("stable", id="stable-without-any-tag"),          # repo has no tags
])
def test_selector_without_its_target_rejected(tmp_path, mode, make_repo, installer):
    inst = installer(search_root=tmp_path / "rt")
    make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))   # no pin_commit / no tag
    action = inst.adopt_source(comp, source=mode)
    assert action.status == "failed" and "does not satisfy" in action.detail


def test_valid_target_journal_recovered_through_adopt(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    _journal(inst, dest, src / ".app.prev", staging, "prior-archived")   # interrupted activation
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=False)
    # Recovery COMPLETED the interrupted activation under the index lock, then adopt
    # proceeded — it did NOT become permanently "busy"/"recovery-required".
    assert action.status == "skipped" and "already exists" in action.detail
    assert dest.is_dir() and (dest / "m").read_text() == "NEW"
    assert not inst._journal_path(dest).exists()        # journal cleared by recovery


def test_adopt_target_does_not_self_contend(tmp_path, git, make_repo, installer):
    # Same source has a valid completing journal; adopt(force) must recover it and then
    # re-stage, never blocking on its own source lock.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    _register_tree(git, inst, dest, _comp(), files={"m": "LIVE"})       # an owned, active prior
    _journal(inst, dest, src / ".app.prev", src / ".app.candidate-1-2", "prior-archived")
    make_repo(tmp_path / "rt" / "app-src", {"m": "NEW"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "done", action.detail                    # recovered, then re-staged
    assert (dest / "m").read_text() == "NEW"
    assert not inst._journal_path(dest).exists()


def test_recovery_required_preserves_candidate_and_prior(tmp_path, monkeypatch, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    _fail_noreplace(monkeypatch)              # the promotion primitive is the one seam
    assert _activate(inst, dest, staging) == "recovery-required"
    assert staging.is_dir() and (staging / "m").read_text() == "NEW"   # candidate PRESERVED
    assert inst._journal_path(dest).exists()                            # journal retained


def test_journal_unlink_failure_after_activation_is_recovery_required(tmp_path, monkeypatch, installer):
    from lhpc.core import runtime_fs
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    # The activation renames succeed, but the owned-journal removal fails -> typed
    # recovery-required (never an untyped exception), journal retained. The marker's remove is
    # the collaborator stubbed: an unlink that fails on an owned runtime file has no System seam.
    monkeypatch.setattr(runtime_fs.OwnedMarker, "remove", lambda self: False)
    assert _activate(inst, dest, staging) == "recovery-required"
    assert (dest / "m").read_text() == "NEW"                  # activation DID happen
    assert inst._journal_path(dest).exists()                  # journal retained for recovery


def test_malformed_journal_blocks_build_and_uninstall(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    src = paths.under("src", "loraham-daemon"); src.mkdir(parents=True)
    d = paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.json").write_text("{ not valid")        # unresolved -> blocks all mutation
    rb = svc.build("daemon", apply=True)
    assert not rb.ok and "blocked" in rb.summary.lower()
    ru = svc.uninstall("daemon", apply=True)
    assert any("blocked" in x.lower() for x in ([ru.summary] + list(ru.details)))


def test_retained_journal_blocks_every_source_op(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    paths.under("src", "loraham-daemon").mkdir(parents=True)
    paths.under("src", "LoRaHAM_Daemon").mkdir(parents=True)
    d = paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.json").write_text("{ retained")        # unresolved -> blocks all source ops
    assert "blocked" in svc.build("daemon", apply=True).summary.lower()
    assert "blocked" in svc.test("daemon", apply=True).summary.lower()
    assert "blocked" in svc.uninstall("daemon", apply=True).summary.lower()
    # A SOURCED stack's start is blocked too (chat -> src/LoRaHAM_Daemon); meshtastic declares
    # no source, so it has no source transaction to be blocked by.
    rs = svc.start("chat", apply=True)
    assert not rs.ok and "unresolved" in rs.summary.lower()


def test_source_guard_holds_index_during_handoff(tmp_path):
    # While the index lock is held externally, the guard cannot even check -> ResourceBusy
    # (no window where a clean op proceeds past a concurrently-created journal).
    from lhpc.core import reslock
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    paths.under("src", "loraham-daemon").mkdir(parents=True)
    with reslock.operation_lock(paths, "source-txn-index", "adopt", "x"):
        res = svc.build("daemon", apply=True)
    assert not res.ok and "blocked" in res.summary.lower()


def test_broken_active_symlink_not_treated_as_intact(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    os.symlink(src / "does-not-exist", dest)            # dangling active symlink
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("PRIOR")
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")
    assert "intact" not in msg                            # broken symlink != usable source
    # the INJECTED occupant is never deleted to continue: retained as evidence, prior kept
    assert "recovery-required" in msg and "occupied" in msg
    assert dest.is_symlink()                              # injected leaf UNTOUCHED
    assert (prev / "m").read_text() == "PRIOR"            # prior retained at .prev


def test_failed_journal_unlink_is_recovery_required(tmp_path, monkeypatch, installer):
    from lhpc.core import runtime_fs
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    prev = src / ".app.prev"; prev.mkdir()
    jf = inst._journal_path(dest); jf.parent.mkdir(parents=True, exist_ok=True); jf.write_text("{}")
    # the marker's remove is the collaborator stubbed (no System seam for an owned-file unlink)
    monkeypatch.setattr(runtime_fs.OwnedMarker, "remove", lambda self: False)   # removal "fails"
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")   # must NOT raise
    assert "recovery-required" in msg and "journal could not be removed" in msg
    assert jf.exists()


def test_failed_prev_cleanup_after_activation_retains_journal(tmp_path, monkeypatch, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    prev = src / ".app.prev"; prev.mkdir()
    jf = inst._journal_path(dest); jf.parent.mkdir(parents=True, exist_ok=True); jf.write_text("{}")
    # the bound removal of `.prev` is the collaborator stubbed: a recursive delete that fails
    # half-way needs a device error no injected System can produce
    monkeypatch.setattr(type(inst), "_prev_cleanup_ok",
                        lambda self, txn, prev, ident=None, **kw: False)  # prev removal "fails"
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")
    assert "recovery-required" in msg and "prior could not be removed" in msg
    assert jf.exists() and prev.exists()                  # journal + prior retained


def test_dangling_linked_source_not_activated(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"
    os.symlink(src / "gone", staging)              # candidate symlink -> NONEXISTENT dir
    outcome = _activate(inst, dest, staging)
    assert outcome == "recovery-required"           # dangling link is NOT a usable source
    assert inst._journal_path(dest).exists()        # journal retained (not deleted)
    assert dest.is_symlink() and not dest.is_dir()  # the dangling link occupies dest


def test_regular_file_active_source_not_activated(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"; staging.write_text("not a dir")  # regular file
    outcome = _activate(inst, dest, staging)
    assert outcome == "recovery-required"           # a regular file is not a source tree
    assert inst._journal_path(dest).exists()


def test_real_dir_candidate_activates(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "f").write_text("x")
    assert _activate(inst, dest, staging) == "activated"
    assert dest.is_dir() and not inst._journal_path(dest).exists()


def test_recovery_rejects_regular_file_active_source(tmp_path, installer):
    # recovery must also require a usable DIRECTORY before clearing the journal.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.write_text("regular file")      # not a dir
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("PRIOR")
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")
    assert "intact" not in msg                       # a file is not a usable active source


def test_activate_failed_rename_leaving_dangling_dest_restores_prior(tmp_path, monkeypatch, installer):
    # dest->prev archives; staging->dest fails AND an external race leaves dest a DANGLING
    # symlink. _activate must NOT accept the dangling symlink as usable: it restores the
    # prior to a usable dir before clearing the journal (no erased recovery evidence).
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    _fail_noreplace(monkeypatch, suffixes=(".app.candidate-1-2",), plant_dangling=True)   # plants the dangling leaf itself
    outcome = _activate(inst, dest, staging)
    # the injected dangling symlink is NEVER deleted to continue: evidence retained,
    # prior stays archived at .prev, journal retained for recovery
    assert outcome == "recovery-required"
    assert dest.is_symlink()                                      # injected leaf UNTOUCHED
    assert (src / ".app.prev" / "m").read_text() == "LIVE"        # prior safe at .prev
    assert inst._journal_path(dest).exists()


def test_activate_dangling_dest_unrestorable_retains_journal(tmp_path, monkeypatch, installer):
    # Same race, but the prior restore ALSO fails -> retain journal (recovery-required),
    # never clear it leaving an unusable active source.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir()
    _fail_noreplace(monkeypatch, plant_dangling=True)  # candidate AND prev restore fail; dangling leaf planted
    assert _activate(inst, dest, staging) == "recovery-required"
    assert inst._journal_path(dest).exists()           # journal retained (recovery route)


def test_activation_prev_cleanup_failure_recovery_required_then_recoverable(tmp_path, monkeypatch, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    real = type(inst)._prev_cleanup_ok
    fail = {"on": True}
    # the bound removal of `.prev` is the collaborator stubbed (see the test above)
    monkeypatch.setattr(
        type(inst), "_prev_cleanup_ok",
        lambda self, txn, prev, ident=None, **kw: False if fail["on"]
        else real(self, txn, prev, ident, **kw))
    # Activation succeeds, but the .prev cleanup fails -> recovery-required (typed).
    assert _activate(inst, dest, staging) == "recovery-required"
    assert dest.is_dir() and (dest / "m").read_text() == "NEW"   # active source usable
    assert inst._journal_path(dest).exists()                     # journal retained
    assert (src / ".app.prev").exists()                          # .prev retained
    # A later recovery (cleanup now works) clears the journal + .prev safely.
    fail["on"] = False
    inst._recover_scan()
    assert not inst._journal_path(dest).exists()
    assert not (src / ".app.prev").exists()
    assert (dest / "m").read_text() == "NEW"


def _txn_dir(inst):
    return inst.paths.under("state", "source-txn")


def test_symlinked_journal_blocks_recovery_not_skipped(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    d = _txn_dir(inst); d.mkdir(parents=True)
    outside = tmp_path / "evil.json"
    outside.write_text('{"version": 2, "state": "planned", "source_rel": "src/x", '
                       '"prev_rel": "src/.x.prev", "candidate_rel": "src/.x.candidate-1-2"}')
    os.symlink(outside, d / "app.json")                     # symlinked journal entry
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "symlink" in m for m in msgs)   # blocks, not skipped
    assert inst._pending_journals() is True                 # still blocks all mutation
    assert (d / "app.json").is_symlink()                    # evidence retained (not deleted)


def test_symlinked_txn_dir_blocks(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    (inst.paths.under("state")).mkdir(parents=True)
    outside = tmp_path / "evil-txn"; outside.mkdir()
    (outside / "app.json").write_text("{}")
    os.symlink(outside, _txn_dir(inst))                     # the txn DIR is a symlink
    assert inst._pending_journals() is True                 # unsafe container -> block
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m for m in msgs)


def test_malformed_journal_is_retained_and_blocks(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    d = _txn_dir(inst); d.mkdir(parents=True)
    (d / "app.json").write_text("{ this is not json")
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "invalid" in m for m in msgs)
    assert (d / "app.json").exists()                        # evidence preserved
    assert inst._pending_journals() is True


def test_absent_txn_dir_is_empty_not_blocked(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")                                   # no state/source-txn dir at all
    assert inst._pending_journals() is False
    assert inst.recover_source_activations() == []


def test_journal_targeting_non_managed_path_is_blocked(tmp_path, installer):
    # A journal whose destination is a CONTAINED but non-managed runtime path
    # (config/foo, state/foo, …) is retained + blocked — recovery never renames/deletes
    # outside the manifest's managed-source set, even if the filename looks plausible.
    inst = installer(search_root=tmp_path / "rt")                                    # only src/app is managed
    victim = inst.paths.under("config", "foo"); victim.mkdir(parents=True)
    (victim / "keep").write_text("KEEP")
    d = _txn_dir(inst); d.mkdir(parents=True, exist_ok=True)
    (d / "foo.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "config/foo",
        "prev_rel": "config/.foo.prev", "candidate_rel": "config/.foo.candidate-1-2"}))
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "not a known managed source" in m for m in msgs)
    assert (victim / "keep").read_text() == "KEEP"           # non-source path untouched
    assert (d / "foo.json").exists()                         # evidence retained
    assert inst._pending_journals() is True


def test_adopt_reports_pinned_provenance(tmp_path, make_repo, installer):
    # A real local pinned repo -> adopt reports pinned-verified provenance in
    # the action state + detail (local git, no network).
    inst = installer(search_root=tmp_path / "rt")
    head = make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done" and action.provenance == "pinned-verified"
    assert "provenance: pinned-verified" in action.detail


def test_adopt_reports_mutable_dev_provenance(tmp_path, make_repo, installer):
    inst = installer(search_root=tmp_path / "rt")
    make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    action = inst.adopt_source(comp, source="dev")           # explicit mutable selection
    assert action.status == "done" and action.provenance == "mutable-dev"


def _register_tree(git, inst, dest, comp, remote="", files=None):
    """Make an EXISTING tree (or one created from `files`) pass the current-identity gate: turn
    it into a committed git repo and write a matching ownership record (HEAD + remote)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (dest / name).write_text(text)
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "commit", "-qm", "prior")
    head = git(dest, "rev-parse", "HEAD")
    rel = str(dest.relative_to(inst.paths.runtime_root))
    assert source_registry.write_record(inst.paths, source_registry.RegistryRecord(
        rel, remote, "pinned", head, time.time(), "", (comp.id,)))
    return head


def test_provenance_not_ok_blocks_activation_prior_intact(tmp_path, monkeypatch, git, make_repo, installer):
    # A not-ok provenance result BLOCKS activation BEFORE the active source is touched.
    from lhpc.core import provenance
    inst = installer(search_root=tmp_path / "rt")
    head = make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    active = inst.paths.under("src", "app"); active.mkdir(parents=True)
    (active / "OLD").write_text("keep")                  # a prior active source
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src",
                                       pin_commit=head))
    _register_tree(git, inst, active, comp)                   # identity gate passes -> reaches provenance
    monkeypatch.setattr(provenance, "evaluate", lambda *a, **k: provenance.ProvenanceResult(
        provenance.UNVERIFIED_BLOCKED, False, False, "forced block"))
    action = inst.adopt_source(comp, source="pinned", force=True)
    assert action.status == "failed" and "provenance blocked before activation" in action.detail
    assert action.provenance == provenance.UNVERIFIED_BLOCKED
    assert (active / "OLD").read_text() == "keep"        # prior active source UNTOUCHED


def test_signer_config_diagnostics_reach_result(tmp_path, make_repo, installer):
    # Trusted-signer config diagnostics are surfaced in the install/adopt result.
    head = make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    inst = installer(comp, search_root=tmp_path / "rt",
                     values={"provenance": {"trusted_signers": ["not-a-fingerprint"]}})
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done"
    assert "signer-config" in action.detail and "malformed" in action.detail


def test_adopt_source_parent_swap_before_staging_blocks(tmp_path, make_repo, installer):
    # A symlinked source parent before staging fails closed; nothing outside touched.
    inst = installer(search_root=tmp_path / "rt")
    head = make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    outside = tmp_path / "out"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    inst.paths.runtime_root.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, inst.paths.runtime_root / "src")            # source parent -> outside
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed"                               # symlinked parent fails closed
    assert list(outside.iterdir()) == [outside / "keep"]           # nothing staged outside
    assert (outside / "keep").read_text() == "KEEP"


def test_same_basename_sources_get_distinct_journals(tmp_path, installer):
    # src/a/app and src/b/app must never share a journal identity.
    inst = installer(search_root=tmp_path / "rt")
    root = inst.paths.runtime_root
    ja = inst._journal_path(root / "src" / "a" / "app")
    jb = inst._journal_path(root / "src" / "b" / "app")
    assert ja.name != jb.name and ja.name.startswith("app-") and jb.name.startswith("app-")


def test_legacy_basename_journal_is_retained_and_blocks(tmp_path, installer):
    # A legacy basename-only journal (app.json) does not match the identity-bound
    # name, so recovery retains it and blocks — never silently migrates or deletes it.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "src/app",
        "prev_rel": "src/.app.prev", "candidate_rel": "src/.app.candidate-1-2"}))
    msgs = inst.recover_source_activations()
    assert any("filename does not match" in m for m in msgs)
    assert (d / "app.json").exists()                     # legacy journal RETAINED, not migrated


def test_post_activation_provenance_mismatch_restores_prior(tmp_path, monkeypatch, git, make_repo, installer):
    # A post-activation provenance failure rolls back to the prior via the held FD BEFORE
    # `.prev`/journal are cleared — prior restored, journal retained, never a green success.
    from lhpc.core import provenance
    inst = installer(search_root=tmp_path / "rt")
    head = make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    active = inst.paths.under("src", "app"); active.mkdir(parents=True)
    (active / "OLD").write_text("PRIOR")                 # a prior active source
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    _register_tree(git, inst, active, comp)                   # identity gate passes -> reaches provenance
    calls = {"n": 0}
    real = provenance.evaluate
    def fake(runner, path, spec, source, trusted, expected_commit=""):
        calls["n"] += 1
        if calls["n"] >= 2:                              # #1 = pre-gate (ok); later = post -> fail
            return provenance.ProvenanceResult(provenance.UNVERIFIED_BLOCKED, False, False, "forced")
        return real(runner, path, spec, source, trusted)
    monkeypatch.setattr(provenance, "evaluate", fake)
    action = inst.adopt_source(comp, source="pinned", force=True)
    assert action.status == "failed" and "rolled back" in action.detail
    assert (active / "OLD").read_text() == "PRIOR"       # prior RESTORED via held-FD rollback
    # a PROVEN rollback leaves a coherent state -> the journal is CLEARED (it is retained
    # only when rollback/record completion cannot be proven)
    assert not inst._journal_path(active).exists()


def test_successful_adopt_clears_journal_only_after_provenance(tmp_path, make_repo, installer):
    # The normal success path clears the journal/.prev ONLY after final provenance passes.
    inst = installer(search_root=tmp_path / "rt")
    head = make_repo(tmp_path / "rt" / "app-src", {"f": "x"})
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    dest = inst.paths.under("src", "app")
    assert action.status == "done" and dest.is_dir()
    assert not inst._journal_path(dest).exists()         # journal cleared (provenance passed)
    assert not (dest.parent / ".app.prev").exists()      # .prev cleaned


def test_journal_filename_uses_full_sha256(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    name = inst._journal_path(inst.paths.runtime_root / "src" / "app").name
    assert re.fullmatch(r"app-[0-9a-f]{64}\.json", name)     # FULL digest, not truncated


def test_journal_missing_txn_id_retained_and_blocks(tmp_path, installer):
    # A journal at the identity-bound path but with NO txn_id (legacy payload) is
    # retained + blocked by recovery, never resumed.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir()
    inst._journal_path(dest).write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "src/app",
        "prev_rel": "src/.app.prev", "candidate_rel": "src/.app.candidate-1-2"}))  # no txn_id
    msgs = inst.recover_source_activations()
    assert any("transaction id" in m for m in msgs)
    assert inst._journal_path(dest).exists()                # retained as evidence


def test_journal_altered_txn_id_retained_and_blocks(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir()
    inst._journal_path(dest).write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "src/app",
        "prev_rel": "src/.app.prev", "candidate_rel": "src/.app.candidate-1-2",
        "txn_id": "deadbeef"}))                              # wrong txn_id
    msgs = inst.recover_source_activations()
    assert any("transaction id" in m for m in msgs)
    assert inst._journal_path(dest).exists()


def test_copy_into_candidate_preserves_symlinks_and_ignores(tmp_path):
    # The local-fallback copy fills the pre-created empty candidate per-entry (no
    # dirs_exist_ok merge), preserving symlinks unfollowed and honoring the ignore set.
    local = tmp_path / "local"; (local / "sub").mkdir(parents=True)
    (local / "f").write_text("F"); (local / "sub" / "g").write_text("G")
    os.symlink("f", local / "ln")                       # relative symlink
    (local / "__pycache__").mkdir(); (local / "__pycache__" / "x").write_text("junk")
    cand = tmp_path / "cand"; cand.mkdir()              # pre-created empty candidate
    Installer._copy_into_candidate(local, str(cand))
    assert (cand / "f").read_text() == "F" and (cand / "sub" / "g").read_text() == "G"
    assert (cand / "ln").is_symlink() and os.readlink(cand / "ln") == "f"   # not followed
    assert not (cand / "__pycache__").exists()         # ignore set honored


def test_journal_exclusive_create_refuses_existing_leaf(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; prev = src / ".app.prev"; staging = src / ".app.candidate-1-2"
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    jp = inst._journal_path(dest)
    jp.write_text("{injected}")                                  # regular file injected
    assert inst._create_journal(dest, prev, staging, _META, {}) is None     # O_EXCL refuses
    assert jp.read_text() == "{injected}"                        # never overwritten
    jp.unlink(); os.symlink(tmp_path / "x", jp)                  # symlink injected
    assert inst._create_journal(dest, prev, staging, _META, {}) is None     # O_NOFOLLOW refuses


def test_injected_journal_blocks_before_prev_change(tmp_path, installer):
    # A journal appearing after the absent-preflight blocks BEFORE any dest->.prev.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    inst._journal_path(dest).write_text("{injected regular journal}")
    outcome = _activate(inst, dest, staging)
    assert outcome == "recovery-required"
    assert (dest / "m").read_text() == "LIVE"                    # dest untouched
    assert not (src / ".app.prev").exists()                      # .prev NEVER created
    assert inst._journal_path(dest).exists()                     # injected journal retained


def test_activate_verifies_candidate_identity_before_promotion(tmp_path, installer):
    # If the candidate is not the FD-verified inode, activation refuses (via _activate_held
    # receiving a mismatched handle) — proven through the transaction's verify_candidate.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")

    class _BadHandle:
        name = ".app.candidate-1-2"
        st_dev = -1
        st_ino = -1
        fd = -1                                   # dead descriptor: no live ident either
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        outcome = inst._activate_held(txn, dest, staging, _META, handle=_BadHandle())
    assert outcome == "recovery-required"                        # a dead handle proves nothing
    assert (dest / "m").read_text() == "LIVE"                    # active source not replaced


def test_candidate_substitution_is_recovery_required_and_preserved(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "x").write_text("NEW")
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        bad = source_fs.CandidateHandle(".app.candidate-1-2", -1, -1, -1)   # wrong inode
        outcome = inst._activate_held(txn, dest, staging, _META, handle=bad)
    assert outcome == "recovery-required"                     # NOT failed-clean
    assert (dest / "m").read_text() == "LIVE"                 # active source untouched
    assert (staging / "x").read_text() == "NEW"              # substituted staging RETAINED
    assert inst._journal_path(dest).exists()                  # journal retained


def test_journal_ownership_lost_before_update_rolls_back(tmp_path, monkeypatch, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    # the journal's state write is the collaborator stubbed: losing ownership of an open,
    # fsynced marker between two steps needs a concurrent process
    monkeypatch.setattr(type(inst), "_update_journal",
                        lambda self, jh, d, p, s, state: False)   # ownership lost on update
    outcome = _activate(inst, dest, staging)
    assert outcome == "recovery-required"
    assert (dest / "m").read_text() == "LIVE"                 # prior restored
    assert inst._journal_path(dest).exists()                  # journal retained


def test_cleanup_owned_staging_removes_intact_retains_substituted(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    with source_fs.ManagedSourceTransaction(inst.paths, src) as txn:
        h = txn.create_candidate(".app.candidate-1-2")
        assert inst._cleanup_owned_staging(txn, h, ".app.candidate-1-2") == "removed"
        assert txn.leaf_kind(".app.candidate-1-2") == "absent"
        h2 = txn.create_candidate(".app.candidate-3-4")
        shutil.rmtree(src / ".app.candidate-3-4")
        os.symlink(tmp_path, src / ".app.candidate-3-4")        # substitute the leaf
        assert inst._cleanup_owned_staging(txn, h2, ".app.candidate-3-4") == "identity-lost"
        assert (src / ".app.candidate-3-4").is_symlink()        # substitute RETAINED


def test_substitution_during_successful_provenance_is_recovery_required(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        h = txn.create_candidate(".app.candidate-1-2")
        staging = src / ".app.candidate-1-2"

        def va():      # provenance "passes" but swaps the now-active dest for a NEW inode
            shutil.rmtree(src / "app"); (src / "app").mkdir(); (src / "app" / "evil").write_text("x")
            return True
        outcome = inst._activate_held(txn, dest, staging, _META, verify_active=va, handle=h)
    assert outcome == "recovery-required"                       # never reported activated
    assert (src / ".app.prev").exists()                        # .prev retained
    assert inst._journal_path(dest).exists()                   # journal retained
    assert (src / "app" / "evil").exists()                     # substituted active leaf retained


def test_substitution_during_failed_provenance_does_not_delete_dest(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        h = txn.create_candidate(".app.candidate-1-2")
        staging = src / ".app.candidate-1-2"

        def va():      # provenance FAILS, and the active dest was swapped meanwhile
            shutil.rmtree(src / "app"); (src / "app").mkdir(); (src / "app" / "evil").write_text("x")
            return False
        outcome = inst._activate_held(txn, dest, staging, _META, verify_active=va, handle=h)
    assert outcome == "recovery-required"
    assert (src / "app" / "evil").exists()                     # substituted dest NOT deleted
    assert (src / ".app.prev").exists() and inst._journal_path(dest).exists()


def test_failed_staging_cleans_controller_candidate(tmp_path, installer):
    inst = installer(search_root=tmp_path / "rt")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"))         # no remote, no local -> fails
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed"
    src = inst.paths.under("src")
    leftovers = [p.name for p in src.iterdir() if p.name.startswith(".app.candidate")] \
        if src.exists() else []
    assert leftovers == []                                     # intact candidate cleaned up


def test_v3_journal_generation_blocked_with_substituted_leaves(tmp_path, installer):
    # A structurally-valid v3 journal — even with substituted candidate/dest/prev leaves —
    # triggers NO automatic promotion/restore/cleanup: typed recovery-required, everything
    # retained, further mutation blocked.
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("SUBSTITUTED DEST")
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("SUBSTITUTED PRIOR")
    staging = src / ".app.candidate-1-2"; staging.mkdir()
    (staging / "m").write_text("SUBSTITUTED CANDIDATE")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 3, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "a" * 40, "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": True}}))
    msgs = inst.recover_source_activations()
    assert any("generation" in m and "recovery-required" in m for m in msgs)
    assert (dest / "m").read_text() == "SUBSTITUTED DEST"       # nothing touched
    assert (prev / "m").read_text() == "SUBSTITUTED PRIOR"
    assert (staging / "m").read_text() == "SUBSTITUTED CANDIDATE"
    assert inst._journal_path(dest).exists()                    # journal retained


def test_v5_inode_recycling_forged_ctime_prior_not_restored(tmp_path, installer):
    # DETERMINISTIC inode-recycling forgery (no reliance on real inode reuse): the journal records
    # the ORIGINAL prior's [dev, ino, ctime_ns]; a `.prev` recreated on the RECYCLED inode has the
    # SAME dev+ino but a fresh ctime. The v5 ctime check catches it -> the forged prior is NOT
    # restored, everything retained. (Candidate absent so recovery takes the prior-restore path.)
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"                                         # died after dest->.prev (dest absent)
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("SUBSTITUTE PRIOR")
    staging = src / ".app.candidate-1-2"                       # absent -> promotion skipped
    real = _ident_of(prev)                                     # [dev, ino, ctime_ns]
    forged = [real[0], real[1], real[2] - 1]                  # SAME dev+ino, forged (older) ctime
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "pinned", "resolved_commit": "", "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": True},
        "idents": {"candidate": None, "prev": forged}}))
    msgs = inst.recover_source_activations()
    assert any("substituted" in m and "recovery-required" in m for m in msgs)   # ctime mismatch caught
    assert not dest.exists()                                   # forged prior NOT restored
    assert (prev / "m").read_text() == "SUBSTITUTE PRIOR"      # untouched
    assert inst._journal_path(dest).exists()                   # retained


def test_v4_journal_retained_as_unprovable_not_restored(tmp_path, installer):
    # A v4 ([dev, ino]-only) journal is no longer trusted for destructive recovery — its identity is
    # forgeable via inode recycling — so it is retained-as-unprovable exactly like v2/v3, never a
    # roll-back. (An identical v5 journal DOES roll back: see test_recover_rolls_back_after_prior_archived.)
    inst = installer(search_root=tmp_path / "rt")
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"                                         # died after dest->.prev (dest absent)
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("PRIOR")
    staging = src / ".app.candidate-1-2"
    _journal(inst, dest, prev, staging, "prior-archived", version=4)
    msgs = inst.recover_source_activations()
    assert any("generation" in m and "recovery-required" in m for m in msgs)
    assert not dest.exists()                                   # NOT restored (v4 untrusted)
    assert (prev / "m").read_text() == "PRIOR"                 # retained
    assert inst._journal_path(dest).exists()                   # journal retained


# --- race safety: a concurrent substitution at every seam of a destructive operation --------------
def _seam(monkeypatch, point: str, action):
    """Fire `action(path)` exactly once at seam `point` — `source_fs.race_seam` is the
    production no-op hook that exists for exactly this injection."""
    fired = {"done": False}
    def hook(p, path=""):
        if p == point and not fired["done"]:
            fired["done"] = True
            action(path)
    monkeypatch.setattr(source_fs, "race_seam", hook)
    return fired


def test_update_refuses_substituted_dir_at_archive(tmp_path, monkeypatch, make_repo, installer):
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"       # v1 active + recorded
    dest = inst.paths.under("src", "app")

    def swap(_path):
        # external process: replace the verified leaf with an unknown directory
        shutil.move(str(dest), str(tmp_path / "stolen"))
        dest.mkdir()
        (dest / "unknown.txt").write_text("injected")
    fired = _seam(monkeypatch, "pre-archive", swap)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "concurrently replaced" in action.detail
    assert (dest / "unknown.txt").read_text() == "injected"             # substitute UNTOUCHED
    assert not dest.with_name(".app.prev").exists()                     # nothing archived
    assert not inst._journal_path(dest).exists()                        # no retained journal
    assert not list(dest.parent.glob(".app.candidate-*"))               # candidate cleaned


def test_update_refuses_substituted_symlink_at_archive(tmp_path, monkeypatch, make_repo, installer):
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    outside = tmp_path / "outside"
    outside.mkdir()

    def swap(_path):
        shutil.rmtree(dest)
        dest.symlink_to(outside)                                        # symlink substitution
    fired = _seam(monkeypatch, "pre-archive", swap)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "concurrently replaced" in action.detail
    assert dest.is_symlink() and os.readlink(dest) == str(outside)      # substitute untouched
    assert outside.exists()                                             # target untouched


def test_fresh_install_refuses_injected_empty_dir(tmp_path, monkeypatch, make_repo, installer):
    # plain rename(2) silently REPLACES an empty directory — the atomic NOREPLACE promotion
    # must refuse instead, leaving the injected directory exactly in place.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    dest = inst.paths.under("src", "app")

    def inject(_path):
        dest.mkdir(parents=True)                                        # injected EMPTY dir
    fired = _seam(monkeypatch, "pre-promote", inject)
    action = inst.adopt_source(comp, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "appeared at the destination" in action.detail
    assert dest.is_dir() and list(dest.iterdir()) == []                 # injected dir UNTOUCHED
    assert source_registry.read_record(inst.paths, "src/app") is None   # no false ownership
    assert not inst._journal_path(dest).exists()
    assert not list(dest.parent.glob(".app.candidate-*"))               # candidate cleaned


def test_fresh_install_refuses_injected_symlink(tmp_path, monkeypatch, make_repo, installer):
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    dest = inst.paths.under("src", "app")
    outside = tmp_path / "outside"
    outside.mkdir()

    def inject(_path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(outside)
    fired = _seam(monkeypatch, "pre-promote", inject)
    action = inst.adopt_source(comp, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "appeared at the destination" in action.detail
    assert dest.is_symlink()                                            # injected leaf untouched
    assert not any(outside.iterdir())                                   # target never written


def test_unchanged_update_and_install_still_succeed(tmp_path, git, make_repo, installer):
    # The protocols must not break legitimate operation: fresh install then a clean update.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "done", action.detail
    dest = inst.paths.under("src", "app")
    assert (dest / "file.txt").read_text() == "v2\n"
    assert not list(dest.parent.glob(".app.quarantine-*"))              # no artifacts


class _OriginOf:
    """A runner answering the one git question the identity gate asks — the origin of the
    checkout — and delegating everything else to the FakeSystem. A pattern stub rather than a
    commands-table entry because the gate asks through a controller-pinned `/proc/<pid>/fd/N`
    path, so the exact argv is only knowable by resolving it."""

    def __init__(self, fake, dest, url):
        self._fake, self._dest, self._url = fake, os.path.realpath(str(dest)), url

    def run(self, argv, timeout, *a, **k):
        argv = list(argv)
        if (argv[:2] == ["git", "-C"] and len(argv) >= 4 and os.path.realpath(argv[2]) == self._dest
                and argv[3:] == ["config", "--get", "remote.origin.url"]):
            return CR(0, self._url + "\n", "")
        return self._fake.run(argv, timeout, *a, **k)


def _svc_env(tmp_path):
    """A registered kiss checkout under a FakeSystem service, identity-verifiable."""
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    dest.mkdir(parents=True)
    (dest / "code.c").write_text("x")
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "pinned", "", time.time(),
                                       "", ("loraham-kiss-tnc", "loraham-kiss-serial")))
    fake = FakeSystem()
    runner = _OriginOf(fake, dest, "https://github.com/makrohard/loraham-kiss-tnc.git")
    svc = ControllerService(system=System(runner=runner, procfs=fake, fs=fake, unix=fake),
                            paths=Paths(runtime_root=tmp_path))
    return svc, dest


def test_uninstall_refuses_substituted_dir_at_detach(tmp_path, monkeypatch):
    svc, dest = _svc_env(tmp_path)

    def swap(_path):
        shutil.move(str(dest), str(tmp_path / "stolen"))
        dest.mkdir()
        (dest / "precious.txt").write_text("user data")
    fired = _seam(monkeypatch, "pre-detach", swap)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok                                                   # truthful failure
    assert (dest / "precious.txt").read_text() == "user data"           # substitute PRESERVED
    assert not list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))  # nothing quarantined


def test_uninstall_refuses_substituted_symlink_at_detach(tmp_path, monkeypatch):
    svc, dest = _svc_env(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")

    def swap(_path):
        shutil.rmtree(dest)
        dest.symlink_to(outside)
    fired = _seam(monkeypatch, "pre-detach", swap)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok
    assert dest.is_symlink()                                            # substitute preserved
    assert (outside / "keep").exists()                                  # target untouched


def test_clean_refuses_substituted_dir_at_detach(tmp_path, monkeypatch):
    svc, dest = _svc_env(tmp_path)

    def swap(_path):
        shutil.move(str(dest), str(tmp_path / "stolen"))
        dest.mkdir()
        (dest / "precious.txt").write_text("user data")
    fired = _seam(monkeypatch, "pre-detach", swap)
    res = svc.clean("kiss", apply=True, purge=True)
    assert fired["done"]
    assert not res.ok
    assert (dest / "precious.txt").read_text() == "user data"           # substitute PRESERVED


def test_uninstall_unchanged_still_succeeds_and_leaves_no_quarantine(tmp_path):
    svc, dest = _svc_env(tmp_path)
    res = svc.uninstall("kiss", apply=True)
    assert res.ok, res.details
    assert not dest.exists()
    assert not list((tmp_path / "src").glob(".loraham-kiss-tnc.quarantine-*"))


def test_orphan_quarantine_evidence_blocks_and_is_retained(tmp_path):
    # A crash between detach and removal leaves a quarantine leaf: destructive ops refuse
    # (actionable), and the evidence is never auto-deleted.
    svc, dest = _svc_env(tmp_path)
    q = dest.parent / ".loraham-kiss-tnc.quarantine-1-2"
    q.mkdir()
    (q / "evidence").write_text("crash remainder")
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("quarantine evidence" in d for d in res.details)
    assert (q / "evidence").exists()                                    # retained
    assert dest.exists()                                                # source untouched


def test_unavailable_renameat2_refuses_before_any_mutation(tmp_path, monkeypatch, make_repo, installer):
    # Without the atomic no-clobber primitive, source lifecycle mutation refuses TYPED —
    # no journal, candidate, source, or registry change; and NO check-then-rename fallback.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    monkeypatch.setattr(source_fs, "_renameat2_fn", None)     # the libc symbol is absent
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "renameat2" in action.detail
    dest = inst.paths.under("src", "app")
    assert not dest.exists()                                            # no source
    assert not inst._journal_path(dest).exists()                        # no journal
    assert source_registry.read_record(inst.paths, "src/app") is None   # no registry
    assert not list(dest.parent.glob(".app.candidate-*"))               # no candidate either
    # uninstall/clean refuse likewise, before any detach
    svc, sdest = _svc_env(tmp_path / "svc")
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok and any("renameat2" in d for d in res.details)
    assert sdest.exists()


def test_injected_prev_at_archive_blocks_with_zero_mutation(tmp_path, monkeypatch, make_repo, installer):
    # A leaf injected at `.prev` between the preflight and the archive rename: the NOREPLACE
    # archive refuses — nothing renamed, injected leaf + active source untouched.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    prev = dest.with_name(".app.prev")

    def inject(_path):
        prev.mkdir()
        (prev / "foreign").write_text("injected")
    fired = _seam(monkeypatch, "pre-archive", inject)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "appeared" in action.detail
    assert (prev / "foreign").read_text() == "injected"                 # injected UNTOUCHED
    assert (dest / "file.txt").exists()                                 # active untouched
    assert not inst._journal_path(dest).exists()


def _prior_archived_crash(inst):
    """The prior-archived crash state: `.prev` holds the archived prior (its v5 ident in the
    journal), no candidate, dest absent. Returns (dest, prev)."""
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    prev = src / ".app.prev"
    prev.mkdir(); (prev / "m").write_text("PRIOR")
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    cand_rel = rel(src / ".app.candidate-1-2")
    st = os.stat(prev, follow_symlinks=False)
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "dev", "resolved_commit": "a" * 40, "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": True},
        "idents": {"candidate": None, "prev": [st.st_dev, st.st_ino, st.st_ctime_ns]}}))
    return dest, prev


def test_recovery_retains_an_occupied_destination(installer):
    # prior-archived crash state: an OCCUPIED destination (injected dir) is never deleted to
    # restore the prior.
    inst = installer()
    dest, prev = _prior_archived_crash(inst)
    dest.mkdir(); (dest / "foreign").write_text("injected occupant")
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and ("occupied" in m or "unverified occupant" in m)
               for m in msgs)
    assert (dest / "foreign").exists()                                  # occupant retained
    assert (prev / "m").read_text() == "PRIOR"                          # prior retained
    assert inst._journal_path(dest).exists()                            # journal retained


def test_recovery_never_restores_a_substituted_prev(installer):
    # prior-archived crash state: a SUBSTITUTED `.prev` (ident mismatch) is never restored or
    # removed — recovery refuses and retains it.
    inst = installer()
    dest, prev = _prior_archived_crash(inst)
    shutil.rmtree(prev)
    prev.mkdir(); (prev / "m").write_text("SUBSTITUTE")                 # different inode
    msgs = inst.recover_source_activations()
    assert any("substituted" in m and "recovery-required" in m for m in msgs)
    assert not dest.exists()                                            # nothing restored
    assert (prev / "m").read_text() == "SUBSTITUTE"                     # untouched
    assert inst._journal_path(dest).exists()


def test_v5_recovery_promotion_substitution_after_preproof(tmp_path, monkeypatch, make_repo, installer):
    # The candidate is swapped between the recovery pre-rename ident proof and the rename:
    # the POST-promotion re-proof (dev+ino) detects it — no foreign promotion, no cleanup, retained.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"
    staging.mkdir(); (staging / "m").write_text("CANDIDATE")
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    st = os.stat(staging, follow_symlinks=False)
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(src / ".app.prev"), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "", "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": False},
        "idents": {"candidate": [st.st_dev, st.st_ino, st.st_ctime_ns], "prev": None}}))

    def swap(_path):
        shutil.move(str(staging), str(tmp_path / "stolen"))
        staging.mkdir(); (staging / "m").write_text("FOREIGN")
    fired = _seam(monkeypatch, "pre-recovery-promote", swap)
    msgs = inst.recover_source_activations()
    assert fired["done"]
    assert any("recovery-required" in m for m in msgs)
    # the foreign leaf was moved to dest by the atomic rename? NO — post-proof detects it;
    # whatever leaf sits at dest/staging is retained, never deleted
    assert (dest / "m").read_text() == "FOREIGN" or (staging / "m").read_text() == "FOREIGN"
    assert inst._journal_path(dest).exists()                    # journal retained


def _substitute_dir(path):
    shutil.rmtree(path)
    path.mkdir()
    (path / "foreign").write_text("substitute")


def test_substitution_at_prev_delete_is_retained(tmp_path, monkeypatch, git, make_repo, installer):
    # Normal activation: `.prev` swapped between its final proof point and deletion —
    # the ident-bound remove refuses; journal retained (recovery-required), prior safe.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    prev = inst.paths.under("src", ".app.prev")
    fired = _seam(monkeypatch, "pre-prev-delete", lambda _p: _substitute_dir(prev))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (prev / "foreign").read_text() == "substitute"       # substitute retained
    dest = inst.paths.under("src", "app")
    assert inst._journal_path(dest).exists()


def test_substitution_at_quarantine_delete_is_retained(tmp_path, monkeypatch):
    # Uninstall: the QUARANTINED leaf is swapped between detach-proof and deletion — the
    # ident-bound removal refuses; the substitute is preserved at the quarantine name.
    svc, dest = _svc_env(tmp_path)

    def swap(_path):
        q = next(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
        _substitute_dir(q)
    fired = _seam(monkeypatch, "pre-quarantine-delete", swap)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok
    q = list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
    assert q and (q[0] / "foreign").read_text() == "substitute"  # evidence retained


def test_probe_level_renameat2_unsupported_refuses(tmp_path, monkeypatch, make_repo, installer):
    # The libc symbol exists but the PROBE on the actual filesystem fails: refusal before
    # any candidate/journal/source/registry mutation.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    real = source_fs._rename_noreplace_at
    def unsupported(parent_fd, old, new):
        if ".lhpc-atomic-probe-" in old:
            raise source_fs.AtomicRenameUnavailable("probe: unsupported filesystem")
        return real(parent_fd, old, new)
    monkeypatch.setattr(source_fs, "_rename_noreplace_at", unsupported)   # the probe's one seam
    monkeypatch.setattr(source_fs, "_ATOMIC_OK_DEVS", set())    # no cached positive from another test
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "unsupported" in action.detail
    dest = inst.paths.under("src", "app")
    assert not dest.exists()
    assert not inst._journal_path(dest).exists()
    assert source_registry.read_record(inst.paths, "src/app") is None


def test_a_file_added_during_staging_is_carried(tmp_path, monkeypatch, git, make_repo, installer):
    """The carry inventory is taken INSIDE the activation, so a file created after the initial
    check — while the candidate was still cloning — is still preserved. This is why the
    authoritative inventory runs after the archive rather than at the start."""
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    fired = _seam(monkeypatch, "pre-archive",
                  lambda _p: (dest / "new-user-file.txt").write_text("late"))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status != "failed", action.detail
    assert (dest / "file.txt").read_text() == "v2\n"             # new upstream active
    assert (dest / "new-user-file.txt").read_text() == "late"    # the late addition survived


def test_upstream_modified_during_staging_blocks_archive(tmp_path, monkeypatch, git, make_repo, installer):
    # A TRACKED file is edited AFTER the initial dirty check (during staging): the FINAL
    # recheck before the archive preserves the source and refuses, with zero mutation.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    fired = _seam(monkeypatch, "pre-archive",
                  lambda _p: (dest / "file.txt").write_text("late operator edit\n"))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "modified during staging" in action.detail
    assert (dest / "file.txt").read_text() == "late operator edit\n"   # the edit is untouched
    assert not dest.with_name(".app.prev").exists()              # never archived
    assert not inst._journal_path(dest).exists()


def test_dirty_file_created_before_uninstall_removal_blocks(tmp_path, monkeypatch):
    # A file created between the initial dirty check and the irreversible detach: the
    # final recheck preserves the source and returns incomplete.
    from lhpc.core.install import DirtyReport
    svc, dest = _svc_env(tmp_path)
    (dest / ".git").mkdir()                                      # dirty checks engage
    calls = {"n": 0}
    def wrapped(self, d, path):
        calls["n"] += 1
        if calls["n"] == 1:
            # the INITIAL check sees a clean tree; the file appears right after it
            (dest / "late-user-file.txt").write_text("late")
            return DirtyReport()
        return DirtyReport(untracked=("late-user-file.txt",))   # FINAL recheck: dirty
    # the dirty scan is the collaborator stubbed: on a FakeSystem `git status` answers nothing,
    # and the race window between the two scans has no seam of its own
    monkeypatch.setattr(Installer, "dirty_report", wrapped)
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("appeared before removal" in d for d in res.details)
    assert dest.exists() and (dest / "late-user-file.txt").exists()   # source preserved


def test_a_file_added_after_the_archive_is_still_carried(tmp_path, monkeypatch, git, make_repo, installer):
    """The inventory is taken through the CAPTURED prior handle after the archive, so even a
    file that lands in the tree at that last moment is carried. This is the window the
    after-archive placement exists to close."""
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    prev = dest.with_name(".app.prev")

    def late_file(_path):
        assert prev.is_dir()                              # the prior IS archived right now
        (prev / "late-user-file.txt").write_text("late")  # pathname write into the tree
    fired = _seam(monkeypatch, "post-archive", late_file)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status != "failed", action.detail
    assert (dest / "file.txt").read_text() == "v2\n"                    # new upstream active
    assert (dest / "late-user-file.txt").read_text() == "late"          # carried at the last moment
    assert not prev.exists()                                            # cleaned up normally
    assert not inst._journal_path(dest).exists()


def test_upstream_modified_after_the_archive_restores_prior(tmp_path, monkeypatch, git, make_repo, installer):
    # A TRACKED file is edited INSIDE the (unchanged) prior directory AFTER the pre-archive
    # check, once it is already archived at `.prev`: the post-archive rescan through the
    # captured handle catches it — no promotion, prior restored no-clobber at its original
    # path, the edit survives, registry/journal state stays consistent.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    rec_before = source_registry.read_record(inst.paths, "src/app")
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    prev = dest.with_name(".app.prev")

    def late_edit(_path):
        assert prev.is_dir()                              # the prior IS archived right now
        (prev / "file.txt").write_text("late operator edit\n")
    fired = _seam(monkeypatch, "post-archive", late_edit)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed"                      # truthful refusal, no false success
    assert "modified during staging" in action.detail
    assert (dest / "file.txt").read_text() == "late operator edit\n"    # OLD source restored
    assert not prev.exists()                              # nothing left archived
    assert not list(dest.parent.glob(".app.candidate-*")) # candidate NOT activated, cleaned
    assert source_registry.read_record(inst.paths, "src/app") == rec_before  # registry intact
    assert not inst._journal_path(dest).exists()          # proven restore -> journal cleared


def test_update_dirty_after_archive_unprovable_restore_is_recovery(tmp_path, monkeypatch, git, make_repo, installer):
    # Same window, but the freed destination slot is REOCCUPIED before the restore: the
    # no-clobber restore cannot land — journal + `.prev` + injected leaf are all retained.
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    prev = dest.with_name(".app.prev")

    def late_file_and_occupy(_path):
        (prev / "late-user-file.txt").write_text("late")
        dest.mkdir()                                      # inject into the freed slot
        (dest / "foreign").write_text("occupied")
    fired = _seam(monkeypatch, "post-archive", late_file_and_occupy)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (prev / "late-user-file.txt").exists()         # evidence retained at .prev
    assert (dest / "foreign").exists()                    # injected leaf untouched
    assert inst._journal_path(dest).exists()              # journal retained (recovery)


def _registered_kiss_checkout(git, make_repo, tmp_path):
    """A REAL committed kiss checkout at its managed path, registered as LHPC's own at HEAD."""
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    make_repo(dest)
    git(dest, "remote", "add", "origin", "https://github.com/makrohard/loraham-kiss-tnc.git")
    head = git(dest, "rev-parse", "HEAD")
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc",
                                       "https://github.com/makrohard/loraham-kiss-tnc.git",
                                       "pinned", head, time.time(), "",
                                       ("loraham-kiss-tnc", "loraham-kiss-serial")))
    return dest


def test_uninstall_dirty_after_detach_restores_source(tmp_path, git, make_repo, monkeypatch):
    # An untracked file lands inside the quarantined directory AFTER the pre-detach check:
    # the post-detach rescan catches it — the source is restored no-clobber at its original
    # path, the new file survives, the registry record and config stay untouched, and the
    # result is a truthful incomplete (never success).
    dest = _registered_kiss_checkout(git, make_repo, tmp_path)
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=RealSystem(), paths=paths)
    rec_before = source_registry.read_record(paths, "src/loraham-kiss-tnc")

    def late_file(path):
        q = next(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
        (q / "late-user-file.txt").write_text("late")     # pathname write post-detach
    fired = _seam(monkeypatch, "pre-quarantine-delete", late_file)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok                                     # truthful incomplete, no success
    assert any("local changes appeared" in d and "restored" in d for d in res.details)
    assert (dest / "file.txt").read_text() == "hello\n"   # source RESTORED at original path
    assert (dest / "late-user-file.txt").read_text() == "late"   # the new file SURVIVES
    assert not list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))  # nothing left behind
    assert source_registry.read_record(paths, "src/loraham-kiss-tnc") == rec_before  # record intact


def test_uninstall_dirty_after_detach_reoccupied_is_recovery(tmp_path, git, make_repo, monkeypatch):
    # Same window, but the original path is REOCCUPIED before the restore: the quarantine
    # evidence is preserved, the injected leaf untouched, the record retained — recovery.
    dest = _registered_kiss_checkout(git, make_repo, tmp_path)
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=RealSystem(), paths=paths)

    def late_and_occupy(path):
        q = next(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
        (q / "late-user-file.txt").write_text("late")
        dest.mkdir()
        (dest / "foreign").write_text("occupied")         # reoccupy the original path
    fired = _seam(monkeypatch, "pre-quarantine-delete", late_and_occupy)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok
    assert any("reoccupied" in d and "recovery" in d for d in res.details)
    q = list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
    assert q and (q[0] / "late-user-file.txt").exists()   # quarantine evidence retained
    assert (dest / "foreign").exists()                    # injected leaf untouched
    assert source_registry.read_record(paths, "src/loraham-kiss-tnc") is not None  # record retained


@pytest.mark.safety("P0.5")
def test_uninstall_still_refuses_a_tree_holding_an_added_file(tmp_path, git, make_repo):
    """The relaxation is for UPDATES only. Uninstall carries nothing forward, so an added file
    there really would be lost — it keeps the full dirty rule and refuses."""
    dest = _registered_kiss_checkout(git, make_repo, tmp_path)
    (dest / "user-data.txt").write_text("precious")        # a plain local ADDITION
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))

    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("local changes present" in d for d in res.details)
    assert (dest / "user-data.txt").read_text() == "precious"    # nothing removed


@pytest.fixture
def v2_update_env(tmp_path, git, make_repo, installer):
    """Installed v1, local advanced to v2 — ready for a force update: (comp, inst, dest, v2_head)."""
    make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    git(tmp_path / "rt" / "local" / "app", "add", "-A")
    git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    v2_head = git(tmp_path / "rt" / "local" / "app", "rev-parse", "HEAD")
    return comp, inst, inst.paths.under("src", "app"), v2_head


# ---- local additions survive a source update ---------------------------------------------------
# The rule: files the operator or the STACK ITSELF adds to a managed checkout are carried into
# the new source; changes to files that belong to UPSTREAM still refuse. Regenerable artifacts
# are neither carried nor blocking.

def _inject_on_git_copy(monkeypatch, failure):
    """Make the FIRST `.git` copy fail with `failure(src, dst)`; delegate every other call.

    A real `copytree` has already created and partly filled the destination when a nested
    entry disappears, so the injection leaves a sentinel behind: an implementation that
    forgets to remove the partial `.git` before retrying must not pass.
    """
    real = shutil.copytree
    calls = {"git": 0}

    def wrapper(src, dst, *a, **kw):
        if os.path.basename(str(src)) == ".git":
            calls["git"] += 1
            if calls["git"] == 1:
                Path(dst).mkdir(parents=True, exist_ok=True)
                (Path(dst) / "already-copied").write_text("partial")
                raise failure(str(src), str(dst))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(shutil, "copytree", wrapper)
    return calls


def _enoent(src, name="objects/ce"):
    return (f"{src}/{name}", "", f"[Errno {errno.ENOENT}] No such file or directory: '{src}/{name}'")


def test_a_repack_during_the_git_copy_does_not_fail_the_adoption(tmp_path, monkeypatch, git, make_repo, installer):
    """Git may repack the source mid-copy: loose objects are packed and their fan-out
    directories pruned between `copytree`'s listing and its read, so an entry vanishes.
    That is the same repository in a different physical representation, not a changed
    source, so the adoption must survive it."""
    repo = tmp_path / "rt" / "local" / "app"
    make_repo(repo)
    (repo / ".gitignore").write_text("settings.json\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore")
    head = git(repo, "rev-parse", "HEAD")
    (repo / "settings.json").write_text('{"mine": true}')          # ignored, still operator data

    comp = _comp()
    inst = installer(comp)
    calls = _inject_on_git_copy(
        monkeypatch, lambda src, dst: shutil.Error([_enoent(src)]))

    assert inst.adopt_source(comp, source="dev").status == "done"
    assert calls["git"] == 2                                       # the transient really fired

    dest = inst.paths.under("src", "app")
    assert (dest / "file.txt").read_text() == "hello\n"            # tracked content
    assert (dest / "settings.json").read_text() == '{"mine": true}'  # ignored content
    assert git(dest, "rev-parse", "HEAD") == head     # identity intact
    assert not (dest / ".git" / "already-copied").exists()         # partial .git was REPLACED


def test_a_mixed_copy_failure_is_not_retried_into_success(tmp_path, monkeypatch, make_repo, installer):
    """`copytree` aggregates every nested failure into one `shutil.Error`. A missing path
    beside a permission failure is NOT the benign repack: retrying it would hide a real
    copy failure behind the harmless one and activate an incomplete tree. Only an
    exclusively-ENOENT error may be retried — `all`, never `any`."""
    repo = tmp_path / "rt" / "local" / "app"
    make_repo(repo)
    comp = _comp()
    inst = installer(comp)
    calls = _inject_on_git_copy(monkeypatch, lambda src, dst: shutil.Error([
        _enoent(src),
        (f"{src}/config", "", f"[Errno {errno.EACCES}] Permission denied: '{src}/config'"),
    ]))

    assert inst.adopt_source(comp, source="dev").status == "failed"
    assert calls["git"] == 1                                       # never retried
    assert not inst.paths.under("src", "app").exists()             # nothing activated


def test_an_ignored_file_survives_an_update(tmp_path, git, make_repo, installer):
    """A stack's own settings/log file is usually `.gitignore`d — `git status` never even shows
    it. It is still the operator's data and must survive."""
    repo = tmp_path / "rt" / "local" / "app"
    make_repo(repo)
    (repo / ".gitignore").write_text("settings.json\nlogs/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore")
    comp = _comp()
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "settings.json").write_text('{"mine": true}')
    (repo / "file.txt").write_text("v2\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v2")

    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "file.txt").read_text() == "v2\n"
    assert (dest / "settings.json").read_text() == '{"mine": true}'


def test_a_nested_added_file_survives_with_its_directories_and_mode(tmp_path, v2_update_env):
    """Nested paths are reproduced whole, and the file's own permission bits come with it —
    a stack that writes an executable helper still finds it executable."""
    comp, inst, dest, _head = v2_update_env
    (dest / "logs").mkdir()
    (dest / "logs" / "history.log").write_text("line one\n")
    (dest / "run-me.sh").write_text("#!/bin/sh\necho hi\n")
    (dest / "run-me.sh").chmod(0o755)

    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "file.txt").read_text() == "v2\n"                    # new upstream
    assert (dest / "logs" / "history.log").read_text() == "line one\n"  # nested path rebuilt
    assert os.stat(dest / "run-me.sh").st_mode & 0o777 == 0o755         # mode carried


def test_an_added_file_survives_two_consecutive_updates(tmp_path, git, v2_update_env):
    """The carried file lands as an ordinary local addition, so the NEXT update inventories and
    carries it again — preservation is not a one-off."""
    repo = tmp_path / "rt" / "local" / "app"
    comp, inst, dest, _head = v2_update_env
    (dest / "notes.txt").write_text("keep me")
    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "notes.txt").read_text() == "keep me"

    (repo / "file.txt").write_text("v3\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v3")
    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "file.txt").read_text() == "v3\n"
    assert (dest / "notes.txt").read_text() == "keep me"                # survived update #2


@pytest.mark.safety("source-additions-preserved")
def test_an_added_file_colliding_with_the_new_upstream_refuses(tmp_path, git, v2_update_env):
    """Upstream has taken ownership of that pathname. LHPC does not merge, rename or pick a
    winner — it refuses and leaves the old checkout in place for the operator to resolve."""
    repo = tmp_path / "rt" / "local" / "app"
    comp, inst, dest, _head = v2_update_env
    (dest / "settings.json").write_text("mine")                         # local addition
    (repo / "settings.json").write_text("upstream now ships this\n")    # ...upstream adds it too
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v3")

    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed"
    assert "settings.json" in action.detail             # the refusal names it
    assert (dest / "settings.json").read_text() == "mine"               # local file untouched
    assert (dest / "file.txt").read_text() == "hello\n"                 # OLD source restored
    assert not dest.with_name(".app.prev").exists()
    assert not inst._journal_path(dest).exists()


def test_an_added_path_blocked_by_a_new_upstream_file_refuses(tmp_path, git, v2_update_env):
    """Parent-path collision: the local file needs `conf/` to be a directory, but the new
    upstream ships `conf` as a FILE."""
    repo = tmp_path / "rt" / "local" / "app"
    comp, inst, dest, _head = v2_update_env
    (dest / "conf").mkdir()
    (dest / "conf" / "mine.ini").write_text("local")
    (repo / "conf").write_text("upstream file, not a directory\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v3")

    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "conf" in action.detail
    assert (dest / "conf" / "mine.ini").read_text() == "local"          # old tree intact
    assert not dest.with_name(".app.prev").exists()


@pytest.mark.safety("source-additions-preserved")
def test_a_carry_failure_restores_the_prior_and_refuses(tmp_path, monkeypatch, v2_update_env):
    """If the carry cannot be PROVEN to have succeeded, the update refuses and the previous
    source stays authoritative — never a green result over lost local data."""
    comp, inst, dest, _head = v2_update_env
    (dest / "notes.txt").write_text("keep me")
    # The copy primitive's return value IS the seam: a real failure here needs ENOSPC or EIO
    # part-way through the transaction, which no injected `System` can produce.
    monkeypatch.setattr(source_fs, "carry_extras",          # the copy primitive IS the seam
                        lambda *a, **k: "notes.txt: simulated copy failure")

    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "simulated copy failure" in action.detail
    assert (dest / "file.txt").read_text() == "hello\n"                 # prior restored
    assert (dest / "notes.txt").read_text() == "keep me"                # local data intact
    assert not dest.with_name(".app.prev").exists()                     # nothing left archived
    assert not list(dest.parent.glob(".app.candidate-*"))               # candidate discarded
    assert not inst._journal_path(dest).exists()                        # transaction resolved


def test_regenerable_artifacts_neither_block_nor_are_carried(tmp_path, v2_update_env):
    """Build output is LHPC's to regenerate, not the operator's data: it must not block an
    update, and it need not survive one. Same exclusion the dirty report uses."""
    comp, inst, dest, _head = v2_update_env
    rels = [f"{d}/leftover" for d in
            (".pio", ".venv", "build", ".work", ".run", "__pycache__", "node_modules")]
    for rel in rels:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text("regenerable")

    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "file.txt").read_text() == "v2\n"                    # not blocked...
    assert [r for r in rels if (dest / r).exists()] == []               # ...and none carried


def test_a_declared_binary_is_disposable_but_a_sibling_is_carried(tmp_path, git, make_repo, installer):
    """The carve-out is the EXACT declared leaf — a file beside it is ordinary operator data."""
    repo = tmp_path / "rt" / "local" / "app"
    make_repo(repo)
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE, bin="bin/app",
                     source=SourceSpec(path="src/app", local_dir="app"))
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "bin").mkdir()
    (dest / "bin" / "app").write_text("built")                          # the declared binary
    (dest / "bin" / "helper.sh").write_text("operator helper")          # its sibling
    (repo / "file.txt").write_text("v2\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v2")

    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "file.txt").read_text() == "v2\n"
    assert not (dest / "bin" / "app").exists()                          # regenerated, not carried
    assert (dest / "bin" / "helper.sh").read_text() == "operator helper"


def test_prev_dirty_before_cleanup_is_retained_operator_only(tmp_path, monkeypatch, v2_update_env):
    # An upstream file is MODIFIED inside the archived `.prev` AFTER the post-archive recheck,
    # immediately before the cleanup: the edit survives, `.prev` stays, the journal is
    # marked prior-dirty-retained, the ACTIVE NEW source + its record stay coherent, the
    # result is truthful incomplete — and no later automatic recovery deletes the prior.
    # (An ADDED file here is not retained: the carry already reproduced it in the live tree.)
    comp, inst, dest, v2_head = v2_update_env
    prev = dest.with_name(".app.prev")

    def late_edit(_path):
        (prev / "file.txt").write_text("late operator edit\n")
    fired = _seam(monkeypatch, "pre-prev-cleanup", late_edit)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed"                       # NEVER a successful update
    assert action.detail.startswith("prior-dirty:")
    assert ".app.prev" in action.detail                    # names the retained path
    assert (prev / "file.txt").read_text() == "late operator edit\n"   # the EDIT survives
    assert (dest / "file.txt").read_text() == "v2\n"       # NEW source stays active
    rec = source_registry.read_record(inst.paths, "src/app")
    assert rec is not None and rec.resolved_commit == v2_head    # registry truthful
    jf = inst._journal_path(dest)
    assert jf.exists()
    assert json.loads(jf.read_text())["state"] == "prior-dirty-retained"
    # AUTOMATIC RECOVERY never retries the deletion — operator-only, everything retained
    for _ in range(2):
        msgs = inst.recover_source_activations()
        assert any("late local changes" in m and "recovery-required" in m for m in msgs)
        assert (prev / "file.txt").exists() and jf.exists()
    # and further source mutation stays blocked while the journal is unresolved
    blocked = inst.adopt_source(comp, force=True, source="dev")
    assert blocked.status == "failed" and "recovery-required" in blocked.detail


def test_an_lhpc_patched_checkout_still_carries_an_added_file(tmp_path, git, make_repo, installer):
    """A checkout whose only tracked change is LHPC's own declared build-time patch (openHop is
    the live case) is not operator work, and an ADDED file beside it must not resurrect that
    judgement: the patch verdict is taken from the tracked diff alone. The update proceeds and
    carries the addition; a real operator edit on top still refuses."""
    local = tmp_path / "rt" / "local" / "app"
    make_repo(local)
    (local / "a.txt").write_text("one\ntwo\n")
    git(local, "add", "-A"); git(local, "commit", "-qm", "a")
    (local / "a.txt").write_text("one\ntwo\nPATCHED\n")             # the change LHPC ships
    patch = tmp_path / "lhpc.patch"
    patch.write_text(git(local, "diff") + "\n")          # a patch ends in a newline
    git(local, "checkout", "--", "a.txt")                # upstream stays clean

    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app",
                                       patches=(str(patch),)))
    inst = installer(comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    git(dest, "apply", str(patch))                       # the build step patches it
    (dest / "notes.txt").write_text("operator data\n")                # ...and a local addition
    rep = inst.dirty_report(dest, "src/app")
    assert rep.untracked and not rep.blocks_update()    # untracked kept, but the patch is exempt
    assert rep                                          # ...and uninstall/clean still see it
    (local / "file.txt").write_text("v2\n")
    git(local, "add", "-A"); git(local, "commit", "-qm", "v2")

    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "file.txt").read_text() == "v2\n"                  # updated
    assert (dest / "notes.txt").read_text() == "operator data\n"      # addition carried
    assert (dest / "a.txt").read_text() == "one\ntwo\n"               # fresh clone, build repatches

    git(dest, "apply", str(patch))
    (dest / "a.txt").write_text("one\ntwo\nPATCHED\nOPERATOR\n")     # a REAL edit on top
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "local modifications" in action.detail


@pytest.mark.safety("source-additions-preserved")
def test_recovery_rolls_back_an_update_interrupted_before_the_carry(tmp_path, v2_update_env):
    """CRASH BETWEEN ARCHIVE AND CARRY. The journal records no carry state on purpose, so a
    recovery that finds a staged candidate beside an archived prior holding local additions
    cannot prove the candidate has them. It must therefore NOT complete the activation: it
    undoes the transaction, and the next update carries the additions again."""
    comp, inst, dest, _v2 = v2_update_env
    (dest / "notes.txt").write_text("operator data\n")
    (dest / "notes.txt").chmod(0o640)
    prev = dest.with_name(".app.prev")
    staging = dest.with_name(".app.candidate-1-2")
    shutil.move(str(dest), str(prev))                       # (2) prior archived
    shutil.copytree(str(tmp_path / "rt" / "local" / "app"), str(staging), symlinks=True)
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))

    def ident(q):
        st = os.stat(q, follow_symlinks=False)
        return [st.st_dev, st.st_ino, st.st_ctime_ns]
    jf = inst._journal_path(dest)
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({                             # ...then the process died
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "", "remote": "", "strategy": "",
                 "components": ["app"], "had_prior": True},
        "idents": {"candidate": ident(staging), "prev": ident(prev)}}))

    inst.recover_source_activations()
    assert (dest / "file.txt").read_text() == "hello\n"     # the PRIOR is active again
    assert (dest / "notes.txt").read_text() == "operator data\n"      # byte-identical
    assert os.stat(dest / "notes.txt").st_mode & 0o777 == 0o640        # and unchanged mode
    assert not staging.exists() and not prev.exists()       # transaction fully undone
    assert not jf.exists()                                  # ...and resolved, not stuck
    # the box is not blocked: the retried update succeeds and carries the addition
    assert inst.adopt_source(comp, force=True, source="dev").status != "failed"
    assert (dest / "file.txt").read_text() == "v2\n"
    assert (dest / "notes.txt").read_text() == "operator data\n"


@pytest.mark.safety("source-additions-preserved")
def test_a_substituted_active_source_retains_the_archive(tmp_path, monkeypatch, v2_update_env):
    """What licenses destroying `.prev` is that the ACTIVE tree carries everything the archive
    held — proven about one inode. If the destination is swapped between that proof and the
    delete, the licence belonged to a tree that is no longer there, so the archive stays.
    In-process the authority is the retained candidate handle (dev+ino: the fd pins the inode)."""
    comp, inst, dest, _v2 = v2_update_env
    (dest / "notes.txt").write_text("operator data\n")
    prev = dest.with_name(".app.prev")

    def swap_dest(_path):
        os.rename(dest, dest.with_name(".app.displaced"))      # our candidate steps aside...
        dest.mkdir()
        (dest / "planted").write_text("A DIFFERENT TREE\n")     # ...a foreign one takes the name
    fired = _seam(monkeypatch, "pre-prev-delete", swap_dest)

    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed"                           # never a clean result
    assert prev.is_dir()                                       # the ARCHIVE IS RETAINED
    assert (prev / "notes.txt").read_text() == "operator data\n"   # ...with the operator's data
    assert (dest / "planted").read_text() == "A DIFFERENT TREE\n"  # substitute untouched
    assert inst._journal_path(dest).exists()                   # transaction retained


@pytest.mark.safety("source-additions-preserved")
def test_recovery_retains_the_archive_when_the_active_source_was_substituted(tmp_path, monkeypatch, v2_update_env):
    """The same boundary under crash RECOVERY, where the authority is the journal's full v5
    candidate ident (the promotion rename refreshed it, so it describes the active leaf)."""
    comp, inst, dest, v2_head = v2_update_env
    prev = dest.with_name(".app.prev")
    # Craft the interruption: record complete, activation done, `.prev` still archived.
    shutil.move(str(dest), str(prev))
    (prev / "notes.txt").write_text("operator data\n")
    shutil.copytree(str(tmp_path / "rt" / "local" / "app"), str(dest), symlinks=True)
    (dest / "notes.txt").write_text("operator data\n")         # the carry already ran
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    staging_rel = rel(dest.with_name(".app.candidate-1-2"))

    def ident(q):
        st = os.stat(q, follow_symlinks=False)
        return [st.st_dev, st.st_ino, st.st_ctime_ns]
    jf = inst._journal_path(dest)
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": staging_rel,
        "txn_id": inst._txn_id(staging_rel),
        "meta": {"selector": "dev", "resolved_commit": v2_head, "remote": "", "strategy": "",
                 "components": ["app"], "had_prior": True},
        "idents": {"candidate": ident(dest), "prev": ident(prev)}}))

    def swap_dest(_path):
        os.rename(dest, dest.with_name(".app.displaced"))
        dest.mkdir()
        (dest / "planted").write_text("A DIFFERENT TREE\n")
    fired = _seam(monkeypatch, "pre-prev-delete", swap_dest)

    msgs = inst.recover_source_activations()
    assert fired["done"]
    assert any("recovery-required" in m for m in msgs), msgs
    assert prev.is_dir() and (prev / "notes.txt").read_text() == "operator data\n"
    assert (dest / "planted").read_text() == "A DIFFERENT TREE\n"   # substitute untouched
    assert jf.exists()                                              # journal retained


@pytest.mark.safety("source-additions-preserved")
def test_recovery_retains_a_candidate_whose_carry_had_already_started(tmp_path, v2_update_env):
    """CRASH *DURING* THE CARRY. The journal records the candidate's identity at
    `prior-archived` — before the carry — and the carry then writes the additions INTO that
    candidate, moving its ctime. Recovery therefore can no longer prove the candidate, and the
    v5 rule is that an unprovable leaf is RETAINED, never destroyed: dev+ino alone is forgeable
    through inode recycling (see the `.prev` twin below), and unlike the post-rename re-proofs
    this recovery never proved all three fields itself.

    So this interruption is fail-closed rather than auto-rolled-back: nothing is lost, the
    operator resolves it. Found by SIGKILLing a real update during a 3 GB carry."""
    comp, inst, dest, _v2 = v2_update_env
    (dest / "notes.txt").write_text("operator data\n")
    prev = dest.with_name(".app.prev")
    staging = dest.with_name(".app.candidate-1-2")
    shutil.move(str(dest), str(prev))
    shutil.copytree(str(tmp_path / "rt" / "local" / "app"), str(staging), symlinks=True)
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))

    def ident(q):
        st = os.stat(q, follow_symlinks=False)
        return [st.st_dev, st.st_ino, st.st_ctime_ns]
    idents = {"candidate": ident(staging), "prev": ident(prev)}   # recorded BEFORE the carry
    (staging / "notes.txt").write_text("operator")                # the carry starts: partial copy
    assert ident(staging)[2] != idents["candidate"][2], "the carry must move the ctime"

    jf = inst._journal_path(dest)
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "", "remote": "", "strategy": "",
                 "components": ["app"], "had_prior": True},
        "idents": idents}))

    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m for m in msgs), msgs
    assert not dest.exists()                              # never promoted over the additions
    assert staging.is_dir()                               # the candidate is EVIDENCE, not rubbish
    assert jf.exists()                                    # transaction retained for the operator
    # ...and NOTHING IS LOST: the archived prior still holds the whole source and the addition.
    assert (prev / "file.txt").read_text() == "hello\n"
    assert (prev / "notes.txt").read_text() == "operator data\n"


@pytest.mark.safety("source-additions-preserved")
def test_recovery_never_deletes_a_candidate_on_a_recycled_inode(tmp_path, v2_update_env):
    """The candidate twin of `test_v5_inode_recycling_forged_ctime_prior_not_restored`, and the
    reason the ctime may not be dropped from the deletion above: a DIFFERENT directory that has
    taken the candidate's hidden pathname and been handed its recycled dev+ino is
    indistinguishable from the real candidate by inode alone. Only the ctime separates them, and
    recovery is about to delete that leaf recursively."""
    comp, inst, dest, _v2 = v2_update_env
    (dest / "notes.txt").write_text("operator data\n")     # additions -> the carry is unprovable
    prev = dest.with_name(".app.prev")
    staging = dest.with_name(".app.candidate-1-2")
    shutil.move(str(dest), str(prev))
    staging.mkdir()
    (staging / "not-ours").write_text("A DIFFERENT TREE ON THE RECYCLED INODE\n")
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))

    def ident(q):
        st = os.stat(q, follow_symlinks=False)
        return [st.st_dev, st.st_ino, st.st_ctime_ns]
    real = ident(staging)
    forged = [real[0], real[1], real[2] - 1]              # SAME dev+ino, different ctime
    jf = inst._journal_path(dest)
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "", "remote": "", "strategy": "",
                 "components": ["app"], "had_prior": True},
        "idents": {"candidate": forged, "prev": ident(prev)}}))

    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m for m in msgs), msgs
    assert (staging / "not-ours").read_text() == "A DIFFERENT TREE ON THE RECYCLED INODE\n"
    assert not dest.exists() and jf.exists()             # everything retained as evidence
    assert (prev / "notes.txt").read_text() == "operator data\n"


@pytest.mark.safety("source-additions-preserved")
def test_recovery_of_an_unprovable_prior_touches_nothing(tmp_path, v2_update_env):
    """Same interruption, but the archived prior is SUBSTITUTED before recovery runs. Recovery
    must decide nothing from a tree it cannot prove is the journal's own: no inventory of it, no
    deletion of the staged candidate, no restore — everything retained as evidence."""
    comp, inst, dest, _v2 = v2_update_env
    (dest / "notes.txt").write_text("operator data\n")
    prev = dest.with_name(".app.prev")
    staging = dest.with_name(".app.candidate-1-2")
    shutil.move(str(dest), str(prev))
    shutil.copytree(str(tmp_path / "rt" / "local" / "app"), str(staging), symlinks=True)
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))

    def ident(q):
        st = os.stat(q, follow_symlinks=False)
        return [st.st_dev, st.st_ino, st.st_ctime_ns]
    jf = inst._journal_path(dest)
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "", "remote": "", "strategy": "",
                 "components": ["app"], "had_prior": True},
        "idents": {"candidate": ident(staging), "prev": ident(prev)}}))
    # SUBSTITUTE the archived prior: same name, different inode than the journal recorded.
    shutil.move(str(prev), str(prev.with_name(".app.prev.real")))
    prev.mkdir()
    (prev / "planted").write_text("not the recorded prior\n")

    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "archived prior" in m for m in msgs), msgs
    assert staging.is_dir() and (staging / "file.txt").exists()   # candidate NOT deleted
    assert (prev / "planted").exists()                            # substitute untouched
    assert not dest.exists()                                      # nothing restored or promoted
    assert jf.exists()                                            # journal retained as evidence
    assert (prev.with_name(".app.prev.real") / "notes.txt").read_text() == "operator data\n"


@pytest.mark.safety("source-additions-preserved")
def test_an_addition_made_after_the_carry_retains_the_archived_prior(tmp_path, monkeypatch, v2_update_env):
    """The `.prev` cleanup destroys the archive, so it runs only once every local addition the
    archive still holds is PROVEN present in the activated source. A file that appears in `.prev`
    after the carry is in neither — it must never be deleted as collateral."""
    comp, inst, dest, v2_head = v2_update_env
    (dest / "notes.txt").write_text("carried\n")
    prev = dest.with_name(".app.prev")

    def late_add(_path):
        (prev / "late.log").write_text("written after the carry\n")
    fired = _seam(monkeypatch, "pre-prev-cleanup", late_add)
    action = inst.adopt_source(comp, force=True, source="dev")

    assert fired["done"]
    assert action.status == "failed"                        # never reported as a clean update
    assert action.detail.startswith("prior-dirty:")
    assert "late.log" in action.detail                      # names the file that is at risk
    assert (prev / "late.log").read_text() == "written after the carry\n"   # NOT deleted
    assert prev.is_dir()                                    # the archive is retained whole
    assert (dest / "file.txt").read_text() == "v2\n"        # the new source stays active...
    assert (dest / "notes.txt").read_text() == "carried\n"   # ...with the carried addition
    jf = inst._journal_path(dest)
    assert json.loads(jf.read_text())["state"] == "prior-dirty-retained"
    # automatic recovery never retries the deletion either — the journal is operator-only now
    # (the seam hook above fires exactly once, so recovery runs with the real cleanup path)
    msgs = inst.recover_source_activations()
    assert any("late local changes" in m and "recovery-required" in m for m in msgs), msgs
    assert (prev / "late.log").exists() and jf.exists()


def test_prev_dirty_during_recovery_cleanup_is_retained(tmp_path, monkeypatch, v2_update_env):
    # Interrupted activation (journal 'activated', record complete, `.prev` still present):
    # an upstream file MODIFIED inside `.prev` right before RECOVERY's cleanup marks the
    # transaction prior-dirty-retained — recovery completes nothing destructive, all retained.
    comp, inst, dest, v2_head = v2_update_env
    prev = dest.with_name(".app.prev")
    # Build the crash state MANUALLY (a real run removes .prev before the journal, so the
    # needed interruption point — record written, .prev still archived — is crafted):
    # dest = the NEW v2 tree, .prev = the archived v1 prior, journal v4 'activated'.
    shutil.move(str(dest), str(prev))                      # archive the v1 prior
    shutil.copytree(str(tmp_path / "rt" / "local" / "app"), str(dest), symlinks=True)
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    staging_rel = rel(dest.with_name(".app.candidate-1-2"))

    def ident(q):
        st = os.stat(q, follow_symlinks=False)
        return [st.st_dev, st.st_ino, st.st_ctime_ns]   # v5 ctime-hardened ident
    jf = inst._journal_path(dest)
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": staging_rel,
        "txn_id": inst._txn_id(staging_rel),
        "meta": {"selector": "dev", "resolved_commit": v2_head, "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": True},
        "idents": {"candidate": ident(dest), "prev": ident(prev)}}))
    assert jf.exists() and prev.is_dir()                   # archived prior + journal remain
    assert (dest / "file.txt").read_text() == "v2\n"       # new source already active

    def late_edit(_path):
        (prev / "file.txt").write_text("late operator edit\n")   # TRACKED change in the prior
    fired = _seam(monkeypatch, "pre-prev-cleanup", late_edit)
    msgs = inst.recover_source_activations()
    assert fired["done"]
    assert any("late local changes" in m and "recovery-required" in m for m in msgs)
    assert (prev / "file.txt").read_text() == "late operator edit\n"   # the edit survives
    assert prev.is_dir()                                   # `.prev` retained
    assert json.loads(jf.read_text())["state"] == "prior-dirty-retained"
    rec = source_registry.read_record(inst.paths, "src/app")
    assert rec is not None and rec.resolved_commit == v2_head    # active record truthful
    # a SECOND automatic recovery still refuses to delete the dirty prior (the seam hook has
    # already fired its once; nothing is injected any more)
    msgs2 = inst.recover_source_activations()
    assert any("late local changes" in m for m in msgs2)
    assert (prev / "file.txt").exists() and jf.exists()


def test_clean_prev_cleanup_still_succeeds_when_not_dirty(tmp_path, v2_update_env):
    # Sanity: an update whose archived prior stays clean completes exactly as before —
    # `.prev` removed, journal cleared, record updated.
    comp, inst, dest, v2_head = v2_update_env
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "done", action.detail
    assert not dest.with_name(".app.prev").exists()
    assert not inst._journal_path(dest).exists()
    assert source_registry.read_record(inst.paths, "src/app").resolved_commit == v2_head


def test_symlinked_source_is_absent_for_every_operation(tmp_path):
    """ONE presence policy (`source_fs.source_present`): a symlink at a managed source path is
    not a managed source. Status, the source check, the install plan, build, test and start all
    say so; nothing follows the link — no git, no build, no log is ever run through it."""
    outside = tmp_path / "outside" / "loraham-daemon"
    (outside / ".git").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    link = tmp_path / "src" / "loraham-daemon"
    link.symlink_to(outside)
    svc = _svc(tmp_path, {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(link, A)})
    assert not svc.is_installed("daemon")
    st = svc.build_snapshot(fresh=True).stacks
    comp = next(ss for ss in st if ss.stack.id == "daemon").components["loraham-daemon"]
    assert comp.source_state is SourceState.MISSING
    res = svc.source_check("daemon")
    assert not res.ok and res.data["counts"][su.UP_TO_DATE] == 0
    plan = svc.install("daemon", apply=False)
    assert any("unexpected symlink leaf" in d for d in plan.details), plan.details
    for op in ("build", "test", "start"):
        r = getattr(svc, op)("daemon", apply=True)
        assert not r.ok, (op, r.summary)
        assert "not installed" in " ".join([r.summary or ""] + list(r.details or [])).lower(), (op, r.summary, r.details)
    assert not list((tmp_path / "logs").glob("*loraham-daemon*"))               # no log through the link
    assert not any(str(link) in " ".join(c) for c in svc._system.runner.calls)   # nothing through the link
    assert link.is_symlink() and outside.is_dir()                                # untouched


def test_detach_and_remove_clears_a_checkout_holding_a_runtime_socket(tmp_path):
    """The verified-removal protocol must finish for a checkout the stack RAN from (runtime
    socket + FIFO inside it): the quarantine is this transaction's own leaf, so IPC leaves are
    deleted with it — otherwise the removal aborted after the detach and left a quarantine
    that blocked every retry."""
    paths = Paths(runtime_root=tmp_path)
    leaf = tmp_path / "src" / "app"
    (leaf / ".run").mkdir(parents=True)
    (leaf / "README.md").write_text("x")
    _bind_unix_socket(leaf / ".run", "gps.sock")
    os.mkfifo(leaf / ".run" / "fifo")
    handle = source_fs.capture_leaf(paths, leaf)
    try:
        ok, why = source_fs.detach_and_remove(paths, leaf, handle)
    finally:
        handle.close()
    assert ok, why
    assert not leaf.exists() and not list((tmp_path / "src").iterdir())    # no quarantine left


def _repo_with_lhpc_patch(git, make_repo, repo: Path):
    """A committed checkout whose ONLY working-tree change is an applied patch file (the shape a
    build-time `openhop-apply-patch.sh` leaves behind). Returns (head, patch_path)."""
    head = make_repo(repo, {"a.txt": "one\n", "b.txt": "keep\n"})
    (repo / "a.txt").write_text("one\ntwo\n")
    patch = repo.parent / "lhpc.patch"
    patch.write_text(git(repo, "diff") + "\n")                        # a patch ends in a newline
    git(repo, "checkout", "--", "a.txt")
    git(repo, "apply", str(patch))
    return head, patch


def test_probe_reports_an_lhpc_patched_tree_as_clean(tmp_path, git, make_repo):
    """A checkout whose only modifications are LHPC's own build-time patch is not dirty: status
    says MATCH (so known-working can confirm it), and the version drops `-dirty`. Without the
    declared patch, or with one extra edit, it is dirty as before."""
    from lhpc.core.probes.source import probe_source
    repo = tmp_path / "src" / "app"
    head, patch = _repo_with_lhpc_patch(git, make_repo, repo)
    sysx = RealSystem()
    plain = probe_source(sysx, SourceSpec(path="src/app", pin_commit=head), str(repo))
    assert plain.state is SourceState.DIRTY
    p = probe_source(sysx, SourceSpec(path="src/app", pin_commit=head, patches=(str(patch),)), str(repo))
    assert p.state is SourceState.MATCH and p.evidence.get("patched") == "lhpc"
    assert not p.version.endswith("-dirty")
    (repo / "b.txt").write_text("keep\nedited\n")                      # an operator edit on top
    p2 = probe_source(sysx, SourceSpec(path="src/app", pin_commit=head, patches=(str(patch),)), str(repo))
    assert p2.state is SourceState.DIRTY


def test_dirty_report_ignores_the_shipped_patch_only(tmp_path, git, make_repo, installer):
    """The update's overwrite gate: a tree carrying exactly the declared patch is not "local
    modifications"; any other tracked change still refuses the overwrite."""
    inst = installer(_comp())
    dest = inst.paths.under("src", "app")
    head, patch = _repo_with_lhpc_patch(git, make_repo, dest)
    assert inst.dirty_report(dest, "src/app")                          # undeclared patch: dirty
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app", patches=(str(patch),)))
    inst = installer(comp)
    assert not inst.dirty_report(dest, "src/app")                      # exactly the patch: clean
    (dest / "a.txt").write_text("one\ntwo\nthree\n")                     # an extra hunk
    assert inst.dirty_report(dest, "src/app")

