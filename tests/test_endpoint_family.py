"""Host/family-aware TCP readiness: a wrong-family/host listener on the same port
must not satisfy a loopback ready endpoint (one shared parser/probe)."""

from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.probes.endpoints import parse_endpoint, tcp_endpoint_present


def _sys(*listeners):
    return FakeSystem(listeners=list(listeners)).system


def test_parse_endpoint_forms():
    assert parse_endpoint("127.0.0.1:4403") == ("127.0.0.1", 4403, "ipv4")
    assert parse_endpoint("[::1]:4403") == ("::1", 4403, "ipv6")
    assert parse_endpoint("localhost:4403") == ("localhost", 4403, None)
    assert parse_endpoint("::1:4403")[2] == "ipv6"


def test_ipv6_listener_does_not_satisfy_ipv4_endpoint():
    sys = _sys(Listener(family="ipv6", ip="::1", port=9999, inode=1))
    ok, ev = tcp_endpoint_present(sys, "127.0.0.1:9999")
    assert not ok and "absent" in ev


def test_ipv4_listener_does_not_satisfy_ipv6_endpoint():
    sys = _sys(Listener(family="ipv4", ip="127.0.0.1", port=9999, inode=1))
    ok, _ = tcp_endpoint_present(sys, "[::1]:9999")
    assert not ok


def test_ipv4_any_satisfies_loopback_v4():
    sys = _sys(Listener(family="ipv4", ip="0.0.0.0", port=9999, inode=1))
    ok, _ = tcp_endpoint_present(sys, "127.0.0.1:9999")
    assert ok


def test_v6_wildcard_satisfies_loopback_v6():
    sys = _sys(Listener(family="ipv6", ip="::", port=9999, inode=1))
    ok, _ = tcp_endpoint_present(sys, "[::1]:9999")
    assert ok


def test_localhost_satisfied_by_either_family():
    assert tcp_endpoint_present(_sys(Listener(family="ipv6", ip="::1", port=9999, inode=1)),
                                "localhost:9999")[0]
    assert tcp_endpoint_present(_sys(Listener(family="ipv4", ip="127.0.0.1", port=9999, inode=1)),
                                "localhost:9999")[0]


def test_wrong_family_lingering_not_counted_on_stop(tmp_path):
    # A ready=true endpoint's stop-cessation check must use the declared family too.
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.paths import Paths
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),))
    sys = _sys(Listener(family="ipv6", ip="::1", port=9999, inode=1))   # wrong family
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), sys)
    gone, lingering = life._ready_endpoints_gone(comp)
    assert gone and not lingering          # ipv6 listener does NOT keep the v4 endpoint alive


# --- 4.1: STATUS agrees with startup (status used to be port-only) -----------

import pytest


def _status_present(sys, address, tmp_path):
    from lhpc.core.status import StatusProber
    from lhpc.core.paths import Paths
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address=address, ready=True),))
    cs = StatusProber(sys, Paths(runtime_root=tmp_path)).assess_component(comp)
    return cs.endpoints[0].present


@pytest.mark.parametrize("address,listener,expect", [
    ("127.0.0.1:9000", Listener("ipv4", "127.0.0.1", 9000, 1), True),
    ("127.0.0.1:9000", Listener("ipv6", "::1", 9000, 1), False),        # wrong family
    ("127.0.0.1:9000", Listener("ipv4", "10.0.0.5", 9000, 1), False),   # wrong host
    ("[::1]:9000",     Listener("ipv6", "::1", 9000, 1), True),
    ("[::1]:9000",     Listener("ipv4", "127.0.0.1", 9000, 1), False),  # wrong family
    ("localhost:9000", Listener("ipv6", "::1", 9000, 1), True),
    ("localhost:9000", Listener("ipv4", "127.0.0.1", 9000, 1), True),
])
def test_status_matches_startup(address, listener, expect, tmp_path):
    sys = _sys(listener)
    startup = tcp_endpoint_present(sys, address)[0]
    status = _status_present(sys, address, tmp_path)
    assert startup == status == expect


def test_status_owner_pid_from_matched_listener_not_wrong_family(tmp_path):
    # Two listeners share the port; the owner PID retained by status must be the MATCHED
    # (declared-family) listener's owner, never the wrong-family one on the same port.
    from lhpc.core.probes.backends import FakeSystem, Listener as L
    from lhpc.core.probes.endpoints import tcp_endpoint_match
    sys = FakeSystem(
        listeners=[L("ipv6", "::1", 9000, inode=11),          # wrong family, owner 111
                   L("ipv4", "127.0.0.1", 9000, inode=22)],   # matched,      owner 222
        owners={11: 111, 22: 222}).system
    present, ev, pid, inc = tcp_endpoint_match(sys, "127.0.0.1:9000")
    assert present and pid == 222 and "owner_pid=222" in ev
