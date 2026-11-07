"""W25 (package v3 item 25): the lab's fake daemon refuses a value-taking option with no value (a trailing `--radio`,
`--id` or `--rflog-path`) with exit 2 and getopt's wording, never the exit 1 of its "cannot open RF log" refusal, so
the two can be told apart. (The real daemon's getopt_long exits 1 for both; the fake keeps them apart on purpose.)"""
import os
import subprocess
import sys

import pytest


def _fake(tmp_path, *args):
    from lhpc_testlab import data_path
    script = str(data_path("loraham-daemon-fake", "loraham_daemon", "loraham_daemon"))
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True, timeout=10, check=False,
                          env=dict(os.environ, LORAHAM_SOCKET_DIR=str(tmp_path)))


@pytest.mark.parametrize("args", [
    ("--radio",),
    ("--radio", "433", "--id"),
    ("--radio", "433", "--id", "r1", "--rflog-path"),
])
def test_an_option_without_its_value_exits_2_naming_it(tmp_path, args):
    out = _fake(tmp_path, *args)
    assert out.returncode == 2
    assert out.stderr.strip() == f"testlab fake loraham_daemon: option '{args[-1]}' requires an argument"
    assert list(tmp_path.iterdir()) == []                      # refused before any socket


def test_an_unopenable_rf_log_still_exits_1(tmp_path):
    """A control: the RF-log refusal keeps its own exit status."""
    path = tmp_path / "logs" / "r1" / "rf-daemon.log"
    out = _fake(tmp_path, "--radio", "433", "--id", "r1", "--rflog-path", str(path))
    assert out.returncode == 1
    assert out.stderr.strip() == f"cannot open RF log {path}: No such file or directory"
