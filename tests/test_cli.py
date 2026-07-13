"""Tests for CLI output and exit behaviour."""

from __future__ import annotations

from lhpc.adapters.cli.main import main
from lhpc.core.services import ControllerService


def test_list_exits_zero(capsys):
    assert main(["list"]) == 0
    assert "stacks defined" in capsys.readouterr().out


def test_status_exits_zero_even_when_services_stopped(capsys):
    # Probing succeeded -> success, even though nothing is installed/running here.
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Status collected" in out


def test_status_unknown_stack_exits_one(capsys):
    assert main(["status", "does-not-exist"]) == 1


def test_explain_shows_direct_default(capsys):
    assert main(["explain", "meshcom"]) == 0
    assert "DIRECT" in capsys.readouterr().out


def test_status_versions_exits_zero(capsys):
    assert main(["status", "--versions"]) == 0
    assert "confirmed-working judgement" in capsys.readouterr().out


def test_update_shows_plan(capsys):
    assert main(["update", "daemon"]) == 0
    out = capsys.readouterr().out
    assert "Update plan" in out and "refresh" in out


def test_install_plan_is_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    main(["bootstrap", "--yes"])
    capsys.readouterr()
    # Nothing installed -> install plans adoptions but does not act without --yes.
    assert main(["install", "daemon", "--check"]) == 0
    assert "Install" in capsys.readouterr().out


def test_repair_and_rollback_are_not_commands(monkeypatch):
    # These verbs were removed (reinstall/update instead) -> argparse rejects them.
    import pytest
    for verb in ("repair", "rollback"):
        with pytest.raises(SystemExit):
            main([verb, "daemon"])


def test_start_plan_is_dry_run_without_yes(tmp_path, monkeypatch, capsys):
    # Nothing installed in a fresh runtime root -> start plans nothing and does
    # not error or transmit.
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    main(["bootstrap", "--yes"])
    capsys.readouterr()
    assert main(["stack", "start", "daemon"]) == 0
    assert "Run plan" in capsys.readouterr().out


def test_help_topic(capsys):
    assert main(["help", "safety"]) == 0
    assert "never auto-enables TX" in capsys.readouterr().out


def test_bootstrap_and_install_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    # This test exercises bootstrap ordering, not the dep gate — neutralise the gate so the outcome
    # doesn't depend on which apt packages this host happens to have.
    monkeypatch.setattr(ControllerService, "install_dep_gate", lambda self, t: {"block": [], "warn": []})
    assert main(["bootstrap", "--yes"]) == 0
    assert (tmp_path / "rt" / "src").is_dir()      # start/ retired (no wrappers)
    capsys.readouterr()
    # install --check is a dry run (no copying); must succeed and plan adoptions.
    assert main(["install", "--check"]) == 0
    out = capsys.readouterr().out
    assert "planned" in out or "change(s) planned" in out


def test_install_requires_bootstrap_first(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "absent"))
    # Neutralise the dep gate (host-independent): this asserts bootstrap ordering, not the gate.
    monkeypatch.setattr(ControllerService, "install_dep_gate", lambda self, t: {"block": [], "warn": []})
    assert main(["install", "--check"]) == 1
    assert "bootstrap" in capsys.readouterr().out.lower()


def test_install_gate_reports_on_check_but_refuses_on_apply(tmp_path, monkeypatch, capsys):
    # N-2: the dep gate must not preempt the bootstrap precondition or the --check plan. With one
    # mandatory dep stubbed missing:
    #  (i)  unbootstrapped + --check -> the bootstrap plan still renders (gate does not preempt);
    #  (ii) bootstrapped + --check   -> BOTH the blocked report AND the plan render, rc != 0;
    #  (iii) bootstrapped, apply     -> hard refusal, rc 1, and svc.install is never invoked.
    monkeypatch.setattr(ControllerService, "install_dep_gate",
                        lambda self, t: {"block": [{"what": "socat",
                                                    "install": "sudo apt install -y socat"}],
                                         "warn": []})

    # (i) unbootstrapped: the plan (bootstrap message) wins; the gate is only reported.
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "absent"))
    assert main(["install", "--check"]) == 1
    out = capsys.readouterr().out
    assert "bootstrap" in out.lower()                       # gate did not preempt the plan
    assert "Install is blocked" in out                      # gate reported, not "Refusing"

    # (ii) bootstrapped: --check renders the plan AND reports the block; rc reflects the block.
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    assert main(["bootstrap", "--yes"]) == 0
    capsys.readouterr()
    assert main(["install", "--check"]) == 1
    out = capsys.readouterr().out
    assert "Install is blocked" in out and "socat" in out   # the blocked report
    assert "planned" in out or "change(s) planned" in out   # AND the rendered plan

    # (iii) apply path: hard refusal before anything runs — svc.install must never be called.
    called = {"n": 0}
    def _boom(self, *a, **k):                               # noqa: ANN001, ANN002, ANN003
        called["n"] += 1
        raise AssertionError("svc.install must not run when the gate blocks the apply path")
    monkeypatch.setattr(ControllerService, "install", _boom)
    capsys.readouterr()
    assert main(["install"]) == 1
    out = capsys.readouterr().out
    assert "Refusing to install" in out
    assert called["n"] == 0


def test_self_update_check_cli(capsys, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.setattr(ControllerService, "self_update_check",
                        lambda self: ActionResult(True, "Update available — upstream abc123 (v9.9)."))
    assert main(["self-update"]) == 0
    assert "Update available" in capsys.readouterr().out


def test_self_update_run_service_cli_plumbing(capsys, monkeypatch):
    """`--run-service` (called by the updater unit) dispatches to self_update_run_service with
    NO arguments — the normal/overwrite mode is read from the claimed request marker, not a flag."""
    from lhpc.core.services import ControllerService, ActionResult
    called = {}
    def fake_run(self):
        called["ran"] = True
        return ActionResult(True, "Update applied; console back.")
    monkeypatch.setattr(ControllerService, "self_update_run_service", fake_run)
    assert main(["self-update", "--run-service"]) == 0 and called.get("ran")
    assert "console back" in capsys.readouterr().out


def test_self_update_repair_and_recover_cli(capsys, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.setattr(ControllerService, "self_update_repair_integration",
                        lambda self: ActionResult(True, "integration installed"))
    monkeypatch.setattr(ControllerService, "self_update_recover_request",
                        lambda self: ActionResult(True, "recovered"))
    assert main(["self-update", "--repair-integration"]) == 0
    assert "integration installed" in capsys.readouterr().out
    assert main(["self-update", "--recover-request"]) == 0
    assert "recovered" in capsys.readouterr().out


def test_self_update_apply_cli_yes(capsys, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    seen = {}
    def fake_apply(self, *, force=False):
        seen["force"] = force
        return ActionResult(True, "Update applied — restart the web console to load it.",
                            next_commands=["stop the console (Ctrl-C) and re-run:  lhpc web"])
    monkeypatch.setattr(ControllerService, "self_update_apply", fake_apply)
    assert main(["self-update", "--apply", "--overwrite", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Update applied" in out and "lhpc web" in out and seen["force"] is True


def test_self_update_apply_cli_aborts_without_yes(capsys, monkeypatch):
    # non-interactive stdin -> _confirm returns False -> aborts, never calls apply
    from lhpc.core.services import ControllerService
    called = {"apply": False}
    monkeypatch.setattr(ControllerService, "self_update_apply",
                        lambda self, *, force=False: called.__setitem__("apply", True))
    assert main(["self-update", "--apply"]) == 0
    assert "Aborted." in capsys.readouterr().out and called["apply"] is False


def test_self_update_busy_cli(capsys, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False:
        ActionResult(False, "A self-update is already in progress — try again shortly.",
                     data={"busy": True}))
    assert main(["self-update", "--apply", "--yes"]) == 1
    assert "already in progress" in capsys.readouterr().out


def test_update_source_flag_plumbs_through(monkeypatch, capsys):
    from lhpc.core.services import ControllerService, ActionResult
    seen = {}
    def fake_update(self, target="", apply=False, source="pinned"):
        seen["source"], seen["apply"] = source, apply
        return ActionResult(True, "ok", data={"changes": 0})
    monkeypatch.setattr(ControllerService, "update", fake_update)
    assert main(["update", "daemon", "--source", "stable", "--yes"]) == 0
    assert seen["source"] == "stable"


def test_clean_requires_purge_and_yes(monkeypatch, capsys):
    from lhpc.core.services import ControllerService, ActionResult
    calls = {}
    def fake_clean(self, target, apply=False, purge=False):
        calls["apply"], calls["purge"] = apply, purge
        return ActionResult(purge or not apply, "clean", data={"changes": 1})
    monkeypatch.setattr(ControllerService, "clean", fake_clean)
    # without --yes: dry-run plan only (interactive confirm declines on closed stdin)
    assert main(["clean", "kiss", "--purge"]) == 0
    assert calls["apply"] is False                                   # never applied
    # with both flags: applied with purge=True
    assert main(["clean", "kiss", "--purge", "--yes"]) == 0
    assert calls["apply"] is True and calls["purge"] is True


def test_install_all_verb_tx_requires_tests(capsys):
    from lhpc.adapters.cli.main import main
    rc = main(["install-all", "--tx", "--no-tests", "--yes"])
    assert rc == 2
    assert "requires host tests" in capsys.readouterr().out


def test_install_all_verb_plumbs_flags(monkeypatch, tmp_path, capsys):
    from lhpc.adapters.cli import main as cli_main
    from lhpc.core.services import ActionResult, ControllerService
    seen = []
    def fake(self, source="pinned", tests=True, tx=False, run_id="", apply=False,
             emit=print):
        seen.append((source, tests, tx, run_id, apply))
        return ActionResult(True, "plan", data={"changes": 1})
    monkeypatch.setattr(ControllerService, "install_all", fake)
    rc = cli_main.main(["install-all", "--yes", "--source", "stable", "--no-tests",
                        "--run-id", "a" * 32])
    assert rc == 0
    assert seen[0] == ("stable", False, False, "", False)        # dry-run first
    assert seen[1] == ("stable", False, False, "a" * 32, True)   # then apply, bound run_id


def test_install_all_unbootstrapped_cli_refuses(tmp_path, monkeypatch, capsys):
    import lhpc.core.paths as paths_mod
    from lhpc.adapters.cli.main import main
    absent = tmp_path / "absent-root"
    monkeypatch.setenv(paths_mod.ENV_RUNTIME_ROOT, str(absent))
    rc = main(["install-all", "--yes"])
    out = capsys.readouterr().out
    assert rc != 0 and "not bootstrapped" in out
    assert not absent.exists()


def test_install_all_default_source_is_dev(monkeypatch, capsys):
    from lhpc.adapters.cli import main as cli_main
    from lhpc.core.services import ActionResult, ControllerService
    seen = []
    monkeypatch.setattr(ControllerService, "install_all",
                        lambda self, source="x", tests=True, tx=False, run_id="",
                        apply=False, emit=print:
                        (seen.append(source), ActionResult(True, "p", data={"changes": 0}))[1])
    cli_main.main(["install-all", "--yes"])
    assert seen and seen[0] == "dev"


# --------------------------------------------------------------------------------------------------
# `lhpc config` — per-stack settings + operator identity (the reported gap)
# --------------------------------------------------------------------------------------------------

def _rt(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    main(["bootstrap", "--yes"])
    capsys.readouterr()


def test_config_list_marks_identity(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "meshcom"]) == 0
    out = capsys.readouterr().out
    assert "mc_callsign" in out and "*" in out and "identity" in out


def test_config_set_and_show(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "meshcom", "mc_callsign", "W1ABC-7"]) == 0
    capsys.readouterr()
    assert main(["config", "meshcom", "mc_callsign"]) == 0
    assert "W1ABC-7" in capsys.readouterr().out


def test_config_set_invalid_value_rejected(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "meshcom", "mc_callsign", "bad!!call"]) == 1
    assert "invalid callsign" in capsys.readouterr().out


def test_config_set_n0call_warns(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "meshcom", "mc_callsign", "N0CALL"]) == 0
    out = capsys.readouterr().out
    assert "WARN" in out and "valid callsign is required" in out


def test_config_unknown_param(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "meshcom", "nosuchparam"]) == 1
    assert "unknown parameter" in capsys.readouterr().out


def test_config_unknown_stack(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "does-not-exist", "call", "W1ABC"]) == 1
    assert "unknown stack" in capsys.readouterr().out


def test_config_operator_sets_and_normalizes_callsign(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "operator", "--callsign", "w1abc"]) == 0     # normalizes to upper
    from lhpc.core.services import ControllerService
    op = ControllerService().config().operator
    assert op.callsign == "W1ABC"


def test_config_operator_reserved_rejects_positional(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "operator", "call", "X"]) == 2
    assert "only --callsign" in capsys.readouterr().out


def test_config_stack_rejects_operator_flags(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "meshcom", "--callsign", "X"]) == 2
    assert "applies only to 'lhpc config operator'" in capsys.readouterr().out


def test_config_conflicting_modes_rejected(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "meshcom", "--reset", "mc_callsign", "X"]) == 2
    assert "conflicting options" in capsys.readouterr().out


def test_config_daemon_param_unknown_key_rejected(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "daemon", "--daemon-param", "NOPE=1"]) == 2
    assert "unknown daemon parameter" in capsys.readouterr().out


def test_config_daemon_param_saves(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["config", "daemon", "--daemon-param", "TXMODE=DIRECT"]) == 0
    assert "saved daemon params" in capsys.readouterr().out


def test_config_ambiguous_param_refuses_without_mutating():
    # No stack in the shipped manifest has a duplicated param name, so drive the guard directly with
    # a fake service: a bare name owned by two components must REFUSE (print component.param) and
    # never call save_config_bundle.
    import argparse
    from lhpc.adapters.cli import main as cli_main
    from lhpc.core.services import ActionResult

    class _Fake:
        def __init__(self):
            self.saved = []

        def stack(self, s):
            return object()

        def config_param_fields(self, stack, band):
            return [{"component": "a", "name": "call", "kind": "run", "key": "a.call", "default": ""},
                    {"component": "b", "name": "call", "kind": "run", "key": "b.call", "default": ""}]

        def config_param_groups(self, stack, band):
            return []

        def save_config_bundle(self, *a, **k):
            self.saved.append((a, k))
            return ActionResult(True, "should not happen")

    fake = _Fake()
    args = argparse.Namespace(stack="x", param="call", value="V", band="", reset=False,
                              daemon_param=None, apply_daemon=False, reset_daemon=False,
                              callsign=None, yes=False)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_main._cmd_config(fake, args)
    out = buf.getvalue()
    assert rc == 1 and "a.call" in out and "b.call" in out
    assert fake.saved == []                                  # NO mutation on ambiguity


# --------------------------------------------------------------------------------------------------
# restart, source-check, known-working, and the broken-hint regression guard
# --------------------------------------------------------------------------------------------------

def test_stack_restart_is_a_command(tmp_path, monkeypatch, capsys):
    _rt(monkeypatch, tmp_path, capsys)
    assert main(["stack", "restart", "meshcom"]) == 0        # not argparse rc 2
    assert "Restart plan" in capsys.readouterr().out


def test_source_check_and_known_working_dispatch(monkeypatch, capsys):
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.setattr(ControllerService, "source_check",
                        lambda self, t="": ActionResult(True, "sources checked"))
    monkeypatch.setattr(ControllerService, "confirm_known_working",
                        lambda self, s: ActionResult(True, "recorded known-working"))
    assert main(["source-check"]) == 0
    assert "sources checked" in capsys.readouterr().out
    assert main(["known-working", "meshcom"]) == 0
    assert "recorded known-working" in capsys.readouterr().out


def test_identity_hint_points_at_a_real_command(tmp_path, monkeypatch):
    # The reported bug: the callsign-failure hint pointed at `lhpc config <stack>`, which argparse
    # rejected. The fixed hint must be a copy-pasteable, PARSEABLE command.
    import shlex
    _ = monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    main(["bootstrap", "--yes"])
    from lhpc.core.services import ControllerService
    from lhpc.adapters.cli.main import build_parser
    hint = ControllerService()._identity_config_hint("chat")
    assert hint.startswith("lhpc config chat ")
    toks = [("W1ABC" if t.startswith("<") else t) for t in shlex.split(hint)[1:]]
    build_parser().parse_args(toks)                          # must NOT SystemExit


def test_all_cli_hints_reference_real_commands():
    # Regression guard: every `lhpc ...` command hint in the service/CLI source must resolve to a
    # registered command (and subcommand). Catches a future hint pointing at a phantom command.
    import re
    import pathlib
    import argparse
    from lhpc.adapters.cli.main import build_parser
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)][0]
    top = set(subs.choices)
    suba = {}
    for n in ("stack", "webserver"):
        inner = [a for a in subs.choices[n]._actions if isinstance(a, argparse._SubParsersAction)]
        if inner:
            suba[n] = set(inner[0].choices)
    root = pathlib.Path(__file__).resolve().parents[1]
    text = "".join((root / r).read_text()
                   for r in ("lhpc/core/services.py", "lhpc/adapters/cli/main.py"))
    bad = set()
    # Only strings that BEGIN with `lhpc ` (a command hint) — excludes mid-sentence prose.
    for m in re.finditer(r'''["']\s*lhpc ([a-z][a-z-]+)(?: ([a-z][a-z-]+))?''', text):
        cmd, sub = m.group(1), m.group(2)
        if cmd not in top:
            bad.add(cmd)
        elif cmd in suba and sub and sub not in suba[cmd]:
            bad.add(f"{cmd} {sub}")
    assert not bad, f"hints referencing unknown commands: {sorted(bad)}"


def test_docs_cli_lists_every_command():
    import argparse
    import pathlib
    from lhpc.adapters.cli.main import build_parser
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)][0]
    doc = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "cli.md").read_text()
    missing = [c for c in subs.choices if f"### {c}" not in doc]
    assert not missing, f"docs/cli.md missing sections for: {missing}"
