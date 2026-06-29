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


def test_repair_plan_is_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    main(["bootstrap", "--yes"])
    capsys.readouterr()
    # Nothing installed -> repair plans re-adoptions but does not act without --yes.
    assert main(["repair", "daemon"]) == 0
    assert "Repair plan" in capsys.readouterr().out


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
