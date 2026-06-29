"""5.2 — supported local web deployment: loopback-only serving + a hardened, NON-installed
user systemd unit template. lhpc never installs/enables/starts it (static checks only)."""

from pathlib import Path

import pytest

from lhpc.adapters.web.app import run_server

_ROOT = Path(__file__).resolve().parent.parent
_UNIT = _ROOT / "deploy" / "lhpc-web.service"


def test_unit_template_exists():
    assert _UNIT.is_file()


def test_unit_is_loopback_and_no_public_bind():
    t = _UNIT.read_text()
    assert "--host 127.0.0.1" in t
    assert "0.0.0.0" not in t


def test_unit_has_bounded_restart_and_journald():
    t = _UNIT.read_text()
    assert "Restart=on-failure" in t and "StartLimitBurst=" in t and "RestartSec=" in t
    assert "StandardOutput=journal" in t and "StandardError=journal" in t


def test_unit_has_conservative_hardening():
    t = _UNIT.read_text()
    for directive in ("NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=read-only",
                      "ReadWritePaths=%h/loraham-pi-control", "RestrictAddressFamilies="):
        assert directive in t, directive
    # PrivateTmp MUST be false so the daemon's shared /tmp sockets stay visible.
    assert "PrivateTmp=false" in t


def test_unit_is_user_service_not_system():
    t = _UNIT.read_text()
    assert "WantedBy=default.target" in t      # user target, not multi-user.target
    assert "\nUser=" not in t                   # runs as the invoking user, no root


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "::", "192.168.1.10"])
def test_run_server_refuses_non_loopback(host, capsys):
    rc = run_server(host=host, port=8770)       # must NOT bind; returns 1
    assert rc == 1
    assert "refusing to bind" in capsys.readouterr().out
