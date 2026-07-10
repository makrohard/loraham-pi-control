"""M11: controller-owned Webserver GUI at /stacks/loraham-pi-control — cached-only GET,
CSRF-guarded POST actions, exposure confirmation, and loopback-only .p12 download."""

from __future__ import annotations

from pathlib import Path

from lhpc.adapters.web.app import create_app
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _app_svc(tmp_path: Path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(lambda: svc), svc


def _csrf(client):
    with client.session_transaction() as s:
        s["_csrf"] = "tok"
    return "tok"


def test_webserver_component_inline_on_stacks_cached_only(tmp_path):
    # The Webserver component is rendered INLINE in the controller row on /stacks (no separate
    # page); GET must not probe/mutate.
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    r = c.get("/stacks")
    assert r.status_code == 200
    body = r.data.decode()
    assert 'id="webserver-row"' in body
    assert "Webserver" in body and "Monitor" in body and "Certificates" in body and "Settings" in body
    assert "Local IP address" in body                          # first Monitor line
    assert not (tmp_path / "state" / "webserver.json").exists()


def test_old_webserver_path_redirects_to_stacks(tmp_path):
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    r = c.get("/stacks/loraham-pi-control")
    assert r.status_code == 302 and r.headers["Location"].endswith("#webserver-row")


def test_webserver_logs_page_and_component_link(tmp_path):
    from lhpc.core import runtime_fs
    app, svc = _app_svc(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-error.log"),
                            "boom [emerg] mkdir failed\n", 0o644)
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-access.log"),
                            "GET / 200\n", 0o644)
    c = app.test_client()
    # component on /stacks links to the logs page
    assert "/webserver/logs" in c.get("/stacks").data.decode()
    # error log (default + explicit) and access log render their tails
    assert "[emerg] mkdir failed" in c.get("/webserver/logs").data.decode()
    assert "[emerg] mkdir failed" in c.get("/webserver/logs?src=error").data.decode()
    assert "GET / 200" in c.get("/webserver/logs?src=access").data.decode()
    # unknown src falls back to the error log, never traverses
    assert "[emerg] mkdir failed" in c.get("/webserver/logs?src=../etc").data.decode()


def test_expose_failure_without_cidr_is_refused_and_not_exposed(tmp_path):
    # A remote-exposure with a valid phrase but no CIDR is refused: the failure detail is shown and,
    # critically, the listener is NOT exposed.
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf(c)
    r = c.post("/webserver/expose",
               data={"_csrf": tok, "cidrs": "", "confirm_phrase": "enable-remote"},
               follow_redirects=True)
    assert "at least one allowed source CIDR" in r.data.decode()  # the actual failure detail
    assert svc.config().webserver.remote_exposed is False         # not exposed (the safety fact)


def test_post_requires_csrf(tmp_path):
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    assert c.post("/webserver/configure", data={"access_mode": "no-auth"}).status_code == 400


def test_configure_via_post(tmp_path):
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf(c)
    r = c.post("/webserver/configure", data={"_csrf": tok, "access_mode": "auth-everywhere"})
    assert r.status_code == 302
    assert svc.config().webserver.access_mode == "auth-everywhere"


def test_expose_requires_confirmation(tmp_path):
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf(c)
    c.post("/webserver/expose", data={"_csrf": tok, "cidrs": "192.168.0.0/24"})  # no confirm
    assert svc.config().webserver.remote_exposed is False


def test_p12_download_is_loopback_only(tmp_path):
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf(c)
    svc.webserver_init()
    c.post("/webserver/cert", data={"_csrf": tok, "op": "issue", "label": "laptop"})
    # remote peer (nginx-set header) -> refused
    assert c.get("/webserver/cert/laptop/download",
                 headers={"X-LHPC-Peer": "remote"}).status_code == 403
    # loopback (no nginx header) -> served as a pkcs12 attachment
    r = c.get("/webserver/cert/laptop/download")
    assert r.status_code == 200 and r.mimetype == "application/x-pkcs12"
    assert r.headers["Content-Disposition"].endswith('laptop.p12"')


def test_webserver_reachable_from_stacks_and_not_a_managed_stack(tmp_path):
    # Reachable inline under the controller row on /stacks; the controller id is NOT a managed
    # stack (a bogus stack-detail 404s; it never enters build_snapshot).
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    body = c.get("/stacks").data.decode()
    assert 'id="webserver-row"' in body and "Webserver (HTTPS / mTLS)" in body
    assert c.get("/stacks/loraham-pi-control").status_code == 302        # old path -> redirect
    assert c.get("/stacks/loraham-pi-control-bogus").status_code == 404
