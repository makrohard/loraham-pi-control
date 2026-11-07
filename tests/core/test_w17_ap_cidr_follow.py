"""W17 NA, C9 rule (INTERFACE-NAUX-v3): when the one apply moves the firewall's AP subnet with the setting, it goes
through firewall_configure, so the rendered firewall apply script carries the new subnet; a subnet set apart is
kept and the script keeps it. No privileged command runs: the scripts are rendered, never executed."""
from __future__ import annotations

import json
import re

import pytest

from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    return ControllerService(system=FakeSystem(files={"/proc/uptime": "100.0 200.0\n"}).system,
                             paths=Paths(runtime_root=tmp_path))


def _rendered_ap(tmp_path) -> dict:
    """The AP part of the candidate embedded in the rendered apply script (config/files/firewall)."""
    text = (tmp_path / "config" / "files" / "firewall" / "firewall-apply.sh").read_text(encoding="utf-8")
    aps = [json.loads(m) for m in re.findall(r'"ap": (\{[^{}]*\})', text)]
    assert aps, "no AP section in the rendered apply script"
    assert all(a == aps[0] for a in aps)
    return aps[0]


@pytest.mark.parametrize("saved, follows", [("10.42.0.0/24", True), ("192.168.4.0/24", False)],
                         ids=["following-moves", "set-apart-kept"])
def test_the_apply_re_renders_the_firewall_with_the_subnet_it_moves(tmp_path, saved, follows):
    svc = _svc(tmp_path)
    assert svc.firewall_configure(ap_enabled=True, ap_interface="wlan0", ap_cidr=saved).ok
    svc._invalidate_config()
    assert _rendered_ap(tmp_path)["cidr"] == saved
    res = svc.network_set_ap_cidr("10.43.0.0/24", type(svc).AP_CIDR_CONFIRM)
    assert res.ok, res.summary
    svc._invalidate_config()
    want = "10.43.0.0/24" if follows else saved
    assert cfgmod.load_config(svc._paths).firewall.ap_cidr == want
    assert _rendered_ap(tmp_path) == {"enabled": True, "interface": "wlan0", "cidr": want}
