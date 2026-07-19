"""The trusted-host / DNS-rebinding policy must be enforced in EVERY serving mode — including the
plain interactive loopback-HTTP console (not only productive/HTTPS). A rebinding hostname is rejected
with 400 before any session, CSRF, or mutation, and the client-supplied X-Forwarded-Host is ignored.
"""

from pathlib import Path

import re

from lhpc.adapters.web.app import create_app
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


class _MutationSpy:
    """Delegates reads; records any attempt to invoke a mutating service method."""

    _MUTATING = {"start", "restart", "stop", "install", "build", "test", "save_config",
                 "save_config_bundle", "update", "uninstall", "clean", "apply_daemon_params"}

    def __init__(self, svc):
        self._svc = svc
        self.mutations = []

    def __getattr__(self, name):
        if name in self._MUTATING:
            def _rec(*a, **k):
                self.mutations.append(name)
                raise AssertionError(f"mutating method {name} reached through a rejected host")
            return _rec
        return getattr(self._svc, name)


def _client(tmp_path: Path, spy: dict | None = None):
    def factory():
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        if spy is not None:
            svc = _MutationSpy(svc)
            spy["svc"] = svc
        return svc
    app = create_app(service_factory=factory)
    app.config["SESSION_COOKIE_SECURE"] = False        # interactive plain-HTTP console (the risky mode)
    app.config["LHPC_PRODUCTIVE"] = False
    return app.test_client()


def _csrf(client):
    body = client.get("/stacks").get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', body)
    return m.group(1) if m else ""


# --- rebinding rejection in interactive mode ------------------------------------------------------

def test_interactive_console_rejects_rebinding_host(tmp_path):
    c = _client(tmp_path)
    r = c.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert b"evil.example" in r.data           # bounded, text/plain — no HTML reflection
    assert b"<" not in r.data                  # not reflected into markup


def test_post_cannot_mutate_config_through_hostile_host(tmp_path):
    spy = {}
    c = _client(tmp_path, spy=spy)
    token = _csrf(c)                            # obtained via a legitimate (localhost) request
    r = c.post("/action", data={"_csrf": token, "op": "start", "target": "daemon"},
               headers={"Host": "attacker.rebind"})
    assert r.status_code == 400                 # rejected in before_request, before dispatch
    assert spy["svc"].mutations == []           # the mutating handler was never reached


# --- legitimate loopback forms accepted -----------------------------------------------------------

def test_loopback_forms_are_accepted(tmp_path):
    c = _client(tmp_path)
    for host in ("localhost", "127.0.0.1", "127.0.0.1:8770", "[::1]", "[::1]:9443"):
        assert c.get("/", headers={"Host": host}).status_code == 200, host


# --- empty / missing Host rejected ----------------------------------------------------------------

def test_empty_host_is_rejected(tmp_path):
    c = _client(tmp_path)
    assert c.get("/", headers={"Host": ""}).status_code == 400


# --- X-Forwarded-Host is never trusted ------------------------------------------------------------

def test_x_forwarded_host_is_not_trusted(tmp_path):
    c = _client(tmp_path)
    # A hostile real Host with a spoofed X-Forwarded-Host claiming loopback must STILL be rejected —
    # enforcement keys on the real Host, never the forwarded header.
    r = c.get("/", headers={"Host": "evil.example", "X-Forwarded-Host": "localhost"})
    assert r.status_code == 400
