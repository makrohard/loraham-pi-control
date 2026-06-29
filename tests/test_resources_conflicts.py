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
from lhpc.core.resources import interpret_conflicts


def _spi(mode: ResourceMode) -> ResourceClaim:
    return ResourceClaim(key="spi.bus.0", kind=ResourceKind.SPI_BUS, mode=mode, group="spi.bus.0")


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
