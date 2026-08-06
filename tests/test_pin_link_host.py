"""Endpoint-pin link host.

The running-stack interface pins (and the Webserver-box port pins) must point at an address that
WORKS for the current viewer, decided from the LIVE listener scope (/proc/net/tcp) — never the saved
bind policy. Regression guard for the dead-remote-link bug where a fixed-loopback service with no
bind knob (MeshCom QEMU) was linked at the request host when the console was opened via the AP/LAN.
"""
from __future__ import annotations

import pytest

from lhpc.core.model import EndpointObservation, EndpointSpec
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def _svc(tmp_path, listeners):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    sysm = FakeSystem(listeners=[Listener(**d) for d in listeners]).system
    return ControllerService(system=sysm, paths=Paths(runtime_root=tmp_path))


def _status(*specs):
    st = type("St", (), {})()
    st.endpoints = [EndpointObservation(spec=sp, present=True) for sp in specs]
    return st


def _ep(address, scheme="tcp"):
    return EndpointSpec(kind="tcp", address=address, client=True, scheme=scheme)


def _l(ip, port):
    return {"family": "ipv6" if ":" in ip else "ipv4", "ip": ip, "port": port, "inode": port}


def test_meshcom_qemu_pins_stay_loopback_when_viewed_remotely(tmp_path):
    # MeshCom QEMU forwards its web UI (18083) and net-console (12323) on 127.0.0.1 and has NO bind
    # knob. Even opened via the AP, the pins must NOT link to the request host — nothing listens there.
    svc = _svc(tmp_path, [_l("127.0.0.1", 18083), _l("127.0.0.1", 12323)])
    ifaces = svc._client_interfaces(_status(_ep("127.0.0.1:18083", "http"),
                                            _ep("127.0.0.1:12323", "tcp")), "meshcom")
    assert ifaces and all(not i["remote_ok"] for i in ifaces)


def test_exposed_listener_pin_uses_request_host(tmp_path):
    # meshtasticd binds 0.0.0.0:4403 at runtime though its manifest address reads 127.0.0.1 — the LIVE
    # scope wins, so the pin becomes request-host-linkable.
    svc = _svc(tmp_path, [_l("0.0.0.0", 4403)])
    [itf] = svc._client_interfaces(_status(_ep("127.0.0.1:4403", "tcp")), "meshtastic")
    assert itf["remote_ok"] and itf["port"] == "4403"


def test_pin_follows_live_listener_not_saved_bind_during_pending_restart(tmp_path):
    # A saved bind change to public does NOT move the link while the RUNNING listener is still
    # loopback (restart pending); only once the process rebinds 0.0.0.0 does the live scope flip.
    loop = _svc(tmp_path, [_l("127.0.0.1", 8001)])
    [before] = loop._client_interfaces(_status(_ep("127.0.0.1:8001", "tcp")), "kiss")
    assert not before["remote_ok"]
    exposed = _svc(tmp_path, [_l("0.0.0.0", 8001)])
    [after] = exposed._client_interfaces(_status(_ep("127.0.0.1:8001", "tcp")), "kiss")
    assert after["remote_ok"]


@pytest.mark.parametrize("raw,expected", [
    ("192.168.178.95:8443", "192.168.178.95"),
    ("lhpc-e293.local:8443", "lhpc-e293.local"),
    ("[fe80::1]:8443", "[fe80::1]"),
    ("[::1]", "[::1]"),
])
def test_url_host_renders_ipv4_hostname_and_bracketed_ipv6(raw, expected):
    from lhpc.adapters.web.app import _url_host
    assert _url_host(raw) == expected
