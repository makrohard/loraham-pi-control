"""M10: `lhpc webserver ...` CLI surface (thin wiring over the service facade). Uses a real
runtime root under tmp_path via LHPC_RUNTIME_ROOT; real pki (cryptography) runs."""

from __future__ import annotations

from pathlib import Path

from lhpc.adapters.cli.main import main


def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "config").mkdir(exist_ok=True)


def test_cli_cert_list_empty(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "cert", "list"]) == 0
    assert "no client certificates" in capsys.readouterr().out


def test_cli_init_status_issue_flow(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "init", "--dns", "pi.local"]) == 0
    assert main(["webserver", "status"]) == 0
    out = capsys.readouterr().out
    assert "remote_exposed False" in out
    # The exposure-status line now reflects the REAL listener (live /proc): "disabled — loopback only"
    # when nothing is bound off-loopback, or "disabled in desired config, but the live listener … is
    # still exposed" on a machine that happens to have :8443 bound. Both share this stable prefix.
    assert "Remote exposure is disabled" in out
    # issue prints a one-time passphrase, never persisted
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    out = capsys.readouterr().out
    assert "ONE-TIME bundle passphrase" in out
    assert main(["webserver", "cert", "list"]) == 0
    assert "laptop" in capsys.readouterr().out


def test_cli_webserver_logs(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    logs = tmp_path / "logs"; logs.mkdir(exist_ok=True)
    (logs / "nginx-error.log").write_text("E-line [emerg]\n")
    (logs / "nginx-access.log").write_text("A-line GET /\n")
    assert main(["webserver", "logs"]) == 0
    out = capsys.readouterr().out
    assert "nginx-error.log" in out and "E-line [emerg]" in out
    assert main(["webserver", "logs", "--access"]) == 0
    out = capsys.readouterr().out
    assert "nginx-access.log" in out and "A-line GET /" in out


def test_cli_expose_requires_confirmation(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    # no phrase -> refused (non-zero); nothing persisted as exposed
    assert main(["webserver", "expose", "--cidr", "192.168.0.0/24"]) == 1
    assert main(["webserver", "status"]) == 0
    assert "remote_exposed False" in capsys.readouterr().out
    # public route with only the normal phrase -> refused (needs the danger phrase)
    assert main(["webserver", "expose", "--cidr", "0.0.0.0/0",
                 "--confirm-phrase", "enable-remote"]) == 1
    # normal LAN range with the typed phrase -> enabled
    assert main(["webserver", "expose", "--cidr", "192.168.0.0/24",
                 "--confirm-phrase", "enable-remote"]) == 0
    assert main(["webserver", "status"]) == 0
    assert "remote_exposed True" in capsys.readouterr().out


def test_cli_configure_validation(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "configure", "--access-mode", "auth-everywhere"]) == 0
