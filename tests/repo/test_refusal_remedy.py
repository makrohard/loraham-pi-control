"""The refusal-remedy contract on the install / update / self-update / build / binary-channel paths.

A refusal (`ActionResult(ok=False, ...)`) carries one plain-language cause in `summary` and a
remedy: a `next_commands` entry the operator can run, or a `details` line of the form
"nothing to run here — <what is wrong on the box and who fixes it>".

`test_no_bare_refusal_on_the_update_paths` is a whole-module negative invariant ("no refusal
constructor in these scopes is bare") that no driven path can prove for every constructor, so it
reads the scopes' source (tests/README.md rule 1, exception). Its behavioural twins drive two
refusals measured on a real box through the ordinary fakes: the self-update identity refusal and
the update blocked by a leftover `.prev` folder.

Not in the guard, named: the admission refusal (`ActionResult(False, <AdmissionRefused>.reason)`)
is the shared admission gate's text, worded once in `_task_admission_blocked` for every operation;
`ControllerService.install` (services.py) is outside this batch's files.
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


def _is_admission_refusal(call: ast.Call) -> bool:
    summary = _arg(call, 1, "summary")
    return isinstance(summary, ast.Attribute) and summary.attr == "reason"


def test_no_bare_refusal_on_the_update_paths():
    bare, seen = [], set()
    for rel, functions in SCOPES.items():
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for fn, call in _refusals(tree, functions):
            seen.add((rel, fn))
            if not _is_admission_refusal(call) and not _has_remedy(call):
                bare.append(f"{rel}:{call.lineno} ({fn})")
    # A renamed function must not empty its scope silently.
    for rel, functions in SCOPES.items():
        for fn in functions or ():
            assert (rel, fn) in seen, f"{rel}: no refusal found in {fn}() — scope out of date"
    assert not bare, "refusal without a remedy (next_commands, or '" + NOTHING_TO_RUN + "…'):\n" \
        + "\n".join(bare)


# ---- behavioural twins ---------------------------------------------------------------------


def test_unsafe_identity_refusal_names_the_command(tmp_path, monkeypatch):
    """`lhpc self-update --apply` on a checkout a developer left in detached HEAD (measured on a
    Pi Zero 2 W): the refusal names the command that puts it back on its branch."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    # Stubbed collaborators: the live identity probe (it needs a real self-hosted checkout) and
    # the job/auto-install/HMAC scan; the refusal under test is the identity gate behind them.
    monkeypatch.setattr(ControllerService, "controller_identity_live",
                        lambda self: {"status": "unsafe", "ok": False,
                                      "reason": "checkout is in detached HEAD"})
    monkeypatch.setattr(ControllerService, "_self_update_blockers", lambda self: None)
    res = svc.self_update_apply()
    assert not res.ok and res.data.get("identity_unsafe")
    assert "detached HEAD" in res.summary
    assert res.next_commands[:2] == [
        f"git -C {tmp_path}/src/loraham-pi-control switch main", "lhpc self-update --apply"]


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
