"""L4: the firewall candidate per instance (A §2.5; PROOF T-F1, T-F6, T-G2, CO-16). Until L5 every stack has one
instance at slot 0, so the candidate is today's (the firewall suite and its goldens hold it, T-F1). With a slot-k
instance (injected here, as L5 will assign them) the candidate gains exactly that instance's rows: id
`<component>-<rid>.tcp-<base>`, port the slot-0 port + 10k; a tick on the slot-0 id selects every instance's row;
the AP's DHCP/DNS rules render the same."""

import dataclasses

from lhpc.core import commands
from lhpc.core import firewall_helper as fh
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.save_config("kiss", {"kiss_host": "0.0.0.0"}).ok      # the KISS port off-box: a firewall row
    return svc


def _with_slot1_on_r2(svc, monkeypatch, stacks=("kiss", "meshtastic")):
    # raising=False: on a code without instances the candidate simply lacks the slot-k rows (the red-before)
    monkeypatch.setattr(type(svc), "_fw_instances",
                        lambda self, stk: (commands.SLOT0, commands.Instance(1, "r2"))
                        if stk.id in stacks else (commands.SLOT0,), raising=False)


def _rows(cand):
    return {e["id"]: e for e in cand["endpoints"]}


def test_t_g2_a_slot_k_instance_adds_exactly_its_own_rows(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    today = _rows(svc.firewall_candidate())
    _with_slot1_on_r2(svc, monkeypatch)
    rows = _rows(svc.firewall_candidate())
    assert {k: today[k] for k in today} == {k: rows[k] for k in today}           # today's rows unchanged
    added = {k: (v["port"], v["deny_default"]) for k, v in rows.items() if k not in today}
    assert added == {"loraham-kiss-tnc-r2.tcp-8001": (8011, False),
                     "meshtastic-r2.tcp-4403": (4413, True), "meshtastic-r2.tcp-9443": (9453, True)}


def test_a_configured_slot0_port_moves_slot_k_with_it(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    assert svc.save_config("kiss", {"kiss_port": "8050"}).ok
    _with_slot1_on_r2(svc, monkeypatch, stacks=("kiss",))
    rows = _rows(svc.firewall_candidate())
    assert (rows["loraham-kiss-tnc.tcp-8001"]["port"], rows["loraham-kiss-tnc-r2.tcp-8001"]["port"]) == (8050, 8060)


def test_t_f6_a_tick_on_the_slot0_id_selects_every_instance(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _with_slot1_on_r2(svc, monkeypatch, stacks=("kiss",))
    fwcfg = dataclasses.replace(svc.config().firewall, allow_endpoints=("loraham-kiss-tnc.tcp-8001",))
    rows = _rows(svc.firewall_candidate(fwcfg))
    assert rows["loraham-kiss-tnc.tcp-8001"]["selected"] and rows["loraham-kiss-tnc-r2.tcp-8001"]["selected"]


def test_co16_the_ap_rules_render_the_same_with_instances(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    fwcfg = dataclasses.replace(svc.config().firewall, ap_enabled=True, ap_interface="wlan0", ap_cidr="10.42.0.0/24")

    def ap_rules():
        rules = fh.resolve_model(svc.firewall_candidate(fwcfg), ownership_id="t", ssh_scopes=[])["rules"]
        return [r for r in rules if str(r.get("match", "")).startswith("ap-")]
    before = ap_rules()
    _with_slot1_on_r2(svc, monkeypatch)
    assert before and ap_rules() == before
