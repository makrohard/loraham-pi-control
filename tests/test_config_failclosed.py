"""A malformed/unreadable persisted stack config must FAIL CLOSED — never silently become defaults.
Only an ABSENT file yields empty/default config. Covers direct loading, the service funnel used by
start/restart/status/config-views, the CLI (typed failure, no side effects), and the web (409, no
echo/traceback). The bad file is preserved for diagnosis in every case.
"""

from pathlib import Path

import pytest

from lhpc.core.config import ConfigError, load_stack_config, _stack_config_path
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _write_malformed(paths, stack_id, band=""):
    p = _stack_config_path(paths, stack_id, band)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("this is not = valid toml [[[\n")
    return p


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))


# --- direct loading -------------------------------------------------------------------------------

def test_absent_stack_config_is_defaults(tmp_path):
    assert load_stack_config(Paths(runtime_root=tmp_path), "daemon") == {}


def test_malformed_stack_config_raises_and_is_preserved(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    bad = _write_malformed(paths, "daemon")
    with pytest.raises(ConfigError):
        load_stack_config(paths, "daemon")
    assert bad.read_text().startswith("this is not")     # left untouched for diagnosis


# --- service funnel (start/restart/reset/status/config views all read through this) ----------------

def test_stack_config_funnel_fails_closed(tmp_path):
    svc = _svc(tmp_path)
    _write_malformed(svc._paths, "daemon")
    with pytest.raises(ConfigError):
        svc.stack_config("daemon")


def test_config_view_fails_closed(tmp_path):
    svc = _svc(tmp_path)
    _write_malformed(svc._paths, "kiss")
    with pytest.raises(ConfigError):
        svc.config_view("kiss")


# --- CLI: clean typed failure, no side effects ----------------------------------------------------

def test_cli_config_reports_typed_failure_without_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    from lhpc.adapters.cli import main as cli
    cli.main(["bootstrap", "--yes"]); capsys.readouterr()
    paths = Paths(runtime_root=tmp_path)
    bad = [_write_malformed(paths, "kiss", b) for b in ("", "868", "433")]
    rc = cli.main(["config", "kiss", "list"])            # reads stored values through the funnel
    err = capsys.readouterr().err
    assert rc == 1
    assert "malformed" in err.lower() or "unreadable" in err.lower()
    assert all(p.exists() for p in bad)                  # no side effects; files preserved


# --- web: bounded 409, no echo, no traceback ------------------------------------------------------

def test_web_returns_409_on_malformed_config(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    _write_malformed(svc._paths, "daemon")
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    # A page that reads the daemon's stored config must 409, not 200-with-defaults and not 500.
    r = c.get("/stacks/daemon/body")
    assert r.status_code == 409
    assert b"Traceback" not in r.data and b"this is not" not in r.data   # no traceback, no echo
