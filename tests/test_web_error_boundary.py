"""4.3 — typed public error boundaries: an UNEXPECTED escape renders a clean typed page
(never a traceback), while HTTP errors keep their status and EXPECTED unsafe runtime-root
states stay typed (no 500). debug/reloader are off."""

from pathlib import Path

from lhpc.adapters.web.app import create_app
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


class _Boom(ControllerService):
    """A service whose dashboard read raises a NON-typed error (simulating an unexpected
    escape past the typed service boundary)."""

    def radio_overview(self):
        raise RuntimeError("unexpected internal explosion with secrets in the traceback")


def test_unexpected_error_renders_clean_500_not_traceback(tmp_path):
    def factory():
        return _Boom(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    client = create_app(service_factory=factory).test_client()
    resp = client.get("/")
    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert "Internal error" in body
    # No traceback / internal detail leaks to the client.
    assert "Traceback" not in body and "explosion with secrets" not in body


def test_404_still_typed_not_500(tmp_path):
    def factory():
        return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    client = create_app(service_factory=factory).test_client()
    resp = client.get("/no/such/path")
    assert resp.status_code == 404 and "Traceback" not in resp.get_data(as_text=True)


def test_unsafe_runtime_root_start_is_typed_not_traceback(tmp_path):
    # An expected unsafe/absent runtime-root state must be a typed ActionResult at the
    # service boundary, never a raised traceback.
    svc = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path / "does-not-exist"))
    res = svc.start("daemon", apply=True)
    assert res.ok is False and res.summary            # typed, populated, no exception
