"""The refusal-remedy contract on the install / update / self-update / build / binary-channel paths.

A refusal (`ActionResult(ok=False, ...)`) carries one plain-language cause in `summary` and a
remedy: a `next_commands` entry the operator can run, or a `details` line of the form
"nothing to run here — <what is wrong on the box and who fixes it>".

`test_no_bare_refusal_on_the_update_paths` is a whole-module negative invariant ("no refusal
constructor in these scopes is bare") that no driven path can prove for every constructor, so it
reads the scopes' source (tests/README.md rule 1, exception). Its behavioural twins drive, through
the ordinary fakes, the self-update identity refusal for every cause the identity check reports
(at both entries, `--apply` and the console's update request; the runtime root and the checkout
a second time for the folder others can write), the live identity check on a real checkout with
no origin and with a foreign one, the binary retirement whose receipt path lhpc rejects as
invalid, the update blocked by a leftover `.prev` folder, and an update refused on a busy lock.

Not in the guard, named: the admission refusals listed in `EXEMPT` by file:line, each with its
reason (the guard fails on a listed site that moved or disappeared, and on any unlisted bare one);
`ControllerService.install` (services.py) is outside this batch's files.

A refusal on a busy lock or a blocked source transaction names the command to run again, never
only `lhpc status` (a diagnostic, not a remedy): `test_busy_refusals_name_the_retry_command`, the
same kind of source-reading guard, with `test_busy_update_names_the_holder_and_the_retry_command`
as its twin.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from repo_paths import REPO

pytestmark = pytest.mark.contract

NOTHING_TO_RUN = "nothing to run here — "

# module -> the functions on the install / update / self-update / build / binary paths (None = all)
SCOPES = {
    "lhpc/core/service_binary_ops.py": None,
    "lhpc/core/service_selfupdate.py": None,
    "lhpc/core/service_lifecycle_ops.py": {"build"},
    "lhpc/core/service_maintenance.py": {"update", "graywolf_upstream_update",
                                         "_graywolf_upstream_update_locked"},
}


def _arg(call: ast.Call, pos: int, name: str):
    if len(call.args) > pos:
        return call.args[pos]
    return next((k.value for k in call.keywords if k.arg == name), None)


def _refusals(tree: ast.AST, functions):
    """(function, call) for every `ActionResult(False, ...)` / `ActionResult(ok=False, ...)`."""
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if functions is not None and fn.name not in functions:
            continue
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call)
                    and getattr(call.func, "id", getattr(call.func, "attr", "")) == "ActionResult"):
                continue
            ok = _arg(call, 0, "ok")
            if isinstance(ok, ast.Constant) and ok.value is False:
                yield fn.name, call


def _has_remedy(call: ast.Call) -> bool:
    commands = _arg(call, 3, "next_commands")
    if commands is not None and not (isinstance(commands, ast.List) and not commands.elts):
        return True
    details = _arg(call, 2, "details")
    return details is not None and any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and NOTHING_TO_RUN in n.value
        for n in ast.walk(details))


# The bare refusals the guard accepts, by file:line, each with its reason. All seven return the
# shared admission refusal `ActionResult(False, <AdmissionRefused>.reason)`: its text is worded
# once, in `_task_admission_blocked` (service_selfupdate.py) and the power-pending gate
# (services.py), for every operation. Only its self-update-pending reason names a command
# (`lhpc self-update --recover-request`); the uninstall, unverifiable and power-pending reasons
# name none. Giving them a remedy changes that shared text for every operation: a U2 item.
_ADMISSION = "shared admission refusal (_task_admission_blocked); remedy is a U2 item"
EXEMPT = {
    ("lhpc/core/service_binary_ops.py", 217): ("binary_install", _ADMISSION),
    ("lhpc/core/service_selfupdate.py", 407): ("self_update_apply", _ADMISSION),
    ("lhpc/core/service_selfupdate.py", 631): ("self_update_apply_operator", _ADMISSION),
    ("lhpc/core/service_selfupdate.py", 1005): ("self_update_trigger", _ADMISSION),
    ("lhpc/core/service_lifecycle_ops.py", 3500): ("build", _ADMISSION),
    ("lhpc/core/service_maintenance.py", 964): ("graywolf_upstream_update", _ADMISSION),
    ("lhpc/core/service_maintenance.py", 1960): ("update", _ADMISSION),
}


def test_no_bare_refusal_on_the_update_paths():
    bare, seen, exempt_seen = [], set(), set()
    for rel, functions in SCOPES.items():
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for fn, call in _refusals(tree, functions):
            seen.add((rel, fn))
            if (rel, call.lineno) in EXEMPT and EXEMPT[(rel, call.lineno)][0] == fn \
                    and not _has_remedy(call):
                exempt_seen.add((rel, call.lineno))
            elif not _has_remedy(call):
                bare.append(f"{rel}:{call.lineno} ({fn})")
    # A renamed function must not empty its scope silently.
    for rel, functions in SCOPES.items():
        for fn in functions or ():
            assert (rel, fn) in seen, f"{rel}: no refusal found in {fn}() — scope out of date"
    # A listed site that moved, gained a remedy or disappeared must be re-listed or dropped.
    stale = sorted(set(EXEMPT) - exempt_seen)
    assert not stale, f"EXEMPT lists no bare refusal at: {stale}"
    assert not bare, "refusal without a remedy (next_commands, or '" + NOTHING_TO_RUN + "…'):\n" \
        + "\n".join(bare)


def _first_text(node) -> str:
    """The literal text a command string starts with (a str constant or an f-string's head)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return node.values[0].value
    return ""


def test_busy_refusals_name_the_retry_command():
    """Every refusal in an `except ResourceBusy` / `except SourceTxnBlocked` arm of these scopes
    names a command to run again, not only `lhpc status` — or, where no operator runs the command
    (the systemd update helper), says "nothing to run here"."""
    wrong, arms = [], 0
    for rel, functions in SCOPES.items():
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    or (functions is not None and fn.name not in functions):
                continue
            for h in ast.walk(fn):
                if not (isinstance(h, ast.ExceptHandler) and h.type is not None
                        and ast.unparse(h.type).split(".")[-1] in ("ResourceBusy",
                                                                    "SourceTxnBlocked")):
                    continue
                for _f, call in _refusals(ast.Module(body=[ast.FunctionDef(
                        name=fn.name, args=fn.args, body=h.body, decorator_list=[])],
                        type_ignores=[]), None):
                    arms += 1
                    commands = _arg(call, 3, "next_commands")
                    texts = [_first_text(e) for e in getattr(commands, "elts", [])]
                    details = _arg(call, 2, "details")
                    nothing = details is not None and any(
                        NOTHING_TO_RUN in _first_text(n) for n in ast.walk(details))
                    if not nothing and not any(t and not t.startswith("lhpc status")
                                               for t in texts):
                        wrong.append(f"{rel}:{call.lineno} ({fn.name})")
    assert arms >= 7, f"only {arms} busy/blocked refusals found — scope out of date"
    assert not wrong, "busy/blocked refusal without a retry command:\n" + "\n".join(wrong)


# ---- behavioural twins ---------------------------------------------------------------------


# Every cause `controller_identity_live` reports as unsafe (its reason template, `{}` for an
# interpolated value) -> a concrete reason and the remedy it gets: a command, or "nothing to run".
_CHECKOUT = "{root}/src/loraham-pi-control"
IDENTITY_CAUSES = {
    "checkout is in detached HEAD":
        ("checkout is in detached HEAD", f"git -C {_CHECKOUT} switch main"),
    "checkout branch {} != {}":
        ("checkout branch 'dev' != 'main'", f"git -C {_CHECKOUT} switch main"),
    "checkout has no origin remote":
        ("checkout has no origin remote", f"git -C {_CHECKOUT} remote add origin {{remote}}"),
    "origin is not the approved canonical remote":
        ("origin is not the approved canonical remote",
         f"git -C {_CHECKOUT} remote set-url origin {{remote}}"),
    "{} is group/other-writable": ("src is group/other-writable", "chmod go-w {root}/src"),
    "controller source_path is not the fixed value":
        ("controller source_path is not the fixed value", None),
    "source path escapes runtime root ({})": ("source path escapes runtime root (x)", None),
    "{} is missing": ("src is missing", None),
    "{} is a symlink (fixed layout required)": ("checkout is a symlink (fixed layout required)", None),
    "{} is not a directory": ("src is not a directory", None),
    "{} not owned by the service user": ("runtime root not owned by the service user", None),
    "checkout realpath escapes the runtime root": ("checkout realpath escapes the runtime root", None),
    "imported package repo != controller checkout":
        ("imported package repo != controller checkout", None),
    "not a git checkout": ("not a git checkout", None),
}


def _templates(node) -> set[str]:
    """The reason template(s) of one verdict: both texts of a `a if c else b` reason."""
    if isinstance(node, ast.IfExp):
        return _templates(node.body) | _templates(node.orelse)
    if isinstance(node, ast.Constant):
        return {node.value}
    return {"".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)}


def test_every_unsafe_identity_cause_has_a_listed_remedy():
    """The causes the twin below drives are exactly the ones the identity check can report."""
    tree = ast.parse((REPO / "lhpc/core/services.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "controller_identity_live")
    causes = set().union(*(_templates(c.args[1]) for c in ast.walk(fn)
                           if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "verdict"
                           and isinstance(c.args[0], ast.Constant)
                           and c.args[0].value == "unsafe"))
    assert causes == set(IDENTITY_CAUSES)


def _identity_svc(tmp_path, monkeypatch, reason):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    verdict = {"status": "unsafe", "ok": False, "reason": reason}
    # Stubbed collaborators: the live identity probe (it needs a real self-hosted checkout), the
    # job/auto-install/HMAC scan, and (trigger) the managed-unit gates in front of the cached
    # verdict; the refusal under test is the identity gate behind them.
    monkeypatch.setattr(ControllerService, "controller_identity_live", lambda self: verdict)
    monkeypatch.setattr(ControllerService, "_self_update_blockers", lambda self: None)
    monkeypatch.setattr(ControllerService, "updater_integration", lambda self: {"status": "ok"})
    monkeypatch.setattr(ControllerService, "self_update_status",
                        lambda self: {"available": True, "identity": verdict})
    monkeypatch.setenv("INVOCATION_ID", "test")
    return svc


@pytest.mark.parametrize("entry", ["apply", "trigger"])
@pytest.mark.parametrize("cause", sorted(IDENTITY_CAUSES))
def test_unsafe_identity_refusal_names_the_remedy_for_its_cause(tmp_path, monkeypatch, cause,
                                                                entry):
    """`lhpc self-update --apply` (and the console's update button) on a checkout whose identity
    is unsafe — measured on a Pi Zero 2 W with a checkout left in detached HEAD: the refusal names
    the command that fixes that cause, or says there is nothing to run and who restores it."""
    reason, command = IDENTITY_CAUSES[cause]
    svc = _identity_svc(tmp_path, monkeypatch, reason)
    res = svc.self_update_apply() if entry == "apply" else svc.self_update_trigger()
    assert not res.ok and res.data.get("identity_unsafe")
    assert reason in res.summary
    if command is None:
        assert res.next_commands == []
        assert any(d.strip().startswith(NOTHING_TO_RUN.strip()) and "install.sh" in d
                   for d in res.details)
    else:
        remote = svc.controller().remote
        assert res.next_commands == [command.format(root=tmp_path, remote=remote),
                                     "lhpc self-update --apply"]


@pytest.mark.parametrize("entry", ["apply", "trigger"])
def test_writable_runtime_root_refusal_names_chmod_of_the_runtime_root(tmp_path, monkeypatch,
                                                                       entry):
    """A runtime root others can write: the refusal names `chmod go-w` of the runtime root
    itself, not of `src` or the checkout."""
    svc = _identity_svc(tmp_path, monkeypatch, "runtime root is group/other-writable")
    res = svc.self_update_apply() if entry == "apply" else svc.self_update_trigger()
    assert not res.ok and res.data.get("identity_unsafe")
    assert res.next_commands == [f"chmod go-w {tmp_path}", "lhpc self-update --apply"]


@pytest.mark.parametrize("entry", ["apply", "trigger"])
def test_writable_checkout_refusal_names_chmod_of_the_checkout(tmp_path, monkeypatch, entry):
    """A checkout others can write: the refusal names `chmod go-w` of the checkout."""
    svc = _identity_svc(tmp_path, monkeypatch, "checkout is group/other-writable")
    res = svc.self_update_apply() if entry == "apply" else svc.self_update_trigger()
    assert not res.ok and res.data.get("identity_unsafe")
    assert res.next_commands == [f"chmod go-w {tmp_path}/src/loraham-pi-control",
                                 "lhpc self-update --apply"]


_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent", "PATH": "/usr/bin:/bin",
}


@pytest.mark.parametrize("origin, command", [
    (None, "remote add origin"),
    ("https://example.invalid/other.git", "remote set-url origin"),
])
def test_live_identity_tells_a_missing_origin_from_a_wrong_one(tmp_path, monkeypatch, origin,
                                                                command):
    """`lhpc self-update --apply` on a real self-hosted checkout with no origin, and with a
    foreign origin: the identity check tells the two apart, and the refusal names
    `git remote add` for the first and `git remote set-url` for the second."""
    import os
    import subprocess

    import lhpc
    from lhpc.core import selfupdate
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import RealSystem
    from lhpc.core.services import ControllerService

    def git(*args):
        subprocess.run(["git", *args], cwd=co, env=_GIT_ENV, check=True, capture_output=True)

    co = tmp_path / "src" / "loraham-pi-control"
    (co / "lhpc").mkdir(parents=True)
    (co / "lhpc" / "__init__.py").write_text("")
    for d in (tmp_path, tmp_path / "src", co):
        os.chmod(d, 0o700)
    git("init", "-q")
    git("checkout", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    if origin:
        git("remote", "add", "origin", origin)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: co)
    monkeypatch.setattr(lhpc, "__file__", str(co / "lhpc" / "__init__.py"))
    monkeypatch.setattr(ControllerService, "_self_update_blockers", lambda self: None)
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    res = svc.self_update_apply()
    assert not res.ok and res.data.get("identity_unsafe")
    assert res.next_commands == [f"git -C {co} {command} {svc.controller().remote}",
                                 "lhpc self-update --apply"]


def test_receipt_refusals_name_the_invalid_path_cause(tmp_path, monkeypatch):
    """A retirement whose receipt path lhpc rejects as invalid (a stack id with a path
    separator): both refusals that follow a failed receipt removal (the switch path and the
    plain retirement) word that cause and say to report it."""
    from types import SimpleNamespace

    from lhpc.core import binary_install
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    rec = SimpleNamespace(files=(), owned_dirs=())
    line = "nothing to run here — the receipt path is invalid: state/binary/a/b.json; report it"
    monkeypatch.setattr(binary_install, "displace", lambda *a, **k: None)  # no files to move
    switch = svc._retire_body("a/b", "present", rec, "", force=True, locked=True, txn="t")
    plain = svc._retire_body("a/b", "present", rec, "", force=True, locked=True, txn="")
    for res in (switch, plain):
        assert not res.ok and "receipt" in res.summary
        assert any(line in d for d in res.details), res.details


def test_busy_update_names_the_holder_and_the_retry_command(tmp_path, monkeypatch):
    """`lhpc update daemon --yes` while another process runs an update (the admission lock is
    held): the refusal says to wait for that update and names the command to run again."""
    import threading

    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    monkeypatch.setattr(ControllerService, "_SELF_LOCK_WAIT_S", 0.2)    # fast contention
    holder = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert holder.bootstrap(apply=True).ok
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    held, release = threading.Event(), threading.Event()

    def hold():
        with holder._admission_guard("update", "meshcore"):
            held.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        named, everything = svc.update("daemon", apply=True), svc.update("", apply=True)
    finally:
        release.set()
        t.join(5)
    assert not named.ok and "is busy: update on 'meshcore'" in named.summary
    assert named.details == ["  wait for the update named above to finish, then run the same "
                             "command again"]
    assert named.next_commands == ["lhpc update daemon --yes"]
    assert everything.next_commands == ["lhpc update --yes"]


class _GitRunner:
    """A clone that succeeds, a clean tree, and the identity the ownership record holds."""

    def run(self, argv, timeout=None, *a, **k):
        from lhpc.core.probes.backends import CommandResult
        if argv[:2] == ["git", "clone"]:
            (Path(argv[-1]) / ".git").mkdir(parents=True, exist_ok=True)
            return CommandResult(0, "", "")
        if "status" in argv or "ls-files" in argv:
            return CommandResult(0, "", "")
        if "config" in argv:
            return CommandResult(0, "https://example/repo.git\n", "")
        return CommandResult(0, "abc123\n", "")


def test_leftover_prev_refusal_names_the_folder_and_the_command(tmp_path):
    """An update blocked by a `.prev` folder an interrupted update left behind (measured on a
    Pi Zero 2 W): the refusal names that folder and the command to run after moving it."""
    import time

    from lhpc.core import source_registry
    from lhpc.core.config import Config
    from lhpc.core.install import Installer
    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem

    rt = tmp_path / "rt"
    (rt / "src" / "repo").mkdir(parents=True)
    (rt / "src" / ".repo.prev").mkdir()                    # left over by an interrupted update
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/repo", local_dir="repo",
                                       remote="https://example/repo.git", branch="main"))
    paths = Paths(runtime_root=rt)
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/repo", "https://example/repo.git", "dev", "abc123", time.time(), "", ("c",)))
    system = FakeSystem().system
    system.runner = _GitRunner()
    inst = Installer(paths, (Stack(id="s", name="s", main="c", components=(comp,)),),
                     Config(values={"install": {"adopt_search_root": str(rt / "nolocal")}}), system)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed"
    assert "src/.repo.prev" in action.detail and "lhpc update c --yes" in action.detail
    assert (rt / "src" / ".repo.prev").is_dir()            # the refusal itself removes nothing
