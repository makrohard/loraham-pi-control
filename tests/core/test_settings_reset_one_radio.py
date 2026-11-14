"""A Settings reset resets one radio (A29 B, Q-B2-6 a): its own file, its auto-start ticks included. Another radio's
settings and the old stack-wide file stay as they are. A list on which no radio can run the stack refuses the reset
by name and changes no file; a box without a hardware list resets its one stack-wide file, ticks included."""
from __future__ import annotations

import tomllib

import pytest
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
TICK = "autostart_meshcore-webui"


def _svc(tmp_path, rows=ROWS):
    if rows is not None:
        radios_fixture.write(tmp_path, rows)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _inst(svc, key):
    return instances.parse_target(svc, key, "start")[1]


def _stored(tmp_path, name):
    p = tmp_path / "config" / "stacks" / name
    return tomllib.loads(p.read_text()) if p.exists() else {}


def test_a_radios_reset_clears_its_own_tick_and_keeps_the_other_radios(tmp_path):
    svc = _svc(tmp_path)
    for rid in ("r2", "r3"):
        assert svc.save_config_bundle("meshcore", values={TICK: "on"}, inst=_inst(svc, f"meshcore@{rid}")).ok
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore", {TICK: "on", "dest": "ALL"})   # old global
    before = _stored(tmp_path, "meshcore.toml")
    res = svc.reset_config("meshcore", inst=_inst(svc, "meshcore@r3"))
    assert res.ok, (res.summary, res.details)
    assert TICK not in _stored(tmp_path, "meshcore@r3.toml")
    assert _stored(tmp_path, "meshcore@r2.toml").get(TICK) == "on"
    assert _stored(tmp_path, "meshcore.toml") == before                       # the old stack-wide file untouched


def test_a_reset_on_a_list_without_a_radio_of_its_bands_is_refused_and_changes_nothing(tmp_path):
    svc = _svc(tmp_path, rows=ROWS[:1])                                       # only r1, a 433 radio; meshcore is 868
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore", {TICK: "on"})
    files = {p.name: p.read_bytes() for p in (tmp_path / "config" / "stacks").iterdir()}
    res = svc.reset_config("meshcore")
    assert not res.ok and "meshcore: no radio of its bands in the hardware list" in res.summary, res.summary
    assert {p.name: p.read_bytes() for p in (tmp_path / "config" / "stacks").iterdir()} == files


@pytest.mark.no_default_hardware                     # the list is really absent (conftest's baseline list off)
def test_a_box_without_a_hardware_list_resets_its_one_file_ticks_included(tmp_path):
    svc = _svc(tmp_path, rows=None)
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "kiss", {"autostart_loraham-kiss-serial": "on",
                                                                     "manual_note": "kept"})
    assert svc.reset_config("kiss").ok
    assert _stored(tmp_path, "kiss.toml") == {"manual_note": "kept"}
