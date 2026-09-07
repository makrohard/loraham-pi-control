"""Tests for read-only resource conflict interpretation."""

from __future__ import annotations

from lhpc.core.manifest import load_manifest
from lhpc.core.model import (
    Component,
    ComponentKind,
    ResourceClaim,
    ResourceKind,
    ResourceMode,
)
from lhpc.core.resources import interpret_conflicts, limit_radio_claims


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
    assert "loraham.radio.868" in keys      # both want the 868 radio
    assert "spi.bus.0" not in keys          # shared bus is cooperative, not a conflict


def test_real_manifest_daemons_cooperate_on_spi():
    # The two daemon instances share SPI cooperatively -> NO spi.bus.0 conflict.
    comps = [c for s in load_manifest() for c in s.components]
    conflicts = interpret_conflicts(comps, running_ids=set())
    assert not [c for c in conflicts if c.resource_key == "spi.bus.0"]


def _radio(band: str) -> ResourceClaim:
    return ResourceClaim(key=f"loraham.radio.{band}", kind=ResourceKind.RADIO_BAND, mode=ResourceMode.EXCLUSIVE)


def test_limit_radio_claims_keeps_only_the_named_bands_and_every_other_claim():
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     resources=(_radio("433"), _radio("868"), _spi(ResourceMode.COOPERATIVE)))
    out = limit_radio_claims(comp, {"433"})
    assert [r.key for r in out.resources] == ["loraham.radio.433", "spi.bus.0"]
    assert out.id == comp.id and out is not comp                      # a copy, the input untouched
    assert [r.key for r in comp.resources] == ["loraham.radio.433", "loraham.radio.868", "spi.bus.0"]


def test_limit_radio_claims_with_no_bands_strips_every_radio_claim():
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     resources=(_radio("433"), _radio("868"), _spi(ResourceMode.COOPERATIVE)))
    assert [r.key for r in limit_radio_claims(comp, set()).resources] == ["spi.bus.0"]


def test_limit_radio_claims_without_radio_claims_is_the_same_component():
    comp = _comp("chat", _spi(ResourceMode.EXCLUSIVE))
    assert limit_radio_claims(comp, {"868"}).resources == comp.resources
