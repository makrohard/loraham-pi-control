"""Advancing the checkout: what `lhpc self-update` reports, what it refuses, and what an
operator-driven apply does to the running console.

Divergence, a dirty tree and untracked files are all REFUSALS with an explicit override, and the
override has to disclose exactly what it would discard — an operator who cannot see that is being
asked to consent to nothing. The two behaviours that grew out of this one live next door:
`test_selfupdate_apply.py` (the overwrite's cleanup, the firewall follow-up, the lock) and
`test_selfupdate_service.py` (the one-click updater the web console triggers).
"""
from __future__ import annotations

import os
import shlex
import sys

import pytest

import gitrepo
from selfupdate_fakes import WEB_INACTIVE, frozen_budget
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult

from pathlib import Path

# --- local state + upstream check --------------------------------------------------------------

def test_local_state_reads_head_branch_clean(env):
    st = selfupdate.local_state(env["sys"])
    assert st["is_git"] and st["branch"] == "main" and st["dirty"] is False
    assert st["head"] and st["head_short"] == st["head"][:9]


def test_check_upstream_up_to_date(env):
    up = selfupdate.check_upstream(env["sys"])
    local = selfupdate.local_state(env["sys"])
    assert up["ok"] and up["upstream_head"] == local["head"]
    assert up["upstream_version"] == selfupdate.__version__ and up["deps_changed"] is False


def test_status_up_to_date_is_green(env):
    selfupdate.refresh_cache(env["sys"], env["paths"])
    v = selfupdate.status_view(env["paths"])
    assert v["ver_color"] == "green" and v["commit_color"] == "green"
    assert v["update_available"] is False and v["available"] is True


def test_status_commit_ahead_same_version_is_yellow(env):
    gitrepo.upstream_commit(env["up"])                               # new commit, version unchanged
    selfupdate.refresh_cache(env["sys"], env["paths"])
    v = selfupdate.status_view(env["paths"])
    assert v["ver_color"] == "green"          # version unchanged stays green
    assert v["commit_color"] == "yellow"      # only the commit changed
    assert v["update_available"] is True


def test_status_version_ahead_is_red(env):
    # Upstream must be STRICTLY newer than whatever we currently ship — derive it from the
    # live version so a release bump (e.g. 0.1.17 -> 0.2.0) never turns this red case green.
    major, minor, _patch = (int(x) for x in selfupdate.__version__.split("."))
    newer = f"{major}.{minor + 1}.0"
    gitrepo.upstream_commit(env["up"], version=newer, touch_pyproject=True)
    selfupdate.refresh_cache(env["sys"], env["paths"])
    v = selfupdate.status_view(env["paths"])
    assert v["ver_color"] == "red" and v["commit_color"] == "red"
    assert v["update_available"] is True and v["upstream_version"] == newer
    assert v["deps_changed"] is True          # pyproject changed -> pip hint


def test_status_view_no_check_is_grey(env, monkeypatch):
    # Never refreshed: status_view is CACHED-ONLY, so it must NOT probe the live checkout to
    # decide availability — repo_root raising proves the GET path never calls it.
    monkeypatch.setattr(selfupdate, "repo_root",
                        lambda: (_ for _ in ()).throw(AssertionError("live repo_root on a GET")))
    v = selfupdate.status_view(env["paths"])                  # never refreshed
    assert v["ver_color"] == "grey" and v["commit_color"] == "grey"
    assert v["update_available"] is False and v["version"] == selfupdate.__version__
    # No cache yet -> availability is unknown/unavailable until a refresh writes local.is_git;
    # it does NOT fall back to a live .git probe.
    assert v["available"] is False and v["is_git"] is False


def test_status_view_cache_without_is_git_is_unavailable(env, monkeypatch):
    # A cache whose `local` lacks `is_git` renders unavailable (never a live fallback).
    monkeypatch.setattr(selfupdate, "repo_root",
                        lambda: (_ for _ in ()).throw(AssertionError("live repo_root on a GET")))
    selfupdate.write_cache(env["paths"], {"schema_version": 1,
                                          "local": {"head": "a" * 40, "head_short": "aaaaaaaaa",
                                                    "branch": "main"},
                                          "upstream": {}, "checked_at": 1})
    v = selfupdate.status_view(env["paths"])
    assert v["available"] is False and v["is_git"] is False and v["update_available"] is False


# --- apply ------------------------------------------------------------------------------------

def test_apply_fast_forward_clean(env):
    new_head = gitrepo.upstream_commit(env["up"])
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] and not res.get("already")
    assert selfupdate.local_state(env["sys"])["head"] == new_head   # working clone fast-forwarded
    # cache refreshed -> now up to date/green
    assert selfupdate.status_view(env["paths"])["commit_color"] == "green"


def test_apply_already_up_to_date(env):
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] and res["already"] is True


def test_apply_refuses_dirty_by_default_then_force(env):
    new_head = gitrepo.upstream_commit(env["up"])
    (env["work"] / "lhpc" / "version.py").write_text('__version__ = "0.1.1"\n# local edit\n')  # dirty
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False and res["dirty"] is True          # default: do NOT overwrite
    assert selfupdate.local_state(env["sys"])["head"] != new_head
    forced = selfupdate.apply_update(env["sys"], env["paths"], force=True)   # opt in to overwrite
    assert forced["ok"] and selfupdate.local_state(env["sys"])["head"] == new_head
    assert "local edit" not in (env["work"] / "lhpc" / "version.py").read_text()   # discarded


@pytest.mark.contract
def test_apply_diverged_history_refused_without_force(env):
    gitrepo.upstream_commit(env["up"])                                # origin/main advances
    (env["work"] / "diverge.txt").write_text("local commit\n")  # local commit -> diverged
    gitrepo.git(env["work"], "add", "-A")
    gitrepo.git(env["work"], "commit", "-m", "local")
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False
    assert "\n" not in res["message"]                          # single clean line (no raw git block)
    # The refusal must carry its OWN remedy: a diverged history can only be resolved by the reset
    # path, and a dead-end refusal left the operator with git surgery (live-found on a Zero).
    assert res.get("needs_overwrite") is True


@pytest.mark.contract
def test_only_proven_divergence_offers_the_destructive_overwrite(env, monkeypatch):
    """`--overwrite` runs `reset --hard` + `clean -ffd`. Offering it for a lock file, a read-only
    filesystem or a corrupt index would destroy untracked work without repairing the fault, so the
    offer requires ancestry-proven divergence."""
    gitrepo.upstream_commit(env["up"])
    real_git = selfupdate._git

    def merge_fails(system, root, args, timeout):
        if args[:1] == ["merge"]:
            return CommandResult(128, "", "fatal: Unable to create '.git/index.lock': File exists.")
        return real_git(system, root, args, timeout)

    monkeypatch.setattr(selfupdate, "_git", merge_fails)
    monkeypatch.setattr(selfupdate, "ff_blocked", lambda system, branch="": False)
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False
    assert not res.get("needs_overwrite")                       # not a divergence -> no offer
    assert "index.lock" in res["message"]                       # the injected fault survives sanitisation

    monkeypatch.setattr(selfupdate, "ff_blocked", lambda system, branch="": True)
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False and res.get("needs_overwrite") is True


@pytest.mark.contract
def test_failed_forced_reset_is_not_reported_as_divergence(env, monkeypatch):
    """The forced path already IS the reset — its failure must not recommend the reset again."""
    gitrepo.upstream_commit(env["up"])
    real_git = selfupdate._git

    def reset_fails(system, root, args, timeout):
        if args[:1] == ["reset"]:
            return CommandResult(1, "", "error: unable to write file (No space left on device)")
        return real_git(system, root, args, timeout)

    monkeypatch.setattr(selfupdate, "_git", reset_fails)
    monkeypatch.setattr(selfupdate, "ff_blocked", lambda system, branch="": True)
    res = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert res["ok"] is False
    assert not res.get("needs_overwrite")
    assert "No space left" in res["message"]                    # the injected fault survives


# --- an advance cut off by its time limit ---------------------------------------------------------

_HANG = "exec sleep 60"  # wallclock: the git must outlast the runner's real time limit, which is under test

def _hanging_git(tmp_path, monkeypatch, meanwhile=":"):
    """Put a `git` first on the runner's PATH whose FIRST advance to `origin/main` (the merge, or the
    forced reset) writes the new commit's files, unstaged, runs `meanwhile` (shell, in the checkout:
    what someone else does there while git runs) and then hangs: the checkout a git cut off
    mid-checkout leaves, HEAD still on the old commit. Every other call is the real git; each one
    after the cut is logged by its subcommand (returned: the log's path)."""
    bindir, flag, cut, log = tmp_path / "bin", tmp_path / "hang", tmp_path / "cut", tmp_path / "after-cut.log"
    bindir.mkdir()
    flag.touch()
    script = bindir / "git"
    script.write_text(f"""#!/bin/sh
if [ "$5" = origin/main ] && [ -e '{flag}' ]; then
  rm -f '{flag}'
  '{gitrepo.GIT}' -C "$2" checkout origin/main -- . && '{gitrepo.GIT}' -C "$2" reset -q && (cd "$2" && {meanwhile}) && touch '{cut}' && {_HANG}
fi
[ -e '{cut}' ] && echo "$3" >> '{log}'
exec '{gitrepo.GIT}' "$@"
""")
    script.chmod(0o755)
    from lhpc.core.probes import backends
    monkeypatch.setitem(backends._FIXED_ENV, "PATH", f"{bindir}:{backends._FIXED_ENV['PATH']}")
    monkeypatch.setattr(selfupdate, "_APPLY_TIMEOUT", 1.0, raising=False)
    return log


def _operator_cli(monkeypatch):
    """`lhpc self-update --apply` on a box whose console is not a systemd unit (no `systemctl`), its
    venv sync and unit refresh stubbed: the real CLI, service and git underneath."""
    from lhpc.adapters.cli.main import main
    from lhpc.core.probes.backends import RealCommandRunner
    from lhpc.core.services import ControllerService
    real = RealCommandRunner.run
    monkeypatch.setattr(RealCommandRunner, "run", lambda self, argv, timeout=None, *a, **kw: (
        CommandResult(127, "", "", not_found=True) if argv[0] == "systemctl"
        else real(self, argv, timeout, *a, **kw)))
    monkeypatch.setattr(selfupdate, "venv_sync", lambda *a, **k: CommandResult(0, "", ""))
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update", lambda self: (True, "n/a"))
    return main


_PARTIAL = "may have written part of the update; nothing was undone."
_AGAIN = "the update again; if it lists changed files, 'overwrite local changes' discards them and completes the update."


@pytest.mark.parametrize("flags", [[], ["--overwrite"]], ids=["merge", "forced-reset"])
def test_an_advance_cut_off_by_its_limit_says_so_and_the_retry_lists_the_changes_then_overwrites(
        env, tmp_path, monkeypatch, capsys, flags):
    """A git cut off mid-checkout leaves a half-written tree. The apply says it timed out and that
    nothing was undone; the plain retry refuses the tree naming its changes, and the overwrite the
    operator then chooses completes the update."""
    old = selfupdate.local_state(env["sys"])["head"]
    new = gitrepo.upstream_commit(env["up"], version="0.1.1")
    main = _operator_cli(monkeypatch)
    _hanging_git(tmp_path, monkeypatch)
    assert main(["self-update", "--apply", "--yes", *flags]) == 1
    out = capsys.readouterr().out
    assert "git did not finish in the time it had" in out and _PARTIAL in out and "refused" not in out, out
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == old
    assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "Local changes are present" in out and "lhpc/version.py" in out, out
    assert main(["self-update", "--apply", "--yes", "--overwrite"]) == 0, capsys.readouterr().out
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == new
    assert gitrepo.git(env["work"], "status", "--porcelain") == ""


def test_after_a_cut_no_git_command_changes_the_checkout(env, tmp_path, monkeypatch):
    """Nothing proves which bytes the cut git wrote (another process may write there meanwhile, even
    the same content), so nothing is undone: after the cut only a read-only git runs, and what was
    written meanwhile stays as it is, a new file, an edit to an untouched file and an edit over a
    file the update had just written (changed or added) alike."""
    old = selfupdate.local_state(env["sys"])["head"]
    (env["up"] / "extra.txt").write_text("upstream extra\n")
    gitrepo.upstream_commit(env["up"], version="0.1.1")  # adds note.txt + extra.txt, changes version.py
    log = _hanging_git(tmp_path, monkeypatch, meanwhile="echo mine > mine.txt && echo '# mine' >> "
                       "pyproject.toml && echo '# mine' > lhpc/version.py && echo mine > extra.txt")
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False and res["timed_out"] is True and _PARTIAL in res["message"], res
    assert log.read_text().split() == ["rev-parse"]
    w = env["work"]
    assert gitrepo.git(w, "rev-parse", "HEAD") == old
    assert (w / "mine.txt").read_text() == "mine\n" and (w / "extra.txt").read_text() == "mine\n"
    assert (w / "pyproject.toml").read_text().endswith("# mine\n")
    assert (w / "lhpc" / "version.py").read_text() == "# mine\n"
    assert (w / "note.txt").exists()                              # the cut git's own write stays too


@pytest.mark.parametrize("make", ["file", "symlink", "dangling"])
def test_a_lock_the_cut_left_is_named_with_its_rm_and_never_removed_or_followed(env, tmp_path, monkeypatch, make):
    """A git killed at the limit can leave `.git/index.lock`. Whether a git still owns it cannot be
    proven, so it is only named, with the command to remove it once no git runs there; a symlinked
    lock is named as the link, and what it points at is never touched."""
    gitrepo.upstream_commit(env["up"], version="0.1.1")
    outside = tmp_path / "important.txt"
    outside.write_text("keep\n")
    _hanging_git(tmp_path, monkeypatch, meanwhile="touch .git/index.lock" if make == "file"
                 else f"ln -s '{outside if make == 'symlink' else tmp_path / 'gone'}' .git/index.lock")
    res = selfupdate.apply_update(env["sys"], env["paths"])
    lock = env["work"] / ".git" / "index.lock"
    assert res["timed_out"] is True and res["lock"] == str(lock), res
    assert f"rm {shlex.quote(str(lock))}." in res["message"] and _AGAIN in res["message"], res["message"]
    assert os.path.lexists(lock) and lock.is_symlink() == (make != "file")      # a dangling link is named too
    assert outside.read_text() == "keep\n"


def test_the_retry_after_a_cut_that_left_the_lock_lists_the_changes_and_the_overwrite_needs_the_lock_gone(
        env, tmp_path, monkeypatch):
    """Real git with the lock still there: the retry still reads the tree and lists the changes; the
    overwrite fails on git's lock, naming it, and changes nothing; once the lock is removed, as the
    message says, the overwrite completes."""
    new = gitrepo.upstream_commit(env["up"], version="0.1.1")
    _hanging_git(tmp_path, monkeypatch, meanwhile="touch .git/index.lock")
    first = selfupdate.apply_update(env["sys"], env["paths"])
    w, lock = env["work"], env["work"] / ".git" / "index.lock"
    assert first["timed_out"] is True and lock.exists(), first
    again = selfupdate.apply_update(env["sys"], env["paths"])
    assert again["ok"] is False and again.get("dirty") is True and again["changes"], again
    before = gitrepo.git(w, "status", "--porcelain")
    forced = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert forced["ok"] is False and "index.lock" in forced["message"], forced
    assert gitrepo.git(w, "status", "--porcelain") == before
    lock.unlink()
    done = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert done["ok"] is True and gitrepo.git(w, "rev-parse", "HEAD") == new, done
    assert gitrepo.git(w, "status", "--porcelain") == ""


def test_a_status_that_fails_is_never_read_as_a_clean_tree(env, monkeypatch):
    """The retry after a cut runs the 5 s `git status` on a half-written tree, the slowest read
    there is: when it fails or times out, the update stops with nothing changed instead of advancing
    over changes it could not list."""
    gitrepo.upstream_commit(env["up"], version="0.1.1")
    old = selfupdate.local_state(env["sys"])["head"]
    real_git = selfupdate._git
    seen = []

    def status_times_out(system, root, args, timeout):
        seen.append(args[0])
        if args[:1] == ["status"]:
            return CommandResult(124, "", "", timed_out=True)
        return real_git(system, root, args, timeout)

    monkeypatch.setattr(selfupdate, "_git", status_times_out)
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False and res.get("status_unread") is True and "nothing was changed" in res["message"], res
    assert "'overwrite local changes' updates without it and discards any local change" in res["message"], res
    assert "merge" not in seen and selfupdate.local_state(env["sys"])["head"] == old


def test_the_cut_message_with_the_longest_user_name_fits_the_apply_record(env, monkeypatch):
    """The one-click record keeps 512 characters: with a 32-character user name (the system limit) and
    a space in the root, the whole message, its quoted rm command and its last sentence, survives."""
    gitrepo.upstream_commit(env["up"])
    real_git = selfupdate._git
    lock = "/home/" + "u" * 32 + "/lora ham/loraham-pi-control/.git/index.lock"     # a root with a space
    monkeypatch.setattr(selfupdate, "_index_lock", lambda system, root: lock)
    monkeypatch.setattr(selfupdate, "_git", lambda system, root, args, timeout: (
        CommandResult(124, "", "", timed_out=True) if args[:1] == ["merge"] else real_git(system, root, args, timeout)))
    msg = selfupdate.apply_update(env["sys"], env["paths"])["message"]
    assert selfupdate.record_last_apply_strict(env["paths"], ok=False, summary=msg)
    assert selfupdate.read_cache(env["paths"])["last_apply"]["summary"] == msg and msg.endswith(_AGAIN), msg
    assert f"rm {shlex.quote(lock)}." in msg, msg                    # one shell word, as pasted


def test_a_forced_cleanup_cut_off_by_its_limit_is_a_timeout_and_the_retry_finishes(env, monkeypatch):
    """The forced path's `clean -ffd` is part of the advance: cut off, it is a timeout like the
    reset's, not the partial "some untracked files could not be removed". The ref had moved already,
    so the retry finds the checkout up to date (and finishes the sync)."""
    new = gitrepo.upstream_commit(env["up"])
    real_git = selfupdate._git
    cut = []

    def first_clean_times_out(system, root, args, timeout):
        if args[:1] == ["clean"] and not cut:
            cut.append(args)
            return CommandResult(124, "", "", timed_out=True)
        return real_git(system, root, args, timeout)

    monkeypatch.setattr(selfupdate, "_git", first_clean_times_out)
    res = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert res["ok"] is False and res["timed_out"] is True and not res.get("cleanup_failed")
    assert _PARTIAL in res["message"] and _AGAIN in res["message"], res["message"]
    assert selfupdate.local_state(env["sys"])["head"] == new
    assert selfupdate.apply_update(env["sys"], env["paths"]).get("already") is True


def test_a_cleanup_cut_after_its_reset_is_listed_by_the_retry_and_finished_by_the_overwrite(env, monkeypatch):
    """The forced reset moved HEAD to upstream, then its `clean -ffd` was cut off after removing some of
    the untracked files: up to date by HEAD, not done by the tree. The plain retry lists what is left;
    the overwrite, run again, finishes the cleanup the operator had agreed to."""
    new = gitrepo.upstream_commit(env["up"])
    w = env["work"]
    for name in ("a.txt", "b.txt"):
        (w / name).write_text("local\n")
    real_git = selfupdate._git
    cut = []

    def clean_cut_after_one(system, root, args, timeout):
        if args[:1] == ["clean"] and not cut:
            cut.append(args)
            (w / "a.txt").unlink()                    # the part the cut-off clean did
            return CommandResult(124, "", "", timed_out=True)
        return real_git(system, root, args, timeout)

    monkeypatch.setattr(selfupdate, "_git", clean_cut_after_one)
    first = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert first["timed_out"] is True and gitrepo.git(w, "rev-parse", "HEAD") == new, first
    again = selfupdate.apply_update(env["sys"], env["paths"])
    assert again["ok"] is False and again.get("unconfirmed") and again.get("changes") == ["?? b.txt"], again
    assert "'overwrite local changes' discards them" in again["message"], again
    done = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert done["ok"] is True and not done.get("already") and not (w / "b.txt").exists(), done
    assert gitrepo.git(w, "status", "--porcelain") == ""
    assert selfupdate.apply_update(env["sys"], env["paths"], force=True).get("already") is True   # clean: nothing to do


def _cut_cleanup(env, monkeypatch):
    """The forced reset moved HEAD to upstream, then its `clean -ffd` was cut off after removing a.txt (b.txt left)."""
    gitrepo.upstream_commit(env["up"])
    w = env["work"]
    for name in ("a.txt", "b.txt"):
        (w / name).write_text("local\n")
    real_git = selfupdate._git
    cut = []

    def clean_cut_after_one(system, root, args, timeout):
        if args[:1] == ["clean"] and not cut:
            cut.append(args)
            (w / "a.txt").unlink()
            return CommandResult(124, "", "", timed_out=True)
        return real_git(system, root, args, timeout)

    monkeypatch.setattr(selfupdate, "_git", clean_cut_after_one)
    assert selfupdate.apply_update(env["sys"], env["paths"], force=True)["timed_out"] is True


def test_the_cli_retry_after_a_cut_cleanup_names_what_is_left_and_the_overwrite(env, tmp_path, monkeypatch, capsys):
    """The operator's path: `lhpc self-update --apply` after the cut cleanup prints the paths the overwrite would
    discard and the overwrite command itself, as the dirty refusal does."""
    main = _operator_cli(monkeypatch)
    _cut_cleanup(env, monkeypatch)
    capsys.readouterr()
    assert main(["self-update", "--apply", "--yes"]) == 1        # a clean finish unconfirmed: no success
    out = capsys.readouterr().out
    assert "These paths would be discarded by 'overwrite local changes':" in out and "b.txt" in out, out
    assert "lhpc self-update --apply --overwrite" in out, out


def test_an_unreadable_status_at_an_up_to_date_head_is_finished_by_the_overwrite_and_said_by_the_retry(env, monkeypatch):
    """The slow-card case: after a cut cleanup the 5 s status times out. The plain retry says the state could not
    be read and names the finish; the overwrite runs the reset and clean again and finishes it."""
    _cut_cleanup(env, monkeypatch)
    real_state = selfupdate.local_state
    monkeypatch.setattr(selfupdate, "local_state", lambda system: {**real_state(system), "status_ok": False,
                                                                    "dirty": False})
    plain = selfupdate.apply_update(env["sys"], env["paths"])
    assert plain["ok"] is False and plain.get("unconfirmed") and plain.get("status_unread") is True, plain
    assert "could not be read" in plain["message"] and "'overwrite local changes'" in plain["message"], plain
    done = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert done["ok"] is True and not done.get("already") and not (env["work"] / "b.txt").exists(), done


def test_the_helpers_budget_is_the_shipped_units_limit_less_the_margin_for_its_message():
    """The budget comes from the helper unit as shipped (its bytes are frozen): the unit's TimeoutStartSec less
    HELPER_MARGIN_S, a margin the helper's own message and record need. If either drifts, this fails."""
    import re

    from lhpc.core import updater_units
    unit = updater_units.render(updater_units.HELPER_UNIT, "/r", "/c", "/v")
    limit = float(re.search(r"^TimeoutStartSec=(\d+)\s*$", unit, re.MULTILINE).group(1))
    assert selfupdate.helper_budget_s() == limit - selfupdate.HELPER_MARGIN_S and selfupdate.HELPER_MARGIN_S >= 15


def test_under_the_helpers_budget_each_step_gets_only_what_is_left(env, monkeypatch):
    """With 10 s of the budget left, the advance runs under a 10 s limit, not its own 300 s, so the helper's
    timeout message speaks before systemd would stop it (the forced path's 1255 s of bounds no longer can)."""
    gitrepo.upstream_commit(env["up"], version="0.1.1")
    real = env["sys"].runner.run
    seen = []
    monkeypatch.setattr(env["sys"].runner, "run", lambda argv, timeout=None, *a, **k: (
        seen.append((argv[3] if argv[:1] == ["git"] else argv[0], timeout)) or real(argv, timeout, *a, **k)))
    frozen_budget(monkeypatch, 10.0)
    assert selfupdate.apply_update(env["sys"], env["paths"])["ok"] is True
    merge = [t for op, t in seen if op == "merge"]
    assert merge and merge[0] == 10.0, seen


def test_a_spent_budget_starts_no_git_and_no_venv_sync(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env["sys"].runner, "run", lambda argv, timeout=None, *a, **k: calls.append(argv))
    frozen_budget(monkeypatch, -1.0)
    r = selfupdate._git(env["sys"], env["work"], ["merge", "--ff-only", "origin/main"], selfupdate._APPLY_TIMEOUT)
    v = selfupdate.venv_sync(env["sys"].runner, sys.executable, env["work"], 600.0)
    assert r.timed_out and v.timed_out and calls == [], calls


def test_a_late_helpers_venv_sync_gets_only_what_is_left(env, monkeypatch):
    """With 7 s of the budget left, each venv-sync step runs under what is left when it starts, not its own 600 s:
    the first step takes 5 s, so the second gets 2 s."""
    import types
    now, seen = [1000.0], []
    def run(argv, timeout=None, *a, **k):
        seen.append(timeout)
        now[0] += 5.0                                   # the step's own time, on the budget's clock
        return CommandResult(0, "", "")
    monkeypatch.setattr(env["sys"].runner, "run", run)
    monkeypatch.setattr(selfupdate, "time", types.SimpleNamespace(monotonic=lambda: now[0], time=selfupdate.time.time))
    monkeypatch.setattr(selfupdate, "_BUDGET_END", [1007.0])
    assert selfupdate.venv_sync(env["sys"].runner, sys.executable, env["work"], 600.0).returncode == 0
    assert seen == [7.0, 2.0], seen


def test_the_advance_runs_under_the_generous_limit_and_the_reads_keep_theirs(env, monkeypatch):
    real_git = selfupdate._git
    seen = []
    monkeypatch.setattr(selfupdate, "_git", lambda system, root, args, timeout: (
        seen.append((args[0], timeout)) or real_git(system, root, args, timeout)))
    gitrepo.upstream_commit(env["up"])
    assert selfupdate.apply_update(env["sys"], env["paths"])["ok"]
    gitrepo.upstream_commit(env["up"], version="0.1.2")
    assert selfupdate.apply_update(env["sys"], env["paths"], force=True)["ok"]
    advance = {(op, t) for op, t in seen if op in ("merge", "reset", "clean")}
    assert {op for op, _ in advance} == {"merge", "reset", "clean"}
    assert {t for _, t in advance} == {selfupdate._APPLY_TIMEOUT} and selfupdate._APPLY_TIMEOUT >= 300
    assert {t for op, t in seen if op == "rev-parse"} == {selfupdate._LOCAL_TIMEOUT}


# --- garble fix: command-output summarizer -----------------------------------------------------

def test_summarize_output_collapses_multiline_and_box_drawing():
    raw = ("\x1b[31merror:\x1b[0m Your local changes would be overwritten\n"
           "hint: commit or stash them.\n"
           "╭─ pip ─╮\n│ ERROR │\n╰───────╯\n")
    out = selfupdate._summarize_output(raw)
    assert "\n" not in out                                     # newlines collapsed
    assert "\x1b" not in out and "─" not in out and "│" not in out   # ANSI + box glyphs stripped
    assert out.startswith("error: Your local changes")    # rule-10: git's own message, first line kept; no typed field; readable, first line preserved
    assert selfupdate._summarize_output("") == ""             # empty -> empty
    assert selfupdate._summarize_output("x" * 500, limit=200).endswith("…")   # bounded


# --- warn-then-override: network-free diverged (fast-forward-blocked) detection -----------------

def test_ff_blocked_true_when_diverged(env):
    gitrepo.upstream_commit(env["up"])                                # origin/main advances
    w = env["work"]
    (w / "diverge.txt").write_text("local commit\n")           # local commit -> diverged
    gitrepo.git(w, "add", "-A"); gitrepo.git(w, "commit", "-m", "local")
    gitrepo.git(w, "fetch", "origin", "main")                         # 'check for updates' populates origin/main
    assert selfupdate.ff_blocked(env["sys"]) is True           # ff-only would be refused -> needs force


def test_ff_blocked_false_when_fast_forwardable(env):
    gitrepo.upstream_commit(env["up"])                                # origin advances; HEAD is its ancestor
    gitrepo.git(env["work"], "fetch", "origin", "main")
    assert selfupdate.ff_blocked(env["sys"]) is False          # a plain fast-forward works -> not blocked


def test_ff_blocked_false_when_up_to_date(env):
    assert selfupdate.ff_blocked(env["sys"]) is False          # HEAD == origin/main -> not blocked


def test_ff_blocked_false_when_not_a_checkout(env, monkeypatch):
    monkeypatch.setattr(selfupdate, "repo_root", lambda: None)
    assert selfupdate.ff_blocked(env["sys"]) is False          # fail-soft, nothing to warn about


def _ff_cmds(root, merge_base_rc):
    from lhpc.core.probes.backends import CommandResult as CR
    g = lambda *a: ("git", "-C", str(root), *a)
    return {
        g("rev-parse", "HEAD"): CR(0, "a" * 40 + "\n", ""),
        g("rev-parse", "--abbrev-ref", "HEAD"): CR(0, "main\n", ""),
        g("status", "--porcelain"): CR(0, "", ""),
        g("rev-parse", "origin/main"): CR(0, "b" * 40 + "\n", ""),   # differs -> not up to date
        g("merge-base", "--is-ancestor", "HEAD", "origin/main"): CR(merge_base_rc, "", "err"),
    }


def test_ff_blocked_exit_code_semantics(tmp_path, monkeypatch):
    # merge-base --is-ancestor: 0 = ancestor (ff-able), 1 = diverged, ANYTHING else = real error.
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: tmp_path)
    assert selfupdate.ff_blocked(FakeSystem(commands=_ff_cmds(tmp_path, 1)).system) is True   # diverged
    assert selfupdate.ff_blocked(FakeSystem(commands=_ff_cmds(tmp_path, 0)).system) is False  # ff-able
    # real git errors must FAIL SOFT (never masquerade as divergence / offer a force):
    for rc in (128, 129, 2):
        assert selfupdate.ff_blocked(FakeSystem(commands=_ff_cmds(tmp_path, rc)).system) is False


def test_check_upstream_fetch_error_is_sanitized(tmp_path, monkeypatch):
    # A noisy multi-line/ANSI/box-drawing fetch failure must become ONE clean bounded line, and stay
    # clean when it flows into apply_update()'s recorded message.
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: tmp_path)
    g = lambda *a: ("git", "-C", str(tmp_path), *a)
    noisy = ("\x1b[31mfatal:\x1b[0m unable to access remote\n"
             "╭────────╮\n│ boom   │\n╰────────╯\nhint: check your network\n")
    cmds = {
        g("rev-parse", "HEAD"): CR(0, "h\n", ""),
        g("rev-parse", "--abbrev-ref", "HEAD"): CR(0, "main\n", ""),
        g("status", "--porcelain"): CR(0, "", ""),
        # forced refspec: check_upstream must follow a rewritten upstream (see
        # test_check_upstream_follows_a_rewritten_upstream)
        g("fetch", "--quiet", "--force", "origin", "--",
          "refs/heads/main:refs/remotes/origin/main"): CR(1, "", noisy),
    }
    out = selfupdate.check_upstream(FakeSystem(commands=cmds).system)
    assert out["ok"] is False
    err = out["error"]
    assert "\n" not in err and "\x1b" not in err and "─" not in err and "│" not in err
    assert err.startswith("fatal: unable to access remote")    # rule-10: git's own message; no typed field
    res = selfupdate.apply_update(FakeSystem(commands=cmds).system, gitrepo.runtime_paths(tmp_path))
    assert res["ok"] is False and "\n" not in res["message"] and "─" not in res["message"]


# --- CLI operator apply: WARN-then-DO (stop web -> apply -> restart) ----------------------------

def test_self_update_apply_operator_stops_and_restarts_web(op_svc, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService, ActionResult
    seen = []
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: (seen.append(force),
                                                      ActionResult(True, "Update applied.",
                                                                   data={"already": True}))[1])
    cmds = {
        ("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"): CR(0, "", ""),  # active
        ("systemctl", "--user", "stop", "lhpc-web.service"): CR(0, "", ""),
        ("systemctl", "--user", "start", "lhpc-web.service"): CR(0, "", ""),
    }
    svc, fake = op_svc(cmds, invocation=False)
    r = svc.self_update_apply_operator(force=True)
    assert r.ok and seen == [True]
    assert ["systemctl", "--user", "stop", "lhpc-web.service"] in fake.calls
    assert ["systemctl", "--user", "start", "lhpc-web.service"] in fake.calls   # restarted after


def _pip_key(root):
    # The venv sync's first step (the locked dependencies): a failure there fails the sync.
    import sys

    from lhpc.core import selfupdate
    return tuple(selfupdate.venv_sync_argvs(sys.executable, root)[0])


_WEB_INACTIVE = WEB_INACTIVE


def _op_inactive(op_svc, monkeypatch, apply_result, *, pip=None):
    """An operator svc with web INACTIVE and a stubbed self_update_apply; `pip` overrides the
    fixture's succeeding venv-sync row. Returns (svc, fake, root)."""
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False: apply_result)
    svc, fake = op_svc(dict(_WEB_INACTIVE), invocation=False)
    root = selfupdate.repo_root()
    if pip is not None:
        fake.commands[_pip_key(root)] = pip
    return svc, fake, root


def test_the_venv_sync_installs_the_locked_dependencies_hash_checked_then_lhpc_without_deps(op_svc, monkeypatch):
    # A real advance syncs the venv in two steps: the runtime dependencies from requirements.lock with
    # every file hash-checked, then LHPC itself with --no-deps, so pip takes nothing from the index
    # that the lock does not name.
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(op_svc, monkeypatch, ActionResult(True, "advanced", data={}))
    monkeypatch.setattr(type(svc), "_refresh_units_post_update", lambda self: (True, "n/a"))
    svc.self_update_apply_operator(force=True)
    pips = [c for c in fake.calls if c[1:4] == ["-m", "pip", "install"]]
    assert pips == [[sys.executable, "-m", "pip", "install", "--require-hashes", "--no-deps", "--force-reinstall", "-r",
                     f"{root}/requirements.lock"],
                    [sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e",
                     str(root)]]


def test_operator_apply_refuses_while_the_units_are_another_releases(op_svc, monkeypatch):
    # The update gate (docs/maintenance.md, *Changing a unit*): another release's units are repaired
    # before any update, so a later unit change meets only units it accepts. Nothing is fetched,
    # stopped or synced; with this release's units the same apply proceeds (the control).
    from lhpc.core import updater_units as U
    from lhpc.core.services import ActionResult, ControllerService
    applied = []
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False: (
        applied.append(force), ActionResult(True, "already", data={"already": True}))[1])
    svc, fake = op_svc(dict(_WEB_INACTIVE), units=True, invocation=False)
    ud, root = svc._user_unit_dir(), str(svc._paths.runtime_root)
    _r, co, venv = U.deployment_paths(root)
    (ud / U.WEB_UNIT).write_text(U.render_compatible(U.WEB_UNIT, root, co, venv))
    r = svc.self_update_apply_operator()
    assert r.ok is False and r.data == {"integration": "compatible"}
    assert r.next_commands == ["lhpc self-update --repair-integration"]
    assert applied == [] and not any(c[0] in ("systemctl", "git") for c in fake.calls)
    (ud / U.HELPER_UNIT).unlink()                           # beside a missing unit: `incomplete`
    assert svc.updater_integration()["status"] == "incomplete"
    assert svc.self_update_apply_operator().data == {"integration": "compatible"} and applied == []
    (ud / U.HELPER_UNIT).write_text(U.render(U.HELPER_UNIT, root, co, venv))
    (ud / U.WEB_UNIT).write_text(U.render(U.WEB_UNIT, root, co, venv))
    assert svc.self_update_apply_operator().ok and applied == [False]


def test_operator_inactive_real_advance_syncs_venv(op_svc, monkeypatch):
    # web inactive + a REAL advance still synchronizes the editable venv (no service control).
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(op_svc, monkeypatch, ActionResult(True, "advanced", data={}))
    # NOT about systemd units: this root is a temp directory with no checkout, so the post-update
    # unit refresh has nothing real to verify. The refresh has its own coverage below and in
    # test_updater_units.py, against a canonical unit set.
    monkeypatch.setattr(type(svc), "_refresh_units_post_update", lambda self: (True, "n/a"))
    r = svc.self_update_apply_operator()
    assert r.ok and r.data.get("update_applied")
    assert list(_pip_key(root)) in fake.calls                                    # venv sync ran
    assert not any(c[:3] == ["systemctl", "--user", "stop"] for c in fake.calls)  # no service control


def test_operator_inactive_already_current_no_sync(op_svc, monkeypatch):
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(op_svc, monkeypatch,
                                   ActionResult(True, "already up to date", data={"already": True}))
    r = svc.self_update_apply_operator()
    assert r.ok
    assert list(_pip_key(root)) not in fake.calls                                # NO sync on a no-op


def test_operator_inactive_apply_failure_no_sync(op_svc, monkeypatch):
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(op_svc, monkeypatch,
                                   ActionResult(False, "apply refused", data={"busy": True}))
    r = svc.self_update_apply_operator()
    assert not r.ok
    assert list(_pip_key(root)) not in fake.calls                                # NO sync on a failure


def test_operator_inactive_sync_failure_is_typed(op_svc, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(op_svc, monkeypatch,
                                   ActionResult(True, "advanced", data={}), pip=CR(1, "boom traceback", ""))
    r = svc.self_update_apply_operator()
    assert not r.ok and r.data.get("venv_sync_failed") and r.data.get("update_applied")
    assert "venv-unsynced" in r.summary and r.next_commands == ["lhpc self-update --apply"]


def test_a_failed_venv_sync_is_the_named_state_venv_unsynced_resolved_by_the_next_apply(
        op_svc, monkeypatch):
    """The checkout moved and the venv sync failed: `venv-unsynced`, journaled and shown by
    `status`. Its one command, `lhpc self-update --apply`, finds the checkout current and runs the
    sync (and the unit refresh) it skipped; its success clears the state."""
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake, root = _op_inactive(op_svc, monkeypatch, ActionResult(True, "advanced", data={}),
                                   pip=CR(1, "boom", ""))
    refreshed = []
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (refreshed.append(1), (True, "units canonical"))[1])
    assert svc.self_update_apply_operator().data.get("state") == "venv-unsynced"
    assert svc.self_update_incomplete()[0] == "venv-unsynced" and not refreshed
    assert any("venv-unsynced" in d and "lhpc self-update --apply" in d
               for d in svc.status().details), svc.status().details
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False:
                        ActionResult(True, "Already up to date.", data={"already": True}))
    fake.commands[_pip_key(root)] = CR(0, "", "")
    fake.calls.clear()
    r = svc.self_update_apply_operator()
    assert r.ok and list(_pip_key(root)) in fake.calls and refreshed == [1], r.summary
    assert svc.self_update_incomplete() == ("", "")
    assert not any("venv-unsynced" in d for d in svc.status().details)


def test_the_check_reports_a_venv_behind_the_checkout(env, monkeypatch):
    """No record names the failed sync (it could not be written), yet the venv's recorded version
    differs from the checkout's: the check says so instead of "Up to date." (finding 83)."""
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=env["sys"], paths=env["paths"])
    assert not svc.self_update_check().data.get("venv_unsynced")
    monkeypatch.setattr(selfupdate, "installed_version", lambda: "0.0.1")
    res = svc.self_update_check()
    assert res.data["venv_unsynced"] and "0.0.1" in res.summary
    assert res.next_commands == ["lhpc self-update --apply"]


@pytest.mark.parametrize("break_it", ["no-metadata", "no-version-file"])
def test_the_check_reports_a_venv_it_cannot_verify(env, monkeypatch, break_it):
    """Fail closed (finding 129): a version it cannot read on either side is not "Up to date." —
    the check says the venv could not be verified and names the re-sync."""
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=env["sys"], paths=env["paths"])
    if break_it == "no-metadata":
        monkeypatch.setattr(selfupdate, "installed_version", lambda: "")
    else:
        (env["work"] / "lhpc" / "version.py").unlink()
    res = svc.self_update_check()
    assert res.data["venv_unsynced"] and "could not be verified" in res.summary
    assert res.next_commands == ["lhpc self-update --apply"]


def test_an_apply_finding_the_checkout_current_syncs_a_venv_behind_it(op_svc, monkeypatch):
    """Without a `venv-unsynced` record, an apply that finds the checkout current still runs the
    sync and the unit refresh when the venv's recorded version differs from the checkout's."""
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake, root = _op_inactive(op_svc, monkeypatch, ActionResult(
        True, "Already up to date.", data={"already": True}))
    # Stubbed: the unit refresh needs a real systemd unit set; this test is about whether the
    # sync runs, and the refresh has its own coverage.
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (True, "units canonical"))
    svc.self_update_apply_operator()
    assert list(_pip_key(root)) not in fake.calls                     # the control: in sync
    monkeypatch.setattr(selfupdate, "installed_version", lambda: "0.0.1")
    r = svc.self_update_apply_operator()
    assert r.ok and list(_pip_key(root)) in fake.calls, r.summary
    assert svc.self_update_incomplete() == ("", "")


@pytest.mark.parametrize("break_it", ["no-metadata", "no-version-file"])
def test_an_apply_finding_the_checkout_current_syncs_a_venv_it_cannot_verify(op_svc, monkeypatch,
                                                                             break_it):
    """Fail closed (finding 129): with no record and a version it cannot read on either side, an
    apply that finds the checkout current still runs the sync."""
    from lhpc.core.services import ActionResult, ControllerService
    svc, fake, root = _op_inactive(op_svc, monkeypatch, ActionResult(
        True, "Already up to date.", data={"already": True}))
    # Stubbed: the unit refresh needs a real systemd unit set; this test is about whether the
    # sync runs, and the refresh has its own coverage.
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (True, "units canonical"))
    if break_it == "no-metadata":
        monkeypatch.setattr(selfupdate, "installed_version", lambda: "")
    else:
        (root / "lhpc" / "version.py").unlink()
    r = svc.self_update_apply_operator()
    assert r.ok and list(_pip_key(root)) in fake.calls, r.summary


@pytest.mark.parametrize("state", ["venv-unsynced", "units-stale"])
def test_a_named_state_whose_record_cannot_be_written_fails_with_the_command_to_run(
        op_svc, monkeypatch, state):
    """No record, no state: `status` cannot show it and `--apply` cannot resume from it. So the
    update is not said to be in that state — it fails with the cause, where the checkout is, and
    what resolves it without the record: for a failed venv sync the sync AND the unit refresh it
    skipped, in that order (never the sync alone); for stale units the repair."""
    from lhpc.core import runtime_fs
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ActionResult, ControllerService
    svc, _fake, root = _op_inactive(
        op_svc, monkeypatch, ActionResult(True, "advanced", data={"new_head_short": "abc1234"}),
        pip=CR(1, "boom", "") if state == "venv-unsynced" else None)
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (False, "units still not canonical"))
    real = runtime_fs.write_marker

    def full_disk(paths, path, text, *a, **kw):
        if path.name == "selfupdate.incomplete":
            raise OSError(28, "No space left on device")
        return real(paths, path, text, *a, **kw)
    monkeypatch.setattr(runtime_fs, "write_marker", full_disk)
    r = svc.self_update_apply_operator()
    cmds = ([selfupdate.venv_sync_command(sys.executable, root),
             "lhpc self-update --repair-integration"]
            if state == "venv-unsynced" else ["lhpc self-update --repair-integration"])
    assert r.ok is False and "state" not in r.data, r.data
    assert r.summary.startswith("recovery-required: the update was applied"), r.summary    # rule-10: a sentence's opening; the result carries no typed field
    assert "No space left on device" in r.summary and "abc1234" in r.summary, r.summary
    assert f"no {state} state exists" in r.summary and r.next_commands == cmds, r.summary
    if state == "venv-unsynced":
        assert "in this order" in r.summary and "resumes it only when the update changed the version number" in r.summary
        # `--apply` really cannot: with no record it finds the checkout current and runs nothing.
        assert not svc._resumes_sync(ActionResult(True, "current", data={"already": True}))
    assert svc.self_update_incomplete() == ("", "")


@pytest.mark.parametrize("raw", ["{not json", '{"state": "units-fine"}', '["units-stale"]'])
def test_an_invalid_self_update_record_is_recovery_required_and_no_command_clears_it(
        op_svc, monkeypatch, raw):
    """Which step failed is unknown: the record is never read as `units-stale` or
    `venv-unsynced`, and neither one-step command clears it — status names it recovery-required
    with what to do by hand."""
    from lhpc.core.services import ActionResult, ControllerService
    svc, _fake, _root = _op_inactive(op_svc, monkeypatch,
                                     ActionResult(True, "Already up to date.", data={}))
    rec = svc._incomplete_path()
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(raw)
    state, _what = svc.self_update_incomplete()
    assert state == "recovery-required" and not svc.units_stale()
    assert any("recovery-required" in d and "by hand" in d for d in svc.status().details)
    monkeypatch.setattr(ControllerService, "_repair_integration_steps",
                        lambda self, *, restart=True: ActionResult(True, "repaired"))
    r = svc.self_update_repair_integration(restart=False)
    assert r.ok and "Still recorded" in r.summary and "recovery-required" in r.summary
    assert rec.read_text() == raw


@pytest.mark.parametrize("raw", ["{not json", '{"state": "units-fine"}', '["units-stale"]'])
def test_an_invalid_self_update_record_survives_the_operator_apply(op_svc, monkeypatch, raw):
    """The other one-step command, `lhpc self-update --apply` (here finding the checkout current),
    does not clear an invalid record either: it stays byte-for-byte, and `status` keeps naming it
    recovery-required."""
    from lhpc.core.services import ActionResult
    svc, _fake, _root = _op_inactive(op_svc, monkeypatch, ActionResult(
        True, "Already up to date.", data={"already": True}))
    rec = svc._incomplete_path()
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(raw)
    assert svc.self_update_apply_operator().data.get("already")     # the checkout is current
    assert rec.read_text() == raw and svc.self_update_incomplete()[0] == "recovery-required"
    assert any("recovery-required" in d for d in svc.status().details)


@pytest.mark.parametrize("raw", ['{"state": []}', '{"state": {}}'], ids=["list", "dict"])
def test_a_record_with_an_unhashable_state_is_recovery_required(op_svc, monkeypatch, raw):
    """Valid JSON whose "state" is a list or an object is a malformed record: recovery-required,
    as an invalid one — never an exception out of `status` or recovery."""
    from lhpc.core.services import ActionResult
    svc, _fake, _root = _op_inactive(op_svc, monkeypatch,
                                     ActionResult(True, "Already up to date.", data={}))
    rec = svc._incomplete_path()
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(raw)
    assert svc.self_update_incomplete()[0] == "recovery-required"
    assert any("recovery-required" in d for d in svc.status().details)


@pytest.mark.parametrize("path", ["apply", "repair"])
def test_a_record_that_cannot_be_cleared_fails_the_step(op_svc, monkeypatch, path):
    """A step that succeeded but could not remove its state record has not finished: `status`
    still shows the state. The result is recovery-required and names the record to remove by
    hand — never ok while the record remains."""
    import json
    from lhpc.core import runtime_fs
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ActionResult, ControllerService
    svc, _fake, _root = _op_inactive(op_svc, monkeypatch, ActionResult(True, "advanced", data={}),
                                     pip=CR(0, "", ""))
    # Stubs the collaborator, systemd: the unit refresh and the repair succeed without a user manager.
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (True, "units canonical"))
    monkeypatch.setattr(ControllerService, "_repair_integration_steps",
                        lambda self, *, restart=True: ActionResult(True, "repaired"))
    rec = svc._incomplete_path()
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"state": "units-stale", "detail": "x"}))
    real = runtime_fs.unlink

    def refused(paths, p, *a, **kw):
        if p == rec:
            raise OSError(30, "Read-only file system")
        return real(paths, p, *a, **kw)
    monkeypatch.setattr(runtime_fs, "unlink", refused)
    r = (svc.self_update_apply_operator() if path == "apply"
         else svc.self_update_repair_integration(restart=False))
    assert r.ok is False and r.summary.startswith("recovery-required:"), r.summary    # rule-10: a sentence's opening; the result carries no typed field
    assert "Read-only file system" in r.summary and str(rec) in " ".join(r.details), r
    assert svc.self_update_incomplete()[0] == "units-stale"


def test_the_repair_does_not_clear_venv_unsynced(op_svc, monkeypatch):
    """`--repair-integration` verifies the units, not the venv: with `venv-unsynced` recorded it
    repairs, keeps the record and names it with its own command."""
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ActionResult, ControllerService
    svc, _fake, _root = _op_inactive(op_svc, monkeypatch, ActionResult(True, "advanced", data={}),
                                     pip=CR(1, "boom", ""))
    assert svc.self_update_apply_operator().data.get("state") == "venv-unsynced"
    monkeypatch.setattr(ControllerService, "_repair_integration_steps",
                        lambda self, *, restart=True: ActionResult(True, "repaired"))
    r = svc.self_update_repair_integration(restart=False)
    assert r.ok and svc.self_update_incomplete()[0] == "venv-unsynced"
    assert "Still recorded" in r.summary and "lhpc self-update --apply" in r.summary, r.summary


def test_operator_admission_held_across_apply_and_sync(op_svc, monkeypatch):
    # admission is NEVER released between apply and venv sync — the lock is held during BOTH.
    from lhpc.core.services import ControllerService, ActionResult
    # `_held_counts()` is the per-thread lock depth — the only in-process observer of a held
    # flock; a second process trying the lock would be the behavioural twin but costs a subprocess.
    held = {}
    def fake_apply(self, *, force=False):
        held["apply"] = self._held_counts().get(self.ADMISSION_KEY, 0)
        return ActionResult(True, "advanced", data={})
    monkeypatch.setattr(ControllerService, "self_update_apply", fake_apply)
    svc, fake = op_svc(dict(_WEB_INACTIVE), invocation=False, units=True)
    root = selfupdate.repo_root()
    real_run = fake.run
    def rec_run(argv, timeout, cwd=None, env=None):
        if list(argv) == list(_pip_key(root)):
            held["sync"] = svc._held_counts().get(svc.ADMISSION_KEY, 0)
        return real_run(argv, timeout, cwd=cwd, env=env)
    monkeypatch.setattr(fake, "run", rec_run)
    assert svc.self_update_apply_operator().ok
    assert held.get("apply", 0) > 0 and held.get("sync", 0) > 0                   # held across BOTH


def test_operator_active_restart_failure_is_not_success(op_svc, monkeypatch):
    # a failed required restart is ALWAYS ok=False, retaining accurate partial-update info.
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "advanced", data={}))
    cmds = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"): CR(0, "", ""),   # ON
            ("systemctl", "--user", "stop", "lhpc-web.service"): CR(0, "", ""),
            ("systemctl", "--user", "start", "lhpc-web.service"): CR(1, "", "boom")}   # restart FAILS
    svc, fake = op_svc(cmds, invocation=False)
    r = svc.self_update_apply_operator()
    assert not r.ok                                                              # NOT reported as success
    assert r.data.get("web_restart_failed") and r.data.get("update_applied")     # partial-update info
    assert "systemctl --user start lhpc-web.service" in r.summary                # exact recovery command
    assert "boom" not in r.summary                                               # no raw command output


def test_diverged_refusal_offers_the_reset_path(tmp_path, monkeypatch):
    """The refusal the operator actually sees must name `--overwrite`; without it a diverged
    checkout is a dead end that only hand-run git can clear (live-found on a Zero)."""
    from lhpc.core import selfupdate as su
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(su, "apply_update", lambda *a, **k: {
        "ok": False, "needs_overwrite": True,
        "message": "Update could not be applied — the local branch has diverged from upstream."})
    r = svc.self_update_apply_operator()
    assert not r.ok
    assert "lhpc self-update --apply --overwrite" in r.next_commands
    assert any("discards exactly those local" in d for d in r.details)


def test_self_update_apply_operator_refuses_in_managed_unit(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.setenv("INVOCATION_ID", "managed")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r = svc.self_update_apply_operator()
    assert not r.ok and r.data.get("reason") == "managed-unit"


# --- managed-service integration: logs dir, boot autostart (linger), foreground guidance --------

def _repair_env(tmp_path, op_svc, systemctl_ok_rows, *, linger_ok=True):
    """A repairable self-hosted root whose `systemctl --user` calls all succeed."""
    import getpass
    import os
    from lhpc.core import updater_units as U
    from lhpc.core.probes.backends import CommandResult as CR
    root = tmp_path / "rt"
    _, checkout, _ = U.deployment_paths(str(root))
    (Path(checkout) / ".git").mkdir(parents=True)
    ud = Path(os.environ["HOME"]) / ".config" / "systemd" / "user"
    ud.mkdir(parents=True, exist_ok=True)
    cmds = dict(systemctl_ok_rows)
    for kind in U.ALL_UNITS:
        cmds[("systemctl", "--user", "show", "-p", "FragmentPath", "-p", "DropInPaths", kind)] = \
            CR(0, f"FragmentPath={ud / kind}\nDropInPaths=\n", "")
    if linger_ok:                                    # else: unmocked -> not_found (no user bus)
        cmds[("loginctl", "enable-linger", getpass.getuser())] = CR(0, "", "")
    return op_svc(cmds, root=root) + (root, getpass.getuser())


def test_repair_integration_creates_logs_dir_and_enables_linger(tmp_path, op_svc, systemctl_ok_rows):
    # `append:{root}/logs/...` — systemd creates the FILE, not the dir; and boot autostart needs
    # linger (install.sh does it; a repaired root never did).
    svc, fake, root, user = _repair_env(tmp_path, op_svc, systemctl_ok_rows)
    r = svc.self_update_repair_integration()
    assert r.ok, r.summary
    assert (root / "logs").is_dir()
    assert ["loginctl", "enable-linger", user] in fake.calls
    assert any("linger: enabled" in d and "autostarts at boot" in d for d in r.details)


def test_a_failed_web_restart_points_at_the_log_that_exists(tmp_path, op_svc, systemctl_ok_rows):
    # R6+ (the Desktop-image report): the hint said `journalctl --user -u lhpc-web.service`,
    # which fails for the operator on the image, and the unit sends its output to logs/lhpc-web.log
    # anyway (docs/deployment.md). The hint names the file the unit really appends to.
    from lhpc.core import updater_units as U
    from lhpc.core.probes.backends import CommandResult
    rows = dict(systemctl_ok_rows)
    rows[("systemctl", "--user", "restart", U.WEB_UNIT)] = CommandResult(1, "", "failed")
    svc, _fake, root, _user = _repair_env(tmp_path, op_svc, rows)
    r = svc.self_update_repair_integration()
    assert not r.ok and r.data.get("web_restart_failed")
    assert str(root.joinpath(*U.WEB_LOG_REL)) in r.summary
    assert "journalctl" not in r.summary


def test_repair_integration_attempts_linger_even_when_managed(tmp_path, monkeypatch, op_svc, systemctl_ok_rows):
    # NEVER gated on INVOCATION_ID: the GUI "Repair & update" bridge runs from a managed LEGACY web
    # unit that still has the user bus — gating would silently deny it boot autostart.
    monkeypatch.setenv("INVOCATION_ID", "managed-legacy-web")
    svc, fake, _root, user = _repair_env(tmp_path, op_svc, systemctl_ok_rows)
    r = svc.self_update_repair_integration(restart=False)
    assert r.ok, r.summary
    assert ["loginctl", "enable-linger", user] in fake.calls


def test_repair_integration_linger_failure_is_fail_soft(tmp_path, op_svc, systemctl_ok_rows):
    # Under the canonical sandboxed web unit (InaccessiblePaths=%t/bus) loginctl cannot reach the
    # bus. That must NEVER fail the repair — it discloses the shell command instead.
    svc, fake, _root, user = _repair_env(tmp_path, op_svc, systemctl_ok_rows, linger_ok=False)
    r = svc.self_update_repair_integration()
    assert r.ok                                                    # repair still succeeded
    assert ["loginctl", "enable-linger", user] in fake.calls       # attempted
    assert any("NOT enabled" in d and f"loginctl enable-linger {user}" in d for d in r.details)


def test_updater_integration_managed_flag_follows_invocation_id(tmp_path, monkeypatch):
    # The unit FILES can verify 'ok' while this console runs in a foreground shell.
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert svc.updater_integration()["managed"] is False
    monkeypatch.setenv("INVOCATION_ID", "abc")
    assert svc.updater_integration()["managed"] is True


def test_foreground_refusals_name_repair_integration(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for r in (svc.self_update_trigger(), svc.self_update_repair_and_trigger()):
        assert not r.ok and "foreground" in r.summary
        assert r.data.get("not_managed") is True
        assert any("--repair-integration" in d for d in r.details)   # a WAY OUT, not a dead end
        assert "lhpc self-update --repair-integration" in r.next_commands


# --- disclose WHAT an overwrite would discard ---------------------------------------------------

def test_local_changes_lists_the_porcelain_paths(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: Path("/x"))
    out = " M lhpc/core/services.py\n?? scratch.txt\n"
    fake = FakeSystem(commands={("git", "-C", "/x", "status", "--porcelain"): CR(0, out, "")})
    assert selfupdate.local_changes(fake.system) == (" M lhpc/core/services.py", "?? scratch.txt")


def test_local_changes_truncation_is_disclosed_not_silent(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: Path("/x"))
    out = "".join(f"?? f{i}\n" for i in range(25))
    fake = FakeSystem(commands={("git", "-C", "/x", "status", "--porcelain"): CR(0, out, "")})
    got = selfupdate.local_changes(fake.system, limit=20)
    assert len(got) == 21 and got[-1] == "… and 5 more"


def test_local_changes_fail_soft(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: Path("/x"))
    assert selfupdate.local_changes(FakeSystem().system) == ()      # git error -> no claim
    monkeypatch.setattr(selfupdate, "repo_root", lambda: None)
    assert selfupdate.local_changes(FakeSystem().system) == ()


def test_divergence_counts_ahead_and_behind(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: Path("/x"))
    monkeypatch.setattr(selfupdate, "local_state", lambda s: {"branch": "main"})
    cmds = {("git", "-C", "/x", "rev-list", "--left-right", "--count", "HEAD...origin/main"):
            CR(0, "3\t7\n", "")}
    assert selfupdate.divergence(FakeSystem(commands=cmds).system) == (3, 7)


def test_divergence_fail_soft_never_invents_a_divergence(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: Path("/x"))
    monkeypatch.setattr(selfupdate, "local_state", lambda s: {"branch": "main"})
    assert selfupdate.divergence(FakeSystem().system) == (0, 0)     # missing ref / git error
    bad = {("git", "-C", "/x", "rev-list", "--left-right", "--count", "HEAD...origin/main"):
           CR(0, "garbage\n", "")}
    assert selfupdate.divergence(FakeSystem(commands=bad).system) == (0, 0)


def test_dirty_refusal_names_the_paths_but_keeps_the_message_single_line(tmp_path, monkeypatch):
    # `message` is flashed verbatim -> must stay one line (the sanitizer contract). The evidence
    # rides in `changes`, which the service turns into ActionResult details.
    from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: Path("/x"))
    monkeypatch.setattr(selfupdate, "local_state",
                        lambda s: {"is_git": True, "dirty": True, "head": "a" * 40,
                                   "branch": "main", "version": "1"})
    monkeypatch.setattr(selfupdate, "check_upstream",
                        lambda s, b="": {"ok": True, "upstream_head": "b" * 40, "deps_changed": False})
    cmds = {("git", "-C", "/x", "status", "--porcelain"): CR(0, "?? scratch.txt\n", "")}
    res = selfupdate.apply_update(FakeSystem(commands=cmds).system, gitrepo.runtime_paths(tmp_path))
    assert res["ok"] is False and res["dirty"] is True
    assert "\n" not in res["message"]
    assert res["changes"] == ["?? scratch.txt"]


def test_cache_read_write_roundtrip(env):
    selfupdate.write_cache(env["paths"], {"schema_version": 1, "local": {"is_git": True, "version": "9.9"},
                                          "checked_at": 1})
    assert selfupdate.read_cache(env["paths"])["local"]["version"] == "9.9"


def test_restart_instructions_env_detection(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    i = selfupdate.restart_instructions(deps_changed=True)
    assert i["under_systemd"] is False and any("lhpc web" in c for c in i["commands"])
    assert any("pip install" in c for c in i["commands"])     # deps hint
    monkeypatch.setenv("INVOCATION_ID", "abc")
    assert any("systemctl --user restart lhpc-web" in c
               for c in selfupdate.restart_instructions()["commands"])


def test_restart_instructions_deps_sync_guidance(monkeypatch):
    # when deps changed, the guidance carries the EXACT (venv) sync command; nothing is installed here.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    i = selfupdate.restart_instructions(deps_changed=True,
                                        deps_sync_cmd="/rt/venv/lhpc/bin/python -m pip install -e /rt/co")
    assert any("/rt/venv/lhpc/bin/python -m pip install -e /rt/co" in c for c in i["commands"])
    # When nothing changed: no sync command is added and the note is empty.
    n = selfupdate.restart_instructions(deps_changed=False)
    assert n["note"] == "" and not any("pip install" in c for c in n["commands"])


def test_non_git_install_unavailable(env, monkeypatch):
    monkeypatch.setattr(selfupdate, "repo_root", lambda: None)
    assert selfupdate.local_state(env["sys"])["is_git"] is False
    assert selfupdate.check_upstream(env["sys"])["ok"] is False
    assert selfupdate.apply_update(env["sys"], env["paths"])["ok"] is False


# --- untracked, non-ignored files count as dirty; an overwrite discards them -------------------

def test_untracked_file_and_dir_block_default_apply(env):
    new_head = gitrepo.upstream_commit(env["up"])
    w = env["work"]
    (w / "untracked.txt").write_text("x")                          # untracked file
    (w / "untr_dir").mkdir(); (w / "untr_dir" / "f").write_text("y")   # untracked directory
    (w / ".venv").mkdir(); (w / ".venv" / "keep").write_text("k")      # IGNORED artifact
    assert selfupdate.local_state(env["sys"])["dirty"] is True     # untracked -> dirty
    res = selfupdate.apply_update(env["sys"], env["paths"])        # default: refuse safely
    assert res["ok"] is False and res["dirty"] is True
    assert (w / "untracked.txt").exists()                          # nothing removed
    assert selfupdate.local_state(env["sys"])["head"] != new_head  # not applied


def test_overwrite_discards_untracked_keeps_ignored(env):
    new_head = gitrepo.upstream_commit(env["up"])
    w = env["work"]
    (w / "untracked.txt").write_text("x")
    (w / "untr_dir").mkdir(); (w / "untr_dir" / "f").write_text("y")
    (w / "lhpc" / "version.py").write_text('__version__ = "0.1.1"\n# tracked edit\n')  # tracked change
    (w / ".venv").mkdir(); (w / ".venv" / "keep").write_text("keep-me")                # ignored
    res = selfupdate.apply_update(env["sys"], env["paths"], force=True)
    assert res["ok"] and selfupdate.local_state(env["sys"])["head"] == new_head
    assert not (w / "untracked.txt").exists() and not (w / "untr_dir").exists()   # untracked discarded
    assert "tracked edit" not in (w / "lhpc" / "version.py").read_text()          # tracked discarded
    assert (w / ".venv" / "keep").read_text() == "keep-me"                        # ignored preserved


# --- a forced update whose reset worked but whose `git clean` failed (ultrareview U2) ------------

def _cleanup_partial():
    from lhpc.core.services import ActionResult
    msg = ("Update aligned to upstream, but some untracked files could NOT be removed "
           "— delete them manually, then restart the console.")
    return ActionResult(False, msg, details=("cannot unlink 'x'", "Restart the web console after cleaning up:",
                                             "  systemctl --user restart lhpc-web"),
                        data={"ok": True, "cleanup_failed": True, "message": msg,
                              "restart": {"note": "", "commands": ["systemctl --user restart lhpc-web"]}})


def test_cleanup_partial_still_syncs_the_venv_and_refreshes_units(op_svc, monkeypatch):
    # The checkout DID advance: skipping the sync and the unit refresh would leave new code on old
    # dependencies and stale units (boot restore skipped). It stays a reported partial (ok=False).
    refreshed = []
    svc, fake, root = _op_inactive(op_svc, monkeypatch, _cleanup_partial())
    monkeypatch.setattr(type(svc), "_refresh_units_post_update",
                        lambda self: (refreshed.append(1), (True, "ok"))[1])
    r = svc.self_update_apply_operator(force=True)
    assert list(_pip_key(root)) in fake.calls and refreshed == [1]
    assert not r.ok and r.data.get("cleanup_failed") and r.data.get("update_applied")


def test_cleanup_partial_does_not_advise_a_restart_already_done(op_svc, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False: _cleanup_partial())
    cmds = {
        ("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"): CR(0, "", ""),
        ("systemctl", "--user", "stop", "lhpc-web.service"): CR(0, "", ""),
        ("systemctl", "--user", "start", "lhpc-web.service"): CR(0, "", ""),
    }
    svc, fake = op_svc(cmds, invocation=False)
    monkeypatch.setattr(type(svc), "_refresh_units_post_update", lambda self: (True, "ok"))
    r = svc.self_update_apply_operator(force=True)
    assert not r.ok and "delete them manually" in r.summary and "then restart the console" not in r.summary
    assert "Restart the web console after cleaning up:" not in r.details
    assert "The web console was restarted on the new version." in r.details
    assert "cannot unlink 'x'" in r.details                                    # the evidence stays


def test_operator_cleanup_partial_then_a_failed_sync_keeps_both_remedies(op_svc, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR
    svc, fake, root = _op_inactive(op_svc, monkeypatch, _cleanup_partial(), pip=CR(1, "boom", ""))
    r = svc.self_update_apply_operator(force=True)
    assert not r.ok and r.data.get("venv_sync_failed") and r.data.get("cleanup_failed")
    assert "venv-unsynced" in r.summary and "untracked files" in r.summary


# --- a dev checkout off its branch, an unreachable upstream: refused with the remedy ------------

def test_a_detached_checkout_is_refused_with_the_switch_back(env):
    """A developer moved the checkout to a detached HEAD: fast-forwarding it would leave it
    detached. Nothing is fetched or moved; Next names the switch back and the retry."""
    from lhpc.core.services import ControllerService
    gitrepo.upstream_commit(env["up"])
    w = env["work"]
    gitrepo.git(w, "checkout", "-q", "--detach")
    before = gitrepo.git(w, "rev-parse", "HEAD")
    res = ControllerService(system=env["sys"], paths=env["paths"]).self_update_apply()
    assert not res.ok
    assert res.next_commands == [f"git -C {w} switch main", "lhpc self-update --apply"]
    assert gitrepo.git(w, "rev-parse", "HEAD") == before
    assert gitrepo.git(w, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


def test_an_unreachable_upstream_is_refused_without_the_overwrite(env):
    """The origin cannot be reached: no change, no reset offered — a line saying what has to
    be fixed before the same command is run again."""
    from lhpc.core.services import ControllerService
    w = env["work"]
    gitrepo.git(w, "remote", "set-url", "origin", str(w.parent / "gone.git"))
    before = gitrepo.git(w, "rev-parse", "HEAD")
    res = ControllerService(system=env["sys"], paths=env["paths"]).self_update_apply()
    assert not res.ok and res.next_commands == []
    assert any(d.startswith("  nothing to run here — ") for d in res.details)    # rule-10: a sentence's opening; the result carries no typed field
    assert gitrepo.git(w, "rev-parse", "HEAD") == before


def test_status_names_a_newer_releases_units_and_the_shell_repair(op_svc, tmp_path):
    from lhpc.core import updater_units as U
    svc, _fake = op_svc(units=True)
    _r, co, venv = U.deployment_paths(str(tmp_path))
    assert not any("units-compatible:" in d for d in svc.status().details)          # control
    (svc._user_unit_dir() / U.WEB_UNIT).write_text(U.render_compatible(U.WEB_UNIT, str(tmp_path), co, venv))
    assert any("units-compatible:" in d and "lhpc self-update --repair-integration" in d
               for d in svc.status().details), svc.status().details
