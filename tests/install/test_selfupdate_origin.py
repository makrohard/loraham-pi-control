"""Self-update follows the checkout's own `origin`, whichever repo it is, and says where updates come
from: "updates from <origin>", with "(different from the official repo <url>)" when it is not the
manifest's `[controller].remote`. A URL is shown without its user/token part, everywhere.

The real CLI, service and git run against repositories on this disk (never the network): `fork` is a
bare repo standing in for someone's own copy of LHPC, `up` advances it.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import gitrepo
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult

MARK = "different from the official repo"


def _line(url, official=None):
    return f"updates from {url}" + (f" ({MARK} {official})" if official else "")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A SELF-HOSTED box: the checkout at <rt>/src/loraham-pi-control is a clone of `fork`, lhpc runs
    from it, the console is not running. The venv sync, the unit refresh and the stack sweep after an
    advance are stubbed (not this behaviour)."""
    import lhpc
    from lhpc.adapters.cli.main import main
    from lhpc.core.probes.backends import RealCommandRunner
    from lhpc.core.services import ControllerService
    fork, _work, up = gitrepo.repos(tmp_path)
    (up / "lhpc" / "__init__.py").write_text("")
    gitrepo.git(up, "add", "-A")
    gitrepo.git(up, "commit", "-m", "package")
    gitrepo.git(up, "push", "origin", "main")
    rt = tmp_path / "rt"
    (rt / "state").mkdir(parents=True)
    co = rt / "src" / "loraham-pi-control"
    gitrepo.git(tmp_path, "clone", str(fork), str(co))
    for d in (rt, rt / "src", co):
        os.chmod(d, 0o700)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: co)
    monkeypatch.setattr(lhpc, "__file__", str(co / "lhpc" / "__init__.py"))
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(rt))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    real = RealCommandRunner.run

    def run(self, argv, timeout=None, *a, **kw):
        if argv[0] == "systemctl":
            return CommandResult(3, "", "")                  # lhpc-web is not running
        return real(self, argv, timeout, *a, **kw)
    monkeypatch.setattr(RealCommandRunner, "run", run)
    monkeypatch.setattr(selfupdate, "venv_sync", lambda *a, **k: CommandResult(0, "", ""))
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update", lambda self: (True, "n/a"))
    monkeypatch.setattr(ControllerService, "_stacks_with_an_update", lambda self, python: "")
    official = ControllerService(paths=Paths(runtime_root=rt)).controller().remote
    return SimpleNamespace(main=main, rt=rt, co=co, fork=fork, up=up, official=official)


def _official(monkeypatch, remote):
    """The manifest's `[controller].remote` (the official repo) set to `remote`."""
    from lhpc.core.manifest import ControllerSpec
    from lhpc.core.services import ControllerService
    spec = ControllerSpec("loraham-pi-control", "LHPC", "src/loraham-pi-control", "main", remote)
    monkeypatch.setattr(ControllerService, "controller", lambda self: spec)


def _svc(w):
    from lhpc.core.probes.backends import RealSystem
    from lhpc.core.services import ControllerService
    return ControllerService(system=RealSystem(), paths=Paths(runtime_root=w.rt))


def test_the_check_follows_a_fork_and_names_it(world, capsys):
    """Test 1: a self-hosted checkout whose origin is a fork: the check runs and names the fork."""
    new = gitrepo.upstream_commit(world.up)
    assert world.main(["self-update"]) == 0
    out = capsys.readouterr().out
    assert _line(world.fork, world.official) in out.splitlines(), out
    assert "Update available" in out and new[:9] in out, out


def test_the_apply_follows_a_fork_and_names_it(world, capsys):
    """Test 2: `--apply` fast-forwards to the fork's main, and its answer names the fork."""
    new = gitrepo.upstream_commit(world.up)
    assert world.main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert _line(world.fork, world.official) in out.splitlines(), out
    assert gitrepo.git(world.co, "rev-parse", "HEAD") == new


def test_the_one_click_update_answer_names_the_fork(world):
    """Test 2b: the console's one-click update (the helper the POST starts) answers with the line."""
    new = gitrepo.upstream_commit(world.up)
    (world.rt / "state" / "selfupdate.request").write_text("normal\n")
    res = _svc(world).self_update_run_service()
    assert res.ok, res
    assert _line(world.fork, world.official) in res.details, res
    assert gitrepo.git(world.co, "rev-parse", "HEAD") == new


def test_the_official_repo_is_named_without_a_marker(world, monkeypatch, capsys):
    """Test 3: origin IS the official repo: the line names it and nothing more."""
    _official(monkeypatch, str(world.fork))
    gitrepo.upstream_commit(world.up)
    assert world.main(["self-update"]) == 0
    out = capsys.readouterr().out
    assert _line(world.fork) in out.splitlines() and MARK not in out, out


def test_an_insteadof_rewrite_is_named_as_the_repo_actually_fetched(world, tmp_path, capsys):
    """Test 5: `url.<x>.insteadOf` sends the fetch elsewhere: the line names that repo, and the check
    reports that repo's head."""
    mirror = tmp_path / "mirror.git"
    gitrepo.git(tmp_path, "clone", "--bare", str(world.fork), str(mirror))
    m = tmp_path / "m"
    gitrepo.git(tmp_path, "clone", str(mirror), str(m))
    (m / "mirror.txt").write_text("only in the mirror\n")
    gitrepo.git(m, "add", "-A")
    gitrepo.git(m, "commit", "-m", "mirror")
    gitrepo.git(m, "push", "origin", "main")
    mirror_head = gitrepo.git(m, "rev-parse", "HEAD")
    gitrepo.git(world.co, "remote", "set-url", "origin", "https://example.invalid/lhpc.git")
    gitrepo.git(world.co, "config", f"url.{mirror}.insteadOf", "https://example.invalid/lhpc.git")
    assert world.main(["self-update"]) == 0
    out = capsys.readouterr().out
    assert _line(mirror, world.official) in out.splitlines(), out
    assert mirror_head[:9] in out, out


def test_the_console_shows_where_updates_come_from(world, web, capsys):
    """Test 6: the console's update panel shows the line from the cached check (no git on GET)."""
    assert world.main(["self-update"]) == 0
    capsys.readouterr()
    body = web(paths=Paths(runtime_root=world.rt)).get("/stacks").get_data(as_text=True)
    assert _line(world.fork, world.official) in body


def test_a_token_in_the_origin_is_never_shown_or_stored(world, web, capsys):
    """Test 8: an origin carrying a user and token: the line names it without them, and the token is
    in no output, no answer, no page, not in the cached envelope and not in the controller data."""
    gitrepo.git(world.co, "remote", "set-url", "origin", "http://user:S3CRET@127.0.0.1:1/lhpc.git")
    shown = _line("http://127.0.0.1:1/lhpc.git", world.official)
    assert world.main(["self-update"]) == 1                    # unreachable, port 1
    out = capsys.readouterr().out
    assert shown in out.splitlines() and "S3CRET" not in out, out
    assert world.main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert shown in out.splitlines() and "S3CRET" not in out, out
    body = web(paths=Paths(runtime_root=world.rt)).get("/stacks").get_data(as_text=True)
    assert shown in body and "S3CRET" not in body
    assert "S3CRET" not in (world.rt / "state" / "selfupdate.json").read_text()
    svc = _svc(world)
    assert "S3CRET" not in repr(svc.self_update_status()) + repr(svc.controller_status())


def test_a_token_in_the_official_url_is_never_shown(world, monkeypatch, capsys):
    """Test 8: the manifest's official URL carrying userinfo is named without it."""
    _official(monkeypatch, "https://user:C4NON@github.com/makrohard/loraham-pi-control.git")
    assert world.main(["self-update"]) == 0
    out = capsys.readouterr().out
    assert _line(world.fork, "https://github.com/makrohard/loraham-pi-control.git") in out.splitlines(), out
    assert "C4NON" not in out and "C4NON" not in (world.rt / "state" / "selfupdate.json").read_text()


def test_a_token_in_a_fetch_error_is_never_returned(tmp_path, monkeypatch):
    """Test 8: a git that echoes the credentialed URL in its fetch error (older gits did): the error
    is returned without the token, and the result still says where it tried to update from."""
    from lhpc.core.probes.backends import FakeSystem
    monkeypatch.setattr(selfupdate, "repo_root", lambda: tmp_path)
    url = "https://user:S3CRET@example.invalid/lhpc.git"
    g = lambda *a: ("git", "-C", str(tmp_path), *a)
    cmds = {g("rev-parse", "HEAD"): CommandResult(0, "h\n", ""),
            g("rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "main\n", ""),
            g("status", "--porcelain"): CommandResult(0, "", ""),
            g("remote", "get-url", "origin"): CommandResult(0, url + "\n", ""),
            g("fetch", "--quiet", "--force", "origin", "--", "refs/heads/main:refs/remotes/origin/main"):
                CommandResult(128, "", f"fatal: unable to access '{url}/': Could not resolve host\n")}
    out = selfupdate.check_upstream(FakeSystem(commands=cmds).system, "main", official="https://x.invalid/o.git")
    assert out["ok"] is False and "S3CRET" not in repr(out), out
    assert "https://example.invalid/lhpc.git" in out["error"], out
    assert out["origin"]["line"] == _line("https://example.invalid/lhpc.git", "https://x.invalid/o.git")


def test_a_degenerate_official_url_never_matches(world, monkeypatch, capsys):
    """Test 9: an official URL that normalises to "" never counts as matching an origin."""
    _official(monkeypatch, ".git")
    assert world.main(["self-update"]) == 0
    out = capsys.readouterr().out
    assert _line(world.fork, ".git") in out.splitlines(), out
    assert _svc(world).self_update_status()["origin_canonical"] is False


def test_a_plain_checkout_names_its_origin_too(env, monkeypatch, capsys):
    """Test 10: a developer checkout (not self-hosted: the identity check does not apply) gets the
    same line."""
    from lhpc.adapters.cli.main import main
    from lhpc.core.services import ControllerService
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(env["paths"].runtime_root))
    official = ControllerService(paths=env["paths"]).controller().remote
    origin = gitrepo.git(env["work"], "remote", "get-url", "origin")
    assert main(["self-update"]) == 0
    out = capsys.readouterr().out
    assert _line(origin, official) in out.splitlines(), out


def test_an_unreachable_fork_still_says_where_it_tried(world, tmp_path, capsys):
    """Test 11: the fetch fails: the check reports the error AND where it tried to update from."""
    gone = tmp_path / "gone.git"
    gitrepo.git(world.co, "remote", "set-url", "origin", str(gone))
    assert world.main(["self-update"]) == 1
    out = capsys.readouterr().out
    assert "Could not reach upstream" in out and _line(gone, world.official) in out.splitlines(), out
