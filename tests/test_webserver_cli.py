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


def test_cli_cert_export_safe_by_default(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "init", "--dns", "pi.local"]) == 0
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    capsys.readouterr()
    dest = tmp_path / "out" / "laptop.p12"
    dest.parent.mkdir()
    # write once -> file at 0600, and stdout carries NO bundle bytes / passphrase
    assert main(["webserver", "cert", "export", "laptop", str(dest)]) == 0
    out = capsys.readouterr().out
    assert dest.exists() and (dest.stat().st_mode & 0o777) == 0o600
    assert "bytes to" in out and "PRIVATE KEY" not in out and "passphrase" not in out.lower()
    body = dest.read_bytes()
    # refuse to overwrite without --force; the original file is untouched
    assert main(["webserver", "cert", "export", "laptop", str(dest)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().out
    assert dest.read_bytes() == body
    # --force overwrites
    assert main(["webserver", "cert", "export", "laptop", str(dest), "--force"]) == 0
    assert (dest.stat().st_mode & 0o777) == 0o600


def test_cli_cert_export_missing_bundle(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "init", "--dns", "pi.local"]) == 0
    capsys.readouterr()
    assert main(["webserver", "cert", "export", "ghost", str(tmp_path / "x.p12")]) == 1
    assert "no export bundle" in capsys.readouterr().out


def test_cli_proxy_confirmation_parity(monkeypatch, tmp_path, capsys):
    # A stack web-UI proxy must keep the web UI's confirmation semantics: an exposure-increasing
    # mode without the phrase is refused; the elevated case needs the danger phrase.
    _env(monkeypatch, tmp_path)
    # lan exposure without a phrase -> refused (no write)
    assert main(["webserver", "proxy", "meshcom", "--mode", "lan", "--port", "8090",
                 "--cidr", "192.168.0.0/24"]) == 1
    capsys.readouterr()
    # lan with the normal phrase -> saved
    assert main(["webserver", "proxy", "meshcom", "--mode", "lan", "--port", "8090",
                 "--cidr", "192.168.0.0/24", "--confirm-phrase", "enable-remote"]) == 0
    # public default route with only the normal phrase -> refused (needs danger)
    assert main(["webserver", "proxy", "meshcom", "--mode", "public", "--port", "8090",
                 "--cidr", "0.0.0.0/0", "--confirm-phrase", "enable-remote"]) == 1
    # with the danger phrase -> saved
    assert main(["webserver", "proxy", "meshcom", "--mode", "public", "--port", "8090",
                 "--cidr", "0.0.0.0/0", "--confirm-phrase", "enable-remote-danger"]) == 0


def test_cli_proxy_rejects_bad_enum(monkeypatch, tmp_path):
    import pytest
    _env(monkeypatch, tmp_path)
    for bad in (["--mode", "remote"], ["--scheme", "ftp"], ["--access-mode", "nope"]):
        with pytest.raises(SystemExit):                       # argparse choices= -> exit 2
            main(["webserver", "proxy", "meshcom", *bad])
