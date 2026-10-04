"""The refusal-remedy contract on the install / update / self-update / build / binary-channel paths.

A refusal (`ActionResult(ok=False, ...)`) carries one plain-language cause in `summary` and a
remedy: a `next_commands` entry the operator can run, or a `details` line of the form
"nothing to run here — <what is wrong on the box and who fixes it>".

`test_no_bare_refusal_on_the_update_paths` is a whole-module negative invariant ("no refusal
constructor in these scopes is bare") that no driven path can prove for every constructor, so it
reads the scopes' source (tests/README.md rule 1, exception). Its behavioural twins live beside
the refusals they drive, through the ordinary fakes:
- `install/test_selfupdate_service.py`: the self-update identity refusal for every cause the
  identity check reports (at both entries, `--apply` and the console's update request; the runtime
  root and the checkout a second time for the folder others can write), and the live identity
  check on a real checkout with no origin and with a foreign one;
- `install/test_binary_channel.py`: the binary retirement whose receipt path lhpc rejects as
  invalid;
- `install/test_source.py`: the update blocked by a leftover `.prev` folder, and an update refused
  on a busy lock.
The identity causes they share with `test_every_unsafe_identity_cause_has_a_listed_remedy` are in
`tests/remedy_contract.py`.

An admission refusal is built by one helper, `service_base.admission_refusal`, whose remedy per
tag `core/test_admission_refusal.py` drives; `test_no_admission_refusal_is_built_by_hand` keeps
every site on it. Not in the guard: `ControllerService.install` (services.py), whose source-channel
refusals are partly computed from varied causes (docs/operations.md, "Not covered yet").

A refusal on a busy lock or a blocked source transaction names the command to run again, never
only `lhpc status` (a diagnostic, not a remedy): `test_busy_refusals_name_the_retry_command`, the
same kind of source-reading guard, with
`install/test_source.py::test_busy_update_names_the_holder_and_the_retry_command` as its twin.

`test_every_computed_remedy_is_registered` is the third source-reading guard of this kind (rule 1,
exception): it counts the refusals whose `next_commands` is computed, per function, against
`DYNAMIC`; its twins are the behavioural tests each `DYNAMIC` entry names, which reach those
refusals and assert a non-empty `next_commands`.
"""

from __future__ import annotations

import ast
from collections import Counter

import pytest

from remedy_contract import IDENTITY_CAUSES, NOTHING_TO_RUN
from repo_paths import REPO

pytestmark = pytest.mark.contract

# module -> the functions on the install / update / self-update / build / binary paths (None = all)
SCOPES = {
    "lhpc/core/service_binary_ops.py": None,
    "lhpc/core/service_selfupdate.py": None,
    "lhpc/core/service_lifecycle_ops.py": {"build"},
    "lhpc/core/service_maintenance.py": {"update", "graywolf_upstream_update",
                                         "_graywolf_upstream_update_locked"},
    "lhpc/core/services.py": {"install"},
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
    empty = (isinstance(commands, ast.Constant) and commands.value is None) \
        or (isinstance(commands, ast.List) and not commands.elts)
    if commands is not None and not empty:
        return True
    details = _arg(call, 2, "details")
    return details is not None and any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and NOTHING_TO_RUN in n.value
        for n in ast.walk(details))


def test_no_bare_refusal_on_the_update_paths():
    bare, seen = [], set()
    for rel, functions in SCOPES.items():
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for fn, call in _refusals(tree, functions):
            seen.add((rel, fn))
            if not _has_remedy(call):
                bare.append(f"{rel}:{call.lineno} ({fn})")
    # A renamed function must not empty its scope silently.
    for rel, functions in SCOPES.items():
        for fn in functions or ():
            assert (rel, fn) in seen, f"{rel}: no refusal found in {fn}() — scope out of date"
    assert not bare, "refusal without a remedy (next_commands, or '" + NOTHING_TO_RUN + "…'):\n" \
        + "\n".join(bare)


def test_no_admission_refusal_is_built_by_hand():
    """Every module turns an `AdmissionRefused` into its refusal through `admission_refusal` (which
    carries the remedy); a hand-built `ActionResult(False, <exc>.reason, …)` would drop it."""
    hand = []
    for path in sorted((REPO / "lhpc").rglob("*.py")):
        if path == REPO / "lhpc" / "core" / "service_base.py":     # the helper itself
            continue
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(call, ast.Call)
                    and getattr(call.func, "id", getattr(call.func, "attr", "")) == "ActionResult"
                    and any(isinstance(a, ast.Attribute) and a.attr == "reason"
                            for a in call.args[1:2])):
                hand.append(f"{path.relative_to(REPO)}:{call.lineno}")
    assert hand == [], hand


# The refusals whose `next_commands` is computed, not a literal list: the guard cannot evaluate them,
# so each function holding such sites is registered with how many it holds, why each list is
# non-empty where that refusal is built (or why the refusal says "nothing to run here" instead),
# and the tests that reach those refusals and assert so. A function whose count of computed sites
# differs from its entry, an entry with no computed site left, or a named test that does not exist
# fails.
DYNAMIC = {
    ("lhpc/core/service_binary_ops.py", "_binary_pin_refusal"): (2,
        "the ways list is built only inside `if lag_clone and accept:` with at least one way; the "
        "override command is appended to `cmds` before the other refusal",
        ("tests/cli/test_cli_pin_override.py::test_cli_meshcom_pin_refusal_with_flag_is_refused_typed",
         "tests/cli/test_cli_pin_override.py::test_cli_meshcom_pin_refusal_without_flag_offers_no_override")),
    ("lhpc/core/service_selfupdate.py", "self_update_apply"): (1,
        "`_identity_remedy` returns the remedy commands, or the refusal's details say nothing to run",
        ("tests/core/test_controller.py::test_self_update_apply_blocked_by_unsafe_identity",)),
    ("lhpc/core/service_selfupdate.py", "_self_update_locked"): (2,
        "`nxt` is set per refusal kind (where it stays empty the details say nothing to run here); "
        "the restart instructions always carry their commands",
        ("tests/install/test_selfupdate.py::test_a_detached_checkout_is_refused_with_the_switch_back",
         "tests/install/test_selfupdate_migration.py::test_service_maps_cleanup_failure_to_partial")),
    ("lhpc/core/service_selfupdate.py", "self_update_trigger"): (1,
        "`_identity_remedy` returns the remedy commands, or the refusal's details say nothing to run",
        ("tests/install/test_selfupdate_service.py::test_trigger_preflight_passes_or_refuses_without_a_request_marker",)),
    ("lhpc/core/service_selfupdate.py", "_self_update_run_service_locked"): (1,
        "`_incomplete_outcome` / `_units_stale_outcome` return at least one command",
        ("tests/install/test_selfupdate_migration.py::test_a_helper_changing_update_keeps_the_reapply_warning_on_every_path",)),
    ("lhpc/core/service_lifecycle_ops.py", "build"): (2,
        "built only inside `if _deps_absent:` and `if _refused:`, from their stacks",
        ("tests/install/test_deps.py::test_direct_build_of_an_uninstalled_component_says_so",
         "tests/golden/test_golden_build.py::test_refused_not_installed")),
    ("lhpc/core/services.py", "install"): (2,
        "the refused install hands on the next steps of the install it completed (`res`): the "
        "HMAC enable that failed afterwards, or the candidate cleanup left incomplete",
        ("tests/web/test_hmac.py::test_install_fails_closed_when_hmac_enable_fails",
         "tests/install/test_binary_install.py::test_switch_transaction_is_resolved_even_when_a_later_step_fails")),
    ("lhpc/core/service_maintenance.py", "update"): (2,
        "built only inside `if running:`, `owners` their stacks (the preflight and the recheck)",
        ("tests/core/test_uninstall_safety.py::test_update_refuses_while_target_running",
         "tests/core/test_op_serialization.py::test_update_rechecks_running_after_locks")),
}


def test_every_computed_remedy_is_registered():
    seen = Counter()
    for rel, functions in SCOPES.items():
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for fn, call in _refusals(tree, functions):
            commands = _arg(call, 3, "next_commands")
            if commands is not None and not isinstance(commands, (ast.List, ast.Constant)):
                seen[(rel, fn)] += 1
    assert dict(seen) == {key: entry[0] for key, entry in DYNAMIC.items()}, \
        f"computed next_commands per function: {dict(seen)}"
    for _count, _reason, tests in DYNAMIC.values():
        for nodeid in tests:
            f, name = nodeid.split("::")
            assert f"def {name}(" in (REPO / f).read_text(encoding="utf-8"), nodeid


def _first_text(node) -> str:
    """The literal text a command string starts with (a str constant or an f-string's head)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return node.values[0].value
    return ""


def _same_operation(fn: str) -> str:
    """The command a busy/blocked refusal in `fn` must name to run the same operation again."""
    if fn.startswith(("self_update", "_self_update", "_recover")):
        return "lhpc self-update"
    if fn.startswith("binary") or fn == "install":
        return "lhpc install"
    return "lhpc build" if fn == "build" else "lhpc update"


def _busy_wrong(rel: str, tree: ast.AST, functions):
    """(arms, wrong): every refusal in an `except ResourceBusy` / `except SourceTxnBlocked` arm,
    and those that name neither the same operation's retry nor "nothing to run here"."""
    wrong, arms = [], 0
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
                if not nothing and not any(t.startswith(_same_operation(fn.name))
                                           for t in texts):
                    wrong.append(f"{rel}:{call.lineno} ({fn.name})")
    return arms, wrong


def test_busy_refusals_name_the_retry_command():
    """Every refusal in an `except ResourceBusy` / `except SourceTxnBlocked` arm of these scopes
    names the command that runs the SAME operation again (`lhpc update …` for an update), not
    another diagnostic — or, where no operator runs the command (the systemd update helper),
    says "nothing to run here"."""
    arms, wrong = 0, []
    for rel, functions in SCOPES.items():
        a, w = _busy_wrong(rel, ast.parse((REPO / rel).read_text(encoding="utf-8")), functions)
        arms, wrong = arms + a, wrong + w
    assert arms >= 7, f"only {arms} busy/blocked refusals found — scope out of date"
    assert not wrong, "busy/blocked refusal without a retry command:\n" + "\n".join(wrong)


def test_the_guards_reject_an_empty_remedy_and_another_operations_command():
    """The guards themselves: `next_commands=None` is no remedy, and a busy refusal whose only
    command is another diagnostic (`lhpc doctor`) names no retry."""
    tree = ast.parse(
        "def update(self):\n"
        "    try:\n        pass\n"
        "    except ResourceBusy:\n"
        "        return ActionResult(False, 'busy', next_commands=['lhpc doctor'])\n"
        "    return ActionResult(False, 'refused', next_commands=None)\n")
    calls = sorted((c for _f, c in _refusals(tree, None)), key=lambda c: c.lineno)
    assert [_has_remedy(c) for c in calls] == [True, False]
    assert _busy_wrong("x.py", tree, None) == (1, ["x.py:5 (update)"])


# ---- the identity causes the guard compares against ----------------------------------------


def _templates(node) -> set[str]:
    """The reason template(s) of one verdict: both texts of a `a if c else b` reason."""
    if isinstance(node, ast.IfExp):
        return _templates(node.body) | _templates(node.orelse)
    if isinstance(node, ast.Constant):
        return {node.value}
    return {"".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)}


def test_every_unsafe_identity_cause_has_a_listed_remedy():
    """The causes its twin (`install/test_selfupdate_service.py`) drives are exactly the ones the
    identity check can report."""
    tree = ast.parse((REPO / "lhpc/core/services.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "controller_identity_live")
    causes = set().union(*(_templates(c.args[1]) for c in ast.walk(fn)
                           if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "verdict"
                           and isinstance(c.args[0], ast.Constant)
                           and c.args[0].value == "unsafe"))
    assert causes == set(IDENTITY_CAUSES)
