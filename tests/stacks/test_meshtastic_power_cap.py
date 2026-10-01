"""Meshtastic's TX power cap: a fresh node must not transmit at the RF95's 20 dBm.

meshtasticd reads `Lora.RF95_MAX_POWER` from the YAML LHPC generates before every start and
clamps the node's own `lora.tx_power` to it at radio init (firmware RadioInterface::limitPower /
RF95Interface), so the cap holds from the first frame. A fresh node's tx_power is unset, which
the firmware turns into the region maximum (27 on EU_868) and the chip into 20 dBm — hence the
default of 17. LHPC never writes `lora.tx_power`, so a lower value the owner set is kept.
"""
from pathlib import Path

import pytest

from lhpc.core.assets import asset_text
from lhpc.core.config import update_yaml
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.validators import ValidationError, validate_param


def _comp(tmp_path):
    fake = FakeSystem(paths=set(), commands={})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=Path(tmp_path)))
    return next(c for s in svc.stacks() for c in s.components if c.id == "meshtastic")


def _cap_lines(text):
    return [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith("RF95_MAX_POWER:")]


def test_fresh_node_is_capped_at_17_dbm_in_the_generated_yaml(tmp_path):
    fc = _comp(tmp_path).config_file
    out = update_yaml(asset_text("bases/meshtasticd.yaml"), fc.params, {}, lambda s: s)
    lora = out.split("\nLora:", 1)[1].split("# 433 MHz", 1)[0]
    assert _cap_lines(lora) == ["RF95_MAX_POWER: 17"]


def test_the_cap_is_the_owner_s_setting(tmp_path):
    fc = _comp(tmp_path).config_file
    out = update_yaml(asset_text("bases/meshtasticd.yaml"), fc.params, {"max_power": "20"},
                      lambda s: s)
    assert _cap_lines(out) == ["RF95_MAX_POWER: 20"]
    p = next(p for p in fc.params if p.name == "max_power")
    assert validate_param(p, "12") == "12"


@pytest.mark.parametrize("bad", ["0", "21", "27"])
def test_a_cap_outside_the_chip_s_range_is_refused(tmp_path, bad):
    p = next(p for p in _comp(tmp_path).config_file.params if p.name == "max_power")
    with pytest.raises(ValidationError):
        validate_param(p, bad)


def test_lhpc_never_writes_the_node_s_own_tx_power(tmp_path):
    # The owner's lower tx_power (e.g. 12, set in the web client) survives starts and updates
    # because no post-start step touches it; only the cap above limits it.
    comp = _comp(tmp_path)
    argv = [str(a) for step in comp.post_steps for a in step.get("argv", ())]
    assert not any("tx_power" in a for a in argv)
