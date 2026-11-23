"""`assert_web_ui`: a component's web UI really runs — 200 without a login, its own page, and one
API answer — and a check that does not pass blames a stack only when LHPC reports the component
gone. Served by a local HTTP(S) server on a free loopback port; the lane's executable is stubbed,
because what is under test is the reading of the answers, not the process table."""
from __future__ import annotations

import datetime
import http.server
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import threading

import lhpc_testlab.release as rel
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

PAGE = b"<html><head><title>MeshCore</title></head><body>app</body></html>"
GOOD = {"db": "ok", "radio": "connected"}

_STATUS = """OK    Status collected (read-only; no network, no changes).
[meshcore] MeshCore (OpenHop)  ({state})
  meshcore-webui           {state:<14} [service] band 868  tx disabled  src ok
"""


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """`lhpc status` reports meshcore-webui in `lab["state"]`; the run log tail is present."""
    box = {"state": "running"}

    def run(env, *args, timeout=300.0, check=False):
        return subprocess.CompletedProcess(["lhpc", *args], 0, _STATUS.format(state=box["state"]), "")
    monkeypatch.setattr(rel, "run_lhpc", run)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "start-meshcore-webui-868.log").write_text("WEBUI-RUN-LOG-LAST\n")
    box["env"] = {"LHPC_RUNTIME_ROOT": str(tmp_path)}
    return box


def _serve(routes, tls_dir=None):
    """A server answering each path from `routes[path]`: a list of `(status, body)` answers,
    one per request, the last one repeated. Returns `(base url, request log, server)`."""
    log = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            log.append(self.path)
            answers = routes.get(self.path, [(404, b"")])
            status, body = answers[min(log.count(self.path), len(answers)) - 1]
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    scheme = "http"
    if tls_dir is not None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_dir / "c.pem", tls_dir / "k.pem")
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"{scheme}://127.0.0.1:{srv.server_address[1]}/", log, srv


@pytest.fixture
def served():
    servers = []

    def start(routes, tls_dir=None):
        url, log, srv = _serve(routes, tls_dir)
        servers.append(srv)
        return url, log
    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _check(lab, url, **kw):
    kw.setdefault("timeout", 0)
    rel.assert_web_ui(lab["env"], "meshcore", "meshcore-webui", url, **kw)


def _failure(lab, url, **kw) -> str:
    with pytest.raises(AssertionError) as exc:
        _check(lab, url, **kw)
    return str(exc.value)


def _health(doc) -> bool:
    return doc.get("db") == "ok" and doc.get("radio") == "connected"


def test_the_right_page_and_the_right_api_answer_pass(lab, served):
    url, log = served({"/": [(200, PAGE)], "/api/health/deep": [(200, json.dumps(GOOD).encode())]})
    _check(lab, url, page="<title>MeshCore</title>", api=("/api/health/deep", _health))
    assert log == ["/", "/api/health/deep"]


def test_the_installed_pages_exact_bytes_pass(lab, served):
    url, _log = served({"/": [(200, PAGE)]})
    _check(lab, url, page=PAGE)


def test_an_api_whose_200_alone_is_the_evidence_passes(lab, served):
    url, log = served({"/": [(200, PAGE)], "/api/v1/fromradio": [(200, b"\x08\x01")]})
    _check(lab, url, page=PAGE, api=("/api/v1/fromradio", None))
    assert log == ["/", "/api/v1/fromradio"]


@pytest.mark.parametrize("code", [401, 403])
def test_a_login_wall_is_not_a_web_ui(lab, served, code):
    url, _log = served({"/": [(code, b"login")]})
    msg = _failure(lab, url, page="<title>MeshCore</title>")
    assert f"meshcore-webui's web UI {url} answered {code} (LHPC reports meshcore-webui: running)" in msg


def test_a_wrong_page_fails(lab, served):
    url, _log = served({"/": [(200, b"<title>Repeater Dashboard</title>")]})
    msg = _failure(lab, url, page="<title>MeshCore</title>")
    assert f"{url} served a page without '<title>MeshCore</title>'" in msg


def test_a_page_that_is_not_the_installed_file_fails(lab, served):
    url, _log = served({"/": [(200, PAGE + b" ")]})
    msg = _failure(lab, url, page=PAGE)
    assert f"{url} served {len(PAGE) + 1} bytes that are not the installed page ({len(PAGE)} bytes)" in msg


def test_an_api_that_never_reports_the_radio_connected_fails(lab, served):
    bad = json.dumps({"db": "ok", "radio": "disconnected"}).encode()
    url, _log = served({"/": [(200, PAGE)], "/api/health/deep": [(200, bad)]})
    msg = _failure(lab, url, page=PAGE, api=("/api/health/deep", _health))
    assert '/api/health/deep answered {"db": "ok", "radio": "disconnected"}' in msg


def test_an_api_that_answers_no_json_or_no_200_fails(lab, served):
    url, _log = served({"/": [(200, PAGE)], "/a": [(200, b"<html>")], "/b": [(401, b"")]})
    assert "/a answered no JSON" in _failure(lab, url, page=PAGE, api=("/a", _health))
    assert "/b answered 401" in _failure(lab, url, page=PAGE, api=("/b", None))


def test_it_polls_until_the_ui_is_ready(lab, served):
    """Counted answers, not time: the first two requests see a starting server."""
    url, log = served({"/": [(503, b""), (503, b""), (200, PAGE)]})
    _check(lab, url, page=PAGE, timeout=60, every=0.01)
    assert log == ["/", "/", "/"]


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.parametrize("state", ["stopped", "failed"])
def test_a_web_ui_that_is_down_with_its_component_gone_is_marked(lab, state):
    lab["state"] = state
    url = f"http://127.0.0.1:{_closed_port()}/"
    msg = _failure(lab, url, page=PAGE)
    assert msg.split("\n", 1)[0] == "STACK-REGRESSION stack=meshcore phase=readiness"
    assert f"{url} answered nothing (LHPC reports meshcore-webui: {state})" in msg
    assert "WEBUI-RUN-LOG-LAST" in msg


def test_a_web_ui_that_is_down_while_its_component_runs_blames_no_stack(lab):
    url = f"http://127.0.0.1:{_closed_port()}/"
    msg = _failure(lab, url, page=PAGE)
    assert not re.search(r"STACK-REGRESSION stack=", msg)
    assert "answered nothing (LHPC reports meshcore-webui: running)" in msg


@pytest.fixture(scope="module")
def tls_dir(tmp_path_factory):
    """A self-signed certificate, as meshtasticd serves its own."""
    d = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    (d / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    return d


def test_a_self_signed_lab_endpoint_is_reached_with_tls(lab, served, tls_dir):
    url, log = served({"/": [(200, PAGE)]}, tls_dir)
    _check(lab, url, page=PAGE, tls=True)
    assert log == ["/"]


def test_without_tls_a_self_signed_endpoint_is_not_trusted(lab, served, tls_dir):
    """The other side of the flag: the default verification refuses the lab's certificate, so
    passing above is the flag's doing."""
    url, _log = served({"/": [(200, PAGE)]}, tls_dir)
    assert "answered nothing" in _failure(lab, url, page=PAGE)


# --- a starting server's interim answer of another shape keeps the poll going (C1 reader k1) --------------


def test_a_json_answer_of_another_shape_is_polled_past(lab, served):
    url, log = served({"/": [(200, PAGE)], "/api/health/deep": [(200, b"[1, 2]"), (200, json.dumps(GOOD).encode())]})
    _check(lab, url, page=PAGE, api=("/api/health/deep", _health), timeout=60, every=0.01)
    assert log.count("/api/health/deep") == 2


def test_a_predicate_that_cannot_read_the_answer_is_a_failed_check_not_a_crash(lab, served):
    url, _log = served({"/": [(200, PAGE)], "/a": [(200, b"{}")], "/b": [(200, b"[1, 2]")]})
    assert "/a answered {}" in _failure(lab, url, page=PAGE, api=("/a", lambda d: d["radio"] == "connected"))
    assert "/b answered [1, 2]" in _failure(lab, url, page=PAGE, api=("/b", _health))


# --- the release cases' predicates, on the answers each upstream gives at its pin --------------------------


def test_meshcore_webui_is_healthy_only_with_its_db_and_its_radio():
    # meshcore-webui@94dcc3d backend/app/main.py:468-509 (`vapid` rides along)
    assert rel.meshcore_webui_healthy({"radio": "connected", "db": "ok", "vapid": "missing"})
    assert not rel.meshcore_webui_healthy({"radio": "disconnected", "db": "ok", "vapid": "loaded"})
    assert not rel.meshcore_webui_healthy({"radio": "connected", "db": "error", "vapid": "loaded"})


def test_openhop_is_set_up_only_on_a_clean_false():
    # openhop_repeater@72266e2 repeater/web/api_endpoints.py:1351-1373: the handler's error path also says false
    assert rel.openhop_set_up({"needs_setup": False, "reasons": {"default_name": True}})
    assert not rel.openhop_set_up({"needs_setup": True, "reasons": {}})
    assert not rel.openhop_set_up({"needs_setup": False, "error": "config unreadable"})
    assert not rel.openhop_set_up({"reasons": {}})                  # no answer to the question is not "set up"


def test_meshchat_is_attached_only_to_the_shared_instance():
    # reticulum-meshchat@45f89a8 meshchat.py:941-956
    assert rel.meshchat_attached({"app_info": {"is_connected_to_shared_instance": True}})
    assert not rel.meshchat_attached({"app_info": {"is_connected_to_shared_instance": False}})
    assert not rel.meshchat_attached({"app_info": {}})


def test_graywolf_reports_the_fetched_version_with_or_without_a_v():
    # graywolf v0.14.14 pkg/webapi/version.go (`version`), .goreleaser.yml `-X main.Version={{.Version}}`
    check = rel.version_is("0.14.14")
    assert check({"version": "0.14.14", "commit": "b589e68", "platform": "linux/arm64"})
    assert check({"version": "v0.14.14"})
    assert not check({"version": "0.14.13"})
    assert not check({"commit": "b589e68"})


# graywolf: its page, not only its version API (a 200 with the right /api/version proves the API server alone)
GRAYWOLF_SHELL = b'<!doctype html><html lang="en"><head><title>graywolf</title></head><body><div id="app"></div></body></html>'
VERSION = json.dumps({"version": "0.14.14", "commit": "b589e68"}).encode()


def test_graywolf_with_its_own_page_and_the_fetched_version_passes(lab, served):
    url, log = served({"/": [(200, GRAYWOLF_SHELL)], "/api/version": [(200, VERSION)]})
    rel.assert_graywolf_web_ui(lab["env"], url, "0.14.14", timeout=0)
    assert log == ["/", "/api/version"]


@pytest.mark.parametrize("page", [b"", b"<html><head><title>Sign in</title></head><body><form></form></body></html>",
                                  b"<html><head><title>Welcome to nginx!</title></head></html>"],
                         ids=["empty", "sign-in", "unrelated"])
def test_graywolf_with_the_right_version_but_not_its_page_fails(lab, served, page):
    # red-before (ca275287): the graywolf call passed no page, so each of these passed on its /api/version alone
    url, _log = served({"/": [(200, page)], "/api/version": [(200, VERSION)]})
    with pytest.raises(AssertionError) as exc:
        rel.assert_graywolf_web_ui(lab["env"], url, "0.14.14", timeout=0)
    assert "served a page without '<title>graywolf</title>'" in str(exc.value), exc.value


# --- the constants come from the files the lane serves or fetches -------------------------------------------


def _data(name):
    from pathlib import Path

    import lhpc
    return (Path(lhpc.__file__).parent / "data" / name / "index.html").read_text()


def test_the_bundled_titles_are_the_packaged_bundles_own():
    assert rel.MESHCORE_WEBUI_TITLE in _data("meshcore-webui-dist")
    assert rel.MESHCHAT_TITLE in _data("meshchat-dist")


def test_the_graywolf_version_checked_is_the_one_the_manifest_fetches_and_marks():
    owner, argv = rel.fetch_argv("graywolf-fetch.sh")
    assert owner == "graywolf" and re.fullmatch(r"\d+\.\d+\.\d+", argv[3]), argv
    from lhpc.core.manifest import default_manifest_path, load_manifest
    marker = next(c.build_marker for s in load_manifest(default_manifest_path()) for c in s.components
                  if c.id == owner)
    assert marker.endswith(f".lhpc-built-{argv[3]}"), marker   # the same pin, stated twice by the manifest


def test_a_fetch_step_that_is_not_declared_exactly_once_is_refused():
    with pytest.raises(AssertionError, match=re.escape("declares 0 build steps running no-such-fetch.sh")):
        rel.fetch_argv("no-such-fetch.sh")
