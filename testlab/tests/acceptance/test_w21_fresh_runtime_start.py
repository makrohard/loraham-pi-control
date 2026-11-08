"""W21 (package v3 item 21): a daemon start on a FRESH runtime root, the first start a new box makes. The shared lab
of this lane keeps `logs/<rid>/` from its first start, so this case builds its own lab root: after init + reset no
`logs/r1/` exists, the start must create it before the launch, and the fake daemon (which opens its RF log at init
and exits 1 when it cannot, as the real one does) must then come up READY. Box H's first live start failed exactly
here (row D1, 2026-11-02)."""
from __future__ import annotations

import pytest
from lhpc_testlab.testing import lab_env, named, run_lab, run_lhpc


@pytest.mark.slow
def test_the_first_daemon_start_on_a_fresh_runtime_creates_the_rf_log_dir_and_comes_up(tmp_path_factory):
    root = tmp_path_factory.mktemp("freshroot") / "runtime"
    env = lab_env(root)
    run_lab(env, "init", check=True)
    run_lab(env, "reset", check=True)
    assert not (root / "logs" / "r1").exists()                  # fresh: no start has made it yet
    run_lhpc(env, "install", "daemon", "--yes", check=True, timeout=600)
    try:
        r = run_lhpc(env, "stack", "start", named(env, "daemon", band="433"), "--yes", timeout=300)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        assert (root / "logs" / "r1" / "rf-daemon.log").is_file()
        view = run_lhpc(env, "daemon", "r1", timeout=60)
        assert view.returncode == 0 and "Radio: READY" in view.stdout, view.stdout[-2000:]
    finally:
        run_lhpc(env, "stack", "stop", named(env, "daemon"), "--yes", timeout=300)
