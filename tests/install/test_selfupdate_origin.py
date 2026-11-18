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


def test_a_refusal_right_before_the_advance_still_names_the_source(world, monkeypatch, capsys):
    """Test 2c: the firewall preflight refuses right before the checkout would move: that refusal is the
    answer, and it still ends with where updates come from; nothing moved."""
    from lhpc.core.service_base import ActionResult
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "firewall_update_nginx_preflight",
                        lambda self: ActionResult(False, "Firewall preflight refused."))
    head = gitrepo.git(world.co, "rev-parse", "HEAD")
    gitrepo.upstream_commit(world.up)
    assert world.main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "Firewall preflight refused." in out and _line(world.fork, world.official) in out.splitlines(), out
    assert gitrepo.git(world.co, "rev-parse", "HEAD") == head


def test_the_one_click_update_never_returns_a_token(world, capfd):
    """Test 8: the one-click helper with an origin carrying a user and token (unreachable): its whole
    answer, serialised, names the origin without them, and nothing is printed with them."""
    import dataclasses
    import json
    gitrepo.git(world.co, "remote", "set-url", "origin", "http://user:S3C'R3T@127.0.0.1:1/lhpc.git")
    (world.rt / "state" / "selfupdate.request").write_text("normal\n")
    res = _svc(world).self_update_run_service()
    answer = json.dumps(dataclasses.asdict(res), default=str)
    assert _line("http://127.0.0.1:1/lhpc.git", world.official) in res.details, res
    seen = capfd.readouterr()
    for text in (answer, seen.out, seen.err, (world.rt / "state" / "selfupdate.json").read_text()):
        assert "S3C" not in text and "R3T" not in text, text


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


# A token, one with a quote (git's http transport takes it), one with an unescaped slash (git refuses it and
# echoes the URL), one with a space (git keeps it as typed) and one with an `@`: S3C and R3T are its two halves,
# neither may show.
TOKENS = ["S3CR3T", "S3C'R3T", "S3C/R3T", "S3C R3T", "S3C@R3T"]


def _no_token(*texts):
    for text in texts:
        assert "S3C" not in text and "R3T" not in text, text


@pytest.mark.parametrize("token", TOKENS, ids=["plain", "quote", "slash", "space", "at"])
def test_a_token_in_the_origin_is_never_shown_or_stored(world, web, capfd, token):
    """Test 8: an origin carrying a user and token: the line names it without them, and the token is
    in no output (stdout or stderr), no answer, no page, not in the cached envelope and not in the
    controller data, serialised as JSON too."""
    import json
    gitrepo.git(world.co, "remote", "set-url", "origin", f"http://user:{token}@127.0.0.1:1/lhpc.git")
    shown = _line("http://127.0.0.1:1/lhpc.git", world.official)
    assert world.main(["self-update"]) == 1                    # unreachable, port 1
    seen = capfd.readouterr()
    assert shown in seen.out.splitlines(), seen.out
    _no_token(seen.out, seen.err)
    assert world.main(["self-update", "--apply", "--yes"]) == 1
    seen = capfd.readouterr()
    assert shown in seen.out.splitlines(), seen.out
    _no_token(seen.out, seen.err)
    body = web(paths=Paths(runtime_root=world.rt)).get("/stacks").get_data(as_text=True)
    assert shown in body
    svc = _svc(world)
    _no_token(body, (world.rt / "state" / "selfupdate.json").read_text(),
              json.dumps([svc.self_update_status(), svc.controller_status()], default=str))


@pytest.mark.parametrize("token", TOKENS, ids=["plain", "quote", "slash", "space", "at"])
def test_a_token_in_the_official_url_is_never_shown(world, monkeypatch, capfd, token):
    """Test 8: the manifest's official URL carrying userinfo is named without it."""
    _official(monkeypatch, f"https://user:{token}@github.com/makrohard/loraham-pi-control.git")
    assert world.main(["self-update"]) == 0
    seen = capfd.readouterr()
    assert _line(world.fork, "https://github.com/makrohard/loraham-pi-control.git") in seen.out.splitlines(), seen.out
    _no_token(seen.out, seen.err, (world.rt / "state" / "selfupdate.json").read_text())


@pytest.mark.parametrize("token,pad,colour", [("S3CR3T", 0, ""), ("S3CR3T", 173, ""), ("S3C'R3T", 0, ""),
                                              ("S3C/R3T", 0, ""), ("S3CR3T", 0, "\x1b[31m")],
                         ids=["short", "cut-before-the-at", "quote", "slash", "coloured"])
def test_a_token_in_a_fetch_error_is_never_returned(tmp_path, monkeypatch, web, token, pad, colour):
    """Test 8: a git (or a transport wrapper) that echoes the credentialed URL in its fetch error: the
    error is returned without the token, and the result still says where it tried to update from. With
    `pad`, the error's 200-character cut falls between the token and its `@`; with `colour`, an ANSI
    colour code is glued to the URL's scheme. The answer goes on through the cache, the controller
    data and the console page without the token too."""
    import json
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(selfupdate, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    (tmp_path / "rt" / "state").mkdir(parents=True)
    url = f"https://user:{token}@example.invalid/lhpc.git"
    g = lambda *a: ("git", "-C", str(tmp_path), *a)
    cmds = {g("rev-parse", "HEAD"): CommandResult(0, "h\n", ""),
            g("rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "main\n", ""),
            g("status", "--porcelain"): CommandResult(0, "", ""),
            g("config", "--get", "remote.origin.url"): CommandResult(0, url + "\n", ""),
            g("remote", "get-url", "origin"): CommandResult(0, url + "\n", ""),
            g("fetch", "--quiet", "--force", "origin", "--", "refs/heads/main:refs/remotes/origin/main"):
                CommandResult(128, "", "r" * pad + f"fatal: '{colour}{url}{colour and chr(27) + '[0m'}/': "
                                       "Could not resolve host\n")}
    system = FakeSystem(commands=cmds).system
    out = selfupdate.check_upstream(system, "main", official="https://x.invalid/o.git")
    assert out["ok"] is False, out
    _no_token(json.dumps(out))
    assert "https://example.invalid" in out["error"] or pad, out
    assert out["origin"]["line"] == _line("https://example.invalid/lhpc.git", "https://x.invalid/o.git")
    svc = ControllerService(system=system, paths=Paths(runtime_root=tmp_path / "rt"))
    monkeypatch.setattr(ControllerService, "_official_remote", lambda self: "https://x.invalid/o.git")
    res = svc.self_update_check()
    body = web(paths=Paths(runtime_root=tmp_path / "rt"), system=system).get("/stacks").get_data(as_text=True)
    _no_token(json.dumps([res.summary, res.details, res.data, svc.self_update_status(), svc.controller_status()],
                         default=str), (tmp_path / "rt" / "state" / "selfupdate.json").read_text(), body)


@pytest.mark.parametrize("origin", ["fork", "/"], ids=["fork", "origin-also-degenerate"])
def test_a_degenerate_official_url_never_matches(world, monkeypatch, capsys, origin):
    """Test 9: an official URL that normalises to "" never counts as matching an origin, not even
    one that normalises to "" as well ("/": "" == "" must not read as the official repo)."""
    _official(monkeypatch, ".git")
    url = world.fork if origin == "fork" else origin
    gitrepo.git(world.co, "remote", "set-url", "origin", str(url))
    world.main(["self-update"])                             # "/" cannot be fetched: the line stays
    out = capsys.readouterr().out
    assert _line(url, ".git") in out.splitlines(), out
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


@pytest.mark.parametrize("token", TOKENS, ids=["plain", "quote", "slash", "space", "at"])
def test_the_no_origin_remedy_never_shows_a_token(world, monkeypatch, capfd, token):
    """Test 8: a checkout without origin is refused with `git remote add origin <official>`; an
    official URL carrying userinfo is printed without it, in the command and in the text."""
    _official(monkeypatch, f"https://user:{token}@github.com/makrohard/loraham-pi-control.git")
    gitrepo.git(world.co, "remote", "remove", "origin")
    assert world.main(["self-update", "--apply", "--yes"]) == 1
    seen = capfd.readouterr()
    assert "remote add origin https://github.com/makrohard/loraham-pi-control.git" in seen.out, seen.out
    _no_token(seen.out, seen.err)


@pytest.mark.parametrize("how", [("config", "--unset", "remote.origin.url"), ("config", "remote.origin.url", ""),
                                 ("remote", "remove", "origin")], ids=["url-unset", "url-empty", "removed"])
def test_an_origin_without_a_url_is_no_origin(world, capsys, how):
    """Test 12: an origin whose URL is unset or empty (git's `get-url` then answers "origin", a path)
    counts as no origin, like a removed one: the apply is refused and nothing moves."""
    gitrepo.git(world.co, *how)
    head = gitrepo.git(world.co, "rev-parse", "HEAD")
    gitrepo.upstream_commit(world.up)
    assert world.main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "checkout has no origin remote" in out, out
    assert not [ln for ln in out.splitlines() if ln.strip().startswith("updates from")], out
    assert gitrepo.git(world.co, "rev-parse", "HEAD") == head
