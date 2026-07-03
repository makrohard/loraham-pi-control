"""Tests for CLI output and exit behaviour."""

from __future__ import annotations

from lhpc.adapters.cli.main import main


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
    assert main(["bootstrap", "--yes"]) == 0
    assert (tmp_path / "rt" / "start").is_dir()
    capsys.readouterr()
    # install --check is a dry run (no copying); must succeed and plan adoptions.
    assert main(["install", "--check"]) == 0
    out = capsys.readouterr().out
    assert "planned" in out or "change(s) planned" in out


def test_install_requires_bootstrap_first(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "absent"))
    assert main(["install", "--check"]) == 1
    assert "bootstrap" in capsys.readouterr().out.lower()


def test_self_update_check_cli(capsys, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.setattr(ControllerService, "self_update_check",
                        lambda self: ActionResult(True, "Update available — upstream abc123 (v9.9)."))
    assert main(["self-update"]) == 0
    assert "Update available" in capsys.readouterr().out


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
