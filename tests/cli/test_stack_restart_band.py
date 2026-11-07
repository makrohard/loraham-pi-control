"""Package item 14 (LEG-D12): `lhpc stack restart` takes `--band`, as start and stop do.

A restart of one band's daemon on a band two enabled radios share is refused by the service, naming both, with
nothing signalled (band_refusal, the stop's W1-11 rule); on a band of one radio the band is passed on. The CLI runs
on a FakeSystem service over a temporary runtime root, never the test host's processes."""
import os

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService

TWO_868 = (radios.Radio("r1", "a", "uputronics", "868", ce="ce0"), radios.Radio("r2", "b", "uputronics", "868", ce="ce1"))
DAEMONS = {100: ["loraham_daemon", "--radio", "868", "--id", "r1"], 101: ["loraham_daemon", "--radio", "868", "--id", "r2"]}


def _fake_service(monkeypatch, tmp_path, rows):
    radios_fixture.write(tmp_path, rows)
    real = ControllerService
    monkeypatch.setattr(cli, "ControllerService",
                        lambda *a, **k: real(system=FakeSystem(cmdlines_data=DAEMONS).system,
                                             paths=Paths(runtime_root=tmp_path)))


def test_a_restart_of_a_shared_band_is_refused_naming_both_radios_and_signals_nothing(tmp_path, monkeypatch,
                                                                                        capsys):
    _fake_service(monkeypatch, tmp_path, TWO_868)
    signals = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append(("kill", pid, sig)))
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append(("killpg", pgid, sig)))
    assert cli.main(["stack", "restart", "daemon", "--band", "868", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "Cannot restart the daemon of 868 MHz alone: 2 radios share it: r1 (a), r2 (b)" in out, out
    assert signals == []


@pytest.mark.parametrize("band", ["433", "868"])
def test_a_restart_passes_its_band_on(tmp_path, monkeypatch, band):
    _fake_service(monkeypatch, tmp_path, radios_fixture.RADIOS)
    calls = []

    def cap(self, op, target, **kw):
        calls.append((op, target, kw.get("apply"), kw.get("band")))
        return ActionResult(True, "ok", data={"changes": 1})
    monkeypatch.setattr(ControllerService, "run_action", cap)
    assert cli.main(["stack", "restart", "daemon", "--band", band, "--yes"]) == 0
    assert calls[-1] == ("restart", "daemon", True, band)


def test_a_restart_band_outside_433_868_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["stack", "restart", "daemon", "--band", "915", "--yes"])
    assert exc.value.code == 2 and "invalid choice: '915'" in capsys.readouterr().err
