"""The console's source-probe cache (`lhpc/core/source_cache.py`): a checkout's git answer is reused for at
most 30 s while its git metadata and the directories of its tracked files are unchanged, and never by a
recheck under the operation locks.

Real git in temporary directories (the `git` / `make_repo` fixtures); git runs are counted at the runner,
and the clock is the cache's own, injected.
"""
from __future__ import annotations

import os

import pytest

from lhpc.core import source_cache
from lhpc.core.model import SourceSpec, SourceState
from lhpc.core.probes.backends import RealSystem


class _Counting:
    """A RealSystem whose runner counts the git commands it runs."""

    def __init__(self):
        self.system = RealSystem()
        self.git = []
        real = self.system.runner.run

        def run(argv, timeout, cwd=None, env=None):
            if argv[:1] == ["git"]:
                self.git.append(argv[3] if len(argv) > 3 else "")
            return real(argv, timeout, cwd=cwd, env=env)
        self.system.runner.run = run


@pytest.fixture
def world(tmp_path, make_repo, git):
    repo = tmp_path / "src" / "app"
    head = make_repo(repo, {"f": "a\n", "d/h": "c\n"})
    cache = source_cache.SourceCache()
    now = [100.0]
    cache.clock = lambda: now[0]
    counted = _Counting()
    spec = SourceSpec(path="src/app", pin_commit=head)

    def probe():
        return source_cache.probe_cached(cache, counted.system, spec, str(repo))
    return repo, cache, now, counted, probe, git


def _status_runs(counted):
    return counted.git.count("status")


def test_a_second_read_within_30_s_runs_no_git(world):
    repo, cache, now, counted, probe, _git = world
    first = probe()
    now[0] += 29.0
    second = probe()
    assert (first.state, second.state) == (SourceState.MATCH, SourceState.MATCH)
    assert second.head == first.head and second.version == first.version
    assert _status_runs(counted) == 1


def test_after_30_s_it_probes_again(world):
    repo, cache, now, counted, probe, _git = world
    probe()
    now[0] += 30.5
    probe()
    assert _status_runs(counted) == 2


@pytest.mark.parametrize("change", ["commit", "stage", "checkout", "packed-refs", "new file in a tracked dir",
                                    "deleted file", "renamed file", "new file at the top"])
def test_a_change_to_the_key_probes_again(world, change):
    repo, cache, now, counted, probe, git = world
    probe()
    if change == "commit":
        (repo / "f").write_text("b\n")
        git(repo, "commit", "-qam", "two")
    elif change == "stage":
        (repo / "f").write_text("b\n")
        git(repo, "add", "f")
    elif change == "checkout":
        git(repo, "checkout", "-q", "-b", "other")
    elif change == "packed-refs":
        git(repo, "tag", "v1")
        git(repo, "pack-refs", "--all")
    elif change == "new file in a tracked dir":
        (repo / "d" / "new").write_text("n\n")
    elif change == "deleted file":
        (repo / "d" / "h").unlink()
    elif change == "renamed file":
        os.rename(repo / "d" / "h", repo / "d" / "h2")
    else:
        (repo / "top").write_text("t\n")
    now[0] += 1.0
    probe()
    assert _status_runs(counted) == 2


def test_a_hand_edit_inside_an_unchanged_directory_shows_within_30_s(world):
    # The stated residual window: an in-place edit of an existing file changes no key part.
    repo, cache, now, counted, probe, _git = world
    assert probe().state is SourceState.MATCH
    st = (repo / "d").stat()
    (repo / "d" / "h").write_text("edited\n")
    os.utime(repo / "d", ns=(st.st_atime_ns, st.st_mtime_ns))     # the directory is left unchanged
    now[0] += 10.0
    assert probe().state is SourceState.MATCH                      # still the cached answer
    now[0] += 21.0
    assert probe().state is SourceState.DIRTY                      # within 30 s of the first probe


def test_clear_drops_every_entry(world):
    repo, cache, now, counted, probe, _git = world
    probe()
    cache.clear()
    probe()
    assert _status_runs(counted) == 2


def test_a_failed_probe_is_not_kept(world, monkeypatch):
    repo, cache, now, counted, probe, _git = world
    real = counted.system.runner.run

    def failing_status(argv, timeout, cwd=None, env=None):
        if argv[3:4] == ["status"] and not counted.git.count("status"):
            counted.git.append("status")
            from lhpc.core.probes.backends import CommandResult
            return CommandResult(returncode=124, stdout="", stderr="", timed_out=True)
        return real(argv, timeout, cwd=cwd, env=env)
    counted.system.runner.run = failing_status
    assert probe().state is SourceState.UNKNOWN
    assert probe().state is SourceState.MATCH                      # retried, not served from the cache


@pytest.mark.parametrize("shape", ["symlinked .git", "git file", "no checkout"])
def test_a_checkout_without_a_plain_git_directory_is_never_cached(world, tmp_path, shape):
    repo, cache, now, counted, probe, _git = world
    target = tmp_path / "elsewhere.git"
    if shape == "symlinked .git":
        os.rename(repo / ".git", target)
        os.symlink(target, repo / ".git")
    elif shape == "git file":
        os.rename(repo / ".git", target)
        (repo / ".git").write_text(f"gitdir: {target}\n")
    else:
        import shutil
        shutil.rmtree(repo)
    probe()
    probe()
    assert source_cache.meta_key(str(repo)) is None
    assert len(cache) == 0
    assert _status_runs(counted) == (0 if shape == "no checkout" else 2)   # git on every read, or none


def test_a_mutating_service_entry_clears_the_cache_on_entry_and_exit():
    from lhpc.core.snapshot_memo import invalidates_snapshot
    seen = []

    class Svc:
        def invalidate_snapshot(self):
            pass

        @invalidates_snapshot
        def change(self):
            seen.append(len(source_cache.SHARED))
            source_cache.SHARED.put("/x", "", (b"",), (), (), 0.0, object(), source_cache.SHARED.generation())
    source_cache.SHARED.put("/y", "", (b"",), (), (), 0.0, object(), source_cache.SHARED.generation())
    Svc().change()
    assert seen == [0] and len(source_cache.SHARED) == 0


def test_a_recheck_never_uses_the_cache_and_a_page_read_does(tmp_path, monkeypatch):
    # `fresh=True` is the authoritative recheck under the operation locks: it neither reads nor fills
    # the process cache; an ordinary snapshot passes the process cache to every source probe.
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import CommandResult, FakeSystem
    from lhpc.core.services import ControllerService
    src = str(tmp_path / "src" / "meshcore-webui")
    a = "a" * 40
    fake = FakeSystem(paths={src, src + "/.git"}, commands={
        ("git", "-C", src, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
            CommandResult(0, f"# branch.oid {a}\n", ""),
        ("git", "-C", src, "describe", "--tags", "--always"): CommandResult(0, "v1\n", "")})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    caches = []
    real = source_cache.probe_cached
    monkeypatch.setattr(source_cache, "probe_cached",
                        lambda cache, *a, **k: (caches.append(cache), real(cache, *a, **k))[1])
    svc.build_snapshot(fresh=True)
    assert caches and all(c is None for c in caches)
    caches.clear()
    svc.invalidate_snapshot()
    svc.build_snapshot()
    assert caches and all(c is source_cache.SHARED for c in caches)


def test_two_page_reads_of_an_unchanged_checkout_run_git_once(tmp_path, make_repo):
    # Through the service, as the console reads: one snapshot per request, the request memo dropped
    # between them. Before the cache every request ran git for every installed checkout.
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService
    repo = tmp_path / "src" / "meshcore-webui"
    make_repo(repo, {"app/main.py": "x = 1\n"})
    counted = _Counting()
    svc = ControllerService(system=counted.system, paths=Paths(runtime_root=tmp_path))
    svc.build_snapshot()
    svc.invalidate_snapshot()                                     # the next request starts
    svc.build_snapshot()
    assert _status_runs(counted) == 1


def test_a_probe_in_flight_during_a_mutation_is_not_kept(world):
    # A page probe that started before a console mutation must not store its answer after the
    # mutation cleared the cache: with an in-place edit the key is unchanged, so the stale MATCH would
    # be served for up to 30 s. The probe finishing late is played in order here: it runs, then the
    # mutation clears the cache and edits a file in place, then the probe returns.
    repo, cache, now, counted, probe, _git = world
    from lhpc.core.probes.source import probe_source

    def probe_then_mutation(system, spec, abs_path, git=True):
        got = probe_source(system, spec, abs_path, git=git)        # the old answer: MATCH
        cache.clear()                                               # a mutating service call
        st = (repo / "d").stat()
        (repo / "d" / "h").write_text("edited\n")
        os.utime(repo / "d", ns=(st.st_atime_ns, st.st_mtime_ns))  # the cheap key cannot see it
        return got
    spec = SourceSpec(path="src/app", pin_commit=_git(repo, "rev-parse", "HEAD"))
    old = source_cache.probe_cached(cache, counted.system, spec, str(repo), probe=probe_then_mutation)
    assert old.state is SourceState.MATCH and len(cache) == 0     # not kept
    assert probe().state is SourceState.DIRTY                      # the next read runs git


def test_a_hit_read_across_a_mutation_is_not_served(world, monkeypatch):
    # A page read that fetched a cached entry must not return it when a console mutation cleared the cache
    # (and edited a file in place) between the fetch and the return. Played in order: the read fetches the
    # entry, the mutation runs while the read checks the directories, then the read decides.
    repo, cache, now, counted, probe, _git = world
    assert probe().state is SourceState.MATCH                      # stored
    real_dir_key = source_cache._dir_key

    def mutation_during_the_check(dirs):
        cache.clear()                                               # a mutating service call
        st = (repo / "d").stat()
        (repo / "d" / "h").write_text("edited\n")
        os.utime(repo / "d", ns=(st.st_atime_ns, st.st_mtime_ns))  # the cheap key cannot see it
        monkeypatch.setattr(source_cache, "_dir_key", real_dir_key)
        return real_dir_key(dirs)
    monkeypatch.setattr(source_cache, "_dir_key", mutation_during_the_check)
    now[0] += 1.0
    assert probe().state is SourceState.DIRTY                      # not the old MATCH: git ran
    assert _status_runs(counted) == 2
