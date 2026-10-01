"""The console's webserver panel shows the server certificate's expiry line and each client
certificate's mark, as `pki` computes them. The line and the mark themselves (the cap, the
provisional window, the boundaries) are owned by tests/host/test_cert_cap_and_expiry.py."""

from __future__ import annotations

import datetime as _dt

import htmlq

from lhpc.core import pki
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

DAY = _dt.timedelta(days=1)


def _init_pki(tmp_path):
    p = Paths(runtime_root=tmp_path)
    pki.init_server_ca(p, force=True)
    pki.init_client_ca(p, force=True)
    pki.build_crl(p)
    return p


def _at(monkeypatch, when):
    """Move the display's clock (pki._now) to `when`; issuing is done before this is called."""
    monkeypatch.setattr(pki, "_now", lambda: when)


def test_the_panel_shows_the_server_line_and_the_client_mark(web, tmp_path, monkeypatch):
    p = _init_pki(tmp_path)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=825)
    act = pki.issue_client_cert(p, "laptop", days=825, passphrase="pw-for-the-test")
    _at(monkeypatch, _dt.datetime.fromisoformat(act["not_after"]) - 59 * DAY
        - _dt.timedelta(minutes=1))
    doc = htmlq.parse(web().get("/stacks").get_data(as_text=True))
    line = pki.pki_status(p)["server_cert"]["expiry_text"]
    mark = pki.client_certs_with_expiry(p)[0]["expiry_mark"]
    assert line and mark
    assert line in doc.text
    assert mark in doc.text                                           # the client list's mark
    assert "renewed automatically" not in doc.text                    # the old phrases are replaced


def test_the_panel_shows_a_provisional_certificate_as_such(web, tmp_path, monkeypatch):
    from lhpc.core import service_system
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": False, "maxerror_us": 1000})
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.webserver_init(dns_sans=["box.lan"], ip_sans=[], confirm=True)
    assert res.ok, res.summary
    sc = pki.pki_status(Paths(runtime_root=tmp_path))["server_cert"]
    assert sc["expiry"]["provisional"] and sc["expiry"]["over_cap"]
    doc = htmlq.parse(web().get("/stacks").get_data(as_text=True))
    assert sc["expiry_text"] in doc.text
    assert "renewed automatically" not in doc.text
