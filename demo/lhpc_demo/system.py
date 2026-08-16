"""The demo's simulation System. Built ONLY on lhpc's own FakeSystem primitives (never
testlab). S1: the stock FakeSystem, enough for the real routes to render. Later slices add
a stateful dispatching runner (stack lifecycle, nmcli/systemctl, scenarios) over it."""
from __future__ import annotations


def build_demo_system(paths):
    from lhpc.core.probes.backends import FakeSystem
    return FakeSystem().system
