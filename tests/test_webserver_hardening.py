"""M12: persistent web session secret (survives restart, explicit rotation), cookie
hardening, and the nginx-set-header-only loopback decision."""

from __future__ import annotations

from pathlib import Path

from lhpc.adapters.web import app as webapp
from lhpc.core import config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _paths(tmp_path: Path) -> Paths:
    return Paths(runtime_root=tmp_path)


def test_session_secret_persists_and_rotates(tmp_path):
    paths = _paths(tmp_path)
    s1 = config.web_session_secret(paths)
    assert len(s1) >= 32
    assert config.web_session_secret(paths) == s1        # stable across calls (survives restart)
    import os, stat
    mode = stat.S_IMODE(os.stat(tmp_path / "config/secrets/web_session.key").st_mode)
    assert mode == 0o600
    s2 = config.rotate_web_session_secret(paths)
    assert s2 != s1 and config.web_session_secret(paths) == s2   # explicit rotation changed it


def test_create_app_uses_persistent_secret(tmp_path):
    paths = _paths(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    app1 = webapp.create_app(lambda: svc)
    app2 = webapp.create_app(lambda: ControllerService(system=FakeSystem().system, paths=paths))
    assert app1.secret_key == app2.secret_key == config.web_session_secret(paths)
    # cookie hardening defaults (Secure enabled only on the productive socket path)
    assert app1.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app1.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app1.config["SESSION_COOKIE_SECURE"] is False


def test_peer_is_loopback_trusts_only_nginx_header(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    app = webapp.create_app(lambda: svc)
    with app.test_request_context(headers={"X-LHPC-Peer": "remote"}):
        assert webapp.peer_is_loopback() is False
    with app.test_request_context(headers={"X-LHPC-Peer": "loopback"}):
        assert webapp.peer_is_loopback() is True
    # A client-supplied spoof cannot help: only the nginx-set value is read; absent => not remote.
    with app.test_request_context():
        assert webapp.peer_is_loopback() is True
