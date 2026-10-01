"""`lhpc webserver cert …`: exit status and output when the nginx reload after a certificate
change fails."""

from lhpc.adapters.cli.main import main


def test_cli_reissue_with_a_failed_reload_exits_1_and_still_shows_the_passphrase(monkeypatch, tmp_path, capsys):
    # A failed reload is a partial failure (exit 1), but the bundle exists: losing its one-time
    # passphrase would leave the operator with a bundle nobody can open.
    from lhpc.core import webserver as _ws
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path)); (tmp_path / "config").mkdir(exist_ok=True)
    assert main(["webserver", "init"]) == 0
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    monkeypatch.setattr(_ws, "reload", lambda system, paths: ("failed", "failed"))
    capsys.readouterr()
    assert main(["webserver", "cert", "reissue", "laptop"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("ERR") and "reload FAILED" in out
    assert "ONE-TIME bundle passphrase" in out


def test_cli_revoke_with_a_failed_reload_exits_1(monkeypatch, tmp_path, capsys):
    from lhpc.core import webserver as _ws
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path)); (tmp_path / "config").mkdir(exist_ok=True)
    assert main(["webserver", "init"]) == 0
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    monkeypatch.setattr(_ws, "reload", lambda system, paths: ("failed", "failed"))
    capsys.readouterr()
    assert main(["webserver", "cert", "revoke", "laptop", "--confirm-label", "laptop"]) == 1
    assert "revocation RECORDED for 'laptop'" in capsys.readouterr().out
