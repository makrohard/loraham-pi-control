"""M2: productive Unix-socket serving with mandatory Waitress (no dev-server fallback),
and the preserved loopback-only guard for interactive TCP."""

from __future__ import annotations

import sys
from pathlib import Path

from lhpc.adapters.web.app import run_server


def test_tcp_mode_still_refuses_non_loopback():
    # Interactive TCP path keeps the loopback-only guard (regression of existing behavior).
    assert run_server(host="1.2.3.4", port=8770, socket=False) == 1


def test_socket_mode_fail_closed_without_waitress(monkeypatch, tmp_path: Path):
    # Simulate waitress absent: productive (socket) serving must FAIL CLOSED, never fall back
    # to the Flask dev server.
    monkeypatch.setitem(sys.modules, "waitress", None)   # `from waitress import serve` -> ImportError
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "state" / "locks").mkdir(parents=True)
    rc = run_server(socket=True)
    assert rc == 1        # refused; no dev-server fallback on the productive path
