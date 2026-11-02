"""`lhpc hardware <setup>` and the hardware list, as a property over every setup and every state of the list
(TEST-DESIGN-B5-AB-v3 §1; the code role's DESIGN-AB-v2 A1–A12).

The expected rows come from the catalog and the model table, not from a copied table: one row per band of the setup
in band order, the model whose variant runs that band's preset, the name `<model short> <band>`, and `ce` only when
the model has several variants for the band. A list that exists in any state stays as it is, and the setup is still
saved. The CLI is the public path; radios.py is new, so each test imports it itself.
"""
from __future__ import annotations

import os

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import radio_models
from lhpc.core.paths import Paths

SETUPS = sorted(cfgmod.HW_SETUPS)
STATES = ["absent", "valid", "invalid", "dangling", "directory"]
EXISTS = "  hardware list: exists, not changed (config/radios.toml names the radios; a daemon starts for each band the board setup serves)"   # measured, CLI at 66f162d8


def _cli(tmp_path, monkeypatch, capsys, *args):
    from lhpc.adapters.cli import main as cli
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    try:
        rc = cli.main(["hardware", *args])
    except SystemExit as exc:                    # argparse refuses an argument outside the choices
        rc = exc.code
    return rc, capsys.readouterr()


def _expected_rows(setup_id):
    """The rows the design names for an absent list, derived from the catalog and the model table."""
    models = radio_models.load()
    rows = []
    for band, preset in sorted(cfgmod.HW_SETUPS[setup_id][1].items()):
        (m, v), = [(m, v) for m in models.values() for v in m.variants if v.preset == preset and v.band in ("", band)]
        several = len([x for x in m.variants if x.band in ("", band)]) > 1
        rows.append((f"r{len(rows) + 1}", f"{m.short} {band}", m.id, band, v.key if several else ""))
    return rows


def _make(tmp_path, state):
    from lhpc.core import radios
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    path = cfg / "radios.toml"
    if state == "valid":
        path.write_text(radios.render((radios.Radio("r5", "kept", "loraham", "433"),)))
    elif state == "invalid":
        path.write_text("[[radio]]\nid = 7\n")
    elif state == "dangling":
        os.symlink(tmp_path / "nowhere", path)
    elif state == "directory":
        path.mkdir()
        (path / "inside").write_text("kept")
    return path


def _snapshot(path):
    if path.is_symlink():
        return ("link", os.readlink(path))
    if path.is_dir():
        return ("dir", sorted((p.name, p.read_bytes()) for p in path.iterdir()))
    return ("file", path.read_bytes()) if path.exists() else ("absent",)


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("setup", SETUPS)
def test_every_setup_against_every_state_of_the_list(tmp_path, monkeypatch, capsys, setup, state):
    from lhpc.core import radios
    path = _make(tmp_path, state)
    before = _snapshot(path)
    rc, out = _cli(tmp_path, monkeypatch, capsys, setup)
    assert rc == 0, out.err
    assert cfgmod.load_config(Paths(runtime_root=tmp_path)).radio.hardware == setup      # the setup is saved
    if state != "absent":
        assert _snapshot(path) == before                                              # a guard: held before A too
        assert EXISTS in out.out.splitlines()                                         # new with A: the detail says so
        return
    want = _expected_rows(setup)
    if not want:                                                                      # unset: no radio, no list
        assert not os.path.lexists(path)
        assert "  hardware list: stays absent (no radio in this setup)" in out.out.splitlines()   # measured
        return
    got = radios.load(Paths(runtime_root=tmp_path))
    assert got.kind == radios.VALID
    assert [(r.id, r.name, r.model, r.band, r.ce) for r in got.radios] == want
    assert [r.band for r in got.radios] == sorted(r.band for r in got.radios)          # band order, 433 first


@pytest.mark.parametrize("arg", ["", " uputronics", "UPUTRONICS", "uputronics\n", "../x", "u" * 4096, "nosuch"])
def test_a_setup_outside_the_catalog_is_refused_by_the_cli_and_writes_nothing(tmp_path, monkeypatch, capsys, arg):
    (tmp_path / "config").mkdir()
    rc, out = _cli(tmp_path, monkeypatch, capsys, arg)
    assert rc == 2 and "invalid choice" in out.err                                    # measured: argparse choices
    assert sorted(p.name for p in (tmp_path / "config").iterdir()) == []


@pytest.mark.parametrize("value", ["", " uputronics", "UPUTRONICS", "uputronics\n", "../x", "u" * 4096, 1, True, 0.5,
                                   b"loraham"])
def test_the_service_refuses_a_setup_outside_the_catalog_and_writes_nothing(tmp_path, value):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir()
    res = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).set_hardware_setup(value)
    assert not res.ok and res.summary.startswith("unknown hardware setup")             # measured at 66f162d8
    assert sorted(p.name for p in (tmp_path / "config").iterdir()) == []


def test_a_written_list_is_left_alone_by_a_second_setup(tmp_path, monkeypatch, capsys):
    from lhpc.core import radios
    (tmp_path / "config").mkdir()
    assert _cli(tmp_path, monkeypatch, capsys, "uputronics")[0] == 0
    first = (tmp_path / "config" / "radios.toml").read_bytes()
    rc, out = _cli(tmp_path, monkeypatch, capsys, "waveshare-868")
    assert rc == 0 and EXISTS in out.out.splitlines()
    assert (tmp_path / "config" / "radios.toml").read_bytes() == first
    assert [r.band for r in radios.load(Paths(runtime_root=tmp_path)).radios] == ["433", "868"]


def test_band_order_holds_for_a_setup_that_lists_868_first(tmp_path, monkeypatch, capsys):
    """Band order is the rule, not the catalog's order: today's catalog lists 433 first, so a setup written the other
    way round is the only input that tells the two apart."""
    from lhpc.core import radios
    name, presets = cfgmod.HW_SETUPS["uputronics"]
    monkeypatch.setitem(cfgmod.HW_SETUPS, "uputronics", (name, dict(reversed(list(presets.items())))))
    assert list(cfgmod.HW_SETUPS["uputronics"][1]) == ["868", "433"]
    (tmp_path / "config").mkdir()
    assert _cli(tmp_path, monkeypatch, capsys, "uputronics")[0] == 0
    assert [(r.id, r.band) for r in radios.load(Paths(runtime_root=tmp_path)).radios] == [("r1", "433"), ("r2", "868")]


def test_a_list_path_that_is_refused_is_never_written_over(tmp_path, monkeypatch, capsys):
    """A path the runtime root refuses (radios.path raises) is not absent: nothing is written, the setup is saved."""
    from lhpc.core import radios
    from lhpc.core.paths import PathContainmentError

    def refused(_paths):
        raise PathContainmentError("config/radios.toml escapes the runtime root")
    monkeypatch.setattr(radios, "path", refused)
    (tmp_path / "config").mkdir()
    rc, out = _cli(tmp_path, monkeypatch, capsys, "loraham")
    assert rc == 0 and EXISTS in out.out.splitlines()
    assert not os.path.lexists(tmp_path / "config" / "radios.toml")
    assert cfgmod.load_config(Paths(runtime_root=tmp_path)).radio.hardware == "loraham"
