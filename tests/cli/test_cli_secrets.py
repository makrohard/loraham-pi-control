"""`lhpc secrets restore` on a terminal: `--overwrite` asks for the typed word, and anything else
aborts with nothing written. The restore itself is owned by tests/core/test_secrets_backup.py."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from lhpc.adapters.cli import main as cli
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _root(tmp_path: Path, name: str) -> Path:
    r = tmp_path / name
    for d in ("config/secrets", "config/stacks", "state"):
        (r / d).mkdir(parents=True, exist_ok=True)
    return r


def _svc(root: Path) -> ControllerService:
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))


def _seed(root: Path) -> ControllerService:
    """A box: a PKI, a secret, secrets.toml and graywolf's state."""
    svc = _svc(root)
    assert svc.webserver_init(dns_sans=["box.invalid"], ip_sans=[]).ok
    (root / "config/secrets/xr_pw").write_bytes(b"SECRET-PROBE")
    os.chmod(root / "config/secrets/xr_pw", 0o600)
    (root / "config/secrets.toml").write_text("[x]\n")
    (root / "state/graywolf").mkdir()
    (root / "state/graywolf/graywolf.db").write_bytes(b"db")
    return svc


def _tree(root: Path) -> dict:
    """{relative name: (kind, mode, bytes)} of everything under the restore targets."""
    out = {}
    for top in ("config/tls", "config/secrets", "config/secrets.toml", "state/graywolf"):
        base = root / top
        if not os.path.lexists(base):
            continue
        for p in [base] + (sorted(base.rglob("*")) if base.is_dir() else []):
            st = p.lstat()
            out[str(p.relative_to(root))] = (
                ("dir", stat.S_IMODE(st.st_mode), b"") if p.is_dir()
                else ("file", stat.S_IMODE(st.st_mode), p.read_bytes()))
    return out


def test_on_a_terminal_overwrite_without_the_typed_word_writes_nothing(tmp_path, monkeypatch, capsys):
    a = _root(tmp_path, "a")
    assert _seed(a).secrets_backup(str(tmp_path / "b.tar")).ok
    b = _root(tmp_path, "b")
    before = _tree(b)
    monkeypatch.setattr(cli, "ControllerService", lambda: _svc(b))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert cli.main(["secrets", "restore", str(tmp_path / "b.tar"), "--overwrite"]) == 0
    assert "Aborted; no target was changed." in capsys.readouterr().out and _tree(b) == before


def test_on_a_terminal_overwrite_with_the_typed_word_applies(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    assert _seed(a).secrets_backup(str(tmp_path / "b.tar")).ok
    b = _root(tmp_path, "b")
    monkeypatch.setattr(cli, "ControllerService", lambda: _svc(b))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "overwrite")
    assert cli.main(["secrets", "restore", str(tmp_path / "b.tar"), "--overwrite"]) == 0
    assert _tree(b) == _tree(a)
