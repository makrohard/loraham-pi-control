"""`lhpc gps`: the read-only Monitor view (`--monitor`, `--sats`) — its output and exit status."""

from lhpc.adapters.cli.main import main
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

LAT, LON = 51.477812, -0.001545


def test_service_monitor_matches_cli_snapshot(tmp_path, capsys):
    """`lhpc gps --monitor` prints the same snapshot; `--sats` needs it; setting flags refused."""
    assert main(["gps", "--source", "fixed", "--lat", str(LAT), "--lon", str(LON), "--alt", "45"]) == 0
    capsys.readouterr()
    assert main(["gps", "--monitor"]) == 0
    out = capsys.readouterr().out
    assert "fixed position (configured)" in out and f"{LAT:.6f}" in out and "45.0 m MSL" in out
    assert main(["gps", "--sats"]) == 2
    assert "--sats needs --monitor" in capsys.readouterr().out
    assert main(["gps", "--monitor", "--source", "gpsd"]) == 2
    assert "read-only" in capsys.readouterr().out
    svc = ControllerService(system=FakeSystem().system)
    assert svc.gps_settings()["source"] == "fixed"          # the refused call changed nothing
