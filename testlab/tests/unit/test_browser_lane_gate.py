"""Test rule E7, browser half: the browser lane run as CI runs it (LHPC_BROWSER=1 under `CI`) FAILS when Chromium is
missing; locally the same lane skips with the reason, and under CI a run that did not ask for the lane still skips.
Each case runs one browser test file in a child pytest whose Playwright browser directory is empty, so Chromium is
missing whether or not Playwright is installed."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_TARGET = Path(__file__).resolve().parents[1] / "browser" / "test_banners.py"


def _lane(tmp_path, **env):
    """The browser file run in a child pytest with `env` over the current environment minus CI and LHPC_BROWSER."""
    (tmp_path / "no-browsers").mkdir()
    child = {k: v for k, v in os.environ.items() if k not in ("CI", "LHPC_BROWSER")}
    child.update(PLAYWRIGHT_BROWSERS_PATH=str(tmp_path / "no-browsers"), **env)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:randomly", "-p", "no:cacheprovider",
                           str(_TARGET), f"--basetemp={tmp_path / 'bt'}"],
                          capture_output=True, text=True, timeout=120, check=False, env=child)


def test_under_ci_the_browser_lane_fails_without_chromium(tmp_path):
    r = _lane(tmp_path, CI="true", LHPC_BROWSER="1")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out[-2000:]
    assert "browser lane under CI:" in out and "CI must provide Chromium (test rule E7)" in out, out[-2000:]
    assert "skipped" not in out, out[-2000:]


def test_locally_the_browser_lane_skips_without_chromium(tmp_path):
    r = _lane(tmp_path, LHPC_BROWSER="1")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "1 skipped" in out and ("chromium not usable" in out or "playwright not installed" in out), out[-2000:]


def test_under_ci_a_run_without_the_browser_lane_still_skips(tmp_path):
    r = _lane(tmp_path, CI="true")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "1 skipped" in out and "browser lane is opt-in" in out, out[-2000:]
