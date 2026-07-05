"""P1 — a ready=true Unix/path endpoint must be runtime-contained unless external;
external endpoints never gate readiness or cessation."""

from lhpc.core.services import ControllerService
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.model import Component, ComponentKind, EndpointSpec
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_outside_root_unix_ready_endpoint_cannot_gate(tmp_path):
    svc = _svc(tmp_path)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="unix", address="/tmp/evil.sock", ready=True),))
    ok, ev = svc._ready_endpoints_present(comp)
    assert not ok and any("not runtime-contained" in e for e in ev)


def test_runtime_contained_unix_ready_endpoint_probes(tmp_path):
    svc = _svc(tmp_path)
    addr = str(tmp_path / "state" / "x.sock")
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="unix", address=addr, ready=True),))
    ok, ev = svc._ready_endpoints_present(comp)
    assert not ok and any("absent" in e for e in ev)   # contained but socket not present


def test_external_unix_ready_endpoint_does_not_gate(tmp_path):
    svc = _svc(tmp_path)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="unix", address="/tmp/ext.sock",
                                             ready=True, external=True),))
    ok, ev = svc._ready_endpoints_present(comp)
    assert ok            # external is observe-only -> never gates -> readiness not blocked


def test_per_component_readiness_timeout_extends_wait(tmp_path, monkeypatch):
    """A slow-booting endpoint that comes up AFTER the global default but WITHIN the
    per-component `readiness_timeout` verifies (the meshcore-pi fix); with only the short
    global default it would time out and be torn down."""
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "ENDPOINT_VERIFY_POLL_S", 0.001)
    monkeypatch.setattr(svc, "ENDPOINT_VERIFY_TIMEOUT_S", 0.005)   # short global default
    calls = {"n": 0}

    def _fake_present(system, addr):
        calls["n"] += 1
        return (calls["n"] >= 20, f"{addr}: {'present' if calls['n'] >= 20 else 'absent'}")

    monkeypatch.setattr("lhpc.core.probes.endpoints.tcp_endpoint_present", _fake_present)
    ep = (EndpointSpec(kind="tcp", address="127.0.0.1:5000", ready=True),)

    # Default budget: times out well before the endpoint appears -> NOT ready.
    fast = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=ep)
    assert svc._ready_endpoints_present(fast)[0] is False

    # Per-component budget: enough polls for the endpoint to appear -> ready.
    calls["n"] = 0
    slow = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=ep, readiness_timeout=1.0)
    assert svc._ready_endpoints_present(slow)[0] is True


def test_stop_cessation_ignores_outside_root_unix(tmp_path):
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="unix", address="/tmp/evil.sock", ready=True),))
    gone, lingering = life._ready_endpoints_gone(comp)
    assert gone and not lingering    # an outside-root endpoint can't keep the stop unverified
