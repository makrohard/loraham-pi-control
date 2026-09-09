"""Advancing the checkout: what `lhpc self-update` reports, what it refuses, and what an
operator-driven apply does to the running console.

Divergence, a dirty tree and untracked files are all REFUSALS with an explicit override, and the
override has to disclose exactly what it would discard — an operator who cannot see that is being
asked to consent to nothing. The two behaviours that grew out of this one live next door:
`test_selfupdate_migration.py` (config migration after a successful advance) and
`test_selfupdate_service.py` (the one-click updater the web console triggers).
"""
from __future__ import annotations

import pytest

import gitrepo
import repo_paths
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult

from pathlib import Path

# --- local state + upstream check --------------------------------------------------------------

def test_historical_manifest_survives_the_runner_capture_cap():
    """`git show <ref>:manifest` must come back WHOLE through the real runner.

    The runner keeps only the last _MAX_CAPTURE_BYTES of a stream — right for a build log, fatal
    for output that is DATA. The self-update config migration parses the pre-update manifest to
    learn a parameter's OLD default; once the manifest outgrew the cap, git show returned a
    beheaded file, TOML parsing failed at line 1, `_prove_candidate` swallowed it as "unprovable",
    and config-default migrations silently stopped completing while self-update deferred.

    This asserts the real thing end to end, so the next time the manifest grows past the cap a
    test says so instead of a Pi quietly failing to migrate.
    """
    import tomllib

    from lhpc.core import manifest as manifest_mod
    from lhpc.core.probes import RealSystem
    from lhpc.core.probes.backends import _MAX_CAPTURE_BYTES

    repo = repo_paths.REPO
    rel = "lhpc/data/manifest.example.toml"
    size = (repo / rel).stat().st_size
    assert size < _MAX_CAPTURE_BYTES, (
        f"{rel} is {size} bytes, at/over the {_MAX_CAPTURE_BYTES}-byte runner capture cap — "
        f"`git show` would return it beheaded and every config-default migration would stop "
        f"completing. Raise _MAX_CAPTURE_BYTES.")

    r = RealSystem().runner.run(["git", "-C", str(repo), "show", f"HEAD:{rel}"], timeout=20.0)
    assert r.returncode == 0, r.stderr[:200]
    # Truncation keeps the TAIL, so the tell is a missing HEAD: compare the first line, not a
    # length (len() counts characters after UTF-8 decode, and this file is full of em dashes).
    first_line = (repo / rel).read_text(encoding="utf-8").split("\n", 1)[0]
    assert r.stdout.startswith(first_line), (
        f"output was beheaded by the capture cap — starts {r.stdout[:40]!r}")
    stacks = manifest_mod.parse_manifest(tomllib.loads(r.stdout))      # the step that used to fail
    assert {s.id for s in stacks} >= {"daemon", "kiss", "graywolf"}


def test_previous_release_manifest_still_parses():
    """The config-default migration parses the manifest of the release being upgraded FROM
    (`service_params`: `git show <from_head>:...`), so a key that release wrote must stay
    accepted even when nothing reads it any more."""
    import tomllib

    from lhpc.core import manifest as manifest_mod
    from lhpc.core.probes import RealSystem
    repo = repo_paths.REPO
    r = RealSystem().runner.run(["git", "-C", str(repo), "show",
                                 "v0.2.10:lhpc/data/manifest.example.toml"], timeout=20.0)
    if r.returncode != 0:
        pytest.skip("v0.2.10 tag not available in this checkout")
    stacks = manifest_mod.parse_manifest(tomllib.loads(r.stdout))
    assert {s.id for s in stacks} >= {"daemon", "kiss", "graywolf"}


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
    # decide availability — repo_root raising proves the GET path never calls it (Issue A).
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
    assert res["ok"] is False and "diverged" in res["message"]
    assert "\n" not in res["message"]                          # single clean line (no raw git block)
    # The refusal must carry its OWN remedy: a diverged history can only be resolved by the reset
    # path, and a dead-end refusal left the operator with git surgery (live-found on a Zero).
    assert res.get("needs_overwrite") is True


@pytest.mark.contract
def test_only_proven_divergence_offers_the_destructive_overwrite(env, monkeypatch):
    """`--overwrite` runs `reset --hard` + `clean -ffd`. Offering it for a lock file, a read-only
    filesystem or a corrupt index would destroy untracked work without repairing the fault, so the
    offer requires ancestry-proven divergence (audit)."""
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
    assert "diverged" not in res["message"]
    assert "index.lock" in res["message"]                       # the REAL fault survives

    monkeypatch.setattr(selfupdate, "ff_blocked", lambda system, branch="": True)
    res = selfupdate.apply_update(env["sys"], env["paths"])
    assert res["ok"] is False and res.get("needs_overwrite") is True and "diverged" in res["message"]


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
    assert "diverged" not in res["message"] and "No space left" in res["message"]


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
    assert err.startswith("fatal: unable to access remote")
    res = selfupdate.apply_update(FakeSystem(commands=cmds).system, gitrepo.runtime_paths(tmp_path))
    assert res["ok"] is False and "\n" not in res["message"] and "─" not in res["message"]


# --- CLI operator apply: WARN-then-DO (stop web -> apply -> restart) ----------------------------

def _op_svc(tmp_path, cmds):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
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


def _pip_key(root):
    import sys
    return (sys.executable, "-m", "pip", "install", "-e", str(root))


def _op_inactive(tmp_path, monkeypatch, apply_result, *, pip=None):
    """An operator svc with web INACTIVE, a stubbed self_update_apply and repo_root, and (optionally) a
    stubbed pip result. Returns (svc, fake, root)."""
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService
    from lhpc.core import selfupdate
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    root = tmp_path / "repo"; root.mkdir()
    monkeypatch.setattr(selfupdate, "repo_root", staticmethod(lambda: root))
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False: apply_result)
    cmds = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"): CR(1, "", "")}   # off
    if pip is not None:
        cmds[_pip_key(root)] = pip
    svc, fake = _op_svc(tmp_path, cmds)
    return svc, fake, root


def test_operator_inactive_real_advance_syncs_venv(tmp_path, monkeypatch):
    # Item 1: web inactive + a REAL advance still synchronizes the editable venv (no service control).
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(tmp_path, monkeypatch,
                                   ActionResult(True, "advanced", data={}), pip=CR(0, "", ""))
    # NOT about systemd units: this root is a temp directory with no checkout, so the post-update
    # unit refresh has nothing real to verify. The refresh has its own coverage below and in
    # test_updater_units.py, against a canonical unit set.
    monkeypatch.setattr(type(svc), "_refresh_units_post_update", lambda self: (True, "n/a"))
    r = svc.self_update_apply_operator()
    assert r.ok and r.data.get("update_applied")
    assert list(_pip_key(root)) in fake.calls                                    # venv sync ran
    assert not any(c[:3] == ["systemctl", "--user", "stop"] for c in fake.calls)  # no service control


def test_operator_inactive_already_current_no_sync(tmp_path, monkeypatch):
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(tmp_path, monkeypatch,
                                   ActionResult(True, "already up to date", data={"already": True}))
    r = svc.self_update_apply_operator()
    assert r.ok
    assert list(_pip_key(root)) not in fake.calls                                # NO sync on a no-op


def test_operator_inactive_apply_failure_no_sync(tmp_path, monkeypatch):
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(tmp_path, monkeypatch,
                                   ActionResult(False, "apply refused", data={"busy": True}))
    r = svc.self_update_apply_operator()
    assert not r.ok
    assert list(_pip_key(root)) not in fake.calls                                # NO sync on a failure


def test_operator_inactive_sync_failure_is_typed(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ActionResult
    svc, fake, root = _op_inactive(tmp_path, monkeypatch,
                                   ActionResult(True, "advanced", data={}), pip=CR(1, "boom traceback", ""))
    r = svc.self_update_apply_operator()
    assert not r.ok and r.data.get("venv_sync_failed") and r.data.get("update_applied")
    assert "pip install -e" in r.summary                                         # bounded recovery command


def test_operator_admission_held_across_apply_and_sync(tmp_path, monkeypatch):
    # Item 1: admission is NEVER released between apply and venv sync — the lock is held during BOTH.
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService, ActionResult
    from lhpc.core import selfupdate
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    root = tmp_path / "repo"; root.mkdir()
    monkeypatch.setattr(selfupdate, "repo_root", staticmethod(lambda: root))
    held = {}
    def fake_apply(self, *, force=False):
        held["apply"] = self._held_counts().get(self.ADMISSION_KEY, 0)
        return ActionResult(True, "advanced", data={})
    monkeypatch.setattr(ControllerService, "self_update_apply", fake_apply)
    cmds = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"): CR(1, "", ""),
            _pip_key(root): CR(0, "", "")}
    svc, fake = _op_svc(tmp_path, cmds)
    real_run = fake.run
    def rec_run(argv, timeout, cwd=None, env=None):
        if list(argv) == list(_pip_key(root)):
            held["sync"] = svc._held_counts().get(svc.ADMISSION_KEY, 0)
        return real_run(argv, timeout, cwd=cwd, env=env)
    monkeypatch.setattr(fake, "run", rec_run)
    svc.self_update_apply_operator()
    assert held.get("apply", 0) > 0 and held.get("sync", 0) > 0                   # held across BOTH


def test_operator_active_restart_failure_is_not_success(tmp_path, monkeypatch):
    # Item 2: a failed required restart is ALWAYS ok=False, retaining accurate partial-update info.
    from lhpc.core.probes.backends import CommandResult as CR
    from lhpc.core.services import ControllerService, ActionResult
    from lhpc.core import selfupdate
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    root = tmp_path / "repo"; root.mkdir()
    monkeypatch.setattr(selfupdate, "repo_root", staticmethod(lambda: root))
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: ActionResult(True, "advanced", data={}))
    cmds = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"): CR(0, "", ""),   # ON
            ("systemctl", "--user", "stop", "lhpc-web.service"): CR(0, "", ""),
            _pip_key(root): CR(0, "", ""),
            ("systemctl", "--user", "start", "lhpc-web.service"): CR(1, "", "boom")}   # restart FAILS
    svc, fake = _op_svc(tmp_path, cmds)
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
    monkeypatch.setattr(su, "apply_update", lambda *a, **k: {
        "ok": False, "needs_overwrite": True,
        "message": "Update could not be applied — the local branch has diverged from upstream."})
    monkeypatch.setattr(ControllerService, "_self_update_blockers", lambda self: None)
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
            ("systemctl", "--user", "enable", "--now", U.RESTART_PATH_UNIT): CR(0, "", ""),
            ("systemctl", "--user", "enable", U.WEB_UNIT): CR(0, "", ""),
            ("systemctl", "--user", "enable", U.BOOT_RESTORE_UNIT): CR(0, "", ""),
            ("systemctl", "--user", "restart", U.WEB_UNIT): CR(0, "", ""),
            # restart=False (the web self-repair bridge) additionally proves the watchers are live
            ("systemctl", "--user", "is-active", "--quiet", U.PATH_UNIT): CR(0, "", ""),
            ("systemctl", "--user", "is-active", "--quiet", U.RESTART_PATH_UNIT): CR(0, "", "")}
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
    # P2-3: when deps changed, the guidance carries the EXACT (venv) sync command AND a clear note that
    # the operator must sync, and that LHPC never installs packages itself.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    i = selfupdate.restart_instructions(deps_changed=True,
                                        deps_sync_cmd="/rt/venv/lhpc/bin/python -m pip install -e /rt/co")
    assert any("/rt/venv/lhpc/bin/python -m pip install -e /rt/co" in c for c in i["commands"])
    assert "never installs" in i["note"] and "sync" in i["note"].lower()
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


