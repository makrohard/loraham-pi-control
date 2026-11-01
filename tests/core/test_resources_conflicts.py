"""Tests for read-only resource conflict interpretation."""

from __future__ import annotations

import pytest

from lhpc.core.manifest import load_manifest
from lhpc.core.model import (
    Component,
    ComponentKind,
    ResourceClaim,
    ResourceKind,
    ResourceMode,
)
from lhpc.core import resources
from lhpc.core.resources import interpret_conflicts, limit_radio_claims, radio_claim


def _spi(mode: ResourceMode) -> ResourceClaim:
    return ResourceClaim(key="spi.bus.0", kind=ResourceKind.SPI_BUS, mode=mode)


def _comp(cid: str, claim: ResourceClaim) -> Component:
    return Component(id=cid, name=cid, kind=ComponentKind.SERVICE, resources=(claim,))


def test_cooperative_peers_do_not_conflict():
    comps = [_comp("d433", _spi(ResourceMode.COOPERATIVE)),
             _comp("d868", _spi(ResourceMode.COOPERATIVE))]
    assert interpret_conflicts(comps, running_ids=set()) == []


def test_exclusive_vs_cooperative_conflicts_declared():
    comps = [_comp("chat", _spi(ResourceMode.EXCLUSIVE)),
             _comp("d433", _spi(ResourceMode.COOPERATIVE))]
    conflicts = interpret_conflicts(comps, running_ids=set())
    assert len(conflicts) == 1 and not conflicts[0].observed


def test_conflict_marked_observed_when_both_running():
    comps = [_comp("chat", _spi(ResourceMode.EXCLUSIVE)),
             _comp("d433", _spi(ResourceMode.COOPERATIVE))]
    conflicts = interpret_conflicts(comps, running_ids={"chat", "d433"})
    assert conflicts[0].observed


def test_consumer_never_conflicts():
    socket_provider = ResourceClaim("loraham.daemon-socket.433", ResourceKind.DAEMON_SOCKET, ResourceMode.PROVIDER)
    socket_consumer = ResourceClaim("loraham.daemon-socket.433", ResourceKind.DAEMON_SOCKET, ResourceMode.CONSUMER)
    comps = [_comp("daemon", socket_provider), _comp("bridge", socket_consumer)]
    assert interpret_conflicts(comps, running_ids={"daemon", "bridge"}) == []


def test_meshtastic_conflicts_with_daemon_on_868_radio_not_spi():
    comps = [c for s in load_manifest() for c in s.components]
    conflicts = interpret_conflicts(comps, running_ids=set())
    pair = frozenset({"loraham-daemon", "meshtastic"})
    keys = {c.resource_key for c in conflicts if frozenset(c.holders) == pair}
    assert radio_claim("868") in keys      # both want the 868 radio
    assert "spi.bus.0" not in keys          # shared bus is cooperative, not a conflict


def test_real_manifest_daemons_cooperate_on_spi():
    # The two daemon instances share SPI cooperatively -> NO spi.bus.0 conflict.
    comps = [c for s in load_manifest() for c in s.components]
    conflicts = interpret_conflicts(comps, running_ids=set())
    assert not [c for c in conflicts if c.resource_key == "spi.bus.0"]


def _radio(band: str) -> ResourceClaim:
    return ResourceClaim(key=radio_claim(band), kind=ResourceKind.RADIO_BAND, mode=ResourceMode.EXCLUSIVE)


def test_limit_radio_claims_keeps_only_the_named_bands_and_every_other_claim():
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     resources=(_radio("433"), _radio("868"), _spi(ResourceMode.COOPERATIVE)))
    out = limit_radio_claims(comp, {"433"})
    assert [r.key for r in out.resources] == [radio_claim("433"), "spi.bus.0"]
    assert out.id == comp.id and out is not comp                      # a copy, the input untouched
    assert [r.key for r in comp.resources] == [radio_claim("433"), radio_claim("868"), "spi.bus.0"]


@pytest.mark.parametrize("key,band", [("loraham.radio.{radio:433}", "433"), ("loraham.radio.868", "868"),
                                      ("loraham.daemon-socket.{radio:868}", "868"), ("loraham.radio.r1", ""),
                                      ("loraham.radio.{radio:434}", ""), ("loraham.radio.{radio:433} ", ""),
                                      ("xloraham.radio.433", ""), ("spi.bus.0", ""), (None, ""), (433, "")])
def test_a_radio_key_names_its_bands_radio_in_either_form(key, band):
    # Stage L5 (W4-4): the shipped manifest names a radio as `{radio:<band>}`; a 0.13.x-shaped manifest's band
    # literal is the same radio (never dropped as a non-radio claim); anything else is no radio key.
    assert resources.radio_band(key) == band
    if band:
        assert resources.normal_key(key).endswith(f".{{radio:{band}}}")
        assert resources.claim_name(key, {"433": "r1", "868": "r2"}.get).endswith({"433": ".r1", "868": ".r2"}[band])
    else:
        assert resources.normal_key(key) == key and resources.claim_name(key, str) == key


def test_a_band_literal_and_its_template_conflict():
    # One radio, two spellings: the conflict is found, and it names the radio by the resolver's answer.
    a = Component(id="a", name="a", kind=ComponentKind.SERVICE, resources=(_radio("868"),))
    b = Component(id="b", name="b", kind=ComponentKind.SERVICE,
                  resources=(ResourceClaim(key="loraham.radio.868", kind=ResourceKind.RADIO_BAND,
                                           mode=ResourceMode.EXCLUSIVE),))
    out = interpret_conflicts([a, b], {"a", "b"}, {"868": "r2"}.get)
    assert [(c.resource_key, c.observed) for c in out] == [("loraham.radio.r2", True)]
    assert out[0].message.endswith("on loraham.radio.r2 — both running")


def test_limit_radio_claims_with_no_bands_strips_every_radio_claim():
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     resources=(_radio("433"), _radio("868"), _spi(ResourceMode.COOPERATIVE)))
    assert [r.key for r in limit_radio_claims(comp, set()).resources] == ["spi.bus.0"]


def test_limit_radio_claims_without_radio_claims_is_the_same_component():
    comp = _comp("chat", _spi(ResourceMode.EXCLUSIVE))
    assert limit_radio_claims(comp, {"868"}).resources == comp.resources
