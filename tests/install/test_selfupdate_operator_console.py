"""What `lhpc self-update --apply` does to a RUNNING console: it is stopped only right before the
checkout is changed, and started again only when this apply stopped it.

A refusal or a no-op ("No changes were made", "Already up to date") leaves the console alone — on
box H an identity refusal still restarted lhpc-web (RESULT-GO1 finding 2). The real CLI, service and
git run underneath; only `systemctl` is answered (the console is active) and recorded.
"""
from __future__ import annotations

import pytest

import gitrepo
from lhpc.core import selfupdate
from lhpc.core.probes.backends import CommandResult

STOP = ["systemctl", "--user", "stop", "lhpc-web.service"]
START = ["systemctl", "--user", "start", "lhpc-web.service"]


def _tree(root):
    """Every file under state/ and config/ with its bytes (lock files are opened, never written)."""
    return {str(p.relative_to(root)): p.read_bytes()
            for d in ("state", "config") if (root / d).is_dir()
            for p in sorted((root / d).rglob("*")) if p.is_file()}


@pytest.fixture()
def cli(env, monkeypatch):
    """`main` with the console ACTIVE: each systemctl call is recorded with the HEAD and the state/ +
    config/ files at that moment; the venv sync and the unit refresh are stubbed (not this behaviour)."""
    from lhpc.adapters.cli.main import main
    from lhpc.core.probes.backends import RealCommandRunner
    from lhpc.core.services import ControllerService
    root = env["paths"].runtime_root
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(root))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    calls = []
    real = RealCommandRunner.run

    def run(self, argv, timeout=None, *a, **kw):
        if argv[0] != "systemctl":
            return real(self, argv, timeout, *a, **kw)
        calls.append((list(argv), gitrepo.git(env["work"], "rev-parse", "HEAD"), _tree(root)))
        return CommandResult(0, "", "")                      # is-active: active; stop/start: done
    monkeypatch.setattr(RealCommandRunner, "run", run)
    monkeypatch.setattr(selfupdate, "venv_sync", lambda *a, **k: CommandResult(0, "", ""))
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update", lambda self: (True, "n/a"))
    return main, calls


def _service_calls(calls):
    return [c[0] for c in calls if c[0][2] in ("stop", "start")]


def _diverge(env):
    gitrepo.upstream_commit(env["up"])
    (env["work"] / "diverge.txt").write_text("local commit\n")
    gitrepo.git(env["work"], "add", "-A")
    gitrepo.git(env["work"], "commit", "-m", "local")


def _dirty(env):
    gitrepo.upstream_commit(env["up"])
    (env["work"] / "pyproject.toml").write_text("# mine\n")


def _unsafe_identity(env, monkeypatch):
    from types import SimpleNamespace

    from lhpc.core.services import ControllerService
    gitrepo.upstream_commit(env["up"])
    monkeypatch.setattr(ControllerService, "controller", lambda self: SimpleNamespace(
        branch="main", remote="https://example.invalid/loraham-pi-control.git"))
    monkeypatch.setattr(ControllerService, "controller_identity_live", lambda self: {
        "status": "unsafe", "reason": "origin is not the approved canonical remote"})


def _unreachable(env):
    gitrepo.upstream_commit(env["up"])
    gitrepo.git(env["work"], "remote", "set-url", "origin", str(env["work"].parent / "gone.git"))


@pytest.mark.parametrize("make, rc, said", [
    (lambda env, mp: _unsafe_identity(env, mp), 1, "Self-update blocked: unsafe controller identity"),
    (lambda env, mp: None, 0, "Already up to date."),
    (lambda env, mp: _dirty(env), 1, "Local changes are present"),
    (lambda env, mp: _diverge(env), 1, "the local branch has diverged from upstream"),
    (lambda env, mp: _unreachable(env), 1, "Could not reach upstream"),
], ids=["identity", "already", "dirty", "diverged", "unreachable"])
def test_a_refusal_or_a_no_op_neither_stops_nor_starts_the_console(env, cli, monkeypatch, capsys, make, rc, said):
    main, calls = cli
    make(env, monkeypatch)
    head = gitrepo.git(env["work"], "rev-parse", "HEAD")
    assert main(["self-update", "--apply", "--yes"]) == rc
    out = capsys.readouterr().out
    assert said in out, out
    assert _service_calls(calls) == [], calls
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == head


def test_the_diverged_refusal_keeps_its_remedy(env, cli, capsys):
    """Refused before the console is touched, the divergence still offers the overwrite (the only remedy)."""
    main, _calls = cli
    _diverge(env)
    assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "lhpc self-update --apply --overwrite" in out and "discards exactly those local" in out, out


def test_an_advance_stops_the_console_before_anything_changes_then_starts_it(env, cli, capsys):
    """The stop comes after the gates and before the first change: at the stop the checkout is at the old
    head and nothing under state/ or config/ differs from before the apply; then the console starts."""
    main, calls = cli
    root = env["paths"].runtime_root
    old = gitrepo.git(env["work"], "rev-parse", "HEAD")
    new = gitrepo.upstream_commit(env["up"])
    before = _tree(root)
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert _service_calls(calls) == [STOP, START], calls
    stop = next(c for c in calls if c[0] == STOP)
    assert stop[1] == old and stop[2] == before
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == new


def test_a_late_release_of_the_console_lock_is_waited_for(env, cli, monkeypatch, capsys):
    """The stopped console lets go of its SHARED lock a moment late: the bounded retry takes it and the
    update goes on. The release happens in the retry's own wait (no real clock)."""
    import time as _time
    main, calls = cli
    holder = selfupdate.controller_runtime_lock(env["paths"], exclusive=False)
    holder.__enter__()
    waits = []

    def wait(s):
        waits.append(s)
        holder.__exit__(None, None, None)
    monkeypatch.setattr(_time, "sleep", wait)
    new = gitrepo.upstream_commit(env["up"])
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert len(waits) == 1 and _service_calls(calls) == [STOP, START], (waits, calls)
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == new


def test_another_holder_of_the_console_lock_is_busy_and_the_console_is_started_again(env, cli, monkeypatch, capsys):
    """Something else keeps the controller-runtime lock past the retry: the apply refuses with nothing
    changed, without hanging, and starts the console it had stopped."""
    from lhpc.core.services import ControllerService
    main, calls = cli
    monkeypatch.setattr(ControllerService, "_LOCK_WAIT_S", 0.0)        # the deadline is reached at once
    old = gitrepo.git(env["work"], "rev-parse", "HEAD")
    gitrepo.upstream_commit(env["up"])
    with selfupdate.controller_runtime_lock(env["paths"], exclusive=False):
        assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "did not release the controller-runtime lock — no changes made." in out, out
    assert _service_calls(calls) == [STOP, START], calls
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == old
