"""Deterministic tests for the self-update core (lhpc/core/selfupdate.py).

Uses REAL git in throwaway temp repos (a bare 'origin' + a working clone + an 'upstream' clone) so the
actual git integration is exercised, never the real working checkout and never real network. The
module's `repo_root()` is monkeypatched to the temp working clone; the code under test drives git via
`RealSystem()` (real subprocess) against those temp repos.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import RealSystem

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent",
}


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), env={**_ENV, "PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _seed(repo: Path, version: str):
    (repo / "lhpc").mkdir(parents=True, exist_ok=True)
    (repo / "lhpc" / "version.py").write_text(f'__version__ = "{version}"\n')
    (repo / "pyproject.toml").write_text('[project]\nname="x"\nversion="0.0.0"\ndependencies=[]\n')
    (repo / ".gitignore").write_text(".venv/\nvenv/\n")   # ignored runtime artifacts (like a real repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "push", "-u", "origin", "main")


def _repos(tmp: Path):
    """(origin, work, up): a bare origin, a working clone (monkeypatch target), and an upstream clone
    for advancing origin/main."""
    origin, work, up = tmp / "origin.git", tmp / "work", tmp / "up"
    _git(tmp, "init", "--bare", "-b", "main", str(origin))
    _git(tmp, "clone", str(origin), str(work))
    _git(work, "checkout", "-b", "main")
    _seed(work, selfupdate.__version__)
    _git(tmp, "clone", str(origin), str(up))
    return origin, work, up


def _upstream_commit(up: Path, *, version: str | None = None, touch_pyproject: bool = False):
    if version is not None:
        (up / "lhpc" / "version.py").write_text(f'__version__ = "{version}"\n')
    if touch_pyproject:
        (up / "pyproject.toml").write_text('[project]\nname="x"\nversion="0.0.0"\ndependencies=["flask"]\n')
    (up / "note.txt").write_text("upstream change\n")
    _git(up, "add", "-A")
    _git(up, "commit", "-m", "upstream")
    _git(up, "push", "origin", "main")
    return _git(up, "rev-parse", "HEAD")


def _paths(tmp: Path) -> Paths:
    (tmp / "rt" / "state").mkdir(parents=True, exist_ok=True)
    return Paths(runtime_root=tmp / "rt")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    origin, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    return {"sys": RealSystem(), "paths": _paths(tmp_path), "work": work, "up": up}


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
    _upstream_commit(env["up"])                               # new commit, version unchanged
    selfupdate.refresh_cache(env["sys"], env["paths"])
    v = selfupdate.status_view(env["paths"])
    assert v["ver_color"] == "green"          # version unchanged stays green
    assert v["commit_color"] == "yellow"      # only the commit changed
    assert v["update_available"] is True


def test_status_version_ahead_is_red(env):
    _upstream_commit(env["up"], version="0.2.0", touch_pyproject=True)
    selfupdate.refresh_cache(env["sys"], env["paths"])
    v = selfupdate.status_view(env["paths"])
    assert v["ver_color"] == "red" and v["commit_color"] == "red"
    assert v["update_available"] is True and v["upstream_version"] == "0.2.0"
    assert v["deps_changed"] is True          # pyproject changed -> pip hint


def test_status_view_no_check_is_grey(env, monkeypatch):
    # Never refreshed: status_view is CACHED-ONLY, so it must NOT probe the live checkout to
    # decide availability — repo_root raising proves the GET path never calls it (Issue A).
    monkeypatch.setattr(selfupdate, "repo_root",
                        lambda: (_ for _ in ()).throw(AssertionError("live repo_root on a GET")))
    v = selfupdate.status_view(env["paths"])                  # never refreshed
    assert v["ver_color"] == "grey" and v["commit_color"] == "grey"
    assert v["update_available"] is False and v["version"] == selfupdate.__version__
    # No cache yet -> availability is unknown/unavailable until a refresh writes local.is_git;
    # it does NOT fall back to a live .git probe.
    assert v["available"] is False and v["is_git"] is False


def test_status_view_legacy_cache_without_is_git_is_unavailable(env, monkeypatch):
    # A pre-existing cache written before `is_git` existed in `local` must render unavailable
    # (never a live fallback), per the backward-compat requirement.
    monkeypatch.setattr(selfupdate, "repo_root",
                        lambda: (_ for _ in ()).throw(AssertionError("live repo_root on a GET")))
    selfupdate.write_cache(env["paths"], {"local": {"head": "a" * 40, "head_short": "aaaaaaaaa",
                                                    "branch": "main"},
                                          "upstream": {}, "checked_at": 1})
    v = selfupdate.status_view(env["paths"])
    assert v["available"] is False and v["is_git"] is False and v["update_available"] is False


# --- apply ------------------------------------------------------------------------------------

def test_apply_fast_forward_clean(env):
    new_head = _upstream_commit(env["up"])
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] and not res.get("already")
    assert selfupdate.local_state(env["sys"])["head"] == new_head   # working clone fast-forwarded
    # cache refreshed -> now up to date/green
    assert selfupdate.status_view(env["paths"])["commit_color"] == "green"


def test_apply_already_up_to_date(env):
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] and res["already"] is True


def test_apply_refuses_dirty_by_default_then_force(env):
    new_head = _upstream_commit(env["up"])
    (env["work"] / "lhpc" / "version.py").write_text('__version__ = "0.1.1"\n# local edit\n')  # dirty
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False and res["dirty"] is True          # default: do NOT overwrite
    assert selfupdate.local_state(env["sys"])["head"] != new_head
    forced = selfupdate.apply_update(env["sys"], env["paths"], force=True)   # opt in to overwrite
    assert forced["ok"] and selfupdate.local_state(env["sys"])["head"] == new_head
    assert "local edit" not in (env["work"] / "lhpc" / "version.py").read_text()   # discarded


def test_apply_diverged_history_refused_without_force(env):
    _upstream_commit(env["up"])                                # origin/main advances
    (env["work"] / "diverge.txt").write_text("local commit\n")  # local commit -> diverged
    _git(env["work"], "add", "-A")
    _git(env["work"], "commit", "-m", "local")
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False and "diverged" in res["message"]
    assert "\n" not in res["message"]                          # single clean line (no raw git block)


# --- garble fix: command-output summarizer -----------------------------------------------------

def test_summarize_output_collapses_multiline_and_box_drawing():
    raw = ("\x1b[31merror:\x1b[0m Your local changes would be overwritten\n"
           "hint: commit or stash them.\n"
           "╭─ pip ─╮\n│ ERROR │\n╰───────╯\n")
    out = selfupdate._summarize_output(raw)
    assert "\n" not in out                                     # newlines collapsed
    assert "\x1b" not in out and "─" not in out and "│" not in out   # ANSI + box glyphs stripped
    assert out.startswith("error: Your local changes")        # readable, first line preserved
    assert selfupdate._summarize_output("") == ""             # empty -> empty
    assert selfupdate._summarize_output("x" * 500, limit=200).endswith("…")   # bounded


# --- warn-then-override: network-free diverged (fast-forward-blocked) detection -----------------

def test_ff_blocked_true_when_diverged(env):
    _upstream_commit(env["up"])                                # origin/main advances
    w = env["work"]
    (w / "diverge.txt").write_text("local commit\n")           # local commit -> diverged
    _git(w, "add", "-A"); _git(w, "commit", "-m", "local")
    _git(w, "fetch", "origin", "main")                         # 'check for updates' populates origin/main
    assert selfupdate.ff_blocked(env["sys"]) is True           # ff-only would be refused -> needs force


def test_ff_blocked_false_when_fast_forwardable(env):
    _upstream_commit(env["up"])                                # origin advances; HEAD is its ancestor
    _git(env["work"], "fetch", "origin", "main")
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
        g("fetch", "--quiet", "origin", "--", "main"): CR(1, "", noisy),
    }
    out = selfupdate.check_upstream(FakeSystem(commands=cmds).system)
    assert out["ok"] is False
    err = out["error"]
    assert "\n" not in err and "\x1b" not in err and "─" not in err and "│" not in err
    assert err.startswith("fatal: unable to access remote")
    res = selfupdate.apply_update(FakeSystem(commands=cmds).system, _paths(tmp_path))
    assert res["ok"] is False and "\n" not in res["message"] and "─" not in res["message"]


# --- CLI operator apply: WARN-then-DO (stop web -> apply -> restart) ----------------------------

def _op_svc(tmp_path, cmds):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    fake = FakeSystem(commands=cmds)
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path)), fake


def test_self_update_apply_operator_stops_and_restarts_web(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.delenv("INVOCATION_ID", raising=False)
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
    svc, fake = _op_svc(tmp_path, cmds)
    r = svc.self_update_apply_operator(force=True)
    assert r.ok and seen == [True]
    assert ["systemctl", "--user", "stop", "lhpc-web.service"] in fake.calls
    assert ["systemctl", "--user", "start", "lhpc-web.service"] in fake.calls   # restarted after


def test_self_update_apply_operator_web_inactive_delegates(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "plain apply", data={}))
    cmds = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"): CR(1, "", "")}  # off
    svc, fake = _op_svc(tmp_path, cmds)
    r = svc.self_update_apply_operator()
    assert r.ok and r.summary == "plain apply"
    assert not any(c[:3] == ["systemctl", "--user", "stop"] for c in fake.calls)   # no service control


def test_self_update_apply_operator_refuses_in_managed_unit(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.setenv("INVOCATION_ID", "managed")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r = svc.self_update_apply_operator()
    assert not r.ok and "managed unit" in r.summary


# --- managed-service integration: logs dir, boot autostart (linger), foreground guidance --------

def _repair_env(tmp_path, monkeypatch, *, linger_ok=True):
    """A repairable self-hosted root whose `systemctl --user` calls all succeed."""
    import getpass
    from lhpc.core import updater_units as U
    from lhpc.core.probes.backends import CommandResult as CR
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "rt"
    _, checkout, _ = U.deployment_paths(str(root))
    (Path(checkout) / ".git").mkdir(parents=True)
    ud = home / ".config" / "systemd" / "user"
    cmds = {("systemctl", "--user", "daemon-reload"): CR(0, "", ""),
            ("systemctl", "--user", "enable", "--now", U.PATH_UNIT): CR(0, "", ""),
            ("systemctl", "--user", "enable", U.WEB_UNIT): CR(0, "", ""),
            ("systemctl", "--user", "restart", U.WEB_UNIT): CR(0, "", ""),
            # restart=False (the web self-repair bridge) additionally proves the watcher is live
            ("systemctl", "--user", "is-active", "--quiet", U.PATH_UNIT): CR(0, "", "")}
    for kind in U.ALL_UNITS:
        cmds[("systemctl", "--user", "show", "-p", "FragmentPath", "-p", "DropInPaths", kind)] = \
            CR(0, f"FragmentPath={ud / kind}\nDropInPaths=\n", "")
    if linger_ok:                                    # else: unmocked -> not_found (no user bus)
        cmds[("loginctl", "enable-linger", getpass.getuser())] = CR(0, "", "")
    return _op_svc(root, cmds) + (root, getpass.getuser())


def test_repair_integration_creates_logs_dir_and_enables_linger(tmp_path, monkeypatch):
    # `append:{root}/logs/...` — systemd creates the FILE, not the dir; and boot autostart needs
    # linger (install.sh does it; a repaired root never did).
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    svc, fake, root, user = _repair_env(tmp_path, monkeypatch)
    r = svc.self_update_repair_integration()
    assert r.ok, r.summary
    assert (root / "logs").is_dir()
    assert ["loginctl", "enable-linger", user] in fake.calls
    assert any("linger: enabled" in d and "autostarts at boot" in d for d in r.details)


def test_repair_integration_attempts_linger_even_when_managed(tmp_path, monkeypatch):
    # NEVER gated on INVOCATION_ID: the GUI "Repair & update" bridge runs from a managed LEGACY web
    # unit that still has the user bus — gating would silently deny it boot autostart.
    monkeypatch.setenv("INVOCATION_ID", "managed-legacy-web")
    svc, fake, _root, user = _repair_env(tmp_path, monkeypatch)
    r = svc.self_update_repair_integration(restart=False)
    assert r.ok, r.summary
    assert ["loginctl", "enable-linger", user] in fake.calls


def test_repair_integration_linger_failure_is_fail_soft(tmp_path, monkeypatch):
    # Under the canonical sandboxed web unit (InaccessiblePaths=%t/bus) loginctl cannot reach the
    # bus. That must NEVER fail the repair — it discloses the shell command instead.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    svc, fake, _root, user = _repair_env(tmp_path, monkeypatch, linger_ok=False)
    r = svc.self_update_repair_integration()
    assert r.ok                                                    # repair still succeeded
    assert ["loginctl", "enable-linger", user] in fake.calls       # attempted
    assert any("NOT enabled" in d and f"loginctl enable-linger {user}" in d for d in r.details)


def test_updater_integration_managed_flag_follows_invocation_id(tmp_path, monkeypatch):
    # The unit FILES can verify 'ok' while this console runs in a foreground shell.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert svc.updater_integration()["managed"] is False
    monkeypatch.setenv("INVOCATION_ID", "abc")
    assert svc.updater_integration()["managed"] is True


def test_foreground_refusals_name_repair_integration(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for r in (svc.self_update_trigger(), svc.self_update_repair_and_trigger()):
        assert not r.ok and "foreground" in r.summary
        assert r.data.get("not_managed") is True
        assert any("--repair-integration" in d for d in r.details)   # a WAY OUT, not a dead end
        assert "lhpc self-update --repair-integration" in r.next_commands


def test_cache_read_write_roundtrip(env):
    selfupdate.write_cache(env["paths"], {"local": {"is_git": True, "version": "9.9"}, "checked_at": 1})
    assert selfupdate.read_cache(env["paths"])["local"]["version"] == "9.9"


def test_restart_instructions_env_detection(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    i = selfupdate.restart_instructions(deps_changed=True)
    assert i["under_systemd"] is False and any("lhpc web" in c for c in i["commands"])
    assert any("pip install" in c for c in i["commands"])     # deps hint
    monkeypatch.setenv("INVOCATION_ID", "abc")
    assert any("systemctl --user restart lhpc-web" in c
               for c in selfupdate.restart_instructions()["commands"])


def test_non_git_install_unavailable(env, monkeypatch):
    monkeypatch.setattr(selfupdate, "repo_root", lambda: None)
    assert selfupdate.local_state(env["sys"])["is_git"] is False
    assert selfupdate.check_upstream(env["sys"])["ok"] is False
    assert selfupdate.apply_update(env["sys"], env["paths"])["ok"] is False


# --- Defect 2: untracked non-ignored files/dirs count as dirty; overwrite discards them --------

def test_untracked_file_and_dir_block_default_apply(env):
    new_head = _upstream_commit(env["up"])
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
    new_head = _upstream_commit(env["up"])
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


# --- Defect 1: legacy default-equal persisted values migrate after a SUCCESSFUL update ---------

def _seed_manifest(work, up, text) -> str:
    """Commit `text` as work/manifest.toml — a SOURCE-tracked manifest so a candidate's pre-update
    default is provable via `git show <from_head>:manifest.toml` — and resync the disposable upstream
    clone. Returns the new work HEAD. Call BEFORE any `_upstream_commit(up)`."""
    (work / "manifest.toml").write_text(text)
    _git(work, "add", "manifest.toml"); _git(work, "commit", "-m", "manifest")
    _git(work, "push", "-q", "origin", "main")
    _git(up, "fetch", "-q", "origin"); _git(up, "reset", "-q", "--hard", "origin/main")
    return _git(work, "rev-parse", "HEAD")


def _svc(tmp_path, work):
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    rt = tmp_path / "rt"
    (rt / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (rt / "config" / "files").mkdir(parents=True, exist_ok=True)
    man = work / "manifest.toml"
    return ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt)), man, rt


def _svc_with_manifest(tmp_path, work, monkeypatch, *, opt_default):
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    _seed_manifest(work, tmp_path / "up",
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="opt"\n  kind="str"\n  default="{opt_default}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n'
        '  [[stack.component.param]]\n  name="empt"\n  kind="str"\n  default="HASDEF"\n')
    return _svc(tmp_path, work)


def test_legacy_default_migrates_overrides_preserved(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    _origin, work, up = _repos(tmp_path)
    svc, man, rt = _svc_with_manifest(tmp_path, work, monkeypatch, opt_default="OLD")
    _upstream_commit(up)                                           # a real update is available
    # LEGACY seed (bypasses the overrides-only save path — writes every value verbatim):
    #   opt == old default (migrate), keep = genuine override, empt = intentional EMPTY override.
    cfgmod.save_stack_config(svc._paths, "s", {"opt": "OLD", "keep": "MINE", "empt": ""})
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") >= 1
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "opt" not in stored                                    # default-equal removed
    assert stored.get("keep") == "MINE" and stored.get("empt") == ""   # overrides (incl. empty) kept
    # Post-update manifest (default OLD -> NEW): the migrated value now resolves to the NEW default.
    man.write_text(man.read_text().replace('default="OLD"', 'default="NEW"'))
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    assert svc2.stack_config("s")["opt"] == "NEW"                 # new default effective
    assert svc2.stack_config("s")["keep"] == "MINE" and svc2.stack_config("s")["empt"] == ""


def test_refused_update_does_not_migrate_config(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _origin, work, up = _repos(tmp_path)
    svc, man, rt = _svc_with_manifest(tmp_path, work, monkeypatch, opt_default="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"opt": "OLD", "keep": "MINE"})
    (work / "dirty.txt").write_text("uncommitted")                # dirty -> apply refused (no force)
    res = svc.self_update_apply()
    assert res.ok is False
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert stored.get("opt") == "OLD" and stored.get("keep") == "MINE"   # config UNCHANGED


def test_already_up_to_date_does_not_migrate(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _origin, work, up = _repos(tmp_path)                          # no upstream commit -> already current
    svc, man, rt = _svc_with_manifest(tmp_path, work, monkeypatch, opt_default="OLD")
    cfgmod.save_stack_config(svc._paths, "s", {"opt": "OLD"})
    res = svc.self_update_apply()
    assert res.ok and res.data.get("already") is True
    assert cfgmod.load_stack_config(svc._paths, "s").get("opt") == "OLD"   # nothing migrated


def test_migration_honors_operator_token_and_canonical_form(tmp_path, monkeypatch):
    # A default may carry an operator token ({callsign}) and a stored value may be non-canonical;
    # migration must compare CANONICAL, operator-substituted forms.
    from lhpc.core import config as cfgmod
    _origin, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    _seed_manifest(work, up,
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="who"\n  kind="str"\n  default="{callsign}"\n'
        '  [[stack.component.param]]\n  name="num"\n  kind="int"\n  default="10"\n')
    _upstream_commit(up)
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_operator_config(svc._paths, "DJ0CHE", ""); svc._invalidate_config()
    # legacy: who == operator-substituted default; num == default (int, plain form)
    cfgmod.save_stack_config(svc._paths, "s", {"who": "DJ0CHE", "num": "10"})
    assert svc.self_update_apply().ok
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "who" not in stored and "num" not in stored          # both recognised as at-default -> migrated


# --- Defect 1 (every valid legacy form) + Defect 2 (race safety) + Defect 3 (durable retry) ----

def _rf_manifest(ropt="OLD", fopt="FOLD"):
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{ropt}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n'
        '  [stack.component.config_file]\n  path="{runtime}/config/files/x.conf"\n  fmt="env"\n'
        f'    [[stack.component.config_file.param]]\n    name="fopt"\n    key="FOPT"\n    default="{fopt}"\n')


def _svc_rf(tmp_path, work, monkeypatch, *, ropt="OLD", fopt="FOLD"):
    """A single-component stack 's' with a RUN param `ropt` (+ `keep`) and a FILE param `fopt`, so
    tests can seed scoped/flat legacy forms of both. The manifest is SOURCE-tracked in `work` (commit
    BEFORE any `_upstream_commit(up)`)."""
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    _seed_manifest(work, tmp_path / "up", _rf_manifest(ropt, fopt))
    return _svc(tmp_path, work)


def _bump_default(work, up, *, ropt="NEW", fopt="FOLD") -> str:
    """Commit a manifest default CHANGE and advance origin/main (a real source transition); returns
    the new work HEAD."""
    return _seed_manifest(work, up, _rf_manifest(ropt, fopt))


def _bump_default_text(work, up, text) -> str:
    """Commit an arbitrary new manifest text and advance origin/main; returns the new work HEAD."""
    return _seed_manifest(work, up, text)


def _cand(from_head, *, key="ropt", kind="r", name="ropt", comp="c", band="", expected="OLD"):
    return {"stack": "s", "band": band, "key": key, "kind": kind, "comp": comp, "name": name,
            "expected": expected, "from_head": from_head}


def _seed_journal(svc, *, from_head, to_head, pending, slot="completed", branch="main", anchored=True):
    """Write a v3 journal record (default in the `completed` slot) and — unless `anchored=False` —
    create the MATCHING durable git transaction anchor it references. Returns the txid."""
    txid = selfupdate.new_txid()
    payload = {"from_head": from_head, "to_head": to_head, "branch": branch, "pending": pending}
    if anchored:
        assert selfupdate.create_anchor(svc._system, txid, payload)
    rec = {**payload, "txid": txid}
    selfupdate.write_migration_journal(svc._paths, {
        "completed": rec if slot == "completed" else None,
        "prepared": rec if slot == "prepared" else None})
    return txid


def test_scoped_run_param_legacy_form_migrates(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"__r__c__ropt": "OLD"})   # scoped legacy form only
    assert svc.self_update_apply().ok
    assert "__r__c__ropt" not in cfgmod.load_stack_config(svc._paths, "s")   # migrated


def test_scoped_file_param_legacy_form_migrates(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, fopt="FOLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"__f__c__fopt": "FOLD"})   # scoped file legacy form
    assert svc.self_update_apply().ok
    assert "__f__c__fopt" not in cfgmod.load_stack_config(svc._paths, "s")


def test_flat_and_scoped_precedence_both_forms(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    # both forms present: scoped is a genuine override, flat is the old default. Precedence: scoped
    # wins. Migration must remove the flat DEFAULT and keep the scoped OVERRIDE (precedence intact).
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "__r__c__ropt": "MINE"})
    assert svc.self_update_apply().ok
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored and stored.get("__r__c__ropt") == "MINE"
    val, amb = svc._resolve_stored(stored, "r", "c", "ropt", 1)
    assert val == "MINE" and not amb                                # scoped override still wins


def test_both_forms_at_default_both_migrate(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "__r__c__ropt": "OLD"})  # both == default
    svc.self_update_apply()
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored and "__r__c__ropt" not in stored    # every valid form migrated


def test_race_change_to_nonempty_override_survives(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    real = selfupdate.apply_update
    def racing(system, paths, **kw):                               # concurrent writer AFTER the snapshot
        r = real(system, paths, **kw)
        cfgmod.update_stack_config(svc._paths, "s", {"ropt": "RACED"})
        return r
    monkeypatch.setattr(selfupdate, "apply_update", racing)
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0               # value changed under us -> not removed
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "RACED"   # override survives


def test_race_change_to_empty_override_survives(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    real = selfupdate.apply_update
    def racing(system, paths, **kw):
        r = real(system, paths, **kw)
        cfgmod.update_stack_config(svc._paths, "s", {"ropt": ""}, clear_empty=False)  # intentional empty
        return r
    monkeypatch.setattr(selfupdate, "apply_update", racing)
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == ""   # empty override survives


def test_migration_write_failure_is_durable_and_retried(tmp_path, monkeypatch):
    import lhpc.core.services as services_mod
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    real_clear = services_mod.conditional_clear_stack_config
    calls = {"n": 0}
    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")                            # fail the FIRST migration write
        return real_clear(*a, **k)
    monkeypatch.setattr(services_mod, "conditional_clear_stack_config", flaky)
    # 1st apply: checkout advances, migration write FAILS -> reported incomplete, config untouched,
    #            intent persisted durably.
    res1 = svc.self_update_apply()
    assert res1.ok and res1.data.get("migrated") == 0 and res1.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"   # nothing discarded
    env, bad = selfupdate.read_migration_journal(svc._paths)
    assert not bad and env and env["completed"]["pending"]                  # durable intent kept
    # 2nd apply (now already-up-to-date): journal proves the transition -> recovery retries + succeeds.
    res2 = svc.self_update_apply()
    assert res2.ok and res2.data.get("already") is True
    assert res2.data.get("migrated") >= 1 and res2.data.get("pending_migrations") == 0
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")          # finally migrated
    assert selfupdate.read_migration_journal(svc._paths)[0] is None          # journal cleared


# --- Defect 4: nested untracked repo + cleanup-command failure ---------------------------------

def test_nested_untracked_repo_blocks_then_overwrite_removes(env):
    new_head = _upstream_commit(env["up"])
    w = env["work"]
    (w / "nested").mkdir()                                        # a NESTED untracked git repo
    _git(w / "nested", "init", "-b", "main", ".")
    (w / "nested" / "f.txt").write_text("x")
    assert selfupdate.local_state(env["sys"])["dirty"] is True    # nested repo -> untracked -> dirty
    assert selfupdate.apply_update(env["sys"], env["paths"])["ok"] is False   # default: refuse
    assert (w / "nested").exists()
    res = selfupdate.apply_update(env["sys"], env["paths"], force=True)       # clean -ffd removes it
    assert res["ok"] and not res.get("cleanup_failed")
    assert not (w / "nested").exists() and selfupdate.local_state(env["sys"])["head"] == new_head


def _fake_commands(root, *, clean_rc):
    from lhpc.core.probes.backends import CommandResult
    def R(rc=0, out="", err=""):
        return CommandResult(returncode=rc, stdout=out, stderr=err)
    r = str(root)
    return {
        ("git", "-C", r, "rev-parse", "HEAD"): R(out="aaaaaaaaa\n"),
        ("git", "-C", r, "rev-parse", "--abbrev-ref", "HEAD"): R(out="main\n"),
        ("git", "-C", r, "status", "--porcelain"): R(out="?? junk\n"),         # dirty -> needs force
        ("git", "-C", r, "fetch", "--quiet", "origin", "--", "main"): R(),
        ("git", "-C", r, "rev-parse", "origin/main"): R(out="bbbbbbbbb\n"),
        ("git", "-C", r, "show", "origin/main:lhpc/version.py"): R(out='__version__ = "0.1.2"\n'),
        ("git", "-C", r, "diff", "--name-only", "HEAD..origin/main", "--", "pyproject.toml"): R(),
        ("git", "-C", r, "reset", "--hard", "origin/main"): R(),               # reset SUCCEEDS
        ("git", "-C", r, "clean", "-ffd"): R(rc=clean_rc, err="cannot unlink 'x': Permission denied"),
    }


def test_apply_cleanup_failure_is_truthful_partial(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import FakeSystem
    root = tmp_path / "repo"; root.mkdir()
    monkeypatch.setattr(selfupdate, "repo_root", lambda: root)
    fs = FakeSystem(commands=_fake_commands(root, clean_rc=1))
    res = selfupdate.apply_update(fs.system, _paths(tmp_path), force=True)
    assert res["ok"] is True and res["cleanup_failed"] is True    # reset worked, cleanup did not
    assert "Permission denied" in res["cleanup_error"]
    assert "could NOT be removed" in res["message"]              # truthful, not a plain success


def test_service_maps_cleanup_failure_to_partial(tmp_path, monkeypatch):
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    monkeypatch.setattr(selfupdate, "apply_update", lambda *a, **k: {
        "ok": True, "cleanup_failed": True, "cleanup_error": "cannot unlink 'x'",
        "message": "Update aligned to upstream, but some untracked files could NOT be removed "
                   "— delete them manually, then restart the console.", "deps_changed": False})
    res = svc.self_update_apply(force=True)
    assert res.ok is False                                        # partial -> not a plain success
    assert "could NOT be removed" in res.summary
    assert any("cannot unlink" in d for d in res.details)


# --- Defect 1/2/3: durable journal transaction, strict validation, interprocess serialization -----

import fcntl                                                              # noqa: E402
import json as _json                                                     # noqa: E402
from lhpc.core import runtime_fs as _rfs                                 # noqa: E402


def _head(work):
    return _git(work, "rev-parse", "HEAD")


def test_interrupted_migration_recovered_by_fresh_service(tmp_path, monkeypatch):
    """Crash AFTER source transition, BEFORE config migration: a fresh post-update service must
    complete the original migration from the durable journal on the next explicit invocation."""
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")                    # commit OLD manifest, HEAD=A
    a = _head(work)
    b = _bump_default(work, up, ropt="NEW")                             # real transition -> NEW, HEAD=B
    svc, man, rt = _svc(tmp_path, work)                                 # FRESH post-update service (NEW default)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})          # value still at OLD default
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])   # anchored completed A->B
    res = svc.self_update_apply()
    assert res.ok and res.data.get("already") is True and res.data.get("migrated") >= 1
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")       # original migration completed
    assert svc.stack_config("s")["ropt"] == "NEW"                        # now follows the new default
    assert selfupdate.read_migration_journal(svc._paths)[0] is None      # journal cleared


def test_journal_persist_failure_refuses_before_mutation(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before = _head(work)
    monkeypatch.setattr(selfupdate, "write_migration_journal", lambda *a, **k: False)   # cannot persist
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("journal_write_failed") is True
    assert _head(work) == before                                         # source NOT advanced
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # config untouched


def test_migration_failure_recovered_after_fresh_service(tmp_path, monkeypatch):
    import lhpc.core.services as services_mod
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    fail = {"on": True}
    real = services_mod.conditional_clear_stack_config
    def clr(*a, **k):
        if fail["on"]:
            raise OSError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(services_mod, "conditional_clear_stack_config", clr)
    res1 = svc.self_update_apply()                                       # advances, migration write FAILS
    assert res1.ok and res1.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"
    # a FRESH service (restart boundary) recovers the durable journal and completes it
    fail["on"] = False
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    res2 = svc2.self_update_apply()
    assert res2.ok and res2.data.get("already") is True and res2.data.get("migrated") >= 1
    assert "ropt" not in cfgmod.load_stack_config(svc2._paths, "s")
    assert selfupdate.read_migration_journal(svc2._paths)[0] is None


_A, _B = "a" * 40, "b" * 40                                                          # distinct valid shas
_CAND = {"stack": "s", "band": "", "key": "ropt", "kind": "r", "comp": "c", "name": "ropt",
         "expected": "OLD"}


def _rec(**over):
    r = {"from_head": _A, "to_head": _B, "branch": "main", "pending": [dict(_CAND)]}
    r.update(over)
    return r


_BAD_JOURNALS = [
    "not json {{{",                                                                  # corrupt JSON
    _json.dumps({"version": 1, "completed": None, "prepared": None}),                # wrong version
    _json.dumps({"version": 2, "completed": {"pending": "x"}}),                      # completed not a record
    _json.dumps({"version": 2, "completed": _rec(from_head="xyz")}),                 # INVALID sha form
    _json.dumps({"version": 2, "completed": _rec(to_head=_A)}),                      # EQUAL from_head/to_head
    _json.dumps({"version": 2, "completed": _rec(branch="bad branch!")}),            # invalid branch token
    _json.dumps({"version": 2, "completed": _rec(pending=[{"stack": "s"}])}),        # candidate missing fields
    _json.dumps({"version": 2, "completed": _rec(pending=[                           # dp_* target candidate
        {"stack": "s", "band": "", "key": "dp_433_TXPOWER", "kind": "r", "comp": "c",
         "name": "dp_433_TXPOWER", "expected": "x"}])}),
    _json.dumps({"version": 2, "prepared": _rec(pending=[                            # unsafe key form (prepared)
        {"stack": "s", "band": "", "key": "../evil", "kind": "r", "comp": "c",
         "name": "ropt", "expected": "x"}])}),
]


@pytest.mark.parametrize("bad", _BAD_JOURNALS)
def test_malformed_journal_blocks_without_mutation_or_deletion(tmp_path, monkeypatch, bad):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)                # a REAL update is available
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "dp_433_X": "keep"})   # protect a dp_* key
    before = _head(work)
    _rfs.write_marker(svc._paths, svc._paths.under("state", "selfupdate-migrate.json"), bad, 0o600)
    res = svc.self_update_apply()                                        # must not crash
    assert res.ok is False and res.data.get("journal_corrupt") is True   # typed recovery-blocked
    assert _head(work) == before                                         # NO source update
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert stored.get("ropt") == "OLD" and stored.get("dp_433_X") == "keep"   # NO deletion


def test_concurrent_apply_returns_busy_without_git_or_migration(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before = _head(work)
    held = _rfs.open_lock(svc._paths, svc._paths.under("state", "locks", "selfupdate.lock"))
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)                            # another process owns the op
    try:
        res = svc.self_update_apply()
        assert res.ok is False and res.data.get("busy") is True
        assert _head(work) == before                                    # no git ran
        assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # no migration ran
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()


def test_check_defers_while_apply_lock_held(tmp_path, monkeypatch):
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    selfupdate.refresh_cache(svc._system, svc._paths)                    # a pre-existing cached status
    before = selfupdate.read_cache(svc._paths).get("checked_at")
    held = _rfs.open_lock(svc._paths, svc._paths.under("state", "locks", "selfupdate.lock"))
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)                            # an apply owns the lock
    try:
        # the explicit check (also the code path the startup freshness thread uses) must DEFER
        res = svc.self_update_check()
        assert res.ok and res.data.get("deferred") is True
        assert selfupdate.read_cache(svc._paths).get("checked_at") == before   # no competing fetch/cache write
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()


# --- Defect 1: present-but-unreadable journal fails closed (tri-state read) --------------------

def _journal_path(svc):
    return svc._paths.under("state", "selfupdate-migrate.json")


def _make_state_dir(svc):
    p = _journal_path(svc).parent
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "directory", "fifo"])
def test_unreadable_journal_blocks_without_any_mutation(tmp_path, monkeypatch, kind):
    import os
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)                # a REAL update is available
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before_head = _head(work)
    before_cfg = dict(cfgmod.load_stack_config(svc._paths, "s"))
    selfupdate.refresh_cache(svc._system, svc._paths)                    # a pre-existing cache
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    _make_state_dir(svc)
    jp = _journal_path(svc)
    if kind == "symlink":
        (jp.parent / "real.json").write_text('{"version": 2, "completed": null, "prepared": null}')
        os.symlink("real.json", jp)                                      # a symlinked leaf -> unsafe
    elif kind == "dangling_symlink":
        os.symlink("does-not-exist.json", jp)
    elif kind == "directory":
        jp.mkdir()
    elif kind == "fifo":
        os.mkfifo(jp)                                                    # must NOT hang the reader
    env, blocked = selfupdate.read_migration_journal(svc._paths)
    assert env is None and blocked is True                              # tri-state: unreadable -> BLOCK
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("journal_corrupt") is True
    assert _head(work) == before_head                                   # no fetch/git mutation reached
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg      # no config mutation
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no cache mutation


def test_absent_journal_is_not_blocked(tmp_path, monkeypatch):
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    env, blocked = selfupdate.read_migration_journal(svc._paths)         # truly absent
    assert env is None and blocked is False                             # ONLY absent proceeds


# --- Defect 2: a prior pending migration can never be overwritten/lost -------------------------

def _seed_completed(svc, from_head, to_head, expected="OLD"):
    """A COMPLETED from_head->to_head transition whose migration is pending, ANCHORED (durable git
    provenance) so it is authorised."""
    _seed_journal(svc, from_head=from_head, to_head=to_head,
                  pending=[_cand(from_head, expected=expected)])


def _old_to_new_repo(tmp_path, monkeypatch):
    """A real OLD->NEW source transition: commit manifest OLD (A), bump to NEW (B). Returns
    (svc-at-B, work, up, a, b)."""
    _o, work, up = _repos(tmp_path)
    _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")                     # manifest OLD, HEAD=A
    a = _head(work)
    b = _bump_default(work, up, ropt="NEW")                              # manifest NEW, HEAD=B
    svc, man, rt = _svc(tmp_path, work)                                  # post-transition service (NEW)
    return svc, man, rt, work, up, a, b


def test_prior_pending_migration_write_failure_defers_and_recovers(tmp_path, monkeypatch):
    # A prior completed transition's pending is migrated FIRST (proven against its OWN record). If the
    # config write fails, the pending is PRESERVED and the update is DEFERRED (never stranded); a later
    # invocation recovers it.
    import lhpc.core.services as services_mod
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    _seed_completed(svc, a, b)
    fail = {"on": True}
    real_clear = services_mod.conditional_clear_stack_config
    def clr(*args, **k):
        if fail["on"]:
            raise OSError("disk full")
        return real_clear(*args, **k)
    monkeypatch.setattr(services_mod, "conditional_clear_stack_config", clr)
    res1 = svc.self_update_apply()
    assert res1.ok and res1.data.get("deferred_recovery") is True and res1.data.get("pending_migrations") >= 1
    env, bad = selfupdate.read_migration_journal(svc._paths)
    assert not bad and env["completed"]["pending"]                       # prior pending PRESERVED
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # nothing deleted
    fail["on"] = False
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    res2 = svc2.self_update_apply()
    assert res2.data.get("migrated") >= 1 and "ropt" not in cfgmod.load_stack_config(svc2._paths, "s")
    assert svc2.stack_config("s")["ropt"] == "NEW"


def test_finalization_clear_failure_self_heals(tmp_path, monkeypatch):
    # If the journal-clear write after a successful recovery migration fails, the config change stands
    # and the stale journal is self-healed (re-run finds keys absent and clears) on a later invocation.
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    _seed_completed(svc, a, b)
    real_clear = selfupdate.clear_migration_journal
    calls = {"n": 0}
    def clr(paths):
        calls["n"] += 1
        if calls["n"] == 1:                                             # finalize clear FAILS to run
            return
        return real_clear(paths)
    monkeypatch.setattr(selfupdate, "clear_migration_journal", clr)
    svc.self_update_apply()
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")       # migration applied
    assert selfupdate.read_migration_journal(svc._paths)[0] is not None   # stale journal lingered
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    svc2.self_update_apply()
    assert selfupdate.read_migration_journal(svc2._paths)[0] is None      # self-healed / cleared
    assert svc2.stack_config("s")["ropt"] == "NEW"


def test_head_matches_neither_transition_state_blocks(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before_head = _head(work)
    _seed_completed(svc, "a" * 40, "b" * 40)                             # completed.to_head is NOT current head
    before_env = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert _head(work) == before_head                                   # no git mutation
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # no config mutation
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_env  # journal preserved (evidence)


# --- Defect 1: journal `expected` is NOT authority — the pre-update default is proven from source ----

def test_forged_expected_run_param_does_not_delete_override(tmp_path, monkeypatch):
    """A structurally-valid journal candidate with forged expected="MINE" must NOT delete an
    operator's current MINE override when the real pre-update default (proven from source) differs."""
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)   # real old default = OLD
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "MINE"})              # a genuine override
    _seed_journal(svc, from_head=a, to_head=b,                               # ANCHORED, but `expected` FORGED
                  pending=[_cand(a, key="ropt", name="ropt", expected="MINE")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0                          # forged expected ignored
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "MINE"   # override PRESERVED


def test_forged_expected_file_param_does_not_delete_override(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)   # real fopt default = FOLD
    cfgmod.save_stack_config(svc._paths, "s", {"file_fopt": "MINE"})
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="file_fopt", kind="f", name="fopt", expected="MINE")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_fopt") == "MINE"


def test_candidate_with_non_owned_band_is_not_deleted(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})               # band "" value
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, band="999")])   # band not owned by stack s
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0 and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"    # nothing deleted


def test_genuine_transition_migrates_default_and_preserves_overrides(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s",
                             {"ropt": "OLD", "keep": "MINE", "file_fopt": ""})   # default / override / empty override
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") >= 1
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored                                              # old-default value migrated
    assert stored.get("keep") == "MINE" and stored.get("file_fopt") == ""    # true + empty overrides kept
    assert svc.stack_config("s")["ropt"] == "NEW"                            # now follows the new default


# --- Defect 2: the fail-closed journal gate also covers explicit + startup freshness checks --------

def _write_bad_journal(jp, kind):
    import os
    jp.parent.mkdir(parents=True, exist_ok=True)
    if kind == "malformed":
        jp.write_text("not json {{{")
    elif kind == "symlink":
        (jp.parent / "real.json").write_text('{"version": 2, "completed": null, "prepared": null}')
        os.symlink("real.json", jp)
    elif kind == "dangling_symlink":
        os.symlink("does-not-exist.json", jp)
    elif kind == "directory":
        jp.mkdir()
    elif kind == "fifo":
        os.mkfifo(jp)


@pytest.mark.parametrize("kind", ["malformed", "symlink", "dangling_symlink", "directory", "fifo"])
def test_check_blocks_on_unsafe_journal_without_fetch(tmp_path, monkeypatch, kind):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)                                                     # a REAL update is available
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    selfupdate.refresh_cache(svc._system, svc._paths)                        # a pre-existing cache
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    before_head, before_cfg = _head(work), dict(cfgmod.load_stack_config(svc._paths, "s"))
    _write_bad_journal(_journal_path(svc), kind)
    before_jbytes = _journal_path(svc).read_bytes() if kind in ("malformed", "symlink") else None
    res = svc.self_update_check()                                            # explicit check path
    assert res.ok is False and res.data.get("journal_corrupt") is True       # typed blocked, no 500
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # NO fetch / cache write
    assert _head(work) == before_head                                        # no git mutation
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg           # no config mutation
    if before_jbytes is not None:
        assert _journal_path(svc).read_bytes() == before_jbytes              # journal unchanged


def test_startup_freshness_check_under_corrupt_journal_makes_no_fetch(tmp_path, monkeypatch):
    # The startup thread calls ControllerService().self_update_check(); under a corrupt journal it must
    # block with no competing fetch or cache write (and never raise).
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    selfupdate.refresh_cache(svc._system, svc._paths)
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    _write_bad_journal(_journal_path(svc), "malformed")
    res = svc.self_update_check()                                            # the exact call the startup thread makes
    assert res.ok is False and res.data.get("journal_corrupt") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no competing fetch/cache write


# --- Defect 1: a candidate's own from_head must NOT select the manifest (record transition binds) ----

def _typed_manifest(kind, default):
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="{kind}"\n  default="{default}"\n')


def test_forged_candidate_from_head_does_not_select_manifest_run(tmp_path, monkeypatch):
    """A candidate forged to point at a DIFFERENT reachable commit X (where the default is MINE), while
    the recorded transition is A->B, must not delete a genuine MINE override: the RECORD's from_head
    (A) selects the manifest, not the candidate's."""
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    x = _seed_manifest(work, up, _rf_manifest(ropt="MINE"))              # commit X: default MINE
    _seed_manifest(work, up, _rf_manifest(ropt="OLD"))                   # commit A: default OLD
    b = _seed_manifest(work, up, _rf_manifest(ropt="NEW"))               # commit B (HEAD): default NEW
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "MINE"})          # a genuine override
    # forged journal: from_head = X (a MINE manifest), to_head = current HEAD, but NO matching anchor
    _seed_journal(svc, from_head=x, to_head=b, pending=[_cand(x, expected="MINE")], anchored=False)
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True     # typed recovery block
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "MINE"   # override PRESERVED


def test_forged_candidate_from_head_does_not_select_manifest_file(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    x = _seed_manifest(work, up, _rf_manifest(fopt="MINE"))
    _seed_manifest(work, up, _rf_manifest(fopt="FOLD"))
    b = _seed_manifest(work, up, _rf_manifest(fopt="FNEW"))
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"file_fopt": "MINE"})
    _seed_journal(svc, from_head=x, to_head=b, anchored=False,
                  pending=[_cand(x, key="file_fopt", kind="f", name="fopt", expected="MINE")])
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_fopt") == "MINE"


def test_real_apply_created_journal_migrates_genuine_old_default(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")      # work + origin at A (manifest OLD)
    (up / "manifest.toml").write_text(_rf_manifest("NEW"))               # upstream advances to B (manifest NEW)
    _git(up, "add", "manifest.toml"); _git(up, "commit", "-m", "NEW"); _git(up, "push", "-q", "origin", "main")
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})           # at the (old) default
    res = svc.self_update_apply()                                        # REAL A->B; journal created by apply
    assert res.ok and not res.data.get("already") and res.data.get("migrated") >= 1
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")       # genuine old-default migrated
    svc2, _m, _r = _svc(tmp_path, work)
    assert svc2.stack_config("s")["ropt"] == "NEW"


# --- Defect 2: use OLD (pre-update) param semantics for BOTH sides of the comparison ---------------

def test_typed_schema_change_preserves_override_under_old_semantics(tmp_path, monkeypatch):
    """Old param is str default '10'; new param is int (which normalises ' 10' -> '10'). A stored ' 10'
    is a genuine override under OLD str semantics and must survive — it must NOT be normalised into the
    old default by the NEW int validator."""
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _typed_manifest("str", "10"))           # OLD: str default "10"
    b = _seed_manifest(work, up, _typed_manifest("int", "10"))           # NEW: int (normalises " 10")
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": " 10"})           # override (leading space) under str
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="10")])   # anchored
    res = svc.self_update_apply()
    assert res.data.get("migrated") == 0                                 # NOT normalised into old default
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == " 10"   # override survives


# --- Defect 3: recovery-state gate on explicit + startup freshness checks (no fetch/mutation) -------

def test_check_blocks_on_head_mismatched_prepared_no_fetch(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)                                                 # a real update available (fetch would act)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    selfupdate.refresh_cache(svc._system, svc._paths)
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    before_head, before_cfg = _head(work), dict(cfgmod.load_stack_config(svc._paths, "s"))
    _seed_journal(svc, from_head="a" * 40, to_head="b" * 40, slot="prepared",   # anchored, HEAD matches neither
                  pending=[_cand("a" * 40)])
    before_journal = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_check()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # NO fetch / cache write
    assert _head(work) == before_head                                            # no source mutation
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg               # no config mutation
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_journal    # no journal mutation


def test_startup_check_blocks_on_recovery_state_no_fetch(tmp_path, monkeypatch):
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    _upstream_commit(up)
    selfupdate.refresh_cache(svc._system, svc._paths)
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    _seed_journal(svc, from_head="a" * 40, to_head="b" * 40,            # anchored completed, HEAD != to_head
                  pending=[_cand("a" * 40)])
    before_journal = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_check()                                        # the exact call the startup thread makes
    assert res.ok is False and res.data.get("recovery_required") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no competing fetch/cache write
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_journal


# --- Defect 1: durable git transaction anchor authorises migration; journal alone never does -------

def _dup_manifest(kind, name="dup", default="D"):
    """A stack whose param `name` is declared by TWO components (ambiguous flat key). `kind`: 'run' or
    'file'."""
    def comp(cid):
        p = (f'  [[stack.component.param]]\n  name="{name}"\n  kind="str"\n  default="{default}"\n'
             if kind == "run" else
             '  [stack.component.config_file]\n  path="{runtime}/config/files/' + cid + '.conf"\n  fmt="env"\n'
             f'    [[stack.component.config_file.param]]\n    name="{name}"\n    key="K"\n    default="{default}"\n')
        return (f'[[stack.component]]\nid="{cid}"\nname="{cid.upper()}"\nkind="service"\nrun="true"\n'
                f'readiness="process"\n{p}')
    return '[[stack]]\nid="s"\nname="S"\nmain="c1"\n' + comp("c1") + comp("c2")


def test_modified_journal_field_disagrees_with_anchor_blocked(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})           # would migrate if not blocked
    txid = _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    before_cfg = dict(cfgmod.load_stack_config(svc._paths, "s"))
    # tamper the runtime journal (a transition field) while the genuine ANCHOR is unchanged
    selfupdate.write_migration_journal(svc._paths, {
        "completed": {"from_head": "c" * 40, "to_head": b, "branch": "main", "txid": txid,
                      "pending": [_cand(a, expected="OLD")]}, "prepared": None})
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True   # disagrees with anchor -> block
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg          # nothing deleted


def test_duplicate_run_param_forged_flat_key_not_deleted(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _dup_manifest("run"))
    b = _bump_default_text(work, up, _dup_manifest("run", default="D2"))
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"dup": "D"})              # a FLAT value == old default
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="dup", name="dup", comp="c1")])  # forged flat key for an AMBIGUOUS name
    res = svc.self_update_apply()
    assert res.data.get("migrated") == 0 and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("dup") == "D"   # ambiguous flat -> NOT deleted


def test_duplicate_file_param_forged_flat_key_not_deleted(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _dup_manifest("file"))
    b = _bump_default_text(work, up, _dup_manifest("file", default="D2"))
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"file_dup": "D"})
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="file_dup", kind="f", name="dup", comp="c1")])
    res = svc.self_update_apply()
    assert res.data.get("migrated") == 0 and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_dup") == "D"


def test_unique_flat_run_and_file_candidates_migrate(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(ropt="OLD", fopt="FOLD"))
    b = _bump_default(work, up, ropt="NEW", fopt="FOLD")                 # ropt default changes; fopt unchanged
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "file_fopt": "FOLD"})   # unique FLAT keys at old default
    _seed_journal(svc, from_head=a, to_head=b, pending=[
        _cand(a, key="ropt", name="ropt", expected="OLD"),
        _cand(a, key="file_fopt", kind="f", name="fopt", expected="FOLD")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") >= 2
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored and "file_fopt" not in stored           # both unique flat keys migrated


@pytest.mark.parametrize("state", ["missing", "stale", "mismatched"])
@pytest.mark.parametrize("action", ["apply", "check"])
def test_bad_anchor_blocks_apply_and_check_without_mutation(tmp_path, monkeypatch, state, action):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    _upstream_commit(up)                                                 # a real update -> a fetch would act
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    selfupdate.refresh_cache(svc._system, svc._paths)
    if state == "missing":
        _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a)], anchored=False)
    elif state == "stale":
        txid = _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a)])
        selfupdate.delete_anchor(svc._system, txid)                     # anchor removed after the fact
    else:  # mismatched: genuine anchor, journal tampered to disagree
        txid = _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a)])
        selfupdate.write_migration_journal(svc._paths, {
            "completed": {"from_head": "c" * 40, "to_head": b, "branch": "main", "txid": txid,
                          "pending": [_cand(a)]}, "prepared": None})
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    before_head = _head(work)
    before_cfg = dict(cfgmod.load_stack_config(svc._paths, "s"))
    before_journal = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_apply() if action == "apply" else svc.self_update_check()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no fetch / cache write
    assert _head(work) == before_head                                            # no source mutation
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg               # no config deletion
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_journal    # no journal mutation


# --- Defect 1: a no-candidate update writes no anchor/journal; defensive write rejection -----------

def test_no_candidate_update_leaves_no_invalid_journal(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")      # work + origin at A
    (up / "manifest.toml").write_text(_rf_manifest("NEW"))               # upstream advances to B
    _git(up, "add", "manifest.toml"); _git(up, "commit", "-m", "NEW"); _git(up, "push", "-q", "origin", "main")
    # NO stored config -> zero legacy candidates. Inject a finalization/clear failure that (with the
    # fix) is never even reached, so no invalid intermediate record can be left behind.
    monkeypatch.setattr(selfupdate, "clear_migration_journal", lambda *a, **k: False)
    res = svc.self_update_apply()                                        # REAL A->B advance, no candidates
    assert res.ok and not res.data.get("already") and res.data.get("migrated") == 0
    env, blocked = selfupdate.read_migration_journal(svc._paths)
    assert env is None and blocked is False                              # NO journal (valid or invalid) left
    refs = svc._system.runner.run(["git", "-C", str(work), "for-each-ref", selfupdate._ANCHOR_NS], timeout=5.0)
    assert refs.stdout.strip() == ""                                     # NO anchor left
    # a fresh service can still check + apply normally (no recovery block)
    svc2, _m, _r = _svc(tmp_path, work)
    assert svc2.self_update_check().data.get("recovery_required") is None
    assert svc2.self_update_apply().data.get("recovery_required") is None


def test_write_migration_journal_rejects_invalid_non_null_record(tmp_path):
    paths = _paths(tmp_path)
    good = {"from_head": "a" * 40, "to_head": "b" * 40, "branch": "main",
            "txid": "a" * 16, "pending": [_cand("a" * 40)]}
    assert selfupdate.write_migration_journal(paths, {"completed": {**good, "txid": ""},   # EMPTY txid
                                                      "prepared": None}) is False
    assert selfupdate.write_migration_journal(paths, {"completed": {**good, "pending": []},  # EMPTY pending
                                                      "prepared": None}) is False
    d = {"completed": {"from_head": "a" * 40, "to_head": "b" * 40, "branch": "main", "txid": "",
                       "pending": [_cand("a" * 40)]}}
    assert selfupdate.write_migration_journal(paths, d) is False
    assert selfupdate.read_migration_journal(paths)[0] is None       # nothing was persisted
    assert selfupdate.write_migration_journal(paths, {"completed": good, "prepared": None}) is True   # valid accepted


# --- Defect 2: current-manifest safety gate uses a FRESH post-update parse ------------------------

def _rf_manifest_no_ropt(fopt="FOLD"):
    """Post-update manifest where the run param `ropt` was REMOVED (keep + file `fopt` remain)."""
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n'
        '  [stack.component.config_file]\n  path="{runtime}/config/files/x.conf"\n  fmt="env"\n'
        f'    [[stack.component.config_file.param]]\n    name="fopt"\n    key="FOPT"\n    default="{fopt}"\n')


def _rf_manifest_no_fopt(ropt="OLD"):
    """Post-update manifest where the file param `fopt` was REMOVED (run params remain)."""
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{ropt}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n')


def test_run_param_removed_in_new_manifest_is_preserved_pending(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(ropt="OLD"))              # OLD: ropt exists
    b = _bump_default_text(work, up, _rf_manifest_no_ropt())            # NEW: ropt REMOVED, HEAD=B
    svc, man, rt = _svc(tmp_path, work)                                 # SAME service, current manifest = NEW
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})          # value at old default
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    res = svc.self_update_apply()
    assert res.data.get("deferred_recovery") is True and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"   # removed param -> NOT deleted


def test_file_param_removed_in_new_manifest_is_preserved_pending(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(fopt="FOLD"))
    b = _bump_default_text(work, up, _rf_manifest_no_fopt())            # fopt REMOVED
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"file_fopt": "FOLD"})
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="file_fopt", kind="f", name="fopt", expected="FOLD")])
    res = svc.self_update_apply()
    assert res.data.get("deferred_recovery") is True and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_fopt") == "FOLD"


def test_fresh_service_retry_of_removed_param_is_non_destructive(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    _o, work, up = _repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(ropt="OLD"))
    b = _bump_default_text(work, up, _rf_manifest_no_ropt())
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    svc.self_update_apply()                                             # deferred (ropt removed)
    # a FRESH post-update service retries the pending candidate: still unprovable -> non-destructive
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    res2 = svc2.self_update_apply()
    assert res2.data.get("deferred_recovery") is True and res2.data.get("recovery_required") is None
    assert cfgmod.load_stack_config(svc2._paths, "s").get("ropt") == "OLD"   # still preserved


def test_retained_param_migrates_with_stale_incumbent_service_cache(tmp_path, monkeypatch):
    # The service's manifest cache is populated PRE-transition (old); the fresh current-manifest parse
    # must still let a RETAINED param migrate, preserving genuine + empty overrides.
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")     # work + origin at A (ropt OLD)
    svc.stacks()                                                        # populate _stacks from the OLD tree
    (up / "manifest.toml").write_text(_rf_manifest("NEW"))              # upstream -> B (ropt retained, default NEW)
    _git(up, "add", "manifest.toml"); _git(up, "commit", "-m", "NEW"); _git(up, "push", "-q", "origin", "main")
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "keep": "MINE", "file_fopt": ""})
    res = svc.self_update_apply()                                       # REAL A->B; migration in-process
    assert res.ok and res.data.get("migrated") >= 1
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored                                        # retained param migrated
    assert stored.get("keep") == "MINE" and stored.get("file_fopt") == ""   # overrides preserved


def test_removed_param_preserved_despite_stale_service_cache(tmp_path, monkeypatch):
    # THE defect-2 case: the incumbent service populated _stacks PRE-transition (OLD, HAS ropt), then
    # advances in-process to a manifest where ropt is REMOVED. Using the stale cache would delete the
    # stored key; the fresh post-update parse keeps it pending instead.
    from lhpc.core import config as cfgmod
    _o, work, up = _repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")     # work + origin at A (ropt OLD)
    svc.stacks()                                                        # populate _stacks from OLD (HAS ropt)
    (up / "manifest.toml").write_text(_rf_manifest_no_ropt())           # upstream -> B: ropt REMOVED
    _git(up, "add", "manifest.toml"); _git(up, "commit", "-m", "rm ropt"); _git(up, "push", "-q", "origin", "main")
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})          # at old default -> a candidate at A
    res = svc.self_update_apply()                                       # REAL A->B in the SAME (stale) service
    assert res.ok and res.data.get("pending_migrations", 0) >= 1        # ropt unprovable in NEW -> pending
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"   # stale cache must NOT delete it


# --- last_apply envelope field (one-click web update outcome) ----------------------------------

def test_record_last_apply_merges_and_survives_refresh(env):
    selfupdate.refresh_cache(env["sys"], env["paths"])
    selfupdate.record_last_apply_strict(env["paths"], ok=True, summary="Update applied.", now=7)
    v = selfupdate.status_view(env["paths"])
    assert v["last_apply"] == {"ok": True, "summary": "Update applied.", "finished_at": 7}
    # a later refresh (check/apply) CARRIES the outcome forward, like identity
    selfupdate.refresh_cache(env["sys"], env["paths"])
    assert selfupdate.status_view(env["paths"])["last_apply"]["summary"] == "Update applied."
    # and the other envelope fields survived the merge
    assert selfupdate.status_view(env["paths"])["available"] is True


def test_record_last_apply_without_prior_cache(tmp_path):
    from lhpc.core.paths import Paths
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    selfupdate.record_last_apply_strict(p, ok=False, summary="x" * 9000, now=1)   # bounded
    v = selfupdate.status_view(p)
    assert v["last_apply"]["ok"] is False and len(v["last_apply"]["summary"]) <= 512


def test_last_apply_malformed_is_rejected_safely(tmp_path):
    from lhpc.core.paths import Paths
    import json
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    # wrong-typed last_apply invalidates the WHOLE envelope (schema discipline) -> grey view
    (tmp_path / "state" / "selfupdate.json").write_text(
        json.dumps({"schema_version": 1, "local": {"is_git": True},
                    "last_apply": {"ok": "yes"}}))
    v = selfupdate.status_view(p)
    assert v["last_apply"] is None and v["available"] is False




# --- one-click updater: marker trigger + de-systemctl'd run-service (escape-proof design) -------

def _upd_svc(tmp_path, *, invocation=True, monkeypatch=None):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    fake = FakeSystem()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    svc._fake = fake
    # integration ok requires the canonical unit set for THIS root — install it under a temp
    # unit dir the service reads.
    from lhpc.core import updater_units as U
    ud = tmp_path / "units"; ud.mkdir()
    root = str(tmp_path)
    _, co, venv = U.deployment_paths(root)
    for k in U.ALL_UNITS:
        (ud / k).write_text(U.render(k, root, co, venv))
    svc._user_unit_dir = lambda: ud
    if monkeypatch is not None:
        monkeypatch.setenv("INVOCATION_ID", "x") if invocation else monkeypatch.delenv("INVOCATION_ID", raising=False)
    return svc


def _seed_available(tmp_path, **identity):
    import json
    env = {"schema_version": 1, "local": {"is_git": True, "head": "a", "head_short": "a",
                                          "branch": "main"},
           "upstream": {"ok": True, "upstream_version": "9.9", "upstream_head": "b",
                        "upstream_head_short": "b"}, "checked_at": 1}
    if identity:
        env["identity"] = identity
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "selfupdate.json").write_text(json.dumps(env))


def test_trigger_writes_exclusive_request_no_systemctl(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    r = svc.self_update_trigger(overwrite=False)
    assert r.ok and r.data["mode"] == "normal"
    assert (tmp_path / "state" / "selfupdate.request").read_text().strip() == "normal"
    # NO systemctl was ever called by the trigger
    assert not any(c and c[0] == "systemctl" for c in svc._fake.calls)
    # a SECOND trigger while pending is refused (exclusive admission)
    r2 = svc.self_update_trigger(overwrite=True)
    assert not r2.ok and r2.data.get("already_pending")


def test_trigger_overwrite_payload(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    assert svc.self_update_trigger(overwrite=True).data["mode"] == "overwrite"
    assert (tmp_path / "state" / "selfupdate.request").read_text().strip() == "overwrite"


def test_trigger_refuses_foreground_process(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, invocation=False, monkeypatch=monkeypatch)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    r = svc.self_update_trigger()
    assert not r.ok and r.data.get("not_managed")
    assert not (tmp_path / "state" / "selfupdate.request").exists()


def test_trigger_refuses_when_integration_not_ok(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    (svc._user_unit_dir() / "lhpc-web.service").write_text("[Service]\nExecStart=/usr/bin/other\n")
    r = svc.self_update_trigger()
    assert not r.ok and r.data.get("integration") in ("incomplete", "ambiguous", "foreign")


def test_trigger_refuses_unsafe_identity(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _seed_available(tmp_path, ok=False, status="unsafe", reason="bad", checked_at=1)
    assert svc.self_update_trigger().data.get("identity_unsafe")
    assert not (tmp_path / "state" / "selfupdate.request").exists()


def _write_request(tmp_path, mode="normal"):
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "selfupdate.request").write_text(mode + "\n")


def test_run_service_claims_applies_records_no_systemctl(tmp_path, monkeypatch):
    from lhpc.core.services import ActionResult, ControllerService
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _write_request(tmp_path, "normal")
    order = []
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: (order.append(force),
                                                      ActionResult(True, "Already up to date.",
                                                                   data={"already": True}))[1])
    res = svc.self_update_run_service()
    assert res.ok and order == [False]
    # request claimed + in-flight released; NO systemctl anywhere
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not (tmp_path / "state" / "selfupdate.inflight").exists()
    assert not any("systemctl" in " ".join(c) for c in svc._fake.calls)
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is True


def test_run_service_overwrite_mode_from_request(tmp_path, monkeypatch):
    from lhpc.core.services import ActionResult, ControllerService
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _write_request(tmp_path, "overwrite")
    seen = {}
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: (seen.__setitem__("f", force),
                                                      ActionResult(True, "ok", data={"already": True}))[1])
    svc.self_update_run_service()
    assert seen["f"] is True


def test_run_service_noop_without_request(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    res = svc.self_update_run_service()
    assert res.ok and res.data.get("noop")


def test_run_service_malformed_request_is_recovery(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _write_request(tmp_path, "wat")
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("malformed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # retained for recovery
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is False


def test_run_service_syncs_venv_and_p2_failure_fails(tmp_path, monkeypatch):
    import sys
    from lhpc.core.probes.backends import CommandResult
    from lhpc.core.services import ActionResult, ControllerService
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: tmp_path / "co")
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "Update applied.",
                                                                  data={"already": False}))
    pip = (sys.executable, "-m", "pip", "install", "-e", str(tmp_path / "co"))
    # success path: pip ok -> update ok
    svc._fake.commands[pip] = CommandResult(returncode=0, stdout="", stderr="")
    _write_request(tmp_path, "normal")
    assert svc.self_update_run_service().ok
    # P2: pip FAILS -> the whole update is reported FAILED and recorded red
    svc._fake.commands[pip] = CommandResult(returncode=1, stdout="", stderr="boom")
    _write_request(tmp_path, "normal")
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("venv_sync_failed")
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is False


def test_run_service_record_failure_retains_inflight(tmp_path, monkeypatch):
    """P2: if the STRICT last-apply write does not persist, the in-flight marker is retained
    (one-click blocked until recovery) — never deleted on an unrecorded outcome."""
    from lhpc.core import selfupdate as _su
    from lhpc.core.services import ActionResult, ControllerService
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _write_request(tmp_path, "normal")
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "ok", data={"already": True}))
    monkeypatch.setattr(_su, "record_last_apply_strict", lambda *a, **k: False)   # durable write fails
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("record_failed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # retained -> recovery required


def test_run_service_refuses_preexisting_inflight_preserving_both(tmp_path, monkeypatch):
    """P1a: a request appearing while an in-flight record already exists must NOT clobber the
    in-flight evidence — the helper fails closed (recovery_required) and preserves both files."""
    from lhpc.core.services import ControllerService
    applied = []
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: applied.append(1))
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    (tmp_path / "state" / "selfupdate.inflight").write_text('{"mode":"normal","pid":4242,"start_time":7}')
    _write_request(tmp_path, "overwrite")
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("recovery_required") and not applied
    assert (tmp_path / "state" / "selfupdate.request").read_text() == "overwrite\n"   # preserved
    assert (tmp_path / "state" / "selfupdate.inflight").read_text() == \
        '{"mode":"normal","pid":4242,"start_time":7}'                                 # NOT clobbered


def test_run_service_inflight_only_is_noop(tmp_path, monkeypatch):
    """In-flight present but no request -> nothing to claim -> no-op, in-flight untouched."""
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    (tmp_path / "state" / "selfupdate.inflight").write_text('{"mode":"normal","pid":1,"start_time":1}')
    res = svc.self_update_run_service()
    assert res.ok and res.data.get("noop")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()


def test_run_service_concurrent_claim_admits_one(tmp_path, monkeypatch):
    """Two claims against ONE request admit exactly one; the loser fails closed, evidence intact."""
    from lhpc.core.services import ActionResult, ControllerService
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "ok", data={"already": True}))
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _write_request(tmp_path, "normal")
    r1 = svc.self_update_run_service()                 # claims + runs, releases inflight
    r2 = svc.self_update_run_service()                 # nothing left to claim
    winners = [r for r in (r1, r2) if r.ok and not r.data.get("noop")]
    noops = [r for r in (r1, r2) if r.data.get("noop") or r.data.get("recovery_required")]
    assert len(winners) == 1 and len(noops) == 1


def test_run_service_final_unlink_failure_retains_evidence(tmp_path, monkeypatch):
    """P2: a failed FINAL in-flight unlink -> incomplete/cleanup_failed, evidence retained."""
    from lhpc.core import runtime_fs
    from lhpc.core.services import ActionResult, ControllerService
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _write_request(tmp_path, "normal")
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "ok", data={"already": True}))
    monkeypatch.setattr(runtime_fs, "unlink", lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))
    res = svc.self_update_run_service()
    assert not res.ok and res.data.get("cleanup_failed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # retained
    # but the outcome WAS recorded durably before the unlink attempt
    assert selfupdate.status_view(svc._paths)["last_apply"] is not None


def test_recover_strict_record_failure_keeps_inflight(tmp_path, monkeypatch):
    """Recovery must NOT clear the in-flight evidence if it cannot durably record the incomplete
    outcome."""
    import json
    from lhpc.core import selfupdate as _su
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": 999999, "start_time": 1}))   # dead helper
    monkeypatch.setattr(_su, "record_last_apply_strict", lambda *a, **k: False)
    res = svc.self_update_recover_request()
    assert not res.ok and res.data.get("record_failed")
    assert (tmp_path / "state" / "selfupdate.inflight").exists()   # NOT silently cleared


# --- request classification + recovery -------------------------------------------------------

def test_classify_and_recover_pending(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _write_request(tmp_path, "normal")
    assert svc.classify_request() == "pending"
    r = svc.self_update_recover_request()
    assert r.ok and r.data["cleared"] == "pending"
    assert svc.classify_request() == "absent"


def test_recover_inflight_requires_dead_helper(tmp_path, monkeypatch):
    import json
    import os
    from lhpc.core.services import _proc_start_time
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    # in-flight owned by a LIVE process (this test) -> recovery refuses
    me = os.getpid()
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": me, "start_time": _proc_start_time(me)}))
    assert svc.classify_request() == "in_flight"
    assert not svc.self_update_recover_request().ok               # helper still alive
    # a DEAD process identity -> recovery clears + records incomplete
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": 999999, "start_time": 1}))
    r = svc.self_update_recover_request()
    assert r.ok and r.data["cleared"] == "in_flight"
    assert selfupdate.status_view(svc._paths)["last_apply"]["ok"] is False


def test_classify_malformed_inflight(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    (tmp_path / "state" / "selfupdate.inflight").write_text("{ not json")
    assert svc.classify_request() == "malformed"
    assert not svc.self_update_recover_request().ok               # never auto-cleared


def test_integration_recovery_required_when_inflight(tmp_path, monkeypatch):
    import json
    import os
    from lhpc.core.services import _proc_start_time
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    me = os.getpid()
    (tmp_path / "state" / "selfupdate.inflight").write_text(
        json.dumps({"mode": "normal", "pid": me, "start_time": _proc_start_time(me)}))
    assert svc.updater_integration()["status"] == "recovery_required"


# --- repair-integration: refuse active drop-ins (P1b) ----------------------------------------

def _repair_svc(tmp_path, monkeypatch, show_for):
    """A repair-ready temp deployment: canonical unit dir + a checkout/.git, with the three
    `systemctl show` responses supplied by `show_for(kind, want) -> stdout`."""
    from lhpc.core.probes.backends import CommandResult
    from lhpc.core import updater_units as U
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    (tmp_path / "src" / "loraham-pi-control" / ".git").mkdir(parents=True)   # self-hosted checkout
    ud = svc._user_unit_dir()
    for kind in U.ALL_UNITS:
        argv = ("systemctl", "--user", "show", "-p", "FragmentPath", "-p", "DropInPaths", kind)
        svc._fake.commands[argv] = CommandResult(returncode=0, stdout=show_for(kind, str(ud / kind)),
                                                 stderr="")
    return svc


def test_repair_success_with_no_dropins(tmp_path, monkeypatch):
    svc = _repair_svc(tmp_path, monkeypatch,
                      lambda k, want: f"FragmentPath={want}\nDropInPaths=\n")
    res = svc.self_update_repair_integration()
    assert res.ok, res.summary
    assert (tmp_path / ".lhpc-root").exists()                     # marker written on success
    assert any("enable" in c for c in svc._fake.calls) and any("restart" in c for c in svc._fake.calls)


def test_repair_refuses_active_dropin(tmp_path, monkeypatch):
    svc = _repair_svc(tmp_path, monkeypatch,
                      lambda k, want: f"FragmentPath={want}\n"
                      + ("DropInPaths=/etc/systemd/user/lhpc-web.service.d/x.conf\n"
                         if k == "lhpc-web.service" else "DropInPaths=\n"))
    res = svc.self_update_repair_integration()
    assert not res.ok and res.data.get("dropin") == "lhpc-web.service"
    assert not (tmp_path / ".lhpc-root").exists()                 # NO marker after refusal
    assert not any("enable" in c or "restart" in c for c in svc._fake.calls)   # no enable/restart


def test_repair_refuses_wrong_fragment(tmp_path, monkeypatch):
    svc = _repair_svc(tmp_path, monkeypatch,
                      lambda k, want: "FragmentPath=/some/other/place.service\nDropInPaths=\n")
    res = svc.self_update_repair_integration()
    assert not res.ok and res.data.get("shadowed")
    assert not (tmp_path / ".lhpc-root").exists()
    assert not any("enable" in c or "restart" in c for c in svc._fake.calls)


# --- console self-repair-and-update (legacy same-root migration) ------------------------------

def _legacy_svc(tmp_path, monkeypatch, *, seed_show=True):
    """A managed console (INVOCATION_ID) with LEGACY same-root units (modified_ours web+helper,
    no .path) -> fixable `incomplete`. Seeds the systemctl responses repair needs."""
    from lhpc.core.probes.backends import CommandResult
    from lhpc.core import updater_units as U
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)        # canonical units + INVOCATION_ID
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    (tmp_path / "src" / "loraham-pi-control" / ".git").mkdir(parents=True)   # self-hosted checkout
    ud = svc._user_unit_dir(); root = str(tmp_path)
    (ud / "lhpc-web.service").write_text(
        f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={root}\n"
        f"ExecStart={root}/venv/lhpc/bin/lhpc web --host 127.0.0.1 --port 8770\n")
    (ud / "lhpc-selfupdate.service").write_text(
        f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={root}\n"
        f"ExecStart={root}/venv/lhpc/bin/lhpc self-update --run-service\n")
    (ud / "lhpc-selfupdate.path").unlink()                   # -> missing
    if seed_show:
        svc._fake.commands[("systemctl", "--user", "show", "-p", "Version")] = \
            CommandResult(returncode=0, stdout="Version=257\n", stderr="")
        for kind in U.ALL_UNITS:
            svc._fake.commands[("systemctl", "--user", "show", "-p", "FragmentPath",
                                "-p", "DropInPaths", kind)] = \
                CommandResult(returncode=0, stdout=f"FragmentPath={ud/kind}\nDropInPaths=\n", stderr="")
        # the watcher enable + is-active probe (restart=False migration) succeed by default
        svc._fake.commands[("systemctl", "--user", "enable", "--now", "lhpc-selfupdate.path")] = \
            CommandResult(returncode=0, stdout="", stderr="")
        svc._fake.commands[("systemctl", "--user", "is-active", "--quiet", "lhpc-selfupdate.path")] = \
            CommandResult(returncode=0, stdout="", stderr="")
    return svc


def test_repair_integration_restart_false_installs_starts_path_no_restart(tmp_path, monkeypatch):
    from lhpc.core import updater_units as U
    svc = _legacy_svc(tmp_path, monkeypatch)
    res = svc.self_update_repair_integration(restart=False)
    assert res.ok, res.summary
    ud = svc._user_unit_dir()
    for k in U.ALL_UNITS:                                    # all three now canonical
        assert (ud / k).read_text() == U.render(k, *U.deployment_paths(str(tmp_path)))
    calls = svc._fake.calls
    assert ["systemctl", "--user", "enable", "--now", "lhpc-selfupdate.path"] in calls
    assert not any(c[:3] == ["systemctl", "--user", "restart"] for c in calls)   # restart=False


def test_repair_removes_stale_same_root_overwrite_only(tmp_path, monkeypatch):
    svc = _legacy_svc(tmp_path, monkeypatch)
    ud = svc._user_unit_dir(); root = str(tmp_path)
    ov = ud / "lhpc-selfupdate-overwrite.service"
    ov.write_text(f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={root}\n"
                  f"ExecStart={root}/venv/lhpc/bin/lhpc self-update --run-service --overwrite\n")
    svc.self_update_repair_integration(restart=False)
    assert not ov.exists()                                   # same-root stale variant removed
    # a FOREIGN overwrite unit is left untouched
    ov.write_text("[Service]\nEnvironment=LHPC_RUNTIME_ROOT=/elsewhere\nExecStart=/elsewhere/x\n")
    svc.self_update_repair_integration(restart=False)
    assert ov.exists()


def test_repair_and_trigger_migrates_then_writes_marker(tmp_path, monkeypatch):
    svc = _legacy_svc(tmp_path, monkeypatch)
    assert svc.updater_integration()["fixable"] and svc.updater_integration()["status"] == "incomplete"
    res = svc.self_update_repair_and_trigger(overwrite=False)
    assert res.ok and res.data.get("triggered")
    assert (tmp_path / "state" / "selfupdate.request").read_text().strip() == "normal"
    assert svc.updater_integration()["status"] == "ok"       # units converged to canonical


def test_repair_and_trigger_ok_delegates_without_repair(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)        # already-canonical units
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    res = svc.self_update_repair_and_trigger()
    assert res.ok and res.data.get("triggered")
    assert not any("daemon-reload" in " ".join(c) for c in svc._fake.calls)   # no repair happened


def test_repair_and_trigger_bus_unavailable_writes_nothing(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult
    svc = _legacy_svc(tmp_path, monkeypatch, seed_show=False)
    svc._fake.commands[("systemctl", "--user", "show", "-p", "Version")] = \
        CommandResult(returncode=1, stdout="", stderr="Failed to connect to bus")
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("bus_unavailable")
    assert not (tmp_path / "state" / "selfupdate.request").exists()           # nothing written
    assert not any(c[:3] == ["systemctl", "--user", "enable"] for c in svc._fake.calls)


def test_repair_and_trigger_refuses_unfixable(tmp_path, monkeypatch):
    svc = _upd_svc(tmp_path, monkeypatch=monkeypatch)
    _seed_available(tmp_path, ok=True, status="ok", reason="ok", checked_at=1)
    # one ambiguous unit -> not fixable
    (svc._user_unit_dir() / "lhpc-web.service").write_text("[Service]\nExecStart=/usr/bin/other\n")
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("unfixable")
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not any("daemon-reload" in " ".join(c) for c in svc._fake.calls)   # no writes/repair


# --- migration-path audit fixes: managed gate, watcher-active, overwrite identity --------------

def test_repair_and_trigger_refuses_foreground_no_writes(tmp_path, monkeypatch):
    """P1a: a FOREGROUND console (no INVOCATION_ID) must refuse BEFORE any unit write/enable/
    marker — even with fixable units and a reachable bus."""
    svc = _legacy_svc(tmp_path, monkeypatch)
    monkeypatch.delenv("INVOCATION_ID", raising=False)      # foreground console
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("not_managed")
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not (tmp_path / ".lhpc-root").exists()
    assert not any("daemon-reload" in " ".join(c) or c[:4] == ["systemctl", "--user", "enable", "--now"]
                   for c in svc._fake.calls)


def test_repair_restart_false_fails_when_watcher_enable_nonzero(tmp_path, monkeypatch):
    """P1b: a nonzero `enable --now .path` fails the migration repair before the root marker."""
    from lhpc.core.probes.backends import CommandResult
    svc = _legacy_svc(tmp_path, monkeypatch)
    svc._fake.commands[("systemctl", "--user", "enable", "--now", "lhpc-selfupdate.path")] = \
        CommandResult(returncode=1, stdout="", stderr="boom")
    res = svc.self_update_repair_integration(restart=False)
    assert not res.ok and res.data.get("path_watcher_failed")
    assert not (tmp_path / ".lhpc-root").exists()           # marker not written


def test_repair_restart_false_fails_when_watcher_not_active(tmp_path, monkeypatch):
    """P1b: watcher not active after enable -> repair fails; via repair_and_trigger no request."""
    from lhpc.core.probes.backends import CommandResult
    svc = _legacy_svc(tmp_path, monkeypatch)
    svc._fake.commands[("systemctl", "--user", "is-active", "--quiet", "lhpc-selfupdate.path")] = \
        CommandResult(returncode=3, stdout="inactive", stderr="")
    res = svc.self_update_repair_and_trigger()
    assert not res.ok and res.data.get("path_watcher_failed")
    assert not (tmp_path / "state" / "selfupdate.request").exists()
    assert not (tmp_path / ".lhpc-root").exists()


def test_repair_success_proves_path_active_before_marker(tmp_path, monkeypatch):
    """P1b: the happy migration proves the watcher active AND queues the request."""
    svc = _legacy_svc(tmp_path, monkeypatch)
    res = svc.self_update_repair_and_trigger()
    assert res.ok and res.data.get("triggered")
    assert ["systemctl", "--user", "is-active", "--quiet", "lhpc-selfupdate.path"] in svc._fake.calls
    assert (tmp_path / "state" / "selfupdate.request").exists()


def test_repair_restart_true_unaffected_by_watcher_active(tmp_path, monkeypatch):
    """restart=True (CLI) stays best-effort: an inactive watcher does not fail it (web restart
    pulls the .path up via Wants=)."""
    from lhpc.core.probes.backends import CommandResult
    svc = _legacy_svc(tmp_path, monkeypatch)
    svc._fake.commands[("systemctl", "--user", "is-active", "--quiet", "lhpc-selfupdate.path")] = \
        CommandResult(returncode=3, stdout="inactive", stderr="")
    assert svc.self_update_repair_integration(restart=True).ok
    assert (tmp_path / ".lhpc-root").exists()


def _write_overwrite_unit(ud, root, execline):
    (ud / "lhpc-selfupdate-overwrite.service").write_text(
        f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={root}\nExecStart={execline}\n")


def test_overwrite_removed_only_with_old_helper_execstart(tmp_path, monkeypatch):
    """P2: same-root env + the exact old overwrite ExecStart -> removed; a same-root unit with a
    DIFFERENT ExecStart is left in place with a cleanup note; foreign env is left."""
    ud = None
    svc = _legacy_svc(tmp_path, monkeypatch); ud = svc._user_unit_dir(); root = str(tmp_path)
    # (a) correct old overwrite variant -> removed
    _write_overwrite_unit(ud, root, f"{root}/venv/lhpc/bin/lhpc self-update --run-service --overwrite")
    assert svc.self_update_repair_integration(restart=False).ok
    assert not (ud / "lhpc-selfupdate-overwrite.service").exists()
    # (b) same root but WRONG ExecStart -> left + note
    _write_overwrite_unit(ud, root, "/usr/bin/somethingelse")
    res = svc.self_update_repair_integration(restart=False)
    assert (ud / "lhpc-selfupdate-overwrite.service").exists()
    assert any("not the recognised old overwrite helper" in d for d in res.details)
    # (c) foreign env -> left
    _write_overwrite_unit(ud, "/elsewhere", "/elsewhere/venv/lhpc/bin/lhpc self-update --run-service --overwrite")
    svc.self_update_repair_integration(restart=False)
    assert (ud / "lhpc-selfupdate-overwrite.service").exists()
