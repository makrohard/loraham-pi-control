"""0.14.0 (REL14-D2 A, commit 6): `lhpc hardware <setup>` — an explicit operator action, not a conversion — also writes
the hardware list when there is none: one row per band of the catalog setup. An existing list is never touched."""
import os

import pytest
import radios_fixture

from lhpc.core import radio_daemon, radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _rows(tmp_path):
    return [(r.id, r.name, r.model, r.band, r.ce) for r in radios.load(Paths(runtime_root=tmp_path)).radios]


# The design table (rule 1): setup -> the rows written to an absent list.
@pytest.mark.parametrize("setup,rows", [
    ("loraham", [("r1", "loraham 433", "loraham", "433", ""), ("r2", "loraham 868", "loraham", "868", "")]),
    ("uputronics", [("r1", "uputron 433", "uputronics", "433", "ce0"), ("r2", "uputron 868", "uputronics", "868", "ce1")]),
    ("uputronics-x", [("r1", "uputron 433", "uputronics", "433", "ce1"), ("r2", "uputron 868", "uputronics", "868", "ce0")]),
    ("uputronics-433", [("r1", "uputron 433", "uputronics", "433", "ce0")]),
    ("uputronics-868", [("r1", "uputron 868", "uputronics", "868", "ce1")]),
    ("waveshare-433", [("r1", "wavesh 433", "waveshare-sx1262", "433", "")]),
    ("waveshare-868", [("r1", "wavesh 868", "waveshare-sx1262", "868", "")]),
])
def test_a_setup_writes_one_row_per_band(tmp_path, setup, rows):
    res = _svc(tmp_path).set_hardware_setup(setup)
    assert res.ok and _rows(tmp_path) == rows
    assert res.details[-1] == "  hardware list written: " + ", ".join(
        f"{rid} {name!r} ({model}, {band} MHz)" for rid, name, model, band, _ce in rows)
    assert radios.load(Paths(runtime_root=tmp_path)).startable


def test_unset_writes_no_list(tmp_path):
    res = _svc(tmp_path).set_hardware_setup("unset")
    assert res.ok and not (tmp_path / "config" / "radios.toml").exists()          # A6: nothing written, said so
    assert res.details[-1] == "  hardware list: stays absent (no radio in this setup)"


@pytest.mark.parametrize("make", [
    lambda p: radios_fixture.write(p),                                               # a valid list
    lambda p: (p / "config" / "radios.toml").write_text("[[radio]]\nid = 7\n"),       # an invalid one
    lambda p: os.symlink(p / "nowhere", p / "config" / "radios.toml"),               # a dangling link
])
def test_an_existing_list_is_never_touched_and_the_setup_still_names_the_bands(tmp_path, make):
    # A list in any state (valid, invalid, a dangling link) stays byte-equal; the setup itself is saved as before,
    # because it names the served bands (B4-D26).
    from lhpc.core import config
    svc = _svc(tmp_path)
    make(tmp_path)
    path = tmp_path / "config" / "radios.toml"
    before = os.readlink(path) if path.is_symlink() else path.read_bytes()
    res = svc.set_hardware_setup("waveshare-868")
    assert res.ok and res.details[-1] == ("  hardware list: exists, not changed (config/radios.toml names the radios; "
                                          "a daemon starts for each band the board setup serves)")
    assert (os.readlink(path) if path.is_symlink() else path.read_bytes()) == before
    assert config.load_config(Paths(runtime_root=tmp_path)).radio.hardware == "waveshare-868"


def test_a_refused_save_writes_nothing_and_fails(tmp_path, monkeypatch):
    # A11: the list is written first; when its save refuses, local.toml is not touched either.
    monkeypatch.setattr(radios, "save", lambda paths, rows, models=None: ["r1: refused by a save check"])
    res = _svc(tmp_path).set_hardware_setup("loraham")
    assert (res.ok, res.summary) == (False, "hardware setup not set: hardware list: not written: r1: refused by a "
                                            "save check")
    assert not (tmp_path / "config" / "radios.toml").exists() and not (tmp_path / "config" / "local.toml").exists()


def test_a_path_that_cannot_be_judged_is_a_list(tmp_path, monkeypatch):
    # tri-state: a probe that raises is never "absent", so nothing is written over it
    def boom(_p):
        raise PermissionError("denied")
    monkeypatch.setattr(radios, "exists_at_all", boom)
    res = _svc(tmp_path).set_hardware_setup("loraham")
    assert res.details[-1] == "  hardware list: exists, not changed (config/radios.toml names the radios; a daemon starts for each band the board setup serves)"
    assert not (tmp_path / "config" / "radios.toml").exists()


def test_a_failed_setup_save_after_the_list_names_the_written_list(tmp_path, monkeypatch):
    from lhpc.core import config
    def fail(*_a, **_k):
        raise OSError("read-only")
    monkeypatch.setattr(config, "save_hardware_setup", fail)
    res = _svc(tmp_path).set_hardware_setup("waveshare-433")
    assert (res.ok, res.summary) == (False, "could not save hardware setup: read-only")
    assert res.details == ["  hardware list written: r1 'wavesh 433' (waveshare-sx1262, 433 MHz)"]


def test_the_cli_setup_ends_the_old_config_loop(tmp_path, capsys, monkeypatch):
    # Before 0.14.0's list: the old [radio] table shows the W12 banner and refuses every radio start. After
    # `lhpc hardware loraham` the list exists, so neither applies and r1's daemon has a start plan.
    from lhpc.adapters.cli import main as cli
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text('[radio]\nhardware = "loraham"\n')
    paths = Paths(runtime_root=tmp_path)
    assert radio_daemon.old_config_state(paths) is radio_daemon.Tri.YES
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    rc = cli.main(["hardware", "loraham"])
    out = capsys.readouterr().out
    assert rc == 0 and "hardware list written: r1 'loraham 433' (loraham, 433 MHz), r2 'loraham 868'" in out
    assert radio_daemon.old_config_state(paths) is radio_daemon.Tri.NO
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    params, refusal, _notes = radio_start.start_plan(svc, "r1")
    assert refusal == "" and params["id"] == "r1" and params["radio"] == "433"
