"""The firewall candidate per instance (A §2.5; PROOF T-F1, T-F6, T-G2, CO-16), K4b: every instance has its own rows,
id `<component>-<rid>.tcp-<base>`, port its radio's (base + 133 x (n - 1)), resolved from the settings its instance
reads (kiss@r1: the 433 store, kiss@r2: the 868 store on the default list r1 433 / r2 868); a tick on the base id
selects every instance's row; the AP's DHCP/DNS rules render the same whatever the radios."""

import dataclasses

import radios_fixture
from lhpc.core import firewall_helper as fh
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, bands=("433", "868")):
    (tmp_path / "config").mkdir(exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for band in bands:                                       # the KISS port off-box on each band: a firewall row
        assert svc.save_config("kiss", {"kiss_host": "0.0.0.0"}, band=band).ok
    return svc


def _rows(cand):
    return {e["id"]: e for e in cand["endpoints"]}


def test_t_g2_each_instance_has_exactly_its_own_rows(tmp_path):
    rows = _rows(_svc(tmp_path).firewall_candidate())
    assert {k: (v["port"], v["deny_default"]) for k, v in rows.items()} == {
        "loraham-kiss-tnc-r1.tcp-8001": (8001, False), "loraham-kiss-tnc-r2.tcp-8001": (8134, False),   # + 133 on r2
        "meshtastic-r1.tcp-4403": (4403, True), "meshtastic-r1.tcp-9443": (9443, True),
        "meshtastic-r2.tcp-4403": (4536, True), "meshtastic-r2.tcp-9443": (9576, True)}


def test_an_instance_takes_no_row_from_another_bands_settings(tmp_path):
    # kiss off-box on 433 only: kiss@r2 (868) binds by its own store (loopback), so it has no row
    rows = _rows(_svc(tmp_path, bands=("433",)).firewall_candidate())
    kiss = [k for k in rows if k.startswith("loraham-kiss-tnc")]
    assert kiss == ["loraham-kiss-tnc-r1.tcp-8001"]


def test_a_configured_port_on_r1_never_moves_r2(tmp_path):
    # DEC-W60: each instance reads its own settings (kiss@r2: 868's store, no port: the manifest's), never r1's
    svc = _svc(tmp_path)
    assert svc.save_config("kiss", {"kiss_port": "8050"}, band="433").ok
    rows = _rows(svc.firewall_candidate())
    assert (rows["loraham-kiss-tnc-r1.tcp-8001"]["port"], rows["loraham-kiss-tnc-r2.tcp-8001"]["port"]) == (8050, 8134)


def test_t_f6_a_tick_on_the_base_id_selects_every_instance(tmp_path):
    svc = _svc(tmp_path)
    fwcfg = dataclasses.replace(svc.config().firewall, allow_endpoints=("loraham-kiss-tnc.tcp-8001",))
    rows = _rows(svc.firewall_candidate(fwcfg))
    assert rows["loraham-kiss-tnc-r1.tcp-8001"]["selected"] and rows["loraham-kiss-tnc-r2.tcp-8001"]["selected"]


def test_co16_the_ap_rules_render_the_same_whatever_the_radios(tmp_path):
    def ap_rules(svc):
        fwcfg = dataclasses.replace(svc.config().firewall, ap_enabled=True, ap_interface="wlan0", ap_cidr="10.42.0.0/24")
        rules = fh.resolve_model(svc.firewall_candidate(fwcfg), ownership_id="t", ssh_scopes=[])["rules"]
        return [r for r in rules if str(r.get("match", "")).startswith("ap-")]
    svc = _svc(tmp_path)
    before = ap_rules(svc)
    radios_fixture.write(tmp_path, radios_fixture.RADIOS + (radios_fixture.R3,))     # + a stick r3 on 868
    after = ap_rules(ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))
    assert before and after == before
